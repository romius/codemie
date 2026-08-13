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
Tools left without an integration on purpose stay visible and fail loudly.

Two deliberate states lead here: the author turned auto lookup off for the slot, or the user chose
"no integration" explicitly. Silently dropping the tool would leave the user wondering why the
assistant cannot do the thing it advertises, so the tool is built and raises on use instead.
"""

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools import ToolException

from codemie.core.models import ToolConfig
from codemie.rest_api.models.assistant import ToolDetails
from codemie.service.tools.toolkit_service import ToolkitService


@pytest.fixture
def tool_definition():
    definition = MagicMock()
    definition.name = "generic_jira_tool"
    definition.settings_config = True
    definition.config_class = MagicMock()
    definition.tool_class = MagicMock()
    return definition


def _initialize(assistant_tool, tools_config, tool_definition, toolkit_details=None, **kwargs):
    toolkit_definition = MagicMock()
    toolkit_definition.settings_config = True
    if toolkit_details is not None:
        kwargs["toolkit_details"] = toolkit_details
    with (
        patch(
            "codemie.service.tools.tool_metadata_service.ToolMetadataService._get_tool_and_toolkit_definitions",
            return_value=(tool_definition, toolkit_definition),
        ),
        patch("codemie.service.settings.settings.SettingsService.get_config") as mock_get_config,
    ):
        tool = ToolkitService._initialize_tool(
            "jira",
            assistant_tool,
            "user-1",
            "project-1",
            "assistant-1",
            tools_config,
            **kwargs,
        )
    return tool, mock_get_config


def test_explicit_no_integration_yields_a_tool_that_reports_it(tool_definition):
    assistant_tool = ToolDetails(name="generic_jira_tool", settings_config=True)

    tool, mock_get_config = _initialize(
        assistant_tool,
        # Built through the real constructor on purpose: the stored "no integration" choice has to
        # pass model validation, or the agent fails to start before any tool can report anything.
        [ToolConfig(name="generic_jira_tool", integration_id="")],
        tool_definition,
    )

    assert tool is not None
    mock_get_config.assert_not_called()
    with pytest.raises(ToolException) as exc:
        tool.run("anything")
    assert "integration" in str(exc.value).lower()


def test_auto_lookup_disabled_yields_the_same_tool(tool_definition):
    assistant_tool = ToolDetails(name="generic_jira_tool", settings_config=True, auto_credentials_lookup=False)

    tool, mock_get_config = _initialize(assistant_tool, None, tool_definition)

    assert tool is not None
    mock_get_config.assert_not_called()
    with pytest.raises(ToolException):
        tool.run("anything")


def test_auto_lookup_enabled_still_resolves_through_settings(tool_definition):
    assistant_tool = ToolDetails(name="generic_jira_tool", settings_config=True)

    _, mock_get_config = _initialize(assistant_tool, None, tool_definition)

    mock_get_config.assert_called_once()


def test_toolkit_pinned_slot_ignores_the_stored_no_integration_choice(tool_definition):
    # An integration pinned on the whole toolkit is as authoritative as one pinned on the tool: the
    # author owns the slot, so it resolves as usual instead of reporting a missing integration.
    assistant_tool = ToolDetails(name="generic_jira_tool", settings_config=True)
    toolkit_details = MagicMock(settings=MagicMock(id="author-int"), auto_credentials_lookup=True)

    _, mock_get_config = _initialize(
        assistant_tool,
        [ToolConfig(name="generic_jira_tool", integration_id="")],
        tool_definition,
        toolkit_details=toolkit_details,
    )

    mock_get_config.assert_called_once()


def test_toolkit_level_auto_lookup_disabled_stops_the_lookup(tool_definition):
    # Toolkits that carry one integration for all their tools expose the decision at their own level;
    # turning it off there must stop the per-user lookup for every tool inside.
    assistant_tool = ToolDetails(name="generic_jira_tool", settings_config=True)
    toolkit_details = MagicMock(settings=None, auto_credentials_lookup=False)

    tool, mock_get_config = _initialize(assistant_tool, None, tool_definition, toolkit_details=toolkit_details)

    mock_get_config.assert_not_called()
    with pytest.raises(ToolException):
        tool.run("anything")


def test_author_pinned_slot_ignores_the_disabled_flag(tool_definition):
    # A pinned integration is authoritative; the flag only governs slots left to the user.
    assistant_tool = ToolDetails.model_construct(
        name="generic_jira_tool",
        settings_config=True,
        auto_credentials_lookup=False,
        settings=MagicMock(id="author-int"),
    )

    _, mock_get_config = _initialize(assistant_tool, None, tool_definition)

    mock_get_config.assert_called_once()
