# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
from unittest.mock import MagicMock

import pytest

from pydantic import ValidationError

from codemie.agents.tools.interactive.request_user_input import (
    REQUEST_USER_INPUT_TOOL_NAME,
    SURFACE_REJECTED_NOTICE,
    RequestUserInputArgs,
    RequestUserInputTool,
)
from codemie.core.a2ui import catalog as a2ui_catalog
from codemie.core.a2ui.config import MAX_SURFACE_COMPONENTS, MAX_SURFACE_PAYLOAD_BYTES
from codemie.service.conversation.a2ui_intake import _validate_answer_against_surface


def _wire_request(declares_catalog=True):
    """Build the request through the wire alias, the way a real client sends it."""
    from codemie.core.models import AssistantChatRequest

    payload = {"text": "x"}
    if declares_catalog:
        payload["a2uiSupportedCatalogs"] = [a2ui_catalog.CATALOG_ID]
    return AssistantChatRequest.model_validate(payload)


def _request(declares_catalog=True):
    request = MagicMock()
    request.a2ui_supported_catalogs = [a2ui_catalog.CATALOG_ID] if declares_catalog else None
    return request


def _tool():
    generator = MagicMock()
    return RequestUserInputTool(thread_generator=generator), generator


def _notices(generator):
    """The plain-text chunks the tool streamed (the rejection notice, if any)."""
    sent = [json.loads(call.args[0]) for call in generator.send.call_args_list]
    return [chunk for chunk in sent if chunk.get("generated_chunk")]


def _assert_no_surface_streamed(generator):
    """A rejected surface must not reach the client.

    The tool still streams a plain-text notice in that case (the turn ends either way, so
    silence would leave the user with an empty message), hence this checks for A2UI chunks
    rather than for silence.
    """
    sent = [json.loads(call.args[0]) for call in generator.send.call_args_list]
    assert not [chunk for chunk in sent if chunk.get("a2ui")]


def _button_surface(action=None):
    """Smallest answerable surface: a root holding one Button with a label child."""
    return [
        {"id": "root", "component": "Column", "children": ["ok"]},
        {
            "id": "ok",
            "component": "Button",
            "child": "ok-label",
            "action": {"event": {"name": "ok"}} if action is None else action,
        },
        {"id": "ok-label", "component": "Text", "text": "OK"},
    ]


def test_execute_emits_a2ui_envelopes_in_order():
    tool, generator = _tool()
    tool.execute(components=_button_surface())
    assert generator.send.call_count == 2  # createSurface + updateComponents
    chunks = [json.loads(c.args[0]) for c in generator.send.call_args_list]
    create, update = (chunk["a2ui"] for chunk in chunks)
    assert "createSurface" in create and create["createSurface"]["surfaceId"]
    assert create["createSurface"]["catalogId"] == a2ui_catalog.CATALOG_ID
    assert update["updateComponents"]["components"][0]["id"] == "root"
    assert update["updateComponents"]["surfaceId"] == create["createSurface"]["surfaceId"]


def _field_surface(path="/name"):
    """The smallest answerable surface that also BINDS a data-model path."""
    return [
        {"id": "root", "component": "Column", "children": ["field", "ok"]},
        {"id": "field", "component": "TextField", "label": "Name", "value": {"path": path}},
        *_button_surface()[1:],
    ]


def test_execute_appends_data_model_envelope():
    tool, generator = _tool()
    tool.execute(components=_field_surface(), data_model={"name": ""})
    assert generator.send.call_count == 3
    last = json.loads(generator.send.call_args_list[-1].args[0])["a2ui"]
    assert last["updateDataModel"]["value"] == {"name": ""}


class TestMalformedArgumentsStillNotifyTheUser:
    """The turn ends whatever the tool raises, so the notice cannot depend on the type.

    ``return_direct`` routes on tool NAMES snapshotted at graph build time, so a rejected
    call ends the turn exactly like a successful one. The arguments are agent-authored, so
    a shape nobody anticipated raises whatever it raises — and the user is owed the notice
    regardless of whether that was a ValueError.
    """

    @pytest.mark.parametrize(
        "components",
        [
            pytest.param([{"id": "top", "component": "Column", "children": 3}], id="scalar-children"),
            pytest.param([{"id": "top", "component": "Column", "tabs": 5}], id="scalar-tabs"),
            pytest.param(
                [{"id": "root", "component": {"name": "Column"}, "children": []}],
                id="component-name-is-an-object",
            ),
        ],
    )
    def test_notice_is_streamed_and_no_surface_escapes(self, components):
        # Driven through `_run`, not `execute`: `execute` raises, and whether that becomes a
        # notice or another attempt is decided one layer up, by the code that knows whether
        # this turn can continue.
        tool, generator = _tool()
        rejected = False
        try:
            tool._run(components=components)
        except Exception:  # noqa: BLE001 - the type under test is "any"
            rejected = True
        assert rejected, "malformed components must not be accepted"
        _assert_no_surface_streamed(generator)
        assert len(_notices(generator)) == 1


