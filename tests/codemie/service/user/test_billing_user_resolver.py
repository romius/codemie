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

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User
from codemie.service.user.billing_user_resolver import billing_user_resolver, get_billing_user


def _caller(user_type="service_account", user_id="codemie-teams-bot"):
    return User(id=user_id, email="bot@svc.example.com", user_type=user_type, project_names=[])


@pytest.mark.asyncio
@patch("codemie.service.user.billing_user_resolver.customer_config")
async def test_get_billing_user_without_header_returns_caller_unchanged(mock_customer_config):
    mock_customer_config.is_feature_enabled.return_value = True
    caller = _caller()
    raw_request = MagicMock()
    raw_request.headers = {}
    assert await get_billing_user(raw_request, caller) is caller


@pytest.mark.asyncio
@patch("codemie.service.user.billing_user_resolver.customer_config")
async def test_get_billing_user_feature_off_returns_caller_unchanged(mock_customer_config):
    mock_customer_config.is_feature_enabled.return_value = False
    caller = _caller()
    raw_request = MagicMock()
    raw_request.headers = {"X-Teams-Sender-Email": "enduser@example.com"}
    assert await get_billing_user(raw_request, caller) is caller


@patch("codemie.service.user.billing_user_resolver.config.TEAMS_SERVICE_ACCOUNT_ID", "codemie-teams-bot")
def test_non_allowlisted_caller_ignores_sender_email():
    caller = _caller(user_id="some-other-user")
    assert billing_user_resolver.resolve(caller, "enduser@example.com") is caller


@patch("codemie.service.user.billing_user_resolver.config.TEAMS_SERVICE_ACCOUNT_ID", "codemie-teams-bot")
@patch("codemie.service.user.billing_user_resolver.get_session")
@patch("codemie.service.user.billing_user_resolver.user_repository")
def test_unmatched_sender_email_raises_clear_error(mock_user_repo, mock_get_session):
    mock_get_session.return_value.__enter__.return_value = MagicMock()
    mock_user_repo.get_by_email.return_value = None
    caller = _caller()
    with pytest.raises(ExtendedHTTPException):
        billing_user_resolver.resolve(caller, "unknown@example.com")


@patch("codemie.service.user.billing_user_resolver.config.TEAMS_SERVICE_ACCOUNT_ID", "codemie-teams-bot")
@patch("codemie.service.user.billing_user_resolver.get_session")
@patch("codemie.service.user.billing_user_resolver.user_repository")
def test_inactive_resolved_user_is_rejected(mock_user_repo, mock_get_session):
    mock_get_session.return_value.__enter__.return_value = MagicMock()
    db_user = MagicMock(
        id="end-user-1",
        email="enduser@example.com",
        user_type="regular",
        is_active=False,
        deleted_at=None,
    )
    mock_user_repo.get_by_email.return_value = db_user
    caller = _caller()
    with pytest.raises(ExtendedHTTPException):
        billing_user_resolver.resolve(caller, "enduser@example.com")


@patch("codemie.service.user.billing_user_resolver.config.TEAMS_SERVICE_ACCOUNT_ID", "codemie-teams-bot")
@patch("codemie.service.user.billing_user_resolver.get_session")
@patch("codemie.service.user.billing_user_resolver.user_repository")
def test_deleted_resolved_user_is_rejected(mock_user_repo, mock_get_session):
    mock_get_session.return_value.__enter__.return_value = MagicMock()
    db_user = MagicMock(
        id="end-user-1",
        email="enduser@example.com",
        user_type="regular",
        is_active=True,
        deleted_at="2026-01-01T00:00:00Z",
    )
    mock_user_repo.get_by_email.return_value = db_user
    caller = _caller()
    with pytest.raises(ExtendedHTTPException):
        billing_user_resolver.resolve(caller, "enduser@example.com")


@patch("codemie.service.user.billing_user_resolver.config.TEAMS_SERVICE_ACCOUNT_ID", "codemie-teams-bot")
@patch("codemie.service.user.billing_user_resolver.get_session")
@patch("codemie.service.user.billing_user_resolver.user_repository")
def test_service_account_resolved_user_is_rejected(mock_user_repo, mock_get_session):
    mock_get_session.return_value.__enter__.return_value = MagicMock()
    db_user = MagicMock(
        id="other-svc",
        email="other-svc@example.com",
        user_type="service_account",
        is_active=True,
        deleted_at=None,
    )
    mock_user_repo.get_by_email.return_value = db_user
    caller = _caller()
    with pytest.raises(ExtendedHTTPException):
        billing_user_resolver.resolve(caller, "other-svc@example.com")


@patch("codemie.service.user.billing_user_resolver.config.TEAMS_SERVICE_ACCOUNT_ID", "codemie-teams-bot")
@patch("codemie.service.user.billing_user_resolver.get_session")
@patch("codemie.service.user.billing_user_resolver.user_repository")
@patch("codemie.service.user.billing_user_resolver.user_project_repository")
@patch("codemie.service.user.billing_user_resolver.user_kb_repository")
def test_matched_sender_email_returns_resolved_user(mock_kb_repo, mock_project_repo, mock_user_repo, mock_get_session):
    mock_get_session.return_value.__enter__.return_value = MagicMock()
    db_user = MagicMock(
        id="end-user-1",
        username="enduser",
        email="enduser@example.com",
        user_type="regular",
        is_admin=False,
        is_maintainer=False,
        is_auditor=False,
        project_limit=None,
        is_active=True,
        deleted_at=None,
    )
    # MagicMock's constructor `name=` kwarg sets the mock's own repr name, not an
    # attribute, so `name`/`picture` (both nullable on UserDB) must be set after the fact.
    db_user.name = None
    db_user.picture = None
    mock_user_repo.get_by_email.return_value = db_user
    mock_project_repo.get_by_user_id.return_value = [MagicMock(project_name="proj-a", is_project_admin=True)]
    mock_kb_repo.get_by_user_id.return_value = []

    caller = _caller()
    resolved = billing_user_resolver.resolve(caller, "enduser@example.com")

    assert resolved.id == "end-user-1"
    assert resolved.project_names == ["proj-a"]
    assert resolved.admin_project_names == ["proj-a"]
