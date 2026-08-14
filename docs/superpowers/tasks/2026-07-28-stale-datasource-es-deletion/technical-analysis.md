# Technical Research

**Task**: stale-datasource elasticsearch deletion lifecycle-state indexed-datasource
**Generated**: 2026-07-28T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-13161: Implement the deletion phase for stale datasources. When a datasource is marked STALE (by the existing nightly StaleDatasourceService), delete its Elasticsearch index and transition it to ARCHIVED state. Add STALE_DATASOURCE_DELETION_ENABLED flag (default False) and STALE_DATASOURCE_MAX_DELETIONS_PER_RUN circuit-breaker cap. The _delete_stale_indexes() method processes all STALE rows: builds shared-index guard from ACTIVE rows, skips rows whose index is shared, deletes ES index, advances rows to ARCHIVED. Add reactivation hook in IndexInfo.complete_progress() to flip STALE/ARCHIVED back to ACTIVE and clear marked_stale_at on any successful indexing completion. See full design spec at docs/superpowers/specs/2026-07-24-stale-datasource-es-deletion-design.md

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/service/stale_datasource/stale_datasource_service.py` — `StaleDatasourceService`: detection/marking phase; `detect_and_mark_stale_datasources()` returns stats dict; `_delete_stale_indexes()` does not yet exist; `already_stale` stat counter is dead code (pre-existing; `_get_candidate_datasources` filters ACTIVE rows only so it can never increment)
- `src/codemie/service/stale_datasource/scheduler.py` — `StaleDatasourceScheduler`: APScheduler cron job with `async_leader_lock`; reads `STALE_DATASOURCE_ENABLED` and `STALE_DATASOURCE_SCHEDULE`; logs stats dict from `detect_and_mark_stale_datasources()` at INFO level (lines 76–82)
- `src/codemie/service/stale_datasource/config.py` — `STALE_DATASOURCE_LOCK_ID = 987654324` (PostgreSQL advisory lock for multi-pod leader election)
- `src/codemie/rest_api/models/index.py` — `IndexInfo` SQLModel table; `LifecycleState` enum (ACTIVE/STALE/ARCHIVED, lines 82–85); `complete_progress()` at line 596; `get_index_identifier()` at line 1206; `IndexInfo.delete()` at line 1253 (deletes ES index AND GitRepo rows AND scheduler/webhook integrations — too broad for this task)
- `src/codemie/configs/config.py` — Central Pydantic settings; existing stale config block at lines 748–754; `finalize_settings` validator at line 864 validates cron expression when `STALE_DATASOURCE_ENABLED=True`
- `src/codemie/clients/elasticsearch.py` — `ElasticSearchClient.get_client()` (sync) and `get_async_client()` (async); sync client is used by existing `IndexInfo.delete()`
- `src/codemie/service/monitoring/agent_monitoring_service.py` — `AgentMonitoringService(BaseMonitoringService)`; `send_count_metric(name="delete_datasource", attributes={...})` at router line 429 is the reference for the new `stale_datasource_index_deleted` metric
- `src/codemie/service/monitoring/base_monitoring_service.py` — `BaseMonitoringService.send_count_metric()` at line 115; underlying OpenTelemetry emission
- `src/codemie/service/analytics/metric_names.py` — `MetricName` enum; no stale-deletion metric name exists yet
- `src/codemie/utils/leader_lock.py` — `async_leader_lock` advisory-lock context manager
- `src/external/alembic/versions/f9g0h1i2j3k4_add_lifecycle_state_to_index_info.py` — migration that added `lifecycle_state` and `marked_stale_at` columns; `ARCHIVED` is already a valid PG enum value
- `deploy-templates/values.yaml` — Helm chart env vars; `STALE_DATASOURCE_*` vars are absent (not declared for any environment)
- `src/codemie/rest_api/main.py` — `_setup_stale_datasource_scheduler()` at line 437, gated on `STALE_DATASOURCE_ENABLED`

### Architecture and Layers Affected

- **Config layer** (`src/codemie/configs/config.py`): two new Pydantic settings fields — `STALE_DATASOURCE_DELETION_ENABLED: bool = False` and `STALE_DATASOURCE_MAX_DELETIONS_PER_RUN: int = 100`. The `finalize_settings` validator may need a guard for the nonsensical combination `STALE_DATASOURCE_DELETION_ENABLED=True` with `STALE_DATASOURCE_ENABLED=False`.
- **Model layer** (`src/codemie/rest_api/models/index.py`): `IndexInfo.complete_progress()` requires a reactivation hook. `_build_progress_update_kwargs` does not currently accept `lifecycle_state` or `marked_stale_at`; the reactivation assignments (`self.lifecycle_state = LifecycleState.ACTIVE`, `self.marked_stale_at = None`) must be added to `complete_progress()` before the `update_progress()` call, with a separate targeted SQL update or by extending `_build_progress_update_kwargs`.
- **Service layer** (`src/codemie/service/stale_datasource/stale_datasource_service.py`): new `_delete_stale_indexes()` private method; `detect_and_mark_stale_datasources()` calls it when `STALE_DATASOURCE_DELETION_ENABLED=True` and merges its stats into the returned dict.
- **Scheduler layer** (`src/codemie/service/stale_datasource/scheduler.py`): must log new deletion stat keys from the merged stats dict at INFO level.
- **ES Client layer** (`src/codemie/clients/elasticsearch.py`): sync `get_client().indices.delete(index=name, ignore_unavailable=True)` wrapped in `asyncio.to_thread` — established pattern (see `conversation_analysis_service.py` and `budget_service.py`).
- **Monitoring layer** (`src/codemie/service/monitoring/agent_monitoring_service.py`): emit `stale_datasource_index_deleted` metric with `{project, repo_name, datasource_type}` attributes.

### Integration Points

- `StaleDatasourceService` → `IndexInfo` (queries STALE rows, updates lifecycle_state to ARCHIVED)
- `StaleDatasourceService` → `ElasticSearchClient.get_client()` (sync ES, wrapped in `asyncio.to_thread`)
- `StaleDatasourceService` → `AgentMonitoringService.send_count_metric()` (deletion metric)
- `StaleDatasourceService` → `AppConfig` (reads two new feature flags)
- `IndexInfo.complete_progress()` → no external service deps; sets own fields and calls `update_progress()`
- `StaleDatasourceScheduler` → `StaleDatasourceService` (instantiates and calls)

### Patterns and Conventions

- **Config flags**: plain `bool = False` Pydantic field in `AppConfig`; no feature-flag registry. New keys follow the existing stale block at lines 748–754 with inline comments.
- **Async wrapping of sync ES**: `asyncio.to_thread(sync_fn, *args)` — do not call sync ES client directly inside an `async def`.
- **Targeted SQL update** (not full ORM save): `sa_update(IndexInfo).where(...).values(lifecycle_state=...)` pattern used throughout `IndexInfo` (`update_progress()`, `try_claim_for_resume()`).
- **Write ordering** (spec decision): ES index delete first (`ignore_unavailable=True`), PG transition to ARCHIVED second — crash-safe without extra state.
- **Shared-index guard**: query ACTIVE rows, compute `get_index_identifier()` in-memory, build a set; skip STALE rows whose resolved index is in the set. These rows remain STALE for re-evaluation next night.
- **Circuit breaker**: if `len(stale_candidates) > STALE_DATASOURCE_MAX_DELETIONS_PER_RUN`, abort the entire deletion phase with no partial deletes (not per-row abort).
- **Stats dict**: service returns a flat dict of counters; scheduler logs every key; merge deletion stats under keys like `deleted`, `skipped_shared`, `circuit_breaker_aborted`.
- **`IndexInfo.delete()` must NOT be used**: it deletes GitRepo rows and integrations in addition to the ES index — task spec explicitly rules this out.
- **`IndexInfo.get_index_identifier()`** at line 1206 is the canonical method for resolving physical ES index names; must be used for both guard-set construction and per-row deletion.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/architecture/layered-architecture.md` — enforces router → service → repository separation; deletion logic belongs in `StaleDatasourceService`, not a router
- `.ai-run/guides/data/elasticsearch-integration.md` — ES details belong in repositories/services; index teardown belongs in a dedicated service; `BaseElasticRepository` handles index setup; this task's targeted deletion fits the "dedicated service" pattern
- `.ai-run/guides/data/database-patterns.md` — schema changes via Alembic in `src/external/alembic/versions/`; `ARCHIVED` is already in the `lifecyclestate` PG enum — no migration needed
- `.ai-run/guides/data/repository-patterns.md` — repositories own storage-specific access; sync ES calls must be wrapped in `asyncio.to_thread` when used from async services
- `.ai-run/guides/development/configuration-patterns.md` — new config vars must be declared in `src/codemie/configs/config.py`; do not read env vars directly in services