class TestArgumentsRejectedBeforeExecute:
    """The schema refuses some shapes before `execute` runs, and that path needs the notice too.

    Driven through `run` rather than `execute` on purpose: calling `execute` directly is
    exactly what hid this gap, since it skips argument parsing entirely.
    """

    @pytest.mark.parametrize(
        "arguments",
        [
            pytest.param({"components": {"root": {}}}, id="components-is-an-object"),
            pytest.param(
                {"components": [{"id": "root", "component": "Column", "children": []}], "data_model": []},
                id="data-model-is-a-list",
            ),
        ],
    )
    def test_the_user_gets_the_notice_and_never_the_raw_error(self, arguments):
        tool, generator = _tool()
        result = tool.run(arguments)
        assert len(_notices(generator)) == 1
        # The framework turns a parse failure into the tool's result, and `return_direct`
        # puts that in front of the user — so it must not be a pydantic dump.
        assert result == SURFACE_REJECTED_NOTICE
        _assert_no_surface_streamed(generator)


class TestSeededDataModel:
    """A seeded key the surface binds nothing under can never be answered.

    The client echoes the whole stored data model back on every submit and the intake
    refuses a key no component binds, so emitting one strands the user on a form that
    cannot be sent — and no retry clears it, because the seed is part of the surface.
    """

    def test_a_nested_key_the_surface_binds_nothing_under_is_dropped(self):
        """The check descends: matching only the first segment let a deeper key through."""
        tool, generator = _tool()
        components = [
            {"id": "root", "component": "Column", "children": ["field", "ok"]},
            {"id": "field", "component": "TextField", "label": "N", "value": {"path": "/user/name"}},
            *_button_surface()[1:],
        ]
        tool.execute(components=components, data_model={"user": {"name": "", "nickname": "x"}})
        envelopes = [json.loads(c.args[0])["a2ui"] for c in generator.send.call_args_list]
        echoed = next(e for e in envelopes if "updateDataModel" in e)["updateDataModel"]["value"]
        _validate_answer_against_surface(envelopes, {"action": {"name": "ok"}}, echoed)

    def test_a_value_the_component_would_refuse_is_dropped(self):
        """Not only unbound keys: a seeded value outside a picker's options strands too."""
        tool, generator = _tool()
        components = [
            {"id": "root", "component": "Column", "children": ["pick", "ok"]},
            {
                "id": "pick",
                "component": "ChoicePicker",
                "label": "P",
                "value": {"path": "/choice"},
                "options": [{"label": "A", "value": "a"}],
            },
            *_button_surface()[1:],
        ]
        tool.execute(components=components, data_model={"choice": ["nope"]})
        envelopes = [json.loads(c.args[0])["a2ui"] for c in generator.send.call_args_list]
        echoed = next(e for e in envelopes if "updateDataModel" in e)["updateDataModel"]["value"]
        _validate_answer_against_surface(envelopes, {"action": {"name": "ok"}}, echoed)

    def test_unbound_key_is_dropped_and_the_surface_still_ships(self):
        tool, generator = _tool()
        tool.execute(components=_field_surface(), data_model={"name": "", "context_note": "seeded"})
        last = json.loads(generator.send.call_args_list[-1].args[0])["a2ui"]
        assert last["updateDataModel"]["value"] == {"name": ""}

    def test_bound_keys_survive_untouched(self):
        tool, generator = _tool()
        tool.execute(components=_field_surface(path="/profile/name"), data_model={"profile": {"name": "Bob"}})
        last = json.loads(generator.send.call_args_list[-1].args[0])["a2ui"]
        assert last["updateDataModel"]["value"] == {"profile": {"name": "Bob"}}

    def test_emitted_data_model_is_one_the_intake_accepts(self):
        """The emit side and the intake must agree on what a valid data model is."""
        tool, generator = _tool()
        tool.execute(components=_field_surface(), data_model={"name": "", "context_note": "seeded"})
        envelopes = [json.loads(c.args[0])["a2ui"] for c in generator.send.call_args_list]
        echoed = next(e for e in envelopes if "updateDataModel" in e)["updateDataModel"]["value"]
        _validate_answer_against_surface(envelopes, {"action": {"name": "ok"}}, echoed)


class TestStrayIdStrings:
    """Models keep listing a child's id in `components` right before the child itself.

    Seen live twice: `components.5` was the bare string 'confirm', followed by the real
    {"id": "confirm", ...}. Pydantic rejects it before the tool runs, so the turn dies
    with no message at all. A bare string carries no meaning in this list — children are
    named in their parent's own field — so a duplicate of a declared id is dropped rather
    than costing the user a turn. Anything else is left to fail loudly.
    """

    def test_stray_id_duplicating_a_component_is_dropped(self):
        components = _button_surface()
        components.insert(1, "ok")  # the id of the Button that follows
        args = RequestUserInputArgs.model_validate({"components": components})
        assert [c["id"] for c in args.components] == ["root", "ok", "ok-label"]

    def test_the_cleaned_surface_still_emits(self):
        tool, generator = _tool()
        components = _button_surface()
        components.insert(1, "ok")
        tool.execute(components=RequestUserInputArgs.model_validate({"components": components}).components)
        assert generator.send.call_count == 2

    def test_stray_id_naming_nothing_is_not_silently_swallowed(self):
        with pytest.raises(ValidationError):
            RequestUserInputArgs.model_validate({"components": [*_button_surface(), "ghost"]})


