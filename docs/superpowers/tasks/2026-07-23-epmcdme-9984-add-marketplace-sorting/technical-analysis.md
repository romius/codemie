# Technical Research

**Task**: marketplace assistants sorting api
**Generated**: 2026-07-23T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-9984: Add sorting options to Assistant Marketplace view. Implement sorting for the marketplace listing of assistants by: Usage (most/least used), Likes (most/least liked), Dislikes (most/least disliked), Name (alphabetical ascending/descending). Acceptance criteria: (1) Marketplace view includes clear sorting controls with options: usage, likes, dislikes, name (ascending/descending). (2) Selecting any sorting option triggers appropriate reordering of assistants. (3) Sorting logic correctly reflects actual metrics and name order. (4) Sorting does not impact filtering or searching functionality. (5) No negative impact to performance or assistant visibility. (6) Sorting options are available for all users with access to Marketplace.

---

## 2. Codebase Findings

### Existing Implementations

**Router layer**

- `src/codemie/rest_api/routers/assistant.py` — The `GET /v1/assistants` endpoint (`index_assistants`, lines 194–244) is the marketplace listing entry point. Currently accepts `scope`, `filters` (JSON), `page`, `per_page`, `minimal_response` query parameters. No `sort_by` or `sort_order` parameter exists. Delegates to `AssistantRepository().query()`.

**Repository / data-access layer**

- `src/codemie/service/assistant/assistant_repository.py` — `AssistantRepository.query()` (lines 60–154) contains all sorting logic. Current sorting is hardcoded per scope:
  - `MARKETPLACE` scope (lines 105–111): `unique_users_count DESC NULLS LAST`, then `update_date DESC NULLS LAST`, then `id ASC` (stable pagination).
  - `PROJECT_WITH_MARKETPLACE` scope (lines 112–132): `is_global ASC`, CASE expression on `unique_users_count DESC`, then `update_date DESC NULLS LAST`, then `id ASC`.
  - All other scopes: `update_date DESC NULLS LAST`.
  - Also contains `update_reaction_counts()` (lines 267–297) and `increment_usage_count()` (lines 234–265) which update the three count columns on the `assistants` table.

**Model layer**

- `src/codemie/rest_api/models/assistant.py` — `AssistantBase` (lines 617–684) declares all four sortable fields:
  - `unique_users_count: Optional[int] = SQLField(default=0, index=False)` (line 679)
  - `unique_likes_count: Optional[int] = SQLField(default=0, index=False)` (line 680)
  - `unique_dislikes_count: Optional[int] = SQLField(default=0, index=False)` (line 681)
  - `name: str` with GIN trigram index `ix_assistants_name` (for text search, not ORDER BY)
  - Also defines `AssistantScope` enum used by the listing endpoint.
  - `AssistantListResponse` (lines 559–576) already includes all three count fields and `name` — no response model change needed.

- `src/codemie/rest_api/models/skill.py` — Defines `SkillSortBy(str, Enum)` (lines 73–78). This is the direct pattern to follow for introducing `AssistantSortBy`.

**Reference pattern (skills)**

- `src/codemie/rest_api/routers/skill.py` (lines 210–212): `sort_by: SkillSortBy | None = Query(None, description="...")` — the established pattern for adding a typed sort parameter to a listing router.
- `src/codemie/repository/skill_repository.py`: maps the enum to `ORDER BY` clauses in the repository.

**Filter layer (no change needed)**

- `src/codemie/service/filter/filter_services.py` — `AssistantFilter.FILTER_CONFIG` handles all SQL filtering. Sorting is applied as a separate `order_by()` call after filters, so sorting and filtering are fully orthogonal. No changes needed here.

**Reaction/usage update path**

- `src/codemie/service/assistant/assistant_user_interaction_service.py` — `AssistantUserInterationService.manage_reaction()` → `AssistantRepository.update_reaction_counts()` updates `unique_likes_count` / `unique_dislikes_count`.
- Chat usage path → `AssistantRepository.increment_usage_count()` updates `unique_users_count`.

**Elasticsearch search path (out of scope)**

- `src/codemie/service/search_and_rerank/marketplace.py` — Separate code path for full-text/semantic search with in-memory popularity_score re-ranking. Not involved in the DB-backed listing endpoint that this task targets.

### Architecture and Layers Affected

