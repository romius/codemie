# Technical Research

**Task**: teams migration alembic settings
**Generated**: 2026-08-27
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-14111 Story 2 of 4 (Data migration): write an Alembic migration that moves existing assistant_project_mapping rows into ms_teams Settings/CredentialTypes rows (the project-scoped ms_teams integration type added in Story 1, already merged on this branch: src/codemie/service/settings/settings_request_validator.py validate_ms_teams_request, src/codemie/rest_api/models/settings.py SettingRequest/SettingsBase, CredentialTypes enum extended with ms_teams), then drops the assistant_project_mapping table (router/service/repository removal is Story 4, not this story — only the table + data move here). No merge logic with any other mechanism — this is a one-way data move. Do not touch or gate anything behind the features:teamsBotIntegration flag — leave it untouched.

---

## 2. Codebase Findings

### Existing Implementations

**Source table (`assistant_project_mapping`)** — to be migrated and dropped:
- `src/external/alembic/versions/7ca305066800_create_assistant_project_mapping.py` — creates the table: columns `id` (str PK), `assistant_id` (FK → `assistants.id`, ON DELETE CASCADE), `project_name` (indexed), `feature` (str), `created_at` (datetime), `updated_by` (str); unique constraint `uix_assistant_project_mapping` on `(assistant_id, project_name, feature)`; index `ix_assistant_project_mapping_project_name`.
- `src/codemie/rest_api/models/usage/assistant_project_mapping.py` — `AssistantProjectMappingSQL` SQLModel (table=True, `__tablename__ = "assistant_project_mapping"`); `AssistantProjectFeature(str, Enum)` with a single member `TEAMS = "teams"`; `AssistantProjectMappingRequest` request model (`project_name`, `feature`).
- `src/codemie/service/assistant/assistant_project_mapping_service.py` — `AssistantProjectMappingService` with `list`, `enable`, `disable`; enforces project-admin/project-access checks via `User`; raises `AssistantProjectMappingNotFound` / `AssistantProjectMappingForbidden`.
- `src/codemie/repository/assistants/assistant_project_mapping_repository.py` — `AssistantProjectMappingRepository` ABC + `SQLAssistantProjectMappingRepository` impl (`create`, `delete`, `get_assistant_ids`, `exists`), all using `Session(PostgresClient.get_engine())` directly (not the ES-backed `BaseModelWithSQLSupport` pattern used by `Settings`).
- `src/codemie/rest_api/routers/assistant_project_mapping.py` — router gates every endpoint behind `_require_teams_bot_feature()` which checks `customer_config.is_feature_enabled("teamsBotIntegration")`. Per task scope, router/service/repository removal is out of scope for this story (Story 4) — this file, the service, and the repository are **not** touched by this migration story; only the `assistant_project_mapping` table itself is dropped by the Alembic migration once data is copied out.
- Tests: `tests/codemie/repository/assistants/test_assistant_project_mapping_repository.py`, `tests/codemie/service/assistant/test_assistant_project_mapping_service.py` — these exercise the table/service directly and will need attention if/when the table is dropped, though removing them is Story 4's concern per the task's scoping, not this story's.

**Destination model (`settings` table, ms_teams `CredentialTypes`)** — already merged in this branch:
- `src/codemie/rest_api/models/settings.py`:
  - `CredentialValues(BaseModel)` — `key: str`, `value: Any`.
  - `SettingRequest(BaseModel)` — `project_name`, `alias`, `credential_type: CredentialTypes`, `credential_values: List[CredentialValues]`, `is_global`, `oauth_state`.
  - `SettingType(str, Enum)` — `USER`, `PROJECT`.
  - `SettingsBase(CommonBaseModel)` — fields: `user_id` (indexed, optional), `created_by: Optional[CreatedByUser]`, `project_name` (indexed), `alias` (optional), `default` (bool, default False), `credential_type: CredentialTypes` (indexed), `credential_values: List[CredentialValues]` (stored via `PydanticListType`), `setting_hash` (optional), `setting_type: Optional[SettingType]` (default `USER`), `is_global` (default False). Has GIN trigram indexes on `project_name`/`alias` and a plain index on `date`.
  - `SettingsBase.check_ms_teams_singleton(project_name, setting_id=None)` — enforces at most one `ms_teams` setting row per `project_name` (queries `PROJECT_NAME_TERM` + `credential_type.keyword == CredentialTypes.MS_TEAMS.value`).
  - `Settings(BaseModelWithSQLSupport, SettingsBase, table=True)`, `__tablename__ = "settings"` — has classmethods `get_by_project_names`, `find_by_resource_id`, `get_all_project_litellm_settings` (a precedent for querying/filtering PROJECT-scoped settings by `credential_type`), `delete_setting`, `get_by_alias`.
  - `CommonBaseModel` (in `src/codemie/rest_api/models/base.py`): `id: Optional[str]` (PK, no default_factory — callers must set it, e.g. `str(uuid4())`), `date: Optional[datetime]`, `update_date: Optional[datetime]`.
