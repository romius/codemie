# OIDC extra_attributes Passthrough Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry extra JWT claims (e.g. `department`, `cost_center`, `team_id`) through the codemie authentication pipeline so they are available to provider tools and workflow context.

**Architecture:** Add `extra_attributes: dict[str, Any] | None = None` to `User` and `UserContext`; extend the `EnterpriseIdpWrapper.authenticate()` adapter bridge to extract and denylist-filter `IdpUser.extra_attributes` before constructing `User`. Downstream call sites pick up the field automatically via optional-field semantics and `model_dump(exclude_none=True)`.

**Tech Stack:** Python 3.11+, Pydantic v2, FastAPI, pytest, `unittest.mock`, `pytest-asyncio`

## Global Constraints

- `dict[str, Any] | None = None` (not `dict[str, Any] = {}`) — `None` default preserves `model_dump(exclude_none=True)` semantics on non-OIDC paths.
- `extra_attributes` must NOT use `Field(exclude=True)` — it must serialize and propagate.
- `_JWT_METADATA_CLAIMS` constant is module-level (not inside the closure) for independent testability.
- `getattr(idp_user, "extra_attributes", None)` — guards against enterprise package versions that lack the field.
- Empty dict after filtering collapses to `None` via `or None`.
- No database changes, no API endpoint changes, no migration.
- MCP wire protocol: `extra_attributes` is intentionally excluded from remote MCP-Connect servers (per EPMCDME-13546). In scope for local in-process MCP and provider/DSP HTTP path only.
- Commit prefix: `EPMCDME-13175:`
- Test runner: `poetry run pytest`

---

### Task 1: Extend User and UserContext models

**Files:**
- Modify: `src/codemie/rest_api/security/user.py:15-156`
- Modify: `tests/codemie/rest_api/security/test_user_context.py`

**Interfaces:**
- Produces: `User.extra_attributes: dict[str, Any] | None = None` (line ~51, after `tenant_id`)
- Produces: `UserContext.extra_attributes: dict[str, Any] | None = None` (line ~139, after `picture`)
- Produces: `UserContext.from_user()` forwards `extra_attributes=user.extra_attributes`

---

- [ ] **Step 1: Write failing tests**

Add these three test cases to `tests/codemie/rest_api/security/test_user_context.py` inside `class TestUserContext`. The file already imports `User` and `UserContext`; add no new imports.

Update the existing `test_from_user_maps_all_fields` docstring (line 26) and add the `extra_attributes` assertion inside it. Then add two new test methods:

```python
# In test_from_user_maps_all_fields, change the docstring from:
#   "from_user() maps all 12 non-sensitive fields from a fully-populated User."
# to:
#   "from_user() maps all 13 non-sensitive fields from a fully-populated User."
# And add at the end of that test method:
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
```

- [ ] **Step 2: Run tests to verify they fail**

```
poetry run pytest tests/codemie/rest_api/security/test_user_context.py -v
```

Expected: `test_from_user_maps_all_fields` fails because `ctx` has no `extra_attributes`; `test_extra_attributes_absent_from_dump_when_none` fails because `User` rejects unknown field; `test_extra_attributes_propagates_through_from_user` fails similarly.

- [ ] **Step 3: Add `from typing import Any` import to `user.py`**

At line 15 of `src/codemie/rest_api/security/user.py`, the file currently imports only from `pydantic`. Add `Any` from `typing`:

```python
from typing import Any

from pydantic import BaseModel, Field, computed_field, model_validator
```

- [ ] **Step 4: Add `extra_attributes` field to `User`**

In `src/codemie/rest_api/security/user.py`, add the field after `tenant_id` (line 50) and before the `@model_validator` decorator (line 52):

```python
    auth_token: str | None = Field(None, exclude=True)
    tenant_id: str | None = Field(None, exclude=True)
    extra_attributes: dict[str, Any] | None = None

    @model_validator(mode='after')
```

- [ ] **Step 5: Add `extra_attributes` field to `UserContext`**

In the same file, add the field after `picture` (line 139) in `UserContext`:

```python
    picture: str | None = None
    extra_attributes: dict[str, Any] | None = None

    @classmethod
    def from_user(cls, user: "User") -> "UserContext":
```

- [ ] **Step 6: Update `from_user()` to forward `extra_attributes`**

Extend the `return cls(...)` constructor in `from_user()` with one line:

```python
    @classmethod
    def from_user(cls, user: "User") -> "UserContext":
        return cls(
            id=user.id,
            username=user.username,
            name=user.name,
            email=user.email,
            roles=user.roles,
            is_admin=user.is_admin,
            is_maintainer=user.is_maintainer,
            user_type=user.user_type,
            project_names=user.project_names,
            admin_project_names=user.admin_project_names,
            knowledge_bases=user.knowledge_bases,
            picture=user.picture,
            extra_attributes=user.extra_attributes,
        )
```

- [ ] **Step 7: Run tests to verify they pass**

