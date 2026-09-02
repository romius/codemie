# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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
#

"""Single source of truth for the A2UI catalog used by the backend.

One static catalog: the full official Basic Catalog. The LLM prompt section and
component validation are derived from it by the A2UI SDK; the catalog id it
advertises must match what the frontend renderer registry declares
(contract-tested against the frontend manifest).
"""

import json
from functools import lru_cache
from typing import Any, Optional

from a2ui.validation.validator import A2uiValidator

from codemie.core.a2ui import envelopes as envelopes_wire
from codemie.core.a2ui.config import (
    ACTION_ENVELOPE_KEYS,
    ACTION_OPTIONAL_FIELDS,
    ACTION_REQUIRED_FIELDS,
    ACCEPTED_ACTION_VERSIONS,
    CATALOG_ID,
    MAX_BINDING_DEPTH,
    MODAL_TRIGGER_ACTION,
    SCHEMA_BLOCK_END,
    SCHEMA_BLOCK_START,
    format_,
    selected_catalog,
)

#: JSON Schema keyword the catalog stores its type definitions under.
_DEFS = "$defs"

__all__ = ["CATALOG_ID", "MAX_BINDING_DEPTH", "SCHEMA_BLOCK_END", "SCHEMA_BLOCK_START"]

_ROLE_DESCRIPTION = (
    "You can request structured user input by showing interactive UI "
    "(forms, buttons, choices) in the chat: call the request_user_input tool "
    "with A2UI Basic Catalog components. Calling it ends your turn; the "
    "user's structured answer arrives as the next message. The turn can only "
    "resume through a Button the user can reach, so this tool rejects a surface "
    "that offers none. Showing a surface does not silence you: whatever you write "
    "in the same message as the tool call is shown to the user above the form, so "
    "introduce it in a sentence when that helps."
)

# Everything the model is told about COMPONENTS comes from the catalog itself (see
# render_prompt_section): describing them in our own words would create a second,
# hand-maintained spec that silently drifts from the catalog — exactly the cost this
# migration removes. This text therefore covers only what the catalog cannot state: how
# this backend transports a surface, since the schemas describe whole wire messages while
# the tool takes the component list alone and the server wraps it.
_TRANSPORT_DESCRIPTION = (
    "Deliver the surface by calling the request_user_input tool. Put the components in its "
    "`components` argument as a flat list: the component with id 'root' first, every parent "
    "before its children, children referenced by id. Pass components only — do not wrap them "
    "in createSurface, updateComponents or any other message, and do not put A2UI JSON in "
    "your reply text; the server builds the wire messages and streams them to the client. "
    "Initial values for bound inputs go in the optional `data_model` argument, as the object "
    "those paths address: a component bound to `/full_name` reads `{\"full_name\": ...}`, one "
    "bound to `/profile/name` reads `{\"profile\": {\"name\": ...}}`. Do not use the path "
    "itself as a key."
)

# The example is validated in the test suite, so a broken one cannot ship. Models follow a
# worked example far more reliably than a 40k-character schema: all three authoring failures
# seen in practice (legacy shape, action with both branches, dangling child id) are shapes
# this example makes obvious.
EXAMPLE_SURFACE = [
    {"id": "root", "component": "Column", "children": ["question", "name", "send"]},
    {"id": "question", "component": "Text", "text": "What should I call you?"},
    {
        "id": "name",
        "component": "TextField",
        "label": "Your name",
        "variant": "shortText",
        "value": {"path": "/name"},
        "checks": [
            {
                "condition": {"call": "required", "args": {"value": {"path": "/name"}}},
                "message": "Please enter your name",
            }
        ],
    },
    {"id": "send", "component": "Button", "child": "send-label", "action": {"event": {"name": "submit"}}},
    {"id": "send-label", "component": "Text", "text": "Send"},
]


def schema_block_is_present() -> bool:
    """True when the rendered section really carries the delimiters the tool points at."""
    section = render_prompt_section()
    return SCHEMA_BLOCK_START in section and SCHEMA_BLOCK_END in section