| Layer | Component | Change Required |
|---|---|---|
| Router (FastAPI) | `rest_api/routers/assistant.py` — `index_assistants` | Add `sort_by: AssistantSortBy \| None` and `sort_order: SortOrder \| None` Query params; pass to repository |
| Enum definition | `rest_api/models/assistant.py` | Add `AssistantSortBy(str, Enum)` with values `usage`, `likes`, `dislikes`, `name` |
| Repository (SQLModel/SQLAlchemy) | `service/assistant/assistant_repository.py` — `query()` | Add `sort_by` / `sort_order` parameters; extend scope-branching block (lines 104–132) to apply user-supplied sort while preserving secondary (`update_date`) and tertiary (`id`) sort keys |
| DB schema (optional) | Alembic migration | Add B-tree indexes on `unique_users_count`, `unique_likes_count`, `unique_dislikes_count`, and `name` (btree) if performance under load is a concern |

Filter, service, and response model layers require no changes.

### Integration Points

- `GET /v1/assistants` endpoint is the only REST entry point for the marketplace listing. All sorting changes flow through this single endpoint and the repository method it delegates to.
- `AssistantRepository.update_reaction_counts()` and `increment_usage_count()` keep the sort-basis columns fresh; no changes needed to these methods.
- `AssistantFilter` (filter_services.py) is orthogonal — it applies WHERE clauses before `ORDER BY`.
- Elasticsearch `SearchAndRerankMarketplace` is a completely separate code path and is unaffected.

### Patterns and Conventions

1. **Sort enum**: `class AssistantSortBy(str, Enum)` in `rest_api/models/assistant.py`, following `SkillSortBy` at `rest_api/models/skill.py:73`. Values: `"usage"`, `"likes"`, `"dislikes"`, `"name"`.
2. **Sort order enum**: A `SortOrder(str, Enum)` with `"asc"` / `"desc"` — or reuse a shared one if it exists.
3. **Router Query param**: `sort_by: AssistantSortBy | None = Query(None, description="...")` on `index_assistants` (mirrors skill router pattern).
4. **Repository column mapping**:
   - `usage` → `Assistant.unique_users_count`
   - `likes` → `Assistant.unique_likes_count`
   - `dislikes` → `Assistant.unique_dislikes_count`
   - `name` → `Assistant.name`
5. **Null handling**: All counter columns are `Optional[int]` with `default=0` in SQLField. Use `.nullslast()` when sorting DESC (existing code already does this). Use `.nullsfirst()` when sorting ASC to surface nulls appropriately.
6. **Stable pagination**: Always keep `update_date DESC NULLS LAST, id ASC` as secondary/tertiary sort keys after the primary user-selected sort column.
7. **No raw SQL strings**: All sort expressions must use SQLAlchemy column-expression API (`.asc()`, `.desc()`, `.nullslast()`) per `database-patterns.md`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

