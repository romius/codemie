# MCP OAuth2 Initiate/Discovery Observability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the mcp_auth OAuth2 initiate/discovery bridge log every value an authorization
server can reject a request over, join initiate↔callback by a `state` prefix, and report
browser-side callback timeouts to the backend — with zero secrets ever logged and zero new
failure paths.

**Architecture:** Centralize three logging helpers (sanitizer, state-prefix, authorize-URL
descriptor) in `_common.py`; every initiate path and the discovery resolver call them and emit
one INFO/WARNING line each, matching the existing `logger`/`MCP OAuth2 …`/`key=value` convention
already in `_oauth2_callback.py` and `_discovery.py`. The UI reports a callback timeout to the
already-unauthenticated `/v1/mcp-auth/oauth2/callback-diagnostics` endpoint via a fire-and-forget
beacon.

**Tech Stack:** Python/FastAPI/pytest (`codemie`), TypeScript/React hook/Vitest (`codemie-ui`).

**Spec:** `docs/superpowers/tasks/2026-08-17-mcp-auth-initiate-observability/spec.md`

## Global Constraints

- Never log: `client_secret`, the full `state`, `code_verifier`, access/refresh tokens,
  `session_binding_hash` value. Every logged string is sanitized or safe by construction.
- A logging helper must never raise (AC8) — a malformed authorize URL degrades to `None` fields.
- Severities per `.ai-run/guides/development/logging-patterns.md`: INFO for milestones, WARNING
  for expected client/validation failures, a distinct message per failure class.
- `codemie-enterprise` is untouched by every task in this plan.
- Commit per task using the repo convention `EPMCDME-NNNNN: Capital description` (no conventional
  commit prefixes). One commit per task.
- Repos: `codemie` (this repo) for tasks 1–9, `codemie-ui` (sibling repo) for task 10. Both are
  already on branch `EPMCDME-14226`.

---

### Task 1: Shared logging helpers in `_common.py`

**Files:**
- Modify: `src/codemie/enterprise/mcp_auth/_common.py:20` (import), append new helpers
- Modify: `src/codemie/enterprise/mcp_auth/_diagnostics.py:17-38` (drop local sanitizer, import from `._common`)
- Test: `tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py`

**Interfaces:**
- Produces: `_sanitize_log_value(value: str | None) -> str | None` (moved, unchanged body),
  `_log_state_prefix(state: str | None) -> str | None`, `_describe_authorize_request(auth_url: str) -> dict[str, str | None]`

Move `_sanitize_log_value`/`_LOG_UNSAFE_CHARS` verbatim from `_diagnostics.py:26-38` into
`_common.py`; `_diagnostics.py` imports it from `._common` instead. Add `parse_qsl` to the
existing `urllib.parse` import in `_common.py:20`.

```python
def _log_state_prefix(state: str | None) -> str | None:
    """First 12 chars of an OAuth2 state — enough to join initiate<->callback, never reversible."""
    return state[:12] if isinstance(state, str) else None


def _describe_authorize_request(auth_url: str) -> dict[str, str | None]:
    """Loggable, non-secret view of an authorize URL. Never raises (AC8) — malformed input
    degrades to all-None fields instead."""
    fields = {"as_host": None, "client_id": None, "redirect_uri": None, "resource": None, "scope": None}
    try:
        parts = urlsplit(auth_url)
        query = dict(parse_qsl(parts.query))
        fields["as_host"] = _sanitize_log_value(parts.netloc) or None
        for key in ("client_id", "redirect_uri", "resource", "scope"):
            fields[key] = _sanitize_log_value(query.get(key))
    except ValueError:
        pass
    return fields
```

- [ ] Write failing tests in `test_oauth2_initiate_bridge.py`: `_describe_authorize_request` on a
  malformed URL (e.g. `"not a url::"`) returns all-`None` values without raising;
  `_log_state_prefix` on a 40-char state returns exactly its first 12 chars and the return value
  never equals the input.
- [ ] Run `pytest tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py -k describe_authorize_request or log_state_prefix -v`; expect FAIL (`ImportError`/`AttributeError`).
- [ ] Implement as above; move sanitizer; update `_diagnostics.py` import.
- [ ] Re-run; expect PASS.

Test-first: yes — `_describe_authorize_request` never raises on a malformed URL; `_log_state_prefix` truncates and never returns the full input.

---

### Task 2: `_diagnostics.py` — support `result="timeout"`

**Files:**
- Modify: `src/codemie/enterprise/mcp_auth/_diagnostics.py:52` (Literal), `:53-60` (new fields), `:63-85` (severity branch)
- Test: `tests/enterprise/mcp_auth/test_oauth2_callback_bridge.py`

