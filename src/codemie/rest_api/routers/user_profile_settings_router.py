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

from fastapi import APIRouter, Depends
from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.user_profile_settings import (
    UserProfileSettingsResponse,
    UserProfileSettingsUpdateRequest,
)
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.user_profile_settings_service import user_profile_settings_service

router = APIRouter(
    tags=["Profile Settings"],
    prefix="/v1/user/profile-settings",
)


def _check_user_access(user_id: str, current_user: User) -> None:
    if current_user.id != user_id and not current_user.is_admin:
        raise ExtendedHTTPException(code=403, message="Access denied")


@router.get("/{user_id}", response_model=UserProfileSettingsResponse, status_code=status.HTTP_200_OK)
def get_profile(user_id: str, current_user: User = Depends(authenticate)):
    """Return user profile settings. Returns 404 if never created."""
    _check_user_access(user_id, current_user)
    profile = user_profile_settings_service.get_profile(user_id)
    return UserProfileSettingsResponse(
        user_id=profile.user_id,
        onboarding=profile.onboarding,
        recent_assistant_ids=profile.recent_assistant_ids,
        last_viewed_release_version=profile.last_viewed_release_version,
    )


@router.put("/{user_id}", response_model=UserProfileSettingsResponse, status_code=status.HTTP_200_OK)
def upsert_profile(
    user_id: str,
    data: UserProfileSettingsUpdateRequest,
    current_user: User = Depends(authenticate),
):
    """Upsert user profile settings. Creates if not exists, updates otherwise."""
    _check_user_access(user_id, current_user)
    profile = user_profile_settings_service.upsert_profile(
        user_id=user_id,
        onboarding=data.onboarding,
        recent_assistant_ids=data.recent_assistant_ids,
        last_viewed_release_version=data.last_viewed_release_version,
    )
    return UserProfileSettingsResponse(
        user_id=profile.user_id,
        onboarding=profile.onboarding,
        recent_assistant_ids=profile.recent_assistant_ids,
        last_viewed_release_version=profile.last_viewed_release_version,
    )
