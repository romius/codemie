# Technical Research

**Task**: integrations teams assistants
**Generated**: 2026-08-27
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-14111: Move Teams bot configuration from current UI to Integrations. Currently Teams is enabled via a per-assistant project mapping (e.g. endpoint like assistants/{assistant_id}/projects/mapping). Need to introduce a new project-scoped integration type 'ms_teams' that holds the project and a list of enabled assistants, migrate existing mappings into it via an Alembic migration, and update assistant-listing logic (Teams-enabled assistants) to read from integrations instead of (or merged with) the old mapping. Also need to find and remove the old Teams bot configuration UI and understand how Integrations are currently modeled (integration types, project scoping, config schema, API endpoints, frontend components) so the new Teams integration type fits the existing pattern.

Task description: Move Teams bot configuration from project mapping to Integrations. Migrate existing Teams project-assistant mappings to a new 'ms_teams' integration type via Alembic migration; the integration is project-scoped and holds project + list of assistants; when listing Teams-enabled assistants, read from integrations (not the old mapping) and merge; support configuring Teams integration per project with selection of an enabled assistant (only enabled assistants selectable); remove old Teams bot configuration UI, expose Teams as new integration type under Integrations; no regression to existing Integrations behavior. Jira ticket EPMCDME-14111.

---

## 2. Codebase Findings

### Existing Implementations

**Current Teams mechanism — `assistant_project_mapping` (the thing to be replaced/migrated):**
- `src/codemie/rest_api/routers/assistant_project_mapping.py` — router, prefix `/v1`, tag "Assistant Project Mappings". Three endpoints:
  - `GET /v1/assistants/projects/mapping?feature=&project=&page=&per_page=` — `list_project_assistants`, requires project membership.
  - `POST /v1/assistants/{assistant_id}/projects/mapping` — `enable_assistant_for_project`, requires project admin.
  - `DELETE /v1/assistants/{assistant_id}/projects/mapping?project=&feature=` — `disable_assistant_for_project`, requires project admin.
  - All three are gated by `_require_teams_bot_feature()` which checks `customer_config.is_feature_enabled("features:teamsBotIntegration")` and 403s with message "Teams Bot Integration is not enabled for this customer" if disabled. This is the *only* feature (`AssistantProjectFeature.TEAMS = "teams"`) currently modeled; the mapping mechanism is written generically ("project feature mapping") but only Teams uses it today.
- `src/codemie/service/assistant/assistant_project_mapping_service.py` — `AssistantProjectMappingService`: `list()` (validates project access, fetches assistant_ids from repo, then queries `AssistantRepository().query(user, scope=AssistantScope.ALL, filters={"id": assistant_ids}, page, per_page)`), `enable()` (project-admin check, project-existence check, idempotent create swallowing `IntegrityError` on race), `disable()` (project-admin check, raises `AssistantProjectMappingNotFound` if absent). Raises `AssistantProjectMappingNotFound` / `AssistantProjectMappingForbidden` (both plain `Exception` subclasses, not `ExtendedHTTPException`).
- `src/codemie/repository/assistants/assistant_project_mapping_repository.py` — `AssistantProjectMappingRepository` (ABC) / `SQLAssistantProjectMappingRepository` (`AssistantProjectMappingRepositoryImpl` alias). Methods: `create`, `delete`, `get_assistant_ids(project_name, feature)` (joins `assistant_project_mapping` to `applications` on name, filters `Application.deleted_at.is_(None)`), `exists`.
- `src/codemie/rest_api/models/usage/assistant_project_mapping.py` — `AssistantProjectFeature(str, Enum)` with single member `TEAMS = "teams"`; `AssistantProjectMappingSQL(SQLModel, table=True)` (`__tablename__ = "assistant_project_mapping"`): `id` (uuid pk), `assistant_id` (FK `assistants.id`, `ondelete="CASCADE"`), `project_name` (indexed string, **not a FK** — joined by name), `feature` (plain string), `created_at`, `updated_by`; unique constraint `uix_assistant_project_mapping` on `(assistant_id, project_name, feature)`. `AssistantProjectMappingRequest(CommonBaseModel)`: `project_name`, `feature: AssistantProjectFeature`.
- Migration: `src/external/alembic/versions/7ca305066800_create_assistant_project_mapping.py` (`down_revision = "p1q2r3s4t5u6"`) creates the table with FK to `assistants`, unique constraint, and a non-unique index on `project_name`.
- Registration: `src/codemie/rest_api/main.py` — `app.include_router(assistant_project_mapping.router)` (line 830), alongside `app.include_router(assistant_mapping.router)` (line 829, a *different* router for per-user assistant mappings — do not confuse the two).

