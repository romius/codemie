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

from unittest.mock import MagicMock, patch

import pytest
from fastapi import status
from httpx import AsyncClient, ASGITransport

from codemie.rest_api.main import app
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User

user = User(id="u1", username="u1", name="u1")
REQUEST_HEADERS = {"user-id": user.id, "username": user.username, "name": user.name}

_MOCK_RESULT = {
    "data": [],
    "pagination": {"page": 0, "pages": 0, "total": 0, "per_page": 100},
}


@pytest.fixture(autouse=True)
def override_auth():
    app.dependency_overrides[authenticate] = lambda: user
    yield
    app.dependency_overrides = {}


def _patch_sub_workflow_enabled(enabled: bool):
    mock_cc = MagicMock()
    mock_cc.is_feature_enabled.return_value = enabled
    return patch("codemie.rest_api.routers.workflow.customer_config", mock_cc)


@pytest.mark.asyncio
async def test_selectable_returns_403_when_flag_disabled():
    with _patch_sub_workflow_enabled(False):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/workflows/sub-workflow-candidates", headers=REQUEST_HEADERS)
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
@patch("codemie.service.workflow_config.workflow_config_index_service.WorkflowConfigIndexService.run")
async def test_selectable_returns_200_when_flag_enabled(mock_run):
    mock_run.return_value = _MOCK_RESULT
    with _patch_sub_workflow_enabled(True):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/workflows/sub-workflow-candidates", headers=REQUEST_HEADERS)
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
@patch("codemie.service.workflow_config.workflow_config_index_service.WorkflowConfigIndexService.run")
async def test_selectable_passes_correct_params_without_exclude_id(mock_run):
    mock_run.return_value = _MOCK_RESULT
    with _patch_sub_workflow_enabled(True):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            await ac.get("/v1/workflows/sub-workflow-candidates", headers=REQUEST_HEADERS)

    call_kwargs = mock_run.call_args[1]
    assert call_kwargs["user"] == user
    assert call_kwargs["filter_by_user"] is False
    assert call_kwargs["minimal_response"] is True
    assert call_kwargs["extra_modifiers"] == []


@pytest.mark.asyncio
@patch("codemie.service.workflow_config.workflow_config_index_service.WorkflowConfigIndexService.run")
async def test_selectable_passes_exclude_self_modifier_when_exclude_id_given(mock_run):
    from codemie.service.workflow_config.workflow_config_index_service import ExcludeSelfModifier

    mock_run.return_value = _MOCK_RESULT
    with _patch_sub_workflow_enabled(True):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            await ac.get(
                "/v1/workflows/sub-workflow-candidates",
                params={"exclude_id": "wf-self"},
                headers=REQUEST_HEADERS,
            )

    call_kwargs = mock_run.call_args[1]
    extra = call_kwargs["extra_modifiers"]
    assert len(extra) == 1
    assert isinstance(extra[0], ExcludeSelfModifier)
    assert extra[0].workflow_id == "wf-self"


@pytest.mark.asyncio
@patch("codemie.service.workflow_config.workflow_config_index_service.WorkflowConfigIndexService.run")
async def test_selectable_honours_pagination_params(mock_run):
    mock_run.return_value = {
        "data": [],
        "pagination": {"page": 2, "pages": 5, "total": 50, "per_page": 10},
    }
    with _patch_sub_workflow_enabled(True):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            await ac.get(
                "/v1/workflows/sub-workflow-candidates",
                params={"page": 2, "per_page": 10},
                headers=REQUEST_HEADERS,
            )

    call_kwargs = mock_run.call_args[1]
    assert call_kwargs["page"] == 2
    assert call_kwargs["per_page"] == 10