class TestDataModelKeyedByThePath:
    """A seed keyed BY the binding path is repaired, not thrown away.

    A component binds with `value: {"path": "/full_name"}`, so keying the seed `/full_name`
    is a reasonable reading — and one the prompt used to invite. The intake reads the model
    as a tree, binds nothing under a key literally called `/full_name`, and the emitter then
    drops every answer as unanswerable: the user submits a filled form and gets an empty one
    back. Observed live on a 16-field survey, all 16 values discarded.
    """

    @staticmethod
    def _surface():
        return [
            {"id": "root", "component": "Column", "children": ["who", "send"]},
            {"id": "who", "component": "TextField", "label": "Name", "value": {"path": "/full_name"}},
            {"id": "send", "component": "Button", "child": "lbl", "action": {"event": {"name": "submit"}}},
            {"id": "lbl", "component": "Text", "text": "Send"},
        ]

    @staticmethod
    def _emitted_model(generator):
        sent = [json.loads(call.args[0]) for call in generator.send.call_args_list]
        models = [chunk["a2ui"]["updateDataModel"] for chunk in sent if "updateDataModel" in chunk.get("a2ui", {})]
        return models[0]["value"] if models else None

    def test_a_pointer_key_reaches_the_client_as_the_object_it_addresses(self):
        tool, generator = _tool()
        tool.execute(components=self._surface(), data_model={"/full_name": "Evgenii"})
        assert self._emitted_model(generator) == {"full_name": "Evgenii"}

    def test_a_nested_pointer_becomes_a_branch(self):
        components = [
            {"id": "root", "component": "Column", "children": ["who", "send"]},
            {"id": "who", "component": "TextField", "label": "Name", "value": {"path": "/profile/name"}},
            {"id": "send", "component": "Button", "child": "lbl", "action": {"event": {"name": "submit"}}},
            {"id": "lbl", "component": "Text", "text": "Send"},
        ]
        tool, generator = _tool()
        tool.execute(components=components, data_model={"/profile/name": "Evgenii"})
        assert self._emitted_model(generator) == {"profile": {"name": "Evgenii"}}

    def test_a_model_that_got_it_right_is_untouched(self):
        """A leading slash is not legal in a plain key, so the rewrite cannot misfire."""
        tool, generator = _tool()
        tool.execute(components=self._surface(), data_model={"full_name": "Evgenii"})
        assert self._emitted_model(generator) == {"full_name": "Evgenii"}

    def test_the_plain_key_wins_when_both_shapes_arrive(self):
        tool, generator = _tool()
        tool.execute(
            components=self._surface(),
            data_model={"full_name": "typed", "/full_name": "stale"},
        )
        assert self._emitted_model(generator) == {"full_name": "typed"}


class TestComponentsNestedInAProperty:
    """A component that landed inside another component's property is put back.

    Models lose track of the array they are writing and keep going inside the last property
    they opened. Observed live: a whole card — its Column, its ChoicePicker, its fields —
    ended up inside a TextField's `checks` while `root.children` still named them, so the
    surface was refused twice over: the checks stopped matching their schema, and the layout
    referenced components that did not exist.
    """

    @staticmethod
    def _surface():
        return [
            {"id": "root", "component": "Column", "children": ["why", "card", "send"]},
            {
                "id": "why",
                "component": "TextField",
                "label": "Why",
                "value": {"path": "/why"},
                "checks": [
                    {
                        "condition": {"call": "required", "args": {"value": {"path": "/why"}}},
                        "message": "Required",
                    },
                    # The model continued the surface inside the array it had open.
                    {"id": "card", "component": "Card", "child": "tenure"},
                    {
                        "id": "tenure",
                        "component": "ChoicePicker",
                        "label": "Tenure",
                        "value": {"path": "/tenure"},
                        "options": [{"label": "New", "value": "new"}],
                    },
                ],
            },
            {"id": "send", "component": "Button", "child": "lbl", "action": {"event": {"name": "submit"}}},
            {"id": "lbl", "component": "Text", "text": "Send"},
        ]

    @staticmethod
    def _emitted(generator):
        return json.loads(generator.send.call_args_list[1].args[0])["a2ui"]["updateComponents"]["components"]

    def test_the_nested_components_come_back_to_the_top(self):
        tool, generator = _tool()
        tool.execute(components=self._surface())
        ids = [c["id"] for c in self._emitted(generator)]
        assert "card" in ids and "tenure" in ids, "the layout references them, so they must exist"

    def test_the_property_keeps_what_actually_belonged_to_it(self):
        tool, generator = _tool()
        tool.execute(components=self._surface())
        why = next(c for c in self._emitted(generator) if c["id"] == "why")
        assert [rule["message"] for rule in why["checks"]] == ["Required"]

    def test_a_component_already_declared_is_not_duplicated(self):
        """The misplaced copy is the accident; the one at the top level wins."""
        tool, generator = _tool()
        surface = self._surface()
        surface.append({"id": "tenure", "component": "Text", "text": "the real one"})
        tool.execute(components=surface)
        components = self._emitted(generator)
        assert [c["id"] for c in components].count("tenure") == 1
        assert next(c for c in components if c["id"] == "tenure")["component"] == "Text"

    def test_a_well_formed_surface_is_untouched(self):
        tool, generator = _tool()
        clean = [
            {"id": "root", "component": "Column", "children": ["send"]},
            {"id": "send", "component": "Button", "child": "lbl", "action": {"event": {"name": "submit"}}},
            {"id": "lbl", "component": "Text", "text": "Send"},
        ]
        tool.execute(components=clean)
        assert self._emitted(generator) == clean


