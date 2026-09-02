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

from sqlmodel import Session

from codemie.rest_api.models.user_profile_settings import (
    OnboardingData,
    UserProfileSettings,
)


class UserProfileSettingsRepository:
    """Repository for user profile settings data operations."""

    @staticmethod
    def get_by_user_id(session: Session, user_id: str) -> UserProfileSettings | None:
        return session.get(UserProfileSettings, user_id)

    @staticmethod
    def upsert(
        session: Session,
        user_id: str,
        onboarding: OnboardingData | None = None,
        recent_assistant_ids: list[str] | None = None,
        last_viewed_release_version: str | None = None,
    ) -> UserProfileSettings:
        existing = session.get(UserProfileSettings, user_id)
        if existing is None:
            profile = UserProfileSettings(
                user_id=user_id,
                onboarding=onboarding if onboarding is not None else OnboardingData(),
                recent_assistant_ids=recent_assistant_ids if recent_assistant_ids is not None else [],
                last_viewed_release_version=last_viewed_release_version,
            )
            session.add(profile)
            session.commit()
            session.refresh(profile)
            return profile

        if onboarding is not None:
            existing.onboarding = onboarding
        if recent_assistant_ids is not None:
            existing.recent_assistant_ids = recent_assistant_ids
        if last_viewed_release_version is not None:
            existing.last_viewed_release_version = last_viewed_release_version
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing


user_profile_settings_repository = UserProfileSettingsRepository()
