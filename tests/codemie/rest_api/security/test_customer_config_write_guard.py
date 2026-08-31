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

"""Exercises the real write guard.

The router tests replace this dependency with an override, so without these cases the
only authorisation check protecting runtime mutation of publicly served configuration
would never actually run.
"""

from unittest.mock import MagicMock, patch

import pytest

from codemie.configs.config import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.authentication import require_customer_config_write
from codemie.rest_api.security.user import User


@pytest.fixture(autouse=True)
def deployed_environment():
    """ENV=local resolves every user to admin (user.py resolve_is_admin), which would make
    every case here pass for the wrong reason. Pin a deployed-like config instead."""
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        yield


def _request(user: User) -> MagicMock:
    request = MagicMock()
    request.state.user = user
    return request


def _user(**overrides) -> User:
    defaults = {
        "id": "user-1",
        "username": "user",
        "name": "User",
        "email": "user@example.com",
        "project_names": ["project1"],
        "admin_project_names": [],
        "knowledge_bases": [],
        "user_type": "regular",
        "is_admin": False,
    }
    return User(**{**defaults, **overrides})


@pytest.mark.asyncio
async def test_admin_may_write():
    await require_customer_config_write(_request(_user(is_admin=True, user_type="admin")))


@pytest.mark.asyncio
async def test_maintainer_may_write():
    user = _user()
    user.is_maintainer = True

    await require_customer_config_write(_request(user))


@pytest.mark.asyncio
async def test_project_admin_may_not_write():
    """A project admin is elevated inside a project only; this setting is platform-wide."""
    user = _user(admin_project_names=["project1"])

    with pytest.raises(ExtendedHTTPException) as error:
        await require_customer_config_write(_request(user))

    assert error.value.code == 403


@pytest.mark.asyncio
async def test_plain_user_may_not_write():
    with pytest.raises(ExtendedHTTPException) as error:
        await require_customer_config_write(_request(_user()))

    assert error.value.code == 403