class TestChecksThatDoNotValidate:
    """A malformed check costs its own hint, not the whole form.

    `checks` are the only recursive part of the catalog — an expression grammar — and the
    one place models actually go wrong. Observed live: a 34-component survey was refused
    whole because one field tried to express "required only if department is Other" and
    recursed into nested or/not. They are client-side validation the server re-does on
    every answer, so the form is worth more than the hint.
    """

    @staticmethod
    def _surface(bad_check):
        return [
            {"id": "root", "component": "Column", "children": ["who", "why", "send"]},
            {
                "id": "who",
                "component": "TextField",
                "label": "Name",
                "value": {"path": "/who"},
                "checks": [
                    {
                        "condition": {"call": "required", "args": {"value": {"path": "/who"}}},
                        "message": "Required",
                    }
                ],
            },
            {
                "id": "why",
                "component": "TextField",
                "label": "Why",
                "value": {"path": "/why"},
                "checks": [bad_check],
            },
            {"id": "send", "component": "Button", "child": "lbl", "action": {"event": {"name": "submit"}}},
            {"id": "lbl", "component": "Text", "text": "Send"},
        ]

    @staticmethod
    def _emitted(generator):
        return json.loads(generator.send.call_args_list[1].args[0])["a2ui"]["updateComponents"]["components"]

    def test_the_surface_ships_without_the_offending_check(self):
        tool, generator = _tool()
        # `message` nested inside `condition` is what models actually produce; pruning it
        # leaves a check with no message, which the catalog refuses.
        tool.execute(components=self._surface({"condition": {"call": "nonsense", "args": {}}}))
        components = self._emitted(generator)
        assert len(components) == 5, "the form must survive intact"
        assert not next(c for c in components if c["id"] == "why").get("checks")

    def test_every_other_field_keeps_its_check(self):
        """Greedy, not scorched earth: only the component that breaks validation loses."""
        tool, generator = _tool()
        tool.execute(components=self._surface({"condition": {"call": "nonsense", "args": {}}}))
        who = next(c for c in self._emitted(generator) if c["id"] == "who")
        assert who["checks"][0]["message"] == "Required"

    def test_a_valid_surface_keeps_all_of_them(self):
        tool, generator = _tool()
        tool.execute(
            components=self._surface(
                {"condition": {"call": "required", "args": {"value": {"path": "/why"}}}, "message": "Required"}
            )
        )
        assert all(c.get("checks") for c in self._emitted(generator) if c["id"] in ("who", "why"))


class TestModalTriggerAction:
    """A Modal's trigger needs an action to exist, and never gets to use it.

    The catalog makes `action` required on every Button. A trigger's click opens a dialog,
    so models leave it out — and the whole surface was refused over a property that carries
    no behaviour: "component 'modal_btn' (Button) does not match the catalog: it requires
    ['action']". Observed live on the second surface of a survey flow.
    """

    @staticmethod
    def _surface(trigger_action=None):
        trigger = {"id": "info_btn", "component": "Button", "child": "info_label"}
        if trigger_action:
            trigger["action"] = trigger_action
        return [
            {"id": "root", "component": "Column", "children": ["info_modal", "send"]},
            {"id": "info_modal", "component": "Modal", "trigger": "info_btn", "content": "info_text"},
            {"id": "info_text", "component": "Text", "text": "Details"},
            trigger,
            {"id": "info_label", "component": "Text", "text": "More info"},
            {"id": "send", "component": "Button", "child": "send_label", "action": {"event": {"name": "submit"}}},
            {"id": "send_label", "component": "Text", "text": "Send"},
        ]

    def test_the_surface_survives_a_trigger_without_an_action(self):
        tool, generator = _tool()
        tool.execute(components=self._surface())
        emitted = json.loads(generator.send.call_args_list[1].args[0])["a2ui"]
        trigger = next(c for c in emitted["updateComponents"]["components"] if c["id"] == "info_btn")
        assert trigger["action"] == {"event": {"name": "openModal"}}

    def test_an_action_the_agent_declared_is_left_alone(self):
        tool, generator = _tool()
        tool.execute(components=self._surface(trigger_action={"event": {"name": "peek"}}))
        emitted = json.loads(generator.send.call_args_list[1].args[0])["a2ui"]
        trigger = next(c for c in emitted["updateComponents"]["components"] if c["id"] == "info_btn")
        assert trigger["action"] == {"event": {"name": "peek"}}

    def test_the_supplied_action_cannot_be_used_as_an_answer(self):
        """It exists for the schema, not for the user — the intake must not accept it."""
        from codemie.service.conversation.a2ui_intake import _button_event_names

        tool, generator = _tool()
        tool.execute(components=self._surface())
        emitted = json.loads(generator.send.call_args_list[1].args[0])["a2ui"]
        components = emitted["updateComponents"]["components"]
        assert _button_event_names(components) == {"submit"}


