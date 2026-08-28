# Technical Research

**Task**: workflow pool sub-workflow compilation
**Generated**: 2026-07-30T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-11609 Task 4 — WorkflowPool service: implement a singleton pre-compilation pool for sub-workflow CompiledStateGraph instances. Split WorkflowExecutor._init_workflow() into _compile_graph() (user-agnostic, uses NodeSlot placeholders) and _inject_user_context() (user-dependent, populates slots at execution time). WorkflowPool manages a thread-safe deque per (workflow_id, config_version_hash), a background watcher daemon that pre-warms the pool, startup hook in the FastAPI lifespan, and SubWorkflowNode.execute() acquire/release integration. Two new env vars: SUBWORKFLOW_POOL_MAX_SIZE (default 5) and SUBWORKFLOW_POOL_WARMUP_INTERVAL_SECONDS (default 60). NodeSlot file and config vars are already written as pre-work. See /home/user/projects/codemie/tmp/jira/EPMCDME-11609/task4.md for the full spec.

---

## 2. Codebase Findings

### Existing Implementations

Pre-work already committed to the current branch (EPMCDME-11609_sub-workflow-node):

- `src/codemie/workflows/nodes/node_slot.py` — `NodeSlot`: fully implemented callable placeholder; `set_delegate()` / `clear()` / `__call__()` interface is ready; docstring at lines 19–25 explicitly names `WorkflowPool` as the consumer and documents `_inject_user_context()` semantics; no further changes needed.
- `src/codemie/core/workflow_models/workflow_models.py` — `WorkflowPoolConfig` at line 361: `enabled` (default `False`), `min_size` (1–20), `max_size` (1–50), `refill_interval_seconds` (≥5) fields already defined and exported from `core/workflow_models/__init__.py`.
- `src/codemie/core/workflow_models/workflow_config.py` — `WorkflowConfigBase` / `WorkflowConfig`: `pool_config` column (line 115–117) wired as nullable JSONB; `parse_execution_config()` already reads and populates `pool_config` from YAML (lines 259–260). Alembic migration `t1u2v3w4x5y6_add_sub_workflow_pool_config` already applied.
- `src/codemie/configs/config.py` — `SUBWORKFLOW_POOL_MAX_SIZE` (default 5, line 506) and `SUBWORKFLOW_POOL_WARMUP_INTERVAL_SECONDS` (default 60, line 507) already declared. Also: `WORKFLOW_POOL_ENABLED` (default `True`, line 172), `WORKFLOW_POOL_MAX_AGE_SECONDS` (default 3600, line 173).

Primary target files requiring new work:

- `src/codemie/workflows/workflow.py` — `WorkflowExecutor`: `_init_workflow()` is a single-pass method (lines ~345–357) that builds `StateGraph`, adds node objects (user-dependent), adds edges, and calls `.compile(interrupt_before=..., checkpointer=...)`. Must be split into `_compile_graph()` (user-agnostic skeleton) and `_inject_user_context()` (user-dependent injection). `_init_workflow()` becomes a thin orchestrator calling both in sequence.
- `src/codemie/workflows/nodes/sub_workflow_node.py` — `SubWorkflowNode.execute()`: currently calls `WorkflowExecutor.create_executor()` + `.stream()` directly with no pool interaction. Both create-path and resume-path are present; pool should only apply to the create-path.
- `src/codemie/rest_api/main.py` — `_initialize_optional_features()` (line 345): correct hook for `WorkflowPool.initialize()`. Background warmup (interval-driven) should follow the `_setup_conversation_analysis_scheduler()` pattern using `APScheduler.AsyncIOScheduler`, stored as `app.state.<name>_scheduler`, stopped in `_shutdown_services()`.
- `src/codemie/service/workflow_pool.py` — new file; does not yet exist.

Architectural precedent:

- `src/codemie/datasource/loader/file_processor_pool.py` — `FileProcessPoolManager`: singleton via `__new__`; `initialize()` / `get_executor()` / `shutdown()` lifecycle; module-level instance + `atexit.register(cls.shutdown)`; `threading.Lock` (`_reinit_lock`) protecting reinitialization; module-level instance exported for import by callers.

### Architecture and Layers Affected

