# Spec — Deprecate ZephyrSquad integration (backend)

**Ticket**: EPMCDME-10913
**Repo**: codemie/ (backend, Python/FastAPI)
**Companion run**: codemie-ui/ (separate MR, cross-linked)

## Goal

Prevent new ZephyrSquad settings from being created or updated in CodeMie, expose a machine-readable `deprecated` flag on the tool catalog so the UI can render a banner, and keep existing ZephyrSquad settings readable (read-only) with no data migration.

## Behavior

### 1. `GET /v1/tools` surfaces the deprecated flag

`ToolMetadata` and `Tool` (`src/codemie_tools/base/models.py`) gain a `deprecated: bool = False` field. `ZEPHYR_SQUAD_TOOL` (`src/codemie_tools/qa/zephyr_squad/tools_vars.py`) sets `deprecated=True`. The field flows through the existing `model_dump()` pipeline into the `GET /v1/tools` response; the UI reads it to draw a deprecation banner.

Default is `False` so existing tools are unaffected. Addition is backward-compatible (new key in the response object).

### 2. Create/update blocked on `POST` and `PUT`

Both settings routers reject write operations for `credential_type == CredentialTypes.ZEPHYR_SQUAD`:

- `src/codemie/rest_api/routers/user_settings.py` — `create_user_setting` and `update_user_setting`
- `src/codemie/rest_api/routers/project_settings.py` — `create_project_setting` and `update_project_setting`

Rejection uses `ExtendedHTTPException(code=HTTP_410_GONE, message="<Type> integration is deprecated", details="New <Type> settings can no longer be created or updated. Existing configurations remain read-only.", help=<replacement guidance>)`.

The guard is **generic, not ZephyrSquad-specific**. `src/codemie/service/settings/settings_request_validator.py` — home of `validate_git_request` / `validate_scheduler_request` / `validate_webhook_request` — owns:

- `DEPRECATED_CREDENTIAL_TYPES: dict[CredentialTypes, str]` — the registry, mapping a deprecated credential type to its replacement hint;
- `validate_credential_type_not_deprecated(request)` — the single check that builds the 410 from that registry.

Each of the four write endpoints calls it once, **before** the per-credential-type `if/elif` dispatch chain: deprecation is orthogonal to which validator a type needs, so it must not depend on branch ordering. Deprecating the next integration is one entry in `DEPRECATED_CREDENTIAL_TYPES` — no router edits. This mirrors the existing `UNSUPPORTED_SCHEDULER_DATASOURCE_TYPES` / `UNSUPPORTED_WEBHOOK_DATASOURCE_TYPES` pattern in the same module, and matches the generic `deprecated` flag used on the tool-catalog side.

### 3. Reads remain unaffected

`GET /v1/settings/user` and `GET /v1/settings/project` continue to return existing ZephyrSquad rows. The `CredentialTypes.ZEPHYR_SQUAD` enum entry stays in place; no PostgreSQL enum change, no Alembic migration.

### 4. No behavior change to the ZephyrSquad tool class itself

`ZephyrSquadGenericTool` and `ZephyrRestAPI` are untouched. Existing agents that have a ZephyrSquad credential attached continue to invoke it. Only the creation of NEW settings is blocked.

## Non-goals

- No hard delete of existing settings rows.
- No removal of the `ZephyrSquad` enum value from `CredentialTypes` (would break deserialization of existing rows).
- No UI changes in this run — those live in codemie-ui/.
- No documentation changes to end-user guides in this repo (docs live in codemie-onboarding/).

## Acceptance criteria

- [ ] `GET /v1/tools` response contains `deprecated: true` on the `ZephyrSquad` entry and `deprecated: false` (or absent-defaulted to false) on all other tools.
- [ ] `POST /v1/settings/user` with `credential_type: ZephyrSquad` returns HTTP 410 with the ExtendedHTTPException envelope (`error.message`, `error.details`, `error.help`).
- [ ] `PUT /v1/settings/user/{id}` for a ZephyrSquad setting returns HTTP 410 with the same envelope.
- [ ] `POST /v1/settings/project` and `PUT /v1/settings/project/{id}` behave identically to their user-settings counterparts for ZephyrSquad.
- [ ] `GET /v1/settings/user` and `GET /v1/settings/project` still return pre-existing ZephyrSquad rows unchanged.
- [ ] Existing tests in `test_qa_toolkit.py` continue to pass (or are updated to assert `deprecated=True` on the ZephyrSquad tool entry rather than removal).
- [ ] Unit tests exist for both new negative router cases (create + update, user + project = 4 cases minimum).

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| `lru_cache` on `get_available_toolkits_info()` masks the new flag until process restart | Existing behavior; note in MR description |
| `test_qa_toolkit.py` asserts tool count and name inclusion | ZephyrSquad stays in the toolkit list; only add `deprecated=True` assertion, no removal |
| Missing `deprecated` field on other Pydantic consumers of `ToolMetadata` | Default `False` keeps consumers backward-compatible; grep to confirm |
| Both user and project routers must be patched symmetrically | Explicit test coverage across all four write endpoints |
| Deprecating a future integration means re-editing four endpoints and missing one | Resolved by the shared `DEPRECATED_CREDENTIAL_TYPES` registry + `validate_credential_type_not_deprecated`: the rule lives in one place, endpoints only call it |
