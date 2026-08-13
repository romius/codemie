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
Isolation guarantees between the two personal integration scopes.

These tests run the service against an in-memory repository so the whole save/resolve cycle is
exercised: a selection made for one workflow must never reach another workflow, plain chat, or
another user.
"""

from typing import Dict, List, Optional, Tuple

import pytest

from codemie.rest_api.models.usage.assistant_user_mapping import (
    ASSISTANT_SCOPE,
    AssistantUserMappingSQL,
    ToolConfig,
)
from codemie.service.assistant.assistant_user_mapping_service import AssistantUserMappingService


class InMemoryMappingRepository:
    """Minimal stand-in storing rows under the (assistant, user, scope) key the DB enforces."""

    def __init__(self):
        self.rows: Dict[Tuple[str, str, str], AssistantUserMappingSQL] = {}

    def get_mapping(
        self, assistant_id: str, user_id: str, workflow_id: str = ASSISTANT_SCOPE
    ) -> Optional[AssistantUserMappingSQL]:
        return self.rows.get((assistant_id, user_id, workflow_id))

    def create_or_update_mapping(
        self,
        assistant_id: str,
        user_id: str,
        tools_config: List[ToolConfig],
        workflow_id: str = ASSISTANT_SCOPE,
    ) -> AssistantUserMappingSQL:
        mapping = AssistantUserMappingSQL(
            id=f"{assistant_id}:{user_id}:{workflow_id}",
            assistant_id=assistant_id,
            user_id=user_id,
            workflow_id=workflow_id,
            tools_config=list(tools_config),
        )
        self.rows[(assistant_id, user_id, workflow_id)] = mapping
        return mapping

    def promote_to_assistant_scope(
        self, assistant_id: str, user_id: str, tools_config: List[ToolConfig], workflow_id: str
    ) -> AssistantUserMappingSQL:
        mapping = self.create_or_update_mapping(assistant_id, user_id, tools_config, ASSISTANT_SCOPE)
        self.rows.pop((assistant_id, user_id, workflow_id), None)
        return mapping


@pytest.fixture
def service():
    return AssistantUserMappingService(repository=InMemoryMappingRepository())


def _slots(configs: List[ToolConfig]) -> Dict[str, str]:
    return {config.name: config.integration_id for config in configs}


def test_workflow_selection_does_not_reach_another_workflow(service):
    service.create_or_update_mapping(
        "assistant-1", "user-1", [{"name": "MCP:jira", "integration_id": "workflow-1-int"}], workflow_id="workflow-1"
    )

    configs, _ = service.get_effective_tools_config("assistant-1", "user-1", "workflow-2")

    assert configs == []


def test_workflow_selection_does_not_reach_chat(service):
    service.create_or_update_mapping(
        "assistant-1", "user-1", [{"name": "MCP:jira", "integration_id": "workflow-1-int"}], workflow_id="workflow-1"
    )

    assert service.get_mapping("assistant-1", "user-1") is None


def test_workflow_selection_does_not_reach_another_user(service):
    service.create_or_update_mapping(
        "assistant-1", "user-1", [{"name": "MCP:jira", "integration_id": "workflow-1-int"}], workflow_id="workflow-1"
    )

    configs, _ = service.get_effective_tools_config("assistant-1", "user-2", "workflow-1")

    assert configs == []


def test_workflow_selection_overrides_the_assistant_one_only_inside_that_workflow(service):
    service.create_or_update_mapping("assistant-1", "user-1", [{"name": "MCP:jira", "integration_id": "assistant-int"}])
    service.create_or_update_mapping(
        "assistant-1", "user-1", [{"name": "MCP:jira", "integration_id": "workflow-1-int"}], workflow_id="workflow-1"
    )

    in_workflow_1, _ = service.get_effective_tools_config("assistant-1", "user-1", "workflow-1")
    in_workflow_2, _ = service.get_effective_tools_config("assistant-1", "user-1", "workflow-2")
    in_chat = service.get_mapping("assistant-1", "user-1")

    assert _slots(in_workflow_1) == {"MCP:jira": "workflow-1-int"}
    assert _slots(in_workflow_2) == {"MCP:jira": "assistant-int"}
    assert _slots(in_chat.tools_config) == {"MCP:jira": "assistant-int"}


def test_apply_to_assistant_leaves_other_workflows_untouched(service):
    service.create_or_update_mapping(
        "assistant-1", "user-1", [{"name": "MCP:jira", "integration_id": "workflow-1-int"}], workflow_id="workflow-1"
    )
    service.create_or_update_mapping(
        "assistant-1", "user-1", [{"name": "MCP:jira", "integration_id": "workflow-2-int"}], workflow_id="workflow-2"
    )

    service.create_or_update_mapping(
        "assistant-1",
        "user-1",
        [{"name": "MCP:jira", "integration_id": "everywhere-int"}],
        workflow_id="workflow-1",
        apply_to_assistant=True,
    )

    in_workflow_1, _ = service.get_effective_tools_config("assistant-1", "user-1", "workflow-1")
    in_workflow_2, _ = service.get_effective_tools_config("assistant-1", "user-1", "workflow-2")
    in_chat = service.get_mapping("assistant-1", "user-1")

    # The workflow the user saved from now inherits the assistant-wide value, while the other
    # workflow keeps the selection saved for it earlier.
    assert _slots(in_workflow_1) == {"MCP:jira": "everywhere-int"}
    assert _slots(in_workflow_2) == {"MCP:jira": "workflow-2-int"}
    assert _slots(in_chat.tools_config) == {"MCP:jira": "everywhere-int"}


def test_untouched_slots_are_inherited_from_the_assistant_scope(service):
    service.create_or_update_mapping(
        "assistant-1",
        "user-1",
        [
            {"name": "Git", "integration_id": "assistant-git"},
            {"name": "MCP:jira", "integration_id": "assistant-jira"},
        ],
    )
    service.create_or_update_mapping(
        "assistant-1", "user-1", [{"name": "MCP:jira", "integration_id": "workflow-jira"}], workflow_id="workflow-1"
    )

    configs, has_assistant_scope = service.get_effective_tools_config("assistant-1", "user-1", "workflow-1")

    assert _slots(configs) == {"Git": "assistant-git", "MCP:jira": "workflow-jira"}
    assert has_assistant_scope is True
