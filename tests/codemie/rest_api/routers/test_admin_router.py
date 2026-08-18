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

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from codemie.rest_api.routers.admin import (
    add_application,
    get_applications,
    get_speech_token,
    reload_llm_models,
    router,
    trigger_spend_collection,
)
from codemie.rest_api.security.authentication import admin_access_only, admin_or_maintainer_or_auditor_access
from codemie.rest_api.security.user import User

ADMIN_MODULE = "codemie.rest_api.routers.admin"


def _async_lock_cm(acquired: bool):
    """Build an async context manager that yields `acquired` (mirrors async_leader_lock behaviour)."""

    @asynccontextmanager
    async def _cm(_lock_id):
        yield acquired

    return _cm


def test_get_applications_returns_application_names():
    applications = [
        SimpleNamespace(name="proj-a", display_name=None),
        SimpleNamespace(name="proj-b", display_name=None),
    ]

    with patch("codemie.rest_api.routers.admin.Application.search_by_name", return_value=applications):
        response = get_applications(search="proj", limit=2)

    assert [app.name for app in response.applications] == ["proj-a", "proj-b"]


def test_add_application_creates_application_and_emits_metric():
    admin = User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)
    request = SimpleNamespace(name="proj-a")

    with (
        patch("codemie.service.user.application_service.application_service.create_application") as mock_create,
        patch("codemie.rest_api.routers.admin.ProjectMonitoringService.send_project_creation_metric") as mock_metric,
    ):
        mock_create.return_value = SimpleNamespace(name="proj-a")
        response = add_application(request, admin=admin)

    assert response.message == f"Application {request} has been created"
    mock_create.assert_called_once_with("proj-a")
    mock_metric.assert_called_once_with(user=admin, project_name="proj-a")


def test_get_speech_token_uses_config_values():
    with patch("codemie.rest_api.routers.admin.config") as mock_config:
        mock_config.AZURE_SPEECH_SERVICE_KEY = "speech-token"
        mock_config.AZURE_SPEECH_REGION = "westus"

        response = get_speech_token()

    assert response == {
        "token": "speech-token",
        "region": "westus",
        "stt_wss_url": "wss://westus.stt.speech.microsoft.com/speech/universal/v2",
        "tts_url": "https://westus.tts.speech.microsoft.com/cognitiveservices/avatar/relay/token/v1",
    }


@pytest.mark.asyncio
async def test_reload_llm_models_refreshes_proxy_models():
    admin = User(id="admin-1", username="admin", email="admin@example.com", is_admin=True)
    models = SimpleNamespace(chat_models=["chat-1", "chat-2"], embedding_models=["embed-1"])
    proxy_provider = SimpleNamespace(reload_models_cache=MagicMock())
    llm_service = SimpleNamespace(initialize_default_litellm_models=MagicMock())

    with (
        patch("codemie.enterprise.litellm.require_litellm_enabled") as mock_require,
        patch("codemie.enterprise.litellm.get_available_models", return_value=models),
        patch(
            "codemie.service.llm_proxy.provider_registry.get_active_llm_proxy_provider",
            return_value=proxy_provider,
        ),
        patch("codemie.service.llm_service.llm_service.llm_service", llm_service),
    ):
        response = await reload_llm_models(admin=admin)

    assert response.message == "Successfully reloaded LLM models: 2 chat models, 1 embedding models"
    mock_require.assert_called_once_with()
    proxy_provider.reload_models_cache.assert_called_once_with()
    llm_service.initialize_default_litellm_models.assert_called_once_with(models)


# ── trigger_spend_collection ──────────────────────────────────────────


@pytest.mark.asyncio
@patch(f"{ADMIN_MODULE}.async_leader_lock", side_effect=_async_lock_cm(acquired=False))
@patch(f"{ADMIN_MODULE}.config")
async def test_trigger_spend_collection_returns_409_when_lock_not_acquired(mock_config, mock_lock):
    mock_config.LITELLM_SPEND_COLLECTOR_ENABLED = True
    mock_config.LLM_PROXY_ENABLED = True
    admin = User(id="a1", username="admin", email="admin@example.com", is_admin=True)

    with pytest.raises(HTTPException) as exc_info:
        await trigger_spend_collection(admin=admin)

    assert exc_info.value.status_code == 409
    assert "already in progress" in exc_info.value.detail
    mock_lock.assert_called_once()


