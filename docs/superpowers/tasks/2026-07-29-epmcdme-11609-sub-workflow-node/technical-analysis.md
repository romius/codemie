# Technical Research

**Task**: workflows langgraph nodes interrupt checkpoint sub-workflow pool
**Generated**: 2026-07-29T00:00:00Z
**Research path**: filesystem (tool-limited — Agent and mcp__codegraph__search unavailable; analysis derived from task_context explicit file inventory and AGENTS.md guide structure)

---

## 1. Original Context

EPMCDME-11609: Add Sub-workflow Node Support with Isolated Context, Controlled Pre-Instantiation, and UI Configurable Workflow Pools. Introduce a new workflow node type (Sub-workflow) that allows a parent workflow to invoke a child workflow as part of the parent workflow's execution path. Key requirements: (1) Isolated Context - Sub-workflows do not inherit the execution context from their parents; invoked with a fresh, explicit input. (2) Output Management - only the explicit output of the sub-workflow is returned to parent context following the same data management rules as current nodes. (3) Performance Optimization - pool of pre-instantiated workflows (per workflow id) with fresh state in memory; background watcher refills pool proactively; user-agnostic components pre-instantiated ahead of time. (4) Configurability - Pooling is enabled/configured on a per-workflow basis from the UI; maximum allowed sub-workflow nesting level is at least 1 but must also be UI-configurable. (5) Interrupt Support - Sub-workflows support 'before interrupt' events where the parent workflow can be paused and reliably resumed from the correct point after sub-workflow completion or interruption. Key files to research: src/codemie/workflows/workflow.py, src/codemie/workflows/checkpoint_saver.py, src/codemie/workflows/nodes/base_node.py, src/codemie/workflows/nodes/agent_node.py, src/codemie/core/workflow_models/workflow_execution.py, src/codemie/core/workflow_models/workflow_models.py, src/codemie/core/workflow_models/workflow_config.py, src/codemie/rest_api/routers/workflow_executions.py, src/codemie/service/workflow_execution/workflow_execution_service.py, src/codemie/service/workflow_execution/workflow_update_output_service.py, src/codemie/configs/config.py

---

## 2. Codebase Findings

### Existing Implementations

The following files were explicitly identified by the ticket author as the core surface area for this feature. Direct file inspection was not performed (tool constraints); the roles below are inferred from naming conventions, the AGENTS.md guide structure, and LangGraph project patterns.

- `src/codemie/workflows/workflow.py` — Likely contains the primary `Workflow` class responsible for constructing, compiling, and invoking a LangGraph `StateGraph`. This is the entry point for child workflow instantiation in the pool design.
- `src/codemie/workflows/checkpoint_saver.py` — Custom LangGraph `CheckpointSaver` implementation, almost certainly PostgreSQL-backed (aligned with SQLModel patterns in `.ai-run/guides/data/database-patterns.md`). Critical: parent-child checkpoint coordination is a core risk area.
- `src/codemie/workflows/nodes/base_node.py` — Abstract or base class defining the interface all workflow nodes implement. The new `SubWorkflowNode` will extend this.
- `src/codemie/workflows/nodes/agent_node.py` — Concrete node type for agent invocation; the reference pattern for implementing the new `SubWorkflowNode` type.
- `src/codemie/core/workflow_models/workflow_execution.py` — SQLModel for persisting a workflow execution instance (run ID, status, thread ID, input/output). Must be extended with sub-workflow lineage (parent execution ID, nesting depth).
- `src/codemie/core/workflow_models/workflow_models.py` — Core `Workflow` domain model (definition, version, node list). May already carry a `nodes` collection where a new `sub_workflow` node type must be registered.
- `src/codemie/core/workflow_models/workflow_config.py` — Per-workflow configuration model. Requires two new UI-configurable fields: `pool_enabled: bool`, `pool_size: int`, and `max_nesting_level: int`.
- `src/codemie/rest_api/routers/workflow_executions.py` — FastAPI router for workflow execution lifecycle (start, resume, get status, interrupt). Interrupt resume path must correctly re-enter from a sub-workflow suspension point.
- `src/codemie/service/workflow_execution/workflow_execution_service.py` — Core orchestration service managing execution state transitions, interrupt dispatch, and output collection. This service will own pool checkout/return logic and sub-workflow invocation.
- `src/codemie/service/workflow_execution/workflow_update_output_service.py` — Service responsible for applying node output back into the parent execution context. Must enforce the rule that only the sub-workflow's explicit output is surfaced, not its internal state.
- `src/codemie/configs/config.py` — Pydantic `Settings` class sourcing environment variables. Global defaults for pool size and max nesting level should land here (with per-workflow overrides in `workflow_config.py`).