### Architectural Decisions

Decisions are recorded in `docs/superpowers/specs/2026-07-24-stale-datasource-es-deletion-design.md`:

- Delete ES index only — no PG row deletions, no GitRepo/scheduler/webhook teardown; `IndexInfo.delete()` is explicitly ruled out as too broad
- Three-state lifecycle: `ACTIVE` → `STALE` (pending deletion) → `ARCHIVED` (ES deleted, PG row kept for audit); ARCHIVED is the idempotency marker; sweep is bounded to STALE rows only
- Write ordering: ES delete first, PG update to ARCHIVED second — crash-safe; a row left STALE after a crash is re-evaluated next night
- Same nightly run: marking phase commits first, then deletion phase runs if `STALE_DATASOURCE_DELETION_ENABLED=True`
- Circuit breaker: abort the whole deletion phase (no partial deletes) if candidate count exceeds `STALE_DATASOURCE_MAX_DELETIONS_PER_RUN` (default 100)
- Shared-index guard: skip STALE rows whose index is still used by an ACTIVE datasource (legacy KB naming collision); these rows remain STALE and are re-evaluated nightly
- Reactivation: `IndexInfo.complete_progress()` unconditionally sets `lifecycle_state = ACTIVE` and `marked_stale_at = None`, covering both STALE → ACTIVE and ARCHIVED → ACTIVE in a single hook

