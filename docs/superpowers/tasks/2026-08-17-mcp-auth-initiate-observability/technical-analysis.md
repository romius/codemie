# Technical Research

**Task**: mcp_auth oauth2 initiate observability logging
**Generated**: 2026-08-17T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

Add MCP OAuth2 initiate and discovery observability.

Problem: `src/codemie/enterprise/mcp_auth/_initiate.py` contains zero log statements; so do `router.py` and `_uri.py` in the same package. Initiate is the hop that chooses `client_id`, `redirect_uri`, `resource` and `scope`, mints the `state`, and writes the PKCE entry — every value an authorization server rejects an authorize request over.

The callback side is densely instrumented, but that does not help in two situations:
- A callback that never arrives leaves no record at all. The backend cannot know the browser did not come back; the user's UI simply times out.
- A callback that fails before state verification cannot be joined to its initiate. The entry log records only `has_code`/`has_state`/`has_error`; the line that carries `auth_config_id` is reached only after verification succeeds. No value is logged by both sides.

Scope: all backend changes land in `codemie`'s `src/codemie/enterprise/mcp_auth/` bridge layer. No `codemie-enterprise` package release is required — the authorize URL returned by the enterprise builder already carries `client_id`, `redirect_uri`, `state`, `resource` and `scope` in its query string, and the discovered-flow snapshot already carries the client-registration provenance. One small change in `codemie-ui` covers the client-side timeout beacon.

Acceptance criteria (9):
1. Every initiate path (static `auth_config`, discovered, recovery, SAML) emits one structured INFO log carrying: flow kind, `mcp_config_id`, `auth_config_id`, `discovered_flow_id`, `user_id`, a `state` prefix, `client_id`, authorize-endpoint host, `redirect_uri`, `resource`, `scope`.
2. Discovered flows additionally log `issuer`, `registration_method`, `registration_reason_code` (`dcr_cache_hit`/`dcr_cache_miss`/`dcr_profile_mismatch`) and `registration_profile_fingerprint`.
3. The discovered-snapshot self-heal branch in `_initiate.py` logs at WARNING and includes the requested versus resolved `discovered_flow_id`.
4. `_discovery.py` logs the resolved snapshot's provenance once per resolution, and its `config_error` branch logs at WARNING including `attempted_mechanisms` and `failure_reasons`. Both are silent today.
5. The OAuth2 callback entry log and the identity-provider-error branch carry the same `state` prefix, so initiate and callback can be joined even when verification fails.
6. A callback that never arrives produces a server-side record: the Web UI reports `result=timeout` with the elapsed wait to the existing `/v1/mcp-auth/oauth2/callback-diagnostics` beacon endpoint, logged at WARNING under a message distinct from bridge failures. The beacon is fire-and-forget: it cannot alter, delay or break the existing timeout behaviour, and a failed beacon is not surfaced to the user.
7. No secrets are logged: no `client_secret`, no full `state`, no `code_verifier`, no access or refresh tokens, no `session_binding_hash` value. Every logged string passes through the existing CWE-117 sanitizer. A test asserts the full `state` does not appear in captured log output.
8. A malformed authorize URL does not raise from the logging helper — the flow being observed must be unaffected by observation. Covered by a test.
9. Log severities follow `.ai-run/guides/development/logging-patterns.md`: INFO for normal flow milestones, WARNING for expected client-side and validation failures, distinct messages per failure class.

Out of scope: fixing EPMCDME-<T1> itself (this ticket only makes it diagnosable); changing the UI timeout value or its tracking behaviour (EPMCDME-<T3>); adding logging to the `codemie-enterprise` package; listing a user's stored credential ids at gate time (would need a read-by-user TMS method that does not exist, deferred).

A full implementation plan with file/line anchors already exists at `local/mcp-auth/LOGGING-PLAN.md` (items A-F) and the ticket is at `local/mcp-auth/tickets/2-task-initiate-observability.md` — both were read and treated as primary sources, with their file/line claims verified against current source rather than trusted blindly (see Sections 2-4).

---

## 2. Codebase Findings

### Existing Implementations

All in `src/codemie/enterprise/mcp_auth/` (backend) plus one hook in `codemie-ui`:

