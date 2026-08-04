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

"""Tests for the non-admin skills config endpoint exposing the effective limit (EPMCDME-13541)."""

from unittest.mock import MagicMock

from codemie.rest_api.models.skill import MIN_CONTENT_LENGTH
from codemie.rest_api.security.user import User
import codemie.rest_api.routers.skill as skill_router
from codemie.rest_api.routers.skill import get_skill_config, router


def _mock_user():
    user = MagicMock(spec=User)
    user.id = "user-123"
    return user


class TestSkillConfigEndpoint:
    def test_returns_default_limit(self):
        resp = get_skill_config(user=_mock_user())
        assert resp.max_content_length == 30000
        assert resp.min_content_length == MIN_CONTENT_LENGTH

    def test_reflects_configured_value(self, monkeypatch):
        monkeypatch.setattr(skill_router, "get_skill_max_content_length", lambda: 50000)
        resp = get_skill_config(user=_mock_user())
        assert resp.max_content_length == 50000

    def test_route_registered_as_non_admin_get(self):
        # Assert by endpoint identity so the guard survives router-prefix refactors.
        matches = [r for r in router.routes if getattr(r, "endpoint", None) is get_skill_config]
        assert matches, "GET /skills/config route not registered against get_skill_config"
        assert "GET" in matches[0].methods