| Layer | Component | Change |
|---|---|---|
| Config | `src/codemie/configs/config.py` | Read-only: env vars already present as pre-work |
| Model | `src/codemie/core/workflow_models/workflow_models.py` | Read-only: `WorkflowPoolConfig` already exists |
| Model | `src/codemie/core/workflow_models/workflow_config.py` | Read-only: `pool_config` column already exists |
| Service (new) | `src/codemie/service/workflow_pool.py` | New file: `WorkflowPool` singleton |
| Workflow Execution | `src/codemie/workflows/workflow.py` | Split `_init_workflow()` into `_compile_graph()` + `_inject_user_context()` |
| Node | `src/codemie/workflows/nodes/sub_workflow_node.py` | Replace direct executor call with pool acquire/release on create-path |
| API Startup | `src/codemie/rest_api/main.py` | Wire `WorkflowPool.initialize()` into `_initialize_optional_features()`; add warmup scheduler |

### Integration Points

Internal module dependencies (new connections introduced by this task):

- `sub_workflow_node` → `WorkflowPool.acquire()` / `.release()` (new; replaces direct `WorkflowExecutor.create_executor()`)
- `WorkflowPool` → `WorkflowExecutor._compile_graph()` (user-agnostic compilation)
- `WorkflowPool` → `WorkflowService` (DB query for all `enable_pool=True` workflows in the warmup watcher)
- `rest_api/main.py._initialize_optional_features` → `WorkflowPool.initialize()` (startup)
- `rest_api/main.py._setup_*_scheduler` → warmup job referencing `WorkflowPool._refill()` (background)

Existing guard already present that gates all pool activation:

- `ENABLE_SUB_WORKFLOW_NODE` (global kill-switch): checked in `workflow.py:515`, `sub_workflow_node.py:66`, `routers/workflow.py:121`, `validation/resources.py:619`. Pool must also be gated behind this flag.
- `WORKFLOW_POOL_ENABLED` (pool-specific kill-switch, default `True`): both flags must be `True` for pool activation.

External integration: none introduced by this task. Pool operates entirely in-process memory; `CompiledStateGraph` is not JSON-serializable and must not be passed to Redis or shared cache.

### Patterns and Conventions