@pytest.mark.asyncio
@patch(f"{ADMIN_MODULE}.config")
async def test_trigger_spend_collection_returns_503_when_collector_disabled(mock_config):
    mock_config.LITELLM_SPEND_COLLECTOR_ENABLED = False
    admin = User(id="a1", username="admin", email="admin@example.com", is_admin=True)

    with pytest.raises(HTTPException) as exc_info:
        await trigger_spend_collection(admin=admin)

    assert exc_info.value.status_code == 503
    assert "LITELLM_SPEND_COLLECTOR_ENABLED" in exc_info.value.detail


@pytest.mark.asyncio
@patch(f"{ADMIN_MODULE}.config")
async def test_trigger_spend_collection_returns_503_when_proxy_disabled(mock_config):
    mock_config.LITELLM_SPEND_COLLECTOR_ENABLED = True
    mock_config.LLM_PROXY_ENABLED = False
    admin = User(id="a1", username="admin", email="admin@example.com", is_admin=True)

    with pytest.raises(HTTPException) as exc_info:
        await trigger_spend_collection(admin=admin)

    assert exc_info.value.status_code == 503
    assert "LLM_PROXY_ENABLED" in exc_info.value.detail


@pytest.mark.asyncio
@patch(f"{ADMIN_MODULE}.async_leader_lock", side_effect=_async_lock_cm(acquired=True))
@patch(f"{ADMIN_MODULE}.config")
async def test_trigger_spend_collection_returns_200_on_success(mock_config, mock_lock):
    mock_config.LITELLM_SPEND_COLLECTOR_ENABLED = True
    mock_config.LLM_PROXY_ENABLED = True

    mock_service = AsyncMock()
    mock_service.collect.return_value = 42
    admin = User(id="a1", username="admin", email="admin@example.com", is_admin=True)

    with patch(
        "codemie.service.spend_tracking.spend_collector_service.LiteLLMSpendCollectorService",
        return_value=mock_service,
    ):
        response = await trigger_spend_collection(admin=admin)

    assert response.message == "Spend collection completed"
    assert response.data == {"rows_inserted": 42}
    mock_service.collect.assert_awaited_once()
    mock_lock.assert_called_once()


class TestApplicationsSearchOpenToAuditor:
    """EPMCDME-10930: GET /v1/admin/applications must accept admin, maintainer, and auditor.

    The router previously gated every route in this file behind a single shared
    admin_access_only dependency, which blocked auditors from the project search
    used by the frontend's ProjectSelector/Analytics "Project" filter even though
    auditors should have read-only visibility into all platform projects. Fixed by
    moving auth to per-route dependencies so only this one GET route was loosened.
    """

    def _find_route(self, path: str, method: str):
        for route in router.routes:
            if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
                return route
        raise AssertionError(f"{method} {path} route not found")

    def test_applications_route_uses_auditor_inclusive_dependency(self):
        route = self._find_route("/v1/admin/applications", "GET")
        dependency_functions = [dep.call for dep in route.dependant.dependencies]

        assert admin_or_maintainer_or_auditor_access in dependency_functions
        assert admin_access_only not in dependency_functions

    def test_other_admin_endpoints_still_use_admin_access_only(self):
        still_admin_only = {
            ("/v1/admin/application", "POST"),
            ("/v1/admin/users/{user_id}/conversations/{conversation_id}", "GET"),
            ("/v1/admin/users/{user_id}/conversations/{conversation_id}/feedback", "PUT"),
            ("/v1/speech/config", "GET"),
            ("/v1/admin/marketplace/reindex", "POST"),
            ("/v1/admin/spend-tracking/collect", "POST"),
            ("/v1/admin/llm/reload", "GET"),
            ("/v1/admin/llm/retire", "POST"),
            ("/v1/admin/llm/retire/bulk", "POST"),
        }

        for path, method in still_admin_only:
            route = self._find_route(path, method)
            dependency_functions = [dep.call for dep in route.dependant.dependencies]
            assert admin_access_only in dependency_functions, f"{method} {path} should still use admin_access_only"
