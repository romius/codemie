# Technical Research

**Task**: workflow iteration langgraph iter_key nested
**Generated**: 2026-07-30T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Implement Nested Iterations Support in Workflow Engine (EPMCDME-4289).

Currently, the Codemie Backend workflow engine supports iteration through collections using the `iter_key` parameter in workflow transitions. There's no support for nested iterations, where each item from an outer iteration could trigger its own set of iterations.

Expected Result:
1. An outer iteration can define its own `iter_key`
2. For each item in the outer iteration, an inner iteration can be defined with a different `iter_key`
3. The system correctly tracks both the outer and inner iteration states
4. Message history and context are properly maintained across all levels of iteration
5. Iteration counters accurately reflect the position in both outer and inner iterations

Affected Areas:
- `workflow.py`: The `WorkflowExecutor` class, particularly the `continue_iteration` method
- `workflow_models.py`: The `WorkflowNextState` class may need modification
- Workflow state tracking and iteration counter mechanisms
- Message history preservation logic
- Workflow graph edge creation and management

---

## 2. Codebase Findings

### Existing Implementations

**Primary workflow execution files:**

- `src/codemie/workflows/workflow.py` — `WorkflowExecutor` class (lines 105–1131). Hosts the core iteration machinery:
  - `continue_iteration` (lines 611–667): fan-out via LangGraph `Send`; detects nested context via `is_in_iteration = iter_number is not None and iter_number > 0`
  - `_build_parallel_context` (lines 594–609): clones or reuses `context_store` based on nesting level
  - `_handle_single_state` (lines 691–708): registers `add_conditional_edges` pointing at `continue_iteration` when `iter_key` is present
  - `init_workflow_edges` (lines 669–675): entry point that iterates all states and calls `_process_transition`
  - `find_map_nodes` (lines 796–804): collects all target nodes of `iter_key` transitions; these nodes receive `current_task_key=TASK_KEY` during `initialize_node`

- `src/codemie/core/workflow_models/workflow_models.py` — `WorkflowNextState` (lines 185–332), `WorkflowState` (lines 361–403). All transition config lives here including `iter_key`, `include_in_iterator_context`, and `finish_iteration`.

- `src/codemie/workflows/constants.py` — Iteration-scoped state-schema keys:
  - `ITER_SOURCE = "iteration_source"` (raw string output from producer node)
  - `ITERATION_NODE_NUMBER_KEY = "iteration_node_number"` (1-indexed outer branch index)
  - `TOTAL_ITERATIONS_KEY = "total_iterations"` (total parallel branches spawned)
  - `FIRST_STATE_IN_ITERATION = "first_state_in_iteration"` (True when starting a new per-item chain)
  - `TASK_KEY = "task"` (per-branch item value)

- `src/codemie/workflows/models.py` — `AgentMessages` TypedDict (lines 74–81). Declares `messages` with `add_messages` reducer and `context_store` with custom `add_or_replace_context_store` reducer. Does NOT declare `ITERATION_NODE_NUMBER_KEY`, `TOTAL_ITERATIONS_KEY`, `FIRST_STATE_IN_ITERATION`, or `ITER_SOURCE` as typed fields — these pass as untyped extra keys.

- `src/codemie/workflows/nodes/base_node.py` — `BaseNode`:
  - `_add_iteration_state` (line 413): writes `ITER_SOURCE`, `ITERATION_NODE_NUMBER_KEY`, `TOTAL_ITERATIONS_KEY`, and the `iter_key` value into the final node output
  - `_prepare_iter_task_messages` (lines 294–307): injects TASK_KEY as a HumanMessage when `FIRST_STATE_IN_ITERATION` is True
  - `finalize_and_update_state` (line 566): main output assembly method

- `src/codemie/workflows/nodes/transform_node.py` — Overrides `_add_iteration_state` (line 720) for transform nodes.

- `src/codemie/workflows/nodes/agent_node.py` — `get_node_name` (line 267): renders "N of M" display using `ITERATION_NODE_NUMBER_KEY` / `TOTAL_ITERATIONS_KEY`.

**Current nested iteration state (partial implementation already exists):**

The `continue_iteration` method already has code that detects a nested context (`is_in_iteration = iter_number is not None and iter_number > 0`). When `is_in_iteration=True`:
- `context_store` is returned as a shared reference (not cloned) to avoid JSONB size explosion
- `messages` list is passed by reference (not copied)
- `ITERATION_NODE_NUMBER_KEY` in the inner `Send` is set to the **outer** branch index, not an independent inner counter

