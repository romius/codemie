# Technical Research

**Task**: budget litellm provider deletion
**Generated**: 2026-08-18T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Fix EPMCDME-14248: Delete LiteLLM shared budget on project budget deletion. Root cause: service.delete_project_budget() is a no-op claiming LiteLLM doesn't expose /budget/delete, but adapter already uses it for override budgets. Need to add delete_managed_budget() in enterprise service, wire delete_shared_budget() in core adapter, and call it from project_budget_service._delete_provider_project_budget().

---

## 2. Codebase Findings

### Existing Implementations

**Core Service Layer**
- `src/codemie/service/budget/project_budget_service.py` — orchestrates project-budget CRUD + provider sync
  - `delete_project_budget()` — main deletion orchestrator
  - `_delete_provider_project_budget()` (line 1503) — calls provider deletion methods
  - `_soft_delete_project_budget_rows()` — CodeMie DB soft-delete
- `src/codemie/service/budget/budget_models.py` — domain models
  - `build_shared_project_budget_id()` — constructs `{budget_id}:shared` identifier
  - `build_override_project_budget_id()` — constructs `{budget_id}:user:{user_id}` identifier

**Provider Abstraction Layer**
- `src/codemie/service/budget/provider.py` — `BudgetEnforcementProvider` Protocol definition
  - `delete_project_budget()` signature
  - `delete_override_budget()` signature (already exists for child budgets)
- `src/codemie/service/budget/provider_registry.py` — registry and noop implementation
  - `_NoopBudgetEnforcementProvider` — stub implementations
  - `get_active_provider()` — factory method

**LiteLLM Provider Implementation**
- `src/codemie/enterprise/litellm/budget_provider_adapter.py` — concrete LiteLLM provider
  - `delete_project_budget()` (line 1100) — calls enterprise service but shared budget not deleted
  - `delete_override_budget()` (line 1302) — **working template** that calls `/budget/delete` directly
  - `delete_member_allocation()` — deletes LiteLLM customer objects
  - Pattern: wraps enterprise calls with `asyncio.to_thread`

**Enterprise Service (external package)**
- `codemie-enterprise/src/codemie_enterprise/litellm/service.py`
  - `delete_project_budget()` (line 580) — **current no-op** with stale justification comment
  - `api_client.post("/budget/delete", ...)` — proven working in `delete_override_budget` adapter method

**Repository Layer**
- `src/codemie/repository/budget_repository.py` — budget DB access
  - `list_active_child_budgets()` — fetches child budget rows (shared + overrides)
- `src/codemie/repository/project_budget_repository.py` — project assignment DB access

**REST API Layer**
- `src/codemie/rest_api/routers/project_budget_router.py` — `DELETE /project-budgets/{budget_id}`

### Architecture and Layers Affected

**Layers touched by this fix:**
1. **Enterprise Service** (`codemie-enterprise`) — add `delete_managed_budget()` method
2. **Provider Adapter** — add `delete_shared_budget()` or reuse `delete_override_budget()` for all child budgets
3. **Provider Protocol** — declare new method signature (if adding separate method)
4. **Core Service** — wire child budget deletion into `_delete_provider_project_budget()`

**Call chain:**
```
DELETE /project-budgets/{id}
└─ project_budget_service.delete_project_budget()
   └─ _delete_provider_project_budget()
      ├─ provider.delete_project_budget()          (virtual key deletion)
      └─ [NEW] provider.delete_shared_budget()     (shared child budget)
         └─ LiteLLMService.delete_managed_budget()
            └─ api_client.post("/budget/delete")
```

### Integration Points

**Internal dependencies:**
- `ProjectBudgetService` → `get_active_provider()` → `LiteLLMBudgetEnforcementProvider`
- `LiteLLMBudgetEnforcementProvider` → `get_litellm_service_or_none()` → `LiteLLMService`
- Child budget list already fetched at line 1594 via `budget_repository.list_active_child_budgets`

**External service connections:**
- LiteLLM proxy API via `LiteLLMAPIClient`
- Endpoint: `POST /budget/delete` with `{"id": "<budget_id>"}` payload
- Already proven working for override budgets in `delete_override_budget()` method

**Cross-repo dependency:**
- Core repo (`codemie`) depends on `codemie-enterprise` package (version 2.3.36)
- Enterprise changes must be released first; core adapter should degrade gracefully if method missing

### Patterns and Conventions

