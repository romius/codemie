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

"""Tests for A2UI fields on GeneratedMessage: persistence, LLM history, round-trip."""

from codemie.rest_api.models.conversation import ChatTurnData, Conversation, GeneratedMessage


def _surface_envelopes(surface_id="s1"):
    return [
        {"version": "v0.9.1", "createSurface": {"surfaceId": surface_id, "catalogId": "cat-1"}},
        {
            "version": "v0.9.1",
            "updateComponents": {
                "surfaceId": surface_id,
                "components": [{"id": "ok", "component": {"Button": {"label": "OK"}}}],
            },
        },
    ]


def _action_envelope(surface_id="s1"):
    return {
        "version": "v0.9.1",
        "action": {"name": "submit", "surfaceId": surface_id, "sourceComponentId": "ok", "context": {}},
    }


def _turn(**overrides):
    defaults = {
        "user_query": "hi",
        "user_query_raw": "hi",
        "assistant_id": "a1",
        "assistant_response": "hello",
        "thoughts": [],
        "history_index": 0,
        "time_elapsed": 1.0,
        "input_tokens": 1,
        "output_tokens": 1,
        "file_names": [],
        "money_spent": 0.0,
    }
    defaults.update(overrides)
    return ChatTurnData(**defaults)


def test_build_chat_history_messages_persists_a2ui_fields():
    envelopes = _surface_envelopes()
    action = _action_envelope()
    user_message, assistant_message = Conversation._build_chat_history_messages(
        _turn(
            user_query="✓ OK",
            user_query_raw="✓ OK",
            assistant_response="",
            a2ui_envelopes=envelopes,
            a2ui_action=action,
            a2ui_data_model={"email": "a@b.c"},
        )
    )
    assert assistant_message.a2ui_envelopes == envelopes
    assert user_message.a2ui_action == action
    assert user_message.a2ui_data_model == {"email": "a@b.c"}


def test_build_chat_history_messages_defaults_a2ui_fields_to_none():
    user_message, assistant_message = Conversation._build_chat_history_messages(_turn())
    assert assistant_message.a2ui_envelopes is None
    assert user_message.a2ui_action is None
    assert user_message.a2ui_data_model is None


def test_to_chat_history_materializes_a2ui_envelopes_for_assistant_turn():
    history = [
        GeneratedMessage(role="Assistant", message="", history_index=0, a2ui_envelopes=_surface_envelopes("srf-7"))
    ]
    conversation = Conversation(id="c1", conversation_id="c1", user_id="u1", history=history, assistant_ids=["a1"])
    chat_messages = conversation.to_chat_history()
    assistant_texts = [m.message for m in chat_messages if m.role.value == "Assistant"]
    assert any("Interactive surface srf-7 shown to the user" in t for t in assistant_texts)
    assert any("Button" in t for t in assistant_texts)


def test_to_chat_history_materializes_a2ui_action_for_user_turn():
    history = [
        GeneratedMessage(role="Assistant", message="", history_index=0, a2ui_envelopes=_surface_envelopes("srf-7")),
        GeneratedMessage(
            role="User",
            message="✓ OK",
            history_index=1,
            a2ui_action=_action_envelope("srf-7"),
            a2ui_data_model={"email": "a@b.c"},
        ),
    ]
    conversation = Conversation(id="c1", conversation_id="c1", user_id="u1", history=history, assistant_ids=["a1"])
    chat_messages = conversation.to_chat_history()
    user_texts = [m.message for m in chat_messages if m.role.value == "User"]
    assert any("Structured response to surface srf-7" in t for t in user_texts)
    assert any("a@b.c" in t for t in user_texts)
    assert any(t.startswith("✓ OK") for t in user_texts)


def test_to_chat_history_leaves_plain_messages_untouched():
    history = [
        GeneratedMessage(role="User", message="hi", history_index=0),
        GeneratedMessage(role="Assistant", message="hello", history_index=0),
    ]
    conversation = Conversation(id="c1", conversation_id="c1", user_id="u1", history=history, assistant_ids=["a1"])
    chat_messages = conversation.to_chat_history()
    assert [m.message for m in chat_messages] == ["hi", "hello"]


def test_generated_message_a2ui_round_trip_with_camel_case_aliases():
    envelopes = _surface_envelopes()
    message = GeneratedMessage(
        role="Assistant",
        message="",
        history_index=0,
        a2ui_envelopes=envelopes,
    )
    dumped = message.model_dump(by_alias=True)
    assert dumped["a2uiEnvelopes"] == envelopes
    # Inbound camelCase payload (API round-trip) populates the same field
    restored = GeneratedMessage(**{"role": "Assistant", "message": "", "a2uiEnvelopes": envelopes})
    assert restored.a2ui_envelopes == envelopes


def test_generated_message_a2ui_action_round_trip():
    action = _action_envelope()
    message = GeneratedMessage(
        role="User",
        message="✓ OK",
        a2ui_action=action,
        a2ui_data_model={"email": "a@b.c"},
    )
    dumped = message.model_dump(by_alias=True)
    assert dumped["a2uiAction"] == action
    assert dumped["a2uiDataModel"] == {"email": "a@b.c"}
    restored = GeneratedMessage(**{"role": "User", "a2uiAction": action, "a2uiDataModel": {"email": "a@b.c"}})
    assert restored.a2ui_action == action
    assert restored.a2ui_data_model == {"email": "a@b.c"}


LEGACY_REQUEST = {
    "request_id": "req-1",
    "surface": [{"type": "button", "id": "go", "label": "Go", "style": "primary"}],
}
LEGACY_RESPONSE = {"request_id": "req-1", "kind": "submit", "payload": {"action": "go", "answers": {}}}


class TestConversationsFromTheOldProtocol:
    """A conversation recorded under the old protocol still loads; its element does not.

    Old interactive elements are not migrated — that requirement was dropped, and such
    conversations are considered invalid. "Invalid" has to mean the element is gone, not
    that the conversation fails to open: a user scrolling back through their own history
    must not hit an error because of a message shape the product no longer supports.
    """

    def test_a_stored_legacy_request_does_not_break_loading(self):
        message = GeneratedMessage.model_validate(
            {"role": "Assistant", "message": "Pick one", "history_index": 0, "interactive_request": LEGACY_REQUEST}
        )
        assert message.message == "Pick one"
        assert message.a2ui_envelopes is None

    def test_a_stored_legacy_response_does_not_break_loading(self):
        message = GeneratedMessage.model_validate(
            {"role": "User", "message": "\u2713 Go", "history_index": 1, "interactive_response": LEGACY_RESPONSE}
        )
        assert message.message == "\u2713 Go"
        assert message.a2ui_action is None

    def test_the_legacy_payload_is_dropped_on_the_next_write(self):
        """The history list is re-serialized whole on every turn, so it clears itself."""
        dumped = GeneratedMessage.model_validate(
            {"role": "Assistant", "message": "", "history_index": 0, "interactive_request": LEGACY_REQUEST}
        ).model_dump()
        assert "interactive_request" not in dumped