- **Singleton via `__new__`**: `_instance = None` class attribute; `__new__` returns existing instance if set. Example: `FileProcessPoolManager`.
- **Module-level instance export**: `workflow_pool = WorkflowPool()` at the bottom of `workflow_pool.py`; imported by name in `main.py` and `sub_workflow_node.py`.
- **Lifecycle: `initialize()` / `shutdown()`**: `initialize()` guards with `if self._initialized: return`; `shutdown()` registered via `atexit.register(workflow_pool.shutdown)`.
- **Thread-safety**: `threading.Lock` per pool key (or a single lock over the pool dict); `collections.deque` for the queue of compiled graphs per key.
- **Pool key**: `(workflow_id, config_version_hash)` where `config_version_hash = hashlib.sha256(workflow_config.yaml_config.encode()).hexdigest()[:16]`.
- **Pool size ceiling**: `min(workflow_config.pool_config.max_size, settings.SUBWORKFLOW_POOL_MAX_SIZE)`.
- **Feature flag kill-switch pattern**: `if not config.WORKFLOW_POOL_ENABLED or not config.ENABLE_SUB_WORKFLOW_NODE: return` at top of activation path.
- **Background warmup**: APScheduler `AsyncIOScheduler` with `"interval"` trigger (seconds = `SUBWORKFLOW_POOL_WARMUP_INTERVAL_SECONDS`); stored as `app.state.workflow_pool_scheduler`; stopped in `_shutdown_services()`.
- **Lazy import to avoid circular**: `SubWorkflowNode` is already lazily imported in `workflow.py:init_sub_workflow_node()`. `WorkflowPool` import in `sub_workflow_node.py` must follow the same pattern.
- **`interrupt_before` / `CheckpointSaver` is workflow-config-level**: Determined by `workflow_config.states`, not by user context — safe to fix at `_compile_graph()` time. However, a pooled graph compiled with a `MemorySaver` checkpointer must have its checkpoint state cleared before reuse.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/workflows/langgraph-workflows.md` — workflow execution patterns; extend `WorkflowExecutor` and nodes; never bypass the execution path or create parallel graph runners; governs how `_init_workflow()` split must integrate.
- `.ai-run/guides/architecture/service-layer-patterns.md` — services are feature-scoped under `src/codemie/service/`; orchestration belongs in service methods; confirms `workflow_pool.py` placement.
- `.ai-run/guides/architecture/layered-architecture.md` — cross-cutting exceptions/config/constants go in `codemie.core`; `WorkflowPoolConfig` already follows this (it lives in `core/workflow_models/`).
- `.ai-run/guides/development/configuration-patterns.md` — use central `src/codemie/configs/config.py`; no direct `os.environ` reads in feature code; env vars are already added following this convention.
- `docs/superpowers/tasks/2026-07-29-epmcdme-11609-sub-workflow-node/spec.md` — parent story spec; confirms pool is task4 scope (explicitly deferred from increments 2a/2b/2c); pool DB schema confirmed as hybrid `pool_config JSONB` + `max_nesting_level INTEGER`.

### Architectural Decisions

- Pool entries are **in-process only**: each pod warms its own pool independently; no shared-memory or Redis-based compiled graph cache (CompiledStateGraph not serializable).
- Pool is **opt-in per workflow** via `pool_config.enabled` (default `False`); global kill-switch `WORKFLOW_POOL_ENABLED` overrides all.
- **CONFLICT requiring clarification**: `docs/superpowers/.../spec.md` describes pool entry lifecycle as "single-use entries; checked out, used, discarded (not returned)" — but `task4.md` specifies `release()` returns the graph to the pool. These contradict each other. The task4.md spec is the authoritative source for this task; the spec.md may document an earlier design iteration. The task4.md path (return to pool, reset checkpoint state before reuse) is the implementation target. This should be confirmed with the ticket author before implementation.
- `WorkflowMode.AUTONOMOUS` routes to `SupervisorWorkflowExecutor` (a separate class). `task4.md` does not address this. Pool should be gated to `WorkflowMode != AUTONOMOUS` until explicitly scoped.

### Derived Conventions

- `_initialize_optional_features()` is the conventional hook for feature-gated service initialization at startup; all new optional services follow the `if config.FLAG: from module import service; service.initialize()` pattern.
- `atexit.register(instance.shutdown)` is called inside `initialize()` to ensure cleanup on all exit paths.
- Thread-safety: `threading.Lock()` as a class attribute; acquired on every `acquire()` and `release()` call; deque operations performed inside the lock.
- Warmup scheduler stored as `app.state.workflow_pool_scheduler` and explicitly stopped in the lifespan teardown block (matches `conversation_analysis_scheduler` and `stale_datasource_scheduler` patterns).

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/workflows/nodes/test_sub_workflow_node.py` — 11 tests covering `SubWorkflowNode.execute()`: feature flag guard, nesting depth, input_mapping Jinja rendering, full context pass-through, happy/fail/interrupt paths, `active_sub_execution_id` lifecycle, create vs. resume paths. All tests mock `WorkflowExecutor.create_executor` entirely — none exercise a pool path.
- `tests/codemie/workflows/test_workflow_state_transitions.py` — `WorkflowExecutor` state transitions and `TestSubWorkflowDispatch` class testing `initialize_node` dispatch when flag enabled/disabled. `_init_workflow()` exercised only indirectly through integration-style setup.
- `tests/codemie/workflows/test_supervisor_workflow_executor.py` — `SupervisorWorkflowExecutor` (unittest.TestCase style, older patterns).
- `tests/codemie/datasource/loader/test_file_processor_pool.py` — **closest structural analog for WorkflowPool tests**: covers inline fallback, broken-pool reinit/retry, exception propagation; uses `autouse` fixture to reset `_initialized`, `_executor`, `_instance` class attributes between tests. This is the exact reset pattern to replicate for `WorkflowPool` test isolation.
- `tests/codemie/core/workflow_models/test_workflow_models.py` — `WorkflowPoolConfig` model validation: defaults, `min_size`/`max_size`/`refill_interval_seconds` bounds via `pydantic.ValidationError`. Already covers the model pre-work.
- `tests/codemie/service/workflow_execution/test_workflow_execution_service.py` — service fixture pattern (patch at construction time).

### Testing Framework and Patterns

- pytest ^8.3.1, pytest-asyncio ^0.23.7, pytest-mock ^3.14.0
- `pythonpath = src`, `testpaths = tests`, `--import-mode=importlib`
- `@pytest.fixture` functions (not classes) for all mocks in newer tests; `MagicMock()` + `with patch(...)` context manager preferred.
- `autouse=True` fixture on `reset_pool_state` that saves/restores class attributes (`_initialized`, `_instance`, pool dict) between tests — must be implemented for `WorkflowPool` test file.
- `side_effect=[...]` list on mocks to simulate sequences of calls.
- Session-scoped `mock_database_engine` in `tests/conftest.py` patches `PostgresClient.get_engine` for all tests.
- No async test patterns found in workflow area — all sync `MagicMock`.

