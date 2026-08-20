# Technical Research

**Task**: mcp oauth auth callback timeout
**Generated**: 2026-08-17
**Research path**: codegraph

---

## 1. Original Context

Fix MCP OAuth2 interactive auth callback timeout race across two repos.
PRIMARY repo codemie-ui (/home/taras_spashchenko/EPAM/cm/codemie-ui):
- Defect 1: AUTH_CALLBACK_TIMEOUT_SECONDS (60s) in src/hooks/useAuthCallbackListener.ts:22 is far shorter than backend PKCE/callback-state lifetimes (600s / 10min / 900s). Comment at useAuthCallbackListener.ts:103 needs updating to state the real backend lifetime constraint.
- Defect 2: On timeout, useMCPAuthPrompt.ts:238-246 (onTimeout) sets status via getRecoverableAuthStatus (src/utils/mcpAuthInitiate.ts:29-31) to 'authentication_required', which removes the row's auth_config_id from trackedAuthConfigIds (useMCPAuthPrompt.ts:201-207). useAuthCallbackListener.ts's tracking effect (146-162) then deletes it from trackedIdsRef, so a late postMessage callback (line 221) is ignored as 'untracked auth_config_id' even though the backend already stored a valid token via TMS. Need to check downstream consumers of trackedAuthConfigIds: WorkflowDetailsPage.tsx:53-64 and pages/chat/hooks/useChatAuthCallbacks.ts:42-62.
- Defect 3 (suspected): retry after timeout reuses cached pending_initiate.auth_url (useMCPAuthPrompt.ts:158) with a PKCE state that may already be one-shot-consumed server-side (getdel semantics), causing session_expired on retry.
- Existing tests: src/hooks/__tests__/useAuthCallbackListener.test.tsx (12 tests, dispatchMessage(origin,data) helper at line 40); src/hooks/__tests__/useMCPAuthPrompt.test.tsx.
- Interim mitigation already exists: mcpAuthTimeoutSeconds app setting surfaced via appInfoStore (src/constants/configKeys.ts, src/store/appInfo.ts:145-150).

SECONDARY repo codemie (/home/taras_spashchenko/EPAM/cm/codemie, this repo):
- src/codemie/enterprise/mcp_auth/_initiate.py, router.py, _uri.py currently have zero log statements. Need logging correlating an initiate call with its later callback: discovered_flow_id, auth_config_id, state prefix, resolved redirect_uri, whether a snapshot was healed (_initiate.py:130-139). Follow .ai-run/guides/development/logging-patterns.md.
- Related backend lifetime constants (for reference, no changes needed): _PKCE_TTL_SECONDS in enterprise mcp_auth redis_pkce_store.py (600s), _CALLBACK_STATE_MAX_AGE in src/codemie/enterprise/mcp_auth/_constants.py (10min), DISCOVERED_FLOW_TTL_SECONDS in discovered_flow.py (900s).

Research both repos' current code structure, related tests, and risk areas needed to plan this fix.

---

## 2. Codebase Findings

### Existing Implementations