**Existing "Integrations" domain (what the ticket calls Integrations — there is no `Integration` entity/table):**
- What the UI/ticket call an "integration" is a row in the `settings` table, model `Settings` (`src/codemie/rest_api/models/settings.py:364`, `__tablename__ = "settings"`), built from `SettingsBase(CommonBaseModel)` (same file, ~line 243). Two scopes via `SettingType` enum (`USER`, `PROJECT`); project scoping is by **plain string** `project_name` (no FK to `applications`).
- Discriminator field is `credential_type: CredentialTypes` (`src/codemie_tools/base/models.py:67`), a large `str, Enum` of ~30 members (`JIRA`, `CONFLUENCE`, `GIT`, `AWS`, `SCHEDULER`, `WEBHOOK`, `EMAIL`, `TELEGRAM`, `A2A`, `LITE_LLM`, `SHAREPOINT`, `DIAL`, etc.) — **no Teams/MS Teams member exists yet**. Adding one requires an Alembic migration if the enum backs a Postgres ENUM type; confirm by checking how `DIAL` (the most recently added "Project settings" entry, same file, line 109) was migrated in.
- Config payload shape is generic key/value: `credential_values: List[CredentialValues]` where `CredentialValues(BaseModel)` = `{key: str, value: Any}` (`settings.py:39`), stored as JSONB via `PydanticListType`. There is no first-class "list of assistant ids" field on `Settings` — any such data would need to be encoded inside `credential_values` (e.g. a `CredentialValues(key="assistant_ids", value=[...])` entry) unless a new dedicated column/table is added.
- API surface: `src/codemie/rest_api/routers/project_settings.py` (prefix `/v1`, tag "Project Settings"), registered in `main.py`. Endpoints: `GET /v1/settings/project/users`, `GET /v1/settings/project` (list/filter via `SettingsIndexService.run`), `POST /v1/settings/project` (`create_project_setting` — per-`credential_type` validation branches for `SCHEDULER`/`LITE_LLM`/`GIT`/`WEBHOOK`, then `_check_permission` then `SettingsService.create_setting`), `PUT /v1/settings/project/{setting_id}`, `DELETE /v1/settings/project/{setting_id}` (calls `Settings.delete_setting` **directly**, bypassing `SettingsService.delete_setting` — skips OAuth revoke / cache invalidation for project-scoped deletes; a known inconsistency, not something to replicate).
- Service: `src/codemie/service/settings/settings.py` — `SettingsService.create_setting` / `update_settings` / `delete_setting`, alias-uniqueness enforced only in app code (`SettingsBase.check_alias_unique`, keyed on `(project_name, alias)` for PROJECT settings — `credential_type` is **not** part of the uniqueness key, so a project could in principle hold more than one integration of the same type under different aliases).
- Permission model: `_check_permission(user, project_name)` in `project_settings.py` (admin/maintainer bypass, else `user.is_application_admin(project_name)` required) — this is the "manage integrations for project X" check to mirror for the new Teams integration type.
- A full prior research pass on this exact domain exists at `docs/superpowers/tasks/2026-07-23-epmcdme-13666-move-copy-project-integrations/technical-analysis.md` (EPMCDME-13666, "Move/Copy Project Integrations") — it documents the `Settings` model, `CredentialTypes`, encryption/masking, permission matrix, resolution-handler chain, and per-credential-type quirks (WEBHOOK global uniqueness, LITE_LLM alias-embeds-project-name, GOOGLE_OAUTH revoke-on-delete, AWS Bedrock cascade-on-delete) in much more depth than is repeated here; it is a directly relevant sibling analysis for this ticket's domain.

