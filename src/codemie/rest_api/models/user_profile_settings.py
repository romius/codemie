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

from pydantic import BaseModel, Field
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import SQLModel, Field as SQLField

from codemie.rest_api.models.base import PydanticType


class OnboardingData(BaseModel):
    completed: bool = False
    completed_flows: list[str] = Field(default_factory=list)
    visited_pages: list[str] = Field(default_factory=list)


class UserProfileSettings(SQLModel, table=True):
    """Stores per-user profile state: onboarding, recent assistants, release version."""

    __tablename__ = "user_profile_settings"

    user_id: str = SQLField(primary_key=True)
    onboarding: OnboardingData = SQLField(
        default_factory=OnboardingData,
        sa_column=Column(
            PydanticType(OnboardingData),
            nullable=False,
            server_default='{"completed":false,"completed_flows":[],"visited_pages":[]}',
        ),
    )
    recent_assistant_ids: list[str] = SQLField(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default="[]"),
    )
    last_viewed_release_version: str | None = SQLField(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )


class UserProfileSettingsResponse(BaseModel):
    user_id: str
    onboarding: OnboardingData = Field(default_factory=OnboardingData)
    recent_assistant_ids: list[str] = Field(default_factory=list)
    last_viewed_release_version: str | None = None


class UserProfileSettingsUpdateRequest(BaseModel):
    onboarding: OnboardingData | None = None
    recent_assistant_ids: list[str] | None = None
    last_viewed_release_version: str | None = None
