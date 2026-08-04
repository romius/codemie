# Technical Research

**Task**: marketplace assistant backend api clone counter reactions
**Generated**: 2026-07-27
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-10889 backend-only scope (codemie repo): Add clone_count tracking for Marketplace assistants.

Scope:
1. Add `clone_count` field to `AssistantBase` / `AssistantListResponse` (src/codemie/rest_api/models/assistant.py, ~line 680/572) + alembic migration (`server_default='0'`, nullable).
2. Add optional `source_assistant_id: Optional[str]` to `AssistantRequest` to mark clone-create.
3. Add `AssistantRepository.increment_clone_count()`, mirroring `update_reaction_counts()` (assistant_repository.py:268-294); call it from the `POST /v1/assistants` handler when `source_assistant_id` is present.
4. Defer folding `clone_count` into marketplace ranking sort (assistant_repository.py:104-132) — MVP scope, optional/follow-up only.
5. Tests: clone increment + metric calc.

Contract with frontend (already agreed, codemie-ui builds independently): request field `source_assistant_id`, response field `clone_count`. Confirm `AssistantRequest` drops unknown fields silently (pydantic default) rather than rejecting them.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/models/assistant.py:559-577` — `AssistantListResponse`: minimal marketplace-facing response model with `unique_users_count`, `unique_likes_count`, `unique_dislikes_count`, `categories`, `is_global`, `shared`, `origin`. Add `clone_count: Optional[int] = None` here (~line 572).
- `src/codemie/rest_api/models/assistant.py:617-683` — `AssistantBase(CommonBaseModel, Owned)`: SQLModel table-backed base. Count fields at lines 679-681 use `SQLField(default=0, index=False)`. Add `clone_count: Optional[int] = SQLField(default=0, index=False)` alongside (~line 680).
- `src/codemie/rest_api/models/assistant.py:294-334+` — `AssistantRequest(BaseModel)`: create/update payload, plain `pydantic.BaseModel` base with no `model_config`/`Config` override for `extra` anywhere in the class chain (only unrelated `Config`/`model_config` hits at lines 224 and 1387). Add `source_assistant_id: Optional[str] = None` here.
- `src/codemie/service/assistant/assistant_repository.py:234-265` — `increment_usage_count()`: fetch fresh via `Assistant.find_by_id(assistant.id)`, guard against missing, `current_count = fresh.count or 0`, compute new count, `.save()`, return. Closest increment-style precedent.
- `src/codemie/service/assistant/assistant_repository.py:267-297` — `update_reaction_counts(assistant, likes_count, dislikes_count)`: exact precedent to mirror for `increment_clone_count()` — `@staticmethod`, fetch fresh instance, mutate count fields, `fresh_assistant.save()`, return. No try/except (differs from AIKata equivalent, which returns `False` on commit exception).
- `src/codemie/service/assistant/assistant_repository.py:104-132` — marketplace ranking sort: `AssistantScope.MARKETPLACE` sorts by `unique_users_count.desc()`; `PROJECT_WITH_MARKETPLACE` uses `case()` on `is_global` + `unique_users_count`. This is the deferred fold-in point for `clone_count` (explicitly out of scope for this task).
- `src/codemie/service/assistant/assistant_user_interaction_service.py:81-84,164,233,242-251` — service layer calling `assistant_repo.increment_usage_count()` / `update_reaction_counts()`. Shows a service-mediates-repository pattern, but note the create-assistant path below calls the repository directly from the router instead.
- `src/codemie/rest_api/routers/assistant.py:669-777` — `create_assistant()` handler, `POST /v1/assistants`. Builds `Assistant(**request.model_dump(exclude={...}))`, saves, calls `AssistantVersionService.create_initial_version`, `GuardrailService.sync_guardrail_assignments_for_entity`, `_track_mcp_usage_on_create`, `_track_assistant_management_metric`. Add `if request.source_assistant_id: AssistantRepository.increment_clone_count(...)` after successful `assistant.save()`, following the same post-save pattern as the existing tracking calls, before returning `AssistantCreateResponse`.
- `src/codemie/rest_api/routers/assistant.py:712-713` — existing comment: "Filter out datasources... This handles cases like cloning an assistant to a different project" — confirms clone-via-create is already an established pattern in this handler, just without a tracked `source_assistant_id` today.

### Architecture and Layers Affected

- **API router** — `src/codemie/rest_api/routers/assistant.py` (`create_assistant` handler, request/response wiring).
- **Repository** — `src/codemie/service/assistant/assistant_repository.py` (`AssistantRepository`, static methods, SQLModel/Session-based).
- **Model / DB schema** — `src/codemie/rest_api/models/assistant.py` (`AssistantBase`/`Assistant` SQLModel table, `AssistantRequest`, `AssistantListResponse` Pydantic/SQLModel classes).
- **Migration** — `src/external/alembic/versions/*.py`.
- Note: the router calls `AssistantRepository` directly for create-flow tracking rather than going through the service layer used elsewhere (`assistant_user_interaction_service.py`) — follow the router's existing direct-call convention for consistency with `_track_assistant_management_metric`.

### Integration Points

- `src/codemie/rest_api/routers/assistant.py` → `src/codemie/service/assistant/assistant_repository.py` (direct static calls).
- `src/codemie/service/assistant/assistant_repository.py` → `src/codemie/rest_api/models/assistant.py` (imports `Assistant`, `AssistantListResponse`, `AssistantRequest`).
- `src/codemie/service/assistant/assistant_user_interaction_service.py` → `assistant_repository.py` (`increment_usage_count`, `update_reaction_counts`) — parallel pattern, not directly touched by this task.
- Alembic: `src/external/alembic/env.py` imports `Assistant`/`AssistantConfiguration` from `codemie.rest_api.models.assistant` for metadata; sets `search_path` to `codemie_config.DEFAULT_DB_SCHEMA`; takes an `ACCESS EXCLUSIVE` lock on `alembic_version` during migration runs.

### Patterns and Conventions

- Counter fields follow a 3-part pattern: (1) `SQLField(default=0, index=False)` on `AssistantBase`, (2) mirrored as `Optional[int] = None` on `AssistantListResponse`, (3) **not** present on `AssistantRequest` — counts are server-managed, never client-settable. `clone_count` follows the same shape; `source_assistant_id` is the new client-settable field (distinct concept — it's a trigger, not a counter).
- Repository counter-mutation methods (`update_reaction_counts`, `increment_usage_count`) are `@staticmethod`, re-fetch a fresh session-tracked instance via `Assistant.find_by_id()`, mutate, call `.save()`.
- Migration idiom for new int counter columns: `op.add_column("assistants", sa.Column("clone_count", sa.Integer(), nullable=True, server_default="0"))` — combining the nullable style from `5f9df283d7f9_add_interaction_settings.py` (assistants-table precedent, no server_default) with the `server_default="0"` style from `k6l7m8n9o0p1_add_workflow_marketplace_columns.py` (workflows table) and `b3a4c5d6e7f8_add_leaderboard_tables.py`.
- `AssistantRequest` has no `extra="forbid"` anywhere in its chain (plain `pydantic.BaseModel`) — Pydantic v2 default `extra="ignore"` applies, confirming unknown fields are silently dropped. Adding `source_assistant_id` explicitly is straightforward and backward-compatible.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/api/rest-api-patterns.md` — routers under `src/codemie/rest_api/routers/`, registered via `app.include_router` in `main.py:658`; use shared exception types and existing auth dependencies.
- `.ai-run/guides/api/endpoint-conventions.md` — request/response schemas live under `src/codemie/rest_api/models/`; keep routers thin, delegate non-trivial logic to handlers/services.
- `.ai-run/guides/data/database-patterns.md` — use SQLModel/SQLAlchemy expressions, no raw SQL; schema changes via Alembic migrations under `src/external/alembic/versions/`; check migration heads before adding a new one.
- `.ai-run/guides/data/repository-patterns.md` — repositories own data access; services should extend the matching repository rather than access storage directly.
- `.ai-run/guides/testing/testing-service-patterns.md` — mock repository/provider boundaries in service tests; use `pytest-asyncio` for async paths.
- `.ai-run/guides/testing/testing-patterns.md` — place tests under matching `tests/codemie/<package path>/` mirror; mock external services; run narrowest relevant scope.

### Architectural Decisions

- Recorded in `notes/projects/epmcdme-10889-marketplace-clone-counter.md` (most authoritative source): contract agreed 2026-07-27 — request field `source_assistant_id: Optional[str]` on `AssistantRequest`/`POST /v1/assistants`; response field `clone_count: Optional[int]` on `AssistantListResponse`/`AssistantBase`. Backend ships independently of frontend.
- Design decision: mirror the existing reactions pattern end to end (repository static method → field mutation → save), not the service-mediated pattern.
- Migration pattern explicitly specified in the ticket notes: `op.add_column('assistants', sa.Column('clone_count', sa.Integer(), server_default='0', nullable=True))`.
- Marketplace ranking sort fold-in explicitly deferred as MVP-optional/out of scope.
- Prior estimate on file: complexity 14/36 (S), routes straight to writing-plans, no brainstorm needed.

### Derived Conventions

- Counter-field-on-both-model pattern (table + response) and static-method-repository-mutator pattern are both directly observable in source and treated as binding convention for this task (see Section 2).
- No TODO/HACK/ADR/DECISION markers found in `assistant.py` or `assistant_repository.py` near the relevant sections.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/repository/test_ai_kata_repository.py:650-679` (`TestAIKataRepositoryUpdateReactionCounts`) — closest structural precedent for a repository counter-update test: mocks `Session`/`get_engine`, calls `repository.update_reaction_counts(...)`, asserts field mutation + `session.add`/`session.commit`, plus a failure-path test (`commit` exception → `False`). Note: this is `AIKataRepository`, not `AssistantRepository`, and `AssistantRepository.update_reaction_counts` has no equivalent try/except.
- `tests/codemie/service/assistant/test_assistant_repository.py` (695 lines) — **no existing test for `update_reaction_counts` or `update_usage_count`** on `AssistantRepository`. Only covers `query()`, `get_users()`, `_enrich_system_prompt_history*`, and private query-builder helpers. This is the file where `increment_clone_count` tests should be added.
- `tests/codemie/service/assistant/test_assistant_user_interaction_service.py:88-163` — service-layer precedent for mocking `AssistantRepository` via `MagicMock(spec=AssistantRepository)` and asserting `mock_assistant_repo.update_reaction_counts.assert_called_once_with(...)`.
- `tests/codemie/rest_api/routers/test_assistant_reactions.py` (96 lines) — API-level precedent using `httpx.AsyncClient` + `ASGITransport(app=app)`, `app.dependency_overrides[assistant_router.authenticate]`, `patch("codemie.rest_api.routers.assistant.Assistant.find_by_id", ...)`. Only covers error paths, not the success/counter-increment path.
- `tests/codemie/rest_api/routers/test_assistant.py` (701 lines) — covers `POST /v1/assistants` indirectly via helper-function unit tests (guardrails, slug handling), not a full end-to-end HTTP create test.
- `tests/codemie/rest_api/routers/test_assistant_marketplace.py` (656 lines) — covers publish/unpublish/categories, not clone or ranking sort.
- No test anywhere asserts actual SQL `ORDER BY` correctness for `unique_users_count` — marketplace sort tests only check filter/pagination behavior on mocked `session.exec` results.

### Testing Framework and Patterns

- pytest + `pytest-asyncio` (`@pytest.mark.asyncio`), `unittest.mock` (`MagicMock`, `patch`, `patch.object`), `httpx.AsyncClient` + `ASGITransport` for router-level tests.
- Repository tests: `@patch("<module>.Assistant.get_engine")` + `@patch("codemie.repository.<x>.Session")`, `mock_session_cls.return_value.__enter__.return_value = mock_session`; assert on `session.add`/`session.commit` and direct field mutation (no real DB).
- Service tests: `MagicMock(spec=AssistantRepository)` via constructor DI; `patch('codemie.rest_api.models.assistant.Assistant.find_by_id', return_value=mock_assistant)`.
- Router tests: `app.dependency_overrides[assistant_router.authenticate] = lambda: user` (autouse fixture), `patch("codemie.rest_api.routers.assistant.Assistant.find_by_id", ...)`.
- No dedicated `conftest.py` colocated with these assistant test dirs — each file defines its own local fixtures.

### Coverage Gaps

- `clone_count` and `source_assistant_id` do not exist anywhere in `src/` or `tests/` — fully greenfield.
- `AssistantRepository` has zero tests for `update_reaction_counts`/`update_usage_count` (unlike `AIKataRepository`) — new `increment_clone_count` tests need to establish this coverage from scratch in `test_assistant_repository.py`.
- No end-to-end HTTP success-path test for `POST /v1/assistants` exists — a new test following `test_assistant_reactions.py`'s pattern is needed to verify `increment_clone_count` is invoked when `source_assistant_id` is present.
- No precedent for "metric calc" / sort-ordering tests — if "metric calc" in the task's test scope means ranking-sort correctness, that has no existing test pattern to mirror (consistent with the ranking fold-in being deferred).

---

## 5. Configuration and Environment

### Environment Variables

None directly relevant — DB connectivity is handled via `codemie.clients.postgres.PostgresClient`/`codemie_config`, no new env vars needed.

### Configuration Files

- `src/external/alembic/alembic.ini` — Alembic config, `script_location = %(here)s`, versions in `src/external/alembic/versions/`.
- `src/external/alembic/env.py` — wires SQLModel metadata (imports `Assistant`, `AssistantConfiguration`), sets schema search_path, takes `ACCESS EXCLUSIVE` lock on `alembic_version` during migration runs.

### Feature Flags and Deployment Concerns

- No feature flags gate marketplace/assistant clone or reaction features — none needed for this change.
- **Multiple alembic heads**: repo currently has ~19 unmerged head revisions in `versions/`. The new migration's `down_revision` must point at the correct current head on the target branch, or a merge migration is needed — verify true head at implementation time (`alembic heads` or scan for revisions with no children).
- Simple additive column: `clone_count` int, nullable, `server_default='0'` — no backfill needed (server_default covers existing rows), no index required (not used in WHERE/ORDER BY per current scope).
- Concurrent-migration locking already handled by `env.py`; just chain onto the correct head.

---

## 6. Risk Indicators

- Multiple alembic heads (~19) in `src/external/alembic/versions/` — picking the wrong `down_revision` will create a divergent migration chain; must confirm true head before writing the migration.
- `AssistantRepository` has no existing test coverage for its counter-mutation methods (`update_reaction_counts`, `update_usage_count`) — `increment_clone_count` tests have no direct same-file precedent to copy verbatim; nearest precedent (`TestAIKataRepositoryUpdateReactionCounts`) is in a different repository class and includes an exception/failure-path pattern that `AssistantRepository`'s existing methods don't implement — decide up front whether `increment_clone_count` should replicate that failure handling or match `AssistantRepository`'s current no-try/except style.
- No end-to-end HTTP test exists for the `POST /v1/assistants` success path — new test must be built from a thinner precedent (`test_assistant_reactions.py` only covers error paths).
- "Metric calc" test scope from the ticket is ambiguous — no existing precedent tests ranking-sort correctness by a numeric field; if this refers to the deferred marketplace-sort fold-in, it may be out of scope; if it refers to the increment arithmetic itself, that's covered by the repository unit test.
- Router calls `AssistantRepository` directly rather than through the service layer used elsewhere (`assistant_user_interaction_service.py`) — inconsistent layering already exists in the codebase; this task should follow the router's existing direct-call convention (matches `_track_assistant_management_metric`) rather than introduce a new service-layer indirection.
- `notes/projects/epmcdme-10889-marketplace-clone-counter.md` and `docs/superpowers/tasks/2026-07-27-marketplace-clone-counter-backend/.state.json` indicate a prior SDLC run/estimate already exists for this ticket (S, 14/36) — worth cross-checking against this analysis rather than re-deriving from scratch.

---

## 7. Summary for Complexity Assessment

This task touches four layers in a shallow, well-precedented way: DB schema (one additive nullable int column via Alembic), model (two field additions across `AssistantBase`/`AssistantListResponse`/`AssistantRequest` in a single file, `assistant.py`), repository (one new static method mirroring `update_reaction_counts` almost line-for-line), and API router (one conditional call inserted into the existing `create_assistant` handler). Total file-change surface is small: 1 model file, 1 repository file, 1 router file, 1 new migration file, plus 1-2 test files. No new endpoints, no new service classes, no cross-service integration.

Technical novelty is very low — every piece of this change has a direct, structurally identical precedent already in the codebase (`update_reaction_counts`/`increment_usage_count` for the repository method, `unique_likes_count`/`unique_dislikes_count` for the model fields, existing reaction-count migrations for the Alembic pattern, and an already-present "cloning to a different project" code path in the router that just lacks source-id tracking today). The only genuine unknown is the correct alembic head to chain onto, given ~19 unmerged heads in the repo.

Test coverage posture is mixed-to-thin: the feature area itself is greenfield (no `clone_count`/`source_assistant_id` references anywhere), and `AssistantRepository`'s existing counter methods have zero tests to build on directly — the nearest true precedent (`TestAIKataRepositoryUpdateReactionCounts`) lives in a sibling repository class with a different error-handling style, and no end-to-end HTTP success-path test exists for `POST /v1/assistants` to extend. This raises the testing effort proportionally more than the implementation effort, and the "metric calc" test requirement in the ticket is ambiguous and should be clarified (increment arithmetic vs. deferred ranking-sort correctness) before estimating test scope precisely.
