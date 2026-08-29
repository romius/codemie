# Technical Research

**Task**: deployment versioning release-notes environment-scoping
**Generated**: 2026-08-03T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Add backend support for storing and exposing deployment timestamps per environment + version. Implement data layer (store timestamps from deployment pipeline), API layer (endpoint with environment filtering), and cross-environment filtering logic.

**Refined requirements (from clarification):**
1. Store the deployment date scoped only to the current environment (not multi-env write).
2. The timestamp must be recorded automatically on service startup — specifically, if no record exists for the current `APP_VERSION` in the current `ENV`, insert a new record with `deployed_at = now()`. This captures "first start of this version in this environment."
3. The `deployed_at` field must be returned as a new additional field in the JSON response of the existing "release notes" / version-info endpoint (`GET /v1/info`).

---

## 2. Codebase Findings

### Existing Implementations

**"Release notes" endpoint — the mutation target:**
- `src/codemie/rest_api/routers/common.py` — `GET /v1/info` returns `InfoResponse(message, version, description)`. This is the only existing endpoint that exposes version information. It is public (no auth dependency on the router), synchronous, and reads `config.APP_VERSION` at call time.
- `src/codemie/core/models.py` lines 762–764 — `InfoResponse(BaseResponse)` with fields `version: str` and `description: str`. This model must gain `deployed_at: datetime | None = None`.

**Startup lifecycle — where the auto-record hook belongs:**
- `src/codemie/rest_api/main.py` line 326 — `_initialize_database_and_defaults()` runs Alembic migrations and creates default app data. This function is called synchronously at line 640 inside the `@asynccontextmanager lifespan(app)` function, before any request is served. A new `_record_deployment_version()` call should be added here (or to a new `_initialize_deployment_version()` helper called from `lifespan`).
- Pattern: other startup initializations follow the same isolated helper function pattern (`_initialize_preconfigured_content()`, `_initialize_optional_features()`, `_check_sharepoint_pkce_redis()`).

**Config values that define the record key:**
- `src/codemie/configs/config.py` line 50 — `APP_VERSION: str = "0.16.0"` (overridable via env var).
- `src/codemie/configs/config.py` line 51 — `ENV: str = "local"` (overridable via env var). This is the environment discriminator used throughout the codebase for OTEL, Pyroscope, and Langfuse.

**No existing deployment-version domain:**
Searches for `release_note`, `ReleaseNote`, `deployment_timestamp`, `deployed_at`, `deploy_time`, `DeploymentVersion`, and `EnvironmentVersion` returned zero results in `src/`. This is a fully greenfield table and service.

**Closest structural precedent — ActivityEvent (append-only table):**
- `src/codemie/service/activity/activity_models.py` — `ActivityEvent` (table=True, `activity_events`): `domain`, `event_type`, `entity_type`, `entity_id`, `actor_id`, `attributes` (JSONB), `created_at` (TIMESTAMP with timezone, `server_default=now()`). Append-only, no UPDATE path.
- `src/codemie/service/activity/activity_repository.py` — Abstract ABC `ActivityEventRepository` + concrete `SQLActivityEventRepository`. Both sync `insert(event, session)` and async `async_insert(event, session)` accept a caller-provided session. This is the recommended pattern.
- `src/external/alembic/versions/38255069bfab_create_activity_events_table.py` — Uses `sa.TIMESTAMP(timezone=True)`, `server_default=sa.text("now()")`, three composite indexes. Direct migration template.

**Versioning precedent — AssistantConfiguration:**
- `src/codemie/rest_api/models/assistant.py` — `AssistantConfiguration` (table=True): `version_number: int`, `created_date: datetime`, `created_by` (JSONB). Establishes the version-number + timestamp pattern for a versioned table.
- `src/external/alembic/versions/772de5bd70b1_add_versioning_for_assistants.py` — Migration template with composite unique index on `(assistant_id, version_number)`.

**Current Alembic migration head:**
- `src/external/alembic/versions/i1n2t3e4r5a6_add_interactive_features_to_assistants.py` — revision `i1n2t3e4r5a6`. Nothing currently uses this as a `down_revision`. The new migration must set `down_revision = "i1n2t3e4r5a6"`.

