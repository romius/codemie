# Technical Research

**Task**: budget enforcement premium models personal agents platform
**Generated**: 2026-07-31T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Default and Personal premium model budget limits are not enforced for platform requests via personal agents. Premium model requests initiated via personal agents on the CodeMie platform bypass the default premium model budget enforcement, even when categorized correctly under the premium_models category. Project-level budget enforcement works correctly; only the default budget enforcement is broken for platform personal agent flows. The fix must ensure: default premium model budget limits are enforced for all platform-originated premium model requests including via personal agents; requests categorized as premium_models are always evaluated against the applicable default premium budget when no project-specific budget overrides it; a user cannot spend more than the configured default premium model limit; when the limit is reached, further premium model requests are blocked/rejected with a clear budget-exceeded response; enforcement behavior is consistent between platform, CLI, and project-budget flows; existing project-level budget enforcement and CLI budget enforcement remain unaffected; automated regression coverage is added.

---

## 2. Codebase Findings

### Existing Implementations

There are two distinct LLM invocation paths in the codebase. Each has its own budget routing logic, and both are affected by this bug.

**Direct path — platform personal agent flow (primary bug)**

- `src/codemie/enterprise/litellm/llm_factory.py` — constructs LangChain LLM objects used by platform agents; contains `_configure_direct_runtime_overrides()`, `_resolve_direct_budget_category()`, `_resolve_direct_project_budget_runtime()`, and `_get_direct_request_category_budget_id()`; **bug is here**
- `src/codemie/enterprise/litellm/runtime_budget_selection.py` — `RuntimeBudgetMode` enum and `select_runtime_budget_mode()`; governs which of four enforcement modes is selected per direct request
- `src/codemie/enterprise/litellm/credentials.py` — `resolve_litellm_user_credentials()`, `ResolvedLiteLLMUserCredentials` with `is_personal: bool`; when `is_personal=True` the factory calls `_resolve_bypass_mode_stream`, skipping budget enforcement entirely; project-scoped platform budget keys are explicitly not flagged `is_personal`

**Proxy path — HTTP/CLI flow**

- `src/codemie/enterprise/litellm/proxy_router.py` — HTTP reverse proxy to LiteLLM; contains `_resolve_tracking_identity()`, `_create_body_stream_with_optional_injection()`, `BudgetAvailability`; has a secondary bug at line 492 where the condition `if not availability.project_scopes` prevents the default premium budget fallback when any project scope (e.g., `PLATFORM`) is present but `PREMIUM_MODELS` is absent

**Shared budget infrastructure**

