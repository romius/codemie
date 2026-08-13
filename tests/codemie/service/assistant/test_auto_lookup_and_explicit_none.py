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
Author-controlled auto lookup and the user's explicit "no integration" choice.

Three states must stay distinguishable for a regular tool slot: the author pinned an integration,
the author left auto lookup on (the chain picks the user's own integration), and the author turned
auto lookup off (nothing is resolved). On top of that the user may explicitly choose "None", which
has to be remembered instead of collapsing back into "nothing chosen yet".
"""

from unittest.mock import MagicMock

import pytest

from codemie.rest_api.models.assistant import ToolDetails, ToolKitDetails
from codemie.rest_api.models.usage.assistant_user_mapping import ASSISTANT_SCOPE, ToolConfig
from codemie.service.assistant.assistant_user_mapping_service import AssistantUserMappingService


def test_auto_lookup_defaults_to_enabled_for_existing_assistants():
    # Existing assistants carry no such field, and their tools resolve through the chain today, so
    # the default must keep that behaviour.
    tool = ToolDetails(name="generic_jira_tool", settings_config=True)
    toolkit = ToolKitDetails(toolkit="jira", tools=[tool], settings_config=True)

    assert tool.auto_credentials_lookup is True
    assert toolkit.auto_credentials_lookup is True


def test_auto_lookup_can_be_turned_off_per_slot():
    tool = ToolDetails(name="generic_jira_tool", settings_config=True, auto_credentials_lookup=False)

    assert tool.auto_credentials_lookup is False


@pytest.fixture
def service():
    return AssistantUserMappingService(repository=MagicMock())


def test_explicit_none_is_remembered_for_a_regular_slot(service):
    # An empty integration_id used to delete the slot, which made the choice indistinguishable from
    # "never chose anything" — the next load then re-applied auto lookup over the user's decision.
    service.repository.get_mapping.return_value = None

    service.create_or_update_mapping(
        assistant_id="a1",
        user_id="u1",
        tools_config=[{"name": "jira", "integration_id": ""}],
    )

    stored = service.repository.create_or_update_mapping.call_args[0][2]
    assert [(tc.name, tc.integration_id) for tc in stored] == [("jira", "")]


def test_explicit_none_still_clears_an_mcp_slot(service):
    # MCP keeps its shipped semantics: no selection means the author's base config, stored as the
    # absence of the slot.
    service.repository.get_mapping.return_value = MagicMock(
        tools_config=[ToolConfig(name="MCP:srv", integration_id="int-1")]
    )

    service.create_or_update_mapping(
        assistant_id="a1",
        user_id="u1",
        tools_config=[{"name": "MCP:srv", "integration_id": ""}],
        workflow_id=ASSISTANT_SCOPE,
    )

    stored = service.repository.create_or_update_mapping.call_args[0][2]
    assert stored == []
