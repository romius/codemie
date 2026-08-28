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

"""Every per-user OAuth endpoint takes a caller-supplied setting_id.

Authentication alone is not enough: a user who learns another project's integration id must not
be able to start a flow against it (which would disclose that integration's OAuth app config
through the generated authorize URL), nor read or delete connection state under it.

Drives the real Jira/Confluence endpoints (built by the shared oauth_router_factory) via a FastAPI
TestClient.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

import codemie.rest_api.routers.confluence_oauth as confluence_router
import codemie.rest_api.routers.jira_oauth as jira_router
import codemie.rest_api.routers.oauth_router_factory as factory
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.authentication import authenticate

_ROUTERS = {
    "jira": (jira_router, "JIRA_OAUTH_ENABLED", "/v1/atlassian-oauth"),
    "confluence": (confluence_router, "CONFLUENCE_OAUTH_ENABLED", "/v1/confluence-oauth"),
}


def _setting(**creds):
    return SimpleNamespace(credential_values=[SimpleNamespace(key=k, value=v) for k, v in creds.items()])


@pytest.fixture(params=sorted(_ROUTERS))
def wired(request, monkeypatch):
    """Enable a provider and expose its module, prefix, and a TestClient builder."""
    module, flag, prefix = _ROUTERS[request.param]
    monkeypatch.setattr(module.config, flag, True, raising=False)

    def client(user_id="u1") -> TestClient:
        app = FastAPI()

        @app.exception_handler(ExtendedHTTPException)
        async def _handler(req, exc: ExtendedHTTPException):
            return JSONResponse(status_code=exc.code, content={"error": {"message": exc.message}})

        app.include_router(module.router)
        app.dependency_overrides[authenticate] = lambda: SimpleNamespace(id=user_id, username=user_id)
        return TestClient(app, raise_server_exceptions=False)

    return SimpleNamespace(module=module, prefix=prefix, client=client, monkeypatch=monkeypatch)


def test_endpoints_reject_caller_without_access(wired):
    monkeypatch, prefix = wired.monkeypatch, wired.prefix

    def _deny(*a, **k):
        raise ExtendedHTTPException(403, "Access denied")

    monkeypatch.setattr(factory, "load_authorized_setting", _deny, raising=True)
    monkeypatch.setattr(
        factory.ToolOAuthTokenPort,
        "get_oauth2_token_or_none",
        staticmethod(lambda *, user_id, integration_id: pytest.fail("token state must not be read")),
        raising=True,
    )

    client = wired.client("mallory")
    assert client.get(f"{prefix}/connection", params={"setting_id": "s1"}).status_code == 403
    assert client.delete(f"{prefix}/connection", params={"setting_id": "s1"}).status_code == 403
    assert client.post(f"{prefix}/connect", json={"setting_id": "s1"}).status_code == 403


def test_connection_status_is_scoped_to_the_caller(wired):
    monkeypatch, prefix = wired.monkeypatch, wired.prefix
    captured = {}
    monkeypatch.setattr(factory, "load_authorized_setting", lambda *a, **k: _setting(), raising=True)

    def fake_get(*, user_id, integration_id):
        captured["args"] = (integration_id, user_id)
        return None

    monkeypatch.setattr(factory.ToolOAuthTokenPort, "get_oauth2_token_or_none", staticmethod(fake_get), raising=True)

    resp = wired.client("alice").get(f"{prefix}/connection", params={"setting_id": "s1"})
    assert resp.json() == {"status": "not_connected", "username": ""}
    assert captured["args"] == ("s1", "alice")


def test_disconnect_revokes_only_the_caller_record(wired):
    monkeypatch, prefix = wired.monkeypatch, wired.prefix
    revoked = {}
    monkeypatch.setattr(factory, "load_authorized_setting", lambda *a, **k: _setting(), raising=True)

    class _Service:
        def revoke_connection(self, user_id, integration_id):
            revoked["args"] = (integration_id, user_id)

    monkeypatch.setattr(wired.module, "_get_oauth_service", lambda: _Service(), raising=True)

    resp = wired.client("bob").delete(f"{prefix}/connection", params={"setting_id": "s1"})
    assert resp.json() == {"status": "disconnected"}
    assert revoked["args"] == ("s1", "bob")