**codemie-ui (frontend)**
- `src/hooks/useAuthCallbackListener.ts` — low-level cross-window `postMessage` listener. Constants `AUTH_CALLBACK_EVENT_TYPE`, `AUTH_CALLBACK_TIMEOUT_SECONDS = 60` (line 22), `AUTH_CALLBACK_TIMEOUT_MS`, `AUTH_CALLBACK_TIMEOUT_MESSAGE`. `getAuthCallbackTimeoutSeconds()` (lines 100-101) resolves `appInfoStore.getMcpAuthTimeoutSeconds()` with a fallback to the 60s constant. Comment at line 103 reads "Keep the UI timeout <= the backend PKCE lifetime." Hook body (106-277) maintains `trackedIdsRef` (Set) and `timeoutsRef` (per-id timers); a tracking-diff `useEffect` (126-182) removes ids from `trackedIdsRef` and clears their timers once an id drops out of the `trackedAuthConfigIds` prop; the message handler (184-265) verifies `event.origin` then checks `trackedIdsRef.current.has(event.data.auth_config_id)` (line 221), logging `'[mcp-auth] Ignoring auth callback for untracked auth_config_id'` and returning early when not tracked.
- `src/hooks/useMCPAuthPrompt.ts` — `initiate()` (90-149) stores `pending_initiate` on the row for `oauth2` flows instead of opening the popup directly; `continueAuth()` (151-188) opens `pendingInitiate.auth_url` in a popup (the retry reuse point); `trackedAuthConfigIds` (`useMemo`, 201-207) filters rows with `status === 'authenticating'`; `onTimeout` (238-246) calls `getRecoverableAuthStatus(row)` and sets `error_context: AUTH_CALLBACK_TIMEOUT_MESSAGE`; line 248 wires `useAuthCallbackListener({ trackedAuthConfigIds, onSuccess, onError, onTimeout })`.
- `src/utils/mcpAuthInitiate.ts` — `getRecoverableAuthStatus` (29-31): `row.recoverable_status ?? (row.status === 'session_expired' ? 'session_expired' : 'authentication_required')`. `getPendingInitiate` (33-47) requires `redirect_uri_hostname`, else returns null.
- `src/pages/workflows/WorkflowDetailsPage.tsx` — `trackedAuthConfigIds` (`useMemo`, 53-62) parses `execution.output` JSON for `auth_config_id` when `overall_status === 'AUTHENTICATION_REQUIRED'`. Line 64 calls `useAuthCallbackListener({ trackedAuthConfigIds })` with no `onSuccess`/`onError`/`onTimeout` handlers — the only one of the three consumers wired this way.
- `src/pages/chat/hooks/useChatAuthCallbacks.ts` — `getAuthenticatingPromptIds` (25-33), `NOOP_HANDLERS` (35-39), `useChatAuthCallbacks` (41-63) wires `onSuccess`/`onError`/`onTimeout` to `chatGenerationStore.markPromptAuthSuccess` / `rollbackPromptAuthRow`.
- `src/constants/configKeys.ts` — `CONFIG_KEYS.MCP_AUTH_TIMEOUT_SECONDS = 'mcpAuthTimeoutSeconds'`, `CONFIG_KEYS.MCP_AUTH_ORIGIN = 'mcpAuthOrigin'`.
- `src/store/appInfo.ts` — `getMcpAuthOrigin()` (140-143), `getMcpAuthTimeoutSeconds()` (145-150); `fetchCustomerConfig()` (164-179) populates `this.configs` from `GET v1/config`.