- `_initiate.py` (469 lines, read in full) — zero `logger.` calls anywhere in the file, confirmed directly. Contains:
  - `build_oauth2_initiate_response` (lines 50-99) — static `auth_config` flow.
  - `build_discovered_oauth2_initiate_response` (102-199) — discovered flow; self-heal branch at 129-139 (`_probe is None` → `MCPToolkitService.ensure_discovered_snapshot_for_server`), currently no log on either the miss or the heal outcome.
  - `build_recovery_oauth2_initiate_response` (202-259) — recovery flow; two exhaustion exits (`exhausted_decision is not None` at 230, `except RecoveryAttemptsExhausted` at 248), both raise `MCPAuthenticationRequiredException` with no log today.
  - `_load_discovered_flow_snapshot_for_binding_or_error` (262-291).
  - `build_saml_initiate_response` (294-346) — SAML flow, no log.
  - `build_saml_metadata_response` (349-407), `build_discovered_auth_status_response` (410-468).
  - All four builder functions share the pattern: lazy `from codemie_enterprise.mcp_auth import ...` inside a `try`, `except ImportError` → `ExtendedHTTPException(503)`.
- `_common.py` (127 lines, read in full) — shared helpers used across the package: `CallbackPageError`, `MCPAuthEnterpriseUnavailableError`, `MCPPostAuth401Result`, `_raise_client_error` (line 69, raises 400 via `ExtendedHTTPException`, no log), `_candidate_string`, `_get_discovery_result_field`/`_get_discovery_candidate_field`, `_as_hostname_from_error_context`, `_is_discovered_auth_config_id`, `_build_discovered_initiate_url`, `_build_recovery_initiate_url`, `_build_discovered_config_error_payload`. Imports only `urlencode, urlsplit` from `urllib.parse` (no `parse_qsl`). No sanitizer function currently lives here.
- `_discovery.py` (297 lines, read in full) — `run_mcp_auth_parallel_discovery_probe`, `_build_discovery_bridge_unavailable_results`, `_build_discovered_failure_payload`, `_prepare_discovered_flow_resolution_config`, `_build_discovered_resolved_payload`, `_resolve_discovered_candidate_payload` (existing `logger.warning` at lines 215 and 218 for storage/resolution failures), `_select_discovered_candidate_pairs`, `build_mcp_auth_discovered_auth_gate_payloads` (existing `logger.warning` at line 274). The snapshot-store call (`deps._require_initialized_discovered_flow_store().store(resolution.snapshot)`) is at line 213, with no success-path log after it. The `resolution.status == "config_error"` branch (lines 221-226) returns a payload with no log.
- `_diagnostics.py` (85 lines, read in full) — `_LOG_UNSAFE_CHARS` regex + `_sanitize_log_value` (CWE-117 sanitizer, neutralizes CR/LF/control chars), `OAuth2CallbackDiagnostics` pydantic model (`result: Literal["success", "error"]`, `ConfigDict(extra="ignore")`, all string fields carry `max_length`), `build_oauth2_callback_diagnostics_response` — branches WARNING (error/no-opener/post_message_error) vs INFO, returns 204.
- `_oauth2_callback.py` — confirmed directly at lines 478-481: `logger.info("MCP OAuth2 callback received: has_code=... has_state=... has_error=...")`, no `state` field present. Lines 519-523: `logger.warning("MCP OAuth2 callback received identity provider error: error=... auth_config_id=... server_name=...")`, no `state` field either. `_store_callback_token` (447-467) already has a `logger.warning` on TMS store failure.
- `router.py` — `oauth2_callback_diagnostics_enabled` (lines 391-399) confirmed unauthenticated by design: no `authenticate` dependency, comment states "Unauthenticated on purpose: the bridge page that fires this beacon is itself unauthenticated."; disabled counterpart at lines 382-388 returns a `503`/`MCPAuthDisabledResponse` sentinel.
- `codemie-ui/src/hooks/useAuthCallbackListener.ts` (286 lines, read in full) — `onIdTimeout` at lines 131-144 exactly as referenced by the plan/ticket. Currently performs only `console.warn`, a `setAuthFlows` state update, and `onTimeoutRef.current?.(authConfigId)`. No `navigator.sendBeacon` or `fetch` call exists anywhere in the file today.

