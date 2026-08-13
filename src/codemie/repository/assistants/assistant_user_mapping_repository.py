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
Repository for assistant-to-tools mappings.
"""

from abc import ABC, abstractmethod
from datetime import datetime, UTC
from typing import Optional, List, Any
from uuid import uuid4

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from codemie.rest_api.models.usage.assistant_user_mapping import (
    ASSISTANT_SCOPE,
    AssistantUserMappingSQL,
    ToolConfig,
)


class AssistantUserMappingRepository(ABC):
    """
    Abstract base class for assistant mapping repository.
    Defines the interface for assistant-to-tools mapping data operations.
    """

    @abstractmethod
    def create_or_update_mapping(
        self,
        assistant_id: str,
        user_id: str,
        tools_config: List[ToolConfig],
        workflow_id: str = ASSISTANT_SCOPE,
    ) -> Any:
        """
        Create or update a mapping between an assistant and tools/settings.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user
            tools_config: List of tool configurations
            workflow_id: Scope of the mapping; ASSISTANT_SCOPE applies to the assistant everywhere

        Returns:
            The created or updated mapping record
        """
        pass

    @abstractmethod
    def get_mapping(self, assistant_id: str, user_id: str, workflow_id: str = ASSISTANT_SCOPE) -> Optional[Any]:
        """
        Get mapping for a specific assistant, user and scope.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user
            workflow_id: Scope to read; ASSISTANT_SCOPE reads the assistant-wide mapping

        Returns:
            Mapping record if found, None otherwise
        """
        pass

    @abstractmethod
    def promote_to_assistant_scope(
        self, assistant_id: str, user_id: str, tools_config: List[ToolConfig], workflow_id: str
    ) -> Any:
        """
        Store the selection at assistant scope and drop the given workflow's row atomically.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user
            tools_config: List of tool configurations to store assistant-wide
            workflow_id: Workflow whose scoped row must stop overriding the new selection

        Returns:
            The created or updated assistant-scoped mapping record
        """
        pass

    @abstractmethod
    def get_mappings_by_assistant(self, assistant_id: str) -> List[Any]:
        """
        Get all mappings for a specific assistant.

        Args:
            assistant_id: ID of the assistant

        Returns:
            List of mapping records for the assistant
        """
        pass

    @abstractmethod
    def get_mappings_by_user(self, user_id: str) -> List[Any]:
        """
        Get all mappings for a specific user.

        Args:
            user_id: ID of the user

        Returns:
            List of mapping records for the user
        """
        pass


class SQLAssistantUserMappingRepository(AssistantUserMappingRepository):
    """
    SQL implementation of the assistant mapping repository.
    Uses SQLModel to interact with the database.
    """

    def create_or_update_mapping(
        self,
        assistant_id: str,
        user_id: str,
        tools_config: List[ToolConfig],
        workflow_id: str = ASSISTANT_SCOPE,
    ) -> AssistantUserMappingSQL:
        """
        Create or update a mapping between an assistant and tools/settings.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user
            tools_config: List of tool configurations
            workflow_id: Scope of the mapping; ASSISTANT_SCOPE applies to the assistant everywhere

        Returns:
            The created or updated mapping record
        """
        mapping = self.get_mapping(assistant_id, user_id, workflow_id)

        if mapping:
            # Update existing record
            with Session(AssistantUserMappingSQL.get_engine()) as session:
                mapping.tools_config = tools_config
                mapping.updated_at = datetime.now(UTC)
                session.add(mapping)
                # Force the JSON column to persist: replacing the list on a detached-then-readded
                # instance is not always detected as dirty, which would leave stale entries (e.g. a
                # slot the user reset to "None" would appear to remain saved).
                flag_modified(mapping, "tools_config")
                session.commit()
                session.refresh(mapping)
                return mapping
        else:
            # Create new record with explicit ID
            with Session(AssistantUserMappingSQL.get_engine()) as session:
                # Create a new record
                mapping = AssistantUserMappingSQL(
                    id=str(uuid4()),
                    assistant_id=assistant_id,
                    user_id=user_id,
                    workflow_id=workflow_id,
                    tools_config=tools_config,
                )
                session.add(mapping)
                session.commit()
                session.refresh(mapping)
                return mapping

    def get_mapping(
        self, assistant_id: str, user_id: str, workflow_id: str = ASSISTANT_SCOPE
    ) -> Optional[AssistantUserMappingSQL]:
        """
        Get mapping for a specific assistant, user and scope.

        The scope predicate is mandatory: without it a workflow-scoped row could be returned to
        chat or the assistant page, where only the assistant-wide mapping may ever apply.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user
            workflow_id: Scope to read; ASSISTANT_SCOPE reads the assistant-wide mapping

        Returns:
            Mapping record if found, None otherwise
        """
        with Session(AssistantUserMappingSQL.get_engine()) as session:
            query = select(AssistantUserMappingSQL).where(
                AssistantUserMappingSQL.assistant_id == assistant_id,
                AssistantUserMappingSQL.user_id == user_id,
                AssistantUserMappingSQL.workflow_id == workflow_id,
            )
            return session.exec(query).first()

    def promote_to_assistant_scope(
        self, assistant_id: str, user_id: str, tools_config: List[ToolConfig], workflow_id: str
    ) -> AssistantUserMappingSQL:
        """
        Store the selection at assistant scope and drop the given workflow's row atomically.

        Both operations share one session and one commit: if the delete were a separate
        transaction and failed, the stale workflow row would keep overriding the selection the
        user just asked to apply everywhere.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user
            tools_config: List of tool configurations to store assistant-wide
            workflow_id: Workflow whose scoped row must stop overriding the new selection

        Returns:
            The created or updated assistant-scoped mapping record
        """
        with Session(AssistantUserMappingSQL.get_engine()) as session:
            assistant_mapping = session.exec(
                select(AssistantUserMappingSQL).where(
                    AssistantUserMappingSQL.assistant_id == assistant_id,
                    AssistantUserMappingSQL.user_id == user_id,
                    AssistantUserMappingSQL.workflow_id == ASSISTANT_SCOPE,
                )
            ).first()

            if assistant_mapping:
                assistant_mapping.tools_config = tools_config
                assistant_mapping.updated_at = datetime.now(UTC)
                session.add(assistant_mapping)
                flag_modified(assistant_mapping, "tools_config")
            else:
                assistant_mapping = AssistantUserMappingSQL(
                    id=str(uuid4()),
                    assistant_id=assistant_id,
                    user_id=user_id,
                    workflow_id=ASSISTANT_SCOPE,
                    tools_config=tools_config,
                )
                session.add(assistant_mapping)

            workflow_mapping = session.exec(
                select(AssistantUserMappingSQL).where(
                    AssistantUserMappingSQL.assistant_id == assistant_id,
                    AssistantUserMappingSQL.user_id == user_id,
                    AssistantUserMappingSQL.workflow_id == workflow_id,
                )
            ).first()
            if workflow_mapping:
                session.delete(workflow_mapping)

            session.commit()
            session.refresh(assistant_mapping)
            return assistant_mapping

    def get_mappings_by_assistant(self, assistant_id: str) -> List[AssistantUserMappingSQL]:
        """
        Get all mappings for a specific assistant.

        Args:
            assistant_id: ID of the assistant

        Returns:
            List of mapping records for the assistant
        """
        with Session(AssistantUserMappingSQL.get_engine()) as session:
            query = select(AssistantUserMappingSQL).where(AssistantUserMappingSQL.assistant_id == assistant_id)
            return session.exec(query).all()

    def get_mappings_by_user(self, user_id: str) -> List[AssistantUserMappingSQL]:
        """
        Get all mappings for a specific user.

        Args:
            user_id: ID of the user

        Returns:
            List of mapping records for the user
        """
        with Session(AssistantUserMappingSQL.get_engine()) as session:
            query = select(AssistantUserMappingSQL).where(AssistantUserMappingSQL.user_id == user_id)
            return session.exec(query).all()


# Default implementation
AssistantUserMappingRepositoryImpl = SQLAssistantUserMappingRepository