**codemie (backend, this repo)**
- `src/codemie/enterprise/mcp_auth/_initiate.py` — `build_oauth2_initiate_response` (50-99), `build_discovered_oauth2_initiate_response` (102-199, with the snapshot-heal probe at 121-139: `_probe = _store.get(discovered_flow_id) if discovered_flow_id else _store.get_for_binding(...)`; `if _probe is None:` triggers `MCPToolkitService.ensure_discovered_snapshot_for_server(...)`), `build_recovery_oauth2_initiate_response` (202-259), `_load_discovered_flow_snapshot_for_binding_or_error` (262-291), `build_saml_initiate_response` (294-346), `build_saml_metadata_response` (349-407), `build_discovered_auth_status_response` (410-469). No `logger`/`logging` usage anywhere in the surfaced ranges.
- `src/codemie/enterprise/mcp_auth/router.py` — `router`/`enabled_router`/`cimd_router`/`enabled_cimd_router` (tag `"MCP Auth"`, prefix `/v1/mcp-auth`); `get_mcp_auth_router()` / `get_cimd_router()` (520-529) toggle disabled vs. enabled routers based on `is_mcp_auth_enabled()`; `OAuth2InitiateRequest`/`OAuth2InitiateResponse` models (75-84). No logging in the surfaced ranges (17-90, 517-530); route-handler bodies themselves were not fully surfaced across queries.
- `src/codemie/enterprise/mcp_auth/_oauth2_callback.py` — `_decode_and_verify_oauth2_callback_state`, `_load_callback_mcp_config`, `_load_raw_callback_oauth_config`, `_validate_callback_state_age` (129-136, compares elapsed age against `_CALLBACK_STATE_MAX_AGE`), `_is_mcp_auth_redis_unavailable` (139-144), `_consume_callback_pkce_state` (147-165 — calls `pkce_store.consume(state)`; a second consume of an already-used state returns `None`, raising `CallbackPageError` with `_CALLBACK_ERROR_SESSION_EXPIRED`), `_validate_callback_state_matches_pkce` (168-178), `_load_discovered_flow_snapshot_or_error` (181-216), `_validate_discovered_snapshot_context` (245-269), `_build_discovered_oauth2_callback_response` (692-738). No logging anywhere in the surfaced ranges.
- `src/codemie/enterprise/mcp_auth/_guards.py` — full file (1-140): `_require_initialized_mcp_auth_components`, `_require_initialized_discovered_flow_store`, `_require_initialized_tms`, `_require_initialized_saml_callback_dependencies`, `is_mcp_auth_enabled` (checks `deps.HAS_MCP_AUTH` and `config.MCP_AUTH_ENABLED`/`config.MCP_AUTH_TMS_ENABLED`), `get_mcp_auth_trust_policy_service`, `invalidate_mcp_auth_trust_policy_cache`. No logging.
- `src/codemie/enterprise/mcp_auth/_uri.py` — full file (1-266, verbatim confirmed): `build_redirect_uri` (75-80, delegates to `_build_callback_uri`), `derive_resource_uri` (178-196, delegates to `codemie_enterprise.mcp_auth.discovery.derive_canonical_mcp_resource_uri` when the enterprise package is importable, else `_derive_resource_uri_without_enterprise`), `_build_callback_uri` (57-72, builds `f"{config.CALLBACK_API_BASE_URL}{get_api_root_path()}{path}"` and validates HTTPS-unless-localhost), `build_saml_acs_url` (105-111), `build_client_metadata_document_response` (121-149), `_is_localhost_hostname`, `_describe_stored_redirect_uri`, `_normalize_resource_hostname`/`_derive_resource_uri_without_enterprise` (resource-URI normalization fallback). Imports TTL/path constants from `._constants` (`_CLIENT_METADATA_CACHE_CONTROL`, `_CLIENT_METADATA_DOCUMENT_PATH`, `_INVALID_*_MESSAGE`, `_LOCALHOST_HOSTS`, `_OAUTH2_CALLBACK_PATH`, `_SAML_ACS_PATH`) — no TTL constant appears among this file's own imports. No logging anywhere in the file.
- `src/codemie/enterprise/mcp_auth/_discovery.py` — the one file in this package with existing logging: `logger.warning(...)` at lines 66, 97, 215, 218, 274 (e.g. "Skipping MCP auth discovery probe because MCP auth discovery dependencies are not initialized", "MCP auth discovery bridge unavailable; returning warning results: {exc}", "MCP auth discovered flow handoff storage unavailable: {exc}").
- `src/codemie/enterprise/mcp_auth/_diagnostics.py` — full file (1-85): `OAuth2CallbackDiagnostics` model + `build_oauth2_callback_diagnostics_response`; `_sanitize_log_value` (29-38, CWE-117 CR/LF sanitization before logging); structured `logger.warning`/`logger.info` calls (71-84) branching on detected failure shape.
- `src/codemie/rest_api/models/mcp_config.py` — `MCPConfig.get_by_auth_config_id` classmethod (234-246), a reverse lookup used by the callback flow; unique partial index `ix_mcp_configs_auth_config_id`.
- `src/codemie/service/mcp_config_service.py` — `MCPConfigService` CRUD plus `auth_config` encryption/validation (182-548) — adjacent to, but not directly in, the timeout-race path.
- `src/codemie/rest_api/routers/google_oauth.py`, `src/codemie/rest_api/routers/sharepoint_oauth.py` — analogous non-MCP OAuth2/PKCE callback routers; both similarly logging-sparse, so no strong in-repo precedent exists for OAuth-callback-router logging outside `_discovery.py`/`_diagnostics.py`.

### Architecture and Layers Affected

- **Frontend hooks layer** (`codemie-ui/src/hooks/`): `useAuthCallbackListener` (shared low-level listener), `useMCPAuthPrompt` (assistant/toolkit auth gate).
- **Frontend feature-consumer layer**: `src/pages/chat/hooks/useChatAuthCallbacks.ts` (chat gate), `src/pages/workflows/WorkflowDetailsPage.tsx` (workflow-execution gate) — three independent call sites of the same shared hook.
- **Frontend store/config layer**: `src/store/appInfo.ts` (Valtio `appInfoStore`), `src/constants/configKeys.ts`.
- **Backend enterprise bridge layer** (`src/codemie/enterprise/mcp_auth/`): `_initiate.py`, `router.py`, `_oauth2_callback.py`, `_guards.py`, `_uri.py`, `_discovery.py`, `_diagnostics.py` — thin adapters around the external `codemie_enterprise.mcp_auth` package, with graceful `ImportError` fallback (`MCPAuthEnterpriseUnavailableError` / HTTP 503).
- **Backend data/service layer**: `src/codemie/rest_api/models/mcp_config.py` (`MCPConfig` model), `src/codemie/service/mcp_config_service.py`.
- **Backend app wiring**: `src/codemie/rest_api/main.py` — MCP config router registered at line 695, MCP auth router included at line 713 (per `.ai-run/guides/integration/mcp-integration.md`).

