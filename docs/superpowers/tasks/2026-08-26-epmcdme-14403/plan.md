# Configurable Chargeback Attribution for Project Budgets — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a persisted `chargeback_attribution` (`"project"` | `"cost_center"`) value to project budgets, exposed through the project update/response API, gated behind `features:projectChargeback`, validated against a linked non-deleted cost center.

**Architecture:** Extend the existing `chargeback_enabled` mechanism end to end (model, migration, repository, service, router, visibility dicts) rather than a new subsystem. `ProjectService.update_project` gains two new checks before its repository call: a feature-flag gate (403) and a cost-center-attribution validator (400).

**Tech Stack:** FastAPI, SQLModel, Alembic, pytest/unittest.mock.

**Spec:** `docs/superpowers/tasks/2026-08-26-epmcdme-14403/spec.md`

## Global Constraints

- Plain constrained `str` (`"project"` | `"cost_center"`), not a Python `Enum` — matches `project_type`.
- No `CONFIG_IDS` entry / runtime-togglable admin API — call only the existing `customer_config.is_feature_enabled("projectChargeback")`.
- No changes to `CostCenterService.ensure_exists_for_project` — the new check calls `cost_center_repository.get_active_by_id` directly instead.
- No backfill migration logic beyond the server default (`'project'` is the safe/current value).
- `chargeback_attribution` is not settable at project-create time — response-schema default only, not added to `ProjectCreateRequest`.
- Commit per task using the repository's existing convention (see recent `git log` subject style).

---

### Task 1: Migration + `Application` model field

**Files:**
- Create: `src/external/alembic/versions/<new_revision>_add_chargeback_attribution_to_applications.py`
- Modify: `src/codemie/core/models.py:394` (new field), `:400-403` (`__table_args__`)

**Interfaces:** Produces `Application.chargeback_attribution: str` (default `"project"`) and DB constraint `ck_applications_chargeback_attribution`.

- [ ] Run `cd src/external/alembic && poetry run alembic heads` to find the current head (repo has multiple unmerged heads — do not assume `f1g2h3i4j5k6` is one); use it as `down_revision`.
- [ ] Write the migration, modeled on `f1g2h3i4j5k6_add_chargeback_enabled_to_applications.py` (no backfill `UPDATE`, unlike that precedent):

```python
def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("chargeback_attribution", sa.String(), server_default=sa.text("'project'"), nullable=False),
    )
    op.create_check_constraint(
        "ck_applications_chargeback_attribution", "applications",
        "chargeback_attribution IN ('project', 'cost_center')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_applications_chargeback_attribution", "applications", type_="check")
    op.drop_column("applications", "chargeback_attribution")
```

- [ ] At `models.py:394`, add `chargeback_attribution: str = SQLField(default="project", nullable=False)` directly after `chargeback_enabled`; in `__table_args__` (`:400-403`) add `CheckConstraint("chargeback_attribution IN ('project', 'cost_center')", name="ck_applications_chargeback_attribution")` alongside `ck_applications_project_type`.
- [ ] Verify: run `poetry run alembic upgrade head` against local Postgres, confirm column/constraint exist, then `poetry run alembic downgrade -1` cleanly.
- [ ] Commit.

Test-first: no — no migration-level test convention exists in this repo; the field is exercised indirectly by Task 2's service test.

---

### Task 2: Repository + Service — persist `chargeback_attribution` (no-op default)

**Files:**
- Modify: `src/codemie/repository/application_repository.py:692-717` (`update_project`)
- Modify: `src/codemie/service/project/project_service.py:174-226` (`update_project`)
- Test: `tests/codemie/service/project/test_project_service_chargeback.py`

**Interfaces:** Produces `ApplicationRepository.update_project(..., chargeback_attribution: str | None = None)` and `ProjectService.update_project(..., chargeback_attribution: str | None = None)`, both defaulting to `None` = no change (AC9).

