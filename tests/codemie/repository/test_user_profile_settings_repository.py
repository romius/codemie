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

from unittest.mock import MagicMock

from codemie.repository.user_profile_settings_repository import UserProfileSettingsRepository
from codemie.rest_api.models.user_profile_settings import OnboardingData, UserProfileSettings


def _make_profile(user_id: str = "user-123") -> UserProfileSettings:
    return UserProfileSettings(
        user_id=user_id,
        onboarding=OnboardingData(),
        recent_assistant_ids=[],
        last_viewed_release_version=None,
    )


def test_get_by_user_id_returns_none_when_missing():
    session = MagicMock()
    session.get.return_value = None
    result = UserProfileSettingsRepository.get_by_user_id(session, "user-123")
    assert result is None
    session.get.assert_called_once_with(UserProfileSettings, "user-123")


def test_get_by_user_id_returns_profile_when_present():
    session = MagicMock()
    profile = _make_profile()
    session.get.return_value = profile
    result = UserProfileSettingsRepository.get_by_user_id(session, "user-123")
    assert result is profile


def test_upsert_creates_new_profile_when_not_exists():
    session = MagicMock()
    session.get.return_value = None

    def refresh_side_effect(obj):
        pass

    session.refresh.side_effect = refresh_side_effect
    UserProfileSettingsRepository.upsert(session, "user-123", recent_assistant_ids=["a1"])
    session.add.assert_called_once()
    session.commit.assert_called_once()
    added = session.add.call_args[0][0]
    assert added.user_id == "user-123"
    assert added.recent_assistant_ids == ["a1"]


def test_upsert_updates_existing_profile():
    session = MagicMock()
    existing = _make_profile()
    existing.recent_assistant_ids = []
    session.get.return_value = existing
    UserProfileSettingsRepository.upsert(session, "user-123", recent_assistant_ids=["a1", "a2"])
    assert existing.recent_assistant_ids == ["a1", "a2"]
    session.add.assert_called_once_with(existing)
    session.commit.assert_called_once()
