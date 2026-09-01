# Frontend Handoff — EPMCDME-14111 Story 1/4 (Foundation)

Status: implementation + fixes complete, check-round review in progress, **not yet merged**.
This is a backend-only repo — no UI change is included here. This doc is what the frontend
needs to know to build against once it lands.

## What this story does

Adds a new Integrations type, `ms_teams`, on the existing generic Settings/Integrations
API (`/v1/settings/project`). It does **not** yet touch the old Teams-mapping endpoints,
does **not** migrate existing data, and does **not** change how "Teams-enabled assistants"
are listed — those are later stories (2–4) in this same ticket. Treat `ms_teams` as
additive/parallel to the old mechanism for now.

## New integration type: `ms_teams`

Use the existing generic Settings endpoints — no new routes.

- `POST /v1/settings/project` — create
- `PUT /v1/settings/project/{setting_id}` — update
- `GET /v1/settings/project` — list (existing generic listing, unchanged)
- `DELETE /v1/settings/project/{setting_id}` — delete (existing generic delete, unchanged)

### Request shape

```json
{
  "project_name": "my-project",
  "alias": "teams-integration",
  "credential_type": "ms_teams",
  "setting_type": "project",
  "credential_values": [
    { "key": "assistant_ids", "value": ["assistant-id-1", "assistant-id-2"] }
  ]
}
```

- `credential_type` must be the literal string `"ms_teams"`.
- `setting_type` must be `"project"` — `"user"` is rejected (HTTP 400). This integration
  cannot be created/updated at user scope, only project scope.
- `credential_values` must contain **exactly one** entry with `key == "assistant_ids"`,
  whose `value` is a **non-empty** array of assistant id strings with **no duplicates**.
  Any of the following are rejected with HTTP 400:
  - missing/empty `assistant_ids` list
  - more than one `assistant_ids` entry
  - duplicate ids within the list
  - any id that doesn't exist or doesn't belong to `project_name` (error message names
    the offending id(s))

### Singleton rule

Only **one** `ms_teams` row is allowed per project. A second `POST` for a project that
already has one is rejected with **HTTP 409** ("ms_teams integration already exists").
To change the assistant list, `PUT` the existing row instead of creating a new one.

### Assistant selection for the UI

- Only assistants that already exist and belong to the target project may be listed in
  `assistant_ids`. There is currently **no "enabled" filter enforced server-side in this
  story** — the spec's "only enabled assistants are selectable" requirement should be
  enforced by the UI when building the picker (filter to enabled assistants client-side),
  since this backend change only validates existence + project ownership, not the
  assistant's enabled state. Flagging this so the UI doesn't rely on the API to filter it.

### Errors

Standard `ExtendedHTTPException` shape (`message`, `details`, `help`), status codes:
- `400` — wrong scope, missing/invalid `assistant_ids`, invalid/duplicate assistant ids
- `409` — singleton violation (project already has an `ms_teams` row)

## What has NOT changed (don't build against these yet)

- The old `assistant_project_mapping` endpoints/table are untouched — still the source of
  truth for "Teams-enabled assistants" listing today. Not removed until Story 4.
- The `features:teamsBotIntegration` flag is untouched — still gates only the old mapping
  endpoints. It is **not** used to gate `ms_teams`. Per explicit product direction, this
  flag will be **kept and reused for the new UI** later (not removed), so frontend can plan
  to reuse it for `ms_teams` UI rollout rather than expecting a new flag.
- No merge of "Teams-enabled assistants" between old mapping and `ms_teams` is implemented —
  a later story switches the listing to read from `ms_teams` integrations only (old mapping
  is fully retired, not merged).
- No data migration yet — any `ms_teams` rows you create now are net-new and independent of
  existing mapping data until Story 2 migrates it.

## Recommended sequencing for frontend work

Given the flag (`features:teamsBotIntegration`) is being kept for UI reuse, it's reasonable
to start UI work for the new `ms_teams` picker behind that flag now against this API, but:
- expect the "Teams-enabled assistants" list endpoint(s) to still reflect only old-mapping
  data until Story 3 lands
- expect that flipping a project over fully (old mapping fully gone) only happens after
  Story 4

Happy to share the same handoff again once Stories 2–4 land with a diff of what actually
changes in the listing/read path.
