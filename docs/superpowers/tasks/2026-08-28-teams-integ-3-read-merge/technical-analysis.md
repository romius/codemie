# Technical Research

**Task**: teams integrations assistant-mapping settings
**Generated**: 2026-08-27T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Story 3 of EPMCDME-14111 (Move Teams bot configuration from project mapping to Integrations) — Read/merge path. Update assistant-listing logic to read from (or merge with) the new ms_teams Settings integration, and extend project_settings.py create/update/list endpoints to support the ms_teams credential type. Context: Story 1 (Foundation) already added the ms_teams CredentialTypes member, the settings.py model's check_ms_teams_singleton, and related plumbing. Story 2 (Data migration) already wrote and landed the Alembic migration moving assistant_project_mapping rows into ms_teams Settings rows (one row per project, assistant_ids array), and dropped the assistant_project_mapping table/FK/index. This story (3) must now make the assistant-listing logic actually read from (or merge with) the new ms_teams Settings integration instead of/alongside the old assistant_project_mapping table (which no longer exists after Story 2's migration — so any remaining code that queries assistant_project_mapping directly needs updating here), and extend project_settings.py's create/update/list endpoints so ms_teams settings rows can be created/updated/listed like other integration credential types, respecting the singleton-per-project constraint and the assistant_ids array field. Per the original split recommendation, this is scoped narrowly to the read/merge/API-extension work — NOT the removal of the old assistant_project_mapping router/service (that is Story 4).

---

## 2. Codebase Findings

### Existing Implementations

**Already landed by Stories 1 & 2 (this branch, commits `a8ff186a0`…`33745d8ab`) — no further work needed:**
- `src/codemie_tools/base/models.py:110` — `CredentialTypes.MS_TEAMS = "ms_teams"` enum member.
- `src/codemie/rest_api/models/settings.py:317-324` — `Settings.check_ms_teams_singleton(project_name, setting_id=None)` classmethod, queries `Settings` by `project_name.keyword` + `credential_type.keyword == "ms_teams"`.
- `src/external/alembic/versions/8b2c1a4d5e6f_add_ms_teams_credential_type.py`, `f1a2b3c4d5e6_add_ms_teams_singleton_unique_index.py` — DB-level singleton unique index (source of the `IntegrityError` the router already catches).
- `src/codemie/service/settings/settings_request_validator.py:269-346` — `validate_ms_teams_request(request, setting_type, setting_id=None)`: enforces PROJECT-only scope, requires `project_name`, requires exactly one `assistant_ids` credential value (non-empty list, unique strings, each id validated via `AssistantService.belongs_to_project`), and calls `Settings.check_ms_teams_singleton`.
- `src/codemie/rest_api/routers/project_settings.py:83-207` — `create_project_setting` and `update_project_setting` already branch on `CredentialTypes.MS_TEAMS` to call `validate_ms_teams_request`, and both already catch `IntegrityError` to return HTTP 409 with an ms_teams-specific message.
- `src/codemie/rest_api/routers/project_settings.py:62-80` — `index_project_settings` (`GET /v1/settings/project`) is generic over `SettingType.PROJECT` and any `credential_type` filter; no ms_teams-specific branching exists or is needed — `ms_teams` rows already list/filter through the existing `SettingsFilter`/`credential_type.keyword` filter path (`src/codemie/service/filter/filter_services.py:104`).
- `src/codemie/service/settings/settings.py` (`SettingsService.create_setting`/`update_settings`) — generic, credential-type-agnostic persistence; no ms_teams special-casing required since `assistant_ids` is just another `CredentialValues` entry.
- `tests/codemie/rest_api/routers/test_project_settings.py:197-308` — existing tests for ms_teams create/update (success, invalid assistant_ids, permission-before-validation ordering, update revalidation).