**Assistant listing / scope:**
- `src/codemie/service/assistant/assistant_repository.py` — `AssistantRepository`, `AssistantRepository().query(user, scope=AssistantScope.ALL, filters, page, per_page)` used by `AssistantProjectMappingService.list()` to resolve assistant ids into full assistant objects.
- `src/codemie/rest_api/routers/assistant.py` — `_get_assistant_by_id_or_raise` used by the mapping router to validate `assistant_id` exists before enabling.
- No existing concept of "enabled assistants" as a list attribute on any integration/settings row; the closest existing multi-valued attribute pattern is `credential_values: List[CredentialValues]`.

### Architecture and Layers Affected

- **API/router layer**: `assistant_project_mapping.py` (to be removed or left for back-compat) and `project_settings.py` (new/extended endpoint(s) for the `ms_teams` integration type). Router registration in `src/codemie/rest_api/main.py`.
- **Service layer**: `AssistantProjectMappingService` (existing, Teams-specific today) and `SettingsService` (generic integrations service). The listing logic that currently calls `AssistantProjectMappingService.list()` needs to be updated to read from `Settings`/integrations (merged or replaced).
- **Repository layer**: `AssistantProjectMappingRepository` (SQL, direct `Session`/`select`) vs. `Settings` (active-record `SQLModel` classmethods, no separate repository class — `get_by_fields`, `get_by_project_names`, etc., inherited from `BaseModelWithSQLSupport`).
- **Data/migration layer**: `src/external/alembic/versions/` — a new Alembic migration is needed both for the data migration (existing `assistant_project_mapping` rows → new `ms_teams` settings rows) and, if `CredentialTypes` is a Postgres ENUM type, for adding the new enum value.
- **Config layer**: `config/customer/customer-config.yaml` feature flag `features:teamsBotIntegration` currently gates the old endpoints; its future (remove, repurpose, or keep gating the new integration type) is a design decision.

### Integration Points

- `assistant_project_mapping` table has a `ondelete="CASCADE"` FK to `assistants.id`; `Settings.project_name` has no FK to `applications` at all — any migrated data loses the assistant-cascade unless the new representation adds its own reference/validation.
- `AssistantRepository` is the shared read path for turning assistant ids into assistant DTOs for listing — reusable regardless of where the ids are read from (mapping table or settings row).
- `Application`/project existence check pattern: `assistant_project_mapping_service.py:_validate_project_exists` inlines a `Session`/`application_repository.get_by_name` lookup; `SettingsService.create_setting` instead calls `ensure_application_exists` (`src/codemie/rest_api/utils/default_applications.py`) which **auto-creates** a missing project — a documented trap in the sibling EPMCDME-13666 analysis, relevant if the new Teams integration creation path reuses `create_setting`.
- `customer_config.is_feature_enabled("features:teamsBotIntegration")` (`src/codemie/configs/customer_config.py:256`) is the sole feature-flag integration point for the current Teams UI/API.

### Patterns and Conventions

