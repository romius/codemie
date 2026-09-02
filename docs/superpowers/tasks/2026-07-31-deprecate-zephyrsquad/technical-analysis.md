# Technical Research

**Task**: integrations zephyr zephyrsquad tool-registry connectors
**Generated**: 2026-07-31T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-10913 — Deprecate ZephyrSquad integration in the CodeMie backend (codemie/ repo, Python/FastAPI).

Full ticket description:
Deprecate the ZephyrSquad integration in the CodeMie platform to prevent new usage of an outdated integration option, reduce maintenance overhead, and keep the integrations list aligned with supported tools.

Preconditions:
- ZephyrSquad integration is currently present in CodeMie.

Scenarios:
1. User opens CodeMie Tools/Integrations (integration/datasource setup area).
2. ZephyrSquad integration is not available for new configuration (or is explicitly marked as deprecated with restricted actions).
3. If existing ZephyrSquad configurations exist, CodeMie handles them per deprecation behavior (read-only / disabled / removed with a message).
4. User sees a clear deprecation message and guidance on next steps.

User has chosen: "Read-only with deprecation banner" for existing configurations.
Scope for THIS run: BACKEND ONLY (codemie/). UI changes will be a separate run in codemie-ui/.

Backend responsibilities to research:
- How is the ZephyrSquad integration currently registered / exposed to the platform (integration types enum, tool catalog, connector/plugin registry)?
- Where is the create/update path for ZephyrSquad integrations that we'd need to block server-side?
- How would we mark an integration type as deprecated at the API / model layer (e.g., a `deprecated` flag returned in integration metadata that the UI can render)?
- Existing test patterns for integration registry / integration APIs.
- Any migrations or data changes needed for existing ZephyrSquad configs.

---

## 2. Codebase Findings

### Existing Implementations

**Credential type enum**
- `src/codemie_tools/base/models.py` line 96 — `CredentialTypes` enum; `ZEPHYR_SQUAD = "ZephyrSquad"` entry. Adjacent entry at line 95: `_ZEPHYR_CLOUD = "ZephyrCloud"  # Deprecated` (underscore-prefix naming convention used to signal earlier deprecation, but does not surface a flag to the API).

**ZephyrSquad tool definition**
- `src/codemie_tools/qa/zephyr_squad/tools_vars.py` — defines `ZEPHYR_SQUAD_TOOL = ToolMetadata(name="ZephyrSquad", label="Zephyr Squad", settings_config=True, config_class=ZephyrSquadConfig)`. No `deprecated` field on `ToolMetadata` currently.

**QA toolkit registration**
- `src/codemie_tools/qa/toolkit.py` line 31 — `QualityAssuranceToolkitUI` lists ZephyrSquad as second tool via `Tool.from_metadata(ZEPHYR_SQUAD_TOOL, tool_class=ZephyrSquadGenericTool)`. Total 5 QA tools: ZephyrScale, ZephyrSquad, XrayGetTests, XrayCreateTest, XrayExecuteGraphQL.
- `QualityAssuranceToolkit(DiscoverableToolkit)` at line 39 auto-discovers and publishes the definition.

**Autodiscovery / tools catalog**
- `src/codemie_tools/base/toolkit_provider.py` — `get_available_toolkits_info()` (line 321) scans all `DiscoverableToolkit` subclasses and returns `toolkit_class.get_definition().model_dump()`. Result is `@functools.lru_cache`-d. `get_available_tools_configs_info()` (line 451) does the same for `CodeMieToolConfig` subclasses.
- `src/codemie/service/tools/tools_info_service.py` — `ToolsInfoService.get_tools_info()` (line 35) calls `toolkit_provider.get_available_toolkits_info()` to assemble the response for `GET /v1/tools`.

**Settings database model**
- `src/codemie/rest_api/models/settings.py` — `Settings(BaseModelWithSQLSupport, SettingsBase, table=True)` at line 365; `__tablename__ = "settings"`. `SettingsBase` (line 244) columns: `user_id`, `created_by`, `project_name`, `alias`, `default`, `credential_type`, `credential_values`, `setting_hash`, `setting_type`, `is_global`. No `deprecated` column exists.

