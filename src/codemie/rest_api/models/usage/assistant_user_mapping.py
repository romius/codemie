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

"""
Models for mapping assistant to tools and settings.
"""

from datetime import datetime, UTC
from typing import List, Dict, Optional
from uuid import uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Column, Index, Field as SQLField


from codemie.rest_api.models.base import BaseModelWithSQLSupport, CommonBaseModel, PydanticListType


# Scope sentinel: an empty workflow id marks a mapping that applies to the assistant everywhere
# (chat, assistant page, every workflow). Postgres treats NULLs as distinct, so a nullable column
# would let duplicate assistant-scoped rows through the unique constraint; the sentinel keeps a
# plain three-column constraint working.
ASSISTANT_SCOPE = ""


class ToolConfig(CommonBaseModel):
    """Represents a single tool configuration"""

    name: str
    integration_id: str


class AssistantUserMappingBase(CommonBaseModel):
    """Base model for tracking assistant mappings to tools and settings"""

    id: str = SQLField(default_factory=lambda: str(uuid4()), primary_key=True)
    assistant_id: str = SQLField(index=True)
    user_id: str = SQLField(index=True)
    workflow_id: str = SQLField(default=ASSISTANT_SCOPE, index=True, nullable=False)
    tools_config: List[ToolConfig] = SQLField(default_factory=list, sa_column=Column(PydanticListType(ToolConfig)))
    created_at: datetime = SQLField(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = SQLField(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        UniqueConstraint('assistant_id', 'user_id', 'workflow_id', name='uix_assistant_user_mapping_scope'),
        Index('ix_assistant_user_mapping_assistant_id', 'assistant_id'),
        Index('ix_assistant_user_mapping_user_id', 'user_id'),
        Index('ix_assistant_user_mapping_workflow_id', 'workflow_id'),
    )

    @classmethod
    def create_with_tools_config(
        cls,
        assistant_id: str,
        user_id: str,
        tools_config_list: List[Dict[str, str]],
        workflow_id: str = ASSISTANT_SCOPE,
    ) -> "AssistantUserMappingBase":
        """
        Create a new AssistantUserMappingBase instance with the given tools_config.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user
            tools_config_list: List of tool configurations
            workflow_id: Scope of the mapping; ASSISTANT_SCOPE applies to the assistant everywhere

        Returns:
            New AssistantUserMappingBase instance
        """
        # Convert dictionaries to ToolConfig instances
        tool_configs = [ToolConfig(**config) for config in tools_config_list]

        instance = cls(assistant_id=assistant_id, user_id=user_id, workflow_id=workflow_id, tools_config=tool_configs)
        return instance


class AssistantUserMappingSQL(BaseModelWithSQLSupport, AssistantUserMappingBase, table=True):
    """SQLModel version of AssistantUserMapping for PostgreSQL storage"""

    __tablename__ = "assistant_user_mapping"


# Use the SQL implementation
AssistantUserMapping = AssistantUserMappingSQL


class AssistantMappingRequest(CommonBaseModel):
    """Request model for creating/updating assistant mappings"""

    tools_config: List[Dict[str, str]]
    # Absent means the assistant scope, i.e. exactly today's behaviour for existing clients.
    workflow_id: Optional[str] = None
    # Only meaningful together with workflow_id: store at assistant scope instead and drop the
    # workflow-scoped selection for that workflow.
    apply_to_assistant: bool = False


class AssistantMappingResponse(CommonBaseModel):
    """Response model for assistant mappings API"""

    id: str
    assistant_id: str
    user_id: str
    tools_config: List[Dict[str, str]]
    workflow_id: str = ASSISTANT_SCOPE
    # Lets the panel decide whether the "apply to the whole assistant" checkbox starts ticked.
    has_assistant_scope_selection: bool = False
    # What the resolution chain would pick for slots the user never chose explicitly, so the panel
    # can pre-select it instead of showing an empty control. Filled only for the credential types
    # the client asks about.
    auto_resolved: List[Dict[str, str]] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_db_model(cls, db_model: AssistantUserMappingBase) -> "AssistantMappingResponse":
        """
        Convert database model to API response model.

        Args:
            db_model: Database model instance

        Returns:
            API response model instance
        """
        # Convert ToolConfig instances to dictionaries
        tools_config_list = [
            {"name": config.name, "integration_id": config.integration_id} for config in db_model.tools_config
        ]

        return cls(
            id=db_model.id,
            assistant_id=db_model.assistant_id,
            user_id=db_model.user_id,
            tools_config=tools_config_list,
            workflow_id=db_model.workflow_id,
            created_at=db_model.created_at,
            updated_at=db_model.updated_at,
        )

    @classmethod
    def from_effective_config(
        cls,
        assistant_id: str,
        user_id: str,
        workflow_id: str,
        tools_config: List[ToolConfig],
        has_assistant_scope_selection: bool,
    ) -> "AssistantMappingResponse":
        """
        Build a response for a workflow-scoped read.

        The payload is not a single stored row but the effective selection for that workflow,
        so there is no record id to report.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the requesting user
            workflow_id: Workflow the selection was resolved for
            tools_config: Effective tool configurations
            has_assistant_scope_selection: Whether an assistant-wide mapping exists

        Returns:
            API response model instance
        """
        return cls(
            id="",
            assistant_id=assistant_id,
            user_id=user_id,
            workflow_id=workflow_id,
            tools_config=[{"name": config.name, "integration_id": config.integration_id} for config in tools_config],
            has_assistant_scope_selection=has_assistant_scope_selection,
        )
