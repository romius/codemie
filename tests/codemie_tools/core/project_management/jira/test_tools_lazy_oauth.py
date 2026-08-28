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

"""The Jira tool must build its client lazily.

Regression: an OAuth-backed Jira tool used to resolve the per-user token in __init__, so merely
assembling the toolset tripped the connect gate at build time — before the aggregate gate could
collect all unconnected providers. Construction must not touch the token manager.
"""

from unittest.mock import patch

from codemie_tools.core.project_management.jira.models import JiraConfig
from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool


def _oauth_config() -> JiraConfig:
    return JiraConfig(url="", auth_type="oauth", integration_id="setting-1", acting_user_id="user-1", cloud=True)


def test_construction_does_not_resolve_oauth():
    with patch.object(GenericJiraIssueTool, "_resolve_oauth") as resolve:
        tool = GenericJiraIssueTool(config=_oauth_config())
    resolve.assert_not_called()
    assert tool.jira is None
    assert tool.issue_search_pattern == r"/rest/api/3/search/jql"


def test_ensure_client_resolves_oauth_once():
    tool = GenericJiraIssueTool(config=_oauth_config())
    with (
        patch.object(GenericJiraIssueTool, "_resolve_oauth", return_value=("tok", "https://base")) as resolve,
        patch("codemie_tools.core.project_management.jira.tools.Jira") as jira_cls,
        patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"),
    ):
        tool._ensure_client()
        tool._ensure_client()  # second call reuses the built client
    resolve.assert_called_once()
    jira_cls.assert_called_once()
