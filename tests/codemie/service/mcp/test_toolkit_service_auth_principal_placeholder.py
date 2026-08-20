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
Tests for the opt-in {{auth.principal_type}} MCP placeholder.

The placeholder resolves to "user" or "client" depending on which bearer
credential seeds the current request. It only exposes the principal type,
never the token value, and is never injected unless referenced.
"""

from unittest.mock import MagicMock, patch

import pytest

from codemie.rest_api.security.user import User
from codemie.service.mcp.models import MCPServerConfig
from codemie.service.mcp.toolkit_service import MCPToolkitService
from codemie.service.security.principal_token_resolver import PrincipalType


@pytest.fixture
def mock_user():
    user = MagicMock(spec=User)
    user.id = "user-123"
    user.name = "Test User"
    user.username = "test.user"
    return user


def make_server_config(headers=None, env=None) -> MCPServerConfig:
    return MCPServerConfig(command="uvx", args=["example-server"], env=env or {}, headers=headers)


@patch("codemie.service.mcp.toolkit_service.resolve_current_bearer_token")
@patch("codemie.service.mcp.toolkit_service.get_current_user")
def test_env_includes_auth_principal_type_user(mock_get_user, mock_resolve, mock_user):
    mock_get_user.return_value = mock_user
    mock_resolve.return_value = ("user-token", PrincipalType.USER)

    env_vars = MCPToolkitService._build_env_with_user_context(make_server_config())

    assert env_vars["auth"] == {"principal_type": "user"}


@patch("codemie.service.mcp.toolkit_service.resolve_current_bearer_token")
@patch("codemie.service.mcp.toolkit_service.get_current_user")
def test_env_includes_auth_principal_type_client(mock_get_user, mock_resolve, mock_user):
    mock_get_user.return_value = mock_user
    mock_resolve.return_value = ("client-token", PrincipalType.CLIENT)

    env_vars = MCPToolkitService._build_env_with_user_context(make_server_config())

    assert env_vars["auth"] == {"principal_type": "client"}


@patch("codemie.service.mcp.toolkit_service.resolve_current_bearer_token")
@patch("codemie.service.mcp.toolkit_service.get_current_user")
def test_env_omits_auth_when_no_bearer_resolvable(mock_get_user, mock_resolve, mock_user):
    mock_get_user.return_value = mock_user
    mock_resolve.return_value = (None, None)

    env_vars = MCPToolkitService._build_env_with_user_context(make_server_config())

    assert "auth" not in env_vars


@patch("codemie.service.mcp.toolkit_service.get_current_user")
def test_env_omits_auth_when_no_current_user(mock_get_user):
    mock_get_user.return_value = None

    env_vars = MCPToolkitService._build_env_with_user_context(make_server_config())

    assert "auth" not in env_vars


@patch("codemie.service.mcp.toolkit_service.resolve_current_bearer_token")
@patch("codemie.service.mcp.toolkit_service.get_current_user")
def test_env_never_contains_token_value(mock_get_user, mock_resolve, mock_user):
    """Only the principal type is exposed — never the resolved token."""
    mock_get_user.return_value = mock_user
    mock_resolve.return_value = ("secret-bearer-value", PrincipalType.CLIENT)

    env_vars = MCPToolkitService._build_env_with_user_context(make_server_config())

    assert "secret-bearer-value" not in str(env_vars)


@patch("codemie.service.mcp.toolkit_service.resolve_current_bearer_token")
@patch("codemie.service.mcp.toolkit_service.get_current_user")
def test_header_placeholder_resolves_to_principal_type(mock_get_user, mock_resolve, mock_user):
    """{{auth.principal_type}} in headers resolves through the placeholder pipeline."""
    mock_get_user.return_value = mock_user
    mock_resolve.return_value = ("client-token", PrincipalType.CLIENT)

    server_config = make_server_config(
        headers={
            "X-Auth-Principal-Type": "{{auth.principal_type}}",
            "X-User": "{{user.username}}",
        }
    )

    MCPToolkitService._process_headers_placeholders(server_config, None)

    assert server_config.headers == {
        "X-Auth-Principal-Type": "client",
        "X-User": "test.user",
    }


@patch("codemie.service.mcp.toolkit_service.resolve_current_bearer_token")
@patch("codemie.service.mcp.toolkit_service.get_current_user")
def test_placeholder_is_opt_in(mock_get_user, mock_resolve, mock_user):
    """Headers without the placeholder never receive principal-type data."""
    mock_get_user.return_value = mock_user
    mock_resolve.return_value = ("user-token", PrincipalType.USER)

    server_config = make_server_config(headers={"X-Static": "value"})

    MCPToolkitService._process_headers_placeholders(server_config, None)

    assert server_config.headers == {"X-Static": "value"}
