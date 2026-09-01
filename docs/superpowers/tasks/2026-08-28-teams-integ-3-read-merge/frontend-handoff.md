# Frontend Handoff — EPMCDME-14111 (Teams → Integrations, final)

Status: Stories 1–3 complete and merged into this branch (not yet merged to `main`). This
is a backend-only repo — no UI change is included here. This doc supersedes the Story 1
handoff (`docs/superpowers/tasks/2026-08-27-teams-integ-1-foundation/frontend-handoff.md`):
everything under "What has NOT changed" there has now landed.

## What changed since Story 1

- **Old path is gone.** `assistant_project_mapping` (router, service, repository, model,
  and its table) has been fully removed. There is no fallback, dual-read, or merge logic —
  `ms_teams` Settings rows are now the *only* source of truth for "Teams-enabled
  assistants" per project.
- **Existing data was migrated**, not dropped: every pre-existing `assistant_project_mapping`
  row was folded into (or merged with) an `ms_teams` Settings row for the same project
  before the old table was removed. No manual backfill is needed from the frontend side.
- **The `features:teamsBotIntegration` flag is now the gate for `ms_teams` itself** (it was
  previously untouched/reserved for later reuse — see Story 1's doc). `POST`/`PUT` on an
  `ms_teams` setting now returns **403** if the flag is disabled for the customer.
- **`credential_type` wire value changed casing: `"ms_teams"` → `"MSTeams"`.** This aligns
  it with the rest of the `CredentialTypes` enum (`Git`, `LiteLLM`, `AzureDevOps`, ...),
  which was previously inconsistent. This affects the request payload, list/detail
  responses, and error messages that echo `credential_type` back — any frontend code that
  matches on the literal string `"ms_teams"` must be updated to `"MSTeams"`. No DB
  migration was needed for this (the DB stores the enum by a separate internal name,
  unaffected), but it is a breaking change for any client-side string match.

## API surface (unchanged shape from Story 1, still no new routes)

Use the existing generic Settings/Integrations endpoints:

- `POST /v1/settings/project` — create
- `PUT /v1/settings/project/{setting_id}` — update
- `GET /v1/settings/project` — list (existing generic listing; `ms_teams` rows now include
  the migrated data)
- `DELETE /v1/settings/project/{setting_id}` — delete

### Request shape (unchanged)

```json
{
  "project_name": "my-project",
  "alias": "teams-integration",
  "credential_type": "MSTeams",
  "setting_type": "project",
  "credential_values": [
    { "key": "assistant_ids", "value": ["assistant-id-1", "assistant-id-2"] }
  ]
}
```

Validation rules are unchanged from Story 1 (project-scope only, exactly one
`assistant_ids` entry, non-empty, no duplicates, every id must belong to `project_name`),
**plus** the new feature-flag check below.

### Errors — updated list

Standard `ExtendedHTTPException` shape (`message`, `details`, `help`):

- **403 — new.** `Teams Bot Integration is not enabled for this customer.` Raised before
  any other validation runs, on both create and update, whenever
  `features:teamsBotIntegration` is disabled. Surface this the same way you'd surface any
  other disabled-feature 403 (e.g. hide/disable the entry point, or show the same
  "contact your administrator" messaging used elsewhere).
- `400` — wrong scope (`setting_type` must be `"project"`), missing/invalid
  `assistant_ids`, invalid/duplicate assistant ids, or (new) a `PUT` whose
  `project_name` doesn't match the setting being updated (`project_name` is now
  server-verified against the existing row, not trusted from the request body).
- `409` — singleton violation. A project may only have one `ms_teams` row; a second
  `POST`, or a concurrent race that would create a second one, is rejected. To change the
  assistant list, `PUT` the existing row instead of creating a new one.

### Assistant selection for the UI (unchanged from Story 1)

Only assistants that exist and belong to the target project may be listed in
`assistant_ids`. There is still no "enabled" filter enforced server-side — the picker
should filter to enabled assistants client-side.

### Housekeeping the frontend doesn't need to handle

Deleting an assistant, or moving it to a different project, now automatically prunes its
id out of any `ms_teams.assistant_ids` list it was part of (best-effort, non-blocking on
the backend side — it will never fail the delete/move itself). No frontend action needed;
mentioning it so a "Teams-enabled assistants" list won't unexpectedly show a stale/deleted
assistant id after this lands.

## What's still open (not blocking frontend work)

- Two commits on this branch still need a manual commit-message squash before the MR
  (cosmetic, ticket-prefix only — no functional impact).
- `make sonar-local` static analysis is still owed pre-merge (no `SONAR_TOKEN` in this
  environment) — unrelated to the API contract described above.

Everything described here is final for this ticket unless product revisits the
`teamsBotIntegration` flag's behavior — no further story is planned to change this API.