All guides are present under `C:\Users\KonstantinShnyrkov\Work\codemie-dev\codemie\.ai-run\guides\`:

| Guide | Relevance |
|---|---|
| `architecture/layered-architecture.md` | Sorting is persistence-level — belongs in `AssistantRepository`, not a service |
| `architecture/project-structure.md` | Confirms routers in `rest_api/routers/`, repositories in `service/` |
| `api/endpoint-conventions.md` | `sort_by` must be a typed Query param in the router, validated by FastAPI/Pydantic |
| `api/rest-api-patterns.md` | Enum-based typed sort param is the correct pattern |
| `data/repository-patterns.md` | Repositories own `ORDER BY` logic |
| `data/database-patterns.md` | SQLAlchemy column expressions only; no raw SQL string interpolation |
| `data/database-optimization.md` | Pagination chain (`offset/limit`) must not be broken; sort applied before limit |
| `standards/code-quality.md` | Ruff linting enforced; enum string values must follow existing `snake_case` convention |

No guide addresses marketplace sorting specifically.

### Architectural Decisions

1. **Sorting is scope-conditional** — the existing repository branches on `AssistantScope` to apply different sort strategies. The new parameterized sort must be inserted inside the appropriate scope branches, not replace the branch structure.
2. **Stable pagination is an explicit design choice** — the `id ASC` tertiary sort exists to provide deterministic page boundaries. This must be preserved when adding a user-selected primary sort.
3. **Denormalized counters are intentionally not indexed** — `index=False` on all three counter columns is explicit in the model. Performance at scale for the new sort options requires a decision about adding B-tree indexes via a new Alembic migration.
4. **Elasticsearch is a separate code path** — the DB listing endpoint and the ES search/rerank path are distinct; the task targets only the DB path.

### Derived Conventions

- `AssistantSortBy` enum should be co-located with `AssistantScope` in `rest_api/models/assistant.py`.
- When `sort_by is None` and scope is `MARKETPLACE`, default to `unique_users_count DESC` (preserves existing behavior).
- The `sort_order` direction applies to the user-selected primary column only; secondary keys always use their existing direction.

---

## 4. Testing Landscape

### Existing Coverage

| Test File | What It Covers |
|---|---|
| `tests/codemie/service/assistant/test_assistant_repository.py` | `AssistantRepository.query()` — scope filtering, pagination, permission matrices, SQL WHERE clause inspection. No tests for `ORDER BY` clauses or sort parameters. |
| `tests/codemie/rest_api/routers/test_assistant_marketplace.py` | Marketplace publish/unpublish/validate router actions. No tests for `GET /v1/assistants` listing or sort parameters. |
| `tests/codemie/repository/test_skill_repository.py` | `SkillRepository.list_accessible_to_user()` with `sort_by=CREATED_DATE` verified via SQL string assertion. This is the exact pattern to replicate for assistant sort tests. |
| `tests/codemie/rest_api/routers/test_assistant.py` | `_ask_assistant`, guardrails. Does not test the listing endpoint. |
| `tests/codemie/datasource/loader/test_assistant_loader.py` | Uses `unique_users_count`, `unique_likes_count`, `unique_dislikes_count` as data fixtures only. |

### Testing Framework and Patterns

- **Framework**: pytest 8.3.1 with pytest-asyncio, pytest-mock, pytest-httpx, pytest-cov.
- **DB mocking**: `@patch("codemie.service.assistant.assistant_repository.Session")` injecting `MagicMock` — mock `session.exec().all()` and `session.exec().one()`.
- **SQL assertion**: compile query with `.compile(compile_kwargs={"literal_binds": True})` and assert on SQL string — used in `test_skill_repository.py` and `TestProjectWithMarketplaceQuery` in the assistant repository tests.
- **Router tests**: `httpx.AsyncClient` + `ASGITransport(app=app)` — no real HTTP server; auth overridden via `app.dependency_overrides`.
- **Global conftest**: `tests/conftest.py` patches `PostgresClient.get_engine` (session-scoped, autouse) to prevent real DB connections.
- **Parametrize**: `@pytest.mark.parametrize` used for permission matrix and multi-case tests.

### Coverage Gaps

The following are completely untested and will require new tests when the feature is implemented:

1. `AssistantRepository.query()` with `sort_by` parameter — no tests for any of the four sort fields (`usage`, `likes`, `dislikes`, `name`).
2. `sort_order=asc` vs `sort_order=desc` direction toggling on any assistant listing endpoint.
3. `GET /v1/assistants?scope=marketplace&sort_by=...` router integration — no test calls this endpoint path with any sort parameter.
4. Invalid `sort_by` value producing HTTP 422 Unprocessable Entity.
5. Default sort behavior (no `sort_by` param, scope=marketplace) preserving existing `unique_users_count DESC` order.
6. Interaction of `sort_by` with `scope=project_with_marketplace` (which has a more complex existing sort expression using a CASE).
7. Null handling for counter columns (`NULLS LAST` / `NULLS FIRST` behavior when counters are null vs 0).

---

## 5. Configuration and Environment

### Environment Variables

| Variable | Default | Relevance |
|---|---|---|
| `PG_URL` / `POSTGRES_*` | (constructed) | PostgreSQL connection — the database containing the `assistants` table |
| `PG_POOL_SIZE` | `10` | Connection pool size; relevant if new sort-heavy queries increase connection load |
| `PLATFORM_MARKETPLACE_DATASOURCE_NAME` | `"marketplace_assistants"` | Elasticsearch datasource name for indexing; not affected by DB-layer sorting |
| `PLATFORM_DATASOURCES_SYNC_ENABLED` | `False` | Controls startup ES re-index; not affected by DB sorting |
| `MARKETPLACE_LLM_VALIDATION_ON_PUBLISH_ENABLED` | `True` | Publish path only; not affected |

No new environment variables are required for this task.

### Configuration Files

- `src/codemie/configs/config.py` — central `Config(BaseSettings)` loaded from `.env` via pydantic-settings. No marketplace-sorting-specific config entries exist or are needed.
- `config/categories/assistant-categories.yaml` — static category list; not relevant.
- `.env` / `tests/.env.test` — no marketplace-specific entries.

### Feature Flags and Deployment Concerns

- No existing feature flag controls marketplace sorting. No new flag is needed — the feature is a straightforward query-layer addition.
- **Alembic migration (recommended)**: The three counter columns (`unique_users_count`, `unique_likes_count`, `unique_dislikes_count`) and the `name` column have no B-tree indexes. Sorting on large datasets without indexes will result in full sequential scans. A new migration should add `btree` indexes on `unique_likes_count`, `unique_dislikes_count` (and optionally `unique_users_count`, `name`) in `src/external/alembic/versions/`. The `unique_users_count` column is already used in the existing marketplace sort — if it is already a performance concern it predates this ticket; the new sort options for `likes`, `dislikes`, and `name` are net-new.
- No Docker, CI/CD, secrets, or deployment manifest changes are needed. Sorting is entirely a query-layer change.

---

## 6. Risk Indicators

- **No database index on `unique_users_count`, `unique_likes_count`, `unique_dislikes_count`** — all three columns are declared `index=False` in `AssistantBase` (lines 679–681 of `rest_api/models/assistant.py`). Sorting by these columns on a large `assistants` table will perform a full sequential scan. Acceptance criterion 5 ("No negative impact to performance") requires a decision about adding B-tree indexes via an Alembic migration.
- **No B-tree index on `name` for ORDER BY** — `ix_assistants_name` is a GIN trigram index for ILIKE search only; it does not support `ORDER BY`. Alphabetical sorting by name will also do a sequential scan at scale.
- **Zero existing tests for `ORDER BY` in the assistant listing path** — `test_assistant_repository.py` does not assert on any sort clause for the MARKETPLACE or PROJECT_WITH_MARKETPLACE scopes. All sort test coverage is a greenfield addition.
- **`PROJECT_WITH_MARKETPLACE` scope uses a CASE expression for sort** — when `sort_by` is user-supplied, the existing CASE expression (which sorts non-globals first, then globals by usage) must be reconciled with the new sort. There is no documented decision on whether user-selected sort overrides the `is_global`-first grouping or not. This interaction needs clarification.
- **No `sort_order` parameter precedent for the assistant router** — the `SkillSortBy` pattern does not include a `sort_order` direction parameter; skills have fixed directions per enum value. The acceptance criteria require ascending/descending for all four sort fields. This is a slightly richer API surface than the existing skill pattern.
- **`AssistantScope.TEMPLATES` uses in-memory Python sort** (lines 251–264 of assistant.py) — this path is unrelated to the marketplace listing but shares the same `index_assistants` handler. The new `sort_by` parameter must be guarded to apply only to the MARKETPLACE and PROJECT_WITH_MARKETPLACE scopes, not to the TEMPLATES path.
- **Pre-existing TODO at `assistant_repository.py:445`** — `# TODO: need to clarify` inside `_apply_marketplace_filter` on external user visibility. This does not block sorting, but could affect which assistants appear in the marketplace view overall.
- **`CHANGELOG.md` is stale** — last entry predates the EPMCDME ticket era; cannot be used for historical context.

