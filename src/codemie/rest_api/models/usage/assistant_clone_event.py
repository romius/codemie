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

"""
Model for tracking individual clone actions on an assistant.

Deliberately has NO uniqueness constraint on (assistant_id, user_id): every clone action
must be recorded, unlike a like/dislike reaction which is a toggleable per-user state.
"""

from datetime import datetime, UTC
from uuid import uuid4

from sqlmodel import Field, Index, SQLModel

from codemie.clients.postgres import PostgresClient


class AssistantCloneEventSQL(SQLModel, table=True):
    """SQLModel for assistant clone events."""

    __tablename__ = "assistant_clone_event"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    assistant_id: str = Field()
    user_id: str = Field()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        Index('ix_assistant_clone_event_assistant_id', 'assistant_id'),
        Index('ix_assistant_clone_event_user_id', 'user_id'),
        Index(
            'ix_assistant_clone_event_assistant_user_created',
            'assistant_id',
            'user_id',
            'created_at',
        ),
    )

    @classmethod
    def get_engine(cls):
        return PostgresClient.get_engine()


AssistantCloneEvent = AssistantCloneEventSQL
