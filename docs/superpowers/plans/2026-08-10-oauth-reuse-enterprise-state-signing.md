# Reuse Enterprise PKCE Store + State Signing for Tool OAuth — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate tool OAuth (GitLab/Jira/Confluence) onto the enterprise state-signing and PKCE-store primitives, and move app credentials out of the PKCE/state record into a separate encrypted snapshot.

**Architecture:** Add payload-agnostic signing helpers and a generic PKCE `context` field in `codemie-enterprise` (existing MCP functions delegate to them → byte-identical, MCP unchanged). In `codemie`, replace the custom `oauth_state_signing.py` signer and the pending-state half of `state_store.py` with the enterprise `encode_signed_state`/`decode_and_verify_signed_state`, `RedisPKCEStore` (verifier + non-secret context), and a small `RedisEncryption`-backed creds snapshot. The signing key and encryption both derive from `MCP_AUTH_HMAC_SECRET`.

**Tech Stack:** Python 3.12, Pydantic v2, Redis, pytest, Poetry; `codemie-enterprise` package (closed source, zero-coupling: enterprise must not import `codemie.*`).

## Global Constraints

- Enterprise package must never import `codemie.*` (zero coupling).
- Enterprise MCP behavior must not change: `encode_oauth2_state`/`decode_and_verify_oauth2_state` output byte-identical; new `PKCEStateData` fields optional/defaulted. The full `tests/mcp_auth/` suite is the guard.
- Apache-2.0 license header on every new `.py` file (copy from any existing file in the same repo).
- Commit messages prefixed `EPMCDME-13527:`; end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Do NOT commit or push without explicit in-the-moment user permission — if unattended, stop after the last code step of each task and leave the commit for the user.
- Base OAuth unit tests stay hermetic: they must pass without the installed `codemie-enterprise` package (extend the existing stub in `tests/codemie/service/oauth/tms_stub.py`).
- Signing key source: `RedisEncryption(config.MCP_AUTH_HMAC_SECRET).signing_key`. No separate `OAUTH_STATE_SIGNING_SECRET`.
- State semantic bounds (base validator): `max_age = 600s`, `clock_skew = 5s`.
- Enterprise version after this change: `2.3.39`.

---

## Task 1: Enterprise — payload-agnostic signing helpers

**Files:**
- Modify: `codemie-enterprise/src/codemie_enterprise/mcp_auth/oauth2_flow.py`
- Modify: `codemie-enterprise/src/codemie_enterprise/mcp_auth/__init__.py`
- Test: `codemie-enterprise/tests/mcp_auth/test_oauth2_flow.py` (add cases)

**Interfaces:**
- Produces:
  - `encode_signed_state(payload: dict[str, Any], signing_key: bytes) -> str`
  - `decode_and_verify_signed_state(state: str, signing_key: bytes) -> dict[str, Any]` (raises `ValueError(_INVALID_OAUTH2_CALLBACK_STATE)` on any tamper/format error)
  - Existing `encode_oauth2_state` / `decode_and_verify_oauth2_state` unchanged in behavior, now delegating.

- [ ] **Step 1: Write failing tests**

```python
# tests/mcp_auth/test_oauth2_flow.py (append)
from codemie_enterprise.mcp_auth.oauth2_flow import (
    encode_signed_state,
    decode_and_verify_signed_state,
)
import pytest

_KEY = b"k" * 32

def test_signed_state_round_trips_arbitrary_dict():
    payload = {"v": 1, "provider": "jira", "integration_id": "s1", "ts": 123}
    token = encode_signed_state(payload, _KEY)
    assert decode_and_verify_signed_state(token, _KEY) == payload

def test_signed_state_rejects_tamper():
    token = encode_signed_state({"a": "b"}, _KEY)
    body, sig = token.split(".")
    with pytest.raises(ValueError):
        decode_and_verify_signed_state(f"{body}.{sig[:-2]}xy", _KEY)

def test_signed_state_rejects_wrong_key():
    token = encode_signed_state({"a": "b"}, _KEY)
    with pytest.raises(ValueError):
        decode_and_verify_signed_state(token, b"other-key-value-................")

def test_mcp_encode_matches_generic_encode():
    from codemie_enterprise.mcp_auth.oauth2_flow import encode_oauth2_state, _build_state_payload
    p = _build_state_payload(auth_config_id="a", user_id="u", session_binding_hash="h", now=5)
    assert encode_oauth2_state(p, _KEY) == encode_signed_state(p.model_dump(), _KEY)
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd codemie-enterprise && poetry run pytest tests/mcp_auth/test_oauth2_flow.py -k "signed_state or matches_generic" -q`
Expected: FAIL (ImportError: `encode_signed_state`).

- [ ] **Step 3: Implement the generic helpers and delegate**

In `oauth2_flow.py`, add (near the existing `encode_oauth2_state`):

