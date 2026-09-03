# Code review — 2026-09-03-epmcdme-14681-status-code-fix (2026-09-03)

**request-changes** · confidence: low · 5 blocking · 0 deferred · 0 filtered as noise
Coverage: edge-case ✓ · blind — n/a (compact) · acceptance — n/a (no spec) · verification-gap — n/a (compact)  (1/1 applicable lenses ran)

## Look here first

- `src/codemie/service/budget/project_budget_service.py:1715` — [infra] NaN pct bypasses guard: `nan < 0` is False; sum becomes NaN; 100% check passes; NaN stored in DB — CR-004
- `src/codemie/rest_api/routers/project_budget_router.py:505` — [public API] CREATE still has `ge=0` on `CategoryBudgetSpec.pct`; POST returns 422 for negative pct while PUT returns 400 — CR-001
- `src/codemie/rest_api/routers/project_budget_router.py:533` — [public API] `le=100` kept on `CategoryBudgetSpecUpdate.pct`; pct>100 on PUT returns 422 (Pydantic), pct<0 returns 400 (service) — CR-003
- `src/codemie/rest_api/routers/project_budget_router.py:533` — [public API] `ge=0` removal dropped `minimum:0` from OpenAPI schema; generated clients and docs lose the lower-bound contract — CR-002
- `tests/codemie/service/budget/test_project_budget_service_group_creation.py:99` — [other] test has only one parametrize case; NaN and boundary pct=-epsilon not exercised — CR-005

## Checked and clean

0 deferred items
