# Technical Research

**Task**: budget spend bucket distribution project-admin
**Generated**: 2026-08-18T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Allow project admins to change spend bucket distribution. Project admins should be able to change distribution of spend buckets for budgets they administer. Acceptance criteria: Project Admin can view spend bucket distribution for budgets in their project; Project Admin can change spend bucket distribution for budgets they administer; The system validates that distribution values are correct before saving; Unauthorized users cannot change spend bucket distribution; Updated distribution is reflected in budget tracking and reporting; All changes are auditable or logged; Tests cover successful update, invalid distribution, unauthorized access, and reporting impact.

---

## 2. Codebase Findings

### Existing Implementations
- `src/codemie/rest_api/routers/project_budget_router.py` — FastAPI router for `/v1/admin/project-budgets` and `/v1/admin/project-budget-groups`; contains all request/response schemas and endpoint definitions; group distribution update is `PUT /{group_id}`
- `src/codemie/service/budget/project_budget_service.py` — `ProjectBudgetService` orchestrates CRUD, category distribution (`_validate_group_categories`, `_update_group_categories`), member allocation, and provider sync; singleton `project_budget_service` at bottom
- `src/codemie/service/budget/budget_enums.py` — `BudgetCategory` (platform/cli/premium_models), `AllocationMode` (equal/fixed), `SyncStatus`, `BudgetType`
- `src/codemie/repository/project_budget_repository.py` — repositories for `ProjectBudgetAssignment`, `ProjectBudgetGroup`, `ProjectMemberBudgetAssignment`; `ProjectBudgetContext` and `ProjectAssignedBudgetSummaryRow` dataclasses
- `src/codemie/repository/budget_repository.py` — base `Budget` row CRUD
- `src/codemie/rest_api/security/authentication.py` — `authenticate`, `maintainer_access_only`, `admin_access_only` FastAPI dependencies
- `src/codemie/rest_api/security/user.py` — `User` model; relevant fields: `is_admin`, `is_maintainer`, `is_auditor`, `admin_project_names`
- `src/codemie/service/activity/activity_models.py` — `ActivityEventCreate`, `BudgetManagementEvent`; events `PROJECT_BUDGET_GROUP_UPDATED`, `PROJECT_BUDGET_UPDATED`, `MEMBER_ALLOCATION_OVERRIDDEN` already defined
- `src/codemie/service/analytics/handlers/budget_handler.py` — budget analytics handler reading from Elasticsearch metrics
- `src/codemie/service/spend_tracking/spend_collector_service.py` — collects per-member spend rows
- `src/codemie/enterprise/litellm/budget_helpers.py` — LiteLLM budget enforcement helpers

### Architecture and Layers Affected
- **REST layer**: `project_budget_router.py` — `PUT /v1/admin/project-budget-groups/{group_id}` endpoint; access dependency needs replacing from `maintainer_access_only` to a new project-scoped write dependency
- **Service layer**: `ProjectBudgetService` — `update_project_budget_group`; group categories validation and update already implemented; no service changes needed beyond ensuring project-admin actor path is permitted
- **Security layer**: `authentication.py` — new dependency `project_admin_group_write_access` (or similar) needed; must load the group's `project_name` and verify it is in `user.admin_project_names`
- **Audit layer**: `activity_event_repository` — already wired in `update_project_budget_group`; may need enrichment to log old vs new category percentages

### Integration Points
- `ProjectBudgetService.update_project_budget_group` → `ProjectBudgetGroupRepository` → PostgreSQL
- `update_project_budget_group` → `activity_event_repository.create` (audit log)
- Distribution change → LiteLLM budget enforcement sync via `BudgetEnforcementProvider`
- Analytics: Elasticsearch via `BudgetHandler` (async, not synchronously refreshed on update)

