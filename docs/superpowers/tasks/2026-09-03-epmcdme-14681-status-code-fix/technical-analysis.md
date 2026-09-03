# Technical Analysis — epmcdme-14681-status-code-fix

## Summary

Fix `PUT /v1/admin/project-budget-groups/{group_id}` so negative category `pct` values return HTTP 400 instead of 422.

## Codebase Findings

**Root cause:** `CategoryBudgetSpecUpdate.pct` has `Field(ge=0, le=100, ...)` in `project_budget_router.py`. Pydantic intercepts negative values before the service layer, returning FastAPI's default 422. Sibling validations (`sum_off`, `no_platform`) live in `_validate_group_categories()` and correctly return 400 via `ExtendedHTTPException(code=400)`.

**Fix surface:**
- `src/codemie/rest_api/routers/project_budget_router.py` (line ~533): `CategoryBudgetSpecUpdate.pct` — remove `ge=0`
- `src/codemie/service/budget/project_budget_service.py` (line ~1703): `_validate_group_categories()` — add negative/NaN check before sum check
- `tests/codemie/service/budget/test_project_budget_service_group_creation.py`: add negative pct test cases

**Architecture:** FastAPI router → `ProjectBudgetService._validate_group_categories()` → repository. Service raises `ExtendedHTTPException(code=400)` for all semantic validation errors.

**Existing coverage:** `test_sum_not_100_fails_validation` (400), `test_platform_missing_fails_validation` (400); no test for negative pct.

## Risk Indicators

1. Removing `ge=0` without adding a service-layer guard would silently allow negative pct values through — both changes must ship together.