```python
def encode_signed_state(payload: dict[str, Any], signing_key: bytes) -> str:
    payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload_b64 = _b64url_encode(payload_json)
    signature = hmac.new(signing_key, payload_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64url_encode(signature)}"


def decode_and_verify_signed_state(state: str, signing_key: bytes) -> dict[str, Any]:
    try:
        state_parts = state.split(".")
        if len(state_parts) != 2 or not state_parts[0] or not state_parts[1]:
            raise ValueError(_INVALID_OAUTH2_CALLBACK_STATE)
        payload_b64, sig_b64 = state_parts
        expected_signature = hmac.new(signing_key, payload_b64.encode("ascii"), hashlib.sha256).digest()
        provided_signature = _b64url_decode(sig_b64)
        if not hmac.compare_digest(provided_signature, expected_signature):
            raise ValueError(_INVALID_OAUTH2_CALLBACK_STATE)
        payload_json = _b64url_decode(payload_b64).decode("utf-8")
        parsed = json.loads(payload_json)
        if not isinstance(parsed, dict):
            raise ValueError(_INVALID_OAUTH2_CALLBACK_STATE)
        return parsed
    except (ValueError, TypeError) as exc:
        raise ValueError(_INVALID_OAUTH2_CALLBACK_STATE) from exc
```

Then replace the bodies of the existing functions to delegate:

```python
def encode_oauth2_state(payload: OAuth2StatePayload, signing_key: bytes) -> str:
    return encode_signed_state(payload.model_dump(), signing_key)


def decode_and_verify_oauth2_state(state: str, signing_key: bytes) -> OAuth2StatePayload:
    return OAuth2StatePayload.model_validate(decode_and_verify_signed_state(state, signing_key))
```

- [ ] **Step 4: Export the new helpers**

In `mcp_auth/__init__.py`, add `encode_signed_state` and `decode_and_verify_signed_state` to the `from .oauth2_flow import (...)` block and to `__all__` (alongside `encode_oauth2_state`).

- [ ] **Step 5: Run the full oauth2_flow suite**

Run: `cd codemie-enterprise && poetry run pytest tests/mcp_auth/test_oauth2_flow.py -q`
Expected: PASS (new cases + all existing MCP cases — delegation is byte-identical).

- [ ] **Step 6: Commit**

```bash
git -C codemie-enterprise add src/codemie_enterprise/mcp_auth/oauth2_flow.py src/codemie_enterprise/mcp_auth/__init__.py tests/mcp_auth/test_oauth2_flow.py
git -C codemie-enterprise commit -m "EPMCDME-13527: add payload-agnostic signed-state helpers (MCP delegates)"
```

---

## Task 2: Enterprise — generic PKCE context + optional session binding

**Files:**
- Modify: `codemie-enterprise/src/codemie_enterprise/mcp_auth/models.py:137` (`PKCEStateData`)
- Test: `codemie-enterprise/tests/mcp_auth/test_models.py`

**Interfaces:**
- Produces: `PKCEStateData(code_verifier, user_id, auth_config_id, session_binding_hash: str | None = None, context: dict[str, str] = {}, discovered_flow_id=None, recovery_flow_id=None)`.

- [ ] **Step 1: Write failing tests**

```python
# tests/mcp_auth/test_models.py (append)
from codemie_enterprise.mcp_auth.models import PKCEStateData

def test_pkce_state_data_allows_tool_shape_without_session_binding():
    d = PKCEStateData(code_verifier="v", user_id="u", auth_config_id="s1",
                      context={"provider": "jira", "instance_url": "https://x"})
    assert d.session_binding_hash is None
    assert d.context["provider"] == "jira"

def test_pkce_state_data_context_defaults_empty_and_mcp_shape_still_works():
    d = PKCEStateData(code_verifier="v", user_id="u", auth_config_id="a", session_binding_hash="h")
    assert d.context == {}
    assert d.session_binding_hash == "h"
```

- [ ] **Step 2: Run, verify fail**

Run: `cd codemie-enterprise && poetry run pytest tests/mcp_auth/test_models.py -k pkce_state_data -q`
Expected: FAIL (`context` unknown / `session_binding_hash` required).

- [ ] **Step 3: Edit `PKCEStateData`**

```python
class PKCEStateData(BaseModel):
    code_verifier: str
    user_id: str
    auth_config_id: str
    session_binding_hash: str | None = None
    context: dict[str, str] = Field(default_factory=dict)
    discovered_flow_id: str | None = None
    recovery_flow_id: str | None = None

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("exclude_none", True)
        return super().model_dump(*args, **kwargs)
```

(`Field` is already imported in this module.)

- [ ] **Step 4: Fix any existing exact-dump assertions**

Run: `cd codemie-enterprise && poetry run pytest tests/mcp_auth/test_models.py -q`
If a test asserts an exact `PKCEStateData(...).model_dump()`, add `"context": {}` to its expected dict (and drop `session_binding_hash` when it was only present because required). `model_dump` uses `exclude_none=True`, so a `None` `session_binding_hash` and empty defaults are omitted — adjust expected dicts to match. Re-run until green.

- [ ] **Step 5: Commit**

