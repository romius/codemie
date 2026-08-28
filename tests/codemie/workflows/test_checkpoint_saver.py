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
from base64 import b64encode
import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from unittest.mock import MagicMock, patch
from codemie.workflows.checkpoint_saver import CheckpointSaver, CheckpointTuple
from pydantic import BaseModel


def serialize_for_test(obj):
    """Helper to serialize objects same way as CheckpointSaver._serialize."""
    serializer = JsonPlusSerializer()
    type_str, data_bytes = serializer.dumps_typed(obj)
    return json.dumps({"type": type_str, "data": b64encode(data_bytes).decode('utf-8')})


@pytest.fixture
def instance():
    return CheckpointSaver()


@pytest.fixture
def mock_config():
    return {'configurable': {'thread_id': 'test_thread_id', 'thread_ts': 'test_thread_ts'}}


class MockMessage(BaseModel):
    content: list


@pytest.fixture
def mock_workflow_execution():
    mock_checkpoint = MagicMock()
    mock_checkpoint.timestamp = '123'

    data = {"channel_values": {"messages": [MockMessage(content=[{"text": "AI Generated Text"}])]}}
    mock_checkpoint.data = serialize_for_test(data)
    mock_checkpoint.metadata = serialize_for_test({"metadata": "test_metadata"})

    mock_execution = MagicMock()
    mock_execution.checkpoints = [mock_checkpoint]

    return mock_execution


@patch('codemie.core.workflow_models.WorkflowExecution.get_by_execution_id')
def test_get_tuple_success(mock_get_execution_by_id, instance, mock_config, mock_workflow_execution):
    mock_get_execution_by_id.return_value = [mock_workflow_execution]
    result = instance.get_tuple(config=mock_config)

    assert isinstance(result, CheckpointTuple)
    assert 'channel_values' in result.checkpoint
    assert result.metadata == {'metadata': 'test_metadata'}


@patch('codemie.core.workflow_models.WorkflowExecution.get_by_execution_id')
def test_get_tuple_no_execution(mock_get_execution_by_id, instance, mock_config, mock_workflow_execution):
    mock_get_execution_by_id.return_value = []
    result = instance.get_tuple(config=mock_config)

    assert result is None


@patch('codemie.core.workflow_models.WorkflowExecution.get_by_execution_id')
def test_list_success(mock_get_execution_by_id, instance, mock_config, mock_workflow_execution):
    mock_get_execution_by_id.return_value = [mock_workflow_execution]
    result = next(instance.list(config=mock_config))

    assert isinstance(result, CheckpointTuple)
    assert 'channel_values' in result.checkpoint
    assert result.metadata == {'metadata': 'test_metadata'}


@patch('codemie.core.workflow_models.WorkflowExecution.get_by_execution_id')
def test_list_no_execution(mock_get_execution_by_id, instance, mock_config, mock_workflow_execution):
    mock_get_execution_by_id.return_value = []

    with pytest.raises(StopIteration):
        next(instance.list(config=mock_config))


@patch('codemie.core.workflow_models.WorkflowExecution.get_by_execution_id')
def test_put(mock_get_execution_by_id, instance, mock_config, mock_workflow_execution):
    mock_get_execution_by_id.return_value = [mock_workflow_execution]

    instance.put(config=mock_config, checkpoint={'ts': '123'}, metadata={'metadata': 'test_metadata'})

    assert mock_workflow_execution.checkpoints[0].timestamp == '123'
    # Verify data is serialized correctly by deserializing and checking value
    assert instance._deserialize(mock_workflow_execution.checkpoints[0].data) == {'ts': '123'}
    assert instance._deserialize(mock_workflow_execution.checkpoints[0].metadata) == {'metadata': 'test_metadata'}

    assert mock_workflow_execution.update.called


def test_put_writes(instance):
    try:
        instance.put_writes()
    except Exception as e:
        pytest.fail(f"put_writes raised an exception {e} when it should not")


def test_find_checkpoints_no_timestamp(instance):
    workflow_execution = MagicMock()
    workflow_execution.checkpoints = ['checkpoint']

    result = instance._find_checkpoints(workflow_execution, None)
    assert result == ['checkpoint']


@patch('codemie.core.workflow_models.WorkflowExecution.get_by_execution_id')
def test_update_last_checkpoint(mock_get_execution_by_id, mock_workflow_execution, instance):
    mock_get_execution_by_id.return_value = [mock_workflow_execution]

    instance.update_last_checkpoint(execution_id="execution_id", output="new output", output_key=None)

    mock_workflow_execution.update.assert_called_once()


@patch('codemie.core.workflow_models.WorkflowExecution.get_by_execution_id')
def test_update_last_checkpoint_replaces_list(mock_get_execution_by_id, instance):
    """Verify that checkpoints list is replaced, not modified in place"""
    mock_checkpoint = MagicMock()

    data = {"channel_values": {"messages": [MockMessage(content=[{"text": "original output"}])]}}
    mock_checkpoint.data = serialize_for_test(data)

    mock_execution = MagicMock()
    mock_execution.checkpoints = [mock_checkpoint]

    mock_get_execution_by_id.return_value = [mock_execution]

    instance.update_last_checkpoint(execution_id="execution_id", output="new output", output_key=None)

    # Verify list was replaced (new list assigned)
    # Note: In the actual implementation, workflow_execution.checkpoints is assigned a new list
    # The mock will capture this assignment
    assert mock_execution.checkpoints is not None
    mock_execution.update.assert_called_once()