### Patterns and Conventions
- Routers never call DB directly; services own all orchestration
- All mutating operations emit `ActivityEventCreate` to `activity_event_repository`
- Access control uses two tiers: `maintainer_access_only` (system-wide) and project-scoped read via `_can_read_project_budget(user, project_name)`
- Distribution validation: `_validate_group_categories` — category validity, `platform` presence, `sum(pct)` within 0.5 tolerance of 100.0
- Decimal arithmetic with `ROUND_HALF_UP` used throughout

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/development/security-patterns.md` — auth/authorization patterns; use `authenticate` and role helpers, not custom inline checks
- `.ai-run/guides/architecture/layered-architecture.md` — router → service → repository discipline; register new routers in `main.py`

### Architectural Decisions
- No ADRs found for project-admin write scoping. Decision to add a project-scoped write dependency mirrors the existing read-access pattern (`_can_read_project_budget`).

### Derived Conventions
- Read access is scoped at the service/router level by checking `project_name in user.admin_project_names`. Write access must follow the same pattern: load the group, extract its `project_name`, then check membership before delegating to the service.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/rest_api/routers/test_project_budget_router.py` — router unit tests; includes `_project_admin_user()` fixture; covers response building and read access
- `tests/codemie/rest_api/routers/test_project_budget_router_additional.py` — additional router tests
- `tests/codemie/rest_api/routers/test_project_budget_router_schema.py` — schema/validation tests
- `tests/codemie/service/budget/test_project_budget_service.py` — service unit tests (resync, allocation logic)
- `tests/codemie/service/budget/test_project_budget_service_lifecycle.py` — lifecycle tests
- `tests/codemie/service/budget/test_project_budget_service_group_creation.py` — group creation tests
- `tests/codemie/repository/test_project_budget_repository.py` — repository tests
- `tests/codemie/service/budget/test_budget_assignments.py` — assignment tests
- `tests/codemie/service/analytics/handlers/test_budget_handler.py` — analytics handler tests

### Testing Framework and Patterns
- pytest with `pytest-asyncio`; `AsyncMock`/`patch` from `unittest.mock`; `SimpleNamespace` for lightweight fakes
- `_project_admin_user()` fixture already exists in router tests

### Coverage Gaps
- No tests for project admin submitting a valid distribution update (success path)
- No tests for project admin submitting an invalid distribution (validation rejection)
- No tests for a project admin attempting to update a group outside their own project (scope leak / unauthorized)
- No tests for a non-admin, non-maintainer user being rejected on the write endpoint
- No tests for audit log content when a project admin performs the update

---

## 5. Configuration and Environment

### Environment Variables
- `config.ENABLE_USER_MANAGEMENT` — switches between legacy JWT roles and DB-backed user roles; determines whether `admin_project_names` is populated
- `config.ENV` — `local` grants `is_admin=True` to all users unconditionally

### Configuration Files
- `SettingsService.get_enforce_member_spend_limits(project_name)` — per-project boolean for member spend limit enforcement

### Feature Flags and Deployment Concerns
- No new feature flags needed; the change is gated by the existing `ENABLE_USER_MANAGEMENT` config which controls whether `admin_project_names` is populated

---

## 6. Risk Indicators

- **Authorization gap (HIGH)**: All write operations (`PATCH`, `PUT`, `DELETE`, `POST rebalance/reset/override`) use `maintainer_access_only`. Project admins have zero write access. A new project-scoped write dependency is required for the group update endpoint.
- **Scope leak risk (MEDIUM)**: The `PUT /{group_id}` endpoint does not load the group to verify project affiliation before the access check. A project admin must only mutate groups in their own project — the group's `project_name` must be fetched before the authorization decision.
- **Audit log completeness (LOW-MEDIUM)**: `update_project_budget_group` emits `PROJECT_BUDGET_GROUP_UPDATED` but does not log old vs new category percentages. The AC requires all changes to be auditable — this may need enrichment.
- **Reporting asynchrony**: `budget_handler.py` reads from Elasticsearch. Distribution changes are reflected only after provider sync and spend aggregation complete — no synchronous reporting refresh path. Tests must mock this pipeline.
- **`admin_project_names` population**: If `ENABLE_USER_MANAGEMENT=false`, `admin_project_names` is empty and project admin write access will always fail. This must be documented or guarded in tests.

---

## 7. Summary for Complexity Assessment

The feature touches the REST, security, and audit layers but does not require new service logic — `ProjectBudgetService.update_project_budget_group` already validates and persists category distributions. The core change is **authorization**: replacing the `maintainer_access_only` dependency on the group update endpoint with a new project-scoped write dependency that loads the group, verifies its `project_name` is in `user.admin_project_names`, and delegates to the existing service method. The read-access pattern (`_can_read_project_budget`) provides a direct template.

The scope-leak risk is the most critical correctness concern: the new dependency must resolve the group from the DB to obtain its `project_name` before the authorization check — it cannot rely solely on a request body field. This adds one DB read per write request but is architecturally clean. The audit log enrichment (logging old vs new percentages) is a low-effort addition to the existing `PROJECT_BUDGET_GROUP_UPDATED` event payload.

Test surface is well-defined: the `_project_admin_user()` fixture already exists; new tests need to cover the success path, invalid distribution, cross-project scope leak rejection, unauthorized user rejection, and audit log content. Overall complexity is low-to-medium — no new models, no new service methods, one new security dependency, and targeted test additions.
