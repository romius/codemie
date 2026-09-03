# Technical Research

**Task**: spendings budget assignment widget aggregation spend_bucket
**Generated**: 2026-09-02T00:00:00Z

---

## 1. Original Context

Bug EPMCDME-13669: Spendings widget shows 0 after assigning a new budget to a user with existing spending. When a new budget is assigned to a user who already has existing LLM spending, the Spendings widget displays the spending value as 0 until the user starts using LLM again. The root cause is likely in the budget assignment logic or spending aggregation/refresh logic — when a budget is assigned, the spending data for that user is not being correctly fetched or the spend bucket is being reset/initialized to 0 instead of reading existing spend data. Acceptance criteria: (1) Spendings widget displays correct existing spending immediately after budget assignment; (2) existing spending not shown as 0 unless actual value is 0; (3) spending data does not require new LLM usage event to become visible after budget assignment; (4) budget assignment triggers or uses correct spending aggregation/refresh logic.

---

## 2. Codebase Findings

### Existing Implementations

**Budget assignment flow:**
- `src/codemie/service/budget/budget_service.py` — `BudgetService.assign_budget_to_user()` (line 1079): Writes new `budget_id` to `user_budget_assignments` table via `budget_repository.upsert_user_category_assignment`, clears `_budget_assignment_cache`, and calls `provider.assign_user_budget()`. Does NOT write or update `project_spend_tracking`.
- `src/codemie/rest_api/routers/user_management_router.py` — `PUT /{user_id}/budgets` (line 493): REST entry point for budget assignment; calls `budget_service.assign_budget_to_user()` then commits.

**LiteLLM provider budget assignment:**
- `src/codemie/enterprise/litellm/budget_provider_adapter.py` — `LiteLLMBudgetProvider.assign_user_budget()` (line 573): Calls `update_customer_budget_in_litellm(litellm_user_id, budget_id)` which calls `service.set_customer_budget_assignment(user_id, budget_id)`. This updates the LiteLLM customer's budget pointer but does NOT reset the customer's spend counter in LiteLLM.
- `src/codemie/enterprise/litellm/budget_helpers.py` — `update_customer_budget_in_litellm()` (line 196): Wraps `LiteLLMService.set_customer_budget_assignment`. Fail-open (returns False on error).

**Spendings widget data path:**
- `src/codemie/rest_api/routers/analytics.py` — `GET /budget_usage` (line 2982): Resolves subject user, calls `budget_usage_service.get_budget_usage(session, subject_user_id, subject_label)`.
- `src/codemie/service/analytics/handlers/budget_usage_service.py` — `BudgetUsageService.get_budget_usage()` (line 320): Loads from DB, decides whether to refresh from LiteLLM, returns tabular rows.
- `src/codemie/service/analytics/handlers/budget_usage_service.py` — `BudgetUsageService._load_from_db()` (line 377): Loads `assignments` by `user_id` (new budget_id after assignment), `spend_map` via `get_latest_by_budget_ids(new_budget_id, subject_label)` (keyed by budget_id), and `prev_day_map` via `get_latest_before_today_by_budget_categories(categories, subject_label)` (keyed by category, surviving budget_id changes).
- `src/codemie/service/analytics/handlers/budget_usage_service.py` — `BudgetUsageService._refresh_from_litellm()` (line 402): Fetches live spend from LiteLLM per category, calls `_collect_spend_rows()`, persists new tracking rows.
- `src/codemie/service/analytics/handlers/budget_usage_service.py` — `_collect_spend_rows()` (line 245): THE BUG SITE. Computes `existing = current_spend_map.get(assignment.budget_id)` (None for new budget), then `prev_row = existing if existing is not None else prev_day_map.get(assignment.category)` (falls back to old-budget row via category). Computes delta; if delta == 0 and not a reset transition, skips writing the row.
- `src/codemie/service/analytics/handlers/budget_usage_service.py` — `_build_budget_usage_rows()` (line 163): Reads `spend_row = spend_map.get(assignment.budget_id)`. Returns `float(spend_row.budget_period_spend) if spend_row else 0.0`.

**Spend tracking repository:**
- `src/codemie/repository/project_spend_tracking_repository.py` — `get_latest_by_budget_ids()` (line 539): Returns most-recent `project_spend_tracking` row per `budget_id` scoped by `project_name` (username). Returns empty dict when no rows exist for a `budget_id`.
- `src/codemie/repository/project_spend_tracking_repository.py` — `get_latest_before_today_by_budget_categories()` (line 731): Returns most-recent row per `budget_category` before UTC midnight, regardless of `budget_id`. Intentionally category-scoped to "survive budget_id reassignments" (see docstring comment).
- `src/codemie/repository/project_spend_tracking_repository.py` — `insert_budget_entries()` (line 359): Bulk upsert on `(project_name, budget_id, budget_category, spend_date)` partial index for `spend_subject_type='budget'`.

