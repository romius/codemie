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

from __future__ import annotations

import hashlib
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient

from codemie.core.exceptions import ExtendedHTTPException
from codemie.enterprise.mcp_auth.router import authenticate as router_authenticate
from codemie.rest_api.main import extended_http_exception_handler
from codemie.rest_api.security.user import User


def _build_app():
    from codemie.enterprise.mcp_auth.router import enabled_router

    app = FastAPI()
    app.include_router(enabled_router)
    app.add_exception_handler(ExtendedHTTPException, extended_http_exception_handler)
    return app, TestClient(app)


def _build_user(**overrides: object) -> User:
    payload = {
        "id": "user-1",
        "name": "Test User",
        "auth_token": "Bearer token-123",
    }
    payload.update(overrides)
    user = User(**payload)
    user.is_admin = bool(payload.get("is_admin", False))
    user.is_maintainer = bool(payload.get("is_maintainer", False))
    return user


def _build_mcp_config(
    *,
    owner_id: str = "user-1",
    is_public: bool = False,
    auth_config: dict[str, object] | None = None,
):
    return SimpleNamespace(
        id="mcp-config-1",
        user_id=owner_id,
        is_public=is_public,
        config=SimpleNamespace(
            url="https://mcp.example.com/",
            auth_config=auth_config
            or {
                "id": "auth-config-1",
                "auth_type": "saml",
                "sso_url": "https://idp.example.com/sso",
                "entity_id": "urn:codemie:test:sp",
                "idp_entity_id": "https://idp.example.com/metadata",
                "idp_x509cert": "CERTDATA",
                "saml_credential_attribute": "mail",
                "saml_session_ttl": 3600,
                "token_delivery": {"method": "header"},
            },
        ),
    )


@pytest.fixture
def app_client():
    return _build_app()


def test_saml_initiate_route_authenticates_loads_config_and_keeps_auth_config_server_owned(
    monkeypatch, app_client
) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    user = _build_user()
    mcp_config = _build_mcp_config()
    captured: dict[str, object] = {}

    def fake_build_saml_initiate_response(**kwargs):
        captured.update(kwargs)
        data = {"auth_url": "https://idp.example.com/sso?SAMLRequest=req&RelayState=relay"}
        result = SimpleNamespace(**data)
        result.model_dump = lambda: data
        return result

    app.dependency_overrides[router_authenticate] = lambda: user
    monkeypatch.setattr(
        mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: mcp_config if config_id == mcp_config.id else None
    )
    monkeypatch.setattr(mcp_auth_router, "build_saml_initiate_response", fake_build_saml_initiate_response)

    response = client.post(
        "/v1/mcp-auth/saml/initiate",
        json={"mcp_config_id": mcp_config.id, "auth_config_id": "client-controlled-value"},
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"auth_url": "https://idp.example.com/sso?SAMLRequest=req&RelayState=relay"}
    assert captured["user"] == user
    assert captured["auth_config_id"] == "auth-config-1"
    assert captured["raw_auth_config"] == mcp_config.config.auth_config


def test_saml_initiate_route_rejects_private_non_owned_config(monkeypatch, app_client) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    app.dependency_overrides[router_authenticate] = lambda: _build_user(id="other-user", auth_token="Bearer token-123")
    monkeypatch.setattr(
        mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: _build_mcp_config(owner_id="owner-user")
    )

    response = client.post("/v1/mcp-auth/saml/initiate", json={"mcp_config_id": "mcp-config-1"})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["error"]["message"] == "Access denied"


def test_saml_initiate_route_returns_not_found_for_missing_config(monkeypatch, app_client) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    app.dependency_overrides[router_authenticate] = lambda: _build_user()
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: None)

    response = client.post("/v1/mcp-auth/saml/initiate", json={"mcp_config_id": "missing-config"})

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["error"]["message"] == "MCP configuration not found"


def test_saml_initiate_route_returns_400_for_non_saml_auth_config(monkeypatch, app_client) -> None:
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    app.dependency_overrides[router_authenticate] = lambda: _build_user()
    monkeypatch.setattr(
        mcp_auth_router.MCPConfig,
        "find_by_id",
        lambda config_id: _build_mcp_config(
            auth_config={
                "id": "auth-config-1",
                "auth_type": "oauth2",
                "authorization_url": "https://idp.example.com/oauth2/authorize",
                "token_url": "https://idp.example.com/oauth2/token",
                "client_id": "client-1",
                "client_type": "public",
                "scopes": ["openid"],
                "token_delivery": {"method": "header"},
            }
        ),
    )

    response = client.post("/v1/mcp-auth/saml/initiate", json={"mcp_config_id": "mcp-config-1"})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["error"]["message"] == "Invalid MCP auth configuration"