```
poetry run pytest tests/codemie/rest_api/security/test_user_context.py -v
```

Expected: all 7 tests pass (5 original + 2 new). The updated `test_from_user_maps_all_fields` also passes.

- [ ] **Step 8: Commit**

```bash
git add src/codemie/rest_api/security/user.py \
        tests/codemie/rest_api/security/test_user_context.py
git commit -m "EPMCDME-13175: add extra_attributes passthrough field to User and UserContext"
```

---

### Task 2: Add denylist filter at EnterpriseIdpWrapper

**Files:**
- Modify: `src/codemie/enterprise/idp/dependencies.py:32-134`
- Create: `tests/enterprise/idp/test_enterprise_idp_wrapper.py`

**Interfaces:**
- Consumes: `User.extra_attributes: dict[str, Any] | None = None` (from Task 1)
- Produces: `_JWT_METADATA_CLAIMS = frozenset({"exp", "iss", "aud", "iat", "nbf", "jti", "sub"})` at module level in `dependencies.py`
- Produces: `EnterpriseIdpWrapper.authenticate()` populates `User.extra_attributes` with JWT-metadata-filtered claims from `idp_user.extra_attributes`

---

- [ ] **Step 1: Create the test file with four failing tests**

Create `tests/enterprise/idp/test_enterprise_idp_wrapper.py` with the following content. The file mocks all `codemie_enterprise` imports so the enterprise package is not required at test time.

```python
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


def _make_wrapper(idp_user):
    """Build EnterpriseIdpWrapper with a provider that returns idp_user."""
    mock_provider = MagicMock()
    mock_provider.authenticate.return_value = idp_user
    mock_provider.get_session_cookie.return_value = "session"

    MockProviderClass = MagicMock(return_value=mock_provider)

    # Patch enterprise imports that are lazily resolved inside authenticate()
    fake_enterprise = MagicMock()
    fake_enterprise.idp.utils.AuthenticationError = Exception
    fake_enterprise.idp.user_type.InvalidUserTypeError = type(
        "InvalidUserTypeError", (Exception,), {"detail": ""}
    )

    with patch.dict(
        sys.modules,
        {
            "codemie_enterprise": fake_enterprise,
            "codemie_enterprise.idp": fake_enterprise.idp,
            "codemie_enterprise.idp.utils": fake_enterprise.idp.utils,
            "codemie_enterprise.idp.user_type": fake_enterprise.idp.user_type,
        },
    ):
        from codemie.enterprise.idp.dependencies import _wrap_enterprise_idp

        WrapperClass = _wrap_enterprise_idp(MockProviderClass, "test-provider")
        return WrapperClass(), fake_enterprise


@pytest.mark.asyncio
async def test_extra_attributes_happy_path():
    """Business claims pass through unchanged when they contain no JWT metadata keys."""
    idp_user = _make_idp_user(extra_attributes={"department": "eng", "team_id": "backend"})
    mock_request = MagicMock()
    mock_request.headers = {}

    wrapper, fake_enterprise = _make_wrapper(idp_user)

    with patch.dict(
        sys.modules,
        {
            "codemie_enterprise": fake_enterprise,
            "codemie_enterprise.idp": fake_enterprise.idp,
            "codemie_enterprise.idp.utils": fake_enterprise.idp.utils,
            "codemie_enterprise.idp.user_type": fake_enterprise.idp.user_type,
        },
    ):
        user = await wrapper.authenticate(mock_request)

    assert user.extra_attributes == {"department": "eng", "team_id": "backend"}


@pytest.mark.asyncio
async def test_extra_attributes_denylist_strips_jwt_metadata():
    """JWT metadata claims (exp, iss, etc.) are removed; business claims survive."""
    idp_user = _make_idp_user(
        extra_attributes={"department": "eng", "exp": 9999999, "iss": "https://idp.example.com"}
    )
    mock_request = MagicMock()
    mock_request.headers = {}

    wrapper, fake_enterprise = _make_wrapper(idp_user)

    with patch.dict(
        sys.modules,
        {
            "codemie_enterprise": fake_enterprise,
            "codemie_enterprise.idp": fake_enterprise.idp,
            "codemie_enterprise.idp.utils": fake_enterprise.idp.utils,
            "codemie_enterprise.idp.user_type": fake_enterprise.idp.user_type,
        },
    ):
        user = await wrapper.authenticate(mock_request)

    assert user.extra_attributes == {"department": "eng"}


@pytest.mark.asyncio
async def test_extra_attributes_empty_dict_collapses_to_none():
    """An empty extra_attributes dict (all claims filtered or none present) becomes None."""
    idp_user = _make_idp_user(extra_attributes={})
    mock_request = MagicMock()
    mock_request.headers = {}

    wrapper, fake_enterprise = _make_wrapper(idp_user)

    with patch.dict(
        sys.modules,
        {
            "codemie_enterprise": fake_enterprise,
            "codemie_enterprise.idp": fake_enterprise.idp,
            "codemie_enterprise.idp.utils": fake_enterprise.idp.utils,
            "codemie_enterprise.idp.user_type": fake_enterprise.idp.user_type,
        },
    ):
        user = await wrapper.authenticate(mock_request)

    assert user.extra_attributes is None


@pytest.mark.asyncio
async def test_extra_attributes_none_when_field_absent():
    """If IdpUser lacks extra_attributes (old enterprise package), User.extra_attributes is None."""
    # SimpleNamespace without extra_attributes — getattr fallback must return None
    idp_user = _make_idp_user()  # no extra_attributes kwarg → not in namespace
    mock_request = MagicMock()
    mock_request.headers = {}

    wrapper, fake_enterprise = _make_wrapper(idp_user)

    with patch.dict(
        sys.modules,
        {
            "codemie_enterprise": fake_enterprise,
            "codemie_enterprise.idp": fake_enterprise.idp,
            "codemie_enterprise.idp.utils": fake_enterprise.idp.utils,
            "codemie_enterprise.idp.user_type": fake_enterprise.idp.user_type,
        },
    ):
        user = await wrapper.authenticate(mock_request)

    assert user.extra_attributes is None
```