- `src/codemie/enterprise/litellm/dependencies.py` — `check_user_budget()`, `get_category_budget_id()`, `get_premium_username()`, `is_premium_model()`, `is_premium_models_enabled()`; all decorated with `@lru_cache` or TTLCache; `is_premium_models_enabled()` returns `False` unless a `premium_models` entry exists in `budgets-config.yaml`
- `src/codemie/enterprise/litellm/budget_categories.py` — `BudgetCategory` enum (PLATFORM, CLI, PREMIUM_MODELS); `build_user_id()` constructs LiteLLM customer key as `{email}_codemie_premium_models`; stale inline comment at line 23 reads `# costly model spending via CLI` but category is used across all paths
- `src/codemie/enterprise/litellm/budget_provider_adapter.py` — `LiteLLMBudgetEnforcementProvider` wraps LiteLLM customer CRUD for budget assignment, spend reset, and runtime resolution
- `src/codemie/service/budget/budget_service.py` — `BudgetService`; CRUD for budgets and user-category assignments; `track_proxy_budget_assignment_for_request()`, `get_all_category_budget_ids_for_request_sync()`
- `src/codemie/service/budget/budget_resolution_service.py` — `BudgetResolutionService`; resolves project vs. global scope; used by both direct and proxy paths
- `src/codemie/service/budget/budget_models.py` — SQLModel ORM: `Budget`, `UserBudgetAssignment`, `ProjectBudgetAssignment`, `ProjectMemberBudgetAssignment`
- `src/codemie/service/budget/budget_enums.py` — `BudgetCategory`, `BudgetScope`, `BudgetType`, `AllocationMode`, `SyncStatus`
- `src/codemie/service/budget/provider.py` — `BudgetEnforcementProvider` abstract interface; `BudgetRuntimeContext`, `BudgetRuntimeProviderResult`, `PersonalBudgetEntry`, `GlobalBudgetState`
- `src/codemie/service/budget/provider_registry.py` — `get_active_provider()` registry singleton
- `src/codemie/service/budget/project_budget_service.py` — project budget lifecycle (create/update/reset/delete allocations)
- `src/codemie/repository/budget_repository.py` — DB CRUD for `Budget` and `UserBudgetAssignment`
- `src/codemie/configs/budget_config.py` — `BudgetConfig` (YAML-backed via pydantic-settings); `budget_config.predefined_budgets` is source of truth for default budget limits
- `src/codemie/configs/config.py` — `Config(BaseSettings)` central settings; `PredefinedBudgetConfig` dataclass; `LITELLM_PREMIUM_MODELS_ALIASES` list
- `src/codemie/service/llm_service/utils.py` — `set_llm_context()` sets `LiteLLMContext` ContextVar per request; determines `effective_project`
- `src/codemie/rest_api/routers/budget_router.py` — REST endpoints for budget CRUD and sync
- `src/codemie/rest_api/routers/project_budget_router.py` — REST endpoints for project-level budget CRUD
- `src/codemie/core/error_constants.py` — `BUDGET_MESSAGE_KEY`, `ErrorCode.LITE_LLM_BUDGET_EXCEEDED_ERROR`, `ErrorCode.AGENT_BUDGET_EXCEEDED`
- `config/budgets/budgets-config.yaml` — currently only declares a `default` budget with `budget_category: platform`; **no `premium_models` entry exists**, causing `is_premium_models_enabled()` to return `False` and the entire premium model budget path to be silently disabled

**Precise bug location in `llm_factory.py`**

In `_configure_direct_runtime_overrides()` (lines 494–509): when both conditions fail — no `ResolvedLiteLLMUserCredentials` and no project runtime overrides — the fallback block unconditionally uses the PLATFORM budget:

```python
platform_budget_id = (
    _get_direct_request_category_budget_id(user_id, LiteLLMBudgetCategory.PLATFORM) if user_id else None
) or get_category_budget_id(LiteLLMBudgetCategory.PLATFORM)
customer = check_user_budget(user_email=user_email, user_id=user_id, budget_id=platform_budget_id)
request_params["model_kwargs"] = {"user": user_email}  # plain email, not premium username
```

`_resolve_direct_budget_category()` — which correctly selects `PREMIUM_MODELS` when the model is premium — is only called inside `_resolve_direct_project_budget_runtime()`, which exits early with `return None, {}, None, None` when `litellm_context.current_project is None`. This is always the case for personal agents on the platform.

**Proxy path secondary bug in `proxy_router.py`**

At `_resolve_tracking_identity()` line 492, the condition `if not availability.project_scopes` prevents the default premium budget fallback when `project_scopes` is non-empty (e.g., contains `PLATFORM`) but does not contain `PREMIUM_MODELS`. The fix must relax this to: `if not availability.project_scopes or BudgetCategory.PREMIUM_MODELS not in availability.project_scopes`.

### Architecture and Layers Affected

- **Integration/Adapter layer**: `llm_factory.py` (direct LLM creation — primary bug), `proxy_router.py` (HTTP proxy path — secondary bug), `credentials.py`
- **Service layer**: `budget_service.py`, `budget_resolution_service.py`, `project_budget_service.py`
- **Repository layer**: `budget_repository.py`, `project_budget_repository.py`
- **Config layer**: `budget_config.py`, `config/budgets/budgets-config.yaml` (config-only prerequisite fix)
- **Background/scheduler layer**: `spend_collector_service.py`, `scheduler.py`, `startup_reconciliation_service.py` (no changes required, but cache TTLs affect test isolation)

