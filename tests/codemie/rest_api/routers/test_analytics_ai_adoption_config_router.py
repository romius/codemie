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

"""Tests for the AI Adoption Config persistence routes on the analytics router.

Test coverage for GET/PUT/DELETE /v1/analytics/ai-adoption-config in
src/codemie/rest_api/routers/analytics.py.
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI, status
from httpx import AsyncClient, ASGITransport

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.routers import analytics as analytics_router
from codemie.rest_api.security.authentication import admin_access_only
from codemie.rest_api.security.user import User

app = FastAPI()
app.include_router(analytics_router.router)


@app.exception_handler(ExtendedHTTPException)
async def extended_http_exception_handler(request, exc: ExtendedHTTPException):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=exc.code,
        content={"code": exc.code, "message": exc.message, "details": exc.details},
    )


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mock_admin_user():
    return User(
        id="admin-123",
        username="admin",
        name="Admin User",
        email="admin@example.com",
        project_names=["project1"],
        is_admin=True,
        is_maintainer=False,
        admin_project_names=[],
        knowledge_bases=[],
    )


@pytest.fixture
def mock_regular_user():
    return User(
        id="user-456",
        username="regular",
        name="Regular User",
        email="regular@example.com",
        project_names=["project1"],
        is_admin=False,
        is_maintainer=False,
        admin_project_names=[],
        knowledge_bases=[],
    )


@pytest.fixture
def override_admin_auth(mock_admin_user):
    app.dependency_overrides[analytics_router.authenticate] = lambda: mock_admin_user
    app.dependency_overrides[admin_access_only] = lambda: None
    yield
    app.dependency_overrides = {}


@pytest.fixture
def override_regular_auth_admin_denied(mock_regular_user):
    def deny_admin():
        raise ExtendedHTTPException(code=status.HTTP_403_FORBIDDEN, message="Access denied")

    app.dependency_overrides[analytics_router.authenticate] = lambda: mock_regular_user
    app.dependency_overrides[admin_access_only] = deny_admin
    yield
    app.dependency_overrides = {}


@pytest.mark.anyio
@patch("codemie.service.analytics.analytics_service.AnalyticsService.save_ai_adoption_config")
async def test_put_ai_adoption_config_returns_200_for_admin(mock_save, override_admin_auth):
    mock_save.return_value = {"data": {}, "metadata": {}}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.put(
            "/v1/analytics/ai-adoption-config",
            json={"ai_maturity": {"activation_threshold": {"value": 20, "description": "x"}}},
        )

    assert response.status_code == status.HTTP_200_OK


@pytest.mark.anyio
async def test_put_ai_adoption_config_returns_403_for_non_admin(override_regular_auth_admin_denied):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.put(
            "/v1/analytics/ai-adoption-config",
            json={"ai_maturity": {"activation_threshold": {"value": 20, "description": "x"}}},
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.anyio
async def test_put_ai_adoption_config_returns_400_for_out_of_bounds_value(override_admin_auth):
    """maturity_activation_threshold has ge=1, le=1000 — a value outside that range must be rejected."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.put(
            "/v1/analytics/ai-adoption-config",
            json={"ai_maturity": {"activation_threshold": {"value": 99999, "description": "x"}}},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.anyio
async def test_put_ai_adoption_config_returns_400_for_malformed_body(override_admin_auth):
    """A non-dict value at a section key must be rejected as 400, not surface as a 500."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.put(
            "/v1/analytics/ai-adoption-config",
            json={"ai_maturity": 5},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.anyio
@patch("codemie.service.analytics.analytics_service.AnalyticsService.reset_ai_adoption_config")
async def test_delete_ai_adoption_config_returns_200_for_admin(mock_reset, override_admin_auth):
    mock_reset.return_value = {"data": {}, "metadata": {}}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.delete("/v1/analytics/ai-adoption-config")

    assert response.status_code == status.HTTP_200_OK


@pytest.mark.anyio
async def test_delete_ai_adoption_config_returns_403_for_non_admin(override_regular_auth_admin_denied):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        response = await ac.delete("/v1/analytics/ai-adoption-config")

    assert response.status_code == status.HTTP_403_FORBIDDEN