- New credential/integration types are added by extending the `CredentialTypes` enum in `src/codemie_tools/base/models.py` and, per credential type, adding an optional validation branch in `project_settings.py`'s create/update handlers (see `SCHEDULER`/`LITE_LLM`/`GIT`/`WEBHOOK` branches) plus any special-case handling in `SettingsService`.
- Errors follow the `ExtendedHTTPException(code, message, details, help)` convention (`src/codemie/core/exceptions.py`), used directly by both the settings router and (implicitly, via router-level try/except) the assistant-project-mapping router; `AssistantProjectMappingNotFound`/`Forbidden` are plain exceptions translated to HTTP codes at the router boundary via `raise_not_found`/`raise_forbidden` helpers in `src/codemie/rest_api/routers/utils.py`.
- Migrations follow the naming convention `<12-char-hex-revision>_<description>.py` with explicit `down_revision` chaining; there are currently multiple apparent alembic heads in `src/external/alembic/versions/` (154 files total) — the correct `down_revision` for a new migration must be determined from the actual current head, not assumed.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/` exists and is the project's primary AI guidance source (see AGENTS.md guide table). Relevant guides for this task: `.ai-run/guides/data/database-patterns.md` (SQLModel/session patterns), `.ai-run/guides/api/rest-api-patterns.md` and `endpoint-conventions.md` (router conventions), `.ai-run/guides/architecture/layered-architecture.md` and `service-layer-patterns.md`. No dedicated guide file specifically for "Integrations"/`Settings`/`CredentialTypes` or for Teams was found under `.ai-run/guides/integration/` (that directory covers Confluence, Jira, X-ray, Google Docs, MCP, cloud, LLM providers, external services, request-hedging — no `teams.md` or `settings.md`/`integrations.md`).
- No inline `NOTE:`/`HACK:`/`ADR:`/`DECISION:` markers were found in the directly relevant source files (`assistant_project_mapping_service.py`, `assistant_project_mapping.py` router, `settings.py` model).

### Architectural Decisions

- No formal ADR file found for either the `assistant_project_mapping` mechanism or the `Settings`/integrations model. The most substantial recorded architectural context is the sibling technical-analysis document from EPMCDME-13666 (`docs/superpowers/tasks/2026-07-23-epmcdme-13666-move-copy-project-integrations/technical-analysis.md`), which is itself research output, not a committed ADR, but documents in detail: no DB unique constraint on `(project_name, alias)` (app-level check only, race-prone); `credential_type` not part of the uniqueness key; encryption is per-credential-type via `ENCRYPTION_TYPE` global config, not per-project; and a documented, already-existing bug where project rename does not update `settings.project_name` (dangling project references already occur in production behavior).

### Derived Conventions

- Feature-gating: boolean and typed flags are declared under `- id: "features:<name>"` blocks in `config/customer/customer-config.yaml` with `settings.enabled` plus `name`/`description`; checked in code via `customer_config.is_feature_enabled("features:<name>")`.
- Router modules are one-file-per-domain under `src/codemie/rest_api/routers/`, each with its own `APIRouter(tags=[...], prefix="/v1")`, registered centrally in `src/codemie/rest_api/main.py`.
- New enum members on shared enums (`CredentialTypes`, `AssistantProjectFeature`) are string-valued and typically PascalCase-ish/human labels (e.g. `"AzureDevOps"`, `"GoogleOAuth"`, `"teams"`), not raw slugs, though the ticket's stated new-type key `ms_teams` diverges from that convention (existing values are `PascalCase`/`CamelCase`, not `snake_case`) — worth flagging for the design/spec stage.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/assistant/test_assistant_project_mapping_service.py` — covers `AssistantProjectMappingService.enable/disable/list`, permission checks (`project_admin_user`/`regular_user` fixtures via `MagicMock`), idempotent-enable-on-`IntegrityError`, not-found on disable.
- `tests/codemie/repository/assistants/test_assistant_project_mapping_repository.py` — repository-level tests for the SQL implementation.
- No test file exists yet for the `assistant_project_mapping` router itself (`test_assistant_project_mapping.py` under `tests/codemie/rest_api/routers/` was not found) — router-level (HTTP) coverage for enable/disable/list appears to be a gap even for the *current* implementation.
- `tests/codemie/rest_api/routers/test_project_settings.py` — covers the generic project-settings router (`index_project_settings`, `get_project_settings_users`, etc.) using the house `FastAPI()` + `ASGITransport` + `anyio_backend` fixture pattern; does not cover any specific `CredentialTypes` value's validation branch in depth.
- `tests/integration/` exists but is empty (per sibling EPMCDME-13666 analysis); `*_integration.py` in this codebase means "test of an integration *feature*", not "integration-level test" — a naming trap for a ticket literally about "Integrations".

### Testing Framework and Patterns