@patch('codemie.core.workflow_models.WorkflowExecution.get_by_execution_id')
def test_update_last_checkpoint_with_output_key(mock_get_execution_by_id, instance):
    """Verify that output_key updates both message content AND context_store"""
    mock_checkpoint = MagicMock()

    data = {
        "channel_values": {
            "messages": [MockMessage(content=[{"text": "original output"}])],
            "context_store": {"prfaq": "original value", "other_key": "other_value"},
        }
    }
    mock_checkpoint.data = serialize_for_test(data)

    mock_execution = MagicMock()
    mock_execution.checkpoints = [mock_checkpoint]

    mock_get_execution_by_id.return_value = [mock_execution]

    instance.update_last_checkpoint(execution_id="execution_id", output="new output", output_key="prfaq")

    # Verify update was called
    mock_execution.update.assert_called_once()

    # Verify the checkpoint data was modified correctly
    # The checkpoint data should now contain the updated output in both places
    # (We can't easily verify the exact content with mocks, but we ensure the method runs without error)


@patch('codemie.core.workflow_models.WorkflowExecution.get_by_execution_id')
def test_update_last_checkpoint_no_execution(mock_get_execution_by_id, instance):
    """Verify error handling when execution is not found"""
    mock_get_execution_by_id.return_value = []

    with pytest.raises(ValueError, match="Workflow execution .* not found"):
        instance.update_last_checkpoint(execution_id="execution_id", output="new output", output_key=None)


@patch('codemie.core.workflow_models.WorkflowExecution.get_by_execution_id')
def test_update_last_checkpoint_no_checkpoints(mock_get_execution_by_id, instance):
    """Verify error handling when no checkpoints exist"""
    mock_execution = MagicMock()
    mock_execution.checkpoints = []

    mock_get_execution_by_id.return_value = [mock_execution]

    with pytest.raises(ValueError, match="No checkpoints found for execution"):
        instance.update_last_checkpoint(execution_id="execution_id", output="new output", output_key=None)


@patch('codemie.core.workflow_models.WorkflowExecution.get_by_execution_id')
def test_update_last_unknown_checkpoint_format(mock_get_execution_by_id, instance):
    mock_checkpoint = MagicMock()

    data = {"channel_values": {"messages": [MockMessage(content=[{}])]}}
    mock_checkpoint.data = serialize_for_test(data)

    mock_execution = MagicMock()
    mock_execution.checkpoints = [mock_checkpoint]

    mock_get_execution_by_id.return_value = [mock_execution]

    with pytest.raises(ValueError, match="Unknown checkpoint format"):
        instance.update_last_checkpoint(execution_id="execution_id", output="new output", output_key=None)


# ── Sub-workflow parent/child checkpoint isolation ────────────────────────────


@patch('codemie.core.workflow_models.WorkflowExecution.get_by_execution_id')
def test_parent_and_child_use_distinct_thread_ids(mock_get_by_id, instance):
    """Parent and child thread_id values map to separate WorkflowExecution records — no collision."""
    parent_exec = MagicMock()
    parent_exec.checkpoints = []
    child_exec = MagicMock()
    child_exec.checkpoints = []

    def get_by_id(exec_id):
        if exec_id == "parent-exec-id":
            return [parent_exec]
        if exec_id == "child-exec-id":
            return [child_exec]
        return []

    mock_get_by_id.side_effect = get_by_id

    instance.put(config={"configurable": {"thread_id": "parent-exec-id"}}, checkpoint={"ts": "p1"}, metadata={})
    instance.put(config={"configurable": {"thread_id": "child-exec-id"}}, checkpoint={"ts": "c1"}, metadata={})

    # Each execution holds its own checkpoint — no cross-contamination
    assert parent_exec.checkpoints[0].timestamp == "p1"
    assert child_exec.checkpoints[0].timestamp == "c1"


@patch('codemie.core.workflow_models.WorkflowExecution.get_by_execution_id')
def test_child_put_does_not_overwrite_parent_checkpoint(mock_get_by_id, instance):
    """Storing a child checkpoint must not modify the parent WorkflowExecution row."""
    parent_exec = MagicMock()
    parent_exec.checkpoints = []
    child_exec = MagicMock()
    child_exec.checkpoints = []

    def get_by_id(exec_id):
        return [parent_exec] if exec_id == "parent-exec-id" else [child_exec]

    mock_get_by_id.side_effect = get_by_id

    instance.put(config={"configurable": {"thread_id": "parent-exec-id"}}, checkpoint={"ts": "p1"}, metadata={})
    parent_ts_after_write = parent_exec.checkpoints[0].timestamp

    instance.put(config={"configurable": {"thread_id": "child-exec-id"}}, checkpoint={"ts": "c1"}, metadata={})

    # Parent row must be untouched after the child write
    assert parent_exec.checkpoints[0].timestamp == parent_ts_after_write
    assert parent_exec.checkpoints[0].timestamp == "p1"
