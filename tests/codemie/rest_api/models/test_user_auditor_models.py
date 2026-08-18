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

"""Tests for is_auditor field on user data models (EPMCDME-10930)."""

from codemie.rest_api.models.user_management import (
    UserDB,
    UserCreateRequest,
    UserUpdateRequest,
    CodeMieUserDetail,
    AdminUserListItem,
    UserListFilters,
)
from codemie.core.models import UserResponse


# UserDB


def test_user_db_is_auditor_defaults_to_false():
    user = UserDB(id="u1", username="alice", email="alice@example.com")
    assert user.is_auditor is False


def test_user_db_is_auditor_accepts_true():
    user = UserDB(id="u1", username="alice", email="alice@example.com", is_auditor=True)
    assert user.is_auditor is True


# UserCreateRequest


def test_user_create_request_is_auditor_defaults_to_false():
    req = UserCreateRequest(
        email="alice@example.com",
        username="alice",
        password="Passw0rd!",
    )
    assert req.is_auditor is False


def test_user_create_request_is_auditor_accepts_true():
    req = UserCreateRequest(
        email="alice@example.com",
        username="alice",
        password="Passw0rd!",
        is_auditor=True,
    )
    assert req.is_auditor is True


# UserUpdateRequest


def test_user_update_request_is_auditor_defaults_to_none():
    req = UserUpdateRequest()
    assert req.is_auditor is None


def test_user_update_request_is_auditor_accepts_false():
    req = UserUpdateRequest(is_auditor=False)
    assert req.is_auditor is False


# CodeMieUserDetail


def test_codemie_user_detail_is_auditor_defaults_to_false():
    detail = CodeMieUserDetail(
        id="u1",
        username="alice",
        email="alice@example.com",
        name=None,
        picture=None,
        user_type="regular",
        is_active=True,
        is_admin=False,
        auth_source="local",
        email_verified=True,
        last_login_at=None,
        date=None,
        update_date=None,
        deleted_at=None,
    )
    assert detail.is_auditor is False


# AdminUserListItem


def test_admin_user_list_item_is_auditor_defaults_to_false():
    item = AdminUserListItem(
        id="u1",
        username="alice",
        email="alice@example.com",
        name=None,
        user_type="regular",
        is_active=True,
        is_admin=False,
        auth_source="local",
        last_login_at=None,
        date=None,
    )
    assert item.is_auditor is False


# UserListFilters


def test_user_list_filters_is_auditor_defaults_to_none():
    f = UserListFilters()
    assert f.is_auditor is None


def test_user_list_filters_is_auditor_accepts_true():
    f = UserListFilters(is_auditor=True)
    assert f.is_auditor is True


# UserResponse (core/models.py)


def test_user_response_is_auditor_defaults_to_false():
    resp = UserResponse(user_id="u1", name="Alice", username="alice")
    assert resp.is_auditor is False