### Coverage Gaps

- `WorkflowPool` singleton service — no file exists anywhere under `tests/`; entirely untested.
- `WorkflowExecutor._compile_graph()` as a standalone method — not tested; `_init_workflow()` is only exercised through indirect integration tests with fully mocked node construction.
- `WorkflowExecutor._inject_user_context()` as a standalone method — not tested.
- `SubWorkflowNode.execute()` pool acquire/release integration — none of the 11 existing tests exercise a pool path.
- Thread-safety / concurrent `acquire()` / `release()` — no `threading.Thread` or `concurrent.futures`-based tests anywhere in the workflow area.
- Pool invalidation on `config_version_hash` change — no test.
- Background warmup watcher behavior — no test.
- Pool `release()` returning graph to pool vs. discarding (single-use design conflict above).

---

## 5. Configuration and Environment

### Environment Variables

| Variable | Default | Status | Purpose |
|---|---|---|---|
| `ENABLE_SUB_WORKFLOW_NODE` | `False` | Existing (pre-work) | Master feature gate — must be `True` for pool to activate |
| `WORKFLOW_POOL_ENABLED` | `True` | Existing (pre-work) | Pool-specific kill-switch |
| `WORKFLOW_POOL_MAX_AGE_SECONDS` | `3600` | Existing (pre-work) | TTL for a compiled graph slot |
| `SUBWORKFLOW_POOL_MAX_SIZE` | `5` | Existing (pre-work, line 506) | Global ceiling on compiled-graph slots per workflow |
| `SUBWORKFLOW_POOL_WARMUP_INTERVAL_SECONDS` | `60` | Existing (pre-work, line 507) | Warmup background job sleep interval |
| `WORKFLOW_MAX_NESTING_DEPTH` | `1` | Existing (pre-work) | Nesting depth cap — relevant for warmup watcher safety |

Neither new env var requires any code change — both are already in `config.py` as pre-work.

### Configuration Files

- `src/codemie/configs/config.py` — single `Config(BaseSettings)` class; all env vars loaded via pydantic-settings with `find_dotenv(".env")`; all pool-related vars already declared at lines 170–173 and 506–507.
- `src/codemie/core/workflow_models/workflow_models.py` — `WorkflowPoolConfig` pydantic model governs per-workflow pool settings (`enabled`, `min_size`, `max_size`, `refill_interval_seconds`).
- `src/codemie/core/workflow_models/workflow_config.py` — `WorkflowConfigBase.pool_config` JSONB column (lines 115–117); `parse_execution_config()` populates it at runtime.

### Feature Flags and Deployment Concerns

- `ENABLE_SUB_WORKFLOW_NODE` (global) + `WORKFLOW_POOL_ENABLED` (pool-specific): both must be `True`; `ENABLE_SUB_WORKFLOW_NODE` defaults to `False`, so pool is effectively disabled by default in a fresh deployment.
- `WorkflowPoolConfig.enabled` (per-workflow, default `False`): individual workflow must opt in; warmup watcher queries for workflows with this flag set.
- `docker-compose.yml` does not declare the new pool vars; operators needing non-default values must add them to `.env` or the compose `environment` block.
- Multi-worker deployment: each Uvicorn worker process holds its own in-process pool (no shared-memory pool across workers). Effective total slots = `SUBWORKFLOW_POOL_MAX_SIZE × WORKERS`. Sizing guidance from task4.md: `THREAD_POOL_MAX_WORKERS` should be at least `concurrent_parent_workflows × (max_nesting_level + 1)`.
- The `WorkflowPoolConfig.max_size` field (per-workflow config, default ceiling 50) and global `SUBWORKFLOW_POOL_MAX_SIZE` (default 5) serve overlapping purposes; implementation must document clearly that global setting is the hard ceiling and per-workflow `max_size` is advisory.

---

## 6. Risk Indicators