**Provider abstraction pattern:**
- Protocol-based (`BudgetEnforcementProvider` in `provider.py`)
- Singleton registry in `provider_registry.py` with noop + concrete implementations
- Core services MUST NOT import `codemie.enterprise.litellm` directly — go through `get_active_provider()`

**Async wrapping pattern:**
- All synchronous enterprise service calls wrapped with `asyncio.to_thread` in adapter
- Example: `await asyncio.to_thread(service.delete_managed_budget, budget_id=...)`

**Child budget hierarchy:**
- One parent budget → multiple child budgets:
  - Shared child: `{budget_id}:shared` (one per project budget)
  - Override children: `{budget_id}:user:{user_id}` (one per member allocation)
- Child budget IDs constructed via `build_shared_project_budget_id()` / `build_override_project_budget_id()`

**Fail-open deletion pattern:**
- Provider operations during deletion logged as warnings, not raised
- Existing try/except at `project_budget_service.py:1523` wraps provider deletion
- CodeMie DB soft-delete proceeds even if LiteLLM deletion fails

**Working template for new deletion:**
- `delete_override_budget()` (adapter line 1302) is the exact pattern to copy:
  - `_get_service()` null-check → skip-log if None
  - `try/except` around `asyncio.to_thread(service.api_client.post, "/budget/delete", ...)`
  - `budget_event=override_budget_delete_completed / _failed` log keys

---

## 3. Documentation Findings

### Guides and Architecture Docs

**Relevant guides in `.ai-run/guides/`:**
- `architecture/layered-architecture.md` — confirms router→service→repository layering
- `architecture/service-layer-patterns.md` — service orchestration conventions
- `data/repository-patterns.md` — repository access patterns
- `testing/testing-patterns.md` — pytest + async mock conventions

### Architectural Decisions

**Provider boundary decision** (documented in `budget_provider_adapter.py:22–23`):
- Core services MUST NOT import from `codemie.enterprise.litellm` directly for budget operations
- All provider interactions go through `get_active_provider()` registry

**Stale justification in enterprise service** (`service.py:580`):
- Method `delete_project_budget()` is documented as deliberate no-op
- Docstring claims "LiteLLM does not currently expose a budget delete facade here"
- **Contradicted by evidence**: `delete_override_budget()` already uses `POST /budget/delete` successfully (adapter line 1311)
- This is a documentation drift issue — the endpoint exists and works

**Child budget design:**
- `{budget_id}:shared` and `{budget_id}:user:{user_id}` are treated as internal LiteLLM budget objects
- Separate from the virtual key (project API key)
- Virtual key deletion happens via `_delete_project_api_key()`; child budget deletion is separate concern

### Derived Conventions

**Deletion sequencing:**
1. Delete provider-side resources (customers, keys, budgets) — fire-and-forget with warnings
2. Soft-delete CodeMie DB rows — always succeeds regardless of provider outcome
3. Child budget list already fetched before deletion (line 1594) but not passed to provider delete step

**Method naming:**
- `delete_project_budget()` — deletes main project budget resources (currently just virtual key)
- `delete_override_budget()` — deletes one override child budget by ID
- Proposed: `delete_shared_budget()` — deletes one shared child budget by ID
- Alternative: iterate child_budgets and call `delete_override_budget()` for all (shared + overrides)

---

## 4. Testing Landscape

### Existing Coverage

**Service layer tests:**
- `tests/codemie/service/budget/test_project_budget_service_lifecycle.py`
  - `test_delete_project_budget_marks_deleted_and_clears_resolution_cache` (line 92)
  - Covers the delete path but **does NOT assert child budget deletion on provider**
- `tests/codemie/service/budget/test_project_budget_service.py` — general service tests
- `tests/codemie/service/budget/test_project_budget_service_group_creation.py` — group creation

**Provider adapter tests:**
- `tests/enterprise/litellm/test_budget_provider_adapter.py`
  - Covers `sync_member_allocation`
  - **No test for `delete_project_budget` or `delete_override_budget`**

### Testing Framework and Patterns

**Framework:**
- pytest + pytest-asyncio (from `pyproject.toml:157`)
- `unittest.mock.AsyncMock`, `MagicMock`, `patch`, `SimpleNamespace`

**Patterns observed:**
- `SimpleNamespace` for lightweight budget/assignment/allocation fixtures
- `patch(…, new=AsyncMock())` for repository and provider method mocking
- `patch("…get_active_provider", return_value=mock_provider)` to inject fake providers
- `pytest.mark.asyncio` on all async tests