### Architecture and Layers Affected

- Enterprise integration/bridge layer (backend): `src/codemie/enterprise/mcp_auth/` — sits between the FastAPI router and the optional `codemie_enterprise.mcp_auth` package, which is imported lazily inside function bodies and guarded by `ImportError`/`MCPAuthEnterpriseUnavailableError` handling, and by an `is_mcp_auth_enabled()` feature-flag check in `_discovery.py`.
- API/router layer (backend): `router.py` — disabled/enabled route-pair pattern per endpoint; the `callback-diagnostics` route's request model lives in `_diagnostics.py`.
- Frontend hooks layer: `codemie-ui/src/hooks/useAuthCallbackListener.ts` — React hook with `useRef`-backed timeout tracking and a `window.addEventListener('message', ...)` listener.
- Test layer: `tests/enterprise/mcp_auth/` (pytest) and `codemie-ui/src/hooks/__tests__/` (Vitest).

### Integration Points

- `codemie_enterprise.mcp_auth` (external, optional, pinned dependency) — supplies `build_oauth2_initiate_response`, `build_recovery_oauth2_initiate_response`, `build_saml_initiate_response`, `OAuth2AuthConfig`/`SAMLAuthConfig`, `MCPAuthRedisUnavailable`, `RecoveryAttemptsExhausted`, `resolve_discovered_oauth2_flow`, `evaluate_discovered_auth_status`, and discovery-module helpers (`DiscoveryProbeCandidate`, `probe_discovery_eligible_servers`). Not directly inspected this session (out of scope per the ticket); its internal file/line references in `local/mcp-auth/LOGGING-PLAN.md` are documentation claims, not independently verified against that repository.
- `codemie.enterprise.mcp_auth.dependencies` (internal, accessed as `_deps`/`deps` module reference in both `_initiate.py` and `_discovery.py`) — supplies `build_redirect_uri`, `derive_resource_uri`, `_require_initialized_mcp_auth_components`, `_require_initialized_discovered_flow_store`, `_require_initialized_tms`, `_validate_discovered_snapshot_context`, `_mcp_auth_service`, `_mcp_auth_discovery_cache`, `_mcp_auth_dcr_credentials_cache`.
- `codemie.configs.logger` (module-level `logger`) and `codemie.configs.config` (`MCP_AUTH_DISCOVERY_CONCURRENCY_LIMIT`, `MCP_AUTH_ENFORCE_HTTPS`, `CALLBACK_API_BASE_URL`) — used directly in `_discovery.py`.
- `codemie.rest_api.models.mcp_config.MCPConfig`, `codemie.service.mcp.toolkit_service.MCPToolkitService` (lazily imported in `_initiate.py` to avoid an import cycle).
- `codemie.core.exceptions` (`ExtendedHTTPException`, `MCPAuthenticationRequiredException`) — the uniform error-raising mechanism across the package.
- Frontend: `@/utils/api` (`api.BASE_URL`), `@/store/appInfo` (`appInfoStore.getMcpAuthOrigin`, `getMcpAuthTimeoutSeconds`) — both mocked in the existing hook test.

### Patterns and Conventions

- Lazy/deferred import of the optional enterprise package inside each builder function body, wrapped in `try`/`except ImportError` → `ExtendedHTTPException(503, ...)` — repeated identically across all four `_initiate.py` builders and in `build_discovered_auth_status_response`/`build_saml_metadata_response`.
- `_raise_client_error(message, details, *, code=400)` in `_common.py` as the uniform client-error helper — currently silent (no log call inside it).
- CWE-117 sanitizer (`_sanitize_log_value` / `_LOG_UNSAFE_CHARS`) currently local to `_diagnostics.py`; used before any client-controllable string is placed into a log line.
- Existing log-line convention observed directly in `_oauth2_callback.py`, `_discovery.py`: `logger` from `codemie.configs.logger`, message text prefixed `"MCP OAuth2 …"`, `key=value` space-separated pairs, one line per event.
- Router pattern: paired disabled/enabled routes per endpoint, gated by import/feature-flag state, with a shared `_DISABLED_RESPONSE`/`MCPAuthDisabledResponse` sentinel for the disabled variant (observed directly in `router.py`).
- Frontend hook pattern: `useRef`-backed maps for timeout handles and tracked ids, ref-mirrored callback props (`onSuccessRef`, `onErrorRef`, `onTimeoutRef`) to avoid effect re-subscription, existing `console.info`/`console.warn` diagnostic calls already present client-side (not routed to the backend).