- `src/codemie/service/settings/settings_request_validator.py::validate_ms_teams_request(request, setting_type, setting_id=None)` — enforces: setting_type must be `PROJECT`; `credential_values` must contain exactly one entry with `key == "assistant_ids"` whose `value` is a non-empty list with no duplicates; every id in that list must satisfy `AssistantService.belongs_to_project(assistant_id, request.project_name)`; and `Settings.check_ms_teams_singleton` must not already find a row for that project. This is the shape the migrated data must conform to: **one `Settings` row per project, `credential_type=ms_teams`, `setting_type=PROJECT`, and a single `credential_values` entry `{key: "assistant_ids", value: [...]}`** — this is what Story 1 already validates for API-created rows, not something this research is asserting the migration must produce (see Section 6).
- `src/codemie_tools/base/models.py` — `CredentialTypes(str, Enum)`, line 110 `MS_TEAMS = "ms_teams"` (already merged, per task).
- `src/external/alembic/versions/8b2c1a4d5e6f_add_ms_teams_credential_type.py` — Story 1's already-merged migration. `down_revision = 'f1g2h3i4j5k6'`; adds `MS_TEAMS` to the Postgres `credentialtypes` enum via `alembic_postgresql_enum.op.sync_enum_values(...)` against `codemie.settings.credential_type`. Its `downgrade()` does `DELETE FROM codemie.settings WHERE credential_type = 'MS_TEAMS'` before shrinking the enum — the established pattern in this repo for reversible enum-value migrations tied to `settings.credential_type`.

### Architecture and Layers Affected
- **Data / migration layer only for this story**: `src/external/alembic/versions/` (new revision file). No router, service, or repository code is in scope — Story 4 owns removing `assistant_project_mapping_service.py`, its repository, and its router.
- The migration touches two tables in the `codemie` schema: reads from `assistant_project_mapping`, writes into `settings`, then drops `assistant_project_mapping` (and its FK/unique constraint/index).

### Integration Points
- `assistant_project_mapping` has a `ForeignKeyConstraint` to `assistants.id` with `ondelete="CASCADE"` — dropping the table removes that constraint; no other table references `assistant_project_mapping`.
- `settings.credential_type` is backed by a Postgres native enum `codemie.credentialtypes`, already extended with `MS_TEAMS` by revision `8b2c1a4d5e6f` (`alembic_postgresql_enum` package, `TableReference(table_schema='codemie', table_name='settings', column_name='credential_type')`).
- `AssistantService.belongs_to_project` (imported in `settings_request_validator.py` from `codemie.service.assistant.assistant_service`) is the run-time check used for API writes; not directly relevant to a raw-SQL/ORM data migration but shows the project↔assistant relationship the migrated `assistant_ids` values must still satisfy logically.
- `customer_config.is_feature_enabled("teamsBotIntegration")` (`src/codemie/rest_api/routers/assistant_project_mapping.py`) is the only place the `features:teamsBotIntegration` flag is checked; also declared in `config/customer/customer-config.yaml:244` (`- id: "features:teamsBotIntegration"`). The task explicitly says to leave this untouched — the migration must not read, write, or gate on this flag.

