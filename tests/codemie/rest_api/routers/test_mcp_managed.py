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

from unittest.mock import patch

import pytest
from fastapi import status
from httpx import AsyncClient, ASGITransport

from codemie.rest_api.main import app
from codemie.rest_api.security.user import User
from codemie.configs import managed_mcp_config as mcp_config
from codemie.configs.managed_mcp_config import ManagedMcpServer


@pytest.fixture
def user():
    return User(id="123", username="testuser", name="Test User")


@pytest.fixture(autouse=True)
def override_dependency(user):
    from codemie.rest_api.routers import mcp_managed as mcp_managed_router

    app.dependency_overrides[mcp_managed_router.authenticate] = lambda: user
    yield
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_list_managed_servers_returns_loaded_entries():
    entries = [
        ManagedMcpServer(name="sample", transport="http", url="https://mcp.example.com/mcp/sample", auth="oauth")
    ]
    with patch("codemie.rest_api.routers.mcp_managed.load_managed_mcp_servers", return_value=entries) as mock_load:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(
                "/v1/mcp/managed-servers?client=claude-desktop",
                headers={"Authorization": "Bearer testtoken"},
            )

        mock_load.assert_called_once_with(client="claude-desktop")
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == [
            {
                "name": "sample",
                "transport": "http",
                "url": "https://mcp.example.com/mcp/sample",
                "auth": "oauth",
                "description": None,
                "clients": None,
                "oauth": None,
            }
        ]


@pytest.mark.asyncio
async def test_list_managed_servers_serializes_oauth_in_camel_case():
    # The catalog YAML and the HTTP response use identical keys, so this pins
    # both the wire format and the accepted input spelling. `callbackHost` is
    # omitted on purpose -- the seam test for its "localhost" default.
    entries = [
        ManagedMcpServer(
            name="onehub_core",
            transport="http",
            url="https://codemie.lab.epam.com/mcp/mcp-proxy/onehub_core",
            auth="oauth",
            oauth={
                "clientId": "codemie-mcp-proxy",
                "scope": "openid profile email",
                "callbackPort": 3118,
                "authorizationUrl": "https://auth.example.com/realms/codemie/protocol/openid-connect/auth?kc_idp_hint=epam-oidc&prompt=login",
                "tokenUrl": "https://auth.example.com/realms/codemie/protocol/openid-connect/token",
            },
        )
    ]
    with patch("codemie.rest_api.routers.mcp_managed.load_managed_mcp_servers", return_value=entries):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/mcp/managed-servers", headers={"Authorization": "Bearer testtoken"})

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == [
        {
            "name": "onehub_core",
            "transport": "http",
            "url": "https://codemie.lab.epam.com/mcp/mcp-proxy/onehub_core",
            "auth": "oauth",
            "description": None,
            "clients": None,
            "oauth": {
                "clientId": "codemie-mcp-proxy",
                "scope": "openid profile email",
                "callbackHost": "localhost",
                "callbackPort": 3118,
                "authorizationServer": None,
                "authorizationUrl": "https://auth.example.com/realms/codemie/protocol/openid-connect/auth?kc_idp_hint=epam-oidc&prompt=login",
                "tokenUrl": "https://auth.example.com/realms/codemie/protocol/openid-connect/token",
            },
        }
    ]


@pytest.mark.asyncio
async def test_list_managed_servers_without_client_param():
    with patch("codemie.rest_api.routers.mcp_managed.load_managed_mcp_servers", return_value=[]) as mock_load:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/mcp/managed-servers", headers={"Authorization": "Bearer t"})

        mock_load.assert_called_once_with(client=None)
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == []


@pytest.mark.asyncio
async def test_authorization_server_survives_yaml_to_http(tmp_path, monkeypatch):
    # Closes the YAML -> HTTP seam: the real loader runs, reading
    # config.CUSTOMER_CONFIG_DIR at call time. Patching the loader instead
    # would stub out the very step that drops an undeclared key.
    (tmp_path / "managed-mcp-servers.yaml").write_text(
        """
servers:
  - name: onehub_core
    transport: http
    url: https://mcp.example.com/mcp/onehub_core
    oauth:
      clientId: codemie-mcp-proxy
      scope: openid profile email
      callbackPort: 3118
      authorizationServer: ["https://auth.example.com/realms/codemie"]
      authorizationUrl: https://auth.example.com/realms/codemie/protocol/openid-connect/auth
      tokenUrl: https://auth.example.com/realms/codemie/protocol/openid-connect/token
"""
    )
    monkeypatch.setattr(mcp_config.config, "CUSTOMER_CONFIG_DIR", tmp_path)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.get(
            "/v1/mcp/managed-servers?client=claude-desktop",
            headers={"Authorization": "Bearer t"},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()[0]["oauth"]["authorizationServer"] == ["https://auth.example.com/realms/codemie"]