---

## 3. Documentation Findings

### Guides and Architecture Docs

`.ai-run/guides/development/logging-patterns.md` (37 lines, read in full) — the guide named directly by acceptance criterion 9. Three rules, each with an in-repo evidence citation:
- **Context In Messages** — log operation/IDs/status/sanitized details, not raw headers/tokens (evidence: `src/codemie/rest_api/security/authentication.py:114`).
- **Metric Failure Labels** — distinct label per failure class; security-relevant failures must never share a label with benign rejections (evidence: `src/codemie/triggers/bindings/webhook.py:398`).
- **Severity** — WARNING for expected validation failures, ERROR/exception logging reserved for server failures needing stack context (evidence: `src/codemie/rest_api/main.py:826`).

`Glob(".ai-run/guides/**")` returned 36 guide files across architecture/agents/api/data/integration/development/standards/testing/workflows categories. Only `logging-patterns.md` was read as directly on point for this ticket; others (e.g. `development/security-patterns.md`, `api/rest-api-patterns.md`) exist but were not read, since the task does not change route signatures or schemas beyond widening one existing `Literal`.

### Architectural Decisions

Two documents named directly in task_context, read in full and cross-checked against current source where the claims were checkable:

- `local/mcp-auth/LOGGING-PLAN.md` (197 lines) — records a placement decision (§2): backend items land in `codemie`'s bridge layer, not in `codemie-enterprise`, because (a) the authorize URL already carries every needed field in its query string and `DiscoveredOAuth2FlowSnapshot` already carries registration provenance, and (b) `codemie-enterprise` is pinned below its released version in `codemie/pyproject.toml`, so a package-side change costs a publish plus a pin bump. This session did not independently re-open `pyproject.toml` or the `codemie-enterprise` repo to re-verify the exact version numbers or the cited `oauth2_flow.py`/`discovered_flow.py` line ranges.
- `local/mcp-auth/tickets/2-task-initiate-observability.md` (88 lines) — the Jira-ready ticket text; scope table matches task_context exactly (`codemie`: `_common.py`, `_initiate.py`, `_discovery.py`, `_oauth2_callback.py`, `_diagnostics.py` + 2 test files; `codemie-ui`: 1 hook + 1 test file; `codemie-enterprise`: explicitly untouched).

The plan's specific line-anchor claims that were independently checked against current source this session all matched:
- `_oauth2_callback.py` lines 478 and 519 — confirmed present, confirmed missing a `state` field, exactly as the plan states.
- `router.py` lines 391-399 — confirmed unauthenticated `callback-diagnostics` route, exactly as the plan states.
- `useAuthCallbackListener.ts` `onIdTimeout` at lines 131-144 — confirmed exact span and confirmed no beacon call exists yet.
- Test harness fixtures claimed by the plan (`_build_app`, `_build_user`, `_build_mcp_config`, `_RecordingPKCEStore` in `test_oauth2_initiate_bridge.py`; `_build_enabled_client`/`_build_disabled_client`/`_build_auth_config`/`_build_mcp_config` in `test_oauth2_callback_bridge.py`; `dispatchMessage(origin, data)` at line 40 in the UI test) — all confirmed present at the cited locations.

The plan itself flags two decisions as still open (§7): whether to include its optional item E2 (a WARNING inside `_raise_client_error`, touching ~15 silent call sites package-wide), and whether to do a deferred `codemie-enterprise` item (a new read-by-user TMS method) now or later. Both are unresolved in the source documents, not resolved by this research.

### Derived Conventions

