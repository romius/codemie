# Delete LiteLLM Shared Budget on Project Budget Deletion

**Ticket**: EPMCDME-14248
**Status**: Design approved
**Date**: 2026-08-18

---

## Problem

When a project budget is deleted in CodeMie, its shared child budget (`{budget_id}:shared`) remains orphaned in LiteLLM. The enterprise service's `delete_project_budget()` method is a deliberate no-op with a stale justification claiming "LiteLLM does not currently expose a budget delete facade." This is contradicted by evidence: `delete_override_budget()` already successfully uses `POST /budget/delete` for override child budgets.

**Root cause:**
- Enterprise service `delete_project_budget()` (service.py:580) returns True without calling LiteLLM
- Adapter `delete_override_budget()` (line 1302) proves `/budget/delete` endpoint exists and works
- Core service `_delete_provider_project_budget()` doesn't delete child budgets
- Child budget list is fetched (line 1594) but only used for soft-delete, not provider deletion

---

## Solution

Add a thin `delete_shared_budget()` method that delegates to proven deletion logic and wire it into the project budget deletion flow.

### Changes Required

#### 1. Enterprise Service (`codemie-enterprise`)

**File**: `src/codemie_enterprise/litellm/service.py`

Add `delete_managed_budget()` method:
- Calls `self.api_client.post("/budget/delete", data={"id": budget_id})`
- Prefix guard: refuse budget IDs that don't start with `codemie:project:` (prevents accidental global budget deletion)
- Treat 404/not-found as success (missing budget doesn't block deletion)
- Return False on real failures; log with existing module style
- Mirror `delete_project_member_budget_assignment()` prefix guard pattern (line 753)

**Deployment**: Enterprise package must release first before core can call it.

#### 2. Core Adapter (`codemie`)

**File**: `src/codemie/enterprise/litellm/budget_provider_adapter.py`

Add `delete_shared_budget()` method:
- Thin wrapper calling `service.delete_managed_budget(budget_id=shared_budget_id)`
- Copy exact pattern from `delete_override_budget()` (line 1302):
  - `_get_service()` null-check → skip-log if None
  - Wrap call in `asyncio.to_thread`
  - try/except with structured logging
  - Log keys: `budget_event=shared_budget_delete_completed / _failed`
- Graceful degradation: catch AttributeError if running against old enterprise version

#### 3. Provider Protocol

**File**: `src/codemie/service/budget/provider.py`

Add signature after `delete_override_budget()`:
```python
async def delete_shared_budget(self, *, shared_budget_id: str) -> None:
    ...
```

#### 4. Provider Registry

**File**: `src/codemie/service/budget/provider_registry.py`

Add noop stub in `_NoopBudgetEnforcementProvider`:
```python
async def delete_shared_budget(self, *, shared_budget_id: str) -> None:
    logger.debug(f"noop provider: delete_shared_budget({shared_budget_id})")
```

#### 5. Core Service

**File**: `src/codemie/service/budget/project_budget_service.py`

In `_delete_provider_project_budget()` (line 1503), after the existing `provider.delete_project_budget()` call:

```python
# Delete shared child budget from provider
shared_budget_id = build_shared_project_budget_id(budget_id)
try:
    await provider.delete_shared_budget(shared_budget_id=shared_budget_id)
except Exception as exc:
    logger.warning(
        f"provider_shared_budget_delete_failed budget_id={budget_id} "
        f"shared_budget_id={shared_budget_id} error={exc}"
    )
```

**Key points:**
- Use `build_shared_project_budget_id()` to derive correct ID (already imported, line 44)
- Separate try/except with `provider_shared_budget_delete_failed` warning (fail-open pattern)
- Provider failure never blocks CodeMie soft-delete

---

## Testing

### Enterprise Service Tests
**File**: `codemie-enterprise/tests/litellm/test_service.py` (or new file)

1. `test_delete_managed_budget_happy_path` — successful deletion
2. `test_delete_managed_budget_not_found_is_success` — 404 returns True
3. `test_delete_managed_budget_prefix_guard` — rejects non-CodeMie budget IDs

### Adapter Tests
**File**: `tests/enterprise/litellm/test_budget_provider_adapter.py`

1. `test_delete_shared_budget_posts_to_endpoint` — correct ID passed to `/budget/delete`
2. `test_delete_shared_budget_when_service_none` — logs skip when provider unavailable
3. `test_delete_shared_budget_handles_old_enterprise` — AttributeError caught gracefully

### Service Tests
**File**: `tests/codemie/service/budget/test_project_budget_service_lifecycle.py`

1. Extend `test_delete_project_budget_marks_deleted_and_clears_resolution_cache` (line 92):
   - Assert `provider.delete_shared_budget` called with `build_shared_project_budget_id(budget_id)`
2. New test: provider failure logs warning but CodeMie soft-delete succeeds

---

## Design Rationale

**Why dedicated `delete_shared_budget()` vs reusing `delete_override_budget()`:**
- Semantic clarity: shared and override budgets are conceptually distinct
- Lower risk: no contract changes to existing `delete_override_budget()` behavior
- Same implementation: both delegate to `/budget/delete` endpoint
- Follows existing pattern: separate methods for separate concerns

**Why fail-open pattern:**
- Provider deletion failures already handled this way (line 1523)
- CodeMie DB soft-delete is authoritative; provider sync is best-effort
- User's delete request succeeds even if LiteLLM is unavailable

**Cross-repo sequencing:**
- Enterprise changes must land first (adds `delete_managed_budget()`)
- Core adapter degrades gracefully (getattr/AttributeError) against old enterprise versions
- Once enterprise is deployed, core can safely call new method

---

## Edge Cases Handled

| Scenario | Behavior |
|---|---|
| Shared budget doesn't exist in LiteLLM | Enterprise service treats 404 as success; deletion completes |
| Provider unavailable | Adapter logs skip; CodeMie soft-delete proceeds |
| Provider call fails | Warning logged; CodeMie soft-delete proceeds (fail-open) |
| Old enterprise version | Adapter catches AttributeError; logs skip; continues |
| Multiple child budgets | Only shared budget deleted (override budgets already cleaned via `_delete_provider_member_allocations`) |

---

## Success Criteria

1. ✅ Shared budget (`{budget_id}:shared`) deleted from LiteLLM when project budget deleted
2. ✅ Provider failure doesn't block CodeMie soft-delete (fail-open)
3. ✅ Follows existing `delete_override_budget()` pattern exactly
4. ✅ No regression in existing budget deletion flow
5. ✅ Cross-repo deployment sequence enforced (enterprise first, core second)
6. ✅ 404/not-found treated as success (deletion completes even if budget missing)
