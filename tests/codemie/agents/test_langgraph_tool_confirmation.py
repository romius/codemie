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

from unittest.mock import MagicMock, patch
from langgraph.checkpoint.memory import InMemorySaver
from codemie.agents.tool_confirmation.conversation_checkpoint_saver import ConversationCheckpointSaver


def _make_agent(require_tool_confirmation=False, conversation_id="conv_1"):
    """Build a minimal LangGraphAgent for run_config testing."""
    from codemie.agents.langgraph_agent import LangGraphAgent
    from codemie.core.models import AssistantChatRequest

    request = MagicMock(spec=AssistantChatRequest)
    request.conversation_id = conversation_id
    request.text = "hello"
    request.history = []
    request.file_names = []
    request.history_index = None
    request.tools_config = None

    with patch.object(LangGraphAgent, "init_agent", return_value=MagicMock()):
        with patch.object(LangGraphAgent, "_setup_supervisor_coordinator", return_value=None, create=True):
            with patch("codemie.agents.langgraph_agent.get_otel_context_for_thread", return_value=None):
                agent = LangGraphAgent.__new__(LangGraphAgent)
                agent.agent_name = "test_agent"
                agent.description = "test"
                agent.tools = []
                agent.subagents = []
                agent.subagent_descriptions = {}
                agent.request = request
                agent.recursion_limit = 25
                agent.system_prompt = "You are helpful."
                agent.thread_generator = MagicMock()
                agent.user = MagicMock()
                agent.user.id = "user_1"
                agent.user.username = "test@test.com"
                agent.llm_model = "gpt-4"
                agent.temperature = None
                agent.top_p = None
                agent.request_uuid = "req_1"
                agent.conversation_id = conversation_id
                agent.handle_tool_error = True
                agent.throw_truncated_error = False
                agent.callbacks = []
                agent.supervisor_callbacks = []
                agent.output_schema = None
                agent.assistant = MagicMock()
                agent.assistant.version = None
                agent.override_global_checkpointer = True
                agent.trace_context = None
                agent._current_llm_run_id = None
                agent._sub_assistant_name_mapping = {}
                agent.history_compaction_pre_model_hook = None
                agent._otel_context = None
                agent.smart_tool_selection_enabled = False
                agent.tool_selection_limit = 5
                agent.verbose = False
                agent.is_react = True
                agent.stream_steps = True
                agent.tool_error_callback = MagicMock()
                agent.agent_executor = MagicMock()
                agent.require_tool_confirmation = require_tool_confirmation
                return agent


def test_get_run_config_uses_in_memory_saver_when_no_confirmation():
    agent = _make_agent(require_tool_confirmation=False)
    with patch("codemie.agents.langgraph_agent.get_run_config", return_value={"recursion_limit": 25}):
        config = agent._get_run_config()
    assert isinstance(config.get("__pregel_checkpointer"), InMemorySaver)
    assert config.get("thread_id") == "thread"


def test_get_run_config_uses_conversation_saver_when_confirmation_enabled():
    agent = _make_agent(require_tool_confirmation=True, conversation_id="conv_123")
    with patch("codemie.agents.langgraph_agent.get_run_config", return_value={"recursion_limit": 25}):
        config = agent._get_run_config()
    assert isinstance(config.get("__pregel_checkpointer"), ConversationCheckpointSaver)
    assert config.get("thread_id") == "conv_123"


def test_get_run_config_conversation_saver_thread_id_matches_conversation_id():
    agent = _make_agent(require_tool_confirmation=True, conversation_id="conv_xyz")
    with patch("codemie.agents.langgraph_agent.get_run_config", return_value={"recursion_limit": 25}):
        config = agent._get_run_config()
    assert config["thread_id"] == "conv_xyz"
