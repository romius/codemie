# Technical Research

**Task**: litellm proxy_router budget_categories cli_request chrome_extension spend_tracking
**Generated**: 2026-08-18T17:41:00Z
**Research path**: filesystem

---

## 1. Original Context

Update `_is_cli_request` in the LiteLLM proxy router to classify Chrome extension traffic as `BudgetCategory.PLATFORM` rather than `BudgetCategory.CLI`, by keying on `client_type` instead of the presence of the `X-CodeMie-CLI` header. Chrome extension requests with `client_type=codemie-chrome-extension` currently get classified as CLI requests because the function checks for a non-empty `X-CodeMie-CLI` header. Fix: make `_is_cli_request` return True only when `client_type` is `codemie-cli` or `codemie_cli` (case-insensitive). The monitoring service (`llm_proxy_monitoring_service.py`) has its own `_is_cli_request` that also needs fixing. Add automated tests for all client type scenarios.

---

## 2. Codebase Findings

### Existing Implementations

- `D:\Projects\codemie\src\codemie\enterprise\litellm\proxy_router.py` (lines 515–519) — LiteLLM proxy orchestration; contains `_is_cli_request` and `_resolve_non_premium_tracking_identity`. The current `_is_cli_request` is a union predicate: returns `True` if `bool(request_info.get(CODEMIE_CLI)) OR client_type in {"codemie-cli", "codemie_cli"}`. The budget routing chain is: premium check → `_is_cli_request` → `BudgetCategory.CLI` → else `BudgetCategory.PLATFORM`.
- `D:\Projects\codemie\src\codemie\service\monitoring\llm_proxy_monitoring_service.py` (lines 433–435) — usage and metrics tracking; contains a separate `_is_cli_request` that currently checks **only** `bool(request_info.get(CODEMIE_CLI))` — the header-only path. This is the primary source of the Chrome extension misclassification in monitoring metrics.
- `D:\Projects\codemie\src\codemie\enterprise\litellm\budget_categories.py` — `BudgetCategory` enum with values `PLATFORM`, `CLI`, `PREMIUM_MODELS`; also contains `build_user_id` for budget identity construction.
- `D:\Projects\codemie\src\codemie\core\constants.py` — defines `CODEMIE_CLI`, `CLIENT_TYPE`, `HEADER_CODEMIE_CLI`, `HEADER_CODEMIE_CLIENT` constants used as keys in `request_info`.

### Architecture and Layers Affected

- **Request ingestion layer** — `proxy_router.py` builds `request_info` from HTTP headers, including `CODEMIE_CLI` (value of `X-CodeMie-CLI`) and `CLIENT_TYPE` (value of `X-CodeMie-Client`).
- **Budget category resolution layer** — `_is_cli_request` and `_resolve_non_premium_tracking_identity` in `proxy_router.py` gate `BudgetCategory.CLI` vs `BudgetCategory.PLATFORM` assignment.
- **Monitoring/metrics layer** — `LLMProxyMonitoringService._is_cli_request` in `llm_proxy_monitoring_service.py` gates the `cli_request` boolean flag emitted in the `codemie_litellm_proxy_usage` metric.

### Integration Points

- `codemie.core.constants` — shared constant keys (`CODEMIE_CLI`, `CLIENT_TYPE`) consumed by both `_is_cli_request` implementations.
- `codemie.enterprise.litellm.budget_categories.BudgetCategory` — enum consumed by `proxy_router._resolve_non_premium_tracking_identity`.
- `codemie.service.budget.budget_enums.BudgetCategory` — core enum mirrored in the enterprise layer.
- `LLM_PROXY_TRACK_USAGE` environment variable — controls whether usage metrics are emitted via the monitoring service.

### Patterns and Conventions