class TestPropertiesOutsideTheCatalog:
    """A property the catalog does not define is dropped, not fatal.

    Seen repeatedly in manual testing: an assistant whose task is to show tier-gated
    options kept marking them `disabled`, and later `description`. The catalog closes an
    option at {label, value}, so the entire form was discarded and the user got nothing —
    strictly worse than the form without decoration the protocol cannot carry. Emitting
    exactly what the catalog defines IS the contract; the answer is still validated
    against the options the surface declared, so nothing is loosened server-side.

    `disabled` is the exception, and it is the reason this class has two cases: dropping a
    description loses decoration, while dropping a gate hands back the very option it
    withheld. That one removes the option instead.
    """

    def _gated_surface(self, extra):
        return [
            {"id": "root", "component": "Column", "children": ["modules", "ok"]},
            {
                "id": "modules",
                "component": "ChoicePicker",
                "label": "Modules",
                "variant": "multipleSelection",
                "value": {"path": "/modules"},
                "options": [
                    {"label": "SSO (Business plan and above)", "value": "sso", **extra},
                    {"label": "Open API", "value": "api"},
                ],
            },
            *_button_surface()[1:],
        ]

    def test_the_form_still_reaches_the_user(self):
        tool, generator = _tool()
        tool.execute(components=self._gated_surface({"description": "Business only"}))
        emitted = json.loads(generator.send.call_args_list[1].args[0])["a2ui"]
        options = emitted["updateComponents"]["components"][1]["options"]
        assert options == [
            {"label": "SSO (Business plan and above)", "value": "sso"},
            {"label": "Open API", "value": "api"},
        ]

    def test_a_gated_option_is_removed_rather_than_ungated(self):
        """`disabled` is the one property whose removal would reverse what it said.

        Stripping it hands the option back to the user as selectable, and the stored
        surface no longer records the gate — so the intake finds the value among those the
        surface offered and confirms the answer. Dropping the option keeps the meaning.
        """
        tool, generator = _tool()
        tool.execute(components=self._gated_surface({"disabled": True}))
        emitted = json.loads(generator.send.call_args_list[1].args[0])["a2ui"]
        options = emitted["updateComponents"]["components"][1]["options"]
        assert options == [{"label": "Open API", "value": "api"}]

    def test_everything_the_catalog_defines_survives(self):
        tool, generator = _tool()
        tool.execute(components=self._gated_surface({"description": "Business only"}))
        picker = json.loads(generator.send.call_args_list[1].args[0])["a2ui"]["updateComponents"]["components"][1]
        assert picker["variant"] == "multipleSelection"
        assert picker["value"] == {"path": "/modules"}
        assert picker["label"] == "Modules"

    def test_an_unknown_component_is_still_rejected(self):
        """Pruning removes properties; it never rescues a component the catalog lacks."""
        tool, generator = _tool()
        with pytest.raises(ValueError, match="Invalid A2UI surface"):
            tool.execute(components=[{"id": "root", "component": "NotAComponent"}])
        _assert_no_surface_streamed(generator)

    def test_the_agents_own_surface_is_not_mutated(self):
        """Pruning works on a copy: the caller's list must not change under it."""
        tool, _ = _tool()
        components = self._gated_surface({"disabled": True})
        tool.execute(components=components)
        assert components[1]["options"][0]["disabled"] is True