### Integration Points

- Frontend `useAuthCallbackListener` communicates with the backend-served OAuth callback HTML page via `window.postMessage`, gated by `event.origin` verification against `appInfoStore.getMcpAuthOrigin()`.
- Frontend `appInfoStore.fetchCustomerConfig()` calls `GET v1/config` to populate `mcpAuthTimeoutSeconds`/`mcpAuthOrigin`.
- Backend `_uri.py` conditionally imports `codemie_enterprise.mcp_auth.discovery` (`derive_canonical_mcp_resource_uri`, `client_metadata`) — an external package, not indexed in this repo's codegraph; `_oauth2_callback.py`/`_guards.py` similarly import `RedisPKCEStore` and related types from `codemie_enterprise.mcp_auth`.
- `_initiate.py`'s discovered-flow healing path calls into `MCPToolkitService.ensure_discovered_snapshot_for_server(...)`.
- `MCPConfig.get_by_auth_config_id` is the reverse lookup the callback path uses to resolve which MCP config an incoming callback belongs to.

### Patterns and Conventions

- Backend: `is_mcp_auth_enabled()` / `get_mcp_auth_router()` / `get_cimd_router()` toggle-router pattern (disabled vs. enabled router swapped in based on feature/config flags) rather than per-request guards.
- Backend: `MCPAuthEnterpriseUnavailableError` + `try: import_module(...) except ImportError:` is the repo's standing convention for guarding against the optional `codemie_enterprise.mcp_auth` dependency (seen in `_uri.py`, and matches `_guards.py`'s `_require_initialized_*` helpers).
- Backend logging precedent (where it exists, in `_diagnostics.py`): sanitize any value before logging (`_sanitize_log_value`, CWE-117 CR/LF stripping) and branch severity by detected failure shape rather than emitting one flat message.
- Frontend: shared cross-cutting hook (`useAuthCallbackListener`) is composed into feature-specific hooks rather than each consumer implementing its own `postMessage` listener; `trackedAuthConfigIds` is always a derived (`useMemo`) view of feature state, not separately-managed state.

---

## 3. Documentation Findings

### Guides and Architecture Docs

`.ai-run/guides/**` (36 files total via Glob) — two guides directly relevant to this task were read in full:
- `.ai-run/guides/development/logging-patterns.md`
- `.ai-run/guides/integration/mcp-integration.md`

### Architectural Decisions

- `.ai-run/guides/development/logging-patterns.md`: "Context In Messages" — log operation, IDs, status, and sanitized details; never raw tokens/headers/credentials (evidence: `src/codemie/rest_api/security/authentication.py:114`). "Metric Failure Labels" — give security-relevant failures (auth, signature, token) a distinct label from benign rejections; never share a label (evidence: `src/codemie/triggers/bindings/webhook.py:398`). "Severity" — match log severity to operational impact; warning/structured-client-error for expected validation failures, error-with-exception-info for server failures (evidence: `ExtendedHTTPException` handler at `src/codemie/rest_api/main.py:826`).
- `.ai-run/guides/integration/mcp-integration.md`: "MCP Configuration" — keep MCP auth logic behind existing service/router modules, not in unrelated routers (evidence: MCP config router registered `main.py:695`; MCP auth router included `main.py:713`).

### Derived Conventions

- The one existing logging precedent inside `src/codemie/enterprise/mcp_auth/` itself is `_diagnostics.py`'s `_sanitize_log_value` + severity-branched `logger.warning`/`logger.info` calls, and `_discovery.py`'s plain `logger.warning` calls on dependency-unavailable / bridge-unavailable conditions — these are the two in-package models to align with, since `_initiate.py`, `router.py`, `_oauth2_callback.py`, `_guards.py`, and `_uri.py` currently have no logging at all.

---

## 4. Testing Landscape

### Existing Coverage