This means the nested path already partially exists but does not provide independent inner counters (requirement 5 from the ticket) and does not independently track inner iteration state (requirement 3).

**Validation constraints blocking nested `iter_key` today:**

`WorkflowNextState` model validator (lines 321–332) prohibits `iter_key` from being combined with `condition`, `switch`, or `state_ids`. A node inside an outer parallel branch that needs to fan out again can only do so via another plain `iter_key` transition — no conditional routing into a sub-iteration is currently possible.

**Existing YAML templates:**

- `config/templates/workflow/workflow_iteration_multi_node_template.yaml` — multi-node single-level iteration with `iter_key: colors` chained identically across two states (same key, not nested)
- `config/templates/workflow/workflow_interation_with_branches_template.yaml` — `iter_key` followed by `state_ids` fan-out (sequential, not nested)
- Production templates (`aice_java_migration_template.yaml`, `java-migration_template.yaml`, etc.) — multiple separate single-level `iter_key: files` iterations at different stages

No template exists demonstrating a workflow where an outer `iter_key` dispatches branches that each contain a different `iter_key`.

### Architecture and Layers Affected

| Layer | Component | How It Is Touched |
|---|---|---|
| **Workflow/Orchestration** | `WorkflowExecutor.continue_iteration` | Core change site — must emit independent inner counters and properly scope inner `Send` state |
| **Workflow/Orchestration** | `WorkflowExecutor._build_parallel_context` | Must decide whether to deep-copy, shallow-copy, or share context per nesting level |
| **Workflow/Orchestration** | `WorkflowExecutor._handle_single_state` + `init_workflow_edges` | Edge registration logic may need to handle states that are simultaneously targets of an outer `iter_key` and sources of an inner `iter_key` |
| **Workflow/Orchestration** | `WorkflowExecutor.find_map_nodes` | Must detect nodes that are targets at multiple nesting depths |
| **Data Models** | `WorkflowNextState` (`workflow_models.py:185`) | May need new fields for inner-iteration metadata or to relax/extend the `iter_key` validator |
| **Data Models** | `AgentMessages` TypedDict (`models.py:74`) | May need new typed fields (e.g., `outer_iteration_number`, `outer_total_iterations`) for independent counter tracking |
| **Node Layer** | `BaseNode._add_iteration_state` | Must write outer and inner iteration counters independently |
| **Node Layer** | `BaseNode._prepare_iter_task_messages` | Must handle task injection at both the outer and inner iteration entry points |
| **Node Layer** | `AgentNode.get_node_name` | Display logic for "N of M" may need to reflect nested depth |
| **Constants** | `src/codemie/workflows/constants.py` | New constants needed for inner iteration tracking (e.g., `OUTER_ITERATION_NODE_NUMBER_KEY`, `OUTER_TOTAL_ITERATIONS_KEY`, or a stack-based key) |
| **Persistence** | `WorkflowExecutionState.iteration_number` (DB column) | Currently stores a flat integer — nested iterations will produce multiple states with the same `iteration_number` for different nesting depths |
| **Validation** | `src/codemie/workflows/validation/schema.py` | Cross-reference validator may need to allow or validate nested `iter_key` configurations |
| **Config Schema** | `src/codemie/workflows/execution_config_schema.yaml` | If new YAML fields are introduced for nested iteration, schema must be updated |

### Integration Points

**Internal:**
- `WorkflowExecutionService.start_state` (called from `base_node.py`) — receives `iteration_number` for DB persistence; nested iterations will pass multiple distinct `(outer_idx, inner_idx)` combinations as flat integers, causing collisions
- `WorkflowExecutionService.finish_state` — writes final state output; no nesting-aware logic
- `ThoughtConsumer` — streams thoughts per state; thoughts are tagged with `iteration_number`; nested iterations may produce duplicate `iteration_number` values
- `CheckpointSaver.put` — serializes full LangGraph state to JSONB in `WorkflowExecution.checkpoints`; nested iteration states are larger (contain outer + inner context)
- `WorkflowExecutionTransition.workflow_context` — JSONB snapshot of full state at transition time; size concern for deeply nested iterations (see `MAX_JSONB_SIZE_BYTES = 1_000_000` in `utils.py:549`)

