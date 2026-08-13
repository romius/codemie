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

"""Unit tests for MCPServerTester."""

from unittest.mock import Mock, patch

import pytest

from codemie.core.exceptions import MCPAuthenticationRequiredException
from codemie.rest_api.models.assistant import MCPServerCheckRequest, MCPServerDetails
from codemie.rest_api.security.user import User
from codemie.service.mcp.mcp_tester import MCPServerTester
from codemie.service.security.token_providers.base_provider import BrokerAuthRequiredException

_PATCH_TARGET = "codemie.service.mcp.toolkit_service.MCPToolkitService.get_mcp_server_tools"


@pytest.fixture
def mock_user():
    user = Mock(spec=User)
    user.id = "test-user-id"
    return user


@pytest.fixture
def mcp_server():
    return MCPServerDetails(name="Test MCP", enabled=True)


@pytest.fixture
def tester(mcp_server, mock_user):
    request = MCPServerCheckRequest(mcp_server=mcp_server)
    return MCPServerTester(request=request, user=mock_user)


class TestMCPServerTesterTest:
    """Verify MCPServerTester.test() behaviour across all outcomes."""

    def test_returns_false_when_tools_empty(self, tester):
        """Empty tools list must produce a False result with the server name in the message."""
        with patch(_PATCH_TARGET, return_value=[]):
            success, message = tester.test()

        assert success is False
        assert "Test MCP" in message

    def test_returns_true_when_tools_found(self, tester):
        """Non-empty tools list must produce (True, 'Success')."""
        with patch(_PATCH_TARGET, return_value=[Mock()]):
            success, message = tester.test()

        assert success is True
        assert message == "Success"

    def test_returns_false_on_generic_exception(self, tester):
        """Generic exceptions must be caught and returned as (False, msg)."""
        with patch(_PATCH_TARGET, side_effect=RuntimeError("connection refused")):
            success, message = tester.test()

        assert success is False
        assert "connection refused" in message

    def test_reraises_broker_auth_exception(self, tester):
        """BrokerAuthRequiredException must propagate unchanged."""
        exc = BrokerAuthRequiredException(
            message="Broker token exchange failed",
            auth_location="https://auth.example.com/login",
            details="HTTP 401",
        )
        with patch(_PATCH_TARGET, side_effect=exc):
            with pytest.raises(BrokerAuthRequiredException) as exc_info:
                tester.test()

        assert exc_info.value is exc

    def test_reraises_mcp_auth_exception(self, tester):
        """MCPAuthenticationRequiredException must propagate unchanged."""
        exc = MCPAuthenticationRequiredException({"error": "authentication_required", "servers": []})
        with patch(_PATCH_TARGET, side_effect=exc):
            with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
                tester.test()

        assert exc_info.value is exc

    def test_calls_get_mcp_server_tools_with_single_usage_true(self, tester):
        """get_mcp_server_tools must be called with mcp_server_single_usage=True."""
        with patch(_PATCH_TARGET, return_value=[Mock()]) as mock_get_tools:
            tester.test()

        assert mock_get_tools.call_args.kwargs.get("mcp_server_single_usage") is True
