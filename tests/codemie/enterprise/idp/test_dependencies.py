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

"""Tests for the EnterpriseIdpWrapper adapter (IdpUser → core User mapping).

The codemie_enterprise package is optional and typically not installed in the
test environment, so its modules are stubbed via sys.modules.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from codemie.enterprise.idp.dependencies import _wrap_enterprise_idp


class _StubAuthenticationError(Exception):
    pass


class _StubInvalidUserTypeError(Exception):
    def __init__(self, value="bad", detail="detail", help_text="help"):
        super().__init__(value)
        self.value = value
        self.detail = detail
        self.help_text = help_text


@pytest.fixture(autouse=True)
def stub_enterprise_modules():
    """Stub codemie_enterprise modules imported inside authenticate()."""
    created = []
    for mod_name, attrs in [
        ("codemie_enterprise", {}),
        ("codemie_enterprise.idp", {}),
        ("codemie_enterprise.idp.utils", {"AuthenticationError": _StubAuthenticationError}),
        ("codemie_enterprise.idp.user_type", {"InvalidUserTypeError": _StubInvalidUserTypeError}),
    ]:
        if mod_name not in sys.modules:
            module = types.ModuleType(mod_name)
            for key, value in attrs.items():
                setattr(module, key, value)
            sys.modules[mod_name] = module
            created.append(mod_name)
    yield
    for mod_name in created:
        sys.modules.pop(mod_name, None)


def make_idp_user(**extra) -> SimpleNamespace:
    fields = {
        "id": "ent-user-1",
        "username": "entuser",
        "name": "Enterprise User",
        "email": "ent@example.com",
        "roles": ["dev"],
        "project_names": ["proj-1"],
        "admin_project_names": [],
        "knowledge_bases": [],
        "picture": "",
        "user_type": "regular",
        "auth_token": None,
    }
    fields.update(extra)
    return SimpleNamespace(**fields)


def make_wrapper(idp_user):
    provider_instance = MagicMock()
    provider_instance.authenticate.return_value = idp_user
    provider_class = MagicMock(return_value=provider_instance)
    wrapper_class = _wrap_enterprise_idp(provider_class, "TestProvider")
    return wrapper_class()


def make_request():
    request = MagicMock()
    request.headers = {"x-userinfo": "base64-claims"}
    return request


@pytest.mark.asyncio
async def test_maps_client_access_token_when_field_present():
    idp_user = make_idp_user(client_access_token="bff-client-token")
    wrapper = make_wrapper(idp_user)

    user = await wrapper.authenticate(make_request())

    assert user.client_access_token == "bff-client-token"
    assert user.auth_token is None


@pytest.mark.asyncio
async def test_client_access_token_none_when_field_absent():
    """Older codemie_enterprise releases without the field yield None (getattr fallback)."""
    idp_user = make_idp_user()
    assert not hasattr(idp_user, "client_access_token")
    wrapper = make_wrapper(idp_user)

    user = await wrapper.authenticate(make_request())

    assert user.client_access_token is None


@pytest.mark.asyncio
async def test_maps_identity_fields():
    idp_user = make_idp_user(auth_token="user-token")
    wrapper = make_wrapper(idp_user)

    user = await wrapper.authenticate(make_request())

    assert user.id == "ent-user-1"
    assert user.username == "entuser"
    assert user.email == "ent@example.com"
    assert user.auth_token == "user-token"