**External:**
- **PostgreSQL** — All workflow state persistence; `psycopg2-binary` (sync) + `asyncpg` via SQLAlchemy (async); JSONB columns used for state snapshots and checkpoints
- **LangGraph 1.1.6** — `StateGraph`, `Send`, `add_messages`; the `Send` fan-out mechanism is the only supported way to dispatch parallel iteration branches; LangGraph's state merging reducers govern how per-branch outputs converge
- **Elasticsearch** — `WorkflowExecutionService` queries `WorkflowExecutionState` by `execution_id.keyword`; no iteration-specific queries found

### Patterns and Conventions

1. **Constants-first naming**: all new state-schema keys must be added as named constants in `src/codemie/workflows/constants.py` before being referenced in `workflow.py` or node files.

2. **TypedDict extension**: new typed fields in the LangGraph state schema go on `AgentMessages` (`src/codemie/workflows/models.py`); untyped extra keys are acceptable for transient per-branch data but typed fields are preferred for iteration counters that must survive reducer merges.

3. **`WorkflowNextState` model validators**: any new field on `WorkflowNextState` that interacts with `iter_key` must be added with a corresponding `model_validator` check maintaining backward compatibility (no breaking changes to existing YAML configs).

4. **Node extension pattern**: iteration-scoped behavior belongs in `continue_iteration` / `_build_parallel_context` in `WorkflowExecutor`; node-level iteration metadata handling belongs in `_add_iteration_state` in `BaseNode`; nodes themselves must not contain routing logic.

5. **Convergence node detection**: `find_convergence_nodes` auto-detects nodes with multiple incoming edges and sets `defer=True`; if nested iteration creates new convergence patterns, the existing detection must still cover them — confirm by running the graph compilation step.

6. **`add_conditional_edges` for all routing**: simple sequential transitions, iteration fan-outs, conditions, and switches all use `add_conditional_edges`; do not add `add_edge` for any routing that may involve iteration.

---

## 3. Documentation Findings

### Guides and Architecture Docs

The following guide files are directly relevant to this task:

- `.ai-run/guides/workflows/langgraph-workflows.md` — Authoritative conventions for extending the workflow engine. Key directive: extend `WorkflowExecutor`, nodes, or validation utilities — never create a parallel graph execution path.
- `.ai-run/guides/architecture/layered-architecture.md` — HTTP concerns → `rest_api/routers/`; orchestration → `service/`; graph execution stays inside `workflows/`. Cross-cutting constants and config belong in `core/` and `configs/`.
- `.ai-run/guides/architecture/service-layer-patterns.md` — Services coordinate repositories and domain logic; do not mix workflow node behavior with service logic.
- `.ai-run/guides/architecture/project-structure.md` — `src/codemie/workflows/` owns LangGraph execution; new nodes go under `src/codemie/workflows/nodes/`; do not create new top-level packages.
- `docs/workflows/04_state_transitions.md` — Documents the convention that "the same `iter_key` must be present in every state within the iteration chain except the last one" (multi-stage iteration mechanics).

### Architectural Decisions

1. **Nested detection via `ITERATION_NODE_NUMBER_KEY > 0`** — Recorded inline at `workflow.py:644–646`. The presence of a positive `ITERATION_NODE_NUMBER_KEY` in state schema signals nested context. This is a boolean signal, not a depth counter. Decision to use a shared context reference in nested mode was made explicitly to prevent JSONB checkpoint size explosion (noted in `_build_parallel_context` docstring).

2. **Context sharing in nested iterations** — `_build_parallel_context` returns a reference (not a copy) when `is_in_iteration=True`. The protection relies on LangGraph's copy-on-write state update semantics, not on Python-level object isolation.

3. **`include_in_iterator_context` whitelist applies to first-level only** — By design (code structure, not a comment): `_build_parallel_context` short-circuits with `return context_store` before consulting the whitelist when `is_in_iteration=True`.

4. **`finish_iteration` semantics** — `TC_IMR_010` documents that with `finish_iteration=True` and an `ITER_SOURCE` containing a list, the code actually iterates the list. This is a documented discrepancy between label and runtime behavior.

### Derived Conventions

- Iteration counter tracking is intentionally flat (single integer per branch) in the current design. Moving to nested counters requires a deliberate decision on the data model: either a compound key (e.g., `"2.1"` string) or separate `OUTER_ITERATION_NODE_NUMBER_KEY` / `INNER_ITERATION_NODE_NUMBER_KEY` constants.
- The `AgentMessages` TypedDict does not enforce types for iteration-related extra keys; any new counter fields must be added as typed fields to participate in LangGraph's state merging correctly.
- Alembic migration is required if `WorkflowExecutionState.iteration_number` needs to store a compound value (currently `Integer` column — may need to become a `String` or a second column added).