**Base model and pagination infrastructure:**
- `src/codemie/rest_api/models/base.py` — `BaseModelWithSQLSupport`, `PaginatedListResponse[T]`, `PaginationData`, `PydanticType`.
- `src/codemie/core/models.py` line 826 — `SYSTEM_USER = CreatedByUser(id="00000000-...", username="system")` — should be used as the actor for the startup-recorded deployment entry.

### Architecture and Layers Affected

| Layer | File | Change type |
|---|---|---|
| DB Persistence | `src/external/alembic/versions/<hash>_add_deployment_versions_table.py` (new) | New migration |
| DB Persistence | `src/external/alembic/env.py` | Import new model |
| Data Model | `src/codemie/rest_api/models/deployment_version.py` (new) | New SQLModel table class + Pydantic DTO |
| Repository | `src/codemie/service/deployment/deployment_version_repository.py` (new) | ABC + SQL implementation |
| Service | `src/codemie/service/deployment/deployment_version_service.py` (new) | Record-on-first-start + query logic |
| Startup | `src/codemie/rest_api/main.py` | Add `_initialize_deployment_version()` call in `lifespan()` |
| API Response Model | `src/codemie/core/models.py` | Add `deployed_at: datetime | None = None` to `InfoResponse` |
| API Router | `src/codemie/rest_api/routers/common.py` | Populate `deployed_at` from service in `app_info()` |

Total: 3 new files, 4 file modifications. The `/v1/info` endpoint itself does not change its route or auth; only its response model gains a field.

### Integration Points

**Internal dependencies for new code:**
- `src/codemie/configs/config.py` — `config.APP_VERSION` and `config.ENV` as the composite key for lookup/insert.
- `src/codemie/clients/postgres.py` — `get_session()` (sync) for the startup record insert and the `app_info()` query. Startup runs before async is established in some paths; sync session is the safe choice.
- `src/codemie/core/models.py` — `SYSTEM_USER` as the actor for the auto-inserted record.
- `src/external/alembic/env.py` — must import the new model class for `target_metadata`.
- `src/codemie/rest_api/main.py` — `lifespan()` startup hook.

**No new external service dependencies**: The feature is entirely internal. No pipeline HTTP webhook, no new third-party library, no cloud provider call.

### Patterns and Conventions

**SQLModel table class (follow ActivityEvent):**
```python
class DeploymentVersion(SQLModel, table=True):
    __tablename__ = "deployment_versions"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    version: str = Field(index=True)
    environment: str = Field(index=True)
    deployed_at: datetime = Field(
        sa_column=Column(TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False)
    )
    # Composite unique constraint — only one record per (version, environment)
    __table_args__ = (UniqueConstraint("version", "environment", name="uq_deployment_versions_version_env"),)
```

**Repository pattern (follow ActivityEventRepository):**
```python
class DeploymentVersionRepository(ABC):
    @abstractmethod
    def get_by_version_and_env(self, version: str, environment: str, session: Session) -> DeploymentVersion | None: ...
    @abstractmethod
    def insert(self, version: str, environment: str, session: Session) -> DeploymentVersion: ...

class SQLDeploymentVersionRepository(DeploymentVersionRepository): ...

deployment_version_repository: DeploymentVersionRepository = SQLDeploymentVersionRepository()
```

**Service (classmethod-only, follow AssistantVersionService):**
```python
class DeploymentVersionService:
    @classmethod
    def record_if_absent(cls) -> DeploymentVersion | None:
        """Called at startup. Insert (APP_VERSION, ENV) if no record exists."""
        with get_session() as session:
            existing = deployment_version_repository.get_by_version_and_env(
                config.APP_VERSION, config.ENV, session
            )
            if existing:
                return existing
            return deployment_version_repository.insert(config.APP_VERSION, config.ENV, session)

    @classmethod
    def get_deployed_at(cls) -> datetime | None:
        """Called by /v1/info handler. Returns deployed_at for (APP_VERSION, ENV)."""
        with get_session() as session:
            record = deployment_version_repository.get_by_version_and_env(
                config.APP_VERSION, config.ENV, session
            )
            return record.deployed_at if record else None
```

**InfoResponse extension (additive, non-breaking):**
```python
class InfoResponse(BaseResponse):
    version: str
    description: str
    deployed_at: Optional[datetime] = None  # New field — None if record absent
```

