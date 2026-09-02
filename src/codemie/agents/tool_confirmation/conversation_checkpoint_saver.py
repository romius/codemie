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

import json
from base64 import b64encode, b64decode
from typing import Optional, Iterable

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    CheckpointTuple,
    Checkpoint,
    CheckpointMetadata,
)
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from codemie.service.conversation_checkpoint_service import ConversationCheckpointService


class StoredCheckpoint(BaseModel):
    """JSONB shape written to conversations.pending_checkpoint."""

    checkpoint: str
    metadata: str


class ConversationCheckpointSaver(BaseCheckpointSaver):
    """
    LangGraph checkpointer backed by conversations.pending_checkpoint - tool call confirmations

    Uses conversation_id as thread_id. Stores only the latest checkpoint (same
    policy as the workflow-scoped CheckpointSaver).
    """

    def __init__(self, checkpoint_service: ConversationCheckpointService):
        """Inject the service used to read/write the pending_checkpoint column."""
        super().__init__()
        self.service = checkpoint_service

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        """Return the latest checkpoint for the conversation, or None if absent."""
        conversation_id = config["configurable"]["thread_id"]
        stored = self.service.get_checkpoint(conversation_id)

        if stored is None:
            return None

        parsed = StoredCheckpoint.model_validate(stored)
        return CheckpointTuple(
            config=config,
            checkpoint=self._deserialize(parsed.checkpoint),
            metadata=self._deserialize(parsed.metadata),
            parent_config=None,
            pending_writes=[],
        )

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        *args,
    ) -> RunnableConfig:
        """Persist the checkpoint, overwriting any previous one for this conversation."""
        conversation_id = config["configurable"]["thread_id"]
        stored = StoredCheckpoint(
            checkpoint=self._serialize(checkpoint),
            metadata=self._serialize(metadata),
        )
        self.service.save_checkpoint(conversation_id, stored.model_dump())

        return {
            "configurable": {
                "thread_id": conversation_id,
                "thread_ts": checkpoint.get("ts", ""),
            }
        }

    def put_writes(self, *args):
        """No-op — single-checkpoint policy; incremental writes not needed."""
        pass

    def list(self, config: RunnableConfig) -> Iterable[CheckpointTuple]:
        """Not required for interrupt/resume. Yields nothing."""
        return iter([])

    def _serialize(self, obj) -> str:
        type_str, data_bytes = self.serde.dumps_typed(obj)

        return json.dumps({"type": type_str, "data": b64encode(data_bytes).decode("utf-8")})

    def _deserialize(self, data_str: str):
        data_dict = json.loads(data_str)
        type_str = data_dict["type"]
        data_bytes = b64decode(data_dict["data"])

        return self.serde.loads_typed((type_str, data_bytes))