def component_names() -> tuple[str, ...]:
    schema = selected_catalog().catalog_schema or {}
    components = schema.get("components", {})
    return tuple(sorted(components))


def _collect_properties(schema: Any, definitions: dict, seen: set[str]) -> set[str]:
    """Property names a component schema accepts, following $ref and allOf composition."""
    if not isinstance(schema, dict):
        return set()
    names: set[str] = set(schema.get("properties") or {})
    for keyword in ("allOf", "anyOf", "oneOf"):
        for branch in schema.get(keyword) or []:
            names |= _collect_properties(branch, definitions, seen)
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference not in seen:
        seen.add(reference)
        names |= _collect_properties(definitions.get(reference.split("/")[-1], {}), definitions, seen)
    return names


def _definitions() -> dict:
    """Every `$defs` block a component schema may resolve a `$ref` against.

    The catalog splits them in two — its own and the shared common types — and every
    caller needs both merged. Built here rather than at each call site so the two sources
    cannot drift apart, and cheap enough to rebuild: the catalog itself is cached.
    """
    return {
        **((selected_catalog().catalog_schema or {}).get(_DEFS) or {}),
        **((selected_catalog().common_types_schema or {}).get(_DEFS) or {}),
    }


def component_properties(name: str) -> tuple[str, ...]:
    """Every property the catalog lets a component carry, composition resolved.

    A catalog component is described as an ``allOf`` over shared fragments (ComponentCommon,
    Checkable, ...) plus its own block, so the property set only exists once those are
    followed. Used by the BE<->FE contract test to compare the two halves property by
    property, not just by component name.
    """
    catalog_schema = selected_catalog().catalog_schema or {}
    definitions = _definitions()
    component = (catalog_schema.get("components") or {}).get(name)
    return tuple(sorted(_collect_properties(component, definitions, set())))


@lru_cache(maxsize=1)
def _validator() -> A2uiValidator:
    return A2uiValidator(selected_catalog())


def _resolve(schema: Any, definitions: dict) -> dict:
    """Follow a $ref one hop so the caller sees a schema with real keywords."""
    if not isinstance(schema, dict):
        return {}
    reference = schema.get("$ref")
    if isinstance(reference, str):
        return definitions.get(reference.split("/")[-1], {})
    return schema


def _property_schemas(schema: Any, definitions: dict, seen: set[str]) -> dict[str, Any]:
    """Sub-schema per property name, with allOf fragments and $ref merged in."""
    if not isinstance(schema, dict):
        return {}
    merged: dict[str, Any] = dict(schema.get("properties") or {})
    for keyword in ("allOf", "anyOf", "oneOf"):
        for branch in schema.get(keyword) or []:
            merged.update(_property_schemas(branch, definitions, seen))
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference not in seen:
        seen.add(reference)
        merged.update(_property_schemas(definitions.get(reference.split("/")[-1], {}), definitions, seen))
    return merged


def _unknown_property_paths(value: Any, schema: Any, definitions: dict, prefix: str = "") -> list[str]:
    """Properties the submitted object carries that its catalog schema does not define.

    The catalog closes every object, so one unlisted key rejects the whole surface — but
    jsonschema only reports "not valid under any of the given schemas" for the component,
    leaving the offending key unnamed. Nested objects (a ChoicePicker option, a Tabs entry)
    are walked too, which is where the key usually is. Everything is read from the catalog,
    so this stays a description OF the contract rather than a second copy of it.
    """
    if not isinstance(value, dict) or not isinstance(schema, dict):
        return []
    allowed = _collect_properties(schema, definitions, set())
    unknown = [f"{prefix}{key}" for key in value if allowed and key not in allowed]
    properties = _property_schemas(schema, definitions, set())
    for key, nested in value.items():
        sub = _resolve(properties.get(key), definitions)
        if not sub:
            continue
        if isinstance(nested, dict):
            unknown += _unknown_property_paths(nested, sub, definitions, f"{prefix}{key}.")
        elif isinstance(nested, list):
            items = _resolve(sub.get("items"), definitions)
            for index, item in enumerate(nested):
                unknown += _unknown_property_paths(item, items, definitions, f"{prefix}{key}[{index}].")
    return unknown


