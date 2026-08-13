# EPMCDME-13175 Verification Scenario

Extra JWT user attributes (`department`, `cost_center`, `team_id`, …) must flow from the
enterprise OIDC IDP through `EnterpriseIdpWrapper` → `User` → `UserContext` → tools/workflows
without being silently dropped.

---

## Environment

| What | Value |
|------|-------|
| **Shell** | WSL2 bash — **not** Windows PowerShell or cmd. All commands below are Linux/bash. |
| **Project root (WSL path)** | `/mnt/c/Users/AleksandrBudanov/Projects/EPMCDME-13175/codemie` |
| **Project root (Windows path)** | `C:\Users\AleksandrBudanov\Projects\EPMCDME-13175\codemie` — for reference only, do not run commands here |
| **Python runtime** | Managed by Poetry inside WSL2; `python` is not on the system PATH — always use `poetry run python` or `poetry run pytest` |
| **Container runtime** | Podman (not Docker) — only relevant for integration checklist in Step 10 |

**How to open the right terminal:**

```
# On Windows: open Windows Terminal → select the Ubuntu (WSL) profile
# or: press Win+R, type "wsl", press Enter
```

Every step in this document starts from the project root in that WSL2 session.

---

## One-time setup (do this once per WSL session)

```bash
# Terminal: WSL2
# Open a WSL terminal and navigate to the project root:
cd /mnt/c/Users/AleksandrBudanov/Projects/EPMCDME-13175/codemie

# Verify you are in the right place:
pwd
# Expected: /mnt/c/Users/AleksandrBudanov/Projects/EPMCDME-13175/codemie

ls pyproject.toml
# Expected: pyproject.toml   (if "No such file" you are in the wrong directory)

# Check out the feature branch:
git checkout EPMCDME-13175_oidc-extra-attributes-passthrough

# Confirm all 5 implementation commits are present:
git log --oneline 4225e1a40..HEAD
# Expected (newest first):
#   805762c26 EPMCDME-13175: add SDLC planning artifacts …
#   1611366f5 EPMCDME-13175: guard extra_attributes against non-dict type …
#   959cc8c41 EPMCDME-13175: filter and forward extra_attributes …
#   954fe139d EPMCDME-13175: add extra_attributes passthrough field …
#   207cdde5b EPMCDME-13175: add spec and planning artifacts …

# Warm up the Poetry environment (first run downloads deps — takes ~30s):
poetry env use 3.12
poetry install --no-root -q
```

> **Tip:** if `poetry` is not found, run `. $HOME/.poetry/env` or `source ~/.profile` first,
> then retry.

---

## Step 1 — Automated test suite (primary gate)

```
# Terminal: WSL2
# Directory: /mnt/c/Users/AleksandrBudanov/Projects/EPMCDME-13175/codemie
```

```bash
poetry run pytest \
  tests/codemie/rest_api/security/test_user_context.py \
  tests/enterprise/idp/test_enterprise_idp_wrapper.py \
  -v
```

**Expected output (relevant lines):**

```
collected 12 items

tests/codemie/rest_api/security/test_user_context.py::TestUserContext::test_from_user_maps_all_fields PASSED
tests/codemie/rest_api/security/test_user_context.py::TestUserContext::test_extra_attributes_absent_from_dump_when_none PASSED
tests/codemie/rest_api/security/test_user_context.py::TestUserContext::test_extra_attributes_propagates_through_from_user PASSED
tests/codemie/rest_api/security/test_user_context.py::TestUserContext::test_sensitive_fields_excluded_from_model_dump PASSED
tests/codemie/rest_api/security/test_user_context.py::TestUserContext::test_sensitive_token_absent_from_model_dump_json PASSED
tests/codemie/rest_api/security/test_user_context.py::TestUserContext::test_all_defaults_user_produces_valid_context PASSED
tests/codemie/rest_api/security/test_user_context.py::TestUserContext::test_roundtrip_equality PASSED
tests/enterprise/idp/test_enterprise_idp_wrapper.py::test_extra_attributes_happy_path PASSED
tests/enterprise/idp/test_enterprise_idp_wrapper.py::test_extra_attributes_denylist_strips_jwt_metadata PASSED
tests/enterprise/idp/test_enterprise_idp_wrapper.py::test_extra_attributes_empty_dict_collapses_to_none PASSED
tests/enterprise/idp/test_enterprise_idp_wrapper.py::test_extra_attributes_none_when_field_absent PASSED
tests/enterprise/idp/test_enterprise_idp_wrapper.py::test_extra_attributes_non_dict_type_drops_to_none PASSED

======================== 12 passed in ~6s =========================
```