- pytest with `pytest.ini` (`testpaths = tests`, `pythonpath = src`, `--import-mode=importlib`), `pytest-asyncio` in **strict** mode (no global `asyncio_mode`), `@pytest.mark.anyio` requires a module-local `anyio_backend` fixture.
- `tests/conftest.py` patches `PostgresClient.get_engine` with a `MagicMock` session-wide — there is no real test DB; all repository/service tests mock at the session or classmethod level.
- Service tests: plain `unittest.mock` (`patch`/`patch.object`/`MagicMock`), `# Arrange`/`# Act`/`# Assert` blocks, `MagicMock(spec=...)` for repository fixtures (as seen in `test_assistant_project_mapping_service.py`).
- Router tests: bare `FastAPI()` + `app.include_router(router)`, `AsyncClient(transport=ASGITransport(app=app))`, `@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")` always the innermost/bottom decorator, assertions against `status.HTTP_*` constants and exact `response.json()`.

### Coverage Gaps

- No HTTP-level tests for `assistant_project_mapping.py` router (enable/disable/list endpoints), so any refactor there is currently unguarded except by the service/repository unit tests.
- No existing tests reference `CredentialTypes` additions or migration of arbitrary Settings rows between representations — a data-migration Alembic script of the kind this ticket needs has no precedent test pattern in this repo to copy from directly (migrations are not unit-tested here; verify by checking for an `alembic` test directory, none was found under `tests/`).
- No test currently exercises "merge" logic between two data sources for the same listing (old mapping table + new integration) since that logic does not exist yet — greenfield for this ticket.

---

## 5. Configuration and Environment

### Environment Variables

- No feature-specific environment variables were found for Teams or for the generic Settings/Integrations domain; behavior is controlled via the `customer-config.yaml` feature-flag file plus the `ENCRYPTION_TYPE` global config (documented in the sibling EPMCDME-13666 analysis) that governs how `credential_values` are encrypted, unrelated to Teams specifically.

### Configuration Files

- `config/customer/customer-config.yaml` — governs feature flags; contains the `features:teamsBotIntegration` block (id, `settings.enabled: true`, `name: "Teams Bot Integration"`, `description: "Enable project-level assistant configuration for Microsoft Teams Bot integration."`). Also currently modified/uncommitted per git status at session start (`M config/customer/customer-config.yaml`) — verify with the user whether that diff is pre-existing local work unrelated to this ticket or something to be aware of before editing the same file.

### Feature Flags and Deployment Concerns

- `features:teamsBotIntegration` is the only flag gating the current Teams mapping endpoints; a design decision is needed on whether the new `ms_teams` integration type reuses this flag, a new flag, or none (integrations are otherwise ungated by feature flags in `project_settings.py` except for the `LITE_LLM` admin-only rule).
- No Dockerfile/CI/CD reference to "teams" or "ms_teams" was found; this appears to be purely an application-level (API + data model) change with no deployment-manifest impact evident from the filesystem.

---

## 6. Risk Indicators