def _prune(value: Any, schema: Any, definitions: dict) -> Any:
    """Copy of ``value`` without the properties its catalog schema does not define."""
    if isinstance(value, list):
        items = _resolve(schema.get("items"), definitions) if isinstance(schema, dict) else {}
        return [_prune(item, items, definitions) for item in value]
    if not isinstance(value, dict) or not isinstance(schema, dict):
        return value
    allowed = _collect_properties(schema, definitions, set())
    properties = _property_schemas(schema, definitions, set())
    pruned = {}
    for key, nested in value.items():
        if allowed and key not in allowed:
            continue
        sub = _resolve(properties.get(key), definitions)
        pruned[key] = _prune(nested, sub, definitions) if sub else nested
    return pruned


def _without_gated_options(component: dict) -> tuple[dict, list[str]]:
    """Remove the options the agent marked unavailable, rather than their marking.

    A gate is one property the catalog has no room for, and dropping it alone inverts what
    it said: the option comes back selectable, and because the stored surface no longer
    records the gate, the intake finds the value among those the surface offered and
    confirms the answer as legitimate. Removing the option instead keeps the meaning the
    agent expressed — the choice is not available — while what reaches the client is still
    exactly the catalog's vocabulary.
    """
    options = component.get("options")
    if not isinstance(options, list):
        return component, []
    kept = [option for option in options if not (isinstance(option, dict) and option.get("disabled"))]
    if len(kept) == len(options):
        return component, []
    gated = [str(option.get("value")) for option in options if isinstance(option, dict) and option.get("disabled")]
    return {**component, "options": kept}, gated


def prune_to_catalog(components: list) -> tuple[list, list[str]]:
    """Return the surface carrying only what the catalog defines, plus what was dropped.

    Agents keep reaching for expressiveness the Basic Catalog does not have — a
    `description` on a ChoicePicker option, a `hint` on a field. Every object in the
    catalog is closed, so one such property used to discard the whole form and leave the
    user with nothing, while the intent was usually already spelled out in the label.
    Dropping the property is not an extension of the protocol but conformance to it: what
    reaches the client is exactly the catalog's vocabulary. The answer is still validated
    against the options the surface declared, so nothing is loosened.

    A gated option is the exception, because dropping its `disabled` would not preserve
    the meaning but reverse it — that one is handled by removing the option itself.
    """
    catalog_schema = selected_catalog().catalog_schema or {}
    definitions = _definitions()
    schemas = catalog_schema.get("components") or {}
    pruned: list = []
    dropped: list[str] = []
    for component in components if isinstance(components, list) else []:
        # The component name is agent-authored and reaches this before any schema check:
        # a non-string (an object, a list) would raise TypeError on lookup instead of
        # failing validation cleanly, so it is filtered rather than trusted.
        name = component.get("component") if isinstance(component, dict) else None
        schema = schemas.get(name) if isinstance(name, str) else None
        if not schema:
            pruned.append(component)
            continue
        component, gated = _without_gated_options(component)
        dropped += [f"{component.get('id')}.options[{value}]" for value in gated]
        paths = _unknown_property_paths(component, schema, definitions)
        dropped += [f"{component.get('id')}.{path}" for path in paths]
        pruned.append(_prune(component, schema, definitions) if paths else component)
    return pruned, dropped