---

## 7. Summary for Complexity Assessment

The task touches three architectural layers: the FastAPI router (`rest_api/routers/assistant.py`), the Pydantic/SQLModel model layer (`rest_api/models/assistant.py`), and the SQLAlchemy repository layer (`service/assistant/assistant_repository.py`). The file change surface is small — approximately 3–4 source files plus a test file and an optional Alembic migration. All four target sort fields (`unique_users_count`, `unique_likes_count`, `unique_dislikes_count`, `name`) already exist as columns on the `assistants` table and are already included in the `AssistantListResponse` model; no schema additions or response model changes are required for the core feature.

The implementation follows a well-established in-repo pattern: the `SkillSortBy` enum in `rest_api/models/skill.py` and its corresponding Query parameter in `rest_api/routers/skill.py` are a direct template. The `AssistantRepository.query()` method already has hardcoded multi-column `ORDER BY` expressions using the `.nullslast()` / `.desc()` / `.asc()` SQLAlchemy API; parameterizing these is a localized change to an existing `if/elif/else` block. The filtering path is orthogonal and requires no changes. The primary technical novelty is introducing a `sort_order` direction parameter (asc/desc), which the skill pattern does not have; this requires a small additional enum or `Literal` type and careful integration with the existing multi-key sort chain (primary user-selected → secondary `update_date` → tertiary `id`).

Test coverage for the affected area is a significant gap: no existing test asserts on any `ORDER BY` clause for the MARKETPLACE or PROJECT_WITH_MARKETPLACE scopes, and no router test calls `GET /v1/assistants` with sort parameters. New tests are required at both the repository level (SQL assertion pattern from `test_skill_repository.py`) and the router level (AsyncClient pattern from `test_assistant_marketplace.py`). The risk indicators are: absent DB indexes on the counter columns (potential sequential scans violating acceptance criterion 5), the interaction of user-selected sort with the existing `PROJECT_WITH_MARKETPLACE` CASE expression (requires a design decision), and the complete absence of sort-related test coverage. Overall, the task is low-to-medium complexity with a clear path, bounded scope, and one open design question about the `PROJECT_WITH_MARKETPLACE` sort interaction.
