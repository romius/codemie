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

"""Tests for APPROVE_FOR_ME auto-resume logic in ToolCallConfirmationMixin."""

from unittest.mock import MagicMock, patch


from codemie.core.models import ToolCallPolicy
from codemie.agents.tool_confirmation.tool_call_confirmation_mixin import ToolCallConfirmationMixin


def _make_mixin(policy: ToolCallPolicy, tools: list):
    """Create a minimal ToolCallConfirmationMixin instance for testing."""
    mixin = ToolCallConfirmationMixin.__new__(ToolCallConfirmationMixin)
    mixin.tool_call_policy = policy
    mixin.tools = tools
    mixin.thread_generator = None  # disable SSE emission
    mixin._pending_tool_confirmation = False
    mixin.conversation_id = "conv-123"
    mixin.agent_name = "test-agent"
    mixin.request_uuid = "req-uuid"
    mixin.agent_executor = MagicMock()
    return mixin


def _make_state(tool_name: str, tool_args: dict):
    """Build a fake LangGraph state snapshot."""
    tool_call = {"id": "tc-1", "name": tool_name, "args": tool_args}
    ai_message = MagicMock()
    ai_message.tool_calls = [tool_call]
    state = MagicMock()
    state.next = ["tools"]
    state.values = {"messages": [ai_message]}
    return state


def _wire_state(mixin, state):
    """Point agent_executor.get_state at the pre-built state."""
    mixin.agent_executor.get_state.return_value = state


def _make_safe_tool(name: str) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.is_safe = MagicMock(return_value=True)
    return tool


def _make_unsafe_tool(name: str) -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.is_safe = MagicMock(return_value=False)
    return tool


# --- APPROVE_FOR_ME + safe tool ---


def test_approve_for_me_safe_tool_returns_needs_auto_resume_true():
    safe_tool = _make_safe_tool("search_kb")
    mixin = _make_mixin(ToolCallPolicy.APPROVE_FOR_ME, [safe_tool])
    state = _make_state("search_kb", {"query": "hello"})
    _wire_state(mixin, state)

    last_msg, needs_auto_resume = mixin.ask_for_tool_confirmation({}, "response")

    assert needs_auto_resume is True
    assert last_msg == "response"
    assert mixin._pending_tool_confirmation is False


def test_approve_for_me_safe_tool_does_not_save_checkpoint():
    safe_tool = _make_safe_tool("search_kb")
    mixin = _make_mixin(ToolCallPolicy.APPROVE_FOR_ME, [safe_tool])
    state = _make_state("search_kb", {"query": "hello"})
    _wire_state(mixin, state)

    with patch(
        "codemie.agents.tool_confirmation.tool_call_confirmation_mixin.ConversationCheckpointService"
    ) as mock_service:
        mixin.ask_for_tool_confirmation({}, "response")
        mock_service.return_value.save_pending_tool_call.assert_not_called()


# --- APPROVE_FOR_ME + unsafe tool ---


def test_approve_for_me_unsafe_tool_returns_needs_auto_resume_false():
    unsafe_tool = _make_unsafe_tool("create_issue")
    mixin = _make_mixin(ToolCallPolicy.APPROVE_FOR_ME, [unsafe_tool])
    state = _make_state("create_issue", {"summary": "bug"})
    _wire_state(mixin, state)

    with patch("codemie.agents.tool_confirmation.tool_call_confirmation_mixin.ConversationCheckpointService"):
        last_msg, needs_auto_resume = mixin.ask_for_tool_confirmation({}, "response")

    assert needs_auto_resume is False
    assert mixin._pending_tool_confirmation is True


# --- APPROVE_FOR_ME + unknown tool (not in self.tools) ---


def test_approve_for_me_unknown_tool_is_conservative():
    mixin = _make_mixin(ToolCallPolicy.APPROVE_FOR_ME, [])  # empty tools list
    state = _make_state("unknown_tool", {})
    _wire_state(mixin, state)

    with patch("codemie.agents.tool_confirmation.tool_call_confirmation_mixin.ConversationCheckpointService"):
        last_msg, needs_auto_resume = mixin.ask_for_tool_confirmation({}, "response")

    assert needs_auto_resume is False
    assert mixin._pending_tool_confirmation is True


# --- ASK_FOR_APPROVAL always interrupts regardless of is_safe ---


def test_ask_for_approval_safe_tool_still_interrupts():
    safe_tool = _make_safe_tool("search_kb")
    mixin = _make_mixin(ToolCallPolicy.ASK_FOR_APPROVAL, [safe_tool])
    state = _make_state("search_kb", {})
    _wire_state(mixin, state)

    with patch("codemie.agents.tool_confirmation.tool_call_confirmation_mixin.ConversationCheckpointService"):
        last_msg, needs_auto_resume = mixin.ask_for_tool_confirmation({}, "response")

    assert needs_auto_resume is False
    assert mixin._pending_tool_confirmation is True


# --- No interrupt (state.next does not contain "tools") ---


def test_no_interrupt_when_next_does_not_contain_tools():
    mixin = _make_mixin(ToolCallPolicy.APPROVE_FOR_ME, [])
    state = MagicMock()
    state.next = []
    _wire_state(mixin, state)

    last_msg, needs_auto_resume = mixin.ask_for_tool_confirmation({}, "response")

    assert needs_auto_resume is False
    assert last_msg == "response"


# --- No tool_calls on last AI message ---


def test_no_interrupt_when_no_tool_calls_on_last_message():
    mixin = _make_mixin(ToolCallPolicy.APPROVE_FOR_ME, [])
    ai_message = MagicMock()
    ai_message.tool_calls = []
    state = MagicMock()
    state.next = ["tools"]
    state.values = {"messages": [ai_message]}
    _wire_state(mixin, state)

    last_msg, needs_auto_resume = mixin.ask_for_tool_confirmation({}, "response")

    assert needs_auto_resume is False