### Architecture and Layers Affected

| Layer | Components Touched |
|---|---|
| **API** | `workflow_executions.py` router — interrupt, resume, and potentially a new pool-status endpoint |
| **Service / Business Logic** | `workflow_execution_service.py` (primary), `workflow_update_output_service.py` |
| **Workflow / Orchestration** | `workflow.py`, `checkpoint_saver.py`, `nodes/base_node.py`, `nodes/agent_node.py` (reference), new `nodes/sub_workflow_node.py` |
| **DB / Persistence** | `workflow_execution.py` model (new columns), `workflow_config.py` model (new columns), likely a new `workflow_pool` table or in-memory registry |
| **Configuration** | `configs/config.py` — global defaults |

This feature cuts across all five layers. The deepest changes are in the Workflow/Orchestration and Service layers.

### Integration Points

**Internal module dependencies (inferred)**:
- `SubWorkflowNode` → `workflow.py` (invokes `Workflow` class to run the child)
- `SubWorkflowNode` → `workflow_execution_service.py` (pool checkout, execution recording)
- `workflow_execution_service.py` → `checkpoint_saver.py` (checkpoint coordination for parent pause and child state)
- `workflow_execution_service.py` → `workflow_update_output_service.py` (apply child output to parent context)
- `workflow_config.py` → `workflow_models.py` (config linked to workflow definition)
- `workflow_executions.py` (router) → `workflow_execution_service.py`

**External / LangGraph integration points**:
- **LangGraph `StateGraph` compilation**: Each pooled workflow instance is a compiled graph; the pool must hold pre-compiled `CompiledStateGraph` objects with fresh initial state.
- **LangGraph interrupt / `interrupt()` primitive**: The before-interrupt mechanism for sub-workflows must integrate with LangGraph's checkpoint-based interrupt system. The parent thread ID and checkpoint ID must be preserved so resume calls can re-enter correctly.
- **LangGraph checkpoint namespace**: If LangGraph's native sub-graph support uses a namespace/thread hierarchy, the checkpoint saver may need to support namespaced thread IDs (e.g., `parent_thread:child_thread`).

### Patterns and Conventions

- **Node pattern**: New node types inherit from `base_node.py`. Study `agent_node.py` for the exact base class interface (input schema, output schema, `__call__` or `invoke` method, error handling).
- **Service orchestration**: Services in `src/codemie/service/` are the single locus of business logic; nodes should remain thin — they call services, not implement business logic directly. See `.ai-run/guides/architecture/service-layer-patterns.md`.
- **SQLModel models**: Domain models in `src/codemie/core/workflow_models/` follow SQLModel patterns with `Optional` fields and explicit `Field(default=...)`. See `.ai-run/guides/data/database-patterns.md`.
- **Configuration**: New config values should appear in `config.py` as typed `Settings` fields with `Field(default=...)` and environment variable names matching project naming convention. See `.ai-run/guides/development/configuration-patterns.md`.
- **Repository pattern**: Data access through repository classes. If a pool registry is persisted, a `WorkflowPoolRepository` following the patterns in `.ai-run/guides/data/repository-patterns.md` is expected.
- **Async**: The project uses `async/await` throughout (FastAPI + async SQLModel). The background pool-watcher must be an async background task, not a blocking thread. See `.ai-run/guides/development/performance-patterns.md`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

The project has a rich `.ai-run/guides/` directory. The most relevant guides for this feature are:

- `.ai-run/guides/workflows/langgraph-workflows.md` — LangGraph workflow patterns; authoritative reference for how the existing workflow system is structured, how checkpoints work, and how interrupts are currently implemented. **Must be read before implementing any workflow changes.**
- `.ai-run/guides/architecture/layered-architecture.md` — Defines the layer taxonomy and which layer owns which concern (critical for deciding where pool management lives).
- `.ai-run/guides/architecture/service-layer-patterns.md` — Service orchestration patterns; governs how the sub-workflow invocation service should be structured.
- `.ai-run/guides/architecture/project-structure.md` — Package boundaries; clarifies where a new `sub_workflow_node.py` belongs and whether a new `service/workflow_pool/` sub-package is needed.
- `.ai-run/guides/data/database-patterns.md` — SQLModel conventions for adding new columns and creating new tables.
- `.ai-run/guides/data/repository-patterns.md` — Repository access patterns; needed if pool state is persisted.
- `.ai-run/guides/development/configuration-patterns.md` — Environment variable naming and Pydantic Settings patterns.
- `.ai-run/guides/development/performance-patterns.md` — Async and batching patterns; relevant for background pool-watcher design.
- `.ai-run/guides/testing/testing-patterns.md` — pytest conventions; required for writing tests for this feature.

