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

"""
Unit tests for UserContext model
"""

from codemie.rest_api.security.user import User, UserContext


class TestUserContext:
    """Test cases for UserContext model"""

    def test_from_user_maps_all_fields(self):
        """from_user() maps all 13 non-sensitive fields from a fully-populated User."""
        user = User(
            id="u-001",
            username="jdoe",
            name="Jane Doe",
            email="jdoe@example.com",
            roles=["admin", "viewer"],
            is_admin=False,  # may be overridden by model_validator; compare against user.is_admin
            is_maintainer=False,
            user_type="internal",
            project_names=["proj-a", "proj-b"],
            admin_project_names=["proj-a"],
            knowledge_bases=["kb-1", "kb-2"],
            picture="https://example.com/avatar.png",
            auth_token="secret-token",
            tenant_id="tenant-1",
            project_limit=10,
        )
        ctx = UserContext.from_user(user)

        assert ctx.id == user.id
        assert ctx.username == user.username
        assert ctx.name == user.name
        assert ctx.email == user.email
        assert ctx.roles == user.roles
        assert ctx.is_admin == user.is_admin  # robust across ENV=local override
        assert ctx.is_maintainer == user.is_maintainer
        assert ctx.user_type == user.user_type
        assert ctx.project_names == user.project_names
        assert ctx.admin_project_names == user.admin_project_names
        assert ctx.knowledge_bases == user.knowledge_bases
        assert ctx.picture == user.picture
        assert ctx.extra_attributes == user.extra_attributes

    def test_extra_attributes_absent_from_dump_when_none(self):
        """extra_attributes=None must not appear in model_dump(exclude_none=True)."""
        user = User(id="u-no-extra")
        ctx = UserContext.from_user(user)
        dumped = ctx.model_dump(exclude_none=True)
        assert "extra_attributes" not in dumped

    def test_extra_attributes_propagates_through_from_user(self):
        """extra_attributes dict is copied verbatim by from_user()."""
        attrs = {"department": "eng", "team_id": "backend"}
        user = User(id="u-with-extra", extra_attributes=attrs)
        ctx = UserContext.from_user(user)
        assert ctx.extra_attributes == attrs

    def test_sensitive_fields_excluded_from_model_dump(self):
        """auth_token, tenant_id, project_limit must not appear in model_dump()."""
        user = User(
            id="u-sensitive",
            auth_token="secret-token",
            tenant_id="tenant-1",
            project_limit=5,
        )
        ctx = UserContext.from_user(user)
        dumped = ctx.model_dump()

        assert "auth_token" not in dumped
        assert "tenant_id" not in dumped
        assert "project_limit" not in dumped

    def test_sensitive_token_absent_from_model_dump_json(self):
        """The raw auth_token string must never appear in JSON serialization."""
        user = User(
            id="u-sensitive",
            auth_token="super-secret-token-xyz",
            tenant_id="tenant-abc",
            project_limit=5,
        )
        ctx = UserContext.from_user(user)
        json_str = ctx.model_dump_json()

        assert "super-secret-token-xyz" not in json_str
        assert "tenant-abc" not in json_str

    def test_all_defaults_user_produces_valid_context(self):
        """A User built with only id should produce a valid UserContext with no errors."""
        user = User(id="minimal-user")
        ctx = UserContext.from_user(user)

        assert ctx.id == "minimal-user"
        # All other fields derive from User defaults; none raise errors
        assert isinstance(ctx.username, str)
        assert isinstance(ctx.roles, list)
        assert isinstance(ctx.project_names, list)

    def test_roundtrip_equality(self):
        """UserContext(**ctx.model_dump()) == ctx."""
        user = User(
            id="u-roundtrip",
            username="rtrip",
            name="Round Trip",
            email="rtrip@example.com",
            roles=["viewer"],
            user_type="external",
            project_names=["p1"],
            admin_project_names=[],
            knowledge_bases=["kb-x"],
            picture="https://example.com/pic.jpg",
        )
        ctx = UserContext.from_user(user)
        ctx2 = UserContext(**ctx.model_dump())

        assert ctx2 == ctx
