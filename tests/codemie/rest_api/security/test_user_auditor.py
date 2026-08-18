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

"""Tests for is_auditor on security User / UserContext (EPMCDME-10930)."""

from unittest.mock import patch

from codemie.rest_api.security.user import User, UserContext


# ─── User.is_auditor ────────────────────────────────────────────────────────


def test_user_is_auditor_defaults_to_false():
    user = User(id="u1", username="alice")
    assert user.is_auditor is False


def test_user_is_auditor_set_true():
    user = User(id="u1", username="alice", is_auditor=True)
    assert user.is_auditor is True


def test_auditor_does_not_trigger_is_admin():
    """is_auditor must never cause is_admin to be set to True (no auto-promote)."""
    with patch("codemie.rest_api.security.user.config") as cfg:
        cfg.ENV = "production"
        cfg.ENABLE_USER_MANAGEMENT = True
        cfg.ADMIN_USER_ID = None
        cfg.ADMIN_ROLE_NAME = "admin"
        user = User(id="u1", username="alice", is_auditor=True, is_admin=False, is_maintainer=False)
        assert user.is_admin is False
        assert user.is_auditor is True


def test_maintainer_still_promotes_to_admin():
    """is_maintainer -> is_admin=True behaviour is unchanged."""
    with patch("codemie.rest_api.security.user.config") as cfg:
        cfg.ENV = "production"
        cfg.ENABLE_USER_MANAGEMENT = True
        cfg.ADMIN_USER_ID = None
        cfg.ADMIN_ROLE_NAME = "admin"
        user = User(id="u1", username="alice", is_maintainer=True, is_admin=False)
        assert user.is_admin is True


# ─── UserContext.is_auditor ──────────────────────────────────────────────────


def test_user_context_is_auditor_defaults_to_none():
    ctx = UserContext(id="u1")
    assert ctx.is_auditor is None


def test_user_context_from_user_propagates_is_auditor():
    with patch("codemie.rest_api.security.user.config") as cfg:
        cfg.ENV = "production"
        cfg.ENABLE_USER_MANAGEMENT = True
        cfg.ADMIN_USER_ID = None
        cfg.ADMIN_ROLE_NAME = "admin"
        user = User(id="u1", username="alice", is_auditor=True)
        ctx = UserContext.from_user(user)
        assert ctx.is_auditor is True


def test_user_context_from_user_propagates_false_is_auditor():
    with patch("codemie.rest_api.security.user.config") as cfg:
        cfg.ENV = "production"
        cfg.ENABLE_USER_MANAGEMENT = True
        cfg.ADMIN_USER_ID = None
        cfg.ADMIN_ROLE_NAME = "admin"
        user = User(id="u1", username="alice", is_auditor=False)
        ctx = UserContext.from_user(user)
        assert ctx.is_auditor is False
