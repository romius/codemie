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

"""Test is_safe() on generic REST tools: GET -> True, POST -> False, missing -> False."""

import json
import pytest


# --- Pattern A: method in args["method"] ---


def _make_jira_tool():
    from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool

    return GenericJiraIssueTool.__new__(GenericJiraIssueTool)


def _make_confluence_tool():
    from codemie_tools.core.project_management.confluence.tools import GenericConfluenceTool

    return GenericConfluenceTool.__new__(GenericConfluenceTool)


def _make_keycloak_tool():
    from codemie_tools.access_management.keycloak.tools import KeycloakTool

    return KeycloakTool.__new__(KeycloakTool)


@pytest.mark.parametrize(
    "make_tool",
    [
        _make_jira_tool,
        _make_confluence_tool,
        _make_keycloak_tool,
    ],
)
def test_pattern_a_get_is_safe(make_tool):
    tool = make_tool()
    assert tool.is_safe({"method": "GET"}) is True


@pytest.mark.parametrize(
    "make_tool",
    [
        _make_jira_tool,
        _make_confluence_tool,
        _make_keycloak_tool,
    ],
)
def test_pattern_a_post_is_unsafe(make_tool):
    tool = make_tool()
    assert tool.is_safe({"method": "POST"}) is False


@pytest.mark.parametrize(
    "make_tool",
    [
        _make_jira_tool,
        _make_confluence_tool,
        _make_keycloak_tool,
    ],
)
def test_pattern_a_missing_method_is_unsafe(make_tool):
    tool = make_tool()
    assert tool.is_safe({}) is False


# --- Pattern B: method nested in args["query"]["method"] ---


def _make_gitlab_tool():
    from codemie_tools.core.vcs.gitlab.tools import GitlabTool

    return GitlabTool.__new__(GitlabTool)


def _make_github_tool():
    from codemie_tools.core.vcs.github.tools import GithubTool

    return GithubTool.__new__(GithubTool)


def _make_aws_tool():
    from codemie_tools.cloud.aws.tools import GenericAWSTool

    return GenericAWSTool.__new__(GenericAWSTool)


@pytest.mark.parametrize(
    "make_tool",
    [
        _make_gitlab_tool,
        _make_github_tool,
        _make_aws_tool,
    ],
)
def test_pattern_b_dict_get_is_safe(make_tool):
    tool = make_tool()
    assert tool.is_safe({"query": {"method": "GET"}}) is True


@pytest.mark.parametrize(
    "make_tool",
    [
        _make_gitlab_tool,
        _make_github_tool,
        _make_aws_tool,
    ],
)
def test_pattern_b_dict_post_is_unsafe(make_tool):
    tool = make_tool()
    assert tool.is_safe({"query": {"method": "POST"}}) is False


@pytest.mark.parametrize(
    "make_tool",
    [
        _make_gitlab_tool,
        _make_github_tool,
        _make_aws_tool,
    ],
)
def test_pattern_b_json_string_get_is_safe(make_tool):
    tool = make_tool()
    assert tool.is_safe({"query": json.dumps({"method": "GET"})}) is True


@pytest.mark.parametrize(
    "make_tool",
    [
        _make_gitlab_tool,
        _make_github_tool,
        _make_aws_tool,
    ],
)
def test_pattern_b_missing_query_is_unsafe(make_tool):
    tool = make_tool()
    assert tool.is_safe({}) is False


@pytest.mark.parametrize(
    "make_tool",
    [
        _make_gitlab_tool,
        _make_github_tool,
        _make_aws_tool,
    ],
)
def test_pattern_b_invalid_json_string_is_unsafe(make_tool):
    tool = make_tool()
    assert tool.is_safe({"query": "not-valid-json"}) is False
