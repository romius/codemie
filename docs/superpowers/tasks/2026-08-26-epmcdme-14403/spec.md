# Spec: Configurable Chargeback Attribution for Project Budgets (EPMCDME-14403)

## Problem

Projects can already enable chargeback (`Application.chargeback_enabled`), but there is no way to
say *how* costs should be attributed — to the project itself or to its linked cost center. This
adds a persisted `chargeback_attribution` value, exposes it through the project update/response
API, and enforces that `cost_center` attribution is only valid when the project has a linked,
non-deleted cost center. Everything stays behind the existing disabled-by-default
`features:projectChargeback` flag.

## Approach

Follow the `chargeback_enabled` precedent end to end (`f1g2h3i4j5k6` migration,
`src/codemie/core/models.py:394`, `application_repository.py:692-717`,
`project_service.py:174-282`, `projects.py` schemas/handlers, `project_visibility_service.py`
dicts) rather than introducing a new mechanism.

**Data shape** — a plain `str` column (not a Python `Enum`), matching the existing `project_type`
column:

```python
chargeback_attribution: str = SQLField(default="project", nullable=False)
# CheckConstraint("chargeback_attribution IN ('project', 'cost_center')",
#                  name="ck_applications_chargeback_attribution")
```

Migration: new file modeled on `f1g2h3i4j5k6_add_chargeback_enabled_to_applications.py` — add the
column with `server_default=sa.text("'project'")`, add the check constraint, pair with
`op.drop_column` + constraint drop in `downgrade()`. `down_revision` is set to whatever `alembic
heads` reports current at implementation time — the repo has multiple unmerged heads and none may
be assumed.

**Repository** — `ApplicationRepository.update_project` gains a `chargeback_attribution: str |
None = None` kwarg, using the existing `if x is not None: application.x = x` guard so omitting it
is a no-op (AC9).

**Service (`ProjectService.update_project`)** — two new checks, in this order, before
`application_repository.update_project(...)`:

1. **Feature gate**: if `chargeback_enabled is not None or chargeback_attribution is not None`,
   and `customer_config.is_feature_enabled("projectChargeback")` is `False`, raise
   `ExtendedHTTPException(code=403, message="Feature not available", details="Project chargeback
   configuration is not enabled for this customer.")` — rejects the whole update call, mirroring
   `assistant_project_mapping.py:38-49`'s `_require_teams_bot_feature` pattern. No `CONFIG_IDS`
   entry is added; the flag stays YAML-file-driven.
2. **Cost-center validation** (new `_validate_chargeback_attribution` helper), only when
   `chargeback_attribution is not None`. If its value is `"cost_center"`, resolve the *final*
   `cost_center_id` for this update (the same value `_resolve_updated_cost_center_id` computes —
   the newly supplied id, `None` if `clear_cost_center`, else the project's current id) and check
   it is active via `cost_center_repository.get_active_by_id`. Missing or soft-deleted →
   `ExtendedHTTPException(code=400, message="Cost center attribution requires a linked, active
   cost center")`. If the value is `"project"`, no cost-center check runs. This closes the known
   gap where `_resolve_updated_cost_center_id`'s fallback branch never re-validates an
   already-linked cost center's soft-delete state.

**API** — add `chargeback_attribution` to `ProjectListItem`, `ProjectDetailResponse`,
`ProjectCreateResponse` (default `"project"`) and `ProjectUpdateRequest` (`Optional[str] = None`),
add it to `validate_non_empty`'s mutable-field enumeration, and thread it through
`ProjectVisibilityService`'s list/detail dicts and `_build_project_detail_response`'s `.get(...)`
read — every site currently carrying `chargeback_enabled` gets the sibling field.

## Acceptance Criteria

- Migration adds `chargeback_attribution` to `applications`, server default `'project'`, non-null.
- `Application` model has the field with a check constraint restricting it to `project`/`cost_center`.
- `ProjectListItem`, `ProjectDetailResponse`, `ProjectCreateResponse`, `ProjectUpdateRequest` all expose `chargeback_attribution`.
- `ProjectService.update_project` → `ApplicationRepository.update_project` persists `chargeback_attribution`; omitting it is a no-op.
- `customer_config.is_feature_enabled("projectChargeback")` gates any request that sets `chargeback_enabled` and/or `chargeback_attribution`, rejecting with 403 when disabled.
- `chargeback_attribution="cost_center"` without a linked, non-deleted cost center (missing or soft-deleted, whether newly supplied or already linked) is rejected with a 400.
- `chargeback_attribution="project"` requires no cost center.
- Tests cover: valid project attribution, valid cost-center attribution, missing cost center, soft-deleted cost center, feature-flag-disabled rejection.
- A request that omits `chargeback_attribution` entirely leaves existing chargeback/cost-center behavior unchanged.

## Non-goals

- No `CONFIG_IDS` entry / runtime-togglable admin API for `projectChargeback` — stays YAML-only.
- No changes to `CostCenterService.ensure_exists_for_project` or its existing 404 behavior for a *newly supplied* `cost_center_id` — the new check is additive, for the `cost_center` attribution case specifically.
- No UI/frontend work (owned by the parent story's separate UI track).
- No Python `Enum` class for the attribution value — stays a plain constrained `str`, matching `project_type`.
- No backfill logic beyond the server default — no existing rows need a data migration since the default (`project`) is the safe/current-behavior value.
- No changes to project creation flow beyond the response schema default — `chargeback_attribution` is not settable at project-create time in this task.

## Testing

Extend `tests/codemie/service/project/test_project_service_chargeback.py` with cases: attribution
`project` (no cost-center check), attribution `cost_center` with active linked cost center (pass),
attribution `cost_center` with no linked cost center (400), attribution `cost_center` with a
soft-deleted linked cost center (400), feature flag disabled with either field set (403). Extend
`tests/codemie/rest_api/routers/test_projects_router.py`'s `TestChargebackEnabledField`-style class
for the new schema field across list/detail/create/update.