**Interfaces:**
- Consumes: `_sanitize_log_value` from Task 1.
- Produces: `OAuth2CallbackDiagnostics.result: Literal["success", "error", "timeout"]`, new optional
  `waited_ms: int | None` (`ge=0, le=3_600_000`), `phase: str | None` (`max_length=64`).

Widen the `Literal` at line 52; add the two `Field`s (same `Field`/`ConfigDict` style as the
existing fields at lines 53-60). In `build_oauth2_callback_diagnostics_response` (lines 63-85),
branch `result == "timeout"` to its own WARNING using a message distinct from the existing
`"MCP OAuth2 callback client diagnostics: …"` line, e.g.
`"MCP OAuth2 callback never observed by client: auth_config_id={} waited_ms={} phase={} opener_present={} target_origin={}"`,
each value sanitized. Existing success/error branch logic (lines 81-84) is otherwise unchanged.

- [ ] Write failing test in `test_oauth2_callback_bridge.py`: POST with `result="timeout"`,
  `waited_ms=45000`, `phase="awaiting_callback"` returns 204 and `caplog` (WARNING level) contains
  the distinct `"never observed by client"` message, not the existing diagnostics message; a
  second test posts `waited_ms=4_000_000` (over the `3_600_000` ceiling) and asserts 422.
- [ ] Run; expect FAIL (`Literal` rejects `"timeout"` with 422, or message text mismatch).
- [ ] Implement as above.
- [ ] Re-run; expect PASS.

Test-first: yes — `result="timeout"` logs the distinct WARNING message and 204s; an out-of-range `waited_ms` 422s.

---

### Task 3: `_initiate.py` — static `auth_config` INFO log (AC1, AC7)

**Files:**
- Modify: `src/codemie/enterprise/mcp_auth/_initiate.py:50-99` (`build_oauth2_initiate_response`)
- Test: `tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py`

**Interfaces:** Consumes `_log_state_prefix`, `_describe_authorize_request` from Task 1.

After the enterprise builder returns (state/auth_url resolved), log one INFO line before
returning:
`"MCP OAuth2 initiate issued: flow=static mcp_config_id={} auth_config_id={} user_id={} state={_log_state_prefix(state)} as_host={} client_id={} redirect_uri={} resource={} scope={}"`
using `_describe_authorize_request(auth_url)` for the last five fields.

- [ ] Write failing test: static-flow request → `caplog` (INFO) contains `flow=static`,
  `client_id=`, `redirect_uri=`, and `state=` equal to exactly the first 12 chars of the real
  `state`. A second test in the same case asserts the full `state` value (read from the response's
  `auth_url`) does NOT appear anywhere in `caplog.text` (AC7).
- [ ] Run; expect FAIL (no such log line exists today).
- [ ] Implement as above.
- [ ] Re-run; expect PASS.

Test-first: yes — static initiate emits the INFO line with a 12-char state prefix; the full state never appears in captured logs.

---

### Task 4: `_initiate.py` — discovered flow INFO + self-heal WARNING (AC1, AC2, AC3)

**Files:**
- Modify: `src/codemie/enterprise/mcp_auth/_initiate.py:102-199` (`build_discovered_oauth2_initiate_response`; heal branch at `:129-139`)
- Test: `tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py`

After the builder returns, log one INFO carrying flow/mcp_config_id/`snapshot.discovered_auth_id`/
`snapshot.discovered_flow_id`/`requested_flow_id`/user_id/state prefix/the `_describe_authorize_request`
fields/`snapshot.issuer`/`snapshot.registration_method`/`snapshot.registration_reason_code`/
`snapshot.registration_profile_fingerprint`. In the heal branch (`_probe is None`, lines 129-139),
add a WARNING before proceeding: `"MCP OAuth2 initiate: discovered snapshot missing, heal
attempted: mcp_config_id={} requested_flow_id={} user_id={} new_flow_id={}"`.

- [ ] Write failing tests: discovered-flow request → INFO contains `registration_reason_code=` and
  `snapshot_healed=false`; a second test that forces the self-heal branch (missing snapshot) →
  WARNING contains both the requested and the new `discovered_flow_id`.
- [ ] Run; expect FAIL.
- [ ] Implement as above.
- [ ] Re-run; expect PASS.

Test-first: yes — discovered initiate emits the INFO with registration fields; the heal branch emits the WARNING with requested-vs-new flow id.

---

### Task 5: `_initiate.py` — recovery flow INFO + exhaustion WARNING (AC1, AC3)

**Files:**
- Modify: `src/codemie/enterprise/mcp_auth/_initiate.py:202-259` (`build_recovery_oauth2_initiate_response`; exhaustion at `:230` and `:248`)
- Test: `tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py`