class TestSurfaceIsAnswerable:
    """``return_direct`` ends the turn expecting the user's action envelope, and only
    Button declares an action — so a surface without one strands the conversation on a
    form the user cannot send, which the intake would reject anyway."""

    def test_execute_rejects_a_surface_with_no_button(self):
        tool, generator = _tool()
        with pytest.raises(ValueError, match="no Button with an action event"):
            tool.execute(
                components=[
                    {"id": "root", "component": "Column", "children": ["prompt", "answer"]},
                    {"id": "prompt", "component": "Text", "text": "Your name?"},
                    {
                        "id": "answer",
                        "component": "TextField",
                        "label": "Name",
                        "value": {"path": "/name"},
                    },
                ]
            )
        _assert_no_surface_streamed(generator)

    def test_execute_rejects_a_button_whose_event_name_is_empty(self):
        tool, generator = _tool()
        with pytest.raises(ValueError, match="no Button with an action event"):
            tool.execute(components=_button_surface(action={"event": {"name": ""}}))
        _assert_no_surface_streamed(generator)

    def test_execute_rejects_a_submit_button_nothing_renders(self):
        """Seen live: the model emitted the Button but never wired it into the layout.

        `root` listed only the ChoicePicker, so the surface reached the user as a form with
        no way to send it. An orphan component is legal A2UI — the catalog validator only
        rejects the opposite case, a reference to a component that does not exist — so the
        submit control has to be checked for reachability, not mere presence.
        """
        tool, generator = _tool()
        with pytest.raises(ValueError, match="no Button with an action event"):
            tool.execute(
                components=[
                    {"id": "root", "component": "Column", "children": ["pick"]},
                    {
                        "id": "pick",
                        "component": "ChoicePicker",
                        "label": "Next step",
                        "value": {"path": "/next"},
                        "options": [{"label": "Continue", "value": "go"}],
                    },
                    *_button_surface()[1:],  # the Button, referenced by nothing
                ]
            )
        _assert_no_surface_streamed(generator)

    def test_execute_accepts_a_button_reachable_through_a_container(self):
        tool, generator = _tool()
        tool.execute(
            components=[
                {"id": "root", "component": "Column", "children": ["card"]},
                {"id": "card", "component": "Card", "child": "inner"},
                {"id": "inner", "component": "Column", "children": ["ok"]},
                *_button_surface()[1:],
            ]
        )
        assert generator.send.call_count == 2

    def test_execute_accepts_a_button_reachable_through_tabs(self):
        tool, generator = _tool()
        tool.execute(
            components=[
                {"id": "root", "component": "Column", "children": ["tabs"]},
                {"id": "tabs", "component": "Tabs", "tabs": [{"title": "One", "child": "ok"}]},
                *_button_surface()[1:],
            ]
        )
        assert generator.send.call_count == 2

    def test_execute_rejects_when_the_only_button_opens_a_modal(self):
        """A Modal's trigger opens the dialog — the renderer never dispatches its action."""
        tool, generator = _tool()
        with pytest.raises(ValueError, match="no Button with an action event"):
            tool.execute(
                components=[
                    {"id": "root", "component": "Column", "children": ["dialog"]},
                    {"id": "dialog", "component": "Modal", "trigger": "ok", "content": "body"},
                    {"id": "body", "component": "Text", "text": "Inside"},
                    *_button_surface()[1:],
                ]
            )
        _assert_no_surface_streamed(generator)

    def test_execute_accepts_a_surface_whose_button_declares_an_event(self):
        tool, generator = _tool()
        tool.execute(components=_button_surface())
        assert generator.send.call_count == 2


def test_execute_rejects_unknown_component():
    tool, generator = _tool()
    with pytest.raises(ValueError, match="Invalid A2UI surface"):
        tool.execute(components=[{"id": "root", "component": "NotAComponent"}])
    _assert_no_surface_streamed(generator)


class TestSurfaceBounds:
    """Agent-authored surfaces are steerable via prompt injection, so their breadth and
    serialized size are bounded BEFORE validation: an oversized surface is validated,
    streamed and then persisted into conversation history forever."""

    def test_execute_rejects_too_many_components(self):
        tool, generator = _tool()
        components = [{"id": "root", "component": "Column", "children": []}] + [
            {"id": f"t{index}", "component": "Text", "text": "x"} for index in range(MAX_SURFACE_COMPONENTS)
        ]
        with pytest.raises(ValueError, match="too many components"):
            tool.execute(components=components)
        _assert_no_surface_streamed(generator)

    def test_execute_rejects_oversized_surface_payload(self):
        tool, generator = _tool()
        components = [{"id": "root", "component": "Text", "text": "x" * (MAX_SURFACE_PAYLOAD_BYTES + 1)}]
        with pytest.raises(ValueError, match="too large"):
            tool.execute(components=components)
        _assert_no_surface_streamed(generator)

    def test_execute_counts_data_model_towards_the_size_bound(self):
        tool, generator = _tool()
        with pytest.raises(ValueError, match="too large"):
            tool.execute(
                components=[{"id": "root", "component": "Text", "text": "hi"}],
                data_model={"blob": "x" * (MAX_SURFACE_PAYLOAD_BYTES + 1)},
            )
        _assert_no_surface_streamed(generator)

    def test_execute_accepts_a_surface_at_the_component_limit(self):
        tool, generator = _tool()
        # Two slots go to the submit Button and its label, which every surface needs.
        child_ids = [f"t{index}" for index in range(MAX_SURFACE_COMPONENTS - 3)]
        components = [{"id": "root", "component": "Column", "children": [*child_ids, "ok"]}] + [
            {"id": child_id, "component": "Text", "text": "x"} for child_id in child_ids
        ]
        components += _button_surface()[1:]
        tool.execute(components=components)
        assert generator.send.call_count == 2


def test_tool_is_return_direct_and_named():
    tool, _ = _tool()
    assert tool.name == REQUEST_USER_INPUT_TOOL_NAME == "request_user_input"
    assert tool.return_direct is True