**Spend collector (batch path, not widget path):**
- `src/codemie/service/spend_tracking/spend_collector_service.py` — `LiteLLMSpendCollectorService._collect_budget_based()` (line 271): Batch collection job; uses `get_latest_before_by_budget_category_ids()` as baseline, same category-keyed fallback design.

### Architecture and Layers Affected

| Layer | Components |
|---|---|
| API | `analytics.py` → `GET /budget_usage`; `user_management_router.py` → `PUT /{user_id}/budgets` |
| Service | `BudgetService.assign_budget_to_user()`; `BudgetUsageService.get_budget_usage()`, `_load_from_db()`, `_refresh_from_litellm()`, `_collect_spend_rows()` |
| Repository | `BudgetRepository.upsert_user_category_assignment()`; `ProjectSpendTrackingRepository.get_latest_by_budget_ids()`, `get_latest_before_today_by_budget_categories()`, `insert_budget_entries()` |
| DB Persistence | `project_spend_tracking` table (tracked by `spend_subject_type='budget'`); `user_budget_assignments` table |
| External | LiteLLM provider via `budget_provider_adapter.py` and `budget_helpers.py` |

### Integration Points

- `BudgetService.assign_budget_to_user()` → `BudgetRepository.upsert_user_category_assignment()` (DB write) then `LiteLLMBudgetProvider.assign_user_budget()` (LiteLLM RPC, fail-open).
- `BudgetUsageService._refresh_from_litellm()` → `get_customer_spending()` / `get_proxy_customer_spending()` / `get_premium_customer_spending()` in `dependencies.py` (sync LiteLLM calls via `asyncio.to_thread`).
- `LiteLLMSpendCollectorService` (batch job) and `BudgetUsageService` (lazy refresh) both write to `project_spend_tracking`; they share delta helpers via `_compute_spend_snapshot` and `_did_budget_reset`.

### Patterns and Conventions

- Lazy-refresh pattern: `BudgetUsageService._needs_refresh()` checks if `spend_map` is empty or the most-recent row exceeds `config.BUDGET_USAGE_STALENESS_THRESHOLD_MS`. On stale, calls LiteLLM, persists, returns updated map.
- Sparse-table convention: Zero-delta rows are skipped (`daily_spend == 0 and not allow_zero`). A missing row means "no change since last snapshot," not "zero spend."
- Category-scoped baseline: `prev_day_map` is keyed by `budget_category` (not `budget_id`) so the daily delta chain survives budget_id reassignments. Introduced in `get_latest_before_today_by_budget_categories`.
- TTLCache for assignment lookup: `_budget_assignment_cache` in `budget_service.py` is cleared on assignment. Does not affect the spend tracking path.
- Provider fail-open: Budget assignment failures to LiteLLM are logged as warnings; the DB write is not rolled back.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `/Users/uladzislau_svetlakou/projects/codemie/.ai-run/guides/data/repository-patterns.md` — repository conventions.
- `/Users/uladzislau_svetlakou/projects/codemie/.ai-run/guides/architecture/service-layer-patterns.md` — service orchestration.
- `/Users/uladzislau_svetlakou/projects/codemie/.ai-run/guides/data/database-patterns.md` — SQLModel and session patterns.
- No specific guide covers the spend tracking data model or budget assignment lifecycle interaction with analytics.

### Architectural Decisions

- `project_spend_tracking_repository.py` docstring on `get_latest_before_by_budget_category_ids()` (line 186): explicitly records the decision to partition by category rather than budget_id so the delta chain survives reassignments. This is the root of the interaction that causes the bug.
- `budget_service.py` line 62–68: `_budget_assignment_cache` TTLCache with `config.BUDGET_ASSIGNMENT_CACHE_TTL`. The cache is cleared on assignment (`_budget_assignment_cache.pop`), which is correct.
- `budget_usage_service.py` line 347–353: Comment explains that `session.rollback()` is called after `_refresh_from_litellm` because `insert_budget_entries` commits internally (expires ORM objects), and `assignments` / `budgets_map` are reloaded after refresh. Notably, `spend_map` is NOT reloaded from DB — it uses the map returned by `_refresh_from_litellm`.

### Derived Conventions

