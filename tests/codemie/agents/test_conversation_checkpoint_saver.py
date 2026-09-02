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
import pytest
from base64 import b64encode
from unittest.mock import MagicMock
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from codemie.agents.tool_confirmation.conversation_checkpoint_saver import ConversationCheckpointSaver
from codemie.service.conversation_checkpoint_service import ConversationCheckpointService


def _serialize(obj) -> str:
    """Helper matching ConversationCheckpointSaver._serialize."""
    serde = JsonPlusSerializer()
    type_str, data_bytes = serde.dumps_typed(obj)
    return json.dumps({"type": type_str, "data": b64encode(data_bytes).decode("utf-8")})


@pytest.fixture
def mock_service():
    return MagicMock(spec=ConversationCheckpointService)


@pytest.fixture
def saver(mock_service):
    return ConversationCheckpointSaver(checkpoint_service=mock_service)


@pytest.fixture
def config():
    return {"configurable": {"thread_id": "conv_abc"}}


def test_get_tuple_returns_none_when_no_checkpoint(saver, mock_service, config):
    mock_service.get_checkpoint.return_value = None
    result = saver.get_tuple(config)
    assert result is None
    mock_service.get_checkpoint.assert_called_once_with("conv_abc")


def test_get_tuple_returns_checkpoint_tuple(saver, mock_service, config):
    checkpoint_data = {"ts": "2026-01-01", "channel_values": {}, "versions_seen": {}, "pending_sends": [], "id": "x"}
    metadata_data = {"source": "loop", "step": 1, "writes": {}, "parents": {}}
    stored = {
        "checkpoint": _serialize(checkpoint_data),
        "metadata": _serialize(metadata_data),
    }
    mock_service.get_checkpoint.return_value = stored
    result = saver.get_tuple(config)
    assert result is not None
    assert result.config == config


def test_put_saves_serialized_checkpoint(saver, mock_service, config):
    checkpoint = {"ts": "2026-01-01", "channel_values": {}, "versions_seen": {}, "pending_sends": [], "id": "x"}
    metadata = {"source": "loop", "step": 1, "writes": {}, "parents": {}}
    saver.put(config, checkpoint, metadata, {})
    mock_service.save_checkpoint.assert_called_once()
    saved = mock_service.save_checkpoint.call_args[0]
    assert saved[0] == "conv_abc"
    assert "checkpoint" in saved[1]
    assert "metadata" in saved[1]


def test_put_writes_is_noop(saver, mock_service):
    saver.put_writes(None, None, None)
    mock_service.save_checkpoint.assert_not_called()
