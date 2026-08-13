# Design: OIDC extra_attributes passthrough
**Ticket:** EPMCDME-13175  
**Date:** 2026-08-11  
**Branch:** EPMCDME-13175_oidc-extra-attributes-passthrough

---

## Problem statement

Extra JWT claims sent by integrators (e.g. `department`, `cost_center`, `team_id`) are silently dropped after OIDC parsing and never reach MCP tools, general tools, or workflow context. The codemie-side pipeline has no field to carry unmapped claims past the enterprise adapter bridge.

---

## Goal

Add a `dict[str, Any] | None` pass-through field (`extra_attributes`) to `User` and `UserContext`, forward it through the `EnterpriseIdpWrapper` adapter bridge with JWT metadata stripped, and propagate it automatically to provider tools and workflow context. Business claims present in the JWT are available to tools after the fix.

---

## Non-goals / out of scope

- Changes to the `codemie_enterprise` package (`IdpUser` model and `_build_idp_user()`) — separate repo; treated as a deployment prerequisite.
- MCP wire protocol propagation to remote MCP-Connect servers — deliberately excluded per EPMCDME-13546; `extra_attributes` is available for local in-process MCP execution only.
- Allowlist or per-deployment configuration of which claims to forward — pass-through all unmapped claims minus JWT metadata.
- Changes to the `UserResponse` API model (`GET /v1/user`) — `extra_attributes` is an internal auth/tool context concept, not a public API field.

---

## Data flow

```
JWT (raw)
  │  ← codemie_enterprise: _build_idp_user() populates IdpUser.extra_attributes
  ▼
IdpUser.extra_attributes
  │
  ▼  EnterpriseIdpWrapper.authenticate()  [dependencies.py]
     denylist strips: exp, iss, aud, iat, nbf, jti, sub
     remaining → User.extra_attributes
  │
  ├── WorkflowContextVar  (get_current_user)     ← free
  └── UserContext.from_user()
        ├── provider_tool_factory.py:175          ← free via model_dump(exclude_none=True)
        └── toolkit_service.py:242  MCPExecutionContext  ← local in-process only
```

---

## Files changed

| File | Change |
|---|---|
| `src/codemie/rest_api/security/user.py` | Add `extra_attributes: dict[str, Any] \| None = None` to `User` and `UserContext`; update `from_user()` |
| `src/codemie/enterprise/idp/dependencies.py` | Add `_JWT_METADATA_CLAIMS` constant; extend `User(...)` constructor with filtered `extra_attributes` |
| `tests/codemie/rest_api/security/test_user_context.py` | Update field-count assertion (12 → 13); add `None`-default and passthrough cases |
| `tests/enterprise/idp/test_enterprise_idp_wrapper.py` | New file — happy path, denylist, and empty/None cases for `EnterpriseIdpWrapper` |

No database changes. No API endpoint changes. No migration.

---

## Detailed design

### `User` model (`user.py:28`)

Add one field after the existing block:

```python
from typing import Any

extra_attributes: dict[str, Any] | None = None
```

`None` default (not `{}`) so `model_dump(exclude_none=True)` omits the field on all non-OIDC paths (local dev, user-management DB path). Not marked `Field(exclude=True)` — must serialize and propagate.

### `UserContext` model (`user.py:121`)

Same field added:

```python
extra_attributes: dict[str, Any] | None = None
```

`from_user()` extended:

```python
@classmethod
def from_user(cls, user: "User") -> "UserContext":
    return cls(
        # ... existing 12 fields unchanged ...
        extra_attributes=user.extra_attributes,
    )
```

### Adapter bridge filter (`dependencies.py`)

Module-level constant (above `_wrap_enterprise_idp`):

```python
_JWT_METADATA_CLAIMS = frozenset({"exp", "iss", "aud", "iat", "nbf", "jti", "sub"})
```

Extended `User(...)` constructor inside `EnterpriseIdpWrapper.authenticate()`:

```python
# getattr guards against old enterprise package versions that lack the field
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

Empty dict after filtering collapses to `None` via `or None`. `getattr` with fallback `None` makes the codemie-side safe to deploy before the enterprise package adds the field.

---

## Testing

### Update: `test_user_context.py`

- Change field-count assertion from 12 to 13.
- Add: `extra_attributes=None` (default) is absent from `model_dump(exclude_none=True)`.
- Add: `extra_attributes={"department": "eng"}` is present and copied by `from_user()`.

### New: `test_enterprise_idp_wrapper.py`

Mock `IdpUser` with `unittest.mock.MagicMock` or a simple dataclass stub — no enterprise package at test time.

| Case | Input `idp_user.extra_attributes` | Expected `user.extra_attributes` |
|---|---|---|
| Happy path | `{"department": "eng", "team_id": "backend"}` | `{"department": "eng", "team_id": "backend"}` |
| Denylist strips metadata | `{"department": "eng", "exp": 9999, "iss": "https://idp"}` | `{"department": "eng"}` |
| Empty dict → None | `{}` | `None` |
| None → None | `None` | `None` |

---

## Constraints

- `dict[str, Any]` (not `dict[str, str]`) — JWT claim values can be strings, numbers, or arrays.
- `None` default, not `{}` — preserves `exclude_none` semantics at all serialization call sites.
- `_JWT_METADATA_CLAIMS` at module level — not inside the closure — to remain independently importable and testable.
- `HAS_IDP` guard: no additional guard needed; `EnterpriseIdpWrapper` only instantiates when `HAS_IDP` is true.

---

## Deployment note

The codemie-side change compiles and is safe to deploy before the enterprise package is updated — `extra_attributes` will be `None` at runtime until `IdpUser.extra_attributes` exists in `codemie_enterprise`. Deploy the enterprise package first, then this change, for the feature to be live end-to-end.

---

## Source references

- Drop point 2: `src/codemie/enterprise/idp/dependencies.py:80–92`
- Drop point 3: `src/codemie/rest_api/security/user.py:142–156`
- MCP wire exclusion: `src/codemie/service/mcp/models.py` (`to_request_fields`, EPMCDME-13546)
- Tech analysis: `docs/superpowers/tasks/2026-08-11-epmcdme-13175-oidc-extra-attributes-passthrough/technical-analysis.md`