**Router update (common.py):**
```python
@router.get("/info", status_code=status.HTTP_200_OK, response_model=InfoResponse)
def app_info():
    from codemie.service.deployment.deployment_version_service import deployment_version_service
    return InfoResponse(
        message="Codemie",
        version=config.APP_VERSION,
        description=APP_DESCRIPTION,
        deployed_at=deployment_version_service.get_deployed_at(),
    )
```

**Startup hook (main.py lifespan):**
```python
def _initialize_deployment_version():
    """Record first-start timestamp for (APP_VERSION, ENV) if not already stored."""
    from codemie.service.deployment.deployment_version_service import deployment_version_service
    try:
        deployment_version_service.record_if_absent()
    except Exception as exc:
        logger.warning(f"Failed to record deployment version: {exc}")
        # Non-fatal — app continues; deployed_at will be None on /v1/info

# Inside lifespan(), after _initialize_database_and_defaults():
_initialize_deployment_version()
```

---

## 3. Documentation Findings

### Guides and Architecture Docs

| Guide | Relevance to this task |
|---|---|
| `.ai-run/guides/architecture/layered-architecture.md` | Mandatory — router/service/repository contract; startup ownership rules |
| `.ai-run/guides/architecture/project-structure.md` | Mandatory — file placement; "narrowest existing package" rule |
| `.ai-run/guides/architecture/service-layer-patterns.md` | Mandatory — service boundary; classmethod-only pattern |
| `.ai-run/guides/data/database-patterns.md` | Mandatory — Alembic migration required; no runtime schema creation |
| `.ai-run/guides/data/repository-patterns.md` | Mandatory — repository returns domain entities; ABC + impl split |
| `.ai-run/guides/api/rest-api-patterns.md` | Required — `ExtendedHTTPException`; auth dependency |
| `.ai-run/guides/api/endpoint-conventions.md` | Required — typed response models in `rest_api/models/` |
| `.ai-run/guides/development/configuration-patterns.md` | Required — all settings through `config.py` |
| `.ai-run/guides/development/error-handling.md` | Required — non-fatal startup failure should `logger.warning`, not raise |

### Architectural Decisions

- **Append-only table**: Activity events migration uses `TIMESTAMP(timezone=True)` + no UPDATE path. Deployment version records are append-only by definition (first-start timestamp never changes).
- **Unique constraint on (version, environment)**: Prevents duplicate startup-time inserts under concurrent workers or rolling restarts. The service should use `INSERT ... ON CONFLICT DO NOTHING` or check-then-insert with session-scoped locking; or rely on the DB unique constraint + catch `IntegrityError` gracefully.
- **Alembic mandatory for persistent tables**: New `deployment_versions` table requires a migration with `down_revision = "i1n2t3e4r5a6"`.
- **Non-fatal startup failure**: Following the pattern of `_check_sharepoint_pkce_redis()` in `main.py`, if the deployment version record cannot be written (DB unavailable at startup edge case), the service should log a warning and the app should continue. `app_info()` returns `deployed_at=None` gracefully.
- **`InfoResponse` extension is additive**: Adding `deployed_at: Optional[datetime] = None` to `InfoResponse` is backward-compatible — existing callers receive `null` for the new field if the record is absent, and existing serialization is unaffected.
- **Sync session for startup**: `_initialize_deployment_version()` runs inside the synchronous part of `lifespan()` (before the `yield`). Use `get_session()` (sync) not `get_async_session()`.

### Derived Conventions

- Helper functions in `main.py` follow the `_initialize_<domain>()` naming convention and are called from `lifespan()` in dependency order.
- Service subdirectory pattern: `src/codemie/service/deployment/` with `__init__.py`, `deployment_version_repository.py`, and `deployment_version_service.py`.
- Model file in `src/codemie/rest_api/models/deployment_version.py` for the SQLModel table class and any Pydantic DTOs.
- Singleton export: `deployment_version_service = DeploymentVersionService()` or classmethod-only (no instantiation), consistent with `activity_event_service = ActivityEventService()` singleton pattern.
- `datetime.now(UTC)` for Python-side timestamps; `server_default=sa.text("now()")` for DB-side default in migration.

---

## 4. Testing Landscape

### Existing Coverage

No existing tests cover deployment versioning, deployment timestamps, or environment-scoped version history. Zero test files match any of these keywords.