def rekey_pointer_data_model(data_model: Optional[dict]) -> tuple[Optional[dict], list[str]]:
    """Turn a data model keyed BY the binding path into the object that path addresses.

    A component binds with `value: {"path": "/full_name"}`, so "keyed by the path" is an
    entirely reasonable reading — and one our own prompt used to invite. The intake reads
    the model as a tree, finds nothing bound under a key literally called `/full_name`, and
    the emitter then drops every answer as unanswerable: the user submits a filled form and
    gets an empty one back. Observed live on a 16-field survey, all 16 values discarded.

    The rewrite is only applied to keys that are unambiguous pointers — a leading slash is
    not legal in a plain data-model key — so a model that got it right is untouched.
    """
    if not isinstance(data_model, dict) or not data_model:
        return data_model, []
    pointers = [key for key in data_model if isinstance(key, str) and key.startswith("/")]
    if not pointers:
        return data_model, []
    rebuilt: dict = {key: value for key, value in data_model.items() if key not in set(pointers)}
    for pointer in pointers:
        segments = [segment for segment in pointer.split("/") if segment]
        if not segments:
            continue
        node = rebuilt
        for segment in segments[:-1]:
            child = node.get(segment)
            node[segment] = child if isinstance(child, dict) else {}
            node = node[segment]
        node.setdefault(segments[-1], data_model[pointer])
    return rebuilt, pointers


def _looks_like_a_component(value: Any, names: frozenset) -> bool:
    """A dict is a component when it names one and carries an id — nothing else does.

    In the Basic Catalog a parent references its children BY ID, as strings; no property
    anywhere nests a component inline. So an object with both keys, found inside another
    component's property, is not that property's value — it is a component that landed in
    the wrong place.
    """
    return (
        isinstance(value, dict)
        and isinstance(value.get("id"), str)
        and isinstance(value.get("component"), str)
        and value["component"] in names
    )


def _extract_components(value: Any, names: frozenset, found: list) -> Any:
    """Strip component objects out of a property, collecting them as we go."""
    if isinstance(value, list):
        kept = []
        for item in value:
            if _looks_like_a_component(item, names):
                found.append(item)
                continue
            kept.append(_extract_components(item, names, found))
        return kept
    if isinstance(value, dict):
        return {key: _extract_components(nested, names, found) for key, nested in value.items()}
    return value


def hoist_misplaced_components(components: list) -> tuple[list, list[str]]:
    """Lift components the agent nested inside another component's property back to the top.

    Models lose track of the array they are writing and continue a surface inside the last
    property they opened. Observed live: a whole card — its Column, its ChoicePicker, its
    fields — ended up inside a TextField's ``checks``, while ``root.children`` still named
    them. The form was refused twice over: the checks no longer matched their schema, and
    the layout referenced components that did not exist at the top level.

    Both faults have the same cause and the same fix, so this runs before pruning would
    strip the nested components down to empty husks. Recovering them is not a guess: the
    catalog never nests a component inline, so anything carrying an id and a component name
    inside a property is misplaced by definition, and the surface already says where it
    belongs by referencing its id.
    """
    names = frozenset(component_names())
    declared = [component for component in components if isinstance(component, dict)]
    known = {component.get("id") for component in declared}
    hoisted: list = []
    rewritten: list = []
    for component in components:
        if not isinstance(component, dict):
            rewritten.append(component)
            continue
        found: list = []
        cleaned = {
            key: (value if key in ("id", "component") else _extract_components(value, names, found))
            for key, value in component.items()
        }
        rewritten.append(cleaned if found else component)
        hoisted.extend(found)

    # A hoisted component may itself carry others nested inside it, and an id already at the
    # top level wins — the misplaced copy is the accident.
    recovered: list = []
    queue = list(hoisted)
    while queue:
        candidate = queue.pop(0)
        nested: list = []
        cleaned = {
            key: (value if key in ("id", "component") else _extract_components(value, names, nested))
            for key, value in candidate.items()
        }
        queue.extend(nested)
        if cleaned.get("id") in known:
            continue
        known.add(cleaned.get("id"))
        recovered.append(cleaned)
    return rewritten + recovered, [str(component.get("id")) for component in recovered]