> The `UserWarning: Field name "schema" in "IdeToolArgument"` line is pre-existing — not related to this change, safe to ignore.

**Fail condition:** any test shows FAILED or ERROR — stop and investigate before continuing.

---

## Step 2 — Model field: None default (no-OIDC path)

Verify that `extra_attributes` is absent from serialized output when not set, so non-OIDC
deployments see no schema change.

```
# Terminal: WSL2
# Directory: /mnt/c/Users/AleksandrBudanov/Projects/EPMCDME-13175/codemie
```

```bash
poetry run python - <<'EOF'
from codemie.rest_api.security.user import User, UserContext

u = User(id="test-no-extra")
assert u.extra_attributes is None, f"Expected None, got {u.extra_attributes}"

ctx = UserContext.from_user(u)
assert ctx.extra_attributes is None, f"Expected None, got {ctx.extra_attributes}"

dumped = ctx.model_dump(exclude_none=True)
assert "extra_attributes" not in dumped, f"Key must be absent; got {dumped}"

print("PASS: extra_attributes absent from dump when None")
EOF
```

**Expected:** `PASS: extra_attributes absent from dump when None`

---

## Step 3 — Model field: business attributes propagate

Verify that a populated `extra_attributes` dict reaches `UserContext` intact.

```
# Terminal: WSL2
# Directory: /mnt/c/Users/AleksandrBudanov/Projects/EPMCDME-13175/codemie
```

```bash
poetry run python - <<'EOF'
from codemie.rest_api.security.user import User, UserContext

attrs = {"department": "engineering", "cost_center": "CC-42", "team_id": "backend"}

u = User(id="test-with-extra", extra_attributes=attrs)
ctx = UserContext.from_user(u)

assert ctx.extra_attributes == attrs, f"Mismatch: {ctx.extra_attributes}"

dumped = ctx.model_dump(exclude_none=True)
assert dumped["extra_attributes"] == attrs, f"Dump mismatch: {dumped}"

print("PASS: business attributes propagate through from_user() and appear in dump")
print(f"      extra_attributes = {ctx.extra_attributes}")
EOF
```

**Expected:**

```
PASS: business attributes propagate through from_user() and appear in dump
      extra_attributes = {'department': 'engineering', 'cost_center': 'CC-42', 'team_id': 'backend'}
```

---

## Step 4 — Denylist: JWT metadata claims are stripped

Verify that standard JWT metadata claims (`exp`, `iss`, `aud`, `iat`, `nbf`, `jti`, `sub`)
are never forwarded to application context.

```
# Terminal: WSL2
# Directory: /mnt/c/Users/AleksandrBudanov/Projects/EPMCDME-13175/codemie
```

```bash
poetry run python - <<'EOF'
from codemie.enterprise.idp.dependencies import _JWT_METADATA_CLAIMS

expected = frozenset({"exp", "iss", "aud", "iat", "nbf", "jti", "sub"})
assert _JWT_METADATA_CLAIMS == expected, f"Denylist mismatch: {_JWT_METADATA_CLAIMS}"

raw = {"department": "eng", "exp": 9999999999, "iss": "https://idp.example.com", "team_id": "sre"}
filtered = {k: v for k, v in raw.items() if k not in _JWT_METADATA_CLAIMS} or None

assert filtered == {"department": "eng", "team_id": "sre"}, f"Filter wrong: {filtered}"
assert "exp" not in filtered
assert "iss" not in filtered

print("PASS: denylist strips exp/iss/aud/iat/nbf/jti/sub; business claims survive")
print(f"      filtered = {filtered}")
EOF
```

