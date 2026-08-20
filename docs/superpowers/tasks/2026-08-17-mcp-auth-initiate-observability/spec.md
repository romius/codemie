# Spec: MCP OAuth2 Initiate and Discovery Observability

**Ticket:** EPMCDME-14226
**Status:** Draft — pending approval

## Problem

`src/codemie/enterprise/mcp_auth/_initiate.py` has zero log statements; neither do `router.py` nor
`_uri.py` in the same package. Initiate is the hop that chooses `client_id`, `redirect_uri`,
`resource`, `scope`, mints `state`, and writes the PKCE entry — every value an authorization server
can reject an authorize request over. The callback side is densely instrumented, but that doesn't
help in two situations:

- A callback that never arrives leaves no server-side record — indistinguishable in logs from "the
  user never clicked Authenticate."
- A callback that fails before `state` verification can't be joined to its initiate: the entry log
  only records `has_code`/`has_state`/`has_error`; the line carrying `auth_config_id` is reached only
  after verification succeeds.

Net effect: EPMCDME-14215 (the callback timeout race) cannot be diagnosed from logs, nor even
counted.

## Scope

**In scope — `codemie`:**
- `src/codemie/enterprise/mcp_auth/_common.py`
- `src/codemie/enterprise/mcp_auth/_initiate.py`
- `src/codemie/enterprise/mcp_auth/_discovery.py`
- `src/codemie/enterprise/mcp_auth/_oauth2_callback.py`
- `src/codemie/enterprise/mcp_auth/_diagnostics.py`
- `src/codemie/enterprise/mcp_auth/router.py` — threads `mcp_config_id` into the static and SAML
  initiate builders; required by AC1, which lists `mcp_config_id` on every initiate log line.
- `tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py`
- `tests/enterprise/mcp_auth/test_oauth2_callback_bridge.py`
- `tests/enterprise/mcp_auth/test_saml_initiate_bridge.py`,
  `tests/enterprise/mcp_auth/test_discovery_probe_bridge.py` — pre-existing bridge tests whose stubs
  the widened `build_saml_initiate_response` signature and the new `resolution.auth_config_id` read
  force to be realigned.

**In scope — `codemie-ui`:**
- `src/hooks/useAuthCallbackListener.ts`
- `src/hooks/__tests__/useAuthCallbackListener.test.tsx`

**Out of scope:**
- Fixing EPMCDME-14215 itself — this ticket only makes it diagnosable.
- Changing the UI timeout value or its tracking behavior (EPMCDME-<T3>).
- Any change to `codemie-enterprise` — the authorize URL and the discovered-flow snapshot already
  expose every value this ticket needs; a package release is not justified.
- Listing a user's stored credential ids at gate time (would need a new TMS read-by-user method —
  deferred, separate follow-up if reproduction points at orphaned rows).

## Design

### Shared helpers (`_common.py`)

New home for logging utilities shared across the package:

- **`_sanitize_log_value`** — moved here from its current file (the existing CWE-117 sanitizer).
  Every user-controlled or endpoint-derived string passed to a log call in this change goes through
  it.
- **`_log_state_prefix(state: str) -> str`** — returns a short, non-reversible prefix of `state`.
  Used by both initiate and callback so the two sides are joinable in logs without ever emitting the
  full `state` value.
- **`_describe_authorize_request(url: str) -> dict`** — parses the authorize URL via
  `urllib.parse.parse_qsl` into `client_id`, `redirect_uri`, `resource`, `scope`, and the
  authorize-endpoint host. Must not raise on a malformed URL (AC8) — returns a best-effort/degraded
  description (missing fields as `None`) instead of propagating a parse error, so a logging call
  can never break the flow being observed.
- **`_raise_client_error`** (separate commit, item E2) — adds a WARNING with the sanitized reason
  before raising, closing ~15 currently-silent 400 sites across the package. Kept as its own commit
  so it can be reverted independently if log volume becomes a concern.

### `_initiate.py`