On success, log INFO with `flow=recovery`, `recovery_flow_id`, and the standard
`_describe_authorize_request` fields. On both exhaustion exits (`exhausted_decision is not None`
at line 230, `except RecoveryAttemptsExhausted` at line 248) — today both raise
`MCPAuthenticationRequiredException` silently — add a WARNING before raising:
`"MCP OAuth2 recovery initiate exhausted: mcp_config_id={} recovery_flow_id={} user_id={}
source={precheck|exception}"`.

- [ ] Write failing tests: successful recovery initiate → INFO with `flow=recovery`; a precheck
  exhaustion and an exception-path exhaustion each → WARNING with `source=precheck` /
  `source=exception` respectively.
- [ ] Run; expect FAIL.
- [ ] Implement as above.
- [ ] Re-run; expect PASS.

Test-first: yes — recovery success emits the INFO line; both exhaustion exits emit the WARNING with the correct `source`.

---

### Task 6: `_initiate.py` — SAML initiate INFO (AC1)

**Files:**
- Modify: `src/codemie/enterprise/mcp_auth/_initiate.py:294-346` (`build_saml_initiate_response`)
- Test: `tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py`

After the SAML `auth_url` is built, log one INFO:
`"MCP SAML initiate issued: auth_config_id={} user_id={} idp_host={} acs_url={}"` (`idp_host` via
`urlsplit(auth_url).netloc`, sanitized).

- [ ] Write failing test: SAML initiate request → INFO contains `idp_host=` and `acs_url=`.
- [ ] Run; expect FAIL.
- [ ] Implement as above.
- [ ] Re-run; expect PASS.

Test-first: yes — SAML initiate emits the INFO line with idp_host and acs_url.

---

### Task 7: `_discovery.py` — resolution INFO + `config_error` WARNING (AC2, AC4)

**Files:**
- Modify: `src/codemie/enterprise/mcp_auth/_discovery.py:213` (after snapshot store), `:221-226` (`config_error` branch)
- Test: `tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py` (discovery is exercised through the discovered-initiate path; no dedicated `_discovery.py` test file exists)

Immediately after the existing `deps._require_initialized_discovered_flow_store().store(...)`
call at line 213, log one INFO with `resolution.status`, `mcp_config_id`, `mcp_config_name`,
`resolution.auth_config_id`, `discovered_flow_id`, `user_id`, `issuer`, `as_hostname`,
`canonical_resource`, `registration_method`, `registration_reason_code`,
`registration_profile_fingerprint` — sourced from the resolved snapshot, once per resolution. In
the `resolution.status == "config_error"` branch (lines 221-226), add a WARNING with
`attempted_mechanisms` and `failure_reasons` read from `resolution.error_context`.

- [ ] Write failing tests: a discovered-initiate call that resolves successfully → INFO contains
  `registration_reason_code=`; a discovered-initiate call that resolves to `config_error` → WARNING
  contains `attempted_mechanisms=` and `failure_reasons=`.
- [ ] Run; expect FAIL.
- [ ] Implement as above.
- [ ] Re-run; expect PASS.

Test-first: yes — successful resolution emits the INFO once; `config_error` emits the WARNING with attempted_mechanisms/failure_reasons.

---

### Task 8: `_oauth2_callback.py` — join callback to initiate via state prefix (AC5)

**Files:**
- Modify: `src/codemie/enterprise/mcp_auth/_oauth2_callback.py:478` (entry log), `:519` (IdP-error branch)
- Test: `tests/enterprise/mcp_auth/test_oauth2_callback_bridge.py`

**Interfaces:** Consumes `_log_state_prefix` from Task 1.

Add `state={_log_state_prefix(state)}` to both the entry log at line 478 (currently only
`has_code`/`has_state`/`has_error`) and the IdP-error branch at line 519 (currently
`error`/`auth_config_id`/`server_name`), so either line is joinable to its initiate even when
verification fails before line 544 (where `auth_config_id` first becomes available).

- [ ] Write failing test: drive a known `state` through initiate then callback; assert the
  callback entry log's `state=` prefix equals `_log_state_prefix` of the same state; a second test
  drives an IdP-error callback and asserts its `state=` prefix is present and correct.
- [ ] Run; expect FAIL (no `state=` field on either line today).
- [ ] Implement as above.
- [ ] Re-run; expect PASS.

Test-first: yes — the callback entry log and the IdP-error branch both carry the same state prefix as the initiate that started the flow.

---

### Task 9: `_common.py` — `_raise_client_error` WARNING (item E2, separate commit)

**Files:**
- Modify: `src/codemie/enterprise/mcp_auth/_common.py:69-70` (`_raise_client_error`)
- Test: `tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py`

