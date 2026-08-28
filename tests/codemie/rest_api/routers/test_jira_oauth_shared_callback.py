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

"""The shared /v1/atlassian-oauth/callback must stay reachable when either Atlassian product's
OAuth is enabled — gating it on JIRA_OAUTH_ENABLED alone would 503 a valid Confluence callback."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

import codemie.rest_api.routers.jira_oauth as jira_oauth
from codemie.service.jira_oauth.flow_service import CallbackResult

_CALLBACK = "/v1/atlassian-oauth/callback"


class _FakeService:
    def __init__(self, result):
        self._result = result

    def handle_callback(self, code, state, error):
        return self._result


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(jira_oauth.router)
    return TestClient(app, raise_server_exceptions=False)


def test_callback_available_when_only_confluence_enabled(monkeypatch):
    # Jira OAuth off, Confluence OAuth on: the shared Atlassian callback must NOT 503.
    monkeypatch.setattr(jira_oauth.config, "JIRA_OAUTH_ENABLED", False, raising=False)
    monkeypatch.setattr(jira_oauth.config, "CONFLUENCE_OAUTH_ENABLED", True, raising=False)
    monkeypatch.setattr(jira_oauth, "_get_oauth_service", lambda: _FakeService(CallbackResult(True, "ok", 200)))

    resp = _client().get(_CALLBACK, params={"code": "c", "state": "s"})
    assert resp.status_code == 200


def test_callback_available_when_only_jira_enabled(monkeypatch):
    monkeypatch.setattr(jira_oauth.config, "JIRA_OAUTH_ENABLED", True, raising=False)
    monkeypatch.setattr(jira_oauth.config, "CONFLUENCE_OAUTH_ENABLED", False, raising=False)
    monkeypatch.setattr(jira_oauth, "_get_oauth_service", lambda: _FakeService(CallbackResult(True, "ok", 200)))

    resp = _client().get(_CALLBACK, params={"code": "c", "state": "s"})
    assert resp.status_code == 200


def test_callback_503_only_when_both_atlassian_products_disabled(monkeypatch):
    monkeypatch.setattr(jira_oauth.config, "JIRA_OAUTH_ENABLED", False, raising=False)
    monkeypatch.setattr(jira_oauth.config, "CONFLUENCE_OAUTH_ENABLED", False, raising=False)

    resp = _client().get(_CALLBACK, params={"code": "c", "state": "s"})
    assert resp.status_code == 503