### Architecture and Layers Affected

- **API layer (routers)**: `src/codemie/rest_api/routers/user_settings.py` and `src/codemie/rest_api/routers/project_settings.py` — both expose create/update endpoints; blocking logic goes here.
- **Service layer**: `src/codemie/service/settings/settings_request_validator.py` — existing validator pattern for per-credential-type guards (scheduler, litellm, git, webhook). A new ZephyrSquad guard follows this pattern.
- **Tool metadata / model layer**: `src/codemie_tools/base/models.py` — `ToolMetadata` and `Tool` Pydantic models must gain a `deprecated: bool = False` field to carry the flag through `model_dump()` to the API response.
- **Tool definition layer**: `src/codemie_tools/qa/zephyr_squad/tools_vars.py` — `ZEPHYR_SQUAD_TOOL` updated to set `deprecated=True`.
- **DB persistence layer**: no schema change required if the deprecated flag is computed from `credential_type` in the service layer (preferred path). A migration would only be needed if a persisted `deprecated` column is added.

### Integration Points

- `GET /v1/tools` → `ToolsInfoService.get_tools_info()` → `toolkit_provider.get_available_toolkits_info()` → `QualityAssuranceToolkitUI.get_definition().model_dump()` → serializes `Tool` objects including the new `deprecated` field.
- `POST /v1/settings/user` and `PUT /v1/settings/user/{setting_id}` → `user_settings.py` router → `SettingsService.create_setting()` / `update_settings()`.
- `POST /v1/settings/project` and `PUT /v1/settings/project/{setting_id}` → `project_settings.py` router → same service layer.
- Alembic migrations at `src/external/alembic/versions/` if a schema change is chosen.

### Patterns and Conventions