class TestTheModelGetsASecondChance:
    """A refused surface goes back to the agent instead of killing the turn.

    `return_direct` routes on the tool NAME, so the graph could not tell a rejected call
    from an accepted one and every authoring slip was fatal on the first try. On the graph
    runtime the tool ends the turn itself, which is what makes the difference visible.
    """

    BROKEN = [{"id": "root", "component": "Column", "children": ["gone"]}]
    VALID = [
        {"id": "root", "component": "Column", "children": ["send"]},
        {"id": "send", "component": "Button", "child": "lbl", "action": {"event": {"name": "submit"}}},
        {"id": "lbl", "component": "Text", "text": "Send"},
    ]

    @staticmethod
    def _retrying_tool():
        generator = MagicMock()
        tool = RequestUserInputTool(thread_generator=generator, return_direct=False)
        # Set here because these tests drive `_run` directly; in production the framework
        # hands the id to `_parse_input`, which is covered by its own test below.
        tool.answering_call_id = "call-1"
        return tool, generator

    def test_the_first_refusal_is_handed_to_the_agent_not_the_user(self):
        tool, generator = self._retrying_tool()
        with pytest.raises(Exception):  # noqa: B017 - the framework turns this into a ToolMessage
            tool._run(components=self.BROKEN)
        assert _notices(generator) == [], "the user is about to see the corrected form, not an apology"
        _assert_no_surface_streamed(generator)

    def test_the_reason_survives_so_the_agent_can_act_on_it(self):
        tool, _ = self._retrying_tool()
        with pytest.raises(Exception, match="non-existent component") as raised:
            tool._run(components=self.BROKEN)
        assert "gone" in str(raised.value), "the agent needs to know WHICH reference is dangling"

    def test_the_turn_ends_once_the_attempts_are_used_up(self):
        from codemie.core.a2ui.config import MAX_SURFACE_ATTEMPTS

        tool, generator = self._retrying_tool()
        for _ in range(MAX_SURFACE_ATTEMPTS - 1):
            with pytest.raises(Exception):  # noqa: B017
                tool._run(components=self.BROKEN)
        assert _notices(generator) == []

        result = tool._run(components=self.BROKEN)
        assert [chunk["generated_chunk"] for chunk in _notices(generator)] == [
            SURFACE_REJECTED_NOTICE
        ], "now the user is owed an explanation"
        assert result.goto == "__end__", "a model that cannot satisfy the catalog must not loop"
        assert result.update["messages"][0].content == SURFACE_REJECTED_NOTICE

    def test_a_surface_that_validates_ends_the_turn_immediately(self):
        tool, generator = self._retrying_tool()
        result = tool._run(components=self.VALID)
        assert result.goto == "__end__"
        message = result.update["messages"][0]
        assert message.tool_call_id == "call-1", "the command must answer the call it was made by"
        assert message.content == ""
        streamed = [json.loads(call.args[0]) for call in generator.send.call_args_list]
        assert [chunk for chunk in streamed if chunk.get("a2ui")], "the surface must reach the client"

    def test_a_valid_form_still_works_on_the_classic_runtime(self):
        """The regression an injected argument caused: it forces a full ToolCall on every
        caller, and the classic AgentExecutor calls tools with a plain dict — so a form
        that was perfectly valid came back as the rejection notice instead of rendering."""
        tool, generator = _tool()
        assert tool.run({"components": self.VALID}) == ""
        streamed = [json.loads(call.args[0]) for call in generator.send.call_args_list]
        assert [chunk for chunk in streamed if chunk.get("a2ui")], "the surface must reach the client"
        assert _notices(generator) == []

    def test_the_call_id_arrives_the_way_the_framework_delivers_it(self):
        """No injected ARGUMENT: declaring one forces every caller to use a full ToolCall,
        and the classic AgentExecutor does not — a valid form there came back as the
        rejection notice. The framework already hands the id to `_parse_input`."""
        generator = MagicMock()
        tool = RequestUserInputTool(thread_generator=generator, return_direct=False)
        result = tool.invoke(
            {"name": "request_user_input", "args": {"components": self.VALID}, "id": "call-42", "type": "tool_call"}
        )
        assert tool.answering_call_id == "call-42"
        assert result.update["messages"][0].tool_call_id == "call-42"

    def test_the_classic_runtime_is_untouched(self):
        """No Command there, and no retry: `return_direct` still ends the turn as before."""
        tool, generator = _tool()
        assert tool.return_direct is True
        with pytest.raises(Exception):  # noqa: B017
            tool._run(components=self.BROKEN)
        assert [chunk["generated_chunk"] for chunk in _notices(generator)] == [SURFACE_REJECTED_NOTICE]


def test_args_schema_exposes_components_and_data_model():
    """What the MODEL sees, which is not the same as what the tool declares.

    `tool_call_id` is injected by the framework and must never reach the prompt: the model
    receives the catalog and its own two arguments, and nothing about our plumbing.
    """
    tool, _ = _tool()
    schema = tool.tool_call_schema.model_json_schema()
    assert set(schema["properties"]) == {"components", "data_model"}
    assert schema["required"] == ["components"]
    assert "tool_call_id" not in tool.tool_call_schema.model_fields