- `_is_cli_request` is implemented as a module-level pure predicate function in both files (not a class method).
- Budget category routing follows a priority chain: premium model check → `_is_cli_request` → `BudgetCategory.CLI` → default `BudgetCategory.PLATFORM`.
- `request_info` is a plain `dict` carrying both `CODEMIE_CLI` (header value) and `CLIENT_TYPE` (header value) as distinct string keys.
- Constants from `codemie.core.constants` are used as dict keys rather than raw strings.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/architecture/layered-architecture.md` — closest match; governs layer boundaries relevant to the proxy router and monitoring service split.
- `.ai-run/guides/testing/testing-patterns.md` — seam-test policy (lines 26–35) requires a callsite seam test per branch; directly applicable to the new Chrome extension client type scenario.
- No guide specifically covers `litellm`, `proxy_router`, `budget_categories`, or `spend_tracking`.

### Architectural Decisions

- No ADRs found for budget category routing or CLI/platform classification.
- The dual-key `request_info` structure (header value + client type) for `_is_cli_request` appears to be an in-progress migration from header-based to client-type-based classification.

### Derived Conventions

- The `proxy_router._is_cli_request` already has a partial `client_type` check, indicating an earlier partial migration; the task completes that migration by removing the `CODEMIE_CLI` header arm.
- The monitoring service `_is_cli_request` was never updated during that partial migration — it remains header-only.
- Case-insensitive matching of `client_type` is implied by the existing `{"codemie-cli", "codemie_cli"}` set in `proxy_router.py`.

---

## 4. Testing Landscape

### Existing Coverage

- `D:\Projects\codemie\tests\enterprise\litellm\test_proxy_router.py`
  - `TestProbeProjectBudgetScopes.test_cli_client_type_uses_cli_budget` (lines 595–619) — covers `codemie-cli` and `codemie_cli` client types for budget scoping.
  - `TestResolveNonPremiumTrackingIdentity` — covers header-based CLI detection path.
  - No test covering `client_type=codemie-chrome-extension` resolving to `BudgetCategory.PLATFORM`.
- `D:\Projects\codemie\tests\codemie\service\monitoring\test_llm_proxy_monitoring_service.py`
  - `TestIsCliRequest` (lines 62–76) — tests only the `CODEMIE_CLI` header path; asserts truthy header value returns `True`.
  - `TestTrackUsage` (lines 78–210) — uses a `cli_request_info` fixture with `client_type: codemie-claude` and truthy `CODEMIE_CLI`.

### Testing Framework and Patterns

- pytest with `pytest-asyncio`.
- `unittest.mock.patch`, `MagicMock`, `AsyncMock` for mock strategies.
- Fixture-based test setup (`cli_request_info` fixture in monitoring tests).
- Seam tests at the callsite are the mandated pattern per `.ai-run/guides/testing/testing-patterns.md`.

### Coverage Gaps

- **No test for `client_type=codemie-chrome-extension` → `BudgetCategory.PLATFORM`** in `test_proxy_router.py`.
- **No test for `client_type=codemie-chrome-extension` → `cli_request=False`** in `test_llm_proxy_monitoring_service.py`.
- **`TestIsCliRequest` tests will break** after the fix — they assert the header-only path and must be replaced with client-type-based assertions.
- **`cli_request_info` fixture** uses `client_type: codemie-claude` with truthy `CODEMIE_CLI`; after fix, `codemie-claude` is not in `{codemie-cli, codemie_cli}` so `cli_request` will be `False` — dependent `TestTrackUsage` tests need realigned fixture data.

---

## 5. Configuration and Environment

### Environment Variables

- `LLM_PROXY_TRACK_USAGE` — controls whether usage metrics are emitted from `LLMProxyMonitoringService`.
- `CODEMIE_MIN_CLI_VERSION` — minimum CLI version enforcement via `X-CodeMie-CLI` header value; separate from budget classification but reads the same header.

### Configuration Files

- No dedicated config files found for this feature area beyond constants in `codemie.core.constants`.

### Feature Flags and Deployment Concerns

- No feature flags found for budget category routing or CLI classification.
- The `X-CodeMie-CLI` header will continue to be used for version enforcement (`CODEMIE_MIN_CLI_VERSION`) — removing it from `_is_cli_request` must not affect that path.

---

## 6. Risk Indicators

- **Divergent implementations**: `proxy_router._is_cli_request` and `LLMProxyMonitoringService._is_cli_request` are out of sync. The monitoring service will continue to misclassify Chrome extension traffic as `cli_request=True` in metrics unless fixed in parallel with the router fix.
- **Existing `proxy_router._is_cli_request` retains a `bool(CODEMIE_CLI)` arm**: The task requires removing the header arm entirely. A Chrome extension that sends both `X-CodeMie-CLI` (non-empty) and `client_type=codemie-chrome-extension` will still be classified as CLI under the current union logic. This arm must be removed.
- **Breaking test changes in monitoring**: `TestIsCliRequest` in `test_llm_proxy_monitoring_service.py` currently passes only because it asserts the old header path. After the fix, these tests will fail and must be rewritten.
- **`cli_request_info` fixture breakage**: The fixture uses `client_type: codemie-claude` which is not a CLI client type. Any `TestTrackUsage` tests relying on `cli_request=True` from this fixture will fail after the fix unless the fixture is updated to use `client_type: codemie-cli`.
- **`CODEMIE_MIN_CLI_VERSION` guard must not be disturbed**: `X-CodeMie-CLI` header value is consumed for version enforcement in a separate code path. Ensure the fix scopes only to `_is_cli_request` predicate logic.
- **No test for the Chrome extension → PLATFORM budget path**: This is an explicitly required scenario per the task and is currently absent from both test files.
- **Case-insensitive matching requirement**: The task specifies case-insensitive matching. The existing `proxy_router` uses a set literal `{"codemie-cli", "codemie_cli"}`; the monitoring service implementation needs to apply the same pattern consistently.

---

## 7. Summary for Complexity Assessment

The task touches two files in distinct architectural layers: `proxy_router.py` (budget category resolution) and `llm_proxy_monitoring_service.py` (metrics emission). Both contain a local `_is_cli_request` predicate that must be updated. The `proxy_router` implementation is a partial migration — it already has a `client_type` arm but retains the old `CODEMIE_CLI` header arm. The fix is surgical: remove the header arm from `proxy_router._is_cli_request`, rewrite the monitoring service `_is_cli_request` from header-only to `client_type`-only, and align both to the same set `{"codemie-cli", "codemie_cli"}` (case-insensitive).

The primary risk is test cascading. At least four existing tests will break: `TestIsCliRequest` in the monitoring test file (asserts the now-removed header path), and `TestTrackUsage` tests relying on the `cli_request_info` fixture whose `client_type: codemie-claude` will no longer yield `cli_request=True`. New tests must be added for the Chrome extension scenario (`client_type=codemie-chrome-extension`) resolving to `BudgetCategory.PLATFORM` in the router and `cli_request=False` in the monitoring service, plus positive tests for both `codemie-cli` and `codemie_cli` variants in each file.

There is no novel architectural pattern required — both functions are pure predicates operating on a plain dict. The `CODEMIE_MIN_CLI_VERSION` enforcement path reads the same `X-CodeMie-CLI` header but is a separate code path and must not be disturbed. Overall complexity is low-to-medium: two small implementation changes plus test rewrites with clear scope, bounded risk, and no external service changes.
