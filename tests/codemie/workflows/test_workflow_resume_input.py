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

from unittest.mock import MagicMock
from langchain_core.messages import HumanMessage

from codemie.workflows.constants import MESSAGES_VARIABLE, CONTEXT_STORE_VARIABLE
from codemie.workflows.workflow import WorkflowExecutor


def _make_executor(user_input: str | None, resume_execution: bool = True) -> MagicMock:
    """Build a minimal MagicMock that stands in for WorkflowExecutor self."""
    executor = MagicMock(spec=WorkflowExecutor)
    executor.user_input = user_input
    executor.resume_execution = resume_execution
    return executor


def test_inject_resume_input_calls_update_state_when_user_input_set():
    executor = _make_executor(user_input="hello resume")
    mock_workflow = MagicMock()
    mock_config = MagicMock()

    WorkflowExecutor._inject_resume_input(executor, mock_workflow, mock_config)

    mock_workflow.update_state.assert_called_once()
    call_args = mock_workflow.update_state.call_args
    assert call_args.args[0] is mock_config
    state_values = call_args.args[1]
    assert MESSAGES_VARIABLE in state_values
    assert isinstance(state_values[MESSAGES_VARIABLE][0], HumanMessage)
    assert state_values[MESSAGES_VARIABLE][0].content == "hello resume"
    assert CONTEXT_STORE_VARIABLE in state_values


def test_inject_resume_input_does_nothing_when_user_input_empty():
    executor = _make_executor(user_input="")
    mock_workflow = MagicMock()
    mock_config = MagicMock()

    WorkflowExecutor._inject_resume_input(executor, mock_workflow, mock_config)

    mock_workflow.update_state.assert_not_called()


def test_inject_resume_input_does_nothing_when_not_resume():
    executor = _make_executor(user_input="some input", resume_execution=False)
    mock_workflow = MagicMock()
    mock_config = MagicMock()

    WorkflowExecutor._inject_resume_input(executor, mock_workflow, mock_config)

    mock_workflow.update_state.assert_not_called()


def test_inject_resume_input_does_nothing_when_user_input_is_none():
    executor = _make_executor(user_input=None)
    mock_workflow = MagicMock()
    mock_config = MagicMock()

    WorkflowExecutor._inject_resume_input(executor, mock_workflow, mock_config)

    mock_workflow.update_state.assert_not_called()


def test_inject_resume_input_merges_json_user_input_into_context_store():
    executor = _make_executor(user_input='{"task": "do this", "extra_key": "value"}')
    mock_workflow = MagicMock()
    mock_config = MagicMock()

    WorkflowExecutor._inject_resume_input(executor, mock_workflow, mock_config)

    state_values = mock_workflow.update_state.call_args.args[1]
    context = state_values[CONTEXT_STORE_VARIABLE]
    assert context.get("task") == "do this"
    assert context.get("extra_key") == "value"
    assert state_values[MESSAGES_VARIABLE][0].content == '{"task": "do this", "extra_key": "value"}'


def test_inject_resume_input_with_forwarded_context_store_from_sub_workflow():
    """
    SubWorkflowNode.execute() resume path passes json.dumps(context_store) as
    user_input to the child executor. Verify _inject_resume_input correctly
    merges that JSON payload back into the child workflow's context_store so the
    child resumes with the forwarded state rather than an empty context.
    """
    import json

    forwarded_ctx = {"task": "resume task", "prior_result": "step-1 output"}
    executor = _make_executor(user_input=json.dumps(forwarded_ctx))
    mock_workflow = MagicMock()
    mock_config = MagicMock()

    WorkflowExecutor._inject_resume_input(executor, mock_workflow, mock_config)

    mock_workflow.update_state.assert_called_once()
    state_values = mock_workflow.update_state.call_args.args[1]
    context = state_values[CONTEXT_STORE_VARIABLE]
    assert context.get("task") == "resume task"
    assert context.get("prior_result") == "step-1 output"
    # The raw JSON is also injected as the user message so LangGraph can replay it
    assert state_values[MESSAGES_VARIABLE][0].content == json.dumps(forwarded_ctx)
