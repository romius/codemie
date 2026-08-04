# Spec: EPMCDME-13794 — Premium Model Budget Enforcement for Personal Agents

**Status**: Approved  
**Date**: 2026-07-31  
**Branch**: `EPMCDME-13794_premium-model-budget-enforcement`

---

## Problem

Default premium model budget limits are not enforced when platform users send requests to premium models through personal agents. The requests are correctly attributed to the `premium_models` category but bypass enforcement because:

1. **Config gate is closed**: `config/budgets/budgets-config.yaml` has no `premium_models` entry, so `is_premium_models_enabled()` returns `False` globally and `get_category_budget_id(PREMIUM_MODELS)` returns `None` — no budget ID exists to enforce against.

2. **Direct path (primary bug)**: `llm_factory._configure_direct_runtime_overrides()` — the no-project-context fallback block unconditionally uses the PLATFORM budget. `_resolve_direct_budget_category()` (which correctly selects `PREMIUM_MODELS`) is only reachable via `_resolve_direct_project_budget_runtime()`, which exits early when `litellm_context.current_project is None`. This is always `None` for personal agents on the platform.

The proxy path (HTTP/CLI) already has correct premium routing logic for personal agents (no project context) — it only fails because the config gate is closed.

---

## Scope

Three file changes + tests. No new modules. `proxy_router.py` is not changed.

### Out of scope

- Changing premium enforcement for project-scoped agents where the project has only PLATFORM scope (EPMCDME-13157: valid state, must not be broken).
- `proxy_router.py` condition changes.
- Any changes to the three-way budget priority order.

---

## Budget Priority (unchanged)

```
project-scoped PREMIUM_MODELS budget  (highest — project has PREMIUM_MODELS scope)
    ↓
default PREMIUM_MODELS budget          (new enforcement: personal agents with no project context)
    ↓
PLATFORM / CLI budget                  (existing fallback)
```

---

## Changes

### 1. `config/budgets/budgets-config.yaml`

Add a `premium_models` predefined budget entry. This is the prerequisite gate — without it, `is_premium_models_enabled()` returns `False` and no code change activates enforcement.

```yaml
- budget_id: default_premium_models
  name: Default Premium Models Budget
  description: Default budget for premium model usage across all platform flows.
  soft_budget: 10.0
  max_budget: 20.0
  budget_duration: 30d
  budget_category: premium_models
```

Operators configure `soft_budget` and `max_budget` per deployment. The entry must exist for enforcement to activate; the specific limit values are deployment-specific.

### 2. `src/codemie/enterprise/litellm/llm_factory.py`

**2a. Generalize `_mirror_platform_budget_assignment` → `_mirror_budget_assignment(category, ...)`**

Accept a `category: CoreBudgetCategory` parameter instead of hardcoding `PLATFORM`. The default budget ID fallback uses `get_category_budget_id(category)` rather than a hardcoded `PLATFORM` lookup. The existing call site for the PLATFORM path passes `category=CoreBudgetCategory.PLATFORM` — no behavior change.

**2b. Inline premium guard in `_configure_direct_runtime_overrides()` fallback**

Before the existing PLATFORM budget block, add:

```python
# Premium model budget enforcement — mirrors proxy_router._resolve_tracking_identity pattern
if user_email:
    premium_username = get_premium_username(user_email, llm_model_details.base_name)
    if premium_username is not None:
        premium_budget_id = (
            _get_direct_request_category_budget_id(user_id, LiteLLMBudgetCategory.PREMIUM_MODELS)
            if user_id else None
        ) or get_category_budget_id(LiteLLMBudgetCategory.PREMIUM_MODELS)
        if premium_budget_id:
            logger.info(
                f"budget_event=runtime_mode_selected component=litellm_llm_factory "
                f"user_id={user_id!r} username={user_email!r} model={llm_model_details.base_name!r} "
                f"mode={RuntimeBudgetMode.GLOBAL_OR_PERSONAL_BUDGET.value!r} "
                f"budget_category={LiteLLMBudgetCategory.PREMIUM_MODELS.value!r} "
                f"litellm_customer_key={premium_username!r}"
            )
            customer = check_user_budget(user_email=premium_username, user_id=user_id, budget_id=premium_budget_id)
            request_params["model_kwargs"] = {"user": premium_username}
            _mirror_budget_assignment(
                user_id=user_id, customer=customer, category=CoreBudgetCategory.PREMIUM_MODELS
            )
            return
```

Guard semantics and fall-through chain:
- `get_premium_username()`: returns `None` for non-premium models → falls through to PLATFORM unchanged.
- `_get_direct_request_category_budget_id(user_id, PREMIUM_MODELS)`: returns the user's personal `PREMIUM_MODELS` budget assignment if one exists; this path does NOT require a default config entry.
- `get_category_budget_id(PREMIUM_MODELS)`: default fallback; returns `None` when no `premium_models` entry exists in `budgets-config.yaml` → falls through to PLATFORM unchanged.
- `premium_budget_id` is `None` when neither personal assignment nor default config is present → falls through to PLATFORM unchanged.
- Personal assignment wins over default: `_get_direct_request_category_budget_id() or get_category_budget_id()` — the `or` only reaches the default when the personal lookup returns `None`.
- `is_premium_models_enabled()` is NOT used as a top-level gate in the guard. The same pattern is used in `proxy_router._resolve_tracking_identity()`. Removing it from the outer check means a user's explicit personal premium budget is enforced even if no default config entry exists. The config entry is still required for the *default* fallback to activate.
- Imports added to the `from .dependencies import` line: `get_premium_username`.