### Integration Points

Internal module dependencies (direction):

- `llm_factory` → `budget_resolution_service` (sync resolve + dispatch)
- `llm_factory` → `dependencies` (`check_user_budget`, `get_category_budget_id`, `get_premium_username`)
- `proxy_router` → `budget_resolution_service`
- `proxy_router` → `budget_service` (`track_proxy_budget_assignment_for_request`, `get_all_category_budget_ids_for_request_sync`)
- `proxy_router` → `dependencies` (`check_user_budget`, `get_category_budget_id`, `get_premium_username`)
- `budget_resolution_service` → `project_budget_repository`
- `budget_service` → `budget_repository`
- `budget_service` → `provider_registry` → `budget_provider_adapter`
- `budget_provider_adapter` → `budget_categories` (`build_user_id`, `derive_category_from_user_id`)
- `dependencies` → `budget_config` (predefined budgets)
- `llm_service/utils.set_llm_context` → `SettingsService` → `LiteLLMContext` ContextVar consumed by `llm_factory`
- `rest_api/handlers/assistant_handlers` → `set_llm_context` → ContextVar

External:

- `codemie_enterprise.litellm.LiteLLMService` — enterprise package wrapping LiteLLM REST API; conditionally imported via `HAS_LITELLM`; all budget customer CRUD goes through this

### Patterns and Conventions