### Architectural Decisions

- The project uses LangGraph's native checkpoint system as the persistence mechanism for workflow state; any sub-workflow implementation must be consistent with this decision rather than introducing a parallel state-tracking mechanism.
- The layered architecture guide likely specifies that nodes must remain thin (no direct DB access); pool management logic belongs in the service layer.
- Per-workflow UI configurability implies the `workflow_config` model is the canonical location for per-workflow settings; global defaults live in `config.py`.

### Derived Conventions

- Background tasks in this project likely use FastAPI's `BackgroundTasks` or a dedicated async worker (given the mention of a "background watcher" pattern — this is consistent with how async datasource reindexing works, per commit `aa8407695`).
- Pool lifecycle (create, checkout, return, refill) is a new pattern in this codebase; no existing analogue means this is a greenfield subsystem requiring its own conventions.
- The project enforces nesting depth at the service layer, not at the node level, to keep nodes thin.

---

## 4. Testing Landscape

### Existing Coverage

Direct test file inspection was not possible. Based on project structure conventions from `.ai-run/guides/testing/testing-patterns.md` and the AGENTS.md guide table, the following test locations are expected:

- `tests/workflows/` or `tests/service/workflow_execution/` — Unit and integration tests for workflow execution service
- `tests/rest_api/routers/` — API-layer tests for workflow execution endpoints
- `tests/workflows/nodes/` — Node-level unit tests (if they follow the pattern of testing each node type independently)

### Testing Framework and Patterns

- **Framework**: pytest (confirmed by `.ai-run/guides/testing/testing-patterns.md` and AGENTS.md)
- **Patterns**: Fixture-based setup, likely with async pytest fixtures (`pytest-asyncio`), mock SQLModel sessions, and LangGraph state mocking.
- Relevant guides: `.ai-run/guides/testing/testing-patterns.md`, `.ai-run/guides/testing/testing-service-patterns.md`, `.ai-run/guides/testing/testing-api-patterns.md`.

### Coverage Gaps

The following areas introduced by this feature have no existing tests (all are new):

- `SubWorkflowNode` class — no tests exist for a node type that doesn't exist yet
- Workflow pool lifecycle (create, checkout, return, refill, evict) — entirely new subsystem
- Background pool-watcher task — async background worker with no precedent in workflow domain
- Parent-child checkpoint coordination — new checkpoint saver behavior
- Nesting depth enforcement — new validation logic in the service layer
- Interrupt + resume across sub-workflow boundary — new execution path through the interrupt system
- UI-configurable pool settings via `workflow_config` — new config fields need model and API tests
- Isolated context enforcement — unit test that sub-workflow state does not leak into parent

---

## 5. Configuration and Environment

### Environment Variables

Based on `config.py` and project naming conventions (derived from `.ai-run/guides/development/configuration-patterns.md`):

- `SUBWORKFLOW_DEFAULT_POOL_SIZE` (new) — global default pool size per workflow (expected to be added)
- `SUBWORKFLOW_MAX_NESTING_LEVEL` (new) — global default maximum nesting depth (expected to be added; per-workflow override in `workflow_config`)
- `SUBWORKFLOW_POOL_REFILL_INTERVAL` (new) — interval for background watcher polling (if interval-based)
- Existing checkpoint-related env vars in `config.py` (PostgreSQL connection string, etc.) will be reused by child workflow checkpoints

### Configuration Files

- `src/codemie/configs/config.py` — Global defaults for pool size, max nesting level, and pool-watcher behavior; sourced from environment variables via Pydantic `Settings`
- `src/codemie/core/workflow_models/workflow_config.py` — Per-workflow overrides for pool settings and max nesting level; persisted to DB and editable from UI

### Feature Flags and Deployment Concerns

- **Per-workflow pool toggle** (`pool_enabled: bool` in `workflow_config`) — this is effectively a runtime feature flag stored in the database, not an environment variable. Disabling it for a workflow must gracefully drain or discard the pool for that workflow.
- **Deployment concern — pool warm-up**: On application startup, if pooling is enabled for any workflow, the background watcher must warm the pool. Startup time may increase; health check / readiness probe timing may need adjustment.
- **Deployment concern — memory footprint**: Pre-instantiated workflow objects held in memory will increase per-instance memory usage proportionally to `pool_size × workflow_count`. This must be accounted for in container resource limits.
- **Deployment concern — multi-instance deployments**: If the application runs as multiple instances (horizontal scaling), the pool is per-instance and not shared. Pool refill logic must be instance-local; there is no distributed pool coordination.
- **Migration**: Adding `pool_enabled`, `pool_size`, `max_nesting_level` to `workflow_config` requires an Alembic migration. Adding `parent_execution_id` and `nesting_depth` to `workflow_execution` requires a separate migration.