- **Per-credential-type guards in routers**: existing pattern in both `user_settings.py` and `project_settings.py` — `if request.credential_type == CredentialTypes.SCHEDULER:` dispatches to a named validator. New ZephyrSquad guard mirrors this with an `elif`.
- **`ExtendedHTTPException`** (not FastAPI's `HTTPException`) is the required error class; response shape is `{"error": {"message", "details", "help"}}`. HTTP 410 Gone is the semantically correct status for a removed/deprecated resource.
- **`CredentialTypes` enum deprecation**: prior art at line 95 uses underscore prefix `_ZEPHYR_CLOUD = "ZephyrCloud"  # Deprecated` — purely a code-level signal; this task needs an API-visible flag instead.
- **`lru_cache` on toolkit discovery**: adding `deprecated=True` to `ZEPHYR_SQUAD_TOOL` will be reflected in cached responses only after a process restart. This is existing behavior and is not a new risk.
- **Migration pattern for data changes**: `e03e516e00da_add_xray_credential_type.py` shows how to alter the `credentialtypes` PostgreSQL enum via `op.sync_enum_values(...)`. For a data migration, see `k5l6m7n8o9p0_deprecate_python_repl.py` (has a companion unit test in `tests/codemie/migrations/`).

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/api/rest-api-patterns.md` — routers in `src/codemie/rest_api/routers/`; errors via `ExtendedHTTPException`; router registration in `src/codemie/rest_api/main.py`.
- `.ai-run/guides/integration/external-services.md` — external service details stay behind toolkit adapters.
- `.ai-run/guides/data/database-patterns.md` — use Alembic for schema changes under `src/external/alembic/versions/`.
- `.ai-run/guides/testing/testing-patterns.md` — test location mirrors `src/`; mock external boundaries.
- `.ai-run/guides/testing/testing-api-patterns.md` — router tests via `AsyncClient`; include negative cases for validation.

### Architectural Decisions

- `_ZEPHYR_CLOUD` underscore-prefix convention: an existing implicit decision to mark deprecated credential types in code without altering the API surface. The current task must go further — the deprecated flag must be exposed to the UI.
- `ExtendedHTTPException` shape: recorded in project memory and confirmed in code. Deviation will break the UI/SDK error path.
- `lru_cache` on toolkit discovery: a recorded behavioral characteristic; process restart required to pick up changes to tool metadata.

### Derived Conventions

- New integration-level guards are added in the router layer (not in the service), as the existing guards for scheduler/litellm/git/webhook demonstrate.
- Error messages for blocked operations should follow the `ExtendedHTTPException(code=..., message=..., details=..., help=...)` signature used throughout the settings routers.
- Tool metadata fields added to `ToolMetadata` / `Tool` flow to the API automatically via `model_dump()` — no extra serialization step needed.

### External Documentation Findings

No third-party library or external API is central to this deprecation task. The ZephyrSquad client library is already present and is not being removed, only its registration is being restricted. This subsection is omitted.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/routers/test_user_settings.py` — covers `create_user_setting` and `update_user_setting`; uses `AsyncClient`, `ASGITransport`, `ExtendedHTTPException`, `CredentialTypes`. New tests for the ZephyrSquad block should be added here.
- `tests/codemie/rest_api/routers/test_project_settings.py` — covers `create_project_setting` and `update_project_setting`. Same extension point.
- `tests/codemie_tools/qa/test_qa_toolkit.py` — asserts `len(toolkit_ui.tools) == 5` (line 35) and `"ZephyrSquad" in tool_names` (line 40). Both assertions will need updating if ZephyrSquad is removed from the toolkit list or if the `deprecated` field causes a structural change.
- `tests/codemie_tools/qa/zephyr_squad/test_squad_generic_tool.py` — unit tests for `ZephyrSquadGenericTool`.
- `tests/codemie_tools/qa/zephyr_squad/test_api_wrapper.py` — unit tests for `ZephyrRestAPI`.
- `tests/codemie/service/settings/test_settings_tester.py` — has zephyr tests but only for `ZEPHYR_SCALE`.
- `tests/codemie/migrations/test_k5l6m7n8o9p0_deprecate_python_repl.py` — pattern to follow for any migration unit test.

### Testing Framework and Patterns

- **Framework**: pytest with `unittest.mock`.
- **DB mock**: session-scoped mock of `PostgresClient.get_engine` in `tests/conftest.py` (lines 34–49) — all router tests use this.
- **Router test fixture**: `tests/codemie/rest_api/routers/conftest.py` — disables rate limiter, injects `request_uuid` via autouse fixture.
- **HTTP client**: `AsyncClient` with `ASGITransport` (not a live server).
- **Error assertion pattern**: check `response.status_code` and `response.json()["error"]["message"]`.

### Coverage Gaps

- No existing tests for blocking ZephyrSquad on create/update (the guard does not exist yet).
- No existing tests for a `deprecated` field on `ToolMetadata` / `Tool` or on the `GET /v1/tools` response.
- `test_qa_toolkit.py` will conflict with the new behavior (asserts ZephyrSquad is present and tool count is 5 — may need adjustment depending on implementation choice).
- No migration unit test exists yet for a ZephyrSquad-specific data migration (if that path is chosen).

---

## 5. Configuration and Environment

### Environment Variables

No ZephyrSquad-specific environment variables were found. Integration credentials are stored in the `settings` table (`credential_values` column, encrypted) — not in env vars.

### Configuration Files

- `src/external/alembic/versions/` — Alembic migration directory. Any schema change requires a new file here following the `{revision}_{slug}.py` naming pattern.
- `src/codemie_tools/qa/zephyr_squad/tools_vars.py` — `ZEPHYR_SQUAD_TOOL` definition; only file that needs changing to mark the tool deprecated at the catalog level.
- `src/codemie_tools/base/models.py` — `ToolMetadata` and `Tool` models; requires `deprecated: bool = False` field addition.

### Feature Flags and Deployment Concerns

- No feature flags govern ZephyrSquad behavior currently.
- The `lru_cache` on `get_available_toolkits_info()` means that after deployment the change is visible immediately (no in-flight cache from a prior process). No special deployment concern.
- If a data migration is written, it must run before or with the deployment (standard Alembic auto-run on startup applies here per the project's deployment pattern).

---

## 6. Risk Indicators

- **`lru_cache` on toolkit discovery**: `toolkit_provider.get_available_toolkits_info()` and `get_available_tools_configs_info()` are cached for the lifetime of the process. The `deprecated=True` field will only be reflected after a cold start. This is existing behavior, not new risk, but it should be documented in the MR.
- **`test_qa_toolkit.py` asserts exact tool count (5) and `"ZephyrSquad" in tool_names`**: these tests will break if ZephyrSquad is removed from `QualityAssuranceToolkitUI.tools` list entirely. If the deprecation keeps ZephyrSquad in the toolkit list but adds `deprecated=True`, they may still pass — but the assertion on `tool_names` inclusion needs review.
- **No `deprecated` field on `ToolMetadata` or `Tool` currently**: this is a net-new field. Adding it is a backward-compatible addition to the `GET /v1/tools` response (new key in dict), but the UI team should be notified to consume it.
- **Dual router surface** (`user_settings.py` and `project_settings.py`): the block must be applied in both or a per-user integration could be created while per-project is blocked (or vice versa). Both must be patched together.
- **`_ZEPHYR_CLOUD` precedent**: the previous "deprecation" of ZephyrCloud used only a code-level underscore prefix — it did not block the API nor surface a flag. There is no established backend pattern for an API-visible deprecation flag; this task introduces one.
- **No migration unit test template for credential-type deprecation**: the existing migration test `test_k5l6m7n8o9p0_deprecate_python_repl.py` covers Python REPL deprecation; it is the closest pattern but addresses a different domain.
- **Existing ZephyrSquad configs in production**: if real rows exist in the `settings` table with `credential_type = 'ZEPHYR_SQUAD'`, the "read-only" behavior requires that `GET /v1/settings` still returns them (no hard delete). The blocking must be on `POST`/`PUT` only, not `GET`.
- **`CredentialTypes.ZEPHYR_SQUAD` enum value in PostgreSQL**: the enum value `'ZephyrSquad'` is persisted in the database. Removing it from the Python enum would break deserialization of existing rows. The enum entry must be retained (or the DB enum altered simultaneously). The deprecation approach (keep entry, add `deprecated` flag) avoids this risk.

---

## 7. Summary for Complexity Assessment

The ZephyrSquad deprecation touches four distinct backend layers: the tool-metadata model layer (`ToolMetadata`/`Tool` in `codemie_tools/base/models.py`), the tool-definition layer (`tools_vars.py`), the API router layer (two routers — `user_settings.py` and `project_settings.py`), and optionally the DB persistence layer if a migration is chosen. The "no migration" path is clearly viable: the `deprecated` flag is computable from `credential_type == CredentialTypes.ZEPHYR_SQUAD` and can be injected by adding `deprecated: bool = False` to `ToolMetadata`/`Tool` and setting it to `True` in `ZEPHYR_SQUAD_TOOL`. This avoids a schema change entirely and keeps the change surface to approximately 5–7 files: `models.py`, `tools_vars.py`, `user_settings.py`, `project_settings.py`, and corresponding test files.

The task does not follow a fully established pattern. ZephyrCloud was deprecated before using only a code-level underscore convention — no API flag, no block on create/update. This task introduces both, meaning there is no prior art to copy verbatim. However, the building blocks are all well-precedented individually: `ExtendedHTTPException` for blocking, `elif credential_type ==` guards in routers, `deprecated: bool = False` field addition to a Pydantic model. The implementation is straightforward mechanical work across known files rather than architectural novelty.

Test coverage posture is mixed. The router test files for user settings and project settings are well-established and the new negative-case tests (blocking ZephyrSquad on create/update) fit naturally into the existing test structure. The main risk is `test_qa_toolkit.py`, which hardcodes the tool count (5) and asserts ZephyrSquad is present by name — these will need adjustment. The broader ZephyrSquad tool tests (`test_squad_generic_tool.py`, `test_api_wrapper.py`) are not directly affected by the deprecation (the tool class is not removed, only registration is flagged). Overall complexity is low-to-medium: no new infrastructure, no architectural change, clear file-level targets, one mildly tricky test update.
