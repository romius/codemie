# Technical Research

**Task**: chargeback attribution project budget cost-center
**Generated**: 2026-08-26T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-14403 — Backend: add configurable chargeback attribution for project budgets (sub-task of parent story EPMCDME-14387).

Summary: Add backend support for configurable chargeback attribution on project budgets.

Description: This sub-task implements the backend part of configurable chargeback for project budgets. It adds a persisted `chargeback_attribution` value to the `Application` project model, exposes it through project update and response schemas, and validates that cost-center attribution is only saved when the project has a linked, non-deleted cost center. The implementation must remain behind the existing disabled-by-default `features:projectChargeback` feature flag.

Preconditions:
- Parent story EPMCDME-14387 defines the required chargeback behavior.
- The existing `chargeback_enabled` field and project cost-center relationship are available in the backend.
- The existing `features:projectChargeback` configuration entry remains disabled by default.

Scenarios of Use:
1. A project maintainer updates project budget chargeback settings through the project update API.
2. The backend persists `chargeback_enabled` and `chargeback_attribution` consistently on the project record.
3. If `chargeback_attribution` is set to `cost_center`, the backend checks that a linked, non-deleted cost center exists.
4. If the feature flag is disabled, chargeback configuration changes are not exposed or applied outside the intended gated behavior.

Affected Areas: codemie backend, Alembic migrations, `Application` model, project request/response schemas, `ProjectService`, `ApplicationRepository`, feature flag handling for `features:projectChargeback`, cost center validation logic.