---

## 4. Testing Landscape

### Existing Coverage

**Primary iteration test files:**

- `tests/codemie/workflows/test_iteration_map_reduce.py` — 12 test cases (TC_IMR_001 through TC_IMR_012) covering:
  - Simple list dispatch via `iter_key` (TC_IMR_001)
  - Context and message cloning for first-level branches (TC_IMR_002, TC_IMR_008)
  - JSON pointer `iter_key` with `/` prefix (TC_IMR_004)
  - Dict task values (TC_IMR_005)
  - `FIRST_STATE_IN_ITERATION` flag behavior (TC_IMR_006)
  - `ITERATION_NODE_NUMBER_KEY` and `TOTAL_ITERATIONS_KEY` counters (TC_IMR_007)
  - Nested iteration — context/message shared reference, outer counter preserved (TC_IMR_009)
  - `finish_iteration` behavior (TC_IMR_010 — documents actual vs. labeled behavior)
  - `include_in_iterator_context` whitelist (TC_IMR_011, TC_IMR_012)

- `tests/codemie/workflows/test_workflow_state_transitions.py` — TC_WST_004 through TC_WST_013 covering iter_key transitions, convergence node detection, fan-out, and one nested iteration scenario (TC_WST_008).

- `tests/codemie/workflows/test_base_node_lifecycle.py` — `test_state_finalization_with_iteration_state` covers `_add_iteration_state` output correctness.

- `tests/codemie/workflows/test_flag_interactions.py` — TC_FI_009 covers `FIRST_STATE_IN_ITERATION` + `TASK_KEY` interaction in node output.

### Testing Framework and Patterns

- **Framework**: pytest; all tests use `def test_*` and `@pytest.fixture`.
- **Mocking**: `unittest.mock` — `Mock`, `patch`; `@patch('codemie.workflows.workflow.WorkflowExecutionService')` pattern on tests that instantiate `WorkflowExecutor`.
- **DB isolation**: global `autouse=True` session fixture in `tests/conftest.py` patches `PostgresClient.get_engine` to prevent real DB calls.
- **WorkflowExecutor instantiation pattern** (used in all iteration tests):
  ```python
  executor = WorkflowExecutor(
      workflow_config=basic_workflow_config,
      user_input="test",
      user=mock_user,
      thought_queue=mock_thought_queue,
      execution_id="exec_123",
  )
  ```
- **BaseNode subclassing**: `MockNode(BaseNode)` with `execute()` returning a configurable result; used in lifecycle and flag-interaction tests.
- Tests invoke `continue_iteration` **directly as a unit** — no test builds a complete `StateGraph` with two nested `iter_key` transitions end-to-end.

### Coverage Gaps

The following areas are not covered by existing tests and will need new tests for EPMCDME-4289:

1. **Independent inner iteration counters** — TC_IMR_009 asserts that inner branches preserve the outer counter value, not that they get an independent inner counter. No test verifies that inner branches 1, 2, and 3 of a nested iteration receive distinct inner `ITERATION_NODE_NUMBER_KEY` values.

2. **`TOTAL_ITERATIONS_KEY` semantics in nested context** — No test verifies what `TOTAL_ITERATIONS_KEY` should be in inner `Send` args (inner list length vs. outer total vs. both).

3. **Full round-trip ITER_SOURCE propagation** — No test exercises the complete flow: outer node runs → writes `ITER_SOURCE` → outer `continue_iteration` dispatches → inner node runs → writes `ITER_SOURCE` → inner `continue_iteration` dispatches. TC_IMR_009 hardcodes `ITER_SOURCE` directly.