The reference test patterns for new code:
- `tests/codemie/service/activity/test_activity_repository.py` — repository tests injecting `MagicMock` session, asserting `session.add`/`session.flush`.
- `tests/codemie/service/activity/test_activity_event_service.py` — service tests patching the repository singleton + `Session` + `PostgresClient`.
- `tests/codemie/rest_api/routers/test_dynamic_config_router.py` — router tests: bare `FastAPI()` + `app.dependency_overrides` + `httpx.AsyncClient` + `ASGITransport`.

### Testing Framework and Patterns

- **Framework**: pytest 8.3.1 + pytest-asyncio 0.23.7; `@pytest.mark.anyio` + `anyio_backend = 'asyncio'` for async router tests.
- **Mocking**: `unittest.mock` (`MagicMock`, `AsyncMock`, `patch`); `pytest-mock` 3.14.0.
- **DB isolation**: global `autouse` fixture in `tests/conftest.py` patches `PostgresClient.get_engine` to `MagicMock`.
- **Router test pattern**: `app = FastAPI(); app.include_router(router); async with AsyncClient(app=app, ...) as client: resp = await client.get("/v1/info")`.

### Coverage Gaps

All of the following are net-new and require tests:

1. **Repository** (`SQLDeploymentVersionRepository`):
   - `get_by_version_and_env()` — record exists path; record absent path (returns `None`).
   - `insert()` — asserts `session.add` and `session.flush` called; returned object has correct `version`, `environment` fields.

2. **Service** (`DeploymentVersionService`):
   - `record_if_absent()` — when no record exists: patches repository, asserts `insert()` called.
   - `record_if_absent()` — when record exists: asserts `insert()` NOT called.
   - `get_deployed_at()` — record exists: returns `deployed_at` datetime.
   - `get_deployed_at()` — no record: returns `None`.

3. **Router** (`GET /v1/info`):
   - Happy path: `deployed_at` present — response JSON contains `deployedAt` (camelCase alias) with a datetime value.
   - `deployed_at` absent (service returns `None`) — response JSON contains `"deployedAt": null`.

4. **InfoResponse model**:
   - Pydantic instantiation with `deployed_at=None` — field present, value `None`.
   - Pydantic instantiation with a datetime — field serializes correctly.

---

## 5. Configuration and Environment

### Environment Variables

| Variable | Default | Role in this feature |
|---|---|---|
| `APP_VERSION` | `"0.16.0"` | Part of the composite key `(version, environment)` for the deployment record. Injected by the deployment pipeline at container start via env var; pydantic-settings picks it up automatically. |
| `ENV` | `"local"` | The other half of the composite key. Values observed in use: `"local"`, `"dev"`, `"prod"` (from `Environment` enum in `constants.py`). |
| `PG_URL` / `POSTGRES_HOST` | varies | PostgreSQL target for the new `deployment_versions` table. Same database and schema (`codemie`) as all other tables. |

No new environment variables are required for this feature.

### Configuration Files

- `src/codemie/configs/config.py` — No changes needed. `APP_VERSION` and `ENV` are already pydantic-settings fields injectable at deploy time.
- `src/external/alembic/versions/<hash>_add_deployment_versions_table.py` — New file; `down_revision = "i1n2t3e4r5a6"`.
- `src/external/alembic/env.py` — Add `from codemie.rest_api.models.deployment_version import DeploymentVersion` (or equivalent) so `target_metadata` includes the new table.

### Feature Flags and Deployment Concerns

- **No new feature flag required**: The deployment-version record is a core infrastructure concern, always enabled. Unlike `ACTIVITY_EVENTS_ENABLED`, there is no reason to make this conditional — it is small, low-risk, and needed for the `/v1/info` response. If desired, a `DEPLOYMENT_VERSION_TRACKING_ENABLED: bool = True` flag could gate the startup call, but this adds complexity without clear benefit.
- **Race condition on rolling restarts**: If multiple worker processes start simultaneously, all will attempt `record_if_absent()` at the same time. The unique constraint on `(version, environment)` plus an `IntegrityError` catch in the service protects against duplicate inserts. This must be handled explicitly.
- **Pipeline injection of APP_VERSION**: This is the only mechanism for version injection. The deployment pipeline must set `APP_VERSION=<semver>` in the container environment before uvicorn starts. No code change is needed; pydantic-settings handles it.
- **No multi-environment data**: Per refined requirements, each running instance stores only its own `(APP_VERSION, ENV)` pair. There is no cross-environment query or history endpoint required.