Acceptance Criteria:
1. A new Alembic migration adds `chargeback_attribution` to `applications` with a safe server default of `project`.
2. The `Application` model includes `chargeback_attribution`.
3. Project update and response schemas include `chargeback_attribution` together with the existing `chargeback_enabled` behavior.
4. Project update flow persists `chargeback_attribution` through `ProjectService` and `ApplicationRepository`.
5. Python-side handling reads and enforces `features:projectChargeback` for the project chargeback capability.
6. Setting attribution to `cost_center` without a linked, non-deleted cost center is rejected with a validation error.
7. Setting attribution to `project` does not require a cost center.
8. Unit or service-level tests cover valid project attribution, valid cost-center attribution, missing cost center, and soft-deleted cost center cases.
9. Existing project budget behavior remains unchanged when no chargeback attribution change is requested.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/core/models.py:379-403` — `Application` SQLModel (table `applications`). Current columns relevant to this task: `cost_center_id: Optional[uuid.UUID] = SQLField(default=None, foreign_key="cost_centers.id", index=True)` (line 392), `deleted_at` (line 393, project soft-delete), `chargeback_enabled: bool = SQLField(default=False, nullable=False)` (line 394). **There is no `chargeback_attribution` field yet.**
- `src/codemie/core/models.py:453-462` — `CostCenter` SQLModel (table `cost_centers`). Has `deleted_at: Optional[datetime] = SQLField(default=None, index=True)` — the soft-delete column that governs "linked, non-deleted cost center" checks.
- `src/codemie/repository/application_repository.py:692-717` — `ApplicationRepository.update_project(session, application, *, name, display_name, description, cost_center_id, chargeback_enabled)` mutates and flushes/refreshes the `Application` row. This is the exact seam where a new `chargeback_attribution` kwarg would be added, following the same `if chargeback_enabled is not None:` pattern (lines 710-712).
- `src/codemie/repository/application_repository.py:138-150, 188-...` — `get_by_name`, `get_active_by_name`, `create` — no chargeback awareness needed there; `create` accepts kwargs and any new model field with a Python-side default flows through automatically (established precedent from the prior `chargeback_enabled` addition).
- `src/codemie/repository/cost_center_repository.py:31-36` — `get_by_id` (any state) vs. `get_active_by_id(session, cost_center_id)` which filters `CostCenter.deleted_at.is_(None)` — this is the existing "non-deleted" lookup primitive.
- `src/codemie/service/cost_center_service.py:111-117` — `CostCenterService.ensure_exists_for_project(session, cost_center_id)`: returns `None` if `cost_center_id is None`, otherwise calls `cost_center_repository.get_active_by_id` and raises `ExtendedHTTPException(code=404, message="Selected cost center not found")` if the active lookup fails. This is the direct precedent pattern for "linked, non-deleted cost center" validation, though it validates a *passed-in* `cost_center_id`, not the *currently linked* one on the project.
- `src/codemie/service/project/project_service.py:174-226` — `ProjectService.update_project(user, project_name, *, name=None, display_name=None, clear_display_name=False, description=None, cost_center_id=None, clear_cost_center=False, enforce_member_spend_limits=None, chargeback_enabled=None)`. Orchestrates: `_get_project_for_update` (404/403 checks) → `_resolve_updated_name` → `_validate_project_description` → `_resolve_updated_display_name` → `_resolve_updated_cost_center_id` → `application_repository.update_project(...)` → `SettingsService.set_enforce_member_spend_limits` (if provided) → activity log → commit. `chargeback_enabled` is passed straight through with no validation.
- `src/codemie/service/project/project_service.py:270-282` — `_resolve_updated_cost_center_id(session, project, cost_center_id, clear_cost_center)`: if a new `cost_center_id` is supplied, calls `cost_center_service.ensure_exists_for_project` (raises 404 if inactive/missing) and returns its `.id`; if `clear_cost_center`, returns `None`; otherwise falls back to `project.cost_center_id` (the currently linked one, unvalidated for soft-delete at this point). This fallback path is the gap this task's AC6/AC7 validation must close: if a project's *existing* linked cost center were later soft-deleted, this method would still return the stale id without re-validating it.
- `src/codemie/rest_api/routers/projects.py:154-282` — `ProjectListItem` (170: `chargeback_enabled: bool = False`), `ProjectDetailResponse` (228: `chargeback_enabled: bool = False`), `ProjectCreateResponse` (251: `chargeback_enabled: bool = False`), `ProjectUpdateRequest` (254-281: `chargeback_enabled: Optional[bool] = None`, with a `model_validator(mode="after")` `validate_non_empty` that lists every mutable field explicitly). None of these currently reference `chargeback_attribution`.
- `src/codemie/rest_api/routers/projects.py:~664-694, ~810-834` — `create_project` handler builds `ProjectCreateResponse(..., chargeback_enabled=project.chargeback_enabled)`; `_build_project_detail_response` builds `ProjectDetailResponse(..., chargeback_enabled=project_detail.get("chargeback_enabled", False))`; `update_project` handler passes `chargeback_enabled=payload.chargeback_enabled` to `project_service.update_project(...)` and returns `chargeback_enabled=project.chargeback_enabled`.
- `src/codemie/service/project/project_visibility_service.py` — enriches list/detail dicts (per the prior task's plan, lines ~106 and ~184) with `"chargeback_enabled": project.chargeback_enabled` alongside `cost_center_id`/`cost_center_name`. This is the dict-shape seam a new `chargeback_attribution` key must also flow through, since `_build_project_detail_response` reads from this dict via `.get(...)`, not from the ORM object directly.
- `src/codemie/configs/customer_config.py:24-30` (`CONFIG_IDS`) — **does not** contain a `"projectChargeback"` entry. `CustomerConfig.is_component_enabled` (lines 238-253) checks `CONFIG_IDS.values()` for a runtime-computed override first, then falls back to `self.components` loaded from the YAML file. `is_feature_enabled(feature_key)` (lines 256-268) simply prefixes `f"features:{feature_key}"` and delegates to `is_component_enabled`.
- `config/customer/customer-config.yaml:269-273` — the YAML file **already declares** the component:
  ```yaml
  - id: "features:projectChargeback"
    settings:
      enabled: false
      name: "Project Chargeback"
      description: "Enable project chargeback status modification"
  ```
  Since this id is not in `CONFIG_IDS`, `is_component_enabled` will skip the runtime-config branch and fall through to the YAML `self.components` list, returning `False` unless the deployed YAML is edited. `customer_config.is_feature_enabled("projectChargeback")` is therefore already wired end-to-end at the config layer — **no code exists yet that calls it** for project chargeback (see Integration Points below).
- `src/external/alembic/versions/f1g2h3i4j5k6_add_chargeback_enabled_to_applications.py` — existing migration: `op.add_column("applications", sa.Column("chargeback_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False))` followed by an `op.execute(UPDATE ...)` backfill from `project_budget_assignments`. `revision = "f1g2h3i4j5k6"`, `down_revision = "u2v3w4x5y6z7"`. This is the direct migration-pattern precedent for the new `chargeback_attribution` column (add a `sa.String`/enum column with `server_default=sa.text("'project'")`).

### Architecture and Layers Affected

- **DB-Persistence**: `Application` model (`src/codemie/core/models.py:379-403`) and a new Alembic migration under `src/external/alembic/versions/`.
- **Repository**: `ApplicationRepository.update_project` (`src/codemie/repository/application_repository.py:692-717`).
- **Service**: `ProjectService.update_project` and its helper `_resolve_updated_cost_center_id` (`src/codemie/service/project/project_service.py:174-282`); `CostCenterService` (`src/codemie/service/cost_center_service.py`) for the active-cost-center lookup primitive; `ProjectVisibilityService` for the enriched dict shape consumed by the router.
- **API**: `src/codemie/rest_api/routers/projects.py` — `ProjectListItem`, `ProjectDetailResponse`, `ProjectCreateResponse`, `ProjectUpdateRequest`, and the `create_project`/`update_project` handlers plus `_build_project_detail_response`.
- **Configuration**: `src/codemie/configs/customer_config.py` (`CustomerConfig.is_feature_enabled`) and `config/customer/customer-config.yaml` (already has the `features:projectChargeback` component; no `CONFIG_IDS` entry exists, meaning enforcement today is purely YAML-file-driven with no runtime-computed component logic like `enterpriseEdition` or `chatContextualNaming` have).

### Integration Points

- `ProjectService.update_project` → `ApplicationRepository.update_project` → `Application.chargeback_enabled` (existing wiring for the boolean flag); the same call chain is the integration point for `chargeback_attribution`.
- `ProjectService._resolve_updated_cost_center_id` → `CostCenterService.ensure_exists_for_project` → `CostCenterRepository.get_active_by_id` (existing "active cost center" validation chain, currently only exercised when a *new* `cost_center_id` is supplied in the same request).
- `customer_config.is_feature_enabled("projectChargeback")` is **not called anywhere in `src/codemie`** today (confirmed by repo-wide grep). Existing call sites that gate service/router behavior with `customer_config.is_feature_enabled(...)` follow two shapes:
  - `src/codemie/rest_api/routers/assistant_project_mapping.py:43`: `if not customer_config.is_feature_enabled(_TEAMS_BOT_FEATURE): raise ...` (router-level gate, raises before touching the service).
  - `src/codemie/service/tools/toolkit_service.py:260,283,612`: `... and customer_config.is_feature_enabled("webSearch")` (service-level boolean AND gate on the effective flag).
  Neither pattern currently exists for chargeback; AC5 requires introducing this call somewhere in `ProjectService.update_project` (or its callers) for the first time.
- `ProjectVisibilityService`'s list/detail dict builders are consumed by `_build_project_detail_response` (`.get("chargeback_enabled", False)`), which is a `.get()`-based read, not a direct model attribute — the new field must be threaded through this dict, not just through `Application`.
- `tests/codemie/configs/test_customer_config.py:190-212` already unit-tests `CustomerConfig().is_feature_enabled("projectChargeback")` toggling True/False against a YAML fixture that includes the `features:projectChargeback` component — this confirms the config layer itself is considered "done" and stable; only the *consumption* of the flag inside project-update logic is unimplemented.

### Patterns and Conventions

- **SQLModel field addition**: `chargeback_enabled: bool = SQLField(default=False, nullable=False)` is the immediate precedent (line 394); a `chargeback_attribution` column would follow the same style, e.g. `chargeback_attribution: str = SQLField(default="project", nullable=False)`, placed directly after `chargeback_enabled`.
- **Migration pattern**: `op.add_column("applications", sa.Column(<name>, <type>, server_default=sa.text(<default>), nullable=False))` with a paired `op.drop_column` in `downgrade()`. The `f1g2h3i4j5k6` migration is the closest analogue for a same-table, non-destructive column add with a safe server default; it did not use a Python-side `Enum`/`CheckConstraint`, so any enum-shaped `chargeback_attribution` (`project` | `cost_center`) would need its own constraint choice — no existing `CheckConstraint` pattern for a string-enum column on `applications` exists yet (the closest analogue is `CheckConstraint("project_type IN ('personal', 'shared')", name="ck_applications_project_type")` at line 402, which does constrain a string column the same way `project_type` is stored).
- **Repository update pattern**: `if <field> is not None: application.<field> = <field>` guards inside `update_project`, letting `None` mean "no change requested" (distinct from `False`/empty-string meaning "explicitly cleared"). This is the exact idiom AC9 ("existing behavior unchanged when no chargeback attribution change is requested") depends on — omitting the kwarg (or passing `None`) must be a no-op.
- **Service validation pattern**: raising `ExtendedHTTPException(code=404 or 400, message=...)` from a classmethod helper before the repository call — see `CostCenterService.ensure_exists_for_project` (404) and `ProjectService._resolve_updated_name` (409). The new cost-center-attribution validation (AC6) would follow this same shape, likely as a new helper (e.g. `_validate_chargeback_attribution`) called before `application_repository.update_project(...)` in `ProjectService.update_project`.
- **API request validator pattern**: `ProjectUpdateRequest.validate_non_empty` (`@model_validator(mode="after")`) explicitly lists every mutable field in its "at least one field provided" check — a new `chargeback_attribution` field must be added to that boolean expression or the validator will incorrectly reject a request that only sets `chargeback_attribution`.
- **Feature-flag gating pattern**: two existing shapes (router-level hard gate vs. service-level boolean AND) as described above; no existing precedent gates a *field write* on a project-update PATCH specifically — closest is `assistant_project_mapping.py`'s router-level `if not is_feature_enabled(...): raise`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/data/database-patterns.md:16-23` — "Use Alembic for schema changes and keep migration files under the existing external Alembic tree" and "Hand-editing migration history without checking heads → Inspect Alembic state before changing migration chains" (evidence cites `README.md:90`). Directly relevant: this repo's Alembic tree currently has **13 unmerged heads** (see Risk Indicators), so the new migration's `down_revision` must be chosen deliberately.
- `.ai-run/guides/development/configuration-patterns.md:14-24` — "Gate optional behavior through config at assembly points or service boundaries" / "Centralize checks near app/router/service assembly" / "Assuming enterprise features always exist → Use enterprise loader/provider abstractions." Evidence cites user-management router gating in `src/codemie/rest_api/main.py:706`. This guide favors gating at the API/service boundary — consistent with the two existing `is_feature_enabled` call shapes found in Section 2.
- `.ai-run/guides/data/repository-patterns.md` (referenced by AGENTS.md) — not re-read in full this session beyond the prior task's citation; its stated principle (from the earlier chargeback_enabled research) is that new DB columns should flow through the matching model and repository rather than bypassing them from the service layer, consistent with the existing `ApplicationRepository.update_project` seam.
- No guide file specifically discusses "chargeback attribution" or "cost center" domain rules; this is undocumented domain logic outside of the Application/CostCenter model relationship itself.

### Architectural Decisions

- Prior task's decision (carried over, still true in code): boolean project feature flags have historically lived in `SettingsService` (e.g. `enforce_member_spend_limits`), while `chargeback_enabled` broke that pattern by living as a direct `applications` column. `chargeback_attribution` continues that column-based approach per this task's AC1/AC2, so the precedent is now two-for-two on directly-persisted chargeback columns.
- The `features:projectChargeback` YAML component was added to `config/customer/customer-config.yaml` (and covered by `tests/codemie/configs/test_customer_config.py`) as part of prior work, but **no `CONFIG_IDS` entry and no consuming code exist** — it is a flag that is fully wired at the config layer but not yet enforced anywhere in project/service logic. This sub-task's AC5 is the first code that must call `is_feature_enabled("projectChargeback")`.
- `CostCenterRepository.get_active_by_id` (`deleted_at.is_(None)` filter) is the canonical "active" check reused by `CostCenterService.delete` (guards against deleting a cost center with linked active projects) and `CostCenterService.ensure_exists_for_project` — establishing that "non-deleted" always means `deleted_at IS NULL`, never a separate `is_active` boolean.

### Derived Conventions

- New optional service-layer parameters default to `None` meaning "no change"; `False`/empty values are explicit user intent — this must be preserved for `chargeback_attribution` (e.g. default `None` in `ProjectService.update_project` and `ApplicationRepository.update_project`, not a falsy-string sentinel).
- Model-level field ordering: chargeback-related fields are grouped together at the end of `Application`'s field list, after `deleted_at` — `chargeback_attribution` would naturally follow `chargeback_enabled` on line 395.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/project/test_project_service_chargeback.py` — `TestProjectServiceChargebackEnabled.test_update_project_passes_chargeback_enabled_to_repository`: mocks `application_repository`, `get_session`, and every `ProjectService` private helper (`_get_project_for_update`, `_resolve_updated_name`, `_validate_project_description`, `_resolve_updated_display_name`, `_resolve_updated_cost_center_id`), then asserts `chargeback_enabled=True` reaches `application_repository.update_project.call_args.kwargs`. This is the direct template for a new `chargeback_attribution` propagation test, and for negative cases (cost-center validation failure) by *not* mocking the new validation helper.
- `tests/codemie/service/test_cost_center_service.py` — tests `CostCenterService.validate_name`, and (per file structure) other `CostCenterService` methods using `MagicMock` users/cost-centers; no test currently exercises `ensure_exists_for_project`'s interaction from the project-update path specifically.
- `tests/codemie/service/project/test_project_service.py`, `test_project_service_delete_update.py`, `test_personal_project_service.py` — broader `ProjectService` coverage (creation, deletion, update flows) that would need to keep passing when `chargeback_attribution` parameters are added (AC9's regression-safety concern).
- `tests/codemie/configs/test_customer_config.py:190-212` — `is_feature_enabled("projectChargeback")` toggle test already exists at the config layer (YAML-driven True/False).
- `tests/codemie/rest_api/routers/test_projects_router.py` — contains `TestChargebackEnabledField` (per the prior task's plan/tests) covering `chargeback_enabled` on `ProjectListItem`, `ProjectDetailResponse`, `ProjectCreateResponse`, `ProjectUpdateRequest`, and the `create_project`/`update_project` handlers — this is the direct template class to extend or sibling to add for `chargeback_attribution`.
- `docs/superpowers/tasks/2026-08-12-add-chargeback-enabled-column-projects/code-review-final.json:40` (prior task's own review record) explicitly flags: "No test covers `is_feature_enabled('projectChargeback')`" as an outstanding gap from that earlier task — i.e., the flag-consumption gap identified in Section 2 was already flagged once before and has not been closed.

### Testing Framework and Patterns

- **Framework**: pytest (`pytest-asyncio` used elsewhere in the router test suite per the prior task's research; the `ProjectService` tests use plain synchronous `unittest.mock`).
- **Mocking**: `unittest.mock.MagicMock`/`patch`/`patch.object`, `@contextmanager`-wrapped fake session context managers for `get_session`, `SimpleNamespace` for lightweight fake ORM rows (avoids a real DB or SQLModel instantiation in service tests).
- **No real-DB fixtures observed** in the `ProjectService`/`ApplicationRepository` test files — all repository/session interaction is mocked at the boundary, consistent with the earlier task's finding that "no fixtures create real DB rows."

### Coverage Gaps

- No test exists for `chargeback_attribution` at any layer (field does not exist yet).
- No test exists that exercises `_resolve_updated_cost_center_id`'s fallback branch (returning the *existing* `project.cost_center_id` without re-validating soft-delete) — relevant because AC6's "missing cost center" and "soft-deleted cost center" cases could target either the newly-supplied `cost_center_id` or the project's already-linked one, and today's code only validates the former.
- No test exists that calls `customer_config.is_feature_enabled("projectChargeback")` from `ProjectService` or the projects router — the flag-consumption gap flagged in the prior task's own code review (`code-review-final.json:40`) is still open.
- No migration-level test exists in the repo (consistent with the earlier finding — migrations are not automatically tested).

---

## 5. Configuration and Environment

### Environment Variables

No environment variables gate chargeback attribution specifically. The `features:projectChargeback` toggle is YAML-file-driven, not env-var-driven (confirmed by its absence from `CONFIG_IDS`'s runtime-computed branch, which is the only branch that reads live `config.*` env-backed settings — see `customer_config.py:174-178` for the analogous `chatContextualNaming` runtime-computed example, which explicitly reads `config.CHAT_CONTEXTUAL_NAMING_ENABLED`).

### Configuration Files

- `config/customer/customer-config.yaml:269-273` — declares `features:projectChargeback` with `enabled: false` by default (matches the task's precondition).
- `src/codemie/configs/customer_config.py` — `CONFIG_IDS` dict (no `projectChargeback` key), `CustomerConfig.is_component_enabled`/`is_feature_enabled` methods that resolve the flag from the YAML component list when no `CONFIG_IDS` override exists.
- `src/external/alembic/alembic.ini` / `src/external/alembic/env.py` — standard Alembic wiring, unchanged by this task except for the new migration file itself.

### Feature Flags and Deployment Concerns

- `features:projectChargeback` is disabled by default in the shipped YAML, matching the stated precondition — no config file change is required for this sub-task.
- Because the flag is not registered in `CONFIG_IDS`, it cannot currently be toggled via any runtime/admin API in the way `chatContextualNaming` or `enterpriseEdition` can — enabling it in a live deployment requires editing and redeploying the YAML file, not a runtime PUT call. Whether this sub-task needs to add a `CONFIG_IDS` entry (to match the runtime-toggle pattern) or simply consume the YAML-only value as-is is not specified by the acceptance criteria.
- No Dockerfile, CI/CD, or deployment-manifest reference to `chargeback_attribution` or `projectChargeback` exists.

---

## 6. Risk Indicators

- **Multi-head Alembic tree**: a scripted scan of `src/external/alembic/versions/` found **13 unmerged heads** (including `f1g2h3i4j5k6`, the `chargeback_enabled` migration itself, which is now a head with no children). Per `.ai-run/guides/data/database-patterns.md`, Alembic state must be inspected (`alembic heads`) before choosing `down_revision` for the new `chargeback_attribution` migration — picking a stale or wrong head risks a detached branch.
- **`features:projectChargeback` is not consumed anywhere in `src/codemie`**: the flag is fully wired at the YAML/config layer (and unit-tested at that layer), but AC5 requires new code to call `customer_config.is_feature_enabled("projectChargeback")` from scratch inside `ProjectService`/router logic — no direct precedent gates a project-field *write* this way; the closest analogues gate router entry (`assistant_project_mapping.py`) or a boolean-AND service condition (`toolkit_service.py`). This exact gap was already flagged once in the prior task's own code review (`docs/superpowers/tasks/2026-08-12-.../code-review-final.json:40`) and remains open.
- **Cost-center validation ambiguity**: `ProjectService._resolve_updated_cost_center_id` only re-validates cost-center activity when a *new* `cost_center_id` is supplied in the same request; when it falls back to `project.cost_center_id` (no change requested), it does not re-check `deleted_at`. AC6 requires rejecting `chargeback_attribution="cost_center"` when the *linked* cost center is missing or soft-deleted — this may need a new validation path that reads the project's current cost-center state independently of `cost_center_id`/`clear_cost_center` request params, since a soft-delete could have happened out-of-band (via `CostCenterService.delete`) after the project was originally linked.
- **`ProjectVisibilityService` dict-shape indirection**: `_build_project_detail_response` reads `chargeback_enabled` via `project_detail.get("chargeback_enabled", False)`, not from the `Application` ORM object directly — a new `chargeback_attribution` field must be threaded through the visibility-service enriched dicts (list and detail) in addition to the model/repository/schema layers, or the detail/list API responses will silently omit or default it.
- **`ProjectUpdateRequest.validate_non_empty` must be updated**: this validator explicitly enumerates every mutable field in its "at least one provided" check; forgetting to add `chargeback_attribution` to that boolean expression means a request that sets *only* `chargeback_attribution` would be incorrectly rejected with "At least one mutable field must be provided."
- **No existing enum/CheckConstraint pattern for a chargeback-shaped string column**: `chargeback_attribution` is described as taking values `"project"` | `"cost_center"` (per the sibling UI-layer research doc), but `Application` has no existing SQLModel-level `Enum` field — the closest precedent (`project_type`) is a plain `str` column constrained only by a `CheckConstraint`, not a Python `Enum` class. The implementation must decide between reusing that pattern or introducing a new one.
- **Zero test coverage for the new field at every layer** — model, migration, repository, service validation, and router/schema — consistent with AC8's explicit call for new tests covering valid-project, valid-cost-center, missing-cost-center, and soft-deleted-cost-center cases.

---

## 7. Summary for Complexity Assessment

This sub-task touches four backend layers with a clear, already-precedented path for three of them: DB-Persistence (`Application` model + a new Alembic migration, following the exact shape of the existing `f1g2h3i4j5k6_add_chargeback_enabled_to_applications.py`), Repository (`ApplicationRepository.update_project`, extending the existing `if <field> is not None:` guard idiom), and API (`ProjectListItem`/`ProjectDetailResponse`/`ProjectCreateResponse`/`ProjectUpdateRequest` in `src/codemie/rest_api/routers/projects.py`, plus `ProjectVisibilityService`'s enriched dicts that the detail-response builder reads from). The minimal file-change surface mirrors the prior `chargeback_enabled` task almost one-to-one: model, migration, repository, service, two visibility-service dict sites, router schemas/handlers, and tests — roughly 6-9 files.

The genuinely novel part of this task is the Service layer's validation and feature-flag logic, which has no direct precedent in this codebase. `customer_config.is_feature_enabled("projectChargeback")` is fully wired at the config/YAML layer (and already unit-tested there) but is called nowhere in `src/codemie` — this sub-task is the first to consume it, and the prior task's own code review already flagged this exact gap as unresolved. Similarly, the "linked, non-deleted cost center" validation required by AC6/AC7 has a partial precedent (`CostCenterService.ensure_exists_for_project` / `CostCenterRepository.get_active_by_id`), but that precedent only validates a *newly supplied* `cost_center_id`, not a project's *already-linked* one — `_resolve_updated_cost_center_id`'s fallback branch does not re-check soft-delete state, which is the specific gap AC6's soft-deleted-cost-center test case is designed to expose.

Test coverage posture is favorable for the mechanical layers (a directly analogous test file, `test_project_service_chargeback.py`, and a directly analogous router test class, `TestChargebackEnabledField`, both exist as copy-adapt templates) but there is zero existing coverage for the flag-consumption and cost-center-validation logic this task must write from scratch, and no existing test exercises the stale-linked-cost-center scenario at any layer. The parent story's own complexity assessment (`docs/superpowers/tasks/2026-08-25-epmcdme-14387/complexity-assessment.json`) explicitly scoped this backend slice as its own Story 1 (sized M) separate from the cross-repo `codemie-chargeback` CronJob work, and named the same two novel risk areas (feature-flag enforcement gap, missing test template for soft-delete/validation edge cases) called out here.

---

## 8. External References

None named by the task as a filesystem path or URL. `task_context` refers to "Parent story EPMCDME-14387" by ticket ID only, without naming a specific file path — per Step 0 scope, this is not treated as an explicit external source. However, exploratory research (Step 1-2) located directly relevant prior artifacts already in this repository under `docs/superpowers/tasks/`, which are cited throughout Sections 2-7 as supporting evidence rather than as Section-0 sourced references:

- `docs/superpowers/tasks/2026-08-25-epmcdme-14387/complexity-assessment.json` — the parent story's own complexity assessment, which explicitly defines this sub-task's scope as "Story 1 (M, codemie backend)" with the same file list and the same two headline risks (feature-flag enforcement gap; missing cost-center/soft-delete test template) identified independently in this document.
- `docs/superpowers/tasks/2026-08-25-epmcdme-14387/technical-analysis.md` — UI-layer (codemie-ui repo) research for the same parent story; confirms `chargeback_attribution` values are `"project"` | `"cost_center"` and that the UI depends on this backend sub-task landing first.
- `docs/superpowers/tasks/2026-08-12-add-chargeback-enabled-column-projects/technical-analysis.md` and `plan.md` — the immediately prior backend task that added `chargeback_enabled`, `features:projectChargeback` (YAML + `CustomerConfig.is_feature_enabled` test coverage), and the full API/repository/service wiring this sub-task extends. Its `code-review-final.json:40` flags "No test covers `is_feature_enabled('projectChargeback')`" as an unresolved gap, which is still true in current source and is directly relevant to this sub-task's AC5.
