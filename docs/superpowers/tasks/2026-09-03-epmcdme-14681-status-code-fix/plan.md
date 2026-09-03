# EPMCDME-14681: Negative Category Pct Returns HTTP 400

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Return HTTP 400 (not 422) when a negative `pct` value is submitted to PUT /v1/admin/project-budget-groups/{group_id}.

**Architecture:** Remove the Pydantic `ge=0` floor from `CategoryBudgetSpecUpdate.pct` so negative values reach the service layer, then add an explicit guard in `_validate_group_categories()` before the sum check, raising `ExtendedHTTPException(code=400)` — consistent with the existing `sum_off` and `no_platform` checks.

**Tech Stack:** Python 3, FastAPI, Pydantic v2, pytest

**Spec:** inline requirements (EPMCDME-14681, status-code fix only)

Commit per task using the repository's existing convention.

## Global Constraints

- project_name 403 issue is out of scope — do not touch 403 paths or auth checks
- `CategoryBudgetSpec` (create path) is out of scope — only `CategoryBudgetSpecUpdate` changes
- Both router and service changes must ship in the same commit (removing `ge=0` alone would silently allow negative values through)
- Existing valid-path behaviour must remain unaffected (AC6)

---

## Acceptance criteria

- AC4: Negative `pct` on PUT /v1/admin/project-budget-groups/{group_id} returns HTTP 400, not 422.
- AC5: Error is raised via `ExtendedHTTPException(code=400)`, consistent with `sum_off` and `no_platform` cases.
- AC6: Valid category update scenarios are unaffected.
- AC7: `test_system_rejects_invalid_distribution_values[negative]` passes.

---

### Task 1: Remove Pydantic ge=0 constraint and add service-layer negative-pct guard

**Files:**
- Modify: `src/codemie/rest_api/routers/project_budget_router.py:533`
- Modify: `src/codemie/service/budget/project_budget_service.py:1713–1714` (inside `_validate_group_categories`)
- Test: `tests/codemie/service/budget/test_project_budget_service_group_creation.py`

**Interfaces:**
- Produces: `_validate_group_categories` raises `ExtendedHTTPException(code=400)` for any category whose `pct < 0`, before the sum check fires.

Test-first: yes — `test_system_rejects_invalid_distribution_values[negative]` calls `_validate_group_categories` directly with a negative pct and asserts `ExtendedHTTPException.code == 400`.

- [ ] **Step 1: Write the failing test**

Append to `tests/codemie/service/budget/test_project_budget_service_group_creation.py` at module level, after the existing class:

```python
@pytest.mark.parametrize("case,categories", [
    (
        "negative",
        {
            "platform": SimpleNamespace(pct=110.0),
            "cli": SimpleNamespace(pct=-10.0),
        },
    ),
])
def test_system_rejects_invalid_distribution_values(case, categories):
    with pytest.raises(ExtendedHTTPException) as exc_info:
        ProjectBudgetService._validate_group_categories(categories, total_amount=100.0)
    assert exc_info.value.code == 400
```

The fixture is crafted so that sum == 100 and platform is present, meaning only the negative-pct guard can make it fail — confirming the guard fires independently of the other checks.

- [ ] **Step 2: Confirm the test fails**

```
pytest "tests/codemie/service/budget/test_project_budget_service_group_creation.py::test_system_rejects_invalid_distribution_values[negative]" -v
```

Expected: FAIL — `_validate_group_categories` does not yet reject negative pct, so no exception is raised.

- [ ] **Step 3: Remove `ge=0` from `CategoryBudgetSpecUpdate.pct`**

`src/codemie/rest_api/routers/project_budget_router.py:533` — change `Field(ge=0, le=100, ...)` to `Field(le=100, ...)`. Keep `le=100` and the existing description string unchanged.

- [ ] **Step 4: Add negative-pct guard in `_validate_group_categories`**

`src/codemie/service/budget/project_budget_service.py` — after line 1713 (the `platform_key not in categories` check) and before line 1714 (the `total_pct = sum(...)` line), insert:

```python
for key, spec in categories.items():
    if spec.pct < 0:
        raise ExtendedHTTPException(
            code=400,
            message=f"Category '{key}' pct must be >= 0, got {spec.pct}",
        )
```

- [ ] **Step 5: Run all tests in the file**

```
pytest tests/codemie/service/budget/test_project_budget_service_group_creation.py -v
```

Expected: all tests PASS, including `test_system_rejects_invalid_distribution_values[negative]`.

- [ ] **Step 6: Commit**

Stage `src/codemie/rest_api/routers/project_budget_router.py`, `src/codemie/service/budget/project_budget_service.py`, and the test file, then commit.

---

**negative-constraints:**
- "project_name 403 issue is out of scope" — no task touches `_ensure_allowed_budget_group_update_fields` or the 403 guard in `update_project_budget_group`. Confirmed: no violation.
- "removing ge=0 without the service check would silently allow negative values through" — Task 1 ships both changes in one commit. Confirmed: no violation.
- `CategoryBudgetSpec` (create path) is not changed. Confirmed: no violation.
