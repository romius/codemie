# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.user_profile_settings import (
    OnboardingData,
    UserProfileSettings,
    UserProfileSettingsUpdateRequest,
)
from codemie.rest_api.security.user import User


@pytest.fixture
def user() -> User:
    return User(
        id="user-123",
        username="testuser",
        email="testuser@example.com",
        name="Test User",
        is_admin=False,
        is_maintainer=False,
        project_names=[],
        admin_project_names=[],
        knowledge_bases=[],
    )


@pytest.fixture
def other_user():
    mock = MagicMock(spec=User)
    mock.id = "other-999"
    mock.is_admin = False
    return mock


def _make_profile(user_id: str = "user-123") -> UserProfileSettings:
    return UserProfileSettings(
        user_id=user_id,
        onboarding=OnboardingData(completed=True, completed_flows=["nav"], visited_pages=["chat"]),
        recent_assistant_ids=["a1", "a2"],
        last_viewed_release_version="1.2.3",
    )


def test_get_profile_returns_404_for_unknown_user(user):
    from codemie.rest_api.routers.user_profile_settings_router import get_profile

    with patch("codemie.rest_api.routers.user_profile_settings_router.user_profile_settings_service") as mock_service:
        mock_service.get_profile.side_effect = ExtendedHTTPException(
            code=404, message="Profile not found for user user-123"
        )
        with pytest.raises(ExtendedHTTPException) as exc_info:
            get_profile("user-123", current_user=user)
        assert exc_info.value.code == 404


def test_get_profile_returns_200(user):
    from codemie.rest_api.routers.user_profile_settings_router import get_profile

    with patch("codemie.rest_api.routers.user_profile_settings_router.user_profile_settings_service") as mock_service:
        mock_service.get_profile.return_value = _make_profile()
        result = get_profile("user-123", current_user=user)
    assert result.user_id == "user-123"
    assert result.onboarding.completed is True
    assert result.recent_assistant_ids == ["a1", "a2"]
    assert result.last_viewed_release_version == "1.2.3"


def test_get_profile_access_denied_for_other_user(other_user):
    from codemie.rest_api.routers.user_profile_settings_router import get_profile

    with pytest.raises(ExtendedHTTPException) as exc_info:
        get_profile("user-123", current_user=other_user)
    assert exc_info.value.code == 403


def test_upsert_profile_access_denied_for_other_user(other_user):
    from codemie.rest_api.routers.user_profile_settings_router import upsert_profile

    with pytest.raises(ExtendedHTTPException) as exc_info:
        upsert_profile(
            "user-123",
            UserProfileSettingsUpdateRequest(),
            current_user=other_user,
        )
    assert exc_info.value.code == 403


def test_admin_can_access_other_users_profile():
    from codemie.rest_api.routers.user_profile_settings_router import get_profile

    admin = MagicMock(spec=User)
    admin.id = "admin-1"
    admin.is_admin = True
    with patch("codemie.rest_api.routers.user_profile_settings_router.user_profile_settings_service") as mock_service:
        mock_service.get_profile.return_value = _make_profile()
        result = get_profile("user-123", current_user=admin)
    assert result.user_id == "user-123"