The `logger`/message-prefix/`key=value` log-line shape and the INFO-vs-WARNING severity split described under Patterns above were derived directly from the existing code in `_oauth2_callback.py`, `_discovery.py`, and `_diagnostics.py`, and are consistent with the guide's Severity and Metric Failure Labels rules.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py` — exists. First 80 lines confirmed harness: `_build_app()` (builds a `FastAPI` app with `enabled_router` + exception handlers), `_build_user(**overrides)`, `_build_mcp_config(...)`, `_RecordingPKCEStore` (records `(state, pkce_state)` tuples on `.store()`).
- `tests/enterprise/mcp_auth/test_oauth2_callback_bridge.py` — exists. First 60 lines confirmed harness: `_build_enabled_client()`, `_build_disabled_client()`, `_build_auth_config(...)`, `_build_mcp_config(...)`.
- `codemie-ui/src/hooks/__tests__/useAuthCallbackListener.test.tsx` — exists. First 50 lines confirmed `dispatchMessage(origin, data)` helper at line 40 exactly, `vi.useFakeTimers()` in `beforeEach`, mocked `@/utils/api` (`BASE_URL: 'https://api.example.com/v1'`) and `@/store/appInfo`.

### Testing Framework and Patterns

- Backend: pytest, FastAPI `TestClient` against the module's own `enabled_router`/`router` (disabled variant), exception handlers registered explicitly (`extended_http_exception_handler`, `mcp_auth_required_handler`).
- Frontend: Vitest + `@testing-library/react` (`renderHook`, `act`), fake timers, module-level `vi.mock` for `@/utils/api` and `@/store/appInfo`.

### Coverage Gaps

- The plan's claim that `test_oauth2_callback_bridge.py` already has "caplog precedent" was not independently confirmed — only the first 60 lines were read, and no `caplog` fixture usage appeared in that window.
- No test file was found targeting `_discovery.py` by name; discovery-side coverage, if any, would have to live inside one of the bridge test files above and was not confirmed either way this session.
- The `codemie-ui` test file's coverage of the not-yet-implemented beacon (timeout → `sendBeacon`/`fetch`) does not exist yet, since the beacon itself does not exist yet in `useAuthCallbackListener.ts`.

---

## 5. Configuration and Environment

### Environment Variables

- `MCP_AUTH_ENABLED` — gates the disabled/enabled router-pair pattern in `router.py` (referenced via `is_mcp_auth_enabled()`).
- `MCP_AUTH_DISCOVERY_CONCURRENCY_LIMIT`, `MCP_AUTH_ENFORCE_HTTPS`, `CALLBACK_API_BASE_URL` — referenced directly in `_discovery.py` via the `config` object.

### Configuration Files

No domain-specific config file beyond the standard `codemie.configs.config` attribute-access pattern observed in `_discovery.py`. `codemie/pyproject.toml` pins the `codemie-enterprise` package version; the exact pinned value was not re-read this session (see Section 3 caveat) — it is cited only in `local/mcp-auth/LOGGING-PLAN.md`.

### Feature Flags and Deployment Concerns

- `is_mcp_auth_enabled()` (from `_guards`) — used in `_discovery.py` to short-circuit the discovery probe when MCP auth is disabled.
- Router-level disabled/enabled pairing in `router.py` is the deployment-time toggle mechanism for every MCP-auth endpoint, including `/oauth2/callback-diagnostics`.
- `oauth2_callback_diagnostics_enabled` is intentionally unauthenticated (confirmed directly, lines 391-399) — any field added to `OAuth2CallbackDiagnostics` remains attacker-controllable input and must continue to pass through the sanitizer before being logged.

---

## 6. Risk Indicators

