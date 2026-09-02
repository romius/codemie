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

"""Verifies the turn-based pause contract: request_user_input ends the agent turn.

This is the single most uncertain integration point of the interactive-input
feature — the pause semantics rest entirely on the tool's return_direct=True
being honored by langgraph's create_react_agent. This test pins that contract:
after the tool runs, the agent must NOT call the LLM a second time.
"""

import json
from unittest.mock import MagicMock

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.prebuilt import create_react_agent
from pydantic import Field

from codemie.agents.tools.interactive.request_user_input import (
    SURFACE_REJECTED_NOTICE,
    RequestUserInputTool,
)


class RecordingBindableFakeChatModel(FakeMessagesListChatModel):
    """Fake chat model that counts how many times the LLM is invoked."""

    call_count: int = Field(default=0)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.call_count += 1
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_request_user_input_ends_turn_without_second_llm_call():
    generator = MagicMock()
    tool = RequestUserInputTool(thread_generator=generator)

    # First (and only) LLM response calls request_user_input. If return_direct
    # is honored, the graph routes to END and never calls the model again.
    tool_call_message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "request_user_input",
                "args": {
                    "components": [
                        {"id": "root", "component": "Column", "children": ["ok"]},
                        {
                            "id": "ok",
                            "component": "Button",
                            "child": "ok-label",
                            "action": {"event": {"name": "ok"}},
                        },
                        {"id": "ok-label", "component": "Text", "text": "OK?"},
                    ]
                },
                "id": "call-1",
            }
        ],
    )
    # A second response is provided as a trap: if the agent loops, it would be consumed.
    trap_message = AIMessage(content="the agent should never reach this")
    model = RecordingBindableFakeChatModel(responses=[tool_call_message, trap_message])

    agent = create_react_agent(model=model, tools=[tool])
    agent.invoke({"messages": [HumanMessage(content="decide please")]})

    # Exactly one LLM call: the turn ended after the tool ran.
    assert model.call_count == 1
    # And the surface was streamed out as A2UI envelopes (createSurface + updateComponents).
    assert generator.send.call_count == 2


def test_rejected_surface_tells_the_user_instead_of_ending_silently():
    """A refused surface still ends the turn, so the user must be told something.

    Observed live: the model produced the legacy protocol shape ({"type": "textinput"}),
    the tool raised, and the user was left with an empty assistant message — the feature
    simply appeared dead. The model cannot correct itself here: `create_react_agent`
    snapshots the NAMES of return_direct tools when the graph is built and routes to END
    on the name alone, so a failed call is indistinguishable from a successful one. The
    only thing the tool can still do is say so on the stream before the turn closes.
    """
    generator = MagicMock()
    tool = RequestUserInputTool(thread_generator=generator)
    rejected_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "request_user_input",
                "args": {
                    "components": [
                        {"id": "root", "type": "vertical-layout", "children": ["b"]},
                        {"id": "b", "type": "button", "label": "Go", "action_id": "go"},
                    ]
                },
                "id": "call-1",
            }
        ],
    )
    trap_message = AIMessage(content="the agent should never reach this")
    model = RecordingBindableFakeChatModel(responses=[rejected_call, trap_message])

    agent = create_react_agent(model=model, tools=[tool])
    agent.invoke({"messages": [HumanMessage(content="show me a form")]})

    sent = [json.loads(call.args[0]) for call in generator.send.call_args_list]
    # The invalid surface never reaches the client.
    assert not [chunk for chunk in sent if chunk.get("a2ui")]
    # But the user is told, rather than left with an empty message.
    assert [chunk for chunk in sent if chunk.get("generated_chunk") == SURFACE_REJECTED_NOTICE]


def test_a_surface_may_be_introduced_by_text():
    """Showing a form must not silence the assistant.

    An agent often has something to say alongside the form ("I need a few details:"),
    and the turn-ending tool call must not swallow it. The model's own message carries
    both the text and the tool call, so the text survives into the assistant reply and
    the client renders it above the surface.
    """
    generator = MagicMock()
    tool = RequestUserInputTool(thread_generator=generator)
    message = AIMessage(
        content="I need a few details first:",
        tool_calls=[
            {
                "name": "request_user_input",
                "args": {
                    "components": [
                        {"id": "root", "component": "Column", "children": ["ok"]},
                        {
                            "id": "ok",
                            "component": "Button",
                            "child": "ok-label",
                            "action": {"event": {"name": "ok"}},
                        },
                        {"id": "ok-label", "component": "Text", "text": "OK"},
                    ]
                },
                "id": "call-1",
            }
        ],
    )
    model = RecordingBindableFakeChatModel(responses=[message, AIMessage(content="never reached")])

    agent = create_react_agent(model=model, tools=[tool])
    result = agent.invoke({"messages": [HumanMessage(content="quote please")]})

    assistant_texts = [m.content for m in result["messages"] if isinstance(m, AIMessage) and m.content]
    assert "I need a few details first:" in assistant_texts
    assert generator.send.call_count == 2, "the surface is still emitted"
    assert model.call_count == 1, "the turn still ends"