---

## 6. Risk Indicators

- **Race condition on concurrent startup**: Multiple uvicorn workers or rolling-restart pods will all call `record_if_absent()` simultaneously on first deploy. The unique constraint on `(version, environment)` prevents duplicate rows, but an unhandled `IntegrityError` at startup would crash the process. The service must catch `sqlalchemy.exc.IntegrityError` and treat it as a non-error (another worker already inserted).
- **Startup failure must be non-fatal**: If PostgreSQL is briefly unavailable at startup (network timing), `_initialize_deployment_version()` must not raise and must not prevent app startup. `deployed_at` being `None` on `/v1/info` is acceptable until the next restart.
- **`InfoResponse` camelCase alias**: `InfoResponse` extends `BaseResponse` which extends `ConfiguredModel` — but checking `core/models.py` line 762, `InfoResponse(BaseResponse)` and `BaseResponse(ConfiguredModel)` where `ConfiguredModel` has `alias_generator=to_camel`. This means the new `deployed_at` field will serialize as `"deployedAt"` in JSON output. Frontend callers must be informed of this alias. Tests must assert against the camelCase key.
- **`APP_VERSION` is not pipeline-injected today**: The current default is hardcoded at `"0.16.0"` in `config.py`. Without pipeline injection, every environment will record the same static default version. The deployment pipeline must inject the correct version string for this feature to be meaningful. This is an operational gap, not a code gap.
- **New migration must chain correctly**: The current head is `i1n2t3e4r5a6`. Any error in setting `down_revision` will break the Alembic migration chain and prevent startup.
- **`alembic/env.py` import required**: Forgetting to import the new `DeploymentVersion` class into `src/external/alembic/env.py` will cause `alembic check` to not detect the new table in `target_metadata`. The table will still be created by the explicit `op.create_table()` call, but autogenerate comparisons will be incorrect.
- **No test coverage for this domain**: Entirely greenfield — all layers need tests written from scratch. Reference patterns are clear (see Section 4).
- **Existing `/v1/info` is unauthenticated**: Adding `deployed_at` to the public info endpoint leaks deployment timing information to unauthenticated callers. This is a minor information-disclosure concern that should be reviewed for compliance requirements.
- **codegraph not indexed**: MCP codegraph tool was unavailable; all research was filesystem-based. Files added after the last git commit may have been missed.

---

## 7. Summary for Complexity Assessment

This task is a **focused, well-scoped addition** that augments one existing endpoint (`GET /v1/info`) with one new field (`deployed_at`) backed by a new single-row-per-environment table. The change surface is small: one new Alembic migration, one new SQLModel table class, one new repository module (ABC + SQL impl), one new service module, one modified startup hook in `main.py`, and two small modifications to existing files (`InfoResponse` in `core/models.py` and `app_info()` in `routers/common.py`). All seven touch points follow well-established patterns that have direct templates in the codebase — the `ActivityEvent` domain (migration + ABC repository + service) and the `AssistantVersionService` (classmethod-only service) are near-perfect blueprints.

The task follows **established patterns throughout** — no new frameworks, no new external dependencies, no novel architectural patterns. The only technically non-trivial element is the race condition handling for concurrent startup: the service must catch `IntegrityError` from the unique constraint rather than naively assuming the check-then-insert is atomic. This is a known PostgreSQL pattern and is handled with a single `try/except IntegrityError` block. The startup-failure tolerance (non-fatal `logger.warning`) also requires deliberate design, but follows the precedent set by `_check_sharepoint_pkce_redis()` in `main.py`.

**Test coverage posture**: entirely absent for this domain, but the infrastructure is mature. Three reference test patterns (`test_activity_repository.py`, `test_activity_event_service.py`, `test_dynamic_config_router.py`) provide copy-and-adapt templates for all three new layers. Key risk factors for complexity scoring: (1) race condition handling at startup is the single non-trivial implementation concern; (2) the unauthenticated exposure of `deployed_at` on `/v1/info` needs a security review; (3) the `APP_VERSION` pipeline injection gap is an operational prerequisite that falls outside the code boundary but is necessary for the feature to deliver value.
