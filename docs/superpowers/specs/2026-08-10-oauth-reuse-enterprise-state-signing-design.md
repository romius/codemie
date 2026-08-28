# Design: reuse enterprise PKCE store + state signing for tool OAuth

**Date:** 2026-08-10
**Ticket:** EPMCDME-13527
**Status:** approved (brainstorming)

## Context

Tool OAuth (GitLab / Jira / Confluence per-user connect) currently ships two custom base-side
components:

- `src/codemie/service/oauth_state_signing.py` — a from-scratch HMAC-SHA256 signer for the OAuth
  `state` parameter (b64url payload + signature, ts window check).
- `src/codemie/service/oauth/state_store.py` — a custom encrypted Redis store (`OAuthStateStore` +
  `GitLabOAuthStateStore` / `AtlassianOAuthStateStore`) that holds the PKCE verifier, the app
  credentials, and the flow result.

The enterprise MCP OAuth path already provides, and exports, equivalent machinery:
`encode_oauth2_state` / `decode_and_verify_oauth2_state`, `OAuth2StatePayload`, `PKCEStateData`,
`RedisPKCEStore`, and `RedisEncryption` (which derives both an encryption key and a `signing_key`
from a secret). The base already builds `RedisEncryption(config.MCP_AUTH_HMAC_SECRET)` and a
`RedisPKCEStore` for MCP in `codemie/enterprise/mcp_auth/dependencies.py`.

Maintaining a second signing stack and a second encrypted state store duplicates encryption, key
rotation, namespace, and TTL policy, and risks divergent security behavior between the two OAuth
paths. This change consolidates tool OAuth onto the enterprise primitives.

The obstacle is shape: `OAuth2StatePayload` and `PKCEStateData` are MCP-shaped — they require
`session_binding_hash` and carry no `provider` / `redirect_uri` / `nonce`; and both
`encode/decode` and `RedisPKCEStore` are hardwired to those exact models.

## Goals

1. Tool OAuth signs its `state` with the enterprise signing primitive (one HMAC/key-rotation path).
2. Tool OAuth stores its in-flight PKCE verifier in the enterprise `RedisPKCEStore`.
3. App credentials (`client_id` / `client_secret`) never live in the PKCE record or the signed
   state; they sit in a separate encrypted snapshot keyed by state.
4. Enterprise MCP behavior is unchanged (additive/delegating changes only).

## Scope

In scope: state signing, the PKCE/verifier store, and credential handling for tool OAuth.

Out of scope (separate follow-ups): an explicit persist/test flag on the flow; removing the result
store as a token transport; decoupling Setting creation from the OAuth flow; frontend test UX. The
result store (`store_result` / `get_result`) is therefore retained as-is in this change.

## Decisions

1. **Generic enterprise primitives**, not extended MCP models. Add payload-agnostic signing helpers
   and one generic field on `PKCEStateData`; leave MCP models otherwise unchanged.
2. **Separate encrypted creds snapshot** keyed by state. App credentials never enter the
   PKCE record or the signed state.

## Enterprise changes (`codemie-enterprise`) — MCP behavior unchanged

### E1. `mcp_auth/oauth2_flow.py` — payload-agnostic signing
Add:

```python
def encode_signed_state(payload: dict[str, Any], signing_key: bytes) -> str: ...
def decode_and_verify_signed_state(state: str, signing_key: bytes) -> dict[str, Any]: ...
```

- `encode_signed_state` = current `encode_oauth2_state` body but over a `dict`
  (`json.dumps(payload, sort_keys=True, separators=(",",":"))` → b64url → HMAC-SHA256 → `payload.sig`).
- `decode_and_verify_signed_state` = current decode body but returns the parsed `dict` (no
  `OAuth2StatePayload.model_validate`), raising `ValueError(_INVALID_OAUTH2_CALLBACK_STATE)` on any
  tamper/format error.
- Refactor the existing MCP functions to delegate:
  `encode_oauth2_state(p, k) = encode_signed_state(p.model_dump(), k)`;
  `decode_and_verify_oauth2_state(s, k) = OAuth2StatePayload.model_validate(decode_and_verify_signed_state(s, k))`.
  Output is byte-identical → existing MCP tests must remain green.
- Export both new helpers from `mcp_auth/__init__.py` and the package `__init__` lazy exports.

### E2. `mcp_auth/models.py` — generic PKCE context
- `session_binding_hash: str | None = None` (was required `str`).
- Add `context: dict[str, str] = Field(default_factory=dict)`.
- MCP callers keep passing `session_binding_hash` and ignore `context`; behavior unchanged.
- Update `tests/mcp_auth/test_models.py` exact-dump assertion(s) to include `context` and the now
  optional `session_binding_hash`.

### E3. `mcp_auth/redis_pkce_store.py` — non-consuming read
- Add `def peek(self, state: str) -> PKCEStateData | None` — same decrypt path as `consume` but with
  `GET` instead of `GETDEL`. Needed so tool `get_status` can report "pending" without destroying the
  in-flight record. No change to `store` / `consume`.