- **Provider/adapter pattern**: `BudgetEnforcementProvider` abstract base → `LiteLLMBudgetEnforcementProvider`; registered via `provider_registry.get_active_provider()`
- **TTL cache pattern**: `_budget_assignment_cache` (`TTLCache`, cachetools, 60s TTL) and `_resolution_cache` (`TTLCache`, 60s TTL); must be cleared in tests via `clear_budget_assignment_cache()` / `clear_budget_resolution_cache()`
- **`@lru_cache` on feature-flag helpers**: `is_premium_models_enabled()`, `is_premium_model()`, `is_proxy_budget_enabled()` are frozen at process startup; tests must call `.cache_clear()` when patching `budget_config.predefined_budgets` or model aliases
- **Category-suffixed LiteLLM user id**: `build_user_id(email, PREMIUM_MODELS)` → `{email}_codemie_premium_models`; PLATFORM users carry no suffix; this scheme is declared stable and must not change
- **Two-path budget routing**: proxy path (`proxy_router._create_body_stream_with_optional_injection`) vs. direct path (`llm_factory._configure_direct_runtime_overrides`); both must handle premium model detection
- **Context-var injection**: `LiteLLMContext` ContextVar set by `set_llm_context()` per request; `litellm_context.current_project` being `None` triggers the bug
- **Fail-open on budget check errors**: `check_user_budget()` catches and logs exceptions, returns `None` on failure
- **Three-way budget priority**: project-scoped premium (highest) → default premium (only when no project or project has no premium scope) → platform/CLI

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/llm-providers.md` — covers LiteLLM proxy behavior, provider pluggability, `is_litellm_enabled` gating; directly governs how premium model enforcement must be gated
- `.ai-run/guides/architecture/layered-architecture.md` — confirms budget enforcement logic belongs in the service layer, not routers; relevant to ensuring the fix does not embed enforcement logic in API handlers
- `.ai-run/guides/architecture/service-layer-patterns.md` — services coordinate repositories and providers; no duplicate provider-selection logic at call sites
- `.ai-run/guides/development/configuration-patterns.md` — feature flags gated near assembly points; `LLM_PROXY_BUDGET_CHECK_ENABLED` is the master budget gate; do not read env vars directly in feature code
- `.ai-run/guides/agents/langchain-agent-patterns.md` — use existing `AIToolsAgent` construction; reuse callbacks; don't create parallel runtime agents
- `.ai-run/guides/agents/agent-tools.md` — tool schema adapters live under `src/codemie/agents/tools/`; not directly relevant to budget enforcement

No guide file covers the `premium_models` budget category, personal agent routing, or default vs. project-scoped budget enforcement divergence directly — these are documented only in source code.

### Architectural Decisions

1. **`is_personal` credential bypass** (`credentials.py:149-152`): project-scoped keys must never qualify as personal credentials; prevents `USER_CREDENTIALS_BYPASS` mode.
2. **Project-scoped premium key fallback** (`credentials.py:164-166`): when no personal key is found, fall back to project premium key with `is_personal=False`; enforcement is not bypassed.
3. **Default premium budget only applies when no project scopes exist** (`proxy_router.py:492`): current check `if not availability.project_scopes`; this is the stated root cause of the reported bug per the secondary proxy path.
4. **Predefined budgets as source of truth** (`dependencies.py:474-483`, `budgets-config.yaml`): only `budget_category: platform` currently exists; a `premium_models` entry must be added for `is_premium_models_enabled()` to return `True`.
5. **Category-suffixed user ID scheme** (`budget_categories.py:26-37`): declared stable; LiteLLM customer entries are keyed by it; must not change.
6. **EPMCDME-12959 effective budget formula**: `effective_max_budget = allocation.allocated_max_budget if enforce_limit else budget.max_budget`; must not be broken.
7. **EPMCDME-13157 platform-only budget**: projects can intentionally have only `PLATFORM` scope with 0% for CLI and premium models; the fix must preserve this as a valid state.
8. **`LLM_PROXY_BUDGET_CHECK_ENABLED` defaults to `False` in Helm** (prior task artifact): budget enforcement must be explicitly enabled in deployment environments; tests must target environments where it is `True`.

### Derived Conventions

- The fix must apply to both execution paths (`llm_factory.py` and `proxy_router.py`) independently; they share the same `dependencies.py` helpers but have separate routing logic.
- `get_category_budget_id(BudgetCategory.PREMIUM_MODELS)` is the canonical getter for the default premium budget id.
- `is_premium_models_enabled()` is the required feature gate before any premium budget lookup; skip premium routing entirely if it returns `False`.
- `check_user_budget` must be reached for premium model requests regardless of whether `project_scopes` is empty or not.
- Tests must clear `is_premium_models_enabled.cache_clear()` and `is_premium_model.cache_clear()` after patching predefined budgets or model aliases.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/enterprise/litellm/test_premium_models_budget.py` — premium model feature flag, username injection via `_create_body_stream_with_optional_injection`, `/spending` endpoint premium/CLI metric presence, proxy budget helpers
- `tests/enterprise/litellm/test_budget_categories.py` — `BudgetCategory` enum, `build_user_id()` suffix construction for all three categories
- `tests/enterprise/litellm/test_budget_helpers.py` — LiteLLM-side budget CRUD helpers, service-unavailable fallbacks
- `tests/enterprise/litellm/test_budget_provider_adapter.py` — `LiteLLMBudgetEnforcementProvider` (sync_member_allocation, collect_member_budget_spend, list_personal_budget_assignments, reconcile_budget_reset_timestamps)
- `tests/enterprise/litellm/test_proxy_router.py` — full proxy_router coverage: body reading, model extraction, `_resolve_project_budget_runtime`, `_resolve_tracking_identity` (premium/non-premium/CLI/web), project scope probing, budget-exceeded error rewriting (`_handle_error_response`, `_build_premium_budget_error_body`)
- `tests/enterprise/litellm/test_llm_factory.py` — `create_litellm_chat_model` budget check/skip, `_resolve_direct_budget_category` (project-scope suppression, premium scope selection), `_resolve_direct_project_budget_runtime` (full sync path, missing-context early return, premium model with project premium scope), `_configure_direct_runtime_overrides` (global-integration bypass, non-global personal key user injection)
- `tests/enterprise/litellm/test_litellm_dependencies.py` — `check_user_budget` (cache hit/miss, premium budget id handling, soft/hard limit enforcement), `is_premium_models_enabled`, `is_premium_model`, `get_premium_username`
- `tests/enterprise/litellm/test_credentials.py` — `resolve_litellm_user_credentials`, `is_personal` flag, project-scoped credential skipping
- `tests/codemie/service/budget/test_budget_resolution_service.py` — `BudgetResolutionService.resolve()` cache population, GLOBAL fallback on DB miss
- `tests/codemie/service/budget/test_budget_service.py` — constraint validation, predefined budget startup sync, `backfill_user_budget_assignments`, `get_all_category_budget_ids_for_request_sync` cache fast-path