class TestToolkitAppend:
    def test_appended_when_interactive_enabled(self):
        from unittest.mock import patch

        from codemie.service.tools.toolkit_service import ToolkitService

        assistant = MagicMock()
        assistant.interactive_enabled = True
        generator = MagicMock()
        flag = MagicMock()
        flag.is_feature_enabled.return_value = True
        flag.get_feature_setting.return_value = None
        with patch("codemie.service.tools.toolkit_service.customer_config", flag):
            tools = ToolkitService._append_request_user_input_tool_if_enabled([], assistant, generator, _request())
        assert len(tools) == 1
        assert tools[0].name == REQUEST_USER_INPUT_TOOL_NAME

    def test_appended_for_request_parsed_from_wire_payload(self):
        # Guards the FE<->BE wire name end to end: a request built from the JSON the
        # frontend actually sends must satisfy the capability gate.
        from unittest.mock import patch

        from codemie.service.tools.toolkit_service import ToolkitService

        assistant = MagicMock()
        assistant.interactive_enabled = True
        flag = MagicMock()
        flag.is_feature_enabled.return_value = True
        with patch("codemie.service.tools.toolkit_service.customer_config", flag):
            tools = ToolkitService._append_request_user_input_tool_if_enabled(
                [], assistant, MagicMock(), _wire_request()
            )
        assert len(tools) == 1

    def test_noop_when_client_does_not_declare_catalog(self):
        # Stale tabs / non-A2UI clients (no a2ui_supported_catalogs on the request)
        # must get no tool so the agent falls back to plain-text questions.
        from unittest.mock import patch

        from codemie.service.tools.toolkit_service import ToolkitService

        assistant = MagicMock()
        assistant.interactive_enabled = True
        flag = MagicMock()
        flag.is_feature_enabled.return_value = True
        with patch("codemie.service.tools.toolkit_service.customer_config", flag):
            tools = ToolkitService._append_request_user_input_tool_if_enabled(
                [], assistant, MagicMock(), _request(declares_catalog=False)
            )
        assert tools == []

    def test_noop_when_interactive_disabled(self):
        from codemie.service.tools.toolkit_service import ToolkitService

        assistant = MagicMock()
        assistant.interactive_enabled = False
        tools = ToolkitService._append_request_user_input_tool_if_enabled([], assistant, MagicMock(), _request())
        assert tools == []

    def test_noop_when_flag_attribute_missing(self):
        from codemie.service.tools.toolkit_service import ToolkitService

        assistant = object()  # no interactive_enabled attribute at all
        tools = ToolkitService._append_request_user_input_tool_if_enabled([], assistant, MagicMock(), _request())
        assert tools == []

    def test_noop_without_thread_generator(self):
        from codemie.service.tools.toolkit_service import ToolkitService

        assistant = MagicMock()
        assistant.interactive_enabled = True
        tools = ToolkitService._append_request_user_input_tool_if_enabled([], assistant, None, _request())
        assert tools == []

    def test_noop_when_customer_flag_disabled(self):
        from unittest.mock import patch

        from codemie.service.tools.toolkit_service import ToolkitService

        assistant = MagicMock()
        assistant.interactive_enabled = True
        flag = MagicMock()
        flag.is_feature_enabled.return_value = False
        with patch("codemie.service.tools.toolkit_service.customer_config", flag):
            tools = ToolkitService._append_request_user_input_tool_if_enabled([], assistant, MagicMock(), _request())
        assert tools == []


def test_tool_points_at_a_schema_block_that_exists():
    """The reference integration binds the tool to the schema through the system prompt.

    A2UI's own tool-based toolset declares a loosely-typed argument and tells the model
    where the schema is; that pointer is the whole contract. Ours named a section heading
    that the rendered prompt never contained, so the model was sent to a place that did
    not exist and had to find the schema inside 40k of JSON by itself.
    """
    tool, _ = _tool()
    description = tool.args_schema.model_fields["components"].description
    assert a2ui_catalog.SCHEMA_BLOCK_START in description
    assert a2ui_catalog.schema_block_is_present(), "the prompt no longer carries that block"


class TestRootNaming:
    """The catalog requires the tree to start at a component with id 'root'.

    Seen live on the review step: the model named its top container `review_root` and the
    whole surface was discarded, though the layout was otherwise complete. When exactly
    one component is referenced by nobody, it IS the top of the tree — renaming it changes
    no structure and no meaning, and the alternative is showing the user nothing.
    """

    def _summary(self, top_id):
        return [
            {"id": top_id, "component": "Column", "children": ["line", "ok"]},
            {"id": "line", "component": "Text", "text": "Total: 100 USD"},
            *_button_surface()[1:],
        ]

    def test_the_single_top_container_becomes_root(self):
        tool, generator = _tool()
        tool.execute(components=self._summary("review_root"))
        emitted = json.loads(generator.send.call_args_list[1].args[0])["a2ui"]
        assert emitted["updateComponents"]["components"][0]["id"] == "root"

    def test_an_explicit_root_is_left_alone(self):
        tool, generator = _tool()
        tool.execute(components=self._summary("root"))
        emitted = json.loads(generator.send.call_args_list[1].args[0])["a2ui"]
        assert [c["id"] for c in emitted["updateComponents"]["components"]][0] == "root"

    def test_two_candidates_stay_ambiguous_and_are_refused(self):
        """Guessing which of several orphans is the tree would be inventing a layout."""
        tool, generator = _tool()
        components = [*self._summary("review_root"), {"id": "stray", "component": "Text", "text": "?"}]
        with pytest.raises(ValueError, match="Invalid A2UI surface"):
            tool.execute(components=components)
        _assert_no_surface_streamed(generator)

    def test_the_agents_own_list_is_not_mutated(self):
        tool, _ = _tool()
        components = self._summary("review_root")
        tool.execute(components=components)
        assert components[0]["id"] == "review_root"