- `codemie-ui/src/hooks/__tests__/useAuthCallbackListener.test.tsx` — 12 tests via a `dispatchMessage(origin, data)` helper (line 40) that fires `window.dispatchEvent(new MessageEvent('message', {...}))`. Confirmed test names include: "marks tracked ids as authenticating", "ignores non-matching origins and malformed payloads", "ignores unrelated auth_config_id values", "updates only the targeted flow on error, clears timeout, and emits onError", "times out authenticating flows back to authentication_required with retry copy and emits onTimeout", "removes untracked ids and clears their timers on rerender", "falls back to 60 seconds when runtime-config timeout is invalid", "clears pending timeouts on unmount", "logs listener origin context on setup", "logs observed mcp_auth_callback messages even when dropped for a bad origin".
- `codemie-ui/src/hooks/__tests__/useMCPAuthPrompt.test.tsx` — exists (content not enumerated test-by-test in this research pass).
- `codemie` backend: `tests/enterprise/mcp_auth/test_oauth2_initiate_bridge.py` covers `derive_resource_uri` (per codegraph blast-radius mapping). No test coverage was surfaced for `build_oauth2_initiate_response`, `build_discovered_oauth2_initiate_response`, `_consume_callback_pkce_state`, `_validate_callback_state_age`, or `build_redirect_uri` specifically — codegraph's blast-radius output flagged `build_redirect_uri` with "⚠️ no covering tests found".

### Testing Framework and Patterns

- Frontend: Vitest + `@testing-library/react` (`renderHook`, `act`), with `vi.useFakeTimers()` for timeout-path tests.
- Backend: pytest, under `tests/enterprise/mcp_auth/`.

### Coverage Gaps

- `src/pages/workflows/WorkflowDetailsPage.tsx`'s use of `useAuthCallbackListener` (line 64, no handlers passed) — codegraph blast-radius flagged "no covering tests found".
- `src/pages/chat/hooks/useChatAuthCallbacks.ts` — codegraph blast-radius flagged "no covering tests found".
- Backend `_initiate.py` snapshot-heal branch (121-139), `_oauth2_callback.py`'s `_consume_callback_pkce_state` one-shot-consume branch, and `_uri.py`'s `build_redirect_uri` — no covering tests surfaced.

---

## 5. Configuration and Environment

### Environment Variables

- `CALLBACK_API_BASE_URL` (`src/codemie/configs`) — base URL used by `_uri.py`'s `_build_callback_uri` to construct the OAuth2 redirect URI and SAML ACS URL.
- `MCP_AUTH_ENABLED`, `MCP_AUTH_TMS_ENABLED` (`src/codemie/configs`) — checked by `_guards.py`'s `is_mcp_auth_enabled()`.

### Configuration Files

- `codemie-ui/src/constants/configKeys.ts` — `mcpAuthTimeoutSeconds`, `mcpAuthOrigin` runtime-config keys.
- `codemie-ui/src/store/appInfo.ts` — `fetchCustomerConfig()` populates these from backend `GET v1/config`.
- Backend `src/codemie/enterprise/mcp_auth/_constants.py` — referenced by `_uri.py`'s imports (`_CLIENT_METADATA_CACHE_CONTROL`, `_CLIENT_METADATA_DOCUMENT_PATH`, `_INVALID_MCP_AUTH_CONFIG_MESSAGE`, `_INVALID_MCP_SERVER_URL_MESSAGE`, `_INVALID_OAUTH2_CONFIG_MESSAGE`, `_LOCALHOST_HOSTS`, `_OAUTH2_CALLBACK_PATH`, `_SAML_ACS_PATH`) and by `_oauth2_callback.py`'s `_validate_callback_state_age` (which compares against `_CALLBACK_STATE_MAX_AGE`, per task_context and per the earlier-verified `_oauth2_callback.py` lines 129-136). The file's own defining content was not surfaced verbatim by codegraph across two targeted queries in this research pass.

### Feature Flags and Deployment Concerns

- `is_mcp_auth_enabled()` gates whether `get_mcp_auth_router()`/`get_cimd_router()` register the enabled or disabled router variant at app startup (`main.py:713`).
- No secrets-management or deployment-manifest content surfaced as directly relevant to this task's scope.

---

## 6. Risk Indicators