### Patterns and Conventions
- **Alembic revision id / heads**: revision ids in this repo are 12-hex-character strings (mixed real hex and hand-picked mnemonic hex-like strings, e.g. `8b2c1a4d5e6f`, `f1g2h3i4j5k6`). The repository currently has **many concurrent Alembic heads** (13 leaf revisions found by walking `revision`/`down_revision` across `src/external/alembic/versions/*.py`), including `8b2c1a4d5e6f` (the ms_teams enum migration) itself as a head. A new migration for this story should set `down_revision = "8b2c1a4d5e6f"` to chain directly after Story 1's merged migration; whether a subsequent merge-heads revision is required is an operational/db-guide concern, not something this research resolves.
- **Data-migration pattern precedent**: `aeb028ff26ac_set_null_is_global_to_false_in_settings.py` — a `settings`-table data migration using a single `op.execute("UPDATE settings SET ...")` with a no-op `downgrade()`.
- **Enum + data cleanup precedent**: `8b2c1a4d5e6f_add_ms_teams_credential_type.py` and `6cce754bc484_add_google_oauth_credential_type.py` — both use `op.sync_enum_values(...)` for schema changes and plain `op.execute("DELETE FROM codemie.settings WHERE credential_type = '<TYPE>'")` for reversible data cleanup in `downgrade()`.
- **Non-trivial data-transform migration precedent**: `k5l6m7n8o9p0_deprecate_python_repl_code_interpreter.py` exposes private helper functions (`_transform_toolkits`, `_transform_workflow_tools`, `_transform_workflow_assistants`, `_transform_yaml_config`) at module level that are unit-tested directly by importing from `external.alembic.versions.<module>` in `tests/codemie/migrations/test_k5l6m7n8o9p0_deprecate_python_repl.py` — this is the established pattern for making migration transform logic testable without a live DB.
- `Settings`/`SettingsBase` use `BaseModelWithSQLSupport`/ES-backed query helpers (`get_by_fields`, `get_all_by_fields`) for most reads, but `Settings.get_by_project_names` and `find_by_resource_id` use raw `Session(cls.get_engine())` + `select(cls)` SQLAlchemy queries — this is the precedent for querying `settings` rows relationally rather than through the ES-backed helpers, relevant to a migration script that must operate purely against Postgres.

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `.ai-run/guides/data/database-patterns.md` — states migrations belong under `src/external/alembic/versions/`, to inspect Alembic head state before changing migration chains, and to prefer SQLModel/SQLAlchemy expressions over raw SQL string interpolation.
- No guide specifically dedicated to "data migration" content/patterns (e.g. moving rows between tables) was found under `.ai-run/guides/`; `database-patterns.md` covers schema migrations generally.

### Architectural Decisions
- No ADR or inline `DECISION:`/`ADR:` marker found referencing `assistant_project_mapping` → `settings` migration; the task ticket itself (EPMCDME-14111 Story 1–4 breakdown) is the only recorded decision trail visible in the repo (via the already-merged Story 1 code and its migration).