- [ ] **Step 2: Create the `tests/enterprise/idp/` directory and `__init__.py`**

```bash
mkdir -p tests/enterprise/idp
touch tests/enterprise/idp/__init__.py
```

- [ ] **Step 3: Run tests to verify they fail**

```
poetry run pytest tests/enterprise/idp/test_enterprise_idp_wrapper.py -v
```

Expected: all 4 tests fail with `TypeError: User() got an unexpected keyword argument 'extra_attributes'` (because `User` already has the field from Task 1) or because `_JWT_METADATA_CLAIMS` does not exist yet / `extra_attributes` is not forwarded in the constructor.

At this point Task 1 must already be committed. The actual failure message will be `AssertionError` because `user.extra_attributes` is `None` in all cases — the `User(...)` constructor in `authenticate()` does not yet pass `extra_attributes`.

- [ ] **Step 4: Add `_JWT_METADATA_CLAIMS` constant to `dependencies.py`**

In `src/codemie/enterprise/idp/dependencies.py`, add the constant after the module docstring and existing imports, immediately above the `is_enterprise_idp_available` function (line 27):

```python
_JWT_METADATA_CLAIMS = frozenset({"exp", "iss", "aud", "iat", "nbf", "jti", "sub"})


def is_enterprise_idp_available() -> bool:
```

- [ ] **Step 5: Extend `User(...)` constructor with filtered `extra_attributes`**

In `src/codemie/enterprise/idp/dependencies.py`, inside `EnterpriseIdpWrapper.authenticate()`, add `_raw_extra` extraction before the `return User(...)` call and pass `extra_attributes` as the last argument:

Replace:
```python
                # Map enterprise IdpUser → codemie User
                return User(
                    id=idp_user.id,
                    username=idp_user.username,
                    name=idp_user.name,
                    email=idp_user.email,
                    roles=list(idp_user.roles),
                    project_names=list(idp_user.project_names),
                    admin_project_names=list(idp_user.admin_project_names),
                    knowledge_bases=list(idp_user.knowledge_bases),
                    picture=idp_user.picture,
                    user_type=idp_user.user_type,
                    auth_token=idp_user.auth_token,
                )
```

With:
```python
                # Map enterprise IdpUser → codemie User
                _raw_extra = getattr(idp_user, "extra_attributes", None)
                return User(
                    id=idp_user.id,
                    username=idp_user.username,
                    name=idp_user.name,
                    email=idp_user.email,
                    roles=list(idp_user.roles),
                    project_names=list(idp_user.project_names),
                    admin_project_names=list(idp_user.admin_project_names),
                    knowledge_bases=list(idp_user.knowledge_bases),
                    picture=idp_user.picture,
                    user_type=idp_user.user_type,
                    auth_token=idp_user.auth_token,
                    extra_attributes={
                        k: v for k, v in _raw_extra.items()
                        if k not in _JWT_METADATA_CLAIMS
                    } or None if _raw_extra else None,
                )
```

- [ ] **Step 6: Run tests to verify they pass**

```
poetry run pytest tests/enterprise/idp/test_enterprise_idp_wrapper.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 7: Run full targeted test suite**

```
poetry run pytest tests/codemie/rest_api/security/test_user_context.py tests/enterprise/idp/test_enterprise_idp_wrapper.py -v
```

Expected: all 11 tests pass (7 from Task 1 + 4 new).

- [ ] **Step 8: Commit**

```bash
git add src/codemie/enterprise/idp/dependencies.py \
        tests/enterprise/idp/__init__.py \
        tests/enterprise/idp/test_enterprise_idp_wrapper.py
git commit -m "EPMCDME-13175: filter and forward extra_attributes in EnterpriseIdpWrapper adapter bridge"
```