def _without_checks(component: dict) -> dict:
    return {key: value for key, value in component.items() if key != "checks"}


def drop_invalid_checks(components: list, validate) -> tuple[list, list[str]]:
    """Drop the `checks` that make a surface invalid, and name whose they were.

    `checks` are client-side validation and nothing else: the spec states they are UX only
    and never a security boundary, and every submitted answer is re-validated server-side
    against the stored surface regardless. They are also the only recursive part of the
    catalog — an expression grammar of nested `call`/`args` — which is where models
    actually go wrong. Observed live: a survey of 34 components was refused whole because
    one field tried to express "required only if department is Other" and recursed.

    Losing one field's inline hint is not comparable to losing the form, so the checks go
    and the surface ships. The greedy part matters: only the component that breaks
    validation loses its checks, so every other field keeps its hint. Dropping them all is
    the fallback for a surface no single removal fixes.

    ``validate`` is passed in rather than imported so this stays a pure function of the
    component list — the caller owns envelope assembly.
    """
    if not validate(components):
        return components, []

    carriers = [component for component in components if isinstance(component, dict) and component.get("checks")]
    for carrier in carriers:
        trial = [_without_checks(c) if c is carrier else c for c in components]
        if not validate(trial):
            return trial, [str(carrier.get("id"))]

    if not carriers:
        return components, []
    trial = [_without_checks(c) if c in carriers else c for c in components]
    if not validate(trial):
        return trial, [str(c.get("id")) for c in carriers]
    return components, []


def modal_trigger_ids(components: list) -> set[str]:
    """Ids of Buttons a Modal uses as its trigger — they open a dialog, they do not submit."""
    return {
        component["trigger"]
        for component in components
        if isinstance(component, dict)
        and component.get("component") == "Modal"
        and isinstance(component.get("trigger"), str)
    }


def give_modal_triggers_an_action(components: list) -> tuple[list, list[str]]:
    """Fill in the action a Modal's trigger needs to exist, plus the ids that got one.

    The catalog makes ``action`` required on every Button — including one whose only job is
    to open a dialog, where an action makes no sense and models routinely leave it out.
    Without this the whole surface is refused over a property that carries no behaviour:
    the renderer suppresses a trigger's dispatch (clicking it opens the dialog), and the
    intake does not accept a trigger's name as an answer. So the value is supplied rather
    than demanded, in the same spirit as naming the root — the form survives, and nothing
    about what the user can do changes.
    """
    triggers = modal_trigger_ids(components)
    if not triggers:
        return components, []
    filled: list[str] = []
    patched: list = []
    for component in components:
        if not isinstance(component, dict) or component.get("id") not in triggers:
            patched.append(component)
            continue
        if component.get("component") != "Button":
            patched.append(component)
            continue
        action = component.get("action")
        event = action.get("event") if isinstance(action, dict) else None
        if isinstance(event, dict) and event.get("name"):
            patched.append(component)
            continue
        filled.append(str(component.get("id")))
        patched.append({**component, "action": {"event": {"name": MODAL_TRIGGER_ACTION}}})
    return patched, filled


def referenced_ids(component: dict) -> list[str]:
    """Every id this component points at, wherever the catalog lets it point.

    Four shapes, because the catalog spells the same relationship four ways: a single
    `child`/`content`/`trigger`, a `children` list, and `tabs`, which hold theirs one level
    down as {"title": ..., "child": id}. Everything here is agent-authored, so a value that
    is not a string is not an id — a scalar where a list belongs would otherwise raise a
    TypeError that escapes the tool's rejection path instead of arriving as a validation
    error the user is told about.
    """
    if not isinstance(component, dict):
        return []
    singles = [component.get(field) for field in ("child", "content", "trigger")]
    children = component.get("children")
    listed = children if isinstance(children, list) else []
    tabs = component.get("tabs")
    nested = [tab.get("child") for tab in (tabs if isinstance(tabs, list) else []) if isinstance(tab, dict)]
    return [value for value in (*singles, *listed, *nested) if isinstance(value, str)]


