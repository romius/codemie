# Spec: Migrate `assistant_project_mapping` into `ms_teams` Settings (EPMCDME-14111 Story 2/4)

## Context

Story 1 (merged) added a project-scoped `ms_teams` `CredentialTypes` value and its validation
(`src/codemie/service/settings/settings_request_validator.py::validate_ms_teams_request`,
`src/codemie/rest_api/models/settings.py::SettingsBase.check_ms_teams_singleton`). The legacy
Teams integration still stores its data in `assistant_project_mapping`
(`src/external/alembic/versions/7ca305066800_create_assistant_project_mapping.py`,
`src/codemie/rest_api/models/usage/assistant_project_mapping.py`). This story writes the one-time
Alembic migration that moves that data into `settings` rows shaped the way Story 1 already
validates, then drops the source table. Router/service/repository removal is Story 4.

## Approach

A single new revision under `src/external/alembic/versions/`, `down_revision = "8b2c1a4d5e6f"`
(Story 1's migration, current head), following the Apache-2.0-header convention of recent files
in that directory.

`upgrade()`:
1. Query `assistant_project_mapping` grouped by `project_name` (raw SQL via `op.get_bind()`, no
   ORM — the table's model will not necessarily still exist by the time this migration runs in
   sequence with later work), collecting the distinct `assistant_id` values per project, ordered
   by `created_at` for determinism.
2. For each project group, check for an existing `ms_teams` row in `settings`
   (`credential_type = 'ms_teams'`, mirroring `check_ms_teams_singleton`'s query shape,
   `src/codemie/rest_api/models/settings.py:33`). If one exists, **skip this project**: log a
   warning naming the project and the count of mapping rows left unmigrated, and leave the
   existing `settings` row untouched. This is the only conflict path — no merge, no overwrite.
3. Otherwise, insert one `settings` row: `id=str(uuid4())`, `date=update_date=utcnow()`,
   `project_name=<group>`, `credential_type='ms_teams'`, `setting_type='PROJECT'`,
   `is_global=False`, `default=False`, `user_id=None`, `alias=None`, `created_by=None`,
   `credential_values` serialized as the single-entry shape Story 1 already expects:
   ```json
   [{"key": "assistant_ids", "value": ["<assistant_id>", ...]}]
   ```
   matching `CredentialValues` (`src/codemie/rest_api/models/settings.py`) and the
   `PydanticListType` column encoding already used by `settings.credential_values`.
4. Drop `assistant_project_mapping` (table, its FK to `assistants.id`, its unique constraint, its
   index) via `op.drop_table`.

`downgrade()`: recreate `assistant_project_mapping` with its original columns, FK
(`ondelete="CASCADE"`), unique constraint, and index — schema only, no data restore, matching
`7ca305066800`'s `upgrade()` shape — then `DELETE FROM codemie.settings WHERE credential_type =
'ms_teams'`, following the compensating-delete precedent in `8b2c1a4d5e6f_add_ms_teams_credential_type.py`
and `6cce754bc484_add_google_oauth_credential_type.py`. This restores schema symmetry; it does not
and cannot restore the original per-row `(assistant_id, project_name, feature)` granularity once
rows have been aggregated into `assistant_ids` lists — accepted per Non-goals.

No application code (router/service/repository), no test file, and no reference to
`features:teamsBotIntegration` are touched by this change.

## Acceptance Criteria

- New Alembic revision exists with `down_revision = "8b2c1a4d5e6f"`, chaining onto Story 1's head.
- `upgrade()` produces at most one `settings` row per `project_name` with
  `credential_type='ms_teams'`, `setting_type='PROJECT'`, and exactly one `credential_values`
  entry `{key: "assistant_ids", value: [...]}` containing the distinct `assistant_id`s from that
  project's `assistant_project_mapping` rows.
- A project that already has an `ms_teams` `settings` row before the migration runs keeps that row
  unchanged, and its `assistant_project_mapping` rows are left unmigrated (logged, not merged).
- `assistant_project_mapping` (table, FK, unique constraint, index) no longer exists after
  `upgrade()`.
- `downgrade()` recreates `assistant_project_mapping`'s schema (empty) and deletes all
  `credential_type='ms_teams'` rows from `settings`.
- The migration never reads, writes, or imports anything related to `features:teamsBotIntegration`.
- No router, service, or repository file for `assistant_project_mapping` is modified or removed.

## Non-Goals

- Removing or modifying the `assistant_project_mapping` router, service, or repository — Story 4.
- Any merge logic with another mechanism when a project already has an `ms_teams` settings row —
  such projects are skipped, not merged or overwritten.
- Touching, reading, or gating anything behind `features:teamsBotIntegration`.
- A unit test for the grouping/transform logic — out of scope for this story.
- Restoring per-row `assistant_project_mapping` granularity on downgrade — only schema and an
  aggregate `settings` delete are restored.
- Re-validating migrated `assistant_id`s against `AssistantService.belongs_to_project` at migration
  time — that is a live-service call inappropriate for a raw data migration; data is trusted as
  already valid at its source.
- Resolving the repository's pre-existing multi-head Alembic state (13 heads) — unrelated to this
  story.

## Open Risks

- Until Story 4 removes the `assistant_project_mapping` router/service/repository code, that code
  will query a table this migration drops. Sequencing this migration's deploy relative to Story 4
  is a decision for the user/reviewer; this migration cannot resolve it.
- `assistant_id`s carried into `settings.assistant_ids` are not re-checked for project membership
  at migration time; if source data was already inconsistent, that inconsistency is carried
  forward silently.
- Skipped-project logging is best-effort (a warning), not a persisted audit trail — an operator
  wanting a list of skipped projects must read migration logs at run time.