- [ ] Write two tests in a new `TestProjectServiceChargebackAttribution` class, copying `TestProjectServiceChargebackEnabled`'s mock setup (patch `application_repository`, `get_session`, and every `ProjectService` private helper; also patch `customer_config` returning `is_feature_enabled=True` and `_validate_chargeback_attribution` as a no-op): (1) `test_update_project_passes_chargeback_attribution_to_repository` — call `update_project(..., chargeback_attribution="cost_center")`, assert `mock_app_repo.update_project.call_args.kwargs["chargeback_attribution"] == "cost_center"`; (2) `test_update_project_omits_chargeback_attribution_as_noop` — call without the kwarg, assert it is `None` in the same call_args.
- [ ] Run: `poetry run pytest tests/codemie/service/project/test_project_service_chargeback.py -v` — expect FAIL (unknown kwarg).
- [ ] Implement: in `application_repository.py:692-717`, add `chargeback_attribution: str | None = None` param and the guard `if chargeback_attribution is not None: application.chargeback_attribution = chargeback_attribution`, mirroring `chargeback_enabled`'s existing guard at `:711-712`. In `project_service.py`, add the same param to `update_project`'s signature (`:174-187`) and pass it through to `application_repository.update_project(...)` (`:198-206`); call `cls._validate_chargeback_attribution(session, chargeback_attribution, resolved_cost_center_id)` right before that repository call as a temporary no-op stub (`pass`) — Task 4 fills it in.
- [ ] Run the test file again — expect PASS.
- [ ] Commit.

Test-first: yes — both new tests fail before the kwarg/propagation exists.

---

### Task 3: Feature-flag gate on chargeback updates

**Files:**
- Modify: `src/codemie/service/project/project_service.py` (imports + `update_project`)
- Test: `tests/codemie/service/project/test_project_service_chargeback.py`

**Interfaces:** Raises `ExtendedHTTPException(code=403, message="Feature not available", details="Project chargeback configuration is not enabled for this customer.")` from `update_project` whenever `chargeback_enabled is not None or chargeback_attribution is not None` and `customer_config.is_feature_enabled("projectChargeback")` is `False`.

- [ ] Write `test_update_project_rejects_chargeback_when_flag_disabled` in a new `TestProjectServiceChargebackFeatureGate` class: patch `customer_config.is_feature_enabled` to return `False`, patch `_get_project_for_update` and `application_repository`, call `update_project(..., chargeback_attribution="project")` inside `pytest.raises(ExtendedHTTPException)`, assert `exc_info.value.code == 403` and `mock_app_repo.update_project.assert_not_called()`.
- [ ] Run the test — expect FAIL (no exception raised).
- [ ] Implement: add `from codemie.configs.customer_config import customer_config` to `project_service.py`'s imports; in `update_project`, immediately after `project = cls._get_project_for_update(...)` (`:189`), add the gate — mirrors `assistant_project_mapping.py:42-49`'s `_require_teams_bot_feature` shape:

```python
if (chargeback_enabled is not None or chargeback_attribution is not None) and not customer_config.is_feature_enabled(
    "projectChargeback"
):
    raise ExtendedHTTPException(
        code=403, message="Feature not available",
        details="Project chargeback configuration is not enabled for this customer.",
    )
```

