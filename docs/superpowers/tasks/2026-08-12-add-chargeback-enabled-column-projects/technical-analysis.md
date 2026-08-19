# Technical Research

**Task**: projects chargeback budget migration
**Generated**: 2026-08-12T00:00:00Z

---

## 1. Original Context

add column to projects chargeback_enabled, create migration, for all projects that have assigned budgets - true by default, else - false

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/core/models.py` (lines 380–449): `Application` class, SQLModel table `applications`. Current columns: `name` (PK via `id`), `display_name`, `description`, `git_repos`, `project_type`, `created_by`, `cost_center_id`, `deleted_at`. No `chargeback_enabled` field exists yet.
- `src/codemie/service/budget/budget_models.py`: `ProjectBudgetAssignment` SQLModel, table `project_budget_assignments`. Key columns: `project_name`, `budget_category`, `budget_id`, `deleted_at`. Active assignments are rows where `deleted_at IS NULL`.
- `src/codemie/repository/application_repository.py`: `ApplicationRepository` (sync SQLModel) — `create`, `get_by_name_case_insensitive`, `list_visible_projects_paginated`. The repository method `create` passes keyword arguments directly to the model constructor; a new column with a model-level default will be picked up automatically.
- `src/codemie/repository/project_budget_repository.py`: `ProjectBudgetAssignmentRepository` — has `get_active_for_project` and `get_active_for_projects` returning `ProjectBudgetAssignment` rows with `deleted_at IS NULL`. These are the queries needed to determine which projects have assigned budgets.
- `src/codemie/service/project/project_service.py`: `ProjectService.create_shared_project` and `update_project` — creates projects via `application_repository.create(...)`. No current handling of `chargeback_enabled`.
- `src/codemie/rest_api/routers/projects.py`: FastAPI router. Response models `ProjectListItem`, `ProjectDetailResponse`, `ProjectCreateResponse`, `ProjectUpdateRequest` will need `chargeback_enabled` if the field must be exposed via API.
- `src/external/alembic/versions/`: All Alembic migrations. Migrations that modified `applications` table directly: `p1q2r3s4t5u6_add_display_name_to_applications.py` (add `display_name`), `a4d9b6c2e7f1_add_deleted_at_to_applications.py` (add `deleted_at`), `5d6358638299_add_description_to_applications.py` (add `description`), `4c96275fa12e_create_applications.py` (original table creation).

### Architecture and Layers Affected

- **DB-Persistence**: `Application` model in `src/codemie/core/models.py` — add `chargeback_enabled: bool` field. Migration file to be added under `src/external/alembic/versions/`.
- **Repository**: `ApplicationRepository` in `src/codemie/repository/application_repository.py` — no change required unless a dedicated query for `chargeback_enabled` is needed. The existing `create` method passes kwargs to the model; new field with default will work without code change.
- **Service**: `ProjectService` in `src/codemie/service/project/project_service.py` — may need to accept and propagate `chargeback_enabled` if it is a mutable field post-creation. Not strictly required for the data migration task alone.
- **API**: `src/codemie/rest_api/routers/projects.py` — `ProjectListItem`, `ProjectDetailResponse`, `ProjectCreateRequest`, `ProjectUpdateRequest` response/request models will need updating if the field is exposed to API consumers.

### Integration Points

- `project_budget_assignments` table is the source-of-truth for determining which projects have active budget assignments (WHERE `deleted_at IS NULL`). The migration backfill must JOIN against this table.
- `application_repository.create()` uses keyword argument expansion — new model fields with defaults do not require changes to the call sites in `ProjectService.create_shared_project`.
- `ProjectDetailResponse.enforce_member_spend_limits` (line 226 in `projects.py`) shows a precedent for boolean project feature flags in API responses. However, `enforce_member_spend_limits` is stored in `SettingsService` (not a DB column), making `chargeback_enabled` a new pattern of directly storing a boolean flag in the `applications` table.

### Patterns and Conventions

- **SQLModel field declaration**: New fields on `Application` use `SQLField(default=...)`. Example from `deleted_at`: `deleted_at: Optional[datetime] = SQLField(default=None)`. For a non-nullable boolean with `False` as default: `chargeback_enabled: bool = SQLField(default=False, nullable=False)`.
- **Migration pattern for adding a column to `applications`**: Use `op.add_column("applications", sa.Column(..., server_default=..., nullable=...))`. Closest example: `p1q2r3s4t5u6_add_display_name_to_applications.py` uses `op.add_column("applications", sa.Column("display_name", sa.String(150), nullable=True))`.
- **Data backfill in migrations**: `a1b2c3d4e5f7_add_project_budget_tables.py` shows the pattern for an `op.execute(UPDATE ...)` immediately following `op.add_column`.
- **Migration chaining**: Each migration file has `revision` and `down_revision` identifiers. The current head of the main branch is `9b9b4c585e54` (`add_clone_count_and_assistant_clone_`). New migration's `down_revision` should be `"9b9b4c585e54"`.
- **Downgrade**: All `add_column` migrations have corresponding `op.drop_column` in `downgrade()`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/data/database-patterns.md`: Confirms Alembic migrations live in `src/external/alembic/versions/`. Instructs to inspect Alembic state before changing migration chains. References `README.md:90` for migration workflow.
- `.ai-run/guides/data/repository-patterns.md`: Repositories own data access. New DB columns should be added to the matching model and repository rather than bypassing them from the service layer.
- `.ai-run/guides/architecture/layered-architecture.md`: Referenced in AGENTS.md but not yet read in this session; the general layering is API → Service → Repository → DB-Persistence.
- `.ai-run/guides/architecture/project-structure.md`: Referenced in AGENTS.md; package boundaries are established.

