# Spec: ms_teams Integration Type (Foundation) — EPMCDME-14111 Story 1/4

## Context

Teams bot configuration is currently managed via the standalone
`assistant_project_mapping` table/router/service (project ↔ assistant,
feature `"teams"`), gated by `features:teamsBotIntegration`
(`config/customer/customer-config.yaml`). This story introduces the target
representation — a new `ms_teams` integration type on the existing generic
`Settings`/`CredentialTypes` model (`src/codemie/rest_api/models/settings.py`,
`src/codemie_tools/base/models.py`) — without touching the old mechanism.
Later stories (2–4) migrate the data, update assistant-listing to read the
new representation, and retire the old path and its flag.

## What changes

1. **Enum member**: add `MS_TEAMS = "ms_teams"` to `CredentialTypes`
   (`src/codemie_tools/base/models.py:67`). `CredentialTypes` backs a Postgres
   ENUM type (`credentialtypes`); add an Alembic migration under
   `src/external/alembic/versions/` that runs `ALTER TYPE credentialtypes ADD
   VALUE 'ms_teams'`, with `down_revision` set to the actual current head
   (determine from the versions directory, not assumed).

2. **Payload shape**: no new column or table. A project-scoped `Settings`
   row with `credential_type = MS_TEAMS` carries the assistant list as one
   entry in the existing `credential_values: List[CredentialValues]`
   (`settings.py:39`):

   ```
   CredentialValues(key="assistant_ids", value=[<assistant_id>, ...])
   ```

   `Settings.project_name` (existing field) carries the project scope; no
   new field is added to `Settings`.

3. **Router** (`src/codemie/rest_api/routers/project_settings.py`):
   - `create_project_setting` (`POST /v1/settings/project`): add an
     `ms_teams` validation branch alongside the existing
     `SCHEDULER`/`LITE_LLM`/`GIT`/`WEBHOOK` branches. Reject the request
     (400, `ExtendedHTTPException`) if `SettingType != PROJECT` — `ms_teams`
     is project-scope only, never `USER`. Validate the `assistant_ids`
     payload (see §4) and the singleton rule (see §5) before calling
     `_check_permission` and `SettingsService.create_setting`, matching the
     existing branch ordering.
   - `PUT /v1/settings/project/{setting_id}`: apply the same `assistant_ids`
     and singleton validation whenever the update targets an `ms_teams` row
     or changes its `assistant_ids` entry.
   - `GET /v1/settings/project`: no change — `ms_teams` rows list through
     the existing `SettingsIndexService.run` path like any other type.
   - `DELETE /v1/settings/project/{setting_id}`: no change — deletion for
     `ms_teams` uses the existing project-scope delete path as-is.
   - Permission check: reuse `_check_permission(user, project_name)`
     unchanged for all `ms_teams` create/update/delete calls.
   - No new gating: `ms_teams` paths are **not** wrapped in a
     `features:teamsBotIntegration` (or any other) feature-flag check. The
     flag remains exactly as-is, continuing to gate only the old
     `assistant_project_mapping` endpoints; it is not read, referenced, or
     modified anywhere in this story's code paths.

4. **Assistant validation**: an assistant id is eligible for
   `assistant_ids` only if it exists and `Assistant.project` equals the
   target `project_name` (`src/codemie/rest_api/models/assistant.py`) —
   mirroring the existence check the old mapping performs at enable-time
   (`assistant_project_mapping.py`'s `_get_assistant_by_id_or_raise`), now
   additionally scoped to the target project. Any id failing this check
   fails the request with a 400/404 `ExtendedHTTPException` naming the
   offending id(s). Applies on both create and update.

5. **Singleton enforcement**: before creating an `ms_teams` row, check for
   an existing non-deleted `ms_teams` `Settings` row with the same
   `project_name`; reject creation with a 409/400
   `ExtendedHTTPException` if one exists. This is a new, `ms_teams`-specific
   app-level check, applied the same read-then-write way the existing
   `(project_name, alias)` uniqueness check works
   (`SettingsBase.check_alias_unique`) — no new DB constraint.

## Non-goals

- No Alembic data migration moving `assistant_project_mapping` rows into
  `ms_teams` `Settings` rows (later story).
- No changes to `assistant_project_mapping.py` (router/service/repository)
  or its table/model — not touched, not removed.
- No changes to `features:teamsBotIntegration`: not removed, not
  repurposed, and not newly referenced by the `ms_teams` code paths added
  here.
- No change to assistant-listing/"Teams-enabled assistants" merge logic —
  that reads today from the old mapping only; wiring it to `ms_teams` is a
  later story.
- No fix to the existing alias-uniqueness race pattern (read-then-write,
  no DB constraint) — the new singleton check follows the same known
  pattern rather than introducing a different one.
- No fix to the existing `DELETE /v1/settings/project/{setting_id}`
  inconsistency (calls `Settings.delete_setting` directly, bypassing
  `SettingsService.delete_setting`) — `ms_teams` deletion uses whatever
  that path already does.
- No frontend/UI work — this repository is backend-only; any Teams
  configuration UI change is out of scope here.

## Acceptance criteria

- `CredentialTypes.MS_TEAMS == "ms_teams"` exists and a passing Alembic
  migration adds it to the Postgres `credentialtypes` enum type.
- `POST /v1/settings/project` with `credential_type=ms_teams`,
  `setting_type=PROJECT` succeeds when `assistant_ids` are all valid
  (exist and belong to the target project) and no other `ms_teams` row
  exists for that project.
- The same request with `setting_type=USER` is rejected.
- The same request containing an assistant id that does not exist, or
  that belongs to a different project, is rejected.
- A second `POST` for an `ms_teams` row on a project that already has one
  is rejected.
- `PUT` re-validates `assistant_ids` the same way when the list changes.
- `GET /v1/settings/project` and `DELETE /v1/settings/project/{id}` work
  for `ms_teams` rows without any new code path (existing generic
  behavior applies unmodified).
- No test, code path, or config change references `features:teamsBotIntegration`
  in connection with the new `ms_teams` paths.