**Not yet updated — the actual gap for this story:**
- `src/codemie/rest_api/models/usage/assistant_project_mapping.py` — still defines `AssistantProjectMappingSQL` (`__tablename__ = "assistant_project_mapping"`) and `AssistantProjectFeature.TEAMS = "teams"` / `AssistantProjectMappingRequest`. The table itself was dropped by the Story 2 migration (`c3d4e5f6a7b8_migrate_assistant_project_mapping_to_settings.py:92`, `op.drop_table("assistant_project_mapping")`), so this SQLModel now maps to a non-existent table.
- `src/codemie/repository/assistants/assistant_project_mapping_repository.py` — `SQLAssistantProjectMappingRepository.get_assistant_ids(project_name, feature)` (lines 74-87) runs `select(AssistantProjectMappingSQL.assistant_id).join(Application, ...)` against the dropped table. `create`, `delete`, `exists` similarly target the dropped table.
- `src/codemie/service/assistant/assistant_project_mapping_service.py` — `AssistantProjectMappingService.list()` (lines 57-87) calls `self.repository.get_assistant_ids(project_name, feature)` — this is the method the task calls out as needing to read from ms_teams Settings instead. `enable()`/`disable()` (lines 89-116) call `repository.create`/`repository.exists`/`repository.delete` — also target the dropped table, but are not named in the task's scope (see Risk Indicators).
- `src/codemie/rest_api/routers/assistant_project_mapping.py` — `GET /v1/assistants/projects/mapping` (`list_project_assistants`), `POST` and `DELETE /v1/assistants/{assistant_id}/projects/mapping` — the router itself is explicitly **not** to be removed/renamed this story (that's Story 4); only the underlying read path the GET route depends on needs to change.

### Architecture and Layers Affected

- **Router layer**: `assistant_project_mapping.py` stays in place (per scope); `project_settings.py` already extended, no further router change identified as required.
- **Service layer**: `AssistantProjectMappingService.list()` — the read path to be rewired from the SQL repository to the `Settings`/ms_teams source of truth.
- **Repository layer**: `SQLAssistantProjectMappingRepository` — either the `get_assistant_ids` method is replaced with logic that queries `Settings` (via `Settings.get_by_fields`/`get_by_project_names`/direct `Session` query on `credential_type == MS_TEAMS`), or a new repository/adapter is introduced. `create`/`delete`/`exists` are unaffected by this story's stated scope but query the same dropped table.
- **Data model layer**: `Settings`/`SettingsBase` (`src/codemie/rest_api/models/settings.py`) is the new source of truth; `credential_values` holds a single `CredentialValues(key="assistant_ids", value=[...])` entry per project (per the Story 2 migration's insert shape, `c3d4e5f6a7b8_migrate_assistant_project_mapping_to_settings.py:56-90`).

### Integration Points

- `AssistantProjectMappingService.list()` currently calls `AssistantRepository().query(user=user, scope=AssistantScope.ALL, filters={"id": assistant_ids}, page=..., per_page=...)` after resolving `assistant_ids` — this downstream call is unaffected by the read-path change, only the *source* of `assistant_ids` changes.
- `Settings.normalize_values()` / `Settings.credential(key)` (`settings.py:269-294`) are the existing helpers for turning a `Settings` row's `credential_values` list into a `{key: value}` dict or fetching one key — `credential("assistant_ids")` would return the list directly.
- `Settings.get_by_fields({PROJECT_NAME_TERM: project_name, "credential_type.keyword": CredentialTypes.MS_TEAMS.value})` is the same query shape `check_ms_teams_singleton` already uses to find the per-project ms_teams row — the natural pattern to reuse for the read path.
- `customer_config.is_feature_enabled("teamsBotIntegration")` (`assistant_project_mapping.py:42-49`) still gates the old mapping router only; per Story 1's frontend-handoff doc this flag is intentionally kept and will be reused for the new ms_teams UI later — this story does not touch that gating.

### Patterns and Conventions

- Settings row lookups project-wide use `Settings.get_by_fields(...)` (single-result) or `Settings.get_by_project_names([...], credential_type=...)` (multi-result) — both classmethods on `Settings`/`BaseModelWithSQLSupport`.
- Credential-type-specific validation/business logic is centralized in `settings_request_validator.py` as free functions (`validate_git_request`, `validate_ms_teams_request`, etc.), dispatched by `if request.credential_type == CredentialTypes.X` chains in the router — the established pattern for any new ms_teams-specific behavior in `project_settings.py`.
- Repository classes in this codebase follow an ABC + `Impl` pattern (`AssistantProjectMappingRepository`/`AssistantProjectMappingRepositoryImpl = SQLAssistantProjectMappingRepository`), constructor-injected into the service (`AssistantProjectMappingService.__init__(self, repository=None)`) — tests rely on `MagicMock(spec=AssistantProjectMappingRepository)` injection, so any interface change to the repository's `get_assistant_ids` contract must preserve (or deliberately update) that seam.

---

## 3. Documentation Findings

### Guides and Architecture Docs

No `.ai-run/guides/` file mentions `ms_teams` or Teams integration by name (checked via grep across `.ai-run/guides/`). The closest relevant guides for this task's layers are `.ai-run/guides/data/database-patterns.md` (SQLModel/session patterns) and `.ai-run/guides/api/rest-api-patterns.md` (FastAPI router conventions) — neither was read in full here since no ms_teams-specific guidance exists in them; conventions were derived from the code itself (Section 2).

### Architectural Decisions

- `docs/superpowers/tasks/2026-08-27-teams-integ-1-foundation/frontend-handoff.md` (this repo, prior story's planning artifact) states explicitly: *"a later story switches the listing to read from `ms_teams` integrations only (old mapping is fully retired, not merged)"* and *"No merge of 'Teams-enabled assistants' between old mapping and `ms_teams` is implemented."* This directly resolves the task's "read from (or merge with)" phrasing in favor of a **full switch**, not a merge — consistent with Story 2 having already migrated all data out of `assistant_project_mapping` and dropped the table.
- `docs/superpowers/tasks/2026-08-27-teams-integ-2-migration/spec.md:71,86-87` — confirms router/service/repository removal is explicitly Story 4's responsibility, and flags that until Story 4 lands, the old router/service/repository code "will query a table this migration drops" — i.e. the current codebase state (post Story 2, pre Story 3) has a known-broken read path that this story must fix.
- `docs/superpowers/tasks/2026-08-27-teams-integ-1-foundation/plan.md:19` — Story 1 explicitly deferred "No change to assistant-listing/merge logic" to a later story, which is this one.

### Derived Conventions

- Feature-scoped validation/permission checks are raised as `ExtendedHTTPException` at the router layer (see `_check_permission`, `raise_access_denied`, `raise_forbidden`, `raise_not_found` helpers) but as plain exceptions (`AssistantProjectMappingNotFound`, `AssistantProjectMappingForbidden`, `ValueError`) at the service/model layer, translated by the router. Any new ms_teams-backed `list()` implementation should keep exceptions at the service layer and let the router translate them, matching `assistant_project_mapping.py`'s existing `try/except AssistantProjectMappingNotFound/Forbidden` blocks.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/assistant/test_assistant_project_mapping_service.py` — covers `enable`/`disable`/`list` against a **mocked** `AssistantProjectMappingRepository` (`MagicMock(spec=...)`), so these tests pass regardless of whether the real repository's underlying table exists; `test_list_returns_empty_when_no_mappings` and `test_list_delegates_to_assistant_repository` assert `mock_repo.get_assistant_ids.assert_called_once_with("proj-x", "teams")` — this assertion is coupled to the current repository interface and will need to change (or be replaced) if `get_assistant_ids`'s implementation/contract changes.
- `tests/codemie/repository/assistants/test_assistant_project_mapping_repository.py` — unit-tests `SQLAssistantProjectMappingRepository` against a fully mocked `Session`/`select`, so these also do not exercise the real (dropped) table; they will need updating if the repository's query logic for `get_assistant_ids` moves to query `Settings` instead of `AssistantProjectMappingSQL`.
- `tests/codemie/rest_api/routers/test_project_settings.py:197-308` — ms_teams create/update coverage already exists (success, validation failure, permission ordering, update revalidation) — no equivalent test exists yet for `GET /v1/settings/project` listing an ms_teams row specifically, nor for the `GET /v1/assistants/projects/mapping` endpoint reading from the new source.
- No existing test file exercises `GET /v1/assistants/projects/mapping` end-to-end against a real or fake `Settings` row; there is no router-level test file for `assistant_project_mapping.py` found under `tests/` (only service- and repository-level tests exist).

### Testing Framework and Patterns

- `pytest` with `pytest.mark.anyio` for async FastAPI route tests (`AsyncClient`/`ASGITransport` against the real `app`), `unittest.mock.patch`/`MagicMock` for service/repository isolation — consistent across `test_project_settings.py`, `test_assistant_project_mapping_service.py`, and `test_assistant_project_mapping_repository.py`.
- Router tests patch the specific service-layer function used by the route (e.g. `patch("codemie.rest_api.routers.project_settings.validate_ms_teams_request")`) rather than exercising real DB queries.

### Coverage Gaps

- No test currently proves the "read from ms_teams Settings" behavior this story implements — both existing service and repository tests are fully mocked against the *old* interface, so a naive implementation change could pass those tests while still being wrong (or the tests could simply go stale/be deleted with no replacement).
- No test exists for `GET /v1/assistants/projects/mapping` when a project has zero, one, or multiple ms_teams rows (the Story 2 migration's singleton-skip logic means a project could theoretically end up with more than one pre-existing `ms_teams` settings row from before the DB unique index existed — see Risk Indicators).

---

## 5. Configuration and Environment

### Environment Variables

None found specific to ms_teams/Teams integration; generic Settings encryption/config env vars are out of scope for this story.

### Configuration Files

- `config/customer/customer-config.yaml:244` — `features:teamsBotIntegration` feature flag entry, currently gating only the old `assistant_project_mapping.py` router endpoints via `customer_config.is_feature_enabled("teamsBotIntegration")` (`assistant_project_mapping.py:38-49`).

### Feature Flags and Deployment Concerns

- Per the Story 1 frontend-handoff doc, `features:teamsBotIntegration` is intentionally kept and will be reused for the new ms_teams UI rollout, not removed or repurposed by this story.

---

## 6. Risk Indicators

- The current codebase state (post Story 2 migration, pre Story 3) has a **known-broken read path**: `AssistantProjectMappingRepository.get_assistant_ids` (and `create`/`delete`/`exists`) query `assistant_project_mapping`, a table the Story 2 migration already dropped (`c3d4e5f6a7b8_..._migrate_assistant_project_mapping_to_settings.py:92`). Any real (non-mocked) invocation of `GET /v1/assistants/projects/mapping`, or of `enable`/`disable`, will fail against a live database today.
- Speculative: the task names only the read/listing path ("assistant-listing logic") as in scope, but `enable()`/`disable()` (and the underlying `create`/`delete`/`exists` repository methods) share the same dropped-table problem and are not explicitly named — leaving them unaddressed would mean the POST/DELETE mapping endpoints remain broken after this story ships, unless the frontend has already stopped calling them in favor of the generic `project_settings.py` PUT for `ms_teams`. This should be resolved by planning, not assumed here.
- Speculative: the Story 1 frontend-handoff doc states the switch to ms_teams-only reads is a full retirement of the old mapping ("not merged"), which conflicts with the task's own phrasing "read from (or merge with)" — planning should treat "switch, don't merge" as the documented decision unless there's a reason to diverge, since Story 2 already migrated all pre-existing data into ms_teams rows and dropped the source table (no old data remains to merge from).
- The Story 2 migration's `upgrade()` explicitly skips (with a warning log, not an error) inserting an ms_teams row for any project that already has one at migration time (`c3d4e5f6a7b8_..._migrate_assistant_project_mapping_to_settings.py:73-80`) — meaning some projects' pre-existing `assistant_project_mapping` rows may have been silently left unmigrated if a manually-created `ms_teams` row already existed for that project before the migration ran. The read path being built in this story will not see those unmigrated assistant ids; this is a pre-existing data-migration risk, not something Story 3 can fix retroactively, but worth flagging as a known gap.
- No router-level (FastAPI `AsyncClient`) tests exist today for `assistant_project_mapping.py`'s `GET` endpoint at all — the existing service/repository tests are fully mocked and will not by themselves prove the new read behavior is correct end-to-end.
- `AssistantProjectMappingService.list()`'s current mocked-repository test assertions (`mock_repo.get_assistant_ids.assert_called_once_with(...)`) are tightly coupled to the current repository interface/contract; changing what `get_assistant_ids` queries (or replacing the repository dependency entirely) will require deliberate test updates, not just new tests alongside the old ones.

---

## 7. Summary for Complexity Assessment

This story touches the service layer (`AssistantProjectMappingService.list()`) and the repository layer (`SQLAssistantProjectMappingRepository`/`AssistantProjectMappingRepository`) to redirect the "Teams-enabled assistants" read path from a table the previous story already dropped to the `Settings` model's `ms_teams` rows, reusing the `Settings.get_by_fields`/`credential_type.keyword` query pattern the Story 1 singleton check already established. The router layer (`assistant_project_mapping.py`) and the generic `project_settings.py` create/update/list endpoints require no further change: create/update ms_teams support, validation, and singleton enforcement were fully implemented and tested in Stories 1–2, and the generic project-settings list endpoint already surfaces ms_teams rows through the existing credential-type filter with no special-casing needed.

Technical novelty is low — the target pattern (query `Settings` by `project_name` + `credential_type`, extract `assistant_ids` via `credential()`/`normalize_values()`) is already proven in this codebase by `check_ms_teams_singleton` and other `get_credentials`/`retrieve_setting` call sites. The primary risk is scope ambiguity around `enable()`/`disable()`, which share the same dropped-table dependency as `list()` but are not explicitly named by the task, and a documented product decision (frontend-handoff: "not merged," full switch) that partially contradicts the task's own "read from (or merge with)" wording — both should be resolved during planning rather than assumed.

Test coverage posture is a real gap: the existing service and repository unit tests for this feature are fully mocked against the current (soon-to-change) repository contract and provide no protection against a subtly wrong reimplementation; there is also no existing router-level test for the `GET /v1/assistants/projects/mapping` endpoint. New/updated tests should validate against `Settings` rows directly (single ms_teams row per project, zero rows, and the multi-row edge case flagged as a migration risk) rather than continuing to mock a repository interface whose underlying query target has changed.

---

## 8. External References

None named by the task.