- `budget_period_spend` in `project_spend_tracking` holds the LiteLLM-reported cumulative spend for the current budget period. The widget reads this field directly (`float(spend_row.budget_period_spend) if spend_row else 0.0`).
- `daily_spend` is the delta from the previous snapshot; it accumulates into `cumulative_spend`.
- A row in `project_spend_tracking` must exist for the current `budget_id` before the widget can show any non-zero value. There is no fallback in `_build_budget_usage_rows` to category-keyed data.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/analytics/handlers/test_budget_usage_service.py` — covers `_calculate_time_until_reset`, `_build_spending_row`, `_build_budget_usage_rows`, `_collect_spend_rows` (zero-row insert on reset, unchanged spend handling), `_compute_spend_delta_safe`, and `BudgetUsageService._needs_refresh`, `_refresh_from_litellm`, full `get_budget_usage` path. Uses `SimpleNamespace` mocks for assignments, budgets, and spend rows. Uses `AsyncMock` for repository and LiteLLM calls.
- `tests/codemie/service/analytics/handlers/test_member_spend_service.py` — covers `MemberSpendService` helpers and `_ensure_fresh`.
- `tests/codemie/service/budget/test_budget_assignments.py` — only tests `_validate_budget_matches_category` (category mismatch rejection). Does NOT test the budget assignment → spend-refresh interaction.
- `tests/codemie/service/budget/test_budget_service.py` — covers `assign_budget_to_user`, `bulk_set_user_budgets`, `reset_user_budget_spending`. Does NOT test the spend tracking side-effects of assignment.
- `tests/codemie/rest_api/routers/test_budget_router.py` and `test_budget_router_additional.py` — API-level budget CRUD tests.
- `tests/codemie/rest_api/routers/test_analytics.py` and `test_analytics_budget_usage_labels.py` — analytics router tests including budget usage.

### Testing Framework and Patterns

- Framework: `pytest` with `pytest-asyncio`.
- Mocking: `unittest.mock.AsyncMock`, `MagicMock`, `patch`. Repository calls are mocked at the method level.
- Fixtures: `SimpleNamespace` for lightweight model stubs (assignments, budgets, spend rows). DB integration tests use a test session.
- Pattern: Unit tests for service helpers use inline `SimpleNamespace` stubs; integration tests (router-level) use `AsyncMock` for full-stack mocking.

### Coverage Gaps

- **No test for the budget-reassignment scenario**: No test verifies the behavior of `_collect_spend_rows` or `BudgetUsageService.get_budget_usage` when `assignment.budget_id` is new (no prior `project_spend_tracking` rows) but the user has existing spend under an old `budget_id`. This is precisely the bug scenario.
- **No test for the `prev_day_map` + new `budget_id` interaction**: The `prev_day_map` fallback path in `_collect_spend_rows` (`prev_row = prev_day_map.get(assignment.category)`) is not tested for the case where `existing = None` (new budget) and `prev_row` comes from a different budget_id.
- **No integration test for `assign_budget_to_user` → widget render**: No end-to-end test that assigns a budget and then calls `GET /budget_usage`, verifying the widget shows the correct spend.
- `test_budget_assignments.py` covers only one case (category mismatch) — the assignment success path with spend tracking is entirely untested.

---

## 5. Configuration and Environment

### Environment Variables

- `BUDGET_USAGE_STALENESS_THRESHOLD_MS` (`config.py`): TTL for spend data freshness in `BudgetUsageService._needs_refresh`. If the most-recent tracking row is older than this threshold, a LiteLLM refresh is triggered.
- `BUDGET_ASSIGNMENT_CACHE_TTL` and `BUDGET_ASSIGNMENT_CACHE_MAX_SIZE` (`config.py`): TTL and size for `_budget_assignment_cache`. Used for proxy request routing, not for the widget path.
- `ENABLE_USER_MANAGEMENT` (`config.py`): Guards budget assignment endpoint (`PUT /{user_id}/budgets`).
- `LITELLM_BUDGET_RESET_WINDOW_MINUTES` (`config.py`): Used by `collect_member_budget_reset_window`, not the widget path.

### Configuration Files

- `src/codemie/configs/config.py` — central config with `BUDGET_USAGE_STALENESS_THRESHOLD_MS`, `BUDGET_ASSIGNMENT_CACHE_TTL`.
- `src/codemie/configs/budget_config.py` — `PredefinedBudgetConfig` and `budget_config` instance. Controls predefined budgets and default budget_id fallbacks.

### Feature Flags and Deployment Concerns

- LiteLLM enterprise feature: the entire spend tracking path is gated on `require_litellm_enabled()` and `is_litellm_enabled()`. If LiteLLM is disabled, `_is_litellm_enabled()` returns False and no refresh is attempted; the widget returns DB data only.
- `ENABLE_USER_MANAGEMENT` must be True for the budget assignment endpoint to function.
- No feature flags specific to the lazy-refresh path or the category-keyed baseline design.

---

## 6. Risk Indicators

- **The exact bug is in `_collect_spend_rows` in `budget_usage_service.py`**: When `assignment.budget_id` is new (no `project_spend_tracking` rows), `existing = None` and `prev_row = prev_day_map.get(assignment.category)` retrieves the old budget's row. If `fresh_spend == prev_row.budget_period_spend` (spend unchanged since last sync), `daily_spend = 0` and `allow_zero = False`, so the row is skipped. `spend_map` remains empty for the new `budget_id`. `_build_budget_usage_rows` returns `budget_period_spend = 0`.
- **Secondary path (LiteLLM reports 0 for new budget)**: If `set_customer_budget_assignment` causes LiteLLM to reset the customer's spend counter (behavior depends on LiteLLM implementation), `fresh_spend = 0 < prev_row.budget_period_spend`. Delta logic treats this as a reset (`daily_spend = 0`). `allow_zero = True` because `_is_reset_transition` returns True. A row IS written with `budget_period_spend = 0`. Widget shows 0.
- **`spend_map` is not reloaded from DB after `_refresh_from_litellm`**: `get_budget_usage` reloads `assignments` and `budgets_map` after rollback, but passes the `spend_map` returned by `_refresh_from_litellm` directly to `_build_budget_usage_rows`. If the refresh path returns an empty dict (no rows inserted), the reload won't help — the widget still shows 0.
- **Category-keyed `prev_day_map` design has no test for cross-budget-id fallback**: The mechanism in `get_latest_before_today_by_budget_categories` was introduced to preserve the delta chain across reassignments. Its interaction with the "skip zero-delta" rule has no test coverage for the reassignment scenario.
- **`_build_budget_usage_rows` has no fallback for missing `spend_map` entry**: `spend_map.get(assignment.budget_id)` returns `None` when no row exists; the code then returns 0.0. No fallback to category-keyed data or provider data.
- **No test for budget assignment → widget scenario**: The critical end-to-end path is entirely untested.
- **Fail-open provider assignment**: If `assign_user_budget` fails silently, the DB assignment succeeds but LiteLLM still has the old budget. The widget would query LiteLLM for a user who is now mapped to the new budget_id in DB but still has spend data under the old customer in LiteLLM — causing a mismatch.
- **`reset_user_budget_spending` has a known risk marker**: Memory note references a race condition in the reset marker write (EPMCDME-12991, release 2.35.0). The reset path writes a zero `project_spend_tracking` row. The same zero-write pattern may be relevant here.

---

## 7. Summary for Complexity Assessment

The bug is isolated to the interaction between two components: `BudgetService.assign_budget_to_user()` in the assignment layer, and `BudgetUsageService._collect_spend_rows()` / `_load_from_db()` in the analytics layer. When a user is assigned a new `budget_id`, the `user_budget_assignments` table is updated correctly, but the `project_spend_tracking` table has no rows for the new `budget_id`. The lazy-refresh path in the Spendings widget fetches fresh spend from LiteLLM, but the delta computation skips writing a row when `daily_spend == 0` — which occurs whenever the spend has not changed since the last snapshot (the common case immediately after assignment). Since `_build_budget_usage_rows` reads `budget_period_spend` directly from the `spend_map` keyed by `budget_id`, and the new `budget_id` has no entry, it returns 0. The condition clears itself once the user makes a new LLM request (which produces a non-zero delta and triggers a row write), matching the observed symptom. Two files are the primary change surface: `budget_usage_service.py` (fix: seed a tracking row for the new `budget_id` when no prior rows exist, regardless of zero delta) and potentially the `assign_budget_to_user` path in `budget_service.py` (fix: trigger or populate a tracking entry at assignment time). The repository layer (`project_spend_tracking_repository.py`) and models are unlikely to need changes.

The task follows a partially established pattern: `reset_user_budget_spending` already performs a similar "force-write a zero marker" operation in `budget_service.py` (lines 1326–1355), including calls to `project_spend_tracking_repository.insert_budget_entries`. This prior art provides a clear implementation template. The analytics layer (lazy-refresh) already supports `allow_zero=True` in `_compute_spend_delta_safe` for reset transitions. The fix can reuse `allow_zero=True` or a new "seed on new budget" flag when `existing is None` and `current_spend_map` has no entry for the assignment's `budget_id`.

Test coverage for the affected domain is moderate: `test_budget_usage_service.py` is substantial for the delta and refresh helpers, but the specific budget-reassignment scenario (new `budget_id`, no prior tracking rows, non-zero LiteLLM spend) has no existing test. A new unit test in `test_budget_usage_service.py` covering `_collect_spend_rows` with `current_spend_map = {}` (new budget) and `prev_day_map` having a non-zero category row is the primary test gap. The fix carries low risk of regression to other paths because the change condition is narrow: it activates only when `existing is None` (no prior tracking rows for the new `budget_id`). The existing zero-delta skip logic for the steady-state case (same budget_id, unchanged spend) is unaffected.