- Three independent, divergently-wired consumers of `useAuthCallbackListener` (`useMCPAuthPrompt`, `useChatAuthCallbacks`, `WorkflowDetailsPage`) share the same tracking/untracking semantics; `WorkflowDetailsPage.tsx:64` passes no `onSuccess`/`onError`/`onTimeout` handlers at all, and both it and `useChatAuthCallbacks.ts` have no covering tests per codegraph's blast-radius analysis — any change to the shared hook's untrack-on-timeout behavior needs manual verification against all three call sites, not just the one with existing tests.
- Backend `_initiate.py`, `router.py`, `_oauth2_callback.py`, `_guards.py`, and `_uri.py` are entirely logger-free (confirmed across every function body surfaced by codegraph in this research pass); the only in-package logging precedents are `_discovery.py` (plain `logger.warning`) and `_diagnostics.py` (sanitized, severity-branched). A new logging addition has two divergent local styles to reconcile against, and no existing test asserts on log output in this package.
- `_consume_callback_pkce_state` in `_oauth2_callback.py` performs a one-shot `pkce_store.consume(state)`; a second consume attempt returns `None` and raises `CallbackPageError` with `_CALLBACK_ERROR_SESSION_EXPIRED` — this mechanically confirms task_context's suspected Defect 3 (retry reusing a cached `auth_url` whose PKCE state was already consumed will hit `session_expired`).
- Research gap: `_PKCE_TTL_SECONDS` (`redis_pkce_store.py`), `DISCOVERED_FLOW_TTL_SECONDS` (`discovered_flow.py`), and the defining line of `_CALLBACK_STATE_MAX_AGE` in `src/codemie/enterprise/mcp_auth/_constants.py` were not surfaced verbatim by codegraph across two targeted queries in this repo — either because they live in the external `codemie_enterprise.mcp_auth` package (not indexed here) or because `_constants.py`'s TTL-related content simply wasn't returned. This should be independently confirmed (e.g. by a direct file Read) before any planning step asserts their exact values as backend fact.
- `router.py`'s actual route-handler function bodies (the FastAPI endpoint functions themselves, as opposed to the surfaced models/router objects) were never returned verbatim by codegraph in this pass — a partial gap in Section 2's router.py coverage.
- Speculative: the fix will likely need to either (a) decouple the frontend's untrack-on-timeout side effect from the backend's actual auth completion state (e.g. keep tracking the id for a longer "grace" window, or re-check backend status instead of trusting only the postMessage channel), and/or (b) avoid reusing an already-consumed PKCE `auth_url` on retry by re-initiating rather than reusing `pending_initiate`. These are design directions for the spec/plan stage, not confirmed requirements — the actual required schema/field/file changes are undetermined by this research pass.
- Speculative: raising the frontend's 60s fallback timeout or aligning it with a backend-supplied lifetime value may require a config/API contract change; the exact mechanism (new field on `GET v1/config`, or a different signal) is undetermined and should be decided at spec/plan time, not inferred here.

---

## 7. Summary for Complexity Assessment

This fix spans two repositories and at least four architectural layers: the frontend hooks layer (`useAuthCallbackListener`, `useMCPAuthPrompt`), the frontend feature-consumer layer (`useChatAuthCallbacks`, `WorkflowDetailsPage`), the frontend store/config layer (`appInfoStore`), and the backend enterprise-bridge layer (`_initiate.py`, `_oauth2_callback.py`, `_guards.py`, `_uri.py`, `router.py`). The file-change surface for the confirmed defects (stale comment, untrack-on-timeout race, backend logging gap) is narrow and well-localized, but the shared `useAuthCallbackListener` hook has three independent, divergently-wired call sites — two of which (`WorkflowDetailsPage`, `useChatAuthCallbacks`) have no existing test coverage — so a change to its tracking semantics carries real regression risk beyond the one call site (`useMCPAuthPrompt`) that is well-tested.

Technical novelty is low-to-moderate: the race condition itself (client-side timeout untracking an id before a late but valid backend callback arrives) is a well-understood class of bug, and the one-shot PKCE consume semantics behind suspected Defect 3 is mechanically confirmed in `_oauth2_callback.py`. The main open question is architectural — whether the fix decouples client-side tracking from a hard timeout, or changes retry to re-initiate rather than reuse a consumed `auth_url` — and that is a design decision for the spec/plan stage, not something resolved by this research. Backend logging is a separate, additive, low-risk change but must reconcile two different existing logging styles within the same package (`_discovery.py` vs. `_diagnostics.py`) and follow the repo's CWE-117 sanitization and severity-matching conventions.

Key risk factors: (1) three divergent consumers of the shared frontend hook, two untested; (2) confirmed one-shot PKCE consume creating a real retry failure mode; (3) a research gap around the exact backend TTL constants (`_PKCE_TTL_SECONDS`, `DISCOVERED_FLOW_TTL_SECONDS`, `_CALLBACK_STATE_MAX_AGE`'s definition) that should be independently verified before the comment/lifetime-alignment fix is finalized; (4) the backend mcp_auth bridge package is almost entirely logger-free, so the logging addition has no single strong in-package precedent to copy verbatim.
