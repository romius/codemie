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

"""MCP-style aggregate OAuth connect gate.

All of the assistant's unconnected OAuth providers must be surfaced together in one gate; already
connected ones (and non-OAuth tools) must not be included; and everything connected must be a no-op.
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from codemie.core.exceptions import OAuthConnectRequiredException
from codemie_tools.core.project_management.confluence.models import ConfluenceConfig
from codemie_tools.core.project_management.jira.models import JiraConfig
from codemie_tools.core.vcs.gitlab.models import GitlabConfig

_GATE_PATH = Path(__file__).resolve().parents[4] / "src" / "codemie" / "service" / "tools" / "oauth_connect_gate.py"
_gate_spec = spec_from_file_location("oauth_connect_gate_under_test", _GATE_PATH)
assert _gate_spec and _gate_spec.loader
gate = module_from_spec(_gate_spec)
_gate_spec.loader.exec_module(gate)

_GITLAB = SimpleNamespace(name="gitlab", config=GitlabConfig(url="x", auth_type="oauth", integration_id="gl-1"))
_JIRA = SimpleNamespace(
    name="generic_jira_tool", config=JiraConfig(url="x", auth_type="oauth", integration_id="ji-1", cloud=True)
)
_CONF = SimpleNamespace(
    name="generic_confluence_tool",
    config=ConfluenceConfig(url="x", auth_type="oauth", integration_id="cf-1", cloud=True),
)
_PAT = SimpleNamespace(name="gitlab_pat", config=GitlabConfig(url="x", token="pat"))


def _run(tools, connected_setting_ids):
    with patch(
        "codemie.service.oauth.token_port.ToolOAuthTokenPort.has_connection",
        side_effect=lambda *, user_id, integration_id: integration_id in connected_setting_ids,
    ):
        gate.enforce_oauth_connected(tools, "user-1")


def test_aggregates_all_unconnected_providers():
    with pytest.raises(OAuthConnectRequiredException) as exc:
        _run([_GITLAB, _JIRA, _CONF], connected_setting_ids=set())
    errors = {p["error"] for p in exc.value.payload["providers"]}
    assert errors == {"gitlab_auth_required", "jira_auth_required", "confluence_auth_required"}


def test_only_unconnected_are_listed():
    with pytest.raises(OAuthConnectRequiredException) as exc:
        _run([_GITLAB, _JIRA], connected_setting_ids={"gl-1"})  # GitLab connected, Jira not
    errors = {p["error"] for p in exc.value.payload["providers"]}
    assert errors == {"jira_auth_required"}


def test_all_connected_is_noop():
    _run([_GITLAB, _JIRA], connected_setting_ids={"gl-1", "ji-1"})  # no raise


def test_no_oauth_tools_is_noop():
    _run([_PAT], connected_setting_ids=set())  # PAT tool has no oauth auth_type => nothing to gate


def test_dedupes_shared_setting_across_tools():
    tool_a = SimpleNamespace(
        name="jira_a", config=JiraConfig(url="x", auth_type="oauth", integration_id="ji-1", cloud=True)
    )
    tool_b = SimpleNamespace(
        name="jira_b", config=JiraConfig(url="x", auth_type="oauth", integration_id="ji-1", cloud=True)
    )
    with pytest.raises(OAuthConnectRequiredException) as exc:
        _run([tool_a, tool_b], connected_setting_ids=set())
    assert len(exc.value.payload["providers"]) == 1  # same setting_id listed once