- **Design conflict — pool entry lifecycle**: `docs/superpowers/.../spec.md` says "single-use entries; checked out, used, discarded" while `task4.md` specifies `release()` returns the graph to the pool. Implementing the wrong semantics will cause either memory overhead (pool never shrinks) or no pool reuse benefit. Must be resolved before implementation starts.
- **CheckpointSaver state cross-contamination**: `task4.md` notes that a pooled graph must reinitialise or reset the `CheckpointSaver` state before reuse. There is no established pattern in the codebase for resetting `langgraph` `MemorySaver` or `PostgresSaver` state on a `CompiledStateGraph`. This is a novel pattern requiring careful implementation and testing.
- **Resume path and pool**: `SubWorkflowNode.execute()` has two distinct paths — create (first call) and resume (after interrupt). The resume path requires checkpoint state from a prior run. Pooled graphs must not be returned to the pool after a run that ended with an interrupt (since checkpoint state is now attached). The pool integration in `execute()` must gate on create-path only; resume path must skip pool entirely.
- **`SupervisorWorkflowExecutor` exclusion**: `WorkflowMode.AUTONOMOUS` uses a separate executor class. Pool must be explicitly gated to exclude autonomous-mode workflows, or `_compile_graph()` must be available on `SupervisorWorkflowExecutor` as well. This is unspecified in `task4.md`.
- **Thread-safety test coverage gap**: No existing concurrency tests in the workflow area. The pool's `acquire()` / `release()` under concurrent access is a correctness requirement with no test coverage established yet.
- **Circular import risk**: `sub_workflow_node.py` already uses lazy import for `WorkflowExecutor`. A new import of `WorkflowPool` in `sub_workflow_node.py` must follow the same lazy-import pattern to avoid the existing circular dependency chain (`workflow.py` → `nodes/` → `workflow.py`).
- **`_compile_graph()` return type coupling**: The split of `_init_workflow()` means `WorkflowPool` now depends on `WorkflowExecutor._compile_graph()` being a stable, callable API. If `WorkflowExecutor` is instantiated per-execution (current pattern), the pool must either instantiate a throwaway executor for compilation or make `_compile_graph()` a classmethod/staticmethod. Neither pattern exists yet in the codebase.
- **No test for `_compile_graph()` as standalone**: `_init_workflow()` is only exercised through indirect integration tests. The split introduces two new methods with zero direct test coverage as a baseline.
- **Background warmup scheduler pattern**: The warmup interval is in seconds (60 default) and uses an `AsyncIOScheduler`. If the warmup job performs synchronous DB queries (via `WorkflowService`), it must be run in a thread executor to avoid blocking the event loop — the existing schedulers (`conversation_analysis`, `stale_datasource`) use `run_in_threadpool` wrappers; this pattern must be confirmed.

---

## 7. Summary for Complexity Assessment

This task touches five architectural layers: Service (new `WorkflowPool` singleton), Workflow Execution (`WorkflowExecutor` refactor), Node (`SubWorkflowNode` integration), API Startup (`main.py` lifespan hook), and Config (read-only; pre-work complete). The pre-work already committed on this branch is substantial — `NodeSlot`, `WorkflowPoolConfig`, `pool_config` DB column, both env vars, and `WORKFLOW_POOL_ENABLED` flag are all in place. The net new file count is modest: one new service file (`workflow_pool.py`), one new test file (`test_workflow_pool.py`), and targeted modifications to `workflow.py`, `sub_workflow_node.py`, `main.py`, and `test_sub_workflow_node.py` — approximately 6 files total, with the new service file carrying the majority of the implementation weight.

The task introduces two novel patterns not previously established in the codebase: (1) splitting a `CompiledStateGraph` build into a user-agnostic compile phase and a user-dependent injection phase, and (2) resetting `CheckpointSaver` state on a pooled `CompiledStateGraph` before reuse. Both are LangGraph-specific and have no local precedent to copy from. The `FileProcessPoolManager` singleton pattern covers the pool lifecycle scaffolding, but the graph reset semantics must be derived from LangGraph internals. This is the primary source of technical novelty and implementation risk.

Test coverage posture is weak in the affected area. The 11 existing `SubWorkflowNode` tests all mock `WorkflowExecutor.create_executor` entirely and will need new pool-path variants. `WorkflowPool` itself is completely untested. Thread-safety testing is absent from the entire workflow area. The acceptance criteria include concurrent acquire/release correctness and config-version-hash invalidation, neither of which has any test infrastructure today. The design conflict between the parent spec.md (single-use discard) and task4.md (return to pool) is a pre-implementation blocker that must be resolved to avoid re-work.