### Testing Framework and Patterns

- pytest 8.3.x, pytest-asyncio 0.23.x, pytest-mock 3.14.x, pytest-httpx 0.35.x
- Async tests use explicit `@pytest.mark.asyncio` per test (not `asyncio_mode = auto`)
- `unittest.mock.MagicMock` / `AsyncMock` / `patch` / `patch.object` for all external dependencies
- `types.SimpleNamespace` for lightweight provider/service stubs
- Module-level patch helper functions: `_patch_budget_name()`, `_patch_aliases()`, `_patch_cli_budget_name()`
- `autouse=True` fixtures for `lru_cache` clearing: `clear_premium_caches` fixture pattern in `test_premium_models_budget.py`
- Module-level `setup_function()` for resolution-cache reset in `test_budget_resolution_service.py`
- Factory helpers inlined per test file: `_make_budget()`, `_make_service()`, `_make_personal_entry()`
- `pytest.mark.skipif(not HAS_LITELLM, ...)` gates for enterprise-only tests
- Environment injected via `pytest.ini` `[env]` section (ENV=local, PG_URL, REPOS_LOCAL_DIR)

### Coverage Gaps

- **`_configure_direct_runtime_overrides` with premium model + no project context + no credentials**: no test asserts that `check_user_budget` is called with the premium category budget ID in this scenario (exact bug path)
- **`create_litellm_chat_model` end-to-end with premium model and no project context**: no test verifies the premium budget ID is passed to `check_user_budget` rather than the platform budget ID
- **`_resolve_direct_budget_category` with no project scopes and a configured global premium budget**: tests cover scope suppression but not the successful global-default premium selection path in isolation
- **Proxy router `_create_body_stream_with_optional_injection` for platform personal agent with premium model**: `test_premium_budget_takes_precedence_over_proxy_budget` patches `check_user_budget` away rather than asserting the exact `budget_id` argument used when `project_scopes` is non-empty but has no `PREMIUM_MODELS` scope
- **`BudgetService._default_budget_id_for_category` for the `premium_models` category** specifically: untested in isolation
- **`budget_service.track_proxy_budget_assignment_for_request` for `PREMIUM_MODELS` category**: no dedicated test
- **Blocking/rejection response when default premium budget limit is reached via platform personal agent path**: error-rewriting tests cover downstream response body, not the pre-request `check_user_budget` raise path

---

## 5. Configuration and Environment

### Environment Variables

