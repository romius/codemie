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

"""Tests for EnterpriseIdpWrapper.authenticate() extra_attributes passthrough."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Import at module level so codemie.enterprise and its transitive imports are
# resolved once and stay in sys.modules for all tests in this file.
# Doing this inside a patch.dict(sys.modules) block would cause all newly-imported
# modules to be removed on block exit, making every subsequent test re-import and
# re-register the Budget SQLAlchemy table → InvalidRequestError.
from codemie.enterprise.idp.dependencies import _wrap_enterprise_idp


def _make_idp_user(**kwargs) -> SimpleNamespace:
    """Return a SimpleNamespace that looks like IdpUser with standard fields."""
    defaults = {
        "id": "u-001",
        "username": "jdoe",
        "name": "Jane Doe",
        "email": "jdoe@example.com",
        "roles": ["viewer"],
        "project_names": ["proj-a"],
        "admin_project_names": [],
        "knowledge_bases": [],
        "picture": "",
        "user_type": "regular",
        "auth_token": "tok-xyz",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_fake_enterprise():
    """Return a MagicMock that stands in for the codemie_enterprise package."""
    fake = MagicMock()
    fake.idp.utils.AuthenticationError = Exception
    fake.idp.user_type.InvalidUserTypeError = type("InvalidUserTypeError", (Exception,), {"detail": ""})
    return fake


def _build_wrapper(idp_user):
    """Build (wrapper_instance, fake_enterprise) with a provider returning idp_user."""
    mock_provider = MagicMock()
    mock_provider.authenticate.return_value = idp_user
    mock_provider_class = MagicMock(return_value=mock_provider)

    # _wrap_enterprise_idp() only defines the wrapper class — no enterprise imports
    # needed here. The lazy enterprise imports (AuthenticationError, etc.) only run
    # inside authenticate(), which each test patches independently.
    fake = _make_fake_enterprise()
    wrapper_class = _wrap_enterprise_idp(mock_provider_class, "test-provider")
    return wrapper_class(), fake


@pytest.mark.asyncio
async def test_extra_attributes_happy_path():
    """Business claims pass through unchanged when none are JWT metadata keys."""
    idp_user = _make_idp_user(extra_attributes={"department": "eng", "team_id": "backend"})
    wrapper, fake = _build_wrapper(idp_user)

    mock_request = MagicMock()
    mock_request.headers = {}

    patch_modules = {
        "codemie_enterprise": fake,
        "codemie_enterprise.idp": fake.idp,
        "codemie_enterprise.idp.utils": fake.idp.utils,
        "codemie_enterprise.idp.user_type": fake.idp.user_type,
    }
    with patch.dict(sys.modules, patch_modules):
        user = await wrapper.authenticate(mock_request)

    assert user.extra_attributes == {"department": "eng", "team_id": "backend"}


@pytest.mark.asyncio
async def test_extra_attributes_denylist_strips_jwt_metadata():
    """JWT metadata claims (exp, iss, etc.) are removed; business claims survive."""
    idp_user = _make_idp_user(extra_attributes={"department": "eng", "exp": 9999999, "iss": "https://idp.example.com"})
    wrapper, fake = _build_wrapper(idp_user)

    mock_request = MagicMock()
    mock_request.headers = {}

    patch_modules = {
        "codemie_enterprise": fake,
        "codemie_enterprise.idp": fake.idp,
        "codemie_enterprise.idp.utils": fake.idp.utils,
        "codemie_enterprise.idp.user_type": fake.idp.user_type,
    }
    with patch.dict(sys.modules, patch_modules):
        user = await wrapper.authenticate(mock_request)

    assert user.extra_attributes == {"department": "eng"}


@pytest.mark.asyncio
async def test_extra_attributes_empty_dict_collapses_to_none():
    """An empty extra_attributes dict (all claims filtered or none present) becomes None."""
    idp_user = _make_idp_user(extra_attributes={})
    wrapper, fake = _build_wrapper(idp_user)

    mock_request = MagicMock()
    mock_request.headers = {}

    patch_modules = {
        "codemie_enterprise": fake,
        "codemie_enterprise.idp": fake.idp,
        "codemie_enterprise.idp.utils": fake.idp.utils,
        "codemie_enterprise.idp.user_type": fake.idp.user_type,
    }
    with patch.dict(sys.modules, patch_modules):
        user = await wrapper.authenticate(mock_request)

    assert user.extra_attributes is None


@pytest.mark.asyncio
async def test_extra_attributes_none_when_field_absent():
    """If IdpUser lacks extra_attributes (old enterprise package), User.extra_attributes is None."""
    # SimpleNamespace without extra_attributes — getattr fallback must return None
    idp_user = _make_idp_user()  # no extra_attributes kwarg → not in namespace
    wrapper, fake = _build_wrapper(idp_user)

    mock_request = MagicMock()
    mock_request.headers = {}

    patch_modules = {
        "codemie_enterprise": fake,
        "codemie_enterprise.idp": fake.idp,
        "codemie_enterprise.idp.utils": fake.idp.utils,
        "codemie_enterprise.idp.user_type": fake.idp.user_type,
    }
    with patch.dict(sys.modules, patch_modules):
        user = await wrapper.authenticate(mock_request)

    assert user.extra_attributes is None


@pytest.mark.asyncio
async def test_extra_attributes_non_dict_type_drops_to_none():
    """A truthy non-dict extra_attributes (e.g. a list from a buggy IDP) drops to None, not AttributeError."""
    idp_user = _make_idp_user(extra_attributes=["not", "a", "dict"])
    wrapper, fake = _build_wrapper(idp_user)

    mock_request = MagicMock()
    mock_request.headers = {}

    patch_modules = {
        "codemie_enterprise": fake,
        "codemie_enterprise.idp": fake.idp,
        "codemie_enterprise.idp.utils": fake.idp.utils,
        "codemie_enterprise.idp.user_type": fake.idp.user_type,
    }
    with patch.dict(sys.modules, patch_modules):
        user = await wrapper.authenticate(mock_request)

    assert user.extra_attributes is None