### Architectural Decisions

- **Soft-delete pattern**: `deleted_at IS NULL` is the universal active-record filter across `applications`, `project_budget_assignments`, `project_member_budget_assignments`, and `budgets`. The backfill query must respect this.
- **`enforce_member_spend_limits` stored in SettingsService** (not as a column): This was a deliberate decision to keep project feature flags out of the core `applications` table. Adding `chargeback_enabled` as a direct column represents a different pattern and should be intentional.
- **Column naming**: The existing `applications` table uses `snake_case` column names, consistent with SQLModel/SQLAlchemy conventions.

### Derived Conventions

- Migrations for the `applications` table follow a straightforward `op.add_column` / `op.drop_column` pattern with `server_default` for non-nullable additions.
- Boolean project feature flags have been stored outside the `applications` table so far (`enforce_member_spend_limits` in SettingsService). Storing `chargeback_enabled` as a direct column is simpler and avoids a settings layer lookup.
- Model fields use `SQLField(...)` wrapper with `default` for Python-side default and `server_default` for DB-side default where needed.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/repository/test_application_repository_pagination.py`: Tests `list_visible_projects_paginated`, pagination, offset/limit logic. Uses `MagicMock` for the session. Does NOT test the `Application` model fields directly.
- `tests/codemie/repository/test_application_repository_visibility.py`: Tests project visibility conditions.
- `tests/codemie/rest_api/routers/test_projects_router.py`: Unit tests for project router endpoints including `create_project`, `list_projects`, `get_project_detail`, `update_project`. Uses `AsyncMock` for session and mocks `ProjectService`. Tests import `Application` model.
- `tests/codemie/service/budget/test_project_budget_service.py`, `test_project_budget_service_lifecycle.py`: Cover budget assignment lifecycle, relevant for understanding the data source for the backfill condition.

### Testing Framework and Patterns

- **Framework**: pytest with `pytest-asyncio` for async tests.
- **Mocking**: `unittest.mock.MagicMock` / `AsyncMock` for sessions and repositories; `patch.object` for repository methods.
- **No fixtures that create real DB rows**: Tests compile SQL queries via `sqlalchemy.dialects.postgresql` and assert the generated SQL strings. No actual database is involved.
- **Test file location convention**: `tests/codemie/repository/` for repository tests, `tests/codemie/rest_api/routers/` for router tests, `tests/codemie/service/` for service tests.

### Coverage Gaps

- No tests exist for `chargeback_enabled` field (field does not yet exist).
- No tests exist verifying the Application model serializes/deserializes `chargeback_enabled`.
- No migration-level tests exist in the repo (migrations are not tested automatically).
- The router response models (`ProjectListItem`, `ProjectDetailResponse`) are not directly unit-tested for field presence — adding `chargeback_enabled` to these models will need tests in `test_projects_router.py`.
- The data backfill logic in the migration (projects with active budget assignments get `true`) has no automated validation path.

---

## 5. Configuration and Environment

### Environment Variables

- No environment variables are directly related to the `chargeback_enabled` column. Budget-related env vars (e.g., provider credentials) are in `src/codemie/configs/budget_config.py` and `src/codemie/configs/config.py` but do not gate the `applications` table schema.

### Configuration Files

- `src/external/alembic/alembic.ini`: Standard Alembic configuration. `script_location = %(here)s`, versions under `src/external/alembic/versions/`. `sqlalchemy.url` is overridden at runtime via `src/external/alembic/env.py`.
- `src/external/alembic/env.py`: Sets the DB URL from application config at runtime.
- `pyproject.toml`: `alembic = "^1.15.2"` and `alembic-postgresql-enum = "^1.7.0"` are declared dependencies.

### Feature Flags and Deployment Concerns

- No feature flag currently gates the `chargeback_enabled` column addition.
- The migration is a non-destructive `ADD COLUMN` with a `server_default`, so it is safe to run on a live PostgreSQL instance without downtime (no table lock beyond the brief DDL).
- The `UPDATE` backfill in `upgrade()` will acquire row-level locks. On large deployments with many projects and assignments, the UPDATE may take a few seconds. A maintenance window is not required but should be noted.
- `downgrade()` should be a plain `op.drop_column("applications", "chargeback_enabled")`.

---

## 6. Risk Indicators

- **Multi-head migration tree**: `src/external/alembic/versions/` has many unresolved heads from parallel feature branches. The correct `down_revision` for the new migration must be confirmed by running `alembic heads` before filing the new migration. The most recent head in the main chain traced from the last `applications`-touching migration is `9b9b4c585e54`. Using the wrong head will create a new detached branch.
- **No existing column-level boolean flags on `applications`**: `enforce_member_spend_limits` is stored in `SettingsService`, not in the table. Adding `chargeback_enabled` as a direct column is a new pattern in this domain — there is no immediately analogous field in `Application` to compare with.
- **Backfill complexity**: The UPDATE logic (`WHERE name IN (SELECT DISTINCT project_name FROM project_budget_assignments WHERE deleted_at IS NULL)`) touches potentially every row in `applications`. Must verify that `project_budget_assignments.project_name` always matches `applications.name` (FK from `project_budget_assignments.project_name → applications.id`).
- **No tests for `chargeback_enabled`**: The field does not exist yet; no test coverage at any layer. Tests will need to be written for the model field, any API response model changes, and the service layer if the field becomes mutable.
- **API surface**: `ProjectListItem`, `ProjectDetailResponse`, and `ProjectCreateResponse` in `src/codemie/rest_api/routers/projects.py` do not currently include `chargeback_enabled`. If this field must be returned by the API, all three models need updating — as does `ProjectUpdateRequest` if the field is settable post-creation.
- **Short task description (< 50 words)**: Requirements are terse. No acceptance criteria or user story. Whether `chargeback_enabled` should be mutable after project creation (i.e., exposed via `PATCH /projects/{name}`) is unspecified. This is a Requirements Clarity risk.

---

## 7. Summary for Complexity Assessment

The task touches three layers: **DB-Persistence** (Application SQLModel + Alembic migration), **Repository** (ApplicationRepository — likely no change needed, new field flows through existing `create` kwargs), and **API** (projects router response/request models if the field is exposed). The file change surface is small: 2–4 files if restricted to model + migration + response models, up to 6 files if service and test files are included. The migration itself is a straightforward non-nullable `ADD COLUMN … DEFAULT false` followed by a targeted `UPDATE` backfill joining `applications` against `project_budget_assignments`.

The task follows an established migration pattern used in `p1q2r3s4t5u6_add_display_name_to_applications.py` and `a4d9b6c2e7f1_add_deleted_at_to_applications.py`, so there is no technical novelty at the migration level. The one novel aspect is that `chargeback_enabled` would be the first directly-persisted boolean feature flag in the `applications` table — all previous boolean project flags (`enforce_member_spend_limits`) are stored in the SettingsService layer. This pattern difference should be intentional and documented.

Test coverage of the affected area is thin: existing `test_application_repository_pagination.py` and `test_application_repository_visibility.py` do not exercise model fields. New tests are needed for the model field, migration backfill correctness, and any API response shape changes. The key risk is the multi-head Alembic tree requiring `alembic heads` verification before setting `down_revision`, and the underspecified API surface (is `chargeback_enabled` readable and/or mutable via REST?).