- [ ] Run the whole file — expect PASS (Task 2's tests must set `is_feature_enabled.return_value = True`).
- [ ] Commit.

Test-first: yes — the new rejection test fails until the gate is added.

---

### Task 4: Cost-center attribution validation

**Files:**
- Modify: `src/codemie/service/project/project_service.py` (imports + `_validate_chargeback_attribution` + call site)
- Test: `tests/codemie/service/project/test_project_service_chargeback.py`

**Interfaces:** Produces `ProjectService._validate_chargeback_attribution(session, chargeback_attribution, resolved_cost_center_id) -> None`, raising `ExtendedHTTPException(code=400, message="Cost center attribution requires a linked, active cost center")` when `chargeback_attribution == "cost_center"` and `resolved_cost_center_id` is `None` or `cost_center_repository.get_active_by_id` returns nothing (covers both a missing and a soft-deleted linked cost center). No check when `chargeback_attribution` is `None`/`"project"`.

- [ ] Write four tests in `TestProjectServiceChargebackAttributionValidation`, each driving `update_project` end to end with `_resolve_updated_cost_center_id` patched to return a chosen `resolved_cost_center_id` and `cost_center_repository.get_active_by_id` patched to return either an active mock cost center or `None`: (1) `"project"` attribution never calls `get_active_by_id`, update succeeds; (2) `"cost_center"` with `get_active_by_id` returning a cost center, update succeeds; (3) `"cost_center"` with `resolved_cost_center_id=None`, raises 400; (4) `"cost_center"` with a `resolved_cost_center_id` set but `get_active_by_id` returning `None` (soft-deleted), raises 400.
- [ ] Run the test class — expect FAIL (stub never raises, tests 3-4 fail).
- [ ] Implement: add `from codemie.repository.cost_center_repository import cost_center_repository` to imports. Replace Task 2's stub with:

```python
@classmethod
def _validate_chargeback_attribution(
    cls, session: Session, chargeback_attribution: str | None, resolved_cost_center_id: UUID | None,
) -> None:
    if chargeback_attribution != "cost_center":
        return
    active = (
        cost_center_repository.get_active_by_id(session, resolved_cost_center_id)
        if resolved_cost_center_id is not None else None
    )
    if active is None:
        raise ExtendedHTTPException(
            code=400, message="Cost center attribution requires a linked, active cost center",
        )
```

Place it near `_resolve_updated_cost_center_id` (`:270-282`); the call site added in Task 2 already passes the right arguments.
- [ ] Run the whole file — expect PASS (Tasks 2-4 combined).
- [ ] Commit.

Test-first: yes — tests 3 and 4 fail until the real helper replaces the stub.

---

### Task 5: API schemas, handlers, and `ProjectVisibilityService` dict threading

**Files:**
- Modify: `src/codemie/rest_api/routers/projects.py:154-282` (schemas), `:672-683` (`create_project`), `:819-834` (`_build_project_detail_response`), `:1000-1024` (`update_project` handler)
- Modify: `src/codemie/service/project/project_visibility_service.py:119`, `:196` (list/detail dict builders)
- Test: `tests/codemie/rest_api/routers/test_projects_router.py`

**Interfaces:** Produces `chargeback_attribution: str = "project"` on `ProjectListItem`/`ProjectDetailResponse`/`ProjectCreateResponse`; `Optional[str] = None` on `ProjectUpdateRequest`.

- [ ] Write a `TestChargebackAttributionField` class mirroring `TestChargebackEnabledField`'s five tests one-for-one, substituting `chargeback_attribution`/`"project"`/`"cost_center"` for `chargeback_enabled`/`False`/`True`, plus a sixth test `test_build_project_detail_response_includes_chargeback_attribution` that calls `_build_project_detail_response({...,"chargeback_attribution": "cost_center"}, "proj")` and asserts the response field.
- [ ] Run: `poetry run pytest tests/codemie/rest_api/routers/test_projects_router.py::TestChargebackAttributionField -v` — expect FAIL.
- [ ] Implement schemas: add `chargeback_attribution: str = "project"` to `ProjectListItem` (after `:170`), `ProjectDetailResponse` (after `:228`), `ProjectCreateResponse` (after `:251`); add `chargeback_attribution: Optional[str] = None` to `ProjectUpdateRequest` (after `:262`); add `and self.chargeback_attribution is None` to `validate_non_empty`'s boolean expression (`:266-275`).
- [ ] Implement handlers: `create_project` (`:672-683`) — add `chargeback_attribution=project.chargeback_attribution` to the response. `update_project` handler (`:1000-1024`) — add `chargeback_attribution=payload.chargeback_attribution` to the `project_service.update_project(...)` call and `chargeback_attribution=project.chargeback_attribution` to its response. `_build_project_detail_response` (`:819-834`) — add `chargeback_attribution=project_detail.get("chargeback_attribution", "project")`.
- [ ] Thread through `ProjectVisibilityService`: at `:119` and `:196`, add `"chargeback_attribution": project.chargeback_attribution,` next to the existing `"chargeback_enabled"` key.
- [ ] Run: `poetry run pytest tests/codemie/rest_api/routers/test_projects_router.py tests/codemie/service/project/test_project_service_chargeback.py -v` — expect PASS.
- [ ] Commit.

Test-first: yes — the six new tests fail until the field is threaded through every schema, handler, and visibility-dict site.

---

## Self-Review Notes

**Spec coverage:** AC1-AC2 → Task 1. AC3 → Task 5. AC4/AC9 → Task 2. AC5 → Task 3. AC6/AC7 → Task 4. AC8 → tests distributed across Tasks 2-5 per the spec's own "Testing" mapping.

**Negative-constraint pass:**
- No `CONFIG_IDS` entry (non-goal) — Task 3 calls the existing `customer_config.is_feature_enabled(...)` directly; no task touches `customer_config.py`.
- No changes to `CostCenterService.ensure_exists_for_project` (non-goal) — Task 4 calls `cost_center_repository.get_active_by_id` directly, bypassing `CostCenterService`; no task modifies `cost_center_service.py`.
- No Python `Enum` (non-goal) — Task 1 uses a plain `str` field + `CheckConstraint`, matching `project_type`.
- No backfill beyond server default (non-goal) — Task 1's migration has no `UPDATE`, unlike its `chargeback_enabled` precedent.
- Not settable at project-create time (non-goal) — Task 5 does not touch `ProjectCreateRequest`.
- No task runs a whole-suite quality gate, browser verification, or code review — each task's test run is scoped to the files it touches; the calling flow owns those stages separately.