### E4. Version
- Bump `pyproject.toml` and `__version__` `2.3.38 → 2.3.39`.

## Base changes (`codemie`)

### B1. Delete custom signer
- Remove `src/codemie/service/oauth_state_signing.py`.
- Remove `OAUTH_STATE_SIGNING_SECRET` and `OAUTH_STATE_SIGNING_MAX_AGE_SECONDS` from `config.py`.
- Signing key = `RedisEncryption(config.MCP_AUTH_HMAC_SECRET).signing_key` (shared with MCP).
- Keep a small **base-side semantic validator** operating on the decoded dict (the enterprise HMAC
  layer only proves integrity, not tool semantics):
  - `ts` window: reject `now - ts > max_age` and `ts > now + skew` (`max_age` and `skew` are module
    constants, e.g. 600s / 5s).
  - `provider` in the adapter's allowlist.
  - `integration_id` in the signed state must equal the one bound to the flow.
  - required fields present (`user_id`, `redirect_uri`, `nonce`).

### B2. Replace the pending-state store
Replace `state_store.py`'s pending-state responsibility (`store_state` / `consume_state` /
`get_pending_state`) with two enterprise-backed stores, built from the shared
`RedisEncryption(config.MCP_AUTH_HMAC_SECRET)` and the app's redis client:

- **PKCE verifier** — enterprise `RedisPKCEStore` (namespace e.g. `tool_oauth`). Record:
  `PKCEStateData(code_verifier=…, user_id=…, auth_config_id=integration_id, session_binding_hash=None,
  context={"provider":…, "instance_url":…, "redirect_uri":…})`. `context` holds only non-secret
  values. `store(state, …)` on initiate; `consume(state)` on callback; `peek(state)` in `get_status`.
- **Creds snapshot** — a thin base wrapper over enterprise `RedisEncryption` (`encrypt`/`decrypt`),
  keyed by `state` (hashed), TTL matching PKCE. Holds `{client_id, client_secret}`. Written on
  initiate, consumed (get-and-delete) on callback.

The **result store** (`store_result` / `get_result`) is retained (renamed to a focused
`OAuthResultStore` if convenient) — untouched behavior, out of scope for this change.

### B3. `flow_engine.py`
- `initiate_flow`: build the tool state dict `{v:1, provider, integration_id, user_id, redirect_uri,
  nonce, ts}`; sign with `encode_signed_state(…, signing_key)`; write the PKCE record + creds
  snapshot.
- `handle_callback`: `decode_and_verify_signed_state` → base semantic validation → `consume` PKCE +
  creds; then exchange/persist as today.
- `get_status`: use `RedisPKCEStore.peek` for the pending check; result store unchanged.

### B4. Provider adapters
- `build_state_data` (GitLab + Atlassian): stop returning `client_id` / `client_secret` in the shared
  dict. Non-secret context (`instance_url`, `provider`, `redirect_uri`) goes to the PKCE `context`;
  creds go to the creds snapshot. The exchange step reads creds from the consumed creds snapshot.

### B5. Startup guard
- The state-signing startup guard requires `MCP_AUTH_HMAC_SECRET` (≥32 bytes) when any tool OAuth
  provider is enabled (drop the separate `OAUTH_STATE_SIGNING_SECRET`). Keep
  `assert_token_vault_available`.

## Testing

- **Enterprise:** existing `tests/mcp_auth/test_oauth2_flow.py`, `test_redis_pkce_store.py`,
  `test_models.py` must stay green (delegation byte-identical; new fields defaulted). Add: generic
  `encode_signed_state`/`decode_and_verify_signed_state` round-trip + tamper; `PKCEStateData` optional
  `session_binding_hash` + `context`; `RedisPKCEStore.peek`.
- **Base:** delete/rewrite `test_signed_state.py` (now: base semantic validator + enterprise signing,
  incl. the future-timestamp rejection case); update `test_flow_engine.py` and the state-store tests
  to the two enterprise-backed stores; keep the `tms_stub` / hermetic pattern and add a
  `RedisPKCEStore`/`RedisEncryption` stub so base OAuth tests don't require the installed enterprise
  package.

## Risks / mitigations

| Risk | Mitigation |
|---|---|
| MCP regression from touching `oauth2_flow.py` / `models.py` | Delegation is byte-identical; new model fields optional/defaulted; the full MCP suite is the guard. |
| `MCP_AUTH_HMAC_SECRET` now also gates tool OAuth | Startup guard updated; documented; local dev already sets it. |
| `get_status` pending detection needs a non-consuming read | Add `RedisPKCEStore.peek` (additive). |
| Enterprise not installed in base test env | Extend the hermetic stub to cover `RedisEncryption`/`RedisPKCEStore`/generic signing. |