### Derived Conventions

- `detect_and_mark_stale_datasources()` is the public entry point; internal phases are private methods (`_get_candidate_datasources`, `_is_datasource_stale`); `_delete_stale_indexes()` follows this pattern
- Scheduler receives the full stats dict and logs all keys; adding new deletion stats keys automatically picks up scheduler logging with no additional changes to the scheduler
- `StaleDatasourceService.__init__` takes `session: AsyncSession` and `metrics_repository: MetricsElasticRepository`; no additional constructor params needed for deletion phase — ES sync client instantiated inline
- `complete_progress()` uses `update_progress()` for progress fields; lifecycle_state/marked_stale_at reactivation requires either augmenting `_build_progress_update_kwargs` or a separate targeted SQL update after `update_progress()`

### Todos

- `docs/superpowers/specs/2026-07-24-stale-datasource-es-deletion-design.md:194` — `STALE_DATASOURCE_NO_UPDATE_DAYS` is defined in config but the current service implementation never uses it; spec notes this as pre-existing drift, not addressed in this task
- `src/codemie/service/stale_datasource/stale_datasource_service.py:147` — `already_stale` stat counter can never increment (candidate query filters ACTIVE rows only); dead counter, removable opportunistically

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/stale_datasource/test_stale_datasource_service.py` — covers `StaleDatasourceService`: `_compute_base_tool_names`, `_fetch_lifecycle_metrics`, `_fetch_tool_usage_metrics`, `_is_datasource_stale`, `detect_and_mark_stale_datasources` (mark-stale wiring, ES error abort, rollback on failure). Covers ACTIVE → STALE transition only; no deletion or ARCHIVED state tests.
- `tests/codemie/rest_api/models/test_index_info.py` — covers `IndexInfo` model methods including `complete_progress`, `start_progress`, `update_index`, `get_index_identifier`. Does not test lifecycle reactivation or STALE/ARCHIVED state handling; the existing `test_complete_progress` only asserts `completed=True` and `is_fetching=False`.
- `tests/codemie/service/index/test_index_service.py` — covers `IndexStatusService.get_index_info_list`; unrelated to deletion phase.
- `tests/codemie/repository/test_base_elastic_repository.py` — covers `BaseElasticRepository` CRUD; no index deletion tests.

### Testing Framework and Patterns

- pytest 8.3.5, pytest-asyncio 0.23.7, pytest-mock 3.14.0, pytest-env 1.1.3, pytest-httpx 0.35.0
- `pytest.ini`: `testpaths = tests`, `pythonpath = src`; no `asyncio_mode` global; each async test requires explicit `@pytest.mark.asyncio`
- Helper factory functions (`_make_service()`, `_make_ds()`) for constructing test objects inline — not fixture-scoped
- `patch.object(service, "_method_name", ...)` inside `with` blocks for partial-mocking internal methods
- `mocker.patch(...)` (pytest-mock) used in model tests for `BaseModelWithSQLSupport.update`
- `AsyncMock()` for session and repository calls; `AsyncMock(side_effect=...)` for error path testing
- Session-scoped `mock_database_engine` autouse fixture in `tests/conftest.py` patches `PostgresClient.get_engine` for all tests
- Test classes group related cases (`TestComputeBaseToolNames`, `TestFetchLifecycleMetrics`, etc.)

### Coverage Gaps

- `_delete_stale_indexes()` — does not exist yet; entire deletion phase is untested
- `STALE_DATASOURCE_DELETION_ENABLED` disabled guard (service returns early when flag is False)
- `STALE_DATASOURCE_MAX_DELETIONS_PER_RUN` circuit-breaker abort path
- Shared-index guard: active-index set construction, STALE row skipping when index is shared
- STALE → ARCHIVED state transition with ES deletion
- `IndexInfo.complete_progress()` reactivation hook: lifecycle_state = ACTIVE and marked_stale_at = None assignments for STALE and ARCHIVED rows
- Stats dict keys for deletion phase merged into `detect_and_mark_stale_datasources()` return value
- ES `asyncio.to_thread` wrapping in async service context

---

## 5. Configuration and Environment

### Environment Variables

All declared in `src/codemie/configs/config.py`, lines 748–754:

- `STALE_DATASOURCE_ENABLED` (bool, default `False`) — master toggle for the nightly detection scheduler
- `STALE_DATASOURCE_SCHEDULE` (str, default `"0 3 * * *"`) — cron schedule (UTC)
- `STALE_DATASOURCE_NO_USAGE_DAYS` (int, default `90`) — days without activity before marking STALE
- `STALE_DATASOURCE_NO_UPDATE_DAYS` (int, default `120`) — fallback threshold when no usage metrics exist (defined but unused in current service — pre-existing drift)
- `STALE_DATASOURCE_GRACE_DAYS` (int, default `7`) — newly created datasources are immune
- `STALE_DATASOURCE_BATCH_SIZE` (int, default `100`) — ES composite aggregation page size

New variables to add in the same config block:
- `STALE_DATASOURCE_DELETION_ENABLED` (bool, default `False`) — deletion phase sub-flag; checked inside `StaleDatasourceService`, not used as a second scheduler toggle
- `STALE_DATASOURCE_MAX_DELETIONS_PER_RUN` (int, default `100`) — circuit-breaker cap

### Configuration Files

- `src/codemie/configs/config.py` — all `STALE_DATASOURCE_*` settings; `finalize_settings` validator at line 864
- `src/codemie/service/stale_datasource/config.py` — `STALE_DATASOURCE_LOCK_ID = 987654324`
- `deploy-templates/values.yaml` — Helm chart env declarations; none of the `STALE_DATASOURCE_*` vars are present yet and must be added

### Feature Flags and Deployment Concerns

- Feature flag pattern: plain `bool = False` Pydantic field in `AppConfig`; no registry.
- **Lock ID collision (must fix)**: `STALE_DATASOURCE_LOCK_ID = 987654324` in `stale_datasource/config.py` collides with `_SPEND_TRACKING_RESET_RECONCILIATION_LOCK_ID = 987654324` in `spend_tracking/scheduler.py`. The deletion phase PR must reassign `STALE_DATASOURCE_LOCK_ID` to an unused value (e.g. `987654326`) to avoid silent lock contention between the two nightly jobs.
- **`deploy-templates/values.yaml` gap**: neither the existing `STALE_DATASOURCE_ENABLED` nor the two new deletion vars appear in the Helm chart; all must be added with safe defaults before deployment.
- **`finalize_settings` guard**: consider raising `ValueError` if `STALE_DATASOURCE_DELETION_ENABLED=True` and `STALE_DATASOURCE_ENABLED=False` (deletion phase has no scheduler to trigger it in that combination).
- No database migration needed — `lifecycle_state`, `marked_stale_at`, and the `ARCHIVED` PG enum value are all already present from migration `f9g0h1i2j3k4`.

---

## 6. Risk Indicators

- **Lock ID collision**: `STALE_DATASOURCE_LOCK_ID = 987654324` in `src/codemie/service/stale_datasource/config.py` collides with `_SPEND_TRACKING_RESET_RECONCILIATION_LOCK_ID = 987654324` in the spend tracking scheduler. Both jobs run nightly. Without a fix, one job silently fails to acquire the lock and skips its run. This must be resolved in this PR.
- **`IndexInfo.delete()` must not be called**: the method at line 1253 of `src/codemie/rest_api/models/index.py` also deletes GitRepo rows and removes scheduler/webhook integrations. Using it for the stale deletion phase would silently destroy live integrations. The ES deletion must be performed directly via `ElasticSearchClient.get_client().indices.delete(index=name, ignore_unavailable=True)`.
- **`complete_progress()` reactivation implementation risk**: `_build_progress_update_kwargs` does not accept `lifecycle_state` or `marked_stale_at`; adding the reactivation hook requires either extending this internal method or placing a separate targeted `sa_update` after `update_progress()`. Incorrect placement (e.g. writing fields that `update_progress` then overwrites) could silently discard the reactivation.
- **`deploy-templates/values.yaml` missing all stale vars**: the `STALE_DATASOURCE_ENABLED` master toggle is also absent, meaning this entire feature has never been deployed via Helm. The PR must add all vars with safe defaults; otherwise `STALE_DATASOURCE_DELETION_ENABLED=True` cannot be deployed to any environment.
- **No existing tests for deletion phase**: `_delete_stale_indexes()`, the circuit-breaker path, the shared-index guard, and the STALE → ARCHIVED transition all have zero test coverage. The reactivation hook in `complete_progress()` is exercised by existing tests but those tests do not assert `lifecycle_state` or `marked_stale_at` values.
- **`asyncio.to_thread` wrapping**: sync ES client calls inside an async service must be wrapped; omitting the wrap causes event loop blocking. The pattern is established in the project but not currently used in this service file.
- **Pre-existing dead counter**: `already_stale` stat in `stale_datasource_service.py` at line 147 can never increment; not a blocker but adds noise to stats logging and should be noted.
- **`STALE_DATASOURCE_NO_UPDATE_DAYS` drift**: defined in config, referenced in spec, but not used in current service implementation. Downstream complexity assessor should note this as a requirements clarity gap — spec may be describing a future state.
- **Shared-index guard correctness**: the guard is computed in-memory against ACTIVE rows. If an ACTIVE row shares an index with a STALE row due to legacy KB naming, the STALE row must remain STALE indefinitely until the ACTIVE row is itself stale. The spec documents this but edge-case test coverage (e.g. the last ACTIVE sharing that index becomes STALE in the same run) is absent.

---

## 7. Summary for Complexity Assessment

This task adds a deletion phase to an existing nightly service, touching six distinct layers: Config (2 new fields), Model (`IndexInfo.complete_progress()` reactivation hook), Service (`StaleDatasourceService._delete_stale_indexes()` new method), Scheduler (stats logging), ES Client (sync `asyncio.to_thread` call), and Monitoring (one new metric). Estimated file change surface is 5–7 files: `src/codemie/configs/config.py`, `src/codemie/rest_api/models/index.py`, `src/codemie/service/stale_datasource/stale_datasource_service.py`, `src/codemie/service/stale_datasource/config.py` (lock ID fix), `src/codemie/service/stale_datasource/scheduler.py` (stats logging), `deploy-templates/values.yaml` (Helm entries), and `tests/codemie/service/stale_datasource/test_stale_datasource_service.py` (new tests). No database migration is required.

The task follows established patterns closely. The deletion service method follows the exact structure of the detection method already in `StaleDatasourceService`. Config flags follow the existing bool=False pydantic pattern. ES sync wrapping via `asyncio.to_thread` is a documented project convention. The one area with mild novelty is the `IndexInfo.complete_progress()` reactivation hook: `_build_progress_update_kwargs` currently does not accept lifecycle fields, so the implementer must extend it or add a second targeted SQL update — a small but non-trivial model modification that must be done carefully to avoid silently discarding the lifecycle reset.

Test coverage posture is mixed: the detection phase has solid service-level unit tests with mocked dependencies, providing a good scaffold to follow for deletion-phase tests. However, the deletion phase itself, the circuit-breaker abort path, the shared-index guard, and the reactivation hook in `complete_progress()` are all untested. The existing `test_complete_progress` asserts only `completed=True` and `is_fetching=False` and will not catch a missing lifecycle reset. Two mandatory risk items require action before merge: the advisory lock ID collision with the spend-tracking reconciliation job (`987654324` is used by both), and the missing `deploy-templates/values.yaml` entries for all `STALE_DATASOURCE_*` variables including the existing detection toggle.