- The `assistant_project_mapping` row has a hard `ondelete="CASCADE"` FK from `assistant_id` to `assistants.id`; the `Settings`/integrations model has no FK integrity to either `assistants` or `applications` at all (`project_name` is a plain string). Speculative: migrating "list of enabled assistants" into a `Settings.credential_values` JSON blob will need explicit application-level validation to replace the FK-cascade guarantee the current table provides (e.g. on assistant deletion).
- `CredentialTypes` is described elsewhere in this codebase as backing a Postgres ENUM (per the sibling EPMCDME-13666 analysis: "a Postgres ENUM `credentialtypes`; adding a value requires an alembic migration") — confirm this before assuming a simple Python-enum-only change; adding `ms_teams`/`Teams` as a member may itself require a migration independent of the data-migration one.
- `(project_name, alias)` uniqueness for PROJECT settings is enforced only in application code (`SettingsBase.check_alias_unique`), read-then-write and race-prone, and `credential_type` is not part of that key — a project could accumulate multiple `ms_teams` rows unless the new type's creation path adds its own singleton-per-project enforcement.
- The current Teams feature is gated behind `features:teamsBotIntegration`; no equivalent gating convention exists elsewhere in `project_settings.py` (only `LITE_LLM` has a special admin-only rule) — carrying the flag forward, dropping it, or introducing a new one is an open design question with behavioral consequences for existing customers who have it enabled/disabled today.
- No router-level (HTTP) test exists today for the endpoints in `assistant_project_mapping.py`, so a router refactor/removal has only service+repository unit-test coverage as a safety net, not an end-to-end HTTP regression check.
- The ticket's proposed integration-type key `ms_teams` (snake_case) is inconsistent with every existing `CredentialTypes` member's naming style (`PascalCase`/`CamelCase`, e.g. `"AzureDevOps"`, `"GoogleOAuth"`); worth resolving explicitly rather than by convention-copy, since assistant-listing/merge logic will likely compare on this literal value.
- "Frontend components" and "old Teams bot configuration UI" named in the task were not found anywhere in this repository — this backend repo (`codemie`) contains only the FastAPI backend, Alembic migrations, and Python tool packages (`codemie`, `codemie_tools`, `external`); no `frontend`/`ui`/`web` directory, `package.json`, or JS/TS source was found at the repository root or under `src/`. The UI-removal portion of this ticket is very likely out of scope for this repository and lives in a separate frontend repository not present on this filesystem.
- A directly relevant, much deeper prior research pass on the exact same "Integrations"/`Settings` domain already exists in-repo at `docs/superpowers/tasks/2026-07-23-epmcdme-13666-move-copy-project-integrations/technical-analysis.md` (18 enumerated risks on the Settings model itself, e.g. WEBHOOK global-uniqueness, LITE_LLM alias-embeds-project-name, AWS Bedrock cascade-on-delete, no-atomicity precedents) — the design/plan stage for this ticket should read that document directly rather than rediscover its findings, since several of its risks (alias uniqueness, encryption/masking on copy, cache invalidation) will recur for any new `CredentialTypes` member.

---

## 7. Summary for Complexity Assessment

This task touches four layers: the API/router layer (`assistant_project_mapping.py` to retire/adjust, `project_settings.py` to extend for the new `ms_teams` type), the service layer (`AssistantProjectMappingService` for the old mechanism, `SettingsService`/`SettingsIndexService` for the new one, plus whatever merge logic sits above both for "Teams-enabled assistants" listing), the data/repository layer (`AssistantProjectMappingSQL`/`AssistantProjectMappingRepository` vs. the active-record `Settings` model with no separate repository class), and the Alembic migration layer (both a possible enum-value migration for `CredentialTypes` and a data-migration script moving existing `assistant_project_mapping` rows into `Settings` rows). The change surface in this repository is moderate: a handful of existing files to modify or retire (2 routers, 2 services, 1 repository, 2 models, 1 migration) plus new files (new migration(s), extended `CredentialTypes` enum, new validation/merge logic, new tests).

Technical novelty is significant on two fronts: (1) the existing "Integrations" domain (`Settings`/`CredentialTypes`) is a generic key-value credential store with no first-class support for "project + list of assistant ids" as a payload shape, so representing that cleanly (inside `credential_values` JSON vs. a new column/table) is a genuine design decision, not a copy-paste of an existing pattern; and (2) the FK-backed cascade-delete guarantee of the current `assistant_project_mapping` table has no equivalent in the string-keyed, non-FK `Settings` model, so referential-integrity behavior on assistant/project deletion will change and needs explicit handling. Test coverage posture is mixed: solid unit coverage exists for the current service/repository, but zero HTTP-level coverage exists for the router being retired, and zero precedent exists in this repo for the "merge two data sources for one listing" pattern this ticket introduces.

Key risk factors, in order of likely impact: possible Postgres-ENUM migration requirement for adding a `CredentialTypes` member (in addition to the data migration itself); the naming-convention mismatch between the ticket's `ms_teams` key and every existing enum member; app-level-only, race-prone alias uniqueness with `credential_type` excluded from the uniqueness key (risk of duplicate per-project Teams integrations); the unresolved fate of the `features:teamsBotIntegration` flag; and the near-certainty that "remove the old Teams bot configuration UI" is entirely out of scope for this backend-only repository and belongs to a separate, not-present frontend codebase — this should be flagged back to the requester rather than assumed away.

---

## 8. External References

None named by the task. The task_context and task_description reference Jira ticket EPMCDME-14111 by number only, with no file path, directory, or URL given as a source of truth to read. No external path was expanded or read for this reason.
