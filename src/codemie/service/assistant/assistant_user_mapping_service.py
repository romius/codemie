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
Service for managing assistant-to-tools mappings.
"""

from typing import Dict, List, Optional

from codemie.configs import logger
from codemie.repository.assistants.assistant_user_mapping_repository import (
    AssistantUserMappingRepositoryImpl,
    AssistantUserMappingRepository,
)
from codemie.service.mcp.toolkit_service import MCP_TOOL_CONFIG_PREFIX
from codemie.rest_api.models.usage.assistant_user_mapping import (
    ASSISTANT_SCOPE,
    AssistantUserMappingSQL,
    ToolConfig,
)


def _is_mcp_slot(name: str) -> bool:
    """MCP slots are addressed as ``MCP:<server>`` and keep their own "no selection" semantics."""
    return name.startswith(MCP_TOOL_CONFIG_PREFIX)


class AssistantUserMappingService:
    """Service for managing assistant-to-tools mappings."""

    def __init__(self, repository: Optional[AssistantUserMappingRepository] = None):
        """Initialize the service with a repository."""
        self.repository = repository if repository else AssistantUserMappingRepositoryImpl()

    def create_or_update_mapping(
        self,
        assistant_id: str,
        user_id: str,
        tools_config: List[Dict[str, str]],
        workflow_id: str = ASSISTANT_SCOPE,
        apply_to_assistant: bool = False,
    ) -> AssistantUserMappingSQL:
        """
        Create or update a mapping between an assistant and tools/settings.

        The incoming ``tools_config`` is merged into the user's existing mapping of the SAME
        scope per slot name:
        - a slot with a non-empty ``integration_id`` is upserted (added or replaced);
        - a slot with an empty ``integration_id`` is removed (user reset it to "None", so the
          tool/server falls back to the author's base config);
        - slots not present in ``tools_config`` are left untouched.

        Scopes never merge into each other: a workflow-scoped save reads and writes only the
        workflow row, so the user's assistant-wide selection stays intact.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user
            tools_config: List of tool configurations with name and integration_id
            workflow_id: Scope the save came from; ASSISTANT_SCOPE means the assistant page
            apply_to_assistant: Store at assistant scope instead, and drop the workflow row for
                ``workflow_id`` so the freshly saved values apply there too. Rows of other
                workflows are deliberately left alone.

        Returns:
            The created or updated mapping record
        """
        target_scope = ASSISTANT_SCOPE if apply_to_assistant else workflow_id

        logger.debug(
            f"Creating or updating mapping for assistant {assistant_id}, user {user_id}, scope '{target_scope}'"
        )

        upserts: Dict[str, ToolConfig] = {}
        removals: set[str] = set()
        for config in tools_config:
            name = config.get("name")
            if not name:
                continue
            integration_id = config.get("integration_id")
            if integration_id:
                upserts[name] = ToolConfig(name=name, integration_id=integration_id)
            elif _is_mcp_slot(name):
                # MCP keeps its shipped meaning: no selection is the author's base config, stored as
                # the absence of the slot.
                removals.add(name)
            else:
                # A regular tool slot must remember that the user explicitly wants no integration.
                # Dropping the slot would make that indistinguishable from "never chose anything",
                # and auto lookup would silently override the decision on the next load.
                upserts[name] = ToolConfig(name=name, integration_id="")

        existing = self.repository.get_mapping(assistant_id, user_id, target_scope)
        merged: Dict[str, ToolConfig] = {tc.name: tc for tc in existing.tools_config} if existing else {}

        for name in removals:
            merged.pop(name, None)
        merged.update(upserts)

        if apply_to_assistant and workflow_id != ASSISTANT_SCOPE:
            # One transaction: a half-applied save would leave the stale workflow row overriding
            # the selection the user just asked to apply everywhere.
            return self.repository.promote_to_assistant_scope(assistant_id, user_id, list(merged.values()), workflow_id)

        return self.repository.create_or_update_mapping(assistant_id, user_id, list(merged.values()), target_scope)

    def get_mapping(
        self, assistant_id: str, user_id: str, workflow_id: str = ASSISTANT_SCOPE
    ) -> Optional[AssistantUserMappingSQL]:
        """
        Get mapping for a specific assistant, user and scope.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user
            workflow_id: Scope to read; ASSISTANT_SCOPE reads the assistant-wide mapping

        Returns:
            Mapping record if found, None otherwise
        """
        logger.debug(f"Getting mapping for assistant {assistant_id}, user {user_id}, scope '{workflow_id}'")
        return self.repository.get_mapping(assistant_id, user_id, workflow_id)

    def get_effective_tools_config(
        self, assistant_id: str, user_id: str, workflow_id: str
    ) -> tuple[List[ToolConfig], bool]:
        """
        Resolve the slots effective for a user inside a workflow.

        The assistant-wide selection is the baseline and the workflow-scoped one overrides it
        per slot name, so a slot the user never touched in this workflow keeps whatever the
        assistant scope says instead of silently dropping to the base configuration.

        Args:
            assistant_id: ID of the assistant
            user_id: ID of the user
            workflow_id: Workflow being executed or displayed

        Returns:
            Tuple of the effective tool configurations and whether the user has a real
            assistant-wide selection — an empty leftover row does not count, since the panel uses
            the flag to decide whether this is the user's first-ever selection
        """
        assistant_mapping = self.repository.get_mapping(assistant_id, user_id, ASSISTANT_SCOPE)
        merged: Dict[str, ToolConfig] = (
            {tc.name: tc for tc in assistant_mapping.tools_config} if assistant_mapping else {}
        )

        if workflow_id and workflow_id != ASSISTANT_SCOPE:
            workflow_mapping = self.repository.get_mapping(assistant_id, user_id, workflow_id)
            if workflow_mapping:
                merged.update({tc.name: tc for tc in workflow_mapping.tools_config})

        return list(merged.values()), bool(assistant_mapping and assistant_mapping.tools_config)

    def get_mappings_by_assistant(self, assistant_id: str) -> List[AssistantUserMappingSQL]:
        """
        Get all mappings for a specific assistant.

        Args:
            assistant_id: ID of the assistant

        Returns:
            List of mapping records for the assistant
        """
        logger.debug(f"Getting all mappings for assistant {assistant_id}")
        return self.repository.get_mappings_by_assistant(assistant_id)

    def get_mappings_by_user(self, user_id: str) -> List[AssistantUserMappingSQL]:
        """
        Get all mappings for a specific user.

        Args:
            user_id: ID of the user

        Returns:
            List of mapping records for the user
        """
        logger.debug(f"Getting all mappings for user {user_id}")
        return self.repository.get_mappings_by_user(user_id)


# Create a singleton instance of the service
assistant_user_mapping_service = AssistantUserMappingService()