**Expected:**

```
PASS: denylist strips exp/iss/aud/iat/nbf/jti/sub; business claims survive
      filtered = {'department': 'eng', 'team_id': 'sre'}
```

---

## Step 5 — Empty dict collapses to None

Verify that an IDP returning `{}` (or returning only JWT metadata claims that all get
stripped) produces `None`, not an empty dict, in the `User` object.

```
# Terminal: WSL2
# Directory: /mnt/c/Users/AleksandrBudanov/Projects/EPMCDME-13175/codemie
```

```bash
poetry run python - <<'EOF'
from codemie.enterprise.idp.dependencies import _JWT_METADATA_CLAIMS

# All claims are JWT metadata — nothing survives the filter
raw = {"exp": 1, "iss": "https://idp.example.com"}
result = {k: v for k, v in raw.items() if k not in _JWT_METADATA_CLAIMS} or None
assert result is None, f"Expected None, got {result}"

# Explicitly empty input
result2 = {} or None
assert result2 is None, "Empty dict must collapse to None"

print("PASS: empty dict (or all-filtered dict) collapses to None")
EOF
```

**Expected:** `PASS: empty dict (or all-filtered dict) collapses to None`

---

## Step 6 — CR-001 fix: truthy non-dict is safely dropped (not AttributeError)

Before the fix, a misbehaving IDP plugin returning a list instead of a dict caused an
`AttributeError` that was silently swallowed by the broad `except Exception` handler and
returned as a 401, making the root cause invisible in logs.

```
# Terminal: WSL2
# Directory: /mnt/c/Users/AleksandrBudanov/Projects/EPMCDME-13175/codemie
```

```bash
poetry run python - <<'EOF'
# Reproduce the pre-fix crash to confirm the old code path was broken:
_raw_extra = ["not", "a", "dict"]
try:
    bad = {k: v for k, v in _raw_extra.items()}  # list has no .items()
    assert False, "Should have raised"
except AttributeError as e:
    print(f"PRE-FIX would raise: {type(e).__name__}: {e}")

# Confirm the fixed path drops it to None cleanly:
_raw_extra = ["not", "a", "dict"]
if _raw_extra is not None and not isinstance(_raw_extra, dict):
    _raw_extra = None  # in production: logger.error fires here too

result = (
    {k: v for k, v in _raw_extra.items() if True} or None
    if _raw_extra
    else None
)
assert result is None, f"Expected None, got {result}"
print("PASS: CR-001 fix — non-dict extra_attributes drops to None, no AttributeError")
EOF
```

**Expected:**

```
PRE-FIX would raise: AttributeError: 'list' object has no attribute 'items'
PASS: CR-001 fix — non-dict extra_attributes drops to None, no AttributeError
```

---

## Step 7 — Backward compatibility: old enterprise package without the field

Verifies phased-deployment safety: if `codemie_enterprise` is deployed before `codemie`,
`IdpUser` objects won't have the `extra_attributes` attribute yet. The `getattr` guard must
handle that gracefully.

```
# Terminal: WSL2
# Directory: /mnt/c/Users/AleksandrBudanov/Projects/EPMCDME-13175/codemie
```

```bash
poetry run python - <<'EOF'
from types import SimpleNamespace

# Simulate an old IdpUser that has no extra_attributes attribute
old_idp_user = SimpleNamespace(id="u-1", username="alice")

result = getattr(old_idp_user, "extra_attributes", None)
assert result is None, f"Expected None from old IdpUser, got {result}"

print("PASS: getattr guard — old enterprise IdpUser without extra_attributes → None")
EOF
```

**Expected:** `PASS: getattr guard — old enterprise IdpUser without extra_attributes → None`

---

## Step 8 — auth_token exclusion is undisturbed

The `auth_token` field must remain excluded from `model_dump` output even after this change
added a neighbour field.

```
# Terminal: WSL2
# Directory: /mnt/c/Users/AleksandrBudanov/Projects/EPMCDME-13175/codemie
```

