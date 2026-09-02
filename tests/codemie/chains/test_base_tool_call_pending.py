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
from codemie.agents.tool_confirmation.models import ToolCallPendingEvent
from codemie.chains.base import StreamedGenerationResult


def test_tool_call_pending_event_fields():
    evt = ToolCallPendingEvent(
        pending_tool_call_id="call_abc",
        tool_name="search_confluence",
        tool_args={"query": "SSO setup"},
    )
    assert evt.pending_tool_call_id == "call_abc"
    assert evt.tool_name == "search_confluence"
    assert evt.tool_args == {"query": "SSO setup"}


def test_streamed_generation_result_includes_tool_call_pending():
    evt = ToolCallPendingEvent(
        pending_tool_call_id="call_abc",
        tool_name="search_confluence",
        tool_args={"query": "SSO setup"},
    )
    result = StreamedGenerationResult(last=True, tool_call_pending=evt)
    dumped = json.loads(result.model_dump_json())
    assert dumped["last"] is True
    assert dumped["tool_call_pending"]["pending_tool_call_id"] == "call_abc"
    assert dumped["tool_call_pending"]["tool_name"] == "search_confluence"


def test_streamed_generation_result_tool_call_pending_defaults_none():
    result = StreamedGenerationResult(generated_chunk="hello")
    assert result.tool_call_pending is None
    dumped = json.loads(result.model_dump_json())
    assert dumped.get("tool_call_pending") is None