```bash
git -C codemie-enterprise add src/codemie_enterprise/mcp_auth/models.py tests/mcp_auth/test_models.py
git -C codemie-enterprise commit -m "EPMCDME-13527: make PKCEStateData session binding optional + add generic context"
```

---

## Task 3: Enterprise — `RedisPKCEStore.peek`

**Files:**
- Modify: `codemie-enterprise/src/codemie_enterprise/mcp_auth/redis_pkce_store.py`
- Test: `codemie-enterprise/tests/mcp_auth/test_redis_pkce_store.py`

**Interfaces:**
- Produces: `RedisPKCEStore.peek(state: str) -> PKCEStateData | None` (non-consuming; leaves the record in place).

- [ ] **Step 1: Write failing test** (mirror the existing store/consume tests' fixture)

```python
def test_peek_returns_without_deleting(pkce_store):  # reuse the module's existing fixture
    data = PKCEStateData(code_verifier="v", user_id="u", auth_config_id="s1")
    pkce_store.store("state-xyz", data)
    peeked = pkce_store.peek("state-xyz")
    assert peeked is not None and peeked.code_verifier == "v"
    # still present for a subsequent consume
    assert pkce_store.consume("state-xyz") is not None
    assert pkce_store.peek("state-xyz") is None
```

- [ ] **Step 2: Run, verify fail** — `poetry run pytest tests/mcp_auth/test_redis_pkce_store.py -k peek -q` → FAIL (`peek` missing).

- [ ] **Step 3: Implement `peek`**

```python
def peek(self, state: str) -> PKCEStateData | None:
    key = self._redis_key(state)
    try:
        payload = self._client.get(key)
    except RedisError as exc:
        self._raise_unavailable(exc)
    if payload is None:
        return None
    try:
        decrypted_payload = self._crypto.decrypt(payload)
    except InvalidToken:
        logger.warning("Discarding unusable PKCE state payload after decryption failure")
        return None
    return PKCEStateData.model_validate_json(decrypted_payload)
```

- [ ] **Step 4: Run, verify pass** — `poetry run pytest tests/mcp_auth/test_redis_pkce_store.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git -C codemie-enterprise add src/codemie_enterprise/mcp_auth/redis_pkce_store.py tests/mcp_auth/test_redis_pkce_store.py
git -C codemie-enterprise commit -m "EPMCDME-13527: add non-consuming RedisPKCEStore.peek"
```

---

## Task 4: Enterprise — version bump + full-suite guard

**Files:**
- Modify: `codemie-enterprise/pyproject.toml` (`version`), `codemie-enterprise/src/codemie_enterprise/__init__.py` (`__version__`)

- [ ] **Step 1: Bump both to `2.3.39`.**

- [ ] **Step 2: Run the full enterprise suite**

Run: `cd codemie-enterprise && poetry run pytest -q`
Expected: all pass (MCP unchanged). If any exact-dump test outside `test_models.py` references `PKCEStateData`, fix expected dict as in Task 2 Step 4.

- [ ] **Step 3: Commit**

```bash
git -C codemie-enterprise add pyproject.toml src/codemie_enterprise/__init__.py
git -C codemie-enterprise commit -m "EPMCDME-13527: bump codemie-enterprise to 2.3.39"
```

---

## Task 5: Base — tool state signer over enterprise helpers

Replaces `oauth_state_signing.py`. Provides: sign a tool state dict, and decode+semantically-validate it (folding in the future-timestamp bound).

**Files:**
- Create: `src/codemie/service/oauth/state_signing.py`
- Create: `tests/codemie/service/oauth/test_state_signing.py`
- Modify: `tests/codemie/service/oauth/tms_stub.py` (add signing/crypto/PKCE stubs — see Step 1)

**Interfaces:**
- Produces:
  - `ToolOAuthState` dataclass: `provider, integration_id, user_id, redirect_uri, nonce, ts, v=1`
  - `sign_tool_state(*, provider, integration_id, user_id, redirect_uri, signing_key: bytes) -> str`
  - `verify_tool_state(state: str, *, signing_key: bytes, expected_providers: set[str], max_age_seconds: int = 600, clock_skew_seconds: int = 5) -> ToolOAuthState` (raises `ToolStateError`)
  - `ToolStateError(ValueError)`

- [ ] **Step 1: Extend the hermetic stub**

The base OAuth tests must run without the installed enterprise package. Add to `tests/codemie/service/oauth/tms_stub.py` a stub `codemie_enterprise.mcp_auth` that also exposes signing + crypto + PKCE (only when the real package is absent — keep the existing `find_spec` guard pattern):

```python
# --- append to tms_stub.py module namespace ---
import hashlib, hmac, json, base64

def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")

def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

def encode_signed_state(payload: dict, signing_key: bytes) -> str:
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    sig = hmac.new(signing_key, body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64(sig)}"

def decode_and_verify_signed_state(state: str, signing_key: bytes) -> dict:
    body, sig = state.split(".")
    expected = hmac.new(signing_key, body.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(_unb64(sig), expected):
        raise ValueError("invalid state")
    return json.loads(_unb64(body).decode())

class RedisEncryption:
    def __init__(self, secret): self._k = (secret or "").encode() or b"stub"
    @property
    def signing_key(self) -> bytes: return hashlib.sha256(self._k + b"sig").digest()
    def encrypt(self, b: bytes) -> bytes: return base64.b64encode(b)
    def decrypt(self, b): return base64.b64decode(b)

class PKCEStateData:
    def __init__(self, **kw): self.__dict__.update(kw); self.context = kw.get("context", {})
    def model_dump_json(self): return json.dumps(self.__dict__)
    @classmethod
    def model_validate_json(cls, s): return cls(**json.loads(s))

class RedisPKCEStore:
    def __init__(self, client, crypto, namespace="tool_oauth"): self._m = {}
    def store(self, state, data): self._m[state] = data
    def consume(self, state): return self._m.pop(state, None)
    def peek(self, state): return self._m.get(state)
```

Register these names on the stub module built by `build_tms_stub_module()` / `install_tms_stub_for_import()`.

- [ ] **Step 2: Write failing tests**

```python
# tests/codemie/service/oauth/test_state_signing.py
import time, pytest
from codemie.service.oauth.state_signing import sign_tool_state, verify_tool_state, ToolStateError

KEY = b"k" * 32
def _sign(**kw):
    d = dict(provider="jira", integration_id="s1", user_id="u1",
             redirect_uri="https://cb", signing_key=KEY)
    d.update(kw); return sign_tool_state(**d)

def test_round_trip():
    s = verify_tool_state(_sign(), signing_key=KEY, expected_providers={"jira"})
    assert s.provider == "jira" and s.integration_id == "s1"

def test_rejects_tamper():
    body, sig = _sign().split(".")
    with pytest.raises(ToolStateError):
        verify_tool_state(f"{body}.{sig[:-2]}xy", signing_key=KEY, expected_providers={"jira"})

def test_rejects_expired():
    tok = _sign()
    with pytest.raises(ToolStateError):
        verify_tool_state(tok, signing_key=KEY, expected_providers={"jira"}, max_age_seconds=-1)

def test_rejects_future_ts(monkeypatch):
    import codemie.service.oauth.state_signing as m
    real = time.time
    monkeypatch.setattr(m.time, "time", lambda: real() + 3600)  # sign 1h in the future
    tok = _sign()
    monkeypatch.setattr(m.time, "time", real)
    with pytest.raises(ToolStateError):
        verify_tool_state(tok, signing_key=KEY, expected_providers={"jira"})

def test_rejects_wrong_provider():
    with pytest.raises(ToolStateError):
        verify_tool_state(_sign(), signing_key=KEY, expected_providers={"gitlab"})
```

- [ ] **Step 3: Run, verify fail** — `docker exec … pytest tests/codemie/service/oauth/test_state_signing.py -q` → FAIL (module missing).

- [ ] **Step 4: Implement `state_signing.py`** (Apache header + this body)

```python
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

from codemie_enterprise.mcp_auth import decode_and_verify_signed_state, encode_signed_state

_STATE_VERSION = 1


class ToolStateError(ValueError):
    """Raised when a tool OAuth state fails signature or semantic validation."""


@dataclass(frozen=True)
class ToolOAuthState:
    provider: str
    integration_id: str
    user_id: str
    redirect_uri: str
    nonce: str
    ts: int
    v: int = _STATE_VERSION


def sign_tool_state(*, provider: str, integration_id: str, user_id: str,
                    redirect_uri: str, signing_key: bytes) -> str:
    payload = {
        "v": _STATE_VERSION,
        "provider": provider,
        "integration_id": integration_id,
        "user_id": user_id,
        "redirect_uri": redirect_uri,
        "nonce": secrets.token_urlsafe(16),
        "ts": int(time.time()),
    }
    return encode_signed_state(payload, signing_key)


def verify_tool_state(state: str, *, signing_key: bytes, expected_providers: set[str],
                      max_age_seconds: int = 600, clock_skew_seconds: int = 5) -> ToolOAuthState:
    try:
        payload = decode_and_verify_signed_state(state, signing_key)
    except Exception as exc:  # signature/format failures
        raise ToolStateError("Invalid or expired authentication state.") from exc
    try:
        parsed = ToolOAuthState(
            v=int(payload.get("v", 0)),
            provider=str(payload["provider"]),
            integration_id=str(payload.get("integration_id", "") or ""),
            user_id=str(payload["user_id"]),
            redirect_uri=str(payload["redirect_uri"]),
            nonce=str(payload["nonce"]),
            ts=int(payload["ts"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolStateError("OAuth state payload is malformed.") from exc

    if parsed.v != _STATE_VERSION:
        raise ToolStateError("Unsupported OAuth state version.")
    if parsed.provider not in expected_providers:
        raise ToolStateError("OAuth state provider not allowed for this callback.")
    if not parsed.user_id or not parsed.redirect_uri or not parsed.nonce:
        raise ToolStateError("OAuth state payload fields are invalid.")
    now = int(time.time())
    if parsed.ts <= 0 or now - parsed.ts > max_age_seconds:
        raise ToolStateError("OAuth state has expired.")
    if parsed.ts > now + clock_skew_seconds:
        raise ToolStateError("OAuth state timestamp is in the future.")
    return parsed
```

- [ ] **Step 5: Run, verify pass** — `pytest tests/codemie/service/oauth/test_state_signing.py -q` → PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/oauth/state_signing.py tests/codemie/service/oauth/test_state_signing.py tests/codemie/service/oauth/tms_stub.py
git commit -m "EPMCDME-13527: sign tool OAuth state via enterprise helpers + semantic validator"
```

---

## Task 6: Base — PKCE + creds stores over enterprise primitives

Replaces the pending-state half of `state_store.py`. The **result store** stays (moved verbatim into this file as `OAuthResultStore`).

**Files:**
- Create: `src/codemie/service/oauth/stores.py`
- Create: `tests/codemie/service/oauth/test_stores.py`
- (Task 8 deletes `state_store.py` once callers move.)

**Interfaces:**
- Produces:
  - `build_tool_pkce_store(namespace: str) -> RedisPKCEStore` — enterprise store from app redis client + `RedisEncryption(config.MCP_AUTH_HMAC_SECRET)`.
  - `ToolCredsSnapshot(namespace)` with `store(state, {"client_id":…, "client_secret":…}) -> bool`, `consume(state) -> dict | None` (get-and-delete), TTL 600s, encrypted via the same `RedisEncryption`.
  - `signing_key() -> bytes` — `RedisEncryption(config.MCP_AUTH_HMAC_SECRET).signing_key`.
  - `OAuthResultStore` — the current result-store methods (`store_result`, `get_result`) verbatim from `state_store.py`.

- [ ] **Step 1: Write failing tests** (use fakeredis if available in-container, else the stub redis)

```python
# tests/codemie/service/oauth/test_stores.py
from codemie.service.oauth.stores import ToolCredsSnapshot

class _FakeRedis:
    def __init__(self): self.kv = {}
    def set(self, k, v, ex=None): self.kv[k] = v
    def getdel(self, k): return self.kv.pop(k, None)

def test_creds_snapshot_round_trip(monkeypatch):
    snap = ToolCredsSnapshot(namespace="tool_oauth", redis_client=_FakeRedis())
    assert snap.store("st", {"client_id": "cid", "client_secret": "sec"}) is True
    got = snap.consume("st")
    assert got == {"client_id": "cid", "client_secret": "sec"}
    assert snap.consume("st") is None  # get-and-delete
```

- [ ] **Step 2: Run, verify fail** — module missing.

- [ ] **Step 3: Implement `stores.py`** (Apache header)

```python
from __future__ import annotations

import hashlib
import json
from typing import Optional

from codemie.clients.redis import create_redis_client
from codemie.configs import config, logger

_CREDS_TTL = 600


def _redis_encryption():
    from codemie_enterprise.mcp_auth import RedisEncryption
    return RedisEncryption(config.MCP_AUTH_HMAC_SECRET)


def signing_key() -> bytes:
    return _redis_encryption().signing_key


def build_tool_pkce_store(namespace: str):
    from codemie_enterprise.mcp_auth import RedisPKCEStore
    return RedisPKCEStore(create_redis_client(), _redis_encryption(), namespace=namespace)


class ToolCredsSnapshot:
    """Encrypted, state-keyed snapshot of app credentials, kept out of the PKCE record."""

    def __init__(self, namespace: str, redis_client=None, crypto=None):
        self._redis = redis_client or create_redis_client()
        self._crypto = crypto or _redis_encryption()
        self._namespace = namespace

    def _key(self, state: str) -> str:
        return f"{self._namespace}:creds:{hashlib.sha256(state.encode()).hexdigest()}"

    def store(self, state: str, creds: dict) -> bool:
        try:
            self._redis.set(self._key(state), self._crypto.encrypt(json.dumps(creds).encode()), ex=_CREDS_TTL)
            return True
        except Exception as exc:
            logger.error(f"Tool OAuth: failed to store credential snapshot: {exc}")
            return False

    def consume(self, state: str) -> Optional[dict]:
        raw = self._redis.getdel(self._key(state))
        if raw is None:
            return None
        try:
            return json.loads(self._crypto.decrypt(raw))
        except Exception as exc:
            logger.warning(f"Tool OAuth: unusable credential snapshot for state: {exc}")
            return None
```

Also move the result-store class here as `OAuthResultStore` (copy `store_result`/`get_result` and the `_redis_*` helpers they use from `state_store.py` verbatim; keep the same Redis key prefix so in-flight flows during deploy still resolve).

- [ ] **Step 4: Run, verify pass** — `pytest tests/codemie/service/oauth/test_stores.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/oauth/stores.py tests/codemie/service/oauth/test_stores.py
git commit -m "EPMCDME-13527: PKCE + encrypted creds snapshot over enterprise RedisEncryption"
```

---

## Task 7: Base — provider adapters return non-secret context only

**Files:**
- Modify: `src/codemie/service/oauth/provider_adapters.py` (`build_state_data` for GitLab + Atlassian base)
- Test: `tests/codemie/service/oauth/adapters/test_provider_adapters.py`

**Interfaces:**
- Changed: `build_state_data(...)` returns only NON-secret context — `{"provider": …, "instance_url": …}` (GitLab) / `{"provider": …}` (Atlassian). It must NOT return `client_id`, `client_secret`, `code_verifier`, `redirect_uri`, or `user_id` (the engine owns those now).
- `build_authorize_url` / `exchange_code_for_tokens` keep reading `state_data["client_id"]`, `["client_secret"]`, `["instance_url"]`, `["redirect_uri"]` — the engine reassembles that dict (Task 8), so their bodies do not change.

- [ ] **Step 1: Write failing test**

```python
def test_gitlab_build_state_data_has_no_secrets():
    from codemie.service.oauth.provider_adapters import GitLabOAuthProviderAdapter
    ctx = GitLabOAuthProviderAdapter.build_state_data(
        user_id="u", integration_id="s1", provider="gitlab",
        redirect_uri="https://cb", client_id="cid", client_secret="sec",
        code_verifier="v", context={"instance_url": "https://gitlab.com"},
    )
    assert "client_secret" not in ctx and "client_id" not in ctx
    assert "code_verifier" not in ctx
    assert ctx["instance_url"] == "https://gitlab.com" and ctx["provider"] == "gitlab"
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Edit `build_state_data`** (GitLab) to return only non-secret context:

```python
@staticmethod
def build_state_data(*, user_id, integration_id, provider, redirect_uri,
                     client_id, client_secret, code_verifier, context):
    instance_url = ensure_instance_allowed(
        str(context.get("instance_url") or config.GITLAB_OAUTH_DEFAULT_INSTANCE_URL)
    )
    return {"provider": provider, "instance_url": instance_url}
```

Atlassian base `build_state_data`:

```python
@staticmethod
def build_state_data(*, user_id, integration_id, provider, redirect_uri,
                     client_id, client_secret, code_verifier, context):
    return {"provider": provider}
```

(Keep the signature — Task 8 still calls it with these kwargs; the secret/verifier kwargs are now ignored by the body. The Protocol in `flow_engine.py` is unchanged.)

- [ ] **Step 4: Run adapter tests** — `pytest tests/codemie/service/oauth/adapters/test_provider_adapters.py -q` → PASS (Bearer-header + finalize tests still green; they don't depend on build_state_data secrets).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/oauth/provider_adapters.py tests/codemie/service/oauth/adapters/test_provider_adapters.py
git commit -m "EPMCDME-13527: adapters emit only non-secret flow context"
```

---

## Task 8: Base — rewire OAuthFlowEngine onto the new stores + signer

**Files:**
- Modify: `src/codemie/service/oauth/flow_engine.py`
- Modify: `src/codemie/service/{gitlab,jira,confluence}_oauth/flow_service.py` (constructor wiring)
- Test: `tests/codemie/service/oauth/test_flow_engine.py` (update fakes)

**Interfaces:**
- Consumes: `sign_tool_state`/`verify_tool_state`/`ToolStateError` (Task 5); `build_tool_pkce_store`, `ToolCredsSnapshot`, `signing_key`, `OAuthResultStore` (Task 6); enterprise `PKCEStateData` (Task 2).
- `OAuthFlowEngine.__init__(self, *, adapter, pkce_store, creds_snapshot, result_store, encryption_service=None)` (replaces the single `state_store`).
- Produces: same public methods (`initiate_flow`, `handle_callback`, `get_status`, `revoke_connection`) with identical return types.

- [ ] **Step 1: Update the test doubles + a CR-focused test**

Replace the `_FakeStateStore` in `test_flow_engine.py` with three fakes (`_FakePkceStore` with `store/consume/peek` returning `PKCEStateData`-like objects, `_FakeCredsSnapshot`, `_FakeResultStore`). Keep every existing assertion (happy path persists; integration_id / user_id mismatch rejected and never persists; tampered state rejected; provider error short-circuits; missing state rejected; finalize failure reported; result published for waiting tab; replay cannot overwrite). Add:

```python
def test_credentials_are_not_stored_in_the_pkce_record():
    engine, adapter, fakes = _engine(...)  # returns the three fakes
    engine.initiate_flow(user_id="u1", client_id="cid", client_secret="sec",
                         integration_id="s1", provider="jira", context={})
    # verifier record carries no secret; creds live only in the creds snapshot
    stored = fakes.pkce.last_stored
    assert "client_secret" not in stored.context and "client_secret" not in vars(stored)
    assert fakes.creds.last == {"client_id": "cid", "client_secret": "sec"}
```

- [ ] **Step 2: Run, verify fail** (engine still references `state_store`).

- [ ] **Step 3: Rewrite the engine wiring**

`__init__`:

```python
def __init__(self, *, adapter, pkce_store, creds_snapshot, result_store, encryption_service=None):
    self.adapter = adapter
    self.pkce_store = pkce_store
    self.creds_snapshot = creds_snapshot
    self.result_store = result_store
    self.encryption_service = encryption_service or EncryptionFactory().get_current_encryption_service()
```

`initiate_flow` (replace signing + store):

```python
from codemie.service.oauth.state_signing import sign_tool_state
from codemie.service.oauth.stores import signing_key
from codemie_enterprise.mcp_auth import PKCEStateData

state = sign_tool_state(
    provider=effective_provider, integration_id=integration, user_id=user_id,
    redirect_uri=redirect_uri, signing_key=signing_key(),
)
ctx = self.adapter.build_state_data(
    user_id=user_id, integration_id=integration, provider=effective_provider,
    redirect_uri=redirect_uri, client_id=effective_client_id,
    client_secret=effective_client_secret, code_verifier=code_verifier, context=context or {},
)
ctx["redirect_uri"] = redirect_uri
self.pkce_store.store(state, PKCEStateData(
    code_verifier=code_verifier, user_id=user_id, auth_config_id=integration,
    session_binding_hash=None, context=ctx,
))
self.creds_snapshot.store(state, {"client_id": effective_client_id, "client_secret": effective_client_secret})
auth_url = self.adapter.build_authorize_url(
    state_data={**ctx, "client_id": effective_client_id}, state=state,
    code_challenge=code_challenge, provider=effective_provider,
)
return {"auth_url": auth_url, "state": state}
```

`handle_callback` (replace decode + consume + reassemble):

```python
from codemie.service.oauth.state_signing import verify_tool_state, ToolStateError

if not state:
    return CallbackResult(False, "Missing state parameter.", 400)
try:
    signed = verify_tool_state(state, signing_key=signing_key(),
                               expected_providers=self.adapter.expected_state_providers)
except ToolStateError:
    return CallbackResult(False, "Invalid or expired authentication state.", 400)

pkce = self.pkce_store.consume(state)
creds = self.creds_snapshot.consume(state)
if error:
    return CallbackResult(False, self.adapter.get_error_message(error, signed.provider), 200)
if pkce is None or creds is None or not code:
    return CallbackResult(False, "Invalid or expired authentication state.", 400)

state_data = {
    **(pkce.context or {}),
    "code_verifier": pkce.code_verifier,
    "user_id": signed.user_id,
    "integration_id": signed.integration_id,
    "redirect_uri": signed.redirect_uri,
    "client_id": creds.get("client_id", ""),
    "client_secret": creds.get("client_secret", ""),
}
```

Then feed `signed` + `state_data` into the existing `_validate_state_data` / `_exchange_and_finalize` / `_publish_success` path. Update `_validate_state_data` to compare against the `ToolOAuthState` fields (`signed.integration_id`, `signed.user_id`, `signed.redirect_uri`) — the CR-001 binding check stays: `state_data["integration_id"] == signed.integration_id`, etc.

`get_status`: use `self.result_store.get_result(state)` then `self.pkce_store.peek(state)` for the pending branch (replacing `get_pending_state`). Ownership check unchanged.

`_publish_success` / `_fail`: call `self.result_store.store_result(...)` instead of `self.state_store.store_result(...)`.

- [ ] **Step 4: Wire the flow services**

In each `…_oauth/flow_service.py`, build the engine with the new stores. GitLab example:

```python
from codemie.service.oauth.stores import build_tool_pkce_store, ToolCredsSnapshot, OAuthResultStore

self.engine = OAuthFlowEngine(
    adapter=GitLabOAuthProviderAdapter(),
    pkce_store=build_tool_pkce_store("tool_oauth_gitlab"),
    creds_snapshot=ToolCredsSnapshot("tool_oauth_gitlab"),
    result_store=OAuthResultStore(namespace="gitlab_oauth"),  # keep the existing result key prefix
)
```

Atlassian (Jira + Confluence share the callback) use namespace `tool_oauth_atlassian` for pkce/creds and their existing result namespaces.

- [ ] **Step 5: Run engine + flow-service tests**

Run: `docker exec -e PYTHONPATH=/tmp/e236:/app/src … pytest tests/codemie/service/oauth tests/codemie/service/gitlab_oauth tests/codemie/service/jira_oauth tests/codemie/service/confluence_oauth -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/oauth/flow_engine.py src/codemie/service/gitlab_oauth/flow_service.py src/codemie/service/jira_oauth/flow_service.py src/codemie/service/confluence_oauth/flow_service.py tests/codemie/service/oauth/test_flow_engine.py
git commit -m "EPMCDME-13527: flow engine uses enterprise PKCE store + creds snapshot + signer"
```

---

## Task 9: Base — config + startup guard

**Files:**
- Modify: `src/codemie/configs/config.py` (remove `OAUTH_STATE_SIGNING_SECRET`, `OAUTH_STATE_SIGNING_MAX_AGE_SECONDS`)
- Modify: `src/codemie/service/oauth_security.py` (`assert_oauth_state_signing_secret_configured`)
- Test: `tests/codemie/service/test_oauth_token_vault_guard.py` or a sibling guard test

**Interfaces:**
- `assert_oauth_state_signing_secret_configured()` now requires `MCP_AUTH_HMAC_SECRET` (≥32 bytes) when any tool OAuth provider is enabled.

- [ ] **Step 1: Write/adjust failing test**

```python
def test_tool_oauth_requires_mcp_hmac_secret(monkeypatch):
    import codemie.service.oauth_security as sec
    monkeypatch.setattr(sec.config, "GITLAB_OAUTH_ENABLED", True, raising=False)
    monkeypatch.setattr(sec.config, "MCP_AUTH_HMAC_SECRET", "", raising=False)
    with pytest.raises(RuntimeError):
        sec.assert_oauth_state_signing_secret_configured()
```

- [ ] **Step 2: Run, verify fail** (still reads `OAUTH_STATE_SIGNING_SECRET`).

- [ ] **Step 3: Update guard body**

```python
def assert_oauth_state_signing_secret_configured() -> None:
    oauth_enabled = config.GITLAB_OAUTH_ENABLED or config.JIRA_OAUTH_ENABLED or config.CONFLUENCE_OAUTH_ENABLED
    if not oauth_enabled:
        return
    secret = config.MCP_AUTH_HMAC_SECRET or ""
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError(
            "Tool OAuth requires MCP_AUTH_HMAC_SECRET to be at least 32 bytes (it signs the OAuth "
            "state and encrypts the PKCE/credential records). Configure it and restart."
        )
```

Remove the two `OAUTH_STATE_SIGNING_*` fields from `config.py`. Update `.env` docs/comments if present.

- [ ] **Step 4: Run** — `pytest tests/codemie/service/test_oauth_token_vault_guard.py -q` and the config-defaults test → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/configs/config.py src/codemie/service/oauth_security.py tests/codemie/service/test_oauth_token_vault_guard.py
git commit -m "EPMCDME-13527: gate tool OAuth on MCP_AUTH_HMAC_SECRET; drop OAUTH_STATE_SIGNING_SECRET"
```

---

## Task 10: Base — delete dead modules + full gate

**Files:**
- Delete: `src/codemie/service/oauth_state_signing.py`
- Delete: `src/codemie/service/oauth/state_store.py` and `src/codemie/service/{gitlab,jira,confluence,atlassian}_oauth/state_store.py` (the subclasses), plus their tests
- Delete/rewrite: `tests/codemie/service/oauth/test_signed_state.py` (superseded by `test_state_signing.py`)

- [ ] **Step 1: Grep for dangling references**

Run:
```bash
grep -rn "oauth_state_signing\|create_signed_oauth_state\|decode_and_verify_oauth_state\|OAuthStateStore\|GitLabOAuthStateStore\|AtlassianOAuthStateStore\|state_store\|OAUTH_STATE_SIGNING_SECRET" src/ tests/
```
Expected after edits: no hits outside the new `stores.py`/`state_signing.py`. Fix any stragglers (imports in flow_service, `__init__.py` exports).

- [ ] **Step 2: Delete the files** (`git rm`), delete `test_signed_state.py`.

- [ ] **Step 3: Run the full OAuth + guard + settings suites (base)**

Run (enterprise source on path):
```bash
docker exec -e PYTHONPATH=/tmp/e236:/app/src codemie-dev-codemie-1 python -m pytest \
  tests/codemie/service/oauth tests/codemie/service/gitlab_oauth tests/codemie/service/jira_oauth \
  tests/codemie/service/confluence_oauth tests/codemie/service/test_oauth_token_vault_guard.py \
  tests/codemie/service/tools/test_oauth_connect_gate.py tests/codemie/rest_api/routers/test_gitlab_oauth_connect.py -q
```
Expected: all PASS.

- [ ] **Step 4: Ruff**

Run: `.venv/bin/ruff check --no-cache src/codemie/service/oauth src/codemie/service/oauth_security.py src/codemie/configs/config.py`
Expected: All checks passed.

- [ ] **Step 5: Enterprise full suite (regression guard)**

Run: `cd codemie-enterprise && poetry run pytest -q`
Expected: all PASS (MCP unchanged).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "EPMCDME-13527: remove custom OAuth state store + signer (superseded by enterprise)"
```

---

## Self-Review notes (coverage)

- Signing → enterprise: Tasks 1, 5, 8 (base uses `encode/decode_signed_state`).
- PKCE store → enterprise `RedisPKCEStore` (+ `peek`): Tasks 3, 6, 8.
- Creds out of PKCE/state record: Tasks 6, 7, 8 (`ToolCredsSnapshot`; adapters emit no secrets; engine stores creds only in the snapshot; test asserts absence).
- Future-timestamp bound: Task 5 (`verify_tool_state` + `test_rejects_future_ts`).
- MCP unchanged: Tasks 1, 2, 3 delegate/default; Task 10 Step 5 runs full enterprise suite.
- Config/guard/secret consolidation: Task 9.
- Dead-code removal + hermetic tests: Tasks 5 (stub), 10.