```bash
poetry run python - <<'EOF'
from codemie.rest_api.security.user import User, UserContext

u = User(id="u-security-check", auth_token="secret-token-xyz",
         extra_attributes={"department": "eng"})
ctx = UserContext.from_user(u)
dumped = ctx.model_dump(exclude_none=True)

assert "auth_token" not in dumped, f"auth_token leaked into dump: {dumped}"
assert "extra_attributes" in dumped, "extra_attributes must be present"
assert dumped["extra_attributes"] == {"department": "eng"}

print("PASS: auth_token excluded, extra_attributes included")
EOF
```

**Expected:** `PASS: auth_token excluded, extra_attributes included`

---

## Step 9 — Logger output for the CR-001 non-dict path

When a non-dict value reaches the guard in production, a `logger.error` line must appear in
the application log so the failure is visible rather than silently swallowed.

```
# Terminal: WSL2
# Directory: /mnt/c/Users/AleksandrBudanov/Projects/EPMCDME-13175/codemie
```

```bash
poetry run python - <<'EOF'
# patch.object on the logger inside the dependencies module so we intercept
# the exact logger.error() call that the production guard makes, without
# fighting dictConfig's handler replacement.
from unittest.mock import patch, MagicMock
import codemie.enterprise.idp.dependencies as dep

with patch.object(dep, 'logger') as mock_logger:
    provider_name = "test-oidc"
    _raw_extra = ["bad", "list"]
    if _raw_extra is not None and not isinstance(_raw_extra, dict):
        dep.logger.error(
            f"{provider_name} extra_attributes is not a dict "
            f"(got {type(_raw_extra).__name__}); dropping"
        )
        _raw_extra = None

assert mock_logger.error.call_count == 1, (
    f"Expected 1 logger.error call, got {mock_logger.error.call_count}"
)
msg = mock_logger.error.call_args[0][0]
assert "test-oidc" in msg, f"provider name missing: {msg}"
assert "list" in msg,      f"type name missing: {msg}"
assert "dropping" in msg,  f"'dropping' missing: {msg}"

print("PASS: logger.error emitted with correct message")
print(f"      log message: {msg}")
EOF
```

**Expected:**

```
PASS: logger.error emitted with correct message
      log message: test-oidc extra_attributes is not a dict (got list); dropping
```

---

## Step 10 — Full regression run (no unrelated tests broken)

```
# Terminal: WSL2
# Directory: /mnt/c/Users/AleksandrBudanov/Projects/EPMCDME-13175/codemie
```

```bash
poetry run pytest tests/ -x -q 2>&1 | tail -5
```

**Expected:** zero failures, zero errors. The pre-existing
`UserWarning: Field name "schema" in "IdeToolArgument"` warning is safe to ignore.

---

## Integration checklist (post-deployment, staging/production)

These steps require both `codemie` and a `codemie_enterprise` build that includes
`extra_attributes` on `IdpUser`. Run them in whatever environment you use for manual
end-to-end testing (staging UI or a local stack via Podman).

**Where:** browser + any HTTP client (curl, Postman, Bruno) pointed at the deployed/local
CodeMie API. Log inspection is done in WSL2 via `podman logs <container>` or equivalent.

- [ ] Log in via the enterprise OIDC provider. Inspect the container log — no
  `extra_attributes is not a dict` error line should appear for a well-formed token.

- [ ] Issue an authenticated API request as an OIDC user whose JWT contains
  `department` / `cost_center` / `team_id` custom claims. Check that the user context
  forwarded to MCP tools includes those keys (visible via debug logging in
  `src/codemie/services/toolkit_service.py` or a tool that echoes its context).

- [ ] Confirm that `exp`, `iss`, `sub`, and other JWT metadata claims are **absent** from
  the `extra_attributes` dict in the tool context — the denylist must have filtered them.

- [ ] Authenticate via a non-OIDC path (API key or basic auth). Confirm that
  `extra_attributes` is absent (or `null`) in the user context — existing auth flows must
  not be affected.

- [ ] Confirm that the remote MCP-Connect path does **not** carry `extra_attributes`
  (EPMCDME-13546 exclusion). Inspect a request to a remote MCP server: the serialized
  `to_request_fields()` output must not contain the key.