- `LLM_PROXY_ENABLED` — master switch; if `False`, proxy is fully disabled and no budget enforcement runs
- `LLM_PROXY_BUDGET_CHECK_ENABLED` — controls LiteLLM-side hard enforcement; defaults to `False` in Helm overlays; must be `True` for enforcement to block requests
- `LITELLM_PREMIUM_MODELS_ALIASES` — comma-style list of model name substrings (e.g., `["opus","claude-4"]`) that trigger `PREMIUM_MODELS` category; empty list disables the feature entirely
- `LITELLM_MSG_BUDGET_EXCEEDED` — user-facing message when hard budget limit is hit; used in `AGENT_MSG_BUDGET_EXCEEDED` for agent flows
- `BUDGET_ASSIGNMENT_CACHE_TTL` — TTL (default 60s) for user→category→budget_id lookup cache; budget assignment changes take up to 60s to propagate
- `BUDGET_RESOLUTION_CACHE_TTL` — TTL (default 60s) for project-scope resolution cache; relevant for test isolation
- `LITELLM_CUSTOMER_CACHE_TTL` — TTL (default 300s) for LiteLLM customer info cache in `check_user_budget`
- `LITELLM_FAIL_OPEN_ON_503` — if `True` (default), a LiteLLM 503 error silently passes budget enforcement; this means LiteLLM downtime still allows overspending after the fix
- `LITELLM_USER_CREDENTIALS_CACHE_TTL` — TTL (default 600s) for personal credentials cache; a personal-key removal takes up to 10 minutes to take effect
- `LITE_LLM_URL` / `LITE_LLM_APP_KEY` / `LITE_LLM_PROXY_APP_KEY` / `LITE_LLM_MASTER_KEY` — LiteLLM proxy endpoint and API keys
- `LLM_PROXY_BUDGET_RECONCILIATION_ENABLED` / `LITELLM_SPEND_COLLECTOR_ENABLED` / `LITELLM_BUDGET_RESET_TRACKER_ENABLED` — scheduled job toggles for spend collection and budget reset

### Configuration Files

- `config/budgets/budgets-config.yaml` — source of truth for predefined default budgets; currently only has `budget_category: platform`; **must have a `budget_category: premium_models` entry added** for enforcement to activate; path is resolved as `Path(__file__).parents[3] / "config/budgets"` — container must mount this directory
- `src/codemie/configs/budget_config.py` — `BudgetConfig` YAML-backed via pydantic-settings `YamlConfigSettingsSource`; `predefined_budgets` list is loaded at startup
- `src/codemie/configs/config.py` — `Config(BaseSettings)` central settings class; `PredefinedBudgetConfig` dataclass; `BUDGETS_CONFIG_DIR`

### Feature Flags and Deployment Concerns

- `is_premium_models_enabled()` — `@lru_cache`; returns `True` only when a `premium_models` budget entry exists in `budgets-config.yaml`; frozen at process startup; yaml changes require rolling restart or explicit `cache_clear()`
- `is_premium_model(model)` — `@lru_cache`; per-model check against `LITELLM_PREMIUM_MODELS_ALIASES`; changing the alias list requires `.cache_clear()` or restart
- `LITELLM_FAIL_OPEN_ON_503` — fail-open toggle; default `True`; after the fix, LiteLLM downtime still bypasses enforcement
- `LLM_PROXY_SHARED_ASSET_PROJECT_BUDGET_ROUTING_ENABLED` — routes shared-asset requests through project budget
- `ENABLE_USER_MANAGEMENT` — affects project budget routing when project scopes are resolved
- No budget-related env vars are referenced in `Dockerfile` or `docker-compose.yml`; all config must be injected at runtime via environment or `.env` file

---

## 6. Risk Indicators