def test_build_saml_initiate_response_derives_platform_acs_url(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies

    captured: dict[str, object] = {}

    def fake_build_saml_initiate_response(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(auth_url="https://idp.example.com/sso?SAMLRequest=req&RelayState=relay")

    monkeypatch.setattr(mcp_auth_dependencies.config, "CALLBACK_API_BASE_URL", "http://localhost:8080")
    monkeypatch.setattr(mcp_auth_dependencies, "_saml_relay_state_store", SimpleNamespace())
    monkeypatch.setattr(mcp_auth_dependencies, "_redis_encryption", SimpleNamespace(signing_key=b"s" * 32))
    monkeypatch.setitem(
        sys.modules,
        "codemie_enterprise.mcp_auth",
        SimpleNamespace(
            MCPAuthRedisUnavailable=RuntimeError,
            SAMLAuthConfig=SimpleNamespace(model_validate=lambda raw: raw),
            build_saml_initiate_response=fake_build_saml_initiate_response,
        ),
    )

    response = mcp_auth_dependencies.build_saml_initiate_response(
        raw_auth_config=_build_mcp_config().config.auth_config,
        user=_build_user(),
        auth_config_id="auth-config-1",
        mcp_config_id="mcp-config-1",
    )

    assert response.auth_url.startswith("https://idp.example.com/sso")
    assert captured["acs_url"] == "http://localhost:8080/v1/mcp-auth/saml/acs"
    assert captured["auth_config_id"] == "auth-config-1"
    assert captured["session_binding_hash"] == hashlib.sha256("Bearer token-123".encode("utf-8")).hexdigest()


def test_build_saml_acs_url_returns_full_url_for_non_default_https_port(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies

    monkeypatch.setattr(mcp_auth_dependencies.config, "CALLBACK_API_BASE_URL", "https://api.example.com:9443")

    acs_url = mcp_auth_dependencies.build_saml_acs_url()

    assert acs_url == "https://api.example.com:9443/v1/mcp-auth/saml/acs"


@pytest.mark.parametrize(
    "callback_base_url",
    [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://[::1]:8080",
    ],
)
def test_build_saml_acs_url_allows_localhost_family_hosts(monkeypatch, callback_base_url: str) -> None:
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies

    monkeypatch.setattr(mcp_auth_dependencies.config, "CALLBACK_API_BASE_URL", callback_base_url)

    acs_url = mcp_auth_dependencies.build_saml_acs_url()

    assert acs_url.endswith("/v1/mcp-auth/saml/acs")
    assert acs_url.startswith(callback_base_url)


def test_build_saml_acs_url_rejects_http_non_localhost(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies

    monkeypatch.setattr(mcp_auth_dependencies.config, "CALLBACK_API_BASE_URL", "http://api.example.com:8080")

    with pytest.raises(ExtendedHTTPException) as exc_info:
        mcp_auth_dependencies.build_saml_acs_url()

    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST
    assert "ACS URL must use HTTPS" in exc_info.value.details


def test_build_saml_initiate_response_translates_missing_authn_request_id(monkeypatch) -> None:
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies

    monkeypatch.setattr(mcp_auth_dependencies.config, "CALLBACK_API_BASE_URL", "https://api.example.com")
    monkeypatch.setattr(mcp_auth_dependencies, "_saml_relay_state_store", SimpleNamespace())
    monkeypatch.setattr(mcp_auth_dependencies, "_redis_encryption", SimpleNamespace(signing_key=b"s" * 32))
    monkeypatch.setitem(
        sys.modules,
        "codemie_enterprise.mcp_auth",
        SimpleNamespace(
            MCPAuthRedisUnavailable=RuntimeError,
            SAMLAuthConfig=SimpleNamespace(model_validate=lambda raw: raw),
            build_saml_initiate_response=lambda **kwargs: (_ for _ in ()).throw(
                ValueError("SAML toolkit did not expose an AuthnRequest ID")
            ),
        ),
    )

    with pytest.raises(ExtendedHTTPException) as exc_info:
        mcp_auth_dependencies.build_saml_initiate_response(
            raw_auth_config=_build_mcp_config().config.auth_config,
            user=_build_user(),
            auth_config_id="auth-config-1",
            mcp_config_id="mcp-config-1",
        )

    assert exc_info.value.code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert exc_info.value.message == "MCP auth initiation failed"
    assert exc_info.value.details == "SAML toolkit did not expose an AuthnRequest ID"


def test_saml_initiate_route_fails_closed_when_auth_token_missing(monkeypatch, app_client) -> None:
    from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies
    from codemie.enterprise.mcp_auth import router as mcp_auth_router

    app, client = app_client
    app.dependency_overrides[router_authenticate] = lambda: _build_user(auth_token=None)
    monkeypatch.setattr(mcp_auth_router.MCPConfig, "find_by_id", lambda config_id: _build_mcp_config())
    monkeypatch.setattr(mcp_auth_dependencies.config, "CALLBACK_API_BASE_URL", "http://localhost:8080")
    monkeypatch.setattr(mcp_auth_dependencies, "_saml_relay_state_store", SimpleNamespace())
    monkeypatch.setattr(mcp_auth_dependencies, "_redis_encryption", SimpleNamespace(signing_key=b"s" * 32))
    monkeypatch.setitem(
        sys.modules,
        "codemie_enterprise.mcp_auth",
        SimpleNamespace(
            MCPAuthRedisUnavailable=RuntimeError,
            SAMLAuthConfig=SimpleNamespace(model_validate=lambda raw: raw),
            build_saml_initiate_response=lambda **kwargs: {"auth_url": "unused"},
        ),
    )

    response = client.post("/v1/mcp-auth/saml/initiate", json={"mcp_config_id": "mcp-config-1"})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert (
        response.json()["error"]["details"]
        == "Authenticated MCP auth initiation requires a bearer token for session binding."
    )


def test_disabled_saml_router_still_returns_story_1_4_payload() -> None:
    from codemie.enterprise.mcp_auth.router import router as disabled_router

    app = FastAPI()
    app.include_router(disabled_router)
    client = TestClient(app)

    response = client.post("/v1/mcp-auth/saml/initiate")

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {
        "feature": "MCP Authorization",
        "state": "inactive — enterprise package not installed or MCP_AUTH_ENABLED not set",
        "action": "Enable MCP_AUTH_ENABLED and install the enterprise package",
    }