- `codegraph_explore` returned unrelated files (an unrelated `EnterpriseTool`/`enterprise_tool.py`, and `router.py`/`common.py` matches from other unrelated packages such as `user_preferences_router.py`, `project_budget_router.py`) for direct full-path queries against `_initiate.py`, `_common.py`, and `_discovery.py`, despite these being real, sizeable files in an indexed repo. Their verbatim source was obtained via direct `Read` instead. This is a tool-reliability caveat for this research session, not a codebase risk.
- Generic keyword dimension queries ("observability", "logging") surfaced an unrelated LLM-tracing subsystem (`src/codemie/enterprise/observability/`, a Langfuse/Phoenix `ObservabilityProvider`) that must not be conflated with this ticket's MCP-auth logging scope — the two "observability" domains share a name but nothing else.
- `_raise_client_error` in `_common.py` has zero logging across roughly 15 call sites spanning `_initiate.py`, `_oauth2_callback.py`, and `_post_auth.py`. The source plan itself lists adding a WARNING here as an open, undecided item (its own §7), not a settled requirement.
- `local/mcp-auth/LOGGING-PLAN.md`'s claims about `codemie-enterprise` internals (`oauth2_flow.py:338-353`, `discovered_flow.py:328-346`, and the exact pinned-vs-released version numbers) were not independently re-verified against that repository this session; `codemie-enterprise` is explicitly out of scope for this change, so this is a documentation-trust gap rather than a blocking one.
- `test_oauth2_callback_bridge.py`'s claimed existing "caplog precedent" was not confirmed beyond the file's first 60 lines.
- codegraph's own blast-radius signal (from earlier queries in this session) flagged several individual functions in this area (e.g. discovered-flow and callback-diagnostics related symbols) as having "no covering tests found," which sits alongside — and was not reconciled with — the confirmed existence of file-level test harnesses above; the discrepancy may simply reflect function-level granularity in that signal.
- The plan's two explicitly open decisions (§7: whether to add the `_raise_client_error` WARNING; whether to pull the deferred `codemie-enterprise` TMS method into this change) are unresolved in the source documents and could change file/commit count if decided differently during implementation.
- Speculative: acceptance criterion 2's `registration_method`/`registration_reason_code`/`registration_profile_fingerprint` fields depend on the external `DiscoveredOAuth2FlowSnapshot`'s exact attribute surface in `codemie_enterprise`, which was not directly inspected this session (deliberately out of scope). If any attribute name differs from what the plan assumes, the discovered-flow log line in `_discovery.py`/`_initiate.py` would need adjustment at implementation time.
- Speculative: moving `_sanitize_log_value` from `_diagnostics.py` into `_common.py` (as the plan proposes) is a cross-file rename/relocation touching an import in `_diagnostics.py`; nothing in this session's research found any other current importer of that symbol, but this was checked only by reading `_diagnostics.py` and `_common.py` directly, not by an exhaustive repo-wide reference search.

---

## 7. Summary for Complexity Assessment

The change is confined to one backend package (`src/codemie/enterprise/mcp_auth/`, five files: `_common.py`, `_initiate.py`, `_discovery.py`, `_oauth2_callback.py`, `_diagnostics.py`) plus two existing backend test files, and one frontend hook (`codemie-ui/src/hooks/useAuthCallbackListener.ts`) plus its existing test file — a bounded, previously-scoped surface with no new files, no new routes, and no schema/model changes beyond widening one `Literal` field (`OAuth2CallbackDiagnostics.result`) and adding two optional fields to it. All four layers touched (enterprise bridge, FastAPI router — comment/behavior only, React hook, and both test suites) were directly inspected this session, and every specific file/line claim made by the two source-of-truth planning documents that was checked against current source matched exactly, including three log-line locations, one route's auth posture, one React callback's line span, and the test harness fixtures in three separate test files.

Technical novelty is low: the change follows logging conventions (message prefix, `key=value` pairs, INFO/WARNING severity split, CWE-117 sanitizer) that already exist verbatim elsewhere in the same package, and the fire-and-forget beacon pattern reuses an already-unauthenticated, already-deployed endpoint. No new external dependency, no new persisted field, and no `codemie-enterprise` package release is implicated — that repository is deliberately untouched, with its provenance-field guarantees taken on documentation trust rather than independently re-verified this session (flagged above).

Risk is concentrated in three places: (1) two research-method gaps this session did not close — the external package's exact attribute surface and the second test file's caplog usage beyond line 60 — both bounded and low-blast-radius; (2) two decisions the planning documents themselves leave open (the `_raise_client_error` WARNING scope, and whether to pull a deferred enterprise-side method into this change), which could shift file/commit counts if resolved mid-implementation; and (3) the generic-keyword codegraph noise (an unrelated LLM-observability subsystem sharing a name) — a disambiguation risk for anyone re-deriving this research rather than a code risk. Test coverage posture is favorable: all three relevant test files and their harness fixtures already exist and were confirmed directly, so the task is additive-to-existing-tests rather than net-new test infrastructure.