- **Two separate execution paths both require fixes**: `llm_factory.py` (direct/platform path) and `proxy_router.py` (HTTP proxy path) have independent routing logic; fixing only one leaves the other unchanged; the reported bug primarily manifests on the `llm_factory` path for in-process personal agents
- **Config prerequisite gates the entire fix**: `config/budgets/budgets-config.yaml` has no `premium_models` entry; `is_premium_models_enabled()` returns `False` globally; without adding this entry, no code change in `llm_factory.py` or `proxy_router.py` will activate premium budget enforcement
- **`@lru_cache` frozen at startup on feature-flag helpers**: `is_premium_models_enabled()` and `is_premium_model()` are process-startup-frozen; regression tests must call `.cache_clear()` in `autouse` fixtures or `setup_function()`; failing to do so causes false-passing tests
- **TTL caches (60s) in production paths**: `_budget_assignment_cache` and `_resolution_cache` use 60s TTLs; budget assignment changes take up to 60s to propagate; test isolation requires explicit `clear_budget_assignment_cache()` / `clear_budget_resolution_cache()` calls
- **`LITELLM_FAIL_OPEN_ON_503` defaults to `True`**: LiteLLM unavailability bypasses enforcement silently; the fix routes premium requests through `check_user_budget()` which is subject to this fail-open behavior
- **Stale inline comment at `budget_categories.py:23`**: reads `# costly model spending via CLI` but `PREMIUM_MODELS` is used across all paths; may mislead future developers; should be corrected as part of this task
- **EPMCDME-13157 platform-only budget is a valid state**: projects intentionally having only `PLATFORM` scope with 0% for premium models must remain valid; the condition relaxation in `proxy_router.py` must not break this pattern
- **`LITELLM_USER_CREDENTIALS_CACHE_TTL` at 600s**: if personal key removal is part of any remediation step, enforcement gap persists for up to 10 minutes
- **`budgets-config.yaml` path hard-coded relative to source tree**: containers that do not mount the `config/` directory will fail at startup; this is an existing constraint, not introduced by the fix
- **No existing test for the primary bug path**: `_configure_direct_runtime_overrides` with premium model, no project context, and no credentials is completely untested; the regression test is a mandatory deliverable
- **`LLM_PROXY_BUDGET_CHECK_ENABLED` defaults to `False` in Helm**: regression tests must run in an environment where this is `True` or mock the enforcement gate explicitly

---

## 7. Summary for Complexity Assessment

This task targets a budget enforcement bypass that affects two independent execution paths. The **primary path** is `src/codemie/enterprise/litellm/llm_factory.py`, function `_configure_direct_runtime_overrides()`, which is invoked when platform personal agents call LLM models directly inside the CodeMie process. The fallback branch (lines 494–509) unconditionally uses the PLATFORM budget regardless of whether the requested model is premium; `_resolve_direct_budget_category()` — which would correctly select `PREMIUM_MODELS` — is only reachable through `_resolve_direct_project_budget_runtime()`, which exits early when `litellm_context.current_project is None`, which is always the case for personal agents. The **secondary path** is `src/codemie/enterprise/litellm/proxy_router.py`, function `_resolve_tracking_identity()`, where the condition `if not availability.project_scopes` (line 492) prevents the default premium budget fallback when any project scope is present but `PREMIUM_MODELS` is absent. A **prerequisite config fix** is also required: `config/budgets/budgets-config.yaml` must have a `premium_models` budget entry added before `is_premium_models_enabled()` returns `True` and any enforcement logic activates. Total direct file change surface: `llm_factory.py`, `proxy_router.py`, `budgets-config.yaml`, plus at minimum one new test class in `tests/enterprise/litellm/test_llm_factory.py` and likely additions to `tests/enterprise/litellm/test_proxy_router.py` and `tests/enterprise/litellm/test_premium_models_budget.py`.

The implementation follows well-established patterns already used on adjacent code paths; the proxy path already handles premium model routing correctly and can serve as the reference implementation. The direct path fix is a narrowly scoped change: extend `_configure_direct_runtime_overrides()` to call `_resolve_direct_budget_category()` (or equivalent inline check) before falling back to the platform budget, and use `get_premium_username()` / `get_category_budget_id(PREMIUM_MODELS)` in the fallback when the model is premium. No new abstractions or architectural patterns are required. The task does not introduce novel dependencies.

Test coverage posture is mixed: the budget and premium model domain has substantial existing test infrastructure (`test_proxy_router.py` and `test_llm_factory.py` are comprehensive), but the exact bug scenario — `_configure_direct_runtime_overrides` with a premium model, no project context, no user credentials — is entirely untested. The `@lru_cache` pattern on feature-flag helpers is a known testing constraint that prior tests handle correctly with `autouse` cache-clearing fixtures; new tests must follow the same pattern. The primary risk factors influencing complexity are the two-path nature of the fix, the config prerequisite that must precede code changes, and the cache isolation requirements in the regression tests.