### 3. `src/codemie/enterprise/litellm/budget_categories.py`

Fix stale comment at line 23:

```python
# Before:
PREMIUM_MODELS = "premium_models"  # costly model spending via CLI

# After:
PREMIUM_MODELS = "premium_models"  # premium model spend — applies to platform, proxy, and CLI paths
```

---

## Testing

### New test class in `tests/enterprise/litellm/test_llm_factory.py`

`TestConfigureDirectRuntimeOverridesPremiumNoProject` — covers the exact bug path: premium model, `litellm_context.current_project = None`, no credentials.

| Test | What it asserts |
|---|---|
| `test_premium_model_no_project_context_uses_premium_budget` | `check_user_budget` called with `premium_username` and `premium_budget_id`; `model_kwargs["user"] == premium_username` |
| `test_non_premium_model_no_project_context_uses_platform_budget` | PLATFORM budget path unchanged |
| `test_premium_model_no_budget_id_anywhere_falls_through_to_platform` | Both `_get_direct_request_category_budget_id` and `get_category_budget_id(PREMIUM_MODELS)` return `None` → PLATFORM budget used |
| `test_premium_model_personal_assignment_wins_over_default` | User has a personal `PREMIUM_MODELS` assignment AND a default config budget both present; asserts `check_user_budget` is called with the *personal* budget ID, not the default; verifies `_get_direct_request_category_budget_id` is preferred over `get_category_budget_id` |
| `test_mirror_budget_assignment_called_with_premium_category` | After a premium model request, `_mirror_budget_assignment` is called with `category=CoreBudgetCategory.PREMIUM_MODELS` (not `PLATFORM`); asserts fire-and-forget DB mirror uses the correct category |
| `test_premium_model_personal_assignment_enforced_without_default_config` | User has personal `PREMIUM_MODELS` assignment; `get_category_budget_id(PREMIUM_MODELS)` returns `None` (no default config); asserts premium budget is still enforced using the personal assignment — confirms `is_premium_models_enabled()` is NOT gating the personal path |

All tests use an `autouse=True` fixture that calls `is_premium_models_enabled.cache_clear()` and `is_premium_model.cache_clear()` in setup.

### Extension in `tests/enterprise/litellm/test_premium_models_budget.py`

| Test | What it asserts |
|---|---|
| `test_proxy_path_personal_agent_premium_model_enforced` | `project_scopes = set()`, premium budget configured → `_resolve_tracking_identity` returns `PREMIUM_MODELS` category with correct `premium_username` |

---

## Acceptance Criteria Coverage

| Criterion | How met |
|---|---|
| Default premium budget enforced for personal agents | Config entry ensures `get_category_budget_id(PREMIUM_MODELS)` returns non-None; llm_factory guard routes to premium budget |
| Requests categorized as `premium_models` evaluated against default budget when no project override | llm_factory and proxy paths both use `get_category_budget_id(PREMIUM_MODELS)` as fallback |
| User cannot spend beyond default premium limit | `check_user_budget()` enforces the configured `max_budget` via LiteLLM |
| Limit reached → requests blocked with budget-exceeded response | Existing `check_user_budget()` / LiteLLM error rewriting handles this; no new code needed |
| Enforcement consistent across platform/CLI/project-budget flows | Config fix enables proxy path for CLI; llm_factory fix enables direct path for platform personal agents |
| Project-level enforcement unaffected | Guard only fires in the no-project-context fallback; project runtime overrides still return early before the guard |
| CLI enforcement unaffected | CLI requests go through proxy_router, which already handles `project_scopes = set()` correctly |
| Regression coverage added | 7 new tests (6 in `test_llm_factory.py` + 1 in `test_premium_models_budget.py`) covering the bug path, all guard fall-through cases, personal-vs-default priority, and mirror category correctness |
| Budget attribution accurate | `premium_username` (`{email}_codemie_premium_models`) used for LiteLLM customer key; mirrors to Codemie DB via `_mirror_budget_assignment` |

---

## Risk Mitigations

| Risk | Mitigation |
|---|---|
| `@lru_cache` frozen at startup | `autouse` fixture calls `cache_clear()` in all new tests |
| `is_premium_models_enabled()` returns `False` in prod if config not deployed | Guard falls through to PLATFORM — safe behavior, no regression |
| `premium_budget_id` is `None` (config deployed but ID lookup fails) | Guard falls through to PLATFORM — safe behavior |
| EPMCDME-13157 platform-only projects affected | Guard is in the no-project-context fallback only; project runtime overrides return before this block |
| EPMCDME-12959 effective budget formula | `check_user_budget()` call is unchanged; formula is inside LiteLLM |
