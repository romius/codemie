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

from unittest.mock import MagicMock, patch

import pytest

from codemie.service.activity.activity_models import ActivityDomain, ActivityEntityType, UserManagementEvent
from codemie.service.user.user_management_service import UserManagementService


def _mock_user(user_id: str = "user-1") -> MagicMock:
    user = MagicMock()
    user.id = user_id
    user.is_admin = False
    user.is_active = True
    return user


# ---------------------------------------------------------------------------
# create_local_user
# ---------------------------------------------------------------------------


@patch("codemie.service.user.user_management_service.activity_event_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_create_local_user_emits_user_created_event(mock_repo, mock_activity):
    session = MagicMock()
    mock_repo.exists_by_email.return_value = False
    mock_repo.exists_by_username.return_value = False
    created_user = _mock_user("new-user-id")
    mock_repo.create.return_value = created_user

    with patch("codemie.service.password_service.password_service.hash_password", return_value="hashed"):
        UserManagementService.create_local_user(session, email="a@b.com", username="auser", password="password123")

    mock_activity.insert.assert_called_once()
    call_args = mock_activity.insert.call_args[0]
    event_dto = call_args[0]
    assert event_dto.domain == ActivityDomain.USER_MANAGEMENT
    assert event_dto.event_type == UserManagementEvent.USER_CREATED
    assert event_dto.entity_type == ActivityEntityType.USER
    assert event_dto.entity_id == "new-user-id"


@patch("codemie.service.user.user_management_service.activity_event_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_create_local_user_no_event_on_duplicate_email(mock_repo, mock_activity):
    """No event should be emitted when creation is rejected due to duplicate email."""
    from codemie.core.exceptions import ExtendedHTTPException

    session = MagicMock()
    mock_repo.exists_by_email.return_value = True

    with pytest.raises(ExtendedHTTPException):
        UserManagementService.create_local_user(session, email="dup@b.com", username="auser", password="password123")

    mock_activity.insert.assert_not_called()


@patch("codemie.service.user.user_management_service.activity_event_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_create_local_user_persists_is_auditor_true(mock_repo, mock_activity):
    """EPMCDME-10930 regression: create_local_user must accept and persist is_auditor.

    Previously create_local_user had no is_auditor parameter at all, so a request to
    create an auditor user silently created a non-auditor (is_auditor=False) instead.
    """
    session = MagicMock()
    mock_repo.exists_by_email.return_value = False
    mock_repo.exists_by_username.return_value = False
    mock_repo.create.side_effect = lambda _session, user: user

    with patch("codemie.service.password_service.password_service.hash_password", return_value="hashed"):
        created = UserManagementService.create_local_user(
            session, email="a@b.com", username="auser", password="password123", is_auditor=True
        )

    assert created.is_auditor is True


@patch("codemie.service.user.user_management_service.activity_event_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_create_local_user_defaults_is_auditor_false(mock_repo, mock_activity):
    session = MagicMock()
    mock_repo.exists_by_email.return_value = False
    mock_repo.exists_by_username.return_value = False
    mock_repo.create.side_effect = lambda _session, user: user

    with patch("codemie.service.password_service.password_service.hash_password", return_value="hashed"):
        created = UserManagementService.create_local_user(
            session, email="a@b.com", username="auser", password="password123"
        )

    assert created.is_auditor is False


# ---------------------------------------------------------------------------
# update_user
# ---------------------------------------------------------------------------


@patch("codemie.service.user.user_management_service.activity_event_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_update_user_emits_user_updated_event(mock_repo, mock_activity):
    session = MagicMock()
    updated_user = _mock_user("target-id")
    mock_repo.update.return_value = updated_user

    UserManagementService.update_user(session, user_id="target-id", actor_user_id="admin-id", is_admin=True)

    mock_activity.insert.assert_called_once()
    call_args = mock_activity.insert.call_args[0]
    event_dto = call_args[0]
    assert event_dto.domain == ActivityDomain.USER_MANAGEMENT
    assert event_dto.event_type == UserManagementEvent.USER_UPDATED
    assert event_dto.entity_type == ActivityEntityType.USER
    assert event_dto.entity_id == "target-id"
    assert event_dto.actor_id == "admin-id"


@patch("codemie.service.user.user_management_service.activity_event_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_update_user_no_event_when_user_not_found(mock_repo, mock_activity):
    """No event emitted when repository returns None (user not found)."""
    session = MagicMock()
    mock_repo.update.return_value = None

    result = UserManagementService.update_user(session, user_id="missing-id", actor_user_id="admin-id")

    assert result is None
    mock_activity.insert.assert_not_called()


# ---------------------------------------------------------------------------
# deactivate_user
# ---------------------------------------------------------------------------


@patch("codemie.service.user.user_management_service.activity_event_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_deactivate_user_emits_user_deactivated_event(mock_repo, mock_activity):
    session = MagicMock()
    target_user = _mock_user("target-id")
    target_user.is_admin = False
    deactivated_user = _mock_user("target-id")
    deactivated_user.is_active = False
    mock_repo.get_by_id.side_effect = [target_user, deactivated_user]
    mock_repo.soft_delete.return_value = None

    UserManagementService.deactivate_user(session, user_id="target-id", actor_user_id="admin-id")

    mock_activity.insert.assert_called_once()
    call_args = mock_activity.insert.call_args[0]
    event_dto = call_args[0]
    assert event_dto.domain == ActivityDomain.USER_MANAGEMENT
    assert event_dto.event_type == UserManagementEvent.USER_DEACTIVATED
    assert event_dto.entity_type == ActivityEntityType.USER
    assert event_dto.entity_id == "target-id"
    assert event_dto.actor_id == "admin-id"


@patch("codemie.service.user.user_management_service.activity_event_repository")
@patch("codemie.service.user.user_management_service.user_repository")
def test_deactivate_user_no_event_when_user_not_found(mock_repo, mock_activity):
    """No event emitted when user is not found (raises 404)."""
    from codemie.core.exceptions import ExtendedHTTPException

    session = MagicMock()
    mock_repo.get_by_id.return_value = None

    with pytest.raises(ExtendedHTTPException):
        UserManagementService.deactivate_user(session, user_id="ghost-id", actor_user_id="admin-id")

    mock_activity.insert.assert_not_called()
