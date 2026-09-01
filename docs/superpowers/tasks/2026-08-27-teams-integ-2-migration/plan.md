# Migrate assistant_project_mapping into ms_teams Settings — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a single new Alembic migration that copies `assistant_project_mapping` rows into
`ms_teams`-typed `settings` rows (one per project, aggregated), then drops
`assistant_project_mapping`, with a schema-only, aggregate-delete `downgrade()`.

**Architecture:** One migration file under `src/external/alembic/versions/`, `down_revision =
"8b2c1a4d5e6f"`. `upgrade()` uses raw SQL via `op.get_bind()` only (no ORM/SQLModel import of either
table's model) to group `assistant_project_mapping` by `project_name`, skip projects that already
have an `ms_teams` settings row, insert the rest as aggregated `settings` rows, then
`op.drop_table`. `downgrade()` recreates the table schema (matching `7ca305066800`) and issues a
compensating `DELETE`, matching the precedent in `8b2c1a4d5e6f`.

**Tech Stack:** Alembic, SQLAlchemy Core (`op.get_bind()`, raw SQL text), PostgreSQL (native enum
`codemie.credentialtypes`, JSONB via `PydanticListType`).

**Spec:** `/Users/Andriy_Lukashchuk/Dev/code-assistant/docs/superpowers/tasks/2026-08-27-teams-integ-2-migration/spec.md`

## Global Constraints

- `down_revision = "8b2c1a4d5e6f"` (Story 1's migration, current head).
- Never read, write, or import anything related to `features:teamsBotIntegration`.
- No router/service/repository file for `assistant_project_mapping` is modified or removed (Story 4).
- No unit test file for the transform logic (spec Non-Goal — explicitly out of scope for this story).
- Do not re-validate migrated `assistant_id`s against `AssistantService.belongs_to_project` — data is
  trusted as-is (spec Non-Goal).
- Commit per task using the repository's existing convention (see recent commits on this branch for
  the `<TICKET>: <summary>` shape actually in use — do not invent a new format).

**Critical DB-literal correction (verified against already-merged code, not narrative text):**
`settings.credential_type` and `settings.setting_type` are native Postgres enum columns
(`src/external/alembic/versions/074d06e75b25_create_settings.py:55,95`); SQLAlchemy's default
`sa.Enum` binds the Python enum **member name**, not `.value`, to these columns. Confirmed by
`8b2c1a4d5e6f_add_ms_teams_credential_type.py:84`, whose own `downgrade()` runs
`DELETE FROM codemie.settings WHERE credential_type = 'MS_TEAMS'` (uppercase) even though
`CredentialTypes.MS_TEAMS.value == "ms_teams"` (lowercase; `src/codemie_tools/base/models.py:110`).
Every raw-SQL literal this migration writes or matches against `settings.credential_type` MUST be
`'MS_TEAMS'` (uppercase enum name), and every `setting_type` literal MUST be `'PROJECT'` (uppercase;
confirmed by the same `074d06e75b25` migration's `postgresql.ENUM('USER', 'PROJECT', ...)`). The
spec's illustrative snippets write `credential_type='ms_teams'` — that casing is for narrative
clarity about *which* credential type, not the literal SQL value; follow this constraint, not the
spec's literal casing.

---

## Task 1: Write the assistant_project_mapping → settings migration

**Files:**
- Create: `src/external/alembic/versions/c3d4e5f6a7b8_migrate_assistant_project_mapping_to_settings.py`
  (revision id is a placeholder 12-hex string in this plan; the implementer must generate a fresh
  unique 12-hex id and use it consistently for `revision` and the filename prefix — check
  `src/external/alembic/versions/` for collisions before committing).

**Interfaces:**
- Produces: `upgrade()` / `downgrade()` module-level functions per Alembic's standard revision
  contract — no other task in this plan consumes them.

- [ ] **Step 1: Author the migration file**

  Header: copy the Apache-2.0 license block verbatim from
  `src/external/alembic/versions/8b2c1a4d5e6f_add_ms_teams_credential_type.py:1-13`, followed by the
  standard revision docstring/id block (`revision`, `down_revision = "8b2c1a4d5e6f"`,
  `branch_labels = None`, `depends_on = None`) matching the shape at
  `8b2c1a4d5e6f_add_ms_teams_credential_type.py:15-33`.

  `upgrade()`:

  ```python
  import json
  import logging
  from datetime import datetime, timezone
  from uuid import uuid4

  import sqlalchemy as sa
  import sqlmodel
  from alembic import op
  from sqlalchemy.dialects.postgresql import JSONB

  logger = logging.getLogger(__name__)


  def upgrade() -> None:
      bind = op.get_bind()

      rows = bind.execute(
          sa.text(
              "SELECT project_name, assistant_id FROM assistant_project_mapping "
              "ORDER BY project_name, created_at"
          )
      ).fetchall()

      by_project: dict[str, list[str]] = {}
      for project_name, assistant_id in rows:
          ids = by_project.setdefault(project_name, [])
          if assistant_id not in ids:
              ids.append(assistant_id)

      insert_stmt = sa.text(
          "INSERT INTO codemie.settings "
          "(id, date, update_date, project_name, credential_type, setting_type, "
          "is_global, \"default\", user_id, alias, created_by, credential_values) "
          "VALUES "
          "(:id, :row_date, :row_date, :project_name, 'MS_TEAMS', 'PROJECT', "
          "false, false, NULL, NULL, NULL, :credential_values)"
      ).bindparams(sa.bindparam("credential_values", type_=JSONB))

      for project_name, assistant_ids in by_project.items():
          existing = bind.execute(
              sa.text(
                  "SELECT 1 FROM codemie.settings "
                  "WHERE project_name = :project_name AND credential_type = 'MS_TEAMS' LIMIT 1"
              ),
              {"project_name": project_name},
          ).fetchone()
          if existing is not None:
              logger.warning(
                  "Skipping ms_teams migration for project %r: an ms_teams settings row "
                  "already exists; %d assistant_project_mapping row(s) left unmigrated.",
                  project_name,
                  len(assistant_ids),
              )
              continue

          bind.execute(
              insert_stmt,
              {
                  "id": str(uuid4()),
                  "row_date": datetime.now(timezone.utc),
                  "project_name": project_name,
                  "credential_values": json.dumps(
                      [{"key": "assistant_ids", "value": assistant_ids}]
                  ),
              },
          )

      op.drop_table("assistant_project_mapping")
  ```

  `downgrade()` — schema recreated verbatim from `7ca305066800_create_assistant_project_mapping.py:21-43`
  (same columns, FK to `assistants.id` with `ondelete="CASCADE"`, unique constraint
  `uix_assistant_project_mapping`, index `ix_assistant_project_mapping_project_name`), followed by
  the compensating delete matching `8b2c1a4d5e6f_add_ms_teams_credential_type.py:84`:

  ```python
  def downgrade() -> None:
      op.create_table(
          "assistant_project_mapping",
          sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
          sa.Column("assistant_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
          sa.Column("project_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
          sa.Column("feature", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
          sa.Column("created_at", sa.DateTime(), nullable=False),
          sa.Column("updated_by", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
          sa.ForeignKeyConstraint(["assistant_id"], ["assistants.id"], ondelete="CASCADE"),
          sa.PrimaryKeyConstraint("id"),
          sa.UniqueConstraint(
              "assistant_id", "project_name", "feature", name="uix_assistant_project_mapping"
          ),
      )
      op.create_index(
          op.f("ix_assistant_project_mapping_project_name"),
          "assistant_project_mapping",
          ["project_name"],
          unique=False,
      )
      op.execute("DELETE FROM codemie.settings WHERE credential_type = 'MS_TEAMS'")
  ```

  Both functions live in the same module, so the `sqlmodel`/`sqlalchemy as sa`/`alembic.op` imports
  shown above the `upgrade()` block cover `downgrade()` too — do not duplicate imports.

- [ ] **Step 2: Manually verify upgrade/downgrade against a local Postgres instance**

  Using the project's local dev DB setup (per `.ai-run/guides/development/setup-guide.md`), with at
  least one `assistant_project_mapping` row seeded for a project that has no existing `ms_teams`
  settings row, and one project seeded with both an `assistant_project_mapping` row *and* a
  pre-existing `ms_teams` settings row:
  - Run `alembic upgrade head` (or the equivalent Makefile target for migrations) and confirm: the
    first project gets exactly one new `settings` row with `credential_type = 'MS_TEAMS'`,
    `setting_type = 'PROJECT'`, and `credential_values` equal to
    `[{"key": "assistant_ids", "value": [<the seeded assistant ids>]}]`; the second project's
    pre-existing `settings` row is unchanged and a warning naming that project was logged;
    `assistant_project_mapping` no longer exists (`\d assistant_project_mapping` in `psql` errors).
  - Run `alembic downgrade -1` and confirm `assistant_project_mapping` exists again (empty, correct
    columns/FK/unique constraint/index) and both `settings` rows with `credential_type = 'MS_TEAMS'`
    are gone.
  - Fix any literal/bind-parameter mismatch found and re-run until both directions match the above.

  Test-first: no — spec Non-Goals explicitly excludes a unit test for the grouping/transform logic;
  correctness is confirmed by this manual upgrade/downgrade run against a local DB rather than an
  automated test.

- [ ] **Step 3: Commit**

  Commit per the repository's existing convention (see recent commits on this branch for the
  ticket-prefixed subject shape actually in use).

---

## Self-review notes

**Spec coverage:**
- New revision, `down_revision = "8b2c1a4d5e6f"` — Task 1, Step 1.
- Group-by-project aggregation, distinct `assistant_id`s, ordered for determinism — Task 1, Step 1
  (`ORDER BY project_name, created_at` + de-dup on insert into `ids`).
- Skip-and-log conflict path (no merge/overwrite) — Task 1, Step 1 `existing` check + `logger.warning`.
- Insert shape (`id`, `date`/`update_date`, `is_global=False`, `default=False`, `user_id=None`,
  `alias=None`, `created_by=None`, single `assistant_ids` credential_values entry) — Task 1, Step 1
  INSERT statement.
- `op.drop_table` for `assistant_project_mapping` (table+FK+unique+index) — Task 1, Step 1, end of
  `upgrade()`.
- `downgrade()` schema-only recreate + aggregate delete — Task 1, Step 1 `downgrade()`.
- Never touches `features:teamsBotIntegration` — no reference anywhere in Task 1; confirmed absent.
- No router/service/repository/test file touched — Task 1 only creates the migration file.

**Negative-constraint pass:**
- "No merge logic with any other mechanism" / "such projects are skipped, not merged or
  overwritten" → Task 1 Step 1's `existing is not None: ... continue` branch never updates or reads
  into the pre-existing row; confirmed no other branch writes to a project once `existing` is found.
- "Do not touch or gate anything behind features:teamsBotIntegration" → no task reads
  `customer_config`, `is_feature_enabled`, or imports `assistant_project_mapping.py`'s router.
- "No router, service, or repository file ... is modified or removed" → Task 1's only file is the
  new migration; `assistant_project_mapping_service.py`, its repository, and its router are not in
  the Files list of any task.
- "A unit test for the grouping/transform logic — out of scope" → Task 1 Step 2 is manual
  verification, not an automated test file; `Test-first: no` is stated with the reason.
- "Restoring per-row granularity on downgrade — out of scope" → `downgrade()` in Task 1 recreates
  schema only, no data restore, matching spec's explicit acceptance.
- "Re-validating assistant_ids against AssistantService.belongs_to_project — out of scope" → Task 1's
  `upgrade()` never imports or calls `AssistantService`.
- negative-constraints: all stated above accounted for; none omitted.
