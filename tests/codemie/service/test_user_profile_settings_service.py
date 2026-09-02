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
from codemie.rest_api.models.user_profile_settings import OnboardingData, UserProfileSettings
from codemie.service.user_profile_settings_service import UserProfileSettingsService


def _make_profile(user_id: str = "user-123") -> UserProfileSettings:
    return UserProfileSettings(
        user_id=user_id,
        onboarding=OnboardingData(),
        recent_assistant_ids=[],
        last_viewed_release_version=None,
    )


def test_get_profile_raises_404_when_not_found():
    with (
        patch("codemie.service.user_profile_settings_service.user_profile_settings_repository") as mock_repo,
        patch("codemie.service.user_profile_settings_service.get_session") as mock_session,
    ):
        mock_session.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_session.return_value.__exit__ = MagicMock(return_value=False)
        mock_repo.get_by_user_id.return_value = None
        with pytest.raises(ExtendedHTTPException) as exc_info:
            UserProfileSettingsService.get_profile("user-123")
        assert exc_info.value.code == 404


def test_get_profile_returns_profile_when_found():
    profile = _make_profile()
    with (
        patch("codemie.service.user_profile_settings_service.user_profile_settings_repository") as mock_repo,
        patch("codemie.service.user_profile_settings_service.get_session") as mock_session,
    ):
        mock_session.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_session.return_value.__exit__ = MagicMock(return_value=False)
        mock_repo.get_by_user_id.return_value = profile
        result = UserProfileSettingsService.get_profile("user-123")
    assert result.user_id == "user-123"


def test_upsert_profile_delegates_to_repository():
    profile = _make_profile()
    with (
        patch("codemie.service.user_profile_settings_service.user_profile_settings_repository") as mock_repo,
        patch("codemie.service.user_profile_settings_service.get_session") as mock_session,
    ):
        mock_session.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_session.return_value.__exit__ = MagicMock(return_value=False)
        mock_repo.upsert.return_value = profile
        result = UserProfileSettingsService.upsert_profile(
            "user-123",
            onboarding=None,
            recent_assistant_ids=None,
            last_viewed_release_version=None,
        )
    assert result.user_id == "user-123"
    mock_repo.upsert.assert_called_once()