def name_the_root(components: list) -> tuple[list, Optional[str]]:
    """Rename the single top-level component to 'root' when the agent called it otherwise.

    The catalog anchors a surface at id 'root'. Models routinely name the container after
    its purpose (`review_root`), which discards a layout that is otherwise complete. A
    component referenced by nobody IS the top of the tree, so when there is exactly one
    the rename is mechanical: no structure changes, and nothing points at the old id by
    definition. With several orphans the tree is genuinely ambiguous and picking one would
    be inventing a layout, so the surface is left to fail.
    """
    declared = [component for component in components if isinstance(component, dict)]
    if any(component.get("id") == "root" for component in declared):
        return components, None
    referenced: set[str] = set()
    for component in declared:
        referenced.update(referenced_ids(component))
    orphans = [c["id"] for c in declared if isinstance(c.get("id"), str) and c["id"] not in referenced]
    if len(orphans) != 1:
        return components, None
    renamed = orphans[0]
    return [{**c, "id": "root"} if c.get("id") == renamed else c for c in declared], renamed


def _collect_required(schema: Any, definitions: dict, seen: set[str]) -> set[str]:
    """Property names a component schema demands, following $ref and allOf composition."""
    if not isinstance(schema, dict):
        return set()
    names: set[str] = {name for name in schema.get("required") or [] if isinstance(name, str)}
    for branch in schema.get("allOf") or []:
        names |= _collect_required(branch, definitions, seen)
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference not in seen:
        seen.add(reference)
        names |= _collect_required(definitions.get(reference.split("/")[-1], {}), definitions, seen)
    return names


def _unknown_hint(component: dict) -> str:
    """Say what is wrong with this component in terms of its own property names.

    Two halves, because a rejected component is usually one or the other and the reader —
    the user, and the model on its next turn — can act on neither a bare "does not match"
    nor a JSON pointer. Missing required properties are named as well as undefined ones:
    an agent that writes `label` on a Button (which takes a `child`) is told both that
    `label` is not a Button property and that `child` is missing, which is the whole
    diagnosis. Without the second half, pruning the unknown property first would leave an
    error naming nothing at all.
    """
    catalog_schema = selected_catalog().catalog_schema or {}
    definitions = _definitions()
    schema = (catalog_schema.get("components") or {}).get(component.get("component"))
    if not schema:
        return ""
    parts = []
    unknown = _unknown_property_paths(component, schema, definitions)
    if unknown:
        parts.append(f"it does not define {sorted(unknown)}")
    missing = sorted(_collect_required(schema, definitions, set()) - set(component))
    if missing:
        parts.append(f"it requires {missing}")
    return f": {' and '.join(parts)}" if parts else ""


def _explain(error: Exception, envelopes: list[dict]) -> str:
    """Turn the SDK's validation failure into the part that is actually actionable.

    A wire message is a ``oneOf`` over createSurface / updateComponents / updateDataModel /
    deleteSurface, so ONE bad component makes jsonschema report every branch it tried:
    "'deleteSurface' is a required property", "Additional properties are not allowed
    ('updateComponents' was unexpected)", and so on. Those lines describe message shapes
    that were never in play. The structured details carry the real one under a path into
    ``updateComponents.components.N``, so it is picked out and the component named — both
    the user and, on its next turn, the model read this text.
    """
    details = getattr(error, "details", None) or []
    inner = [detail for detail in details if "." in getattr(detail, "path", "")]
    if not inner:
        return str(error)
    lines = []
    for detail in inner:
        component = envelopes_wire.component_at(envelopes, detail.path)
        if isinstance(component, dict):
            name = component.get("component")
            lines.append(
                f"component '{component.get('id')}' ({name}) does not match the catalog{_unknown_hint(component)}"
            )
        else:
            lines.append(f"{detail.path}: {detail.message}")
    return "; ".join(dict.fromkeys(lines))


