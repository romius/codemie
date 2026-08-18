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

"""Tests for admin_or_maintainer_or_auditor_access dependency (EPMCDME-10930)."""

import pytest
from unittest.mock import patch

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.authentication import (
    admin_access_only,
    admin_or_maintainer_or_auditor_access,
    maintainer_access_only,
)
from codemie.rest_api.security.user import User
from codemie.configs import config


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
async def test_auditor_access_granted_for_admin(mocker):
    request = mocker.MagicMock()
    request.state.user = User(id='1', username='alice', is_admin=True, is_maintainer=False, is_auditor=False)
    result = await admin_or_maintainer_or_auditor_access(request)
    assert result is None


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
async def test_auditor_access_granted_for_maintainer(mocker):
    request = mocker.MagicMock()
    request.state.user = User(id='1', username='alice', is_admin=False, is_maintainer=True, is_auditor=False)
    result = await admin_or_maintainer_or_auditor_access(request)
    assert result is None


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
async def test_auditor_access_granted_for_auditor(mocker):
    request = mocker.MagicMock()
    request.state.user = User(id='1', username='alice', is_admin=False, is_maintainer=False, is_auditor=True)
    result = await admin_or_maintainer_or_auditor_access(request)
    assert result is None


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
async def test_auditor_access_denied_for_regular_user(mocker):
    request = mocker.MagicMock()
    request.state.user = User(id='1', username='alice', is_admin=False, is_maintainer=False, is_auditor=False)
    with pytest.raises(ExtendedHTTPException) as exc_info:
        await admin_or_maintainer_or_auditor_access(request)
    assert exc_info.value.code == 403


# ---------------------------------------------------------------------------
# EPMCDME-10930 spec 5.2: write-rejection (403) for a pure auditor, and the
# PUT /v1/admin/users/{id} is_auditor-assignment authorization matrix.
#
# Every write endpoint touched by this feature (user CRUD, budgets,
# project-budgets, project mutation) is gated by either admin_access_only or
# maintainer_access_only, so testing these two guard functions directly
# against a pure-auditor fixture covers the write-rejection requirement for
# all of them without duplicating a test per route.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
async def test_admin_access_only_denies_pure_auditor(mocker):
    """Pure auditor gets 403 from admin_access_only (gates PUT/POST/DELETE user
    endpoints; also the PUT .../is_auditor assignment path)."""
    request = mocker.MagicMock()
    request.state.user = User(id='1', username='alice', is_admin=False, is_maintainer=False, is_auditor=True)
    with pytest.raises(ExtendedHTTPException) as exc_info:
        await admin_access_only(request)
    assert exc_info.value.code == 403


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
async def test_admin_access_only_allows_admin(mocker):
    """PUT .../is_auditor assignment succeeds for a plain admin caller."""
    request = mocker.MagicMock()
    request.state.user = User(id='1', username='alice', is_admin=True, is_maintainer=False, is_auditor=False)
    result = await admin_access_only(request)
    assert result is None


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
async def test_admin_access_only_allows_maintainer(mocker):
    """PUT .../is_auditor assignment succeeds for a maintainer caller."""
    request = mocker.MagicMock()
    request.state.user = User(id='1', username='alice', is_admin=False, is_maintainer=True, is_auditor=False)
    result = await admin_access_only(request)
    assert result is None


@pytest.mark.anyio
@patch.object(config, 'ENV', 'dev')
@patch.object(config, 'ENABLE_USER_MANAGEMENT', True)
async def test_maintainer_access_only_denies_pure_auditor(mocker):
    """Pure auditor gets 403 from maintainer_access_only (gates budget write routes)."""
    request = mocker.MagicMock()
    request.state.user = User(id='1', username='alice', is_admin=False, is_maintainer=False, is_auditor=True)
    with pytest.raises(ExtendedHTTPException) as exc_info:
        await maintainer_access_only(request)
    assert exc_info.value.code == 403