### Derived Conventions
- Migration filenames follow `<revision_id>_<snake_case_description>.py`.
- `downgrade()` is implemented even for pure data migrations, generally as either a no-op (`aeb028ff26ac`) or a compensating `DELETE`/reverse-transform (`8b2c1a4d5e6f`, `6cce754bc484`).
- Migration files carry the same Apache-2.0 license header as the newer files in `src/external/alembic/versions/` (e.g. `8b2c1a4d5e6f_add_ms_teams_credential_type.py`), while older ones (e.g. `7ca305066800_create_assistant_project_mapping.py`, `aeb028ff26ac_...py`) do not — recent files consistently include it.

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/repository/assistants/test_assistant_project_mapping_repository.py` and `tests/codemie/service/assistant/test_assistant_project_mapping_service.py` cover the source table's repository/service today.
- `tests/codemie/migrations/test_k5l6m7n8o9p0_deprecate_python_repl.py` is the one example of a migration-logic unit test in the repo, importing transform helpers directly from the migration module under `external.alembic.versions.*`.
- No existing test targets `assistant_project_mapping` → `settings` data movement, `Settings.check_ms_teams_singleton`, or `validate_ms_teams_request` end-to-end migration behavior.

### Testing Framework and Patterns
- pytest (`pytest.ini` present at repo root). Migration-logic tests import private helper functions from the versioned migration module rather than running Alembic end-to-end against a live DB.

### Coverage Gaps
- No test exists yet for the new migration's transform behavior (grouping `assistant_project_mapping` rows by project, producing `Settings` rows, or dropping the table).
- No test exists for the interaction between migrated data and `check_ms_teams_singleton`/`validate_ms_teams_request` (e.g., what happens if a project already has an ms_teams `Settings` row from another path when the migration runs).

---

## 5. Configuration and Environment

### Environment Variables
- None found scoped specifically to `assistant_project_mapping` or this migration.

### Configuration Files
- `config/customer/customer-config.yaml:244` — declares `features:teamsBotIntegration` as a customer-config feature id. This is the flag the task says must remain untouched; it governs only the router in `assistant_project_mapping.py`, not the Settings/CredentialTypes path, and is unrelated to how a raw Alembic data migration executes.

### Feature Flags and Deployment Concerns
- `features:teamsBotIntegration` (`config/customer/customer-config.yaml`, checked via `customer_config.is_feature_enabled(...)` in `src/codemie/rest_api/routers/assistant_project_mapping.py`) — explicitly out of scope per the task; the migration must not read or reference it.
- No Dockerfile/CI/CD reference to `assistant_project_mapping` or `ms_teams` migrations was found.

---

## 6. Risk Indicators

- Speculative: the migration will need a query/transform step that groups `assistant_project_mapping` rows by `(project_name, feature='teams')` into one `assistant_ids` list per project to satisfy `validate_ms_teams_request`'s "exactly one `assistant_ids` credential_values entry" and `check_ms_teams_singleton`'s "at most one ms_teams row per project" constraints — this grouping is not itself present anywhere in the codebase and would need to be authored fresh in the migration.
- Speculative: because `Settings.credential_values` is stored via `PydanticListType` and `id`/`date` have no `default_factory` at the `CommonBaseModel` level (unlike `AssistantProjectMappingSQL.id`, which does have `default_factory=lambda: str(uuid4())`), a migration that inserts `Settings` rows via raw SQL/Core rather than the ORM will need to generate `id` values and populate `date`/`credential_values` JSON explicitly.
- The repository currently has **13 distinct Alembic heads** (verified by walking `revision`/`down_revision` pairs across all files in `src/external/alembic/versions/`), including the Story 1 migration `8b2c1a4d5e6f` itself as a head. Chaining the new migration onto `8b2c1a4d5e6f` is straightforward, but the pre-existing multi-head state is a latent risk for anyone running `alembic upgrade head` without specifying a target, and is unrelated to this story but present in the environment it will land in.
- `assistant_project_mapping` has a `ForeignKeyConstraint` to `assistants.id` (`ondelete="CASCADE"`) and a unique constraint/index; dropping the table removes these — no other table was found referencing `assistant_project_mapping`, but this was verified only by filename/content grep (`assistant_project_mapping`), not a full FK graph traversal of the live schema.
- No existing test covers migration-time interaction with `Settings.check_ms_teams_singleton` (e.g., a project that already has a manually-created ms_teams `Settings` row before this migration runs) — this is a coverage gap the task's "no merge logic with any other mechanism" instruction implies should simply not be handled, but it is unverified by any test today.
- The router, service, and repository for `assistant_project_mapping` remain in the codebase and reference the table this migration drops; per the task these are explicitly Story 4's responsibility, but until Story 4 lands, that code will be querying a table that no longer exists once this migration runs (relevant to sequencing/deployment order, not to this story's own correctness).

---

## 7. Summary for Complexity Assessment

This task is scoped to a single new Alembic migration file under `src/external/alembic/versions/`, touching two existing tables (`assistant_project_mapping` as source, `settings` as destination) and no application code — no router, service, or repository changes are in scope (those are Story 4). The destination shape (`Settings` row per project, `credential_type=ms_teams`, `setting_type=PROJECT`, one `assistant_ids` credential_values entry) is already fully specified by Story 1's merged validator (`validate_ms_teams_request`) and model (`SettingsBase.check_ms_teams_singleton`), which reduces design ambiguity for what "done" looks like, even though translating "many mapping rows per project" into "one settings row per project" is a genuine grouping transform that has no direct precedent in this codebase.

Precedent exists for both the schema-only piece (enum extension + `DELETE ... WHERE credential_type = ...` cleanup in `8b2c1a4d5e6f` and `6cce754bc484`) and for pure UPDATE-style data migrations (`aeb028ff26ac`) and for transform-logic unit testing of a migration module (`k5l6m7n8o9p0`, which unit-tests private helpers imported straight from the migration file). No precedent exists for a migration that both aggregates rows across a group-by and drops the source table in the same revision, and no test currently exercises the migration's core transform or its interaction with the singleton/validator constraints from Story 1 — this is the primary source of risk rather than any structural novelty in where the code lives.

Testing posture is thin: only one migration-logic test file exists in the whole repo (`tests/codemie/migrations/test_k5l6m7n8o9p0_deprecate_python_repl.py`), and it covers an unrelated feature; the `assistant_project_mapping` tests that do exist only cover the code this story does not touch. The repo's multi-head Alembic state (13 heads) is a pre-existing environmental risk, not one this story introduces, but a plan should confirm the correct `down_revision` (`8b2c1a4d5e6f`) and check whether any merge-heads step is expected around this change.

---

## 8. External References

None named by the task. The task names in-repo paths (`src/codemie/service/settings/settings_request_validator.py`, `src/codemie/rest_api/models/settings.py`, and the `CredentialTypes` enum) as the Story 1 source of truth — these were read directly and are reported in full under Section 2 rather than here, since they are inside this repository, not an external source.