def validate_envelopes(envelopes: list[dict], root_id: str | None = None) -> list[str]:
    """Validate a list of wire envelopes (messages + components inside them)
    against the catalog. Returns error strings ([] = valid)."""
    try:
        _validator().validate(envelopes, root_id=root_id)
    except Exception as error:  # SDK raises schema/topology-specific exceptions
        return [_explain(error, envelopes)]
    return []


@lru_cache(maxsize=1)
def render_prompt_section() -> str:
    """The A2UI section appended to an interactive assistant's system prompt.

    Deliberately assembled from the SDK's catalog description rather than from its
    ``generate_system_prompt`` helper: that helper documents the format's own transport
    (A2UI JSON wrapped in ``<a2ui-json>`` tags inside the reply) and omits the component
    schemas unless asked, which leaves the model without the one thing it needs — what a
    component actually looks like. Here the schemas are mandatory and the transport is
    described as what this backend really implements, a tool call.

    Three parts, and the worked example is one of them on purpose. It was removed once, on
    the argument that a hand-authored surface is a second description of the components —
    and the very next surface came back with every input carrying a literal ``value`` (``3``,
    ``false``, ``[]``) instead of a ``{"path": ...}`` binding. Nothing writes back to a
    literal, so the form rendered and could not be filled in. Measured on the same assistant
    and model within three hours: 13 of 13 inputs bound with the example, 0 of 13 without it.
    The catalog schema states that ``value`` accepts either form; only the example shows
    which one an input needs. It is validated by the test suite, so a broken one cannot ship.
    """
    catalog_description = format_().prompt_generator.catalog_description(include_schema=True)
    example = "A complete, valid `components` argument:\n" + json.dumps(EXAMPLE_SURFACE, indent=2)
    return "\n\n".join((_ROLE_DESCRIPTION, _TRANSPORT_DESCRIPTION, example, catalog_description))


# Client->server `action` envelope validation. The SDK ships no client_to_server
# JSON schema asset for 0.9.1 (assets/0.9.1/ holds only server_to_client.json,
# catalog.json, common_types.json), and its pydantic models
# (a2ui.core.schema.client_to_server.A2uiClientActionMessage) pin the version to
# Literal["v0.9"] and make sourceComponentId/timestamp/context required — stricter
# than the v0.9.1 wire this backend emits (WIRE_VERSION) and the renderer submits.
# So the documented wire contract is validated structurally here, with unknown keys
# forbidden (mirroring the SDK's StrictBaseModel extra="forbid"): a smuggled key
# such as catalogId must never reach the server, which resolves the catalog only
# from its own stored surface record.


def validate_action_envelope(envelope) -> list[str]:
    """Validate a client->server `action` wire envelope. Returns error strings ([] = valid)."""
    if not isinstance(envelope, dict):
        return ["Action envelope must be an object"]
    errors = [f"Unknown envelope key: {key}" for key in sorted(set(envelope) - ACTION_ENVELOPE_KEYS)]
    version = envelope.get("version")
    if version is not None and version not in ACCEPTED_ACTION_VERSIONS:
        errors.append(f"Unsupported action envelope version: {version!r}")
    action = envelope.get("action")
    if not isinstance(action, dict):
        errors.append("Action envelope must carry an 'action' object")
        return errors
    allowed = ACTION_REQUIRED_FIELDS | ACTION_OPTIONAL_FIELDS
    errors.extend(f"Unknown action key: {key}" for key in sorted(set(action) - set(allowed)))
    for field, expected_type in ACTION_REQUIRED_FIELDS.items():
        value = action.get(field)
        if not isinstance(value, expected_type) or not value:
            errors.append(f"Action field '{field}' must be a non-empty {expected_type.__name__}")
    for field, expected_type in ACTION_OPTIONAL_FIELDS.items():
        value = action.get(field)
        if value is not None and not isinstance(value, expected_type):
            errors.append(f"Action field '{field}' must be a {expected_type.__name__}")
    return errors
