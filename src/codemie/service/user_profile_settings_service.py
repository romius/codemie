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

import logging

from codemie.clients.postgres import get_session
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.user_profile_settings_repository import user_profile_settings_repository
from codemie.rest_api.models.user_profile_settings import OnboardingData, UserProfileSettings

logger = logging.getLogger(__name__)


class UserProfileSettingsService:
    """Business logic for user profile settings."""

    @staticmethod
    def get_profile(user_id: str) -> UserProfileSettings:
        with get_session() as session:
            profile = user_profile_settings_repository.get_by_user_id(session, user_id)
            if profile is None:
                logger.info(f"Profile settings not found for user {user_id}")
                raise ExtendedHTTPException(code=404, message=f"Profile settings not found for user {user_id}")
            return profile

    @staticmethod
    def upsert_profile(
        user_id: str,
        onboarding: OnboardingData | None,
        recent_assistant_ids: list[str] | None,
        last_viewed_release_version: str | None,
    ) -> UserProfileSettings:
        with get_session() as session:
            return user_profile_settings_repository.upsert(
                session,
                user_id,
                onboarding=onboarding,
                recent_assistant_ids=recent_assistant_ids,
                last_viewed_release_version=last_viewed_release_version,
            )


user_profile_settings_service = UserProfileSettingsService()