Every initiate path — static `auth_config`, discovered, recovery, SAML — emits one structured INFO
log immediately after the authorize request is built (client_id/redirect_uri/resource/scope/state
already resolved at that point), carrying:

flow kind, `mcp_config_id`, `auth_config_id`, `discovered_flow_id`, `user_id`,
`_log_state_prefix(state)`, and the fields from `_describe_authorize_request` (client_id,
authorize-endpoint host, redirect_uri, resource, scope). *(AC1)*

Discovered flows additionally log `issuer`, `registration_method`, `registration_reason_code`
(`dcr_cache_hit` / `dcr_cache_miss` / `dcr_profile_mismatch`), `registration_profile_fingerprint` —
sourced from the discovered-flow snapshot the enterprise builder already returns. *(AC2)*

The discovered-snapshot self-heal branch logs at WARNING with the requested vs. resolved
`discovered_flow_id`, so a snapshot swapped underneath the UI is detectable. Recovery-path
exhaustion likewise logs at WARNING. *(AC3)*

### `_discovery.py`

After the resolved snapshot is stored (existing call site), log its provenance once per resolution
at INFO. The `config_error` branch — currently silent — logs at WARNING with `attempted_mechanisms`
and `failure_reasons`. *(AC4)*

### `_oauth2_callback.py`

The entry log and the identity-provider-error branch switch from their current ad-hoc prefix logic
to `_log_state_prefix`, so a line from either side of the flow is joinable to its initiate by the
same value, including when verification fails before `auth_config_id` is available. *(AC5)*

### `_diagnostics.py`

Widen the `result` field's `Literal` to add `"timeout"`. Add optional `waited_ms` and `phase`
fields. Branch severity: WARNING for `result="timeout"`, using a message string distinct from the
bridge-failure messages (a client abandonment must not share a log signature with a bridge error).
Sanitizer import moves to come from `_common`. *(AC6, AC9)*

### `codemie-ui`: `useAuthCallbackListener.ts`

`onIdTimeout` (current lines ~131-144) sends a fire-and-forget POST to the existing
`/v1/mcp-auth/oauth2/callback-diagnostics` endpoint with `result: "timeout"` and the elapsed wait.
The call:
- Never blocks, delays, or alters the existing timeout behavior (fired after the timeout path is
  already committed to firing).
- Swallows its own errors — a failed beacon is never surfaced to the user and never retried.
- Sends nothing on the success or identity-provider-error paths — only on timeout. *(AC6)*

## No secrets logged (AC7)

Every logged string in this change is either a static literal, a sanitized value via
`_common._sanitize_log_value`, or a value already safe by construction (`_log_state_prefix`'s
output, `client_id`, endpoint host). Never logged, in this change or as a result of it:
`client_secret`, full `state`, `code_verifier`, access/refresh tokens, `session_binding_hash` value.

## Testing

- `tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py` — `caplog` assertions per initiate
  path for the INFO log's fields; WARNING assertions for the self-heal and recovery-exhaustion
  branches; a test asserting the full `state` value never appears in captured log output; a test
  with a malformed authorize URL asserting no exception propagates from the logging helper.
- `tests/enterprise/mcp_auth/test_oauth2_callback_bridge.py` — assertions that the callback's state
  prefix matches the initiate's for the same flow; `result=timeout` diagnostics assertions.
- `src/hooks/__tests__/useAuthCallbackListener.test.tsx` — beacon sent on timeout with `result` and
  elapsed wait; a throwing transport changes nothing observable (no re-throw, no behavior change);
  no beacon sent on the success or error paths.

## Quality gates

- `codemie`: `make ruff` → `make build` → `make license-check` → `make test` → `make gitleaks`
- `codemie-ui`: `npm run lint` → `npm run typecheck` → `npm run test:unit` → `npm run test:integration`

## Open decisions — resolved

- **Item E2** (`_raise_client_error` WARNING): **included**, as its own commit.
- **Deferred enterprise item** (listing stored credential ids at gate time): **not** pulled into
  this ticket — stays out of scope per the ticket.