4. **`find_map_nodes` for truly nested workflow config** — No test asserts what `find_map_nodes` returns when two states both have `iter_key` (one pointing to the other's source node).

5. **`init_workflow_edges` / `add_conditional_edges` for a nested-iter config** — No integration test builds a real or mock `StateGraph` with two `iter_key` transitions and verifies edge registration is correct.

6. **Empty inner collection** — No test exercises `items_to_process == []` inside a nested call (LangGraph behavior with empty `Send` list is undefined in current tests).

7. **`_build_parallel_context` whitelist in nested context** — Whether `include_in_iterator_context` should or should not apply to inner levels is unverified.

---

## 5. Configuration and Environment

### Environment Variables

| Variable | Code Default | Purpose |
|---|---|---|
| `WORKFLOW_MAX_CONCURRENCY` | `5` | Hard ceiling on per-workflow parallel branch count; `get_max_concurrency()` enforces this globally |
| `WORKFLOW_DEFAULT_CONCURRENCY` | `2` | Default `max_concurrency` when YAML omits it |
| `THREAD_POOL_MAX_WORKERS` | `20` | Thread pool for background workflow execution |
| `ASSISTANT_THREAD_POOL_MAX_WORKERS` | `60` | Thread pool for assistant tasks inside iteration branches |
| `ENABLE_LANGGRAPH_AITOOLS_AGENT` | `True` | Selects `LangGraphAgent` vs `AIToolsAgent` inside iteration branch nodes |

No `WORKFLOW_`, `LANGGRAPH_`, or `ITERATION_` prefixed variables appear in `.env`, `tests/.env.test`, or `docker-compose.yml`. All workflow concurrency and recursion knobs are controlled through `Config` class defaults or per-workflow YAML fields.

### Configuration Files

- `src/codemie/configs/config.py` — Central `Config(BaseSettings)` class; all runtime tuning knobs for workflow execution live here
- `src/codemie/core/workflow_models/workflow_config.py` — `WorkflowConfigBase`: per-workflow fields including `max_concurrency` (YAML), `recursion_limit` (YAML), `max_iteration_key_output_limit` (DB column, default 200)
- `src/codemie/workflows/constants.py` — `RECURSION_LIMIT = 50` (fallback); all iteration state-schema key names
- `src/codemie/workflows/execution_config_schema.yaml` — JSON Schema for YAML workflow config validation; must be updated for any new YAML fields
- `docker-compose.yml` — Does NOT set any `WORKFLOW_*` env vars; operators must add them explicitly to tune for nested iteration workloads

### Feature Flags and Deployment Concerns

- **`WORKFLOW_GENERATION_ENABLED` (default `False`)** — When `True`, the workflow generator routes are mounted; the generator includes `iter_key` schema validation logic (`workflow_generator/schemas.py`) that will need updating to understand nested `iter_key` semantics.
- **`config.verbose` (always `False`)** — Controls `workflow.compile(debug=...)`. No env-var path; requires code change to enable debug mode for nested iteration diagnostics.
- **No dedicated flag for nested iterations** — The partial groundwork (`is_in_iteration` check) is unconditional. No feature flag gates nested behavior.

**Deployment concerns:**

1. **LangGraph recursion limit**: default `RECURSION_LIMIT = 50`. Nested iterations multiply recursion depth: outer N branches × inner M branches × steps per branch. Even modest nesting (N=3, M=3, 5 steps each) reaches 45 recursion depth. Operators must raise `recursion_limit` in workflow YAML for nested configs.

2. **Thread starvation**: `max_concurrency` is capped at `WORKFLOW_MAX_CONCURRENCY = 5`. A nested iteration can produce up to `N × M` concurrent tasks competing for the thread pool (`THREAD_POOL_MAX_WORKERS = 20`). Deep nesting at scale risks thread starvation deadlock.

3. **JSONB size limit**: `MAX_JSONB_SIZE_BYTES = 1_000_000` (`utils.py:549`). With the default `include_in_iterator_context: ["*"]`, every nested branch carries a full copy of `context_store`. Nested iterations make context duplication quadratic.

4. **`max_iteration_key_output_limit = 200`**: Limits per-branch output size; currently no partition or reset for inner loops.

5. **`WORKERS = 1` default**: Single uvicorn worker — all nested iteration concurrency runs in one process; no horizontal scaling path exists at the application level.

---

## 6. Risk Indicators

- **`ITERATION_NODE_NUMBER_KEY` has no depth dimension**: A single flat integer tracks "current branch index." True nested iterations (requirement 5) need independent counters per depth level. The current data model has no field for this. Adding a new constant and typed field to `AgentMessages` is required — this touches the LangGraph state schema, which is a high-impact change.

- **`AgentMessages` TypedDict does not declare iteration keys as typed fields**: `ITERATION_NODE_NUMBER_KEY`, `TOTAL_ITERATIONS_KEY`, `FIRST_STATE_IN_ITERATION`, and `ITER_SOURCE` all flow as untyped extra keys. LangGraph's state merging may silently drop or mishandle untyped keys during reducer application. Any new nested iteration keys should be declared as typed fields.

- **Lambda closure bug in `_handle_single_state` at line 698**: The lambda uses `self` in the body instead of `_self`, and passes `_self` as the first positional argument (making `state_schema` receive the executor instance). This is an existing bug that may surface or be inadvertently fixed during the nested iteration change.

- **`WorkflowExecutionState.iteration_number` DB column is a flat integer**: Nested iterations produce multiple states with identical `iteration_number` values from different depths. Either a second column (`outer_iteration_number`) or a changed column type is needed. This requires an Alembic migration.

- **`finish_iteration` semantics are inconsistent with labeled behavior** (documented by TC_IMR_010): With `finish_iteration=True` and a list `ITER_SOURCE`, the code iterates the list rather than stopping. This pre-existing inconsistency may compound for nested configurations.

- **Context reference sharing in nested mode lacks Python-level isolation**: `_build_parallel_context` returns the original `context_store` reference when `is_in_iteration=True`. If any inner-branch node writes to the context store in a way that is not properly handled by LangGraph's copy-on-write, concurrent branches may observe partial mutations. No test exercises concurrent write scenarios.

- **`include_in_iterator_context` whitelist is silently ignored in nested mode**: Users configuring whitelist filtering at the outer iteration level may not realize the whitelist does not apply to inner branches. No warning, no documentation note, and no test verifying intent.

- **No existing YAML template for nested iteration**: Implementing and validating the feature requires creating a new template; there is no prior art in the codebase showing the intended YAML syntax for a two-level `iter_key` configuration.

- **`find_map_nodes` only does shallow lookup** (`state.next.state_id or state.next.condition.then`): For a workflow where Node A has `iter_key` → Node B, and Node B has `iter_key` → Node C, `find_map_nodes` will collect both B and C as map nodes, but only because it iterates all states. There is no recursive-descent or depth-tracking logic. If behavior changes based on depth (e.g., first-level map nodes vs. nested map nodes), `find_map_nodes` must be extended.

- **`WORKFLOW_MAX_CONCURRENCY = 5` and `RECURSION_LIMIT = 50` are runtime bottlenecks for nested iteration at any meaningful scale**: No code changes can address these without operators explicitly tuning the values. Documentation or a warning in the validator is needed.

- **No codegraph MCP indexing available**: Research conducted via filesystem fallback only; some cross-file call chains (especially through dynamic dispatch or async callbacks) may not have been fully traced.

---

## 7. Summary for Complexity Assessment

The nested iterations task (EPMCDME-4289) touches the Workflow/Orchestration layer deeply — specifically `WorkflowExecutor.continue_iteration`, `_build_parallel_context`, `_handle_single_state`, and `find_map_nodes` in `src/codemie/workflows/workflow.py` — as well as the Data Models layer (`WorkflowNextState`, `AgentMessages` TypedDict), the Node Layer (`BaseNode._add_iteration_state`, `_prepare_iter_task_messages`, `AgentNode.get_node_name`), and the Persistence layer (`WorkflowExecutionState.iteration_number` DB column). The minimum change surface is 5–7 source files; with test additions, schema changes, and YAML template creation, the realistic surface is 10–14 files. The database column change (`iteration_number`) requires a new Alembic migration, adding a deployment step.

The task introduces a genuinely new semantic into the workflow engine. The partial groundwork (the `is_in_iteration` boolean check and the shared-reference path in `_build_parallel_context`) shows intent but not completion. The core design decision — whether to use a flat "outer/inner" pair of counter constants or a stack-based nesting mechanism — has not been made and will determine the scope. Using a pair of constants (`OUTER_ITERATION_NODE_NUMBER_KEY` + `INNER_ITERATION_NODE_NUMBER_KEY`) is the minimal-change approach; a stack-based mechanism would handle arbitrary nesting depth but is a larger design change. Either approach must update `AgentMessages`, `constants.py`, `_add_iteration_state` in `BaseNode`, and potentially the DB schema.

Test coverage for the existing single-level iteration is thorough (12 dedicated test cases). However, all tests invoke `continue_iteration` as a direct unit call — no integration-level test builds a `StateGraph` with two nested `iter_key` transitions end-to-end. The nested iteration task will require at minimum 5–7 new test cases covering: independent inner counters, `TOTAL_ITERATIONS_KEY` semantics at inner depth, full `ITER_SOURCE` round-trip, empty inner collection behavior, and `init_workflow_edges` edge registration for a two-level `iter_key` config. The lambda closure bug in `_handle_single_state` (line 698 of `workflow.py`) is an independent defect that should be resolved before or alongside this work to avoid masking integration failures during testing.