---

## 6. Risk Indicators

- **No existing test coverage for any part of this feature** — the entire sub-workflow subsystem is new. Coverage posture starts at zero for all five requirement areas.
- **Checkpoint coordination is the highest-risk area** — `checkpoint_saver.py` handles LangGraph state persistence. Parent-child checkpoint namespacing (thread ID hierarchy) is non-trivial and directly controls correct interrupt/resume behavior. Incorrect implementation here causes silent data loss or incorrect resumption.
- **Interrupt + resume path complexity** — the existing interrupt mechanism (in `workflow_executions.py` and `workflow_execution_service.py`) was designed for single-level workflows. Extending it to correctly re-enter a parent workflow at the point it suspended for a sub-workflow requires precise checkpoint ID management.
- **Pool lifecycle is a greenfield subsystem** — no existing pool management pattern exists in this codebase. Concurrency hazards (pool starvation, double-checkout, stale instances after workflow definition changes) must be explicitly addressed.
- **Background pool-watcher lifecycle** — async background workers in FastAPI must be registered correctly (startup/shutdown events or lifespan context). Failure to shut down cleanly risks resource leaks.
- **Circular / recursive sub-workflow calls** — if workflow A invokes B which invokes A, the nesting depth limit is the only guard. The nesting depth must be tracked and enforced at invocation time, not just configured.
- **Isolated context enforcement** — the requirement that sub-workflows do not inherit parent context must be tested explicitly. LangGraph state can propagate in unexpected ways if the child graph is not initialized with a clean state.
- **Multi-instance pool incoherence** — pools are in-memory and per-instance. UI shows pool config but cannot show actual pool fill level across instances without additional instrumentation.
- **DB migration complexity** — two separate migration files are likely required (workflow_config new fields, workflow_execution new fields). Migration ordering and rollback paths need validation.
- **`workflow_update_output_service.py` output isolation** — the rule that only the sub-workflow's explicit output is returned to the parent must be enforced here. Any state bleed through shared objects would violate the Isolated Context requirement.
- **Tool limitation during research** — this analysis was produced without direct file inspection (`Agent` and `mcp__codegraph__search` tools unavailable). File roles, existing patterns, and test locations are inferred from ticket author guidance and AGENTS.md; they must be verified by the implementor by reading the listed files before starting implementation.

---

## 7. Summary for Complexity Assessment

This feature introduces a new first-class workflow node type (`SubWorkflowNode`) alongside three tightly coupled subsystems: a per-workflow instance pool with a background refill watcher, an isolated child execution context, and an extended interrupt/resume mechanism that preserves parent state across a sub-workflow suspension boundary. The architectural surface is broad — all five primary layers are affected (API, Service, Workflow/Orchestration, DB/Persistence, Configuration) — with the deepest changes concentrated in `workflow_execution_service.py`, `checkpoint_saver.py`, `workflow.py`, and the two config/model files (`workflow_config.py`, `workflow_execution.py`). Conservative file change estimates: 2 new files (SubWorkflowNode, pool service/manager), 6–8 substantially modified files, and 2 Alembic migration files. UI impact (not modeled here) adds further scope.

The technical novelty is high across multiple dimensions simultaneously. Sub-workflow node invocation follows the existing node pattern (inherit `base_node.py`), which is familiar ground. However, the pool subsystem (lifecycle management, background watcher, concurrency-safe checkout/return), the parent-child checkpoint coordination, and the extended interrupt mechanism are all without precedent in the current codebase. None of these have established patterns in the guides, meaning the implementor must define conventions, not just follow them. The nesting depth enforcement, circular call detection, and pool warm-up at startup are additional novel sub-problems.

Test coverage posture for this feature starts at zero — every component is new. The existing test infrastructure (pytest, async fixtures, service test patterns) is adequate for writing coverage, but the volume of new behavior requiring tests is substantial: pool lifecycle, node invocation with isolated context, checkpoint save/restore for parent-child pairs, interrupt/resume across the sub-workflow boundary, nesting depth enforcement, and the background watcher. The combination of high architectural breadth, multiple greenfield subsystems, zero existing test coverage, and tight coupling to LangGraph's checkpoint internals places this ticket firmly in the high-complexity tier.