Kept as its own commit so it can be reverted independently of tasks 1–8 if log volume becomes a
concern (spec's resolved decision). Add one WARNING before the existing `raise`:
`"MCP auth client error: {message} details={_sanitize_log_value(details)}"`. This closes ~15
currently-silent 400 sites across `_initiate.py`, `_oauth2_callback.py`, `_post_auth.py` — no
call-site changes needed, they all route through this one function.

- [ ] Write failing test: trigger any existing 400 path that calls `_raise_client_error` (e.g. an
  invalid static-initiate request) and assert `caplog` (WARNING) contains
  `"MCP auth client error:"` and the sanitized details.
- [ ] Run; expect FAIL (no log call exists today).
- [ ] Implement as above.
- [ ] Re-run; expect PASS.

Test-first: yes — any `_raise_client_error` call site now emits the WARNING before raising the 400.

---

### Task 10: `codemie-ui` — fire-and-forget timeout beacon (AC6)

**Files:**
- Modify: `codemie-ui/src/hooks/useAuthCallbackListener.ts:131-144` (`onIdTimeout`)
- Test: `codemie-ui/src/hooks/__tests__/useAuthCallbackListener.test.tsx`

**Interfaces:** Consumes `api.BASE_URL` (`@/utils/api`, already imported/mocked in this file).

Inside `onIdTimeout`, alongside (not replacing) the existing `console.warn` /
`setAuthFlows`/`onTimeoutRef.current?.(...)` calls, fire a POST to
`${api.BASE_URL}/v1/mcp-auth/oauth2/callback-diagnostics` with body
`{result: 'timeout', auth_config_id, opener_present: false, waited_ms: <resolved timeout ms>,
phase: 'awaiting_callback'}`, via `navigator.sendBeacon` when available, else
`fetch(url, {method: 'POST', keepalive: true, headers: {'content-type': 'application/json'}, body})`.
Wrap the whole call in `try/catch`; never `await`, never re-throw, never retry — a failed beacon
must not change the existing timeout behavior or be surfaced to the user. Do not modify the
timeout duration or the existing `clearTimeout`/state-update logic — only add the beacon call.

- [ ] Write failing tests in `useAuthCallbackListener.test.tsx` (extend existing `dispatchMessage`
  harness): on a fake-timer-driven timeout, a beacon (mocked `navigator.sendBeacon` or `fetch`) is
  called exactly once with `result: 'timeout'` and the resolved `waited_ms`; when the transport
  mock throws/rejects, `authFlows` state and `onTimeout` callback invocation are unchanged from
  today's behavior; on the success and identity-provider-error paths, the beacon is never called.
- [ ] Run `npm run test:unit -- useAuthCallbackListener`; expect FAIL (no beacon call exists yet).
- [ ] Implement as above.
- [ ] Re-run; expect PASS.

Test-first: yes — beacon fires once on timeout with `result: 'timeout'`; a throwing transport changes no observable behavior; no beacon on success/error paths.

---

## Self-Review

**Spec coverage:** AC1 → Tasks 3–6; AC2 → Tasks 4, 7; AC3 → Tasks 4, 5; AC4 → Task 7; AC5 → Task
8; AC6 → Tasks 2, 10; AC7 → Tasks 1, 3 (design constraint honored in every task: only sanitized or
safe-by-construction values are ever formatted into a log line); AC8 → Task 1; AC9 → severity
choices stated per task, matching `logging-patterns.md`.

**Negative constraints:**
- "No secrets logged" / "no full `state`" — Task 1 makes `_log_state_prefix` non-reversible by
  construction; Task 3 asserts the full state is absent from captured logs; no task ever passes
  `client_secret`, `code_verifier`, a token, or `session_binding_hash`'s value to a log call.
- "Logging helper must not raise" (AC8) — Task 1's `_describe_authorize_request` catches
  `ValueError` and degrades to `None` fields instead of propagating; no task lets a log call raise
  into the request path.
- "Beacon cannot alter, delay, or break the existing timeout behaviour; a failed beacon is not
  surfaced to the user" — Task 10 fires the beacon fire-and-forget, inside its own `try/catch`,
  without `await`, alongside (not before/gating) the existing state update, and leaves the
  timeout's own duration and clear logic untouched.
- "No codemie-enterprise changes" — no task in this plan modifies any file outside `codemie`'s
  `src/codemie/enterprise/mcp_auth/`+tests or `codemie-ui`'s hook+test.
- "Not fixing EPMCDME-14215 itself" / "not changing the UI timeout value or tracking behaviour" —
  every task only adds observability (log lines, a beacon call); no task changes control flow,
  timeout duration, or retry/backoff behavior.
- "Deferred TMS read-by-user method" — no task added for it.

**Type/name consistency:** `_log_state_prefix`, `_describe_authorize_request`, and
`_sanitize_log_value` are defined once in Task 1 and referenced by identical name/signature in
Tasks 3–9; `OAuth2CallbackDiagnostics.waited_ms`/`.phase` defined in Task 2 and referenced by the
same names in Task 10's request body.