### Coverage Gaps

**Critical gaps for this fix:**
- No test asserts that child budget IDs (shared + overrides) are deleted from LiteLLM on `delete_project_budget`
- No test for `LiteLLMBudgetEnforcementProvider.delete_project_budget` behavior in `test_budget_provider_adapter.py`
- No test for shared budget ID being passed to `/budget/delete` endpoint
- No test for "budget not found" (404) counting as success (AC #6)
- No test for provider failure not blocking CodeMie soft-delete (AC #2)

**Tests to add:**
1. Enterprise service: `test_delete_managed_budget_happy_path`, `test_delete_managed_budget_not_found_is_success`, `test_delete_managed_budget_prefix_guard`
2. Adapter: `test_delete_shared_budget_posts_to_endpoint`, `test_delete_shared_budget_when_service_none`
3. Service: extend `test_delete_project_budget_marks_deleted_and_clears_resolution_cache` to assert `provider.delete_shared_budget` called with correct ID
4. Service: new test for group deletion removes one shared budget per category
5. Service: new test for provider failure logged as warning, CodeMie rows still soft-deleted

---

## 5. Configuration and Environment

### Environment Variables

**LiteLLM connection:**
- Base URL and API key consumed by `LiteLLMAPIClient`
- Exact env var names in `src/codemie/enterprise/litellm/config.py`
- Templates in `.env.example`

### Configuration Files

- `litellm_config.yaml` — LiteLLM proxy configuration
- `src/codemie/configs/budget_config.py` — budget config values

### Feature Flags and Deployment Concerns

- No feature flags directly gating budget deletion
- **Deployment sequencing**: enterprise package must be released first with `delete_managed_budget()` before core can call it
- Core adapter should degrade gracefully (getattr check or try/except AttributeError) if running against older enterprise package
- Current enterprise version: `2.3.36` (from `pyproject.toml`)

---

## 6. Risk Indicators

- **Stale no-op justification** — `service.delete_project_budget()` docstring claims LiteLLM doesn't expose `/budget/delete`, but this is contradicted by working usage in `delete_override_budget()`
- **Missing test coverage** — no existing test asserts child budget deletion on provider; gap must be filled
- **Cross-repo dependency** — enterprise changes must land first; core must handle version mismatch gracefully
- **Identifier confusion** — `delete_project_budget()` currently receives `provider_budget_ref` (virtual key alias), not budget ID; new method must use correct identifier from `build_shared_project_budget_id()`
- **Multiple child budgets** — one shared + N overrides; fix must handle all children, not just shared
- **Fail-open pattern** — provider deletion failures must not block CodeMie soft-delete; existing try/except at line 1523 handles this for main deletion but new child deletion needs separate exception handling
- **Prefix guard needed** — `delete_managed_budget()` should refuse non-CodeMie-managed budget IDs (guard like `delete_project_member_budget_assignment` at service.py:753) to prevent accidental deletion of global budgets

---

## 7. Summary for Complexity Assessment

**Layers touched:** 4 layers across 2 repositories — enterprise service (1 new method), core provider protocol (1 signature addition or reuse existing), core adapter (1 new method), core service (wire child deletion into existing flow). REST API and repository layers unchanged.

**File change surface:** 5-7 files estimated — enterprise `service.py`, core `provider.py`, `provider_registry.py`, `budget_provider_adapter.py`, `project_budget_service.py`, plus 3-5 test files. Child budget list already fetched (line 1594); no new query needed.

**Technical novelty:** Low — exact working template exists in `delete_override_budget()`. The `/budget/delete` endpoint is proven functional. Pattern is copy-paste with identifier swap. No new integrations, no schema changes, no new failure modes beyond what's already handled.

**Test coverage posture:** Gap exists but straightforward to fill — mock provider method, assert called with correct ID, verify fail-open behavior. Existing lifecycle test provides structure; needs assertion added. Enterprise repo needs new test file or new test in existing suite.

**Key risk factors:** Cross-repo sequencing (enterprise first, core second with graceful degradation), stale documentation must be updated, identifier derivation must use `build_shared_project_budget_id()` not `provider_budget_ref`, prefix guard required to prevent global budget deletion, and all N child budgets (shared + overrides) must be deleted not just shared. Fail-open pattern already in place but new exception block needed for child deletion loop.
