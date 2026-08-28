# Technical Research

**Task**: sub-workflow node input output propagation workflow state
**Generated**: 2026-08-04T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

The SubWorkflowNode currently passes only the parent's raw user_input as the child workflow's input. The correct behaviour is: if the previous node in the parent workflow is the START node, use user_input; if the previous node is any other node (e.g. an assistant node), use that node's output as the child's input. Additionally, the child sub-workflow's own last-node output (i.e. what it produces at its END) must be captured and returned as the SubWorkflowNode's output to the parent workflow, and that output should flow through to the parent's context_store under the sub-workflow node's name (the same way an AssistantNode output does today).

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/workflows/nodes/sub_workflow_node.py` — SubWorkflowNode: creates/resumes child workflow, constructs `child_input` via `_render_input`, calls `WorkflowExecutor.create_executor`, streams the child, returns `finished_child.output or ""`
- `src/codemie/workflows/nodes/base_node.py` — BaseNode: abstract `__call__` lifecycle — `execute()` → `post_process_output()` → `finalize_and_update_state()` → context_store merge; also `_add_output_key()` for explicit keying
- `src/codemie/workflows/workflow.py` — WorkflowExecutor: compiles the LangGraph StateGraph, `on_workflow_start` initialises child graph state, `init_sub_workflow_node()` factory at line 886, `initialize_node()` branches on `state.workflow_id`
- `src/codemie/workflows/constants.py` — all graph-state key constants (see Section 5)
- `src/codemie/core/workflow_models/workflow_models.py` — WorkflowState, WorkflowNextState (`output_key`, `store_in_context`, etc.)
- `src/codemie/core/workflow_models/workflow_execution.py` — WorkflowExecution SQLModel; `output: Optional[str]` field; per-state output lives in `WorkflowExecutionState.output`, not this field
- `src/codemie/workflows/nodes/assistant_node.py` — reference implementation: calls `assistant.invoke_task`, `post_process_output` returns `output.result`; output propagates to context_store through `finalize_and_update_state` → `parse_from_string_representation` → dict merge, or via explicit `output_key`
- `src/codemie/workflows/models.py` — `AgentMessages` TypedDict; `context_store` annotated with `add_or_replace_context_store` reducer

### `_render_input` current logic (sub_workflow_node.py lines 158-169)

Three branches, evaluated in order:

1. `input_mapping` is set → Jinja2-render each template against `context_store`, return `json.dumps(rendered_dict)`
2. `not context_store` (empty dict) → return `state_schema.get(USER_INPUT) or ""`
3. Fallback → return `json.dumps(context_store)` (entire context store as JSON)

**Critical gap**: Branch 2 only fires when `context_store` is an empty dict. It does NOT check whether the immediately preceding node was the START node. If a prior non-START node ran but wrote nothing to `context_store`, branch 3 fires and dumps an empty JSON object, not `user_input`. If the sub-workflow node is the first node in the workflow, `context_store` will be empty only if the JSON-parsed `user_input` did not produce a dict — an unreliable proxy for "previous node was START".

### `execute` child input path (sub_workflow_node.py line 106)

```
child_input = self._render_input(state_schema)
WorkflowService.create_workflow_execution(child_config, user, child_input, ...)
WorkflowExecutor.create_executor(child_config, child_input, user, ...)
```

### `post_process_output` current behavior (sub_workflow_node.py lines 155-156)

```python
def post_process_output(self, ...):
    return str(output)
```

Converts whatever `execute()` returned to a string. No extraction logic.

### `execute` return value (sub_workflow_node.py line 153)

```python
return finished_child.output or ""
```

`finished_child` is the `WorkflowExecution` DB row re-fetched after streaming. Because `WorkflowExecution.output` is **never set on the success path** (confirmed in `WorkflowExecutionService.finish()` at line 198 — it reads but never writes `output`), `finished_child.output` is `None` for every successful child, so `execute()` always returns `""`. The SubWorkflowNode is effectively a data black hole.

### How AssistantNode output reaches context_store

1. `post_process_output` returns `output.result` (a plain string or JSON string).
2. `finalize_and_update_state` → `_collect_new_values` → `parse_from_string_representation(processed_output)`.
3. If the string parses to a `dict`, every key is merged into `context_store`.
4. If `workflow_state.next.output_key` is set: `_add_output_key()` writes `context_store[output_key] = actual_value` explicitly, bypassing the parse path.
5. There is **no automatic "write under node name"** behavior — the node name is never used as a context_store key unless the node's output string itself is a JSON dict with an appropriately named key, or `output_key` is configured.

### Architecture and Layers Affected

| Layer | Component | Change needed |
|---|---|---|
| Node execution | `SubWorkflowNode._render_input()` | Replace empty-context heuristic with START-node identity check |
| Node execution | `SubWorkflowNode.execute()` | Replace `finished_child.output` with last `WorkflowExecutionState.output` lookup |
| Node execution | `SubWorkflowNode.post_process_output()` | May need light adaptation once `execute()` returns real content |
| Persistence (read-only) | `WorkflowExecutionService` or `WorkflowService` | Add or use existing method to fetch the last `WorkflowExecutionState` for a completed child |
| Graph state | `AgentMessages` / constants | May need a START node name constant if one is not already used at runtime |

No changes to WorkflowExecutor, BaseNode, or the context_store reducer are required by the task.

### Integration Points

- `SubWorkflowNode` → `WorkflowService.create_workflow_execution` (create path)
- `SubWorkflowNode` → `WorkflowExecutor.create_executor` (graph instantiation, circular import guarded with local import inside `execute()`)
- `SubWorkflowNode` → `WorkflowExecutionService.find_workflow_execution_by_id` (DB re-fetch of finished child)
- `SubWorkflowNode` → `workflow_pool` (acquire/release on the create path)
- `BaseNode._add_output_key()` → `CONTEXT_STORE_VARIABLE` reducer — the mechanism the fix will leverage to surface the child output into parent context_store
- `on_workflow_start` (workflow.py line 1126) — sets `CONTEXT_STORE_VARIABLE: parse_from_string_representation(user_input)` as initial child state; the child's `user_input` arg is `child_input`

### Patterns and Conventions

- All nodes inherit `BaseNode`; output lifecycle is `execute() → post_process_output() → finalize_and_update_state()`. The fix stays within this contract.
- Context-store write via `output_key` field on `WorkflowNextState` is the canonical "write under a specific key" mechanism; `_add_output_key()` is the code path.
- Context-store write via parsed-dict merge (`parse_from_string_representation`) is the automatic path; requires the processed output to be valid JSON representing a dict.
- `PREVIOUS_EXECUTION_STATE_NAMES` is a list of node `id` strings in the graph state; it reflects which nodes completed immediately before the current node (supports fan-out scenarios). When the current node is the first in the graph, this list will contain the LangGraph START sentinel (`"__start__"` — LangGraph's `START` constant).
- Circular import between `SubWorkflowNode` and `WorkflowExecutor` is already handled with a local import inside `execute()`; do not add a module-level import.
- Nodes must not access the DB directly — `WorkflowService` and `WorkflowExecutionService` are the correct service entry points.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/workflows/langgraph-workflows.md` — canonical authority on `WorkflowExecutor`/`StateGraph`; nodes must extend `BaseNode`; no parallel graph execution paths; `WorkflowExecutor.create_executor` at `workflow.py:112` is the canonical entry point
- `.ai-run/guides/architecture/layered-architecture.md` — nodes stay thin (call services, not DB); services own orchestration
- `.ai-run/guides/architecture/service-layer-patterns.md` — `WorkflowExecutor.init_sub_workflow_node()` is the factory; dispatch branches on `state.workflow_id`
- `docs/workflows/05_context_management.md` — full lifecycle of `context_store`: JSON user-input pre-populates it before first state; each state appends via `store_in_context`; `output_key` / `clear_context_store` / `reset_keys_in_context_store` flags; Jinja2 `{{var}}` syntax for reads
- `docs/workflows/03_workflow_states.md` — state schema for all four node types; `resolve_dynamic_values_in_prompt`, `output_schema`, `interrupt_before`, `retry_policy` patterns

### Prior Task Specs (directly relevant)

- `docs/superpowers/tasks/2026-07-29-epmcdme-11609-sub-workflow-node/spec.md` — Increment 1 (foundation): `workflow_id` as exclusive discriminant; `input_mapping: Optional[dict[str,str]]`; lineage fields
- `docs/superpowers/tasks/2026-07-30-epmcdme-11609-sub-workflow-exec/spec.md` — Increment 2a: full `execute()` sequence spec, data-flow diagram, child isolation rules, **output capture contract** — the spec states `finished_child.output` should be the surfaced value, but the success path never sets it (confirmed bug)
- `docs/superpowers/tasks/2026-07-30-epmcdme-11609-2b-selectable-resume/spec.md` — Increments 2b+2c: interrupt/resume; `active_sub_execution_id` must be written before `child_executor.stream()`

### Architectural Decisions

1. `input_mapping` (Jinja2 templates rendered against parent `context_store`) takes precedence over all other input-selection logic; this path is not changed by the current task.
2. Only `finished_child.output` is specified as the data to surface to the parent; the prior spec intended `BaseNode._add_output_key()` as the write mechanism.
3. Child execution is fully isolated: fresh `ThoughtQueue`, own LangGraph `thread_id` (`child_execution.execution_id`), no checkpoint namespace sharing.
4. `ENABLE_SUB_WORKFLOW_NODE` (default `True`) gates dispatch; disabling raises `FeatureDisabledError`.
5. `WORKFLOW_MAX_NESTING_DEPTH = 1` global default; per-workflow `max_nesting_level` can lower it.

### Derived Conventions

- "Previous node was START" is correctly detected by checking whether `PREVIOUS_EXECUTION_STATE_NAMES` in the current graph state contains the LangGraph START sentinel. The list will be `["__start__"]` (or contain it) when no real node has yet executed. This is more robust than the current empty-context-store heuristic.
- "Previous non-START node's output" is most reliably obtained by fetching `WorkflowExecutionState` using `PREVIOUS_EXECUTION_STATE_ID` (the DB UUID already in graph state), rather than trying to reconstruct it from `context_store` keys, because context_store key names are not guaranteed to match node identity.
- For `finished_child.output` to work, either the success path of `WorkflowExecutionService.finish()` must be modified to persist the last-state output onto `WorkflowExecution.output`, OR `SubWorkflowNode.execute()` must independently query the child's last `WorkflowExecutionState`.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/workflows/nodes/test_sub_workflow_node.py` — covers: feature-flag guard, nesting-depth check, `input_mapping` Jinja rendering, full-context-store-as-JSON passthrough, empty-context-store → user_input fallback (T4b), happy-path execute return value, child FAILED/INTERRUPTED/ABORTED status handling, `active_sub_execution_id` lifecycle, resume path, pool acquire/release
- `tests/codemie/workflows/test_base_node_lifecycle.py` — full `BaseNode.__call__` lifecycle, `output_key` storage, `append_to_context`/`reset_keys_in_context_store` mechanics, `PREVIOUS_EXECUTION_STATE_NAMES` read/write, `MockNode(BaseNode)` subclass pattern

### Testing Framework and Patterns

- pytest 8.3.x with pytest-asyncio 0.23.x, pytest-mock 3.14.x
- Module-scoped `autouse=True` fixture using `patch()` to suppress `workflow_pool` singleton
- `MagicMock()` instances for `workflow_state`, `workflow_execution_service`, `node`, `state_schema` as shared fixtures
- `side_effect = [obj1, obj2, ...]` lists on `find_workflow_execution_by_id` to simulate ordered DB call chain
- `captured_input` dict via `side_effect` override on `create_workflow_execution` for assertion of child input value
- Class-based grouping: `TestSubWorkflowNodePoolIntegration`, `TestPrecedingStateName`, etc.
- `MockNode(BaseNode)` concrete subclass with overrideable `execute_impl` and `mock_execute_result` for BaseNode lifecycle tests

### Coverage Gaps

The following scenarios required by this task have no existing tests:

- **START-node input selection by node identity**: no test verifies that when `PREVIOUS_EXECUTION_STATE_NAMES = ["__start__"]`, `_render_input` returns `state_schema[USER_INPUT]`, regardless of whether `context_store` is empty or not
- **Non-START previous-node output as child input**: no test verifies that when `PREVIOUS_EXECUTION_STATE_NAMES = ["assistant_node"]`, `_render_input` extracts and passes the correct previous-node output (not the whole `context_store` JSON dump)
- **Child output stored in parent context_store under sub-workflow node name**: no test calls `node(state_schema)` (full `__call__` path) and asserts `result[CONTEXT_STORE_VARIABLE]["<node_name>"] == "child last-node output"`
- **Resume path input selection**: T9 does not verify input selection logic for START vs. non-START previous node on the resume branch
- **ABORTED child**: the `ABORTED` branch (sub_workflow_node.py line 147-148) has no dedicated test

---

## 5. Configuration and Environment

### Environment Variables

- `ENABLE_SUB_WORKFLOW_NODE` (default `True`) — master feature flag; gates the sub-workflow REST endpoint, node construction, and graph wiring; disabling raises `FeatureDisabledError`
- `WORKFLOW_MAX_NESTING_DEPTH` (default `1`) — caps recursion depth; enforced in `sub_workflow_node.py` and `workflow_service.py`
- `WORKFLOW_POOL_ENABLED` (default `True`) — enables the pre-warmed sub-workflow graph pool
- `SUBWORKFLOW_POOL_MAX_SIZE` (default `5`) — ceiling on pool size per workflow
- `WORKFLOW_POOL_MAX_AGE_SECONDS` (default `3600`) — TTL for pooled graphs before eviction
- `SUBWORKFLOW_POOL_WARMUP_INTERVAL_SECONDS` (default `60`) — background warmup poll interval

### Configuration Files

- `src/codemie/configs/config.py` — central Pydantic `BaseSettings`; all workflow feature flags and pool tuning live here
- `src/codemie/workflows/constants.py` — graph-state key constants consumed by all nodes and the executor
- `.env` — live overrides (currently only `ENABLE_USER_MANAGEMENT=True`; no sub-workflow overrides)
- `tests/.env.test` — test environment overrides

### Key Constants (constants.py)

- `USER_INPUT = "user_input"` — raw user message key in graph state; `_render_input` branch 2 reads from here
- `CONTEXT_STORE_VARIABLE = "context_store"` — inter-node output dict key; already read by `_render_input`
- `PREVIOUS_EXECUTION_STATE_NAMES = "previous_execution_state_names"` — list of node ID strings; **the field needed to detect if previous node is START**
- `PREVIOUS_EXECUTION_STATE_ID = "previous_execution_state_id"` — DB UUID of last `WorkflowExecutionState`; **the field needed to look up previous node's output from DB**
- `RESULT_FINALIZER_NODE = "result_finalizer_node"` — name of the terminal assembly node in a workflow; the child sub-workflow's "last-node output" is the output of this node in the child's execution history
- `END_NODE = "end"` / `END_STATE = "__end__"` — LangGraph terminal markers
- `RECURSION_LIMIT = 50` — LangGraph recursion cap for graph invocation
- `RESULT_PREFIX = "Result:"` — prefix convention for final-output messages

### Feature Flags and Deployment Concerns

- `ENABLE_SUB_WORKFLOW_NODE`: this task changes the behavior of a guarded feature; no flag changes are needed
- `WORKFLOW_POOL_ENABLED`: pool acquire/release paths in `execute()` are not changed by this task, but any refactor of `execute()` must preserve the acquire/release symmetry

---

## 6. Risk Indicators

- **`finished_child.output` is always `None` on the success path** — `WorkflowExecutionService.finish()` reads but never writes `WorkflowExecution.output`. The existing `execute()` return value `finished_child.output or ""` always returns `""` for successful child workflows. The fix must either (a) write `output` in `finish()` using the last `WorkflowExecutionState.output`, or (b) have `SubWorkflowNode.execute()` independently query the last `WorkflowExecutionState` for the child. Path (b) requires knowing what query to use; path (a) touches `WorkflowExecutionService.finish()`, which is called by all workflow types — regression surface is wider than `SubWorkflowNode` alone.
- **No `previous_node` identity field in runtime graph state** — `PREVIOUS_EXECUTION_STATE_NAMES` holds node `id` strings (not display names), and `__start__` may or may not appear depending on LangGraph version behavior. The exact value in the list when SubWorkflowNode is the first node needs confirmation by reading the LangGraph source or an integration test; cannot be verified by static analysis alone.
- **`context_store` empty-dict heuristic is fragile** — the current "empty context_store = previous node was START" logic is incorrect if a prior node ran but wrote nothing to `context_store`. The fix must not rely on this heuristic.
- **No "write under node name" automatic behavior** — context_store keys are either parsed from the node's JSON output dict or written via explicit `output_key` config. For the child output to appear in the parent's `context_store` under the sub-workflow node's own name, either (a) `output_key` must be set on the `WorkflowNextState`, or (b) `post_process_output` must return a JSON string like `'{"<node_name>": "<child_output>"}'`. The requirement says "under the sub-workflow node's name," which implies the fix must construct this key automatically using `self.name` (or equivalent node name attribute) — confirm the attribute name on `BaseNode` or `SubWorkflowNode`.
- **Missing tests for all new behavior** — three major gaps (START detection, previous-node output extraction, context_store propagation) have no existing test fixtures; new tests are required alongside the code change.
- **Circular import guard** — `SubWorkflowNode` imports `WorkflowExecutor` with a local import inside `execute()`. Any additional service call added to `execute()` must not introduce a new module-level circular import.
- **Resume path not covered by the fix requirements** — the task description does not mention the resume path. Input selection on the resume path (T9) currently also uses `_render_input`; the new START-detection logic will apply on resume too, which may be correct but should be verified.
- **`WorkflowExecution.output` historical nullability** — code comment in `workflow_execution_history_formatter.py` line 117 states: "For historical reasons, WorkflowExecution.output is often empty, and the real output is stored in the last workflow execution state." Any fix that writes to `WorkflowExecution.output` must be consistent with this documented invariant.

---

## 7. Summary for Complexity Assessment

This task touches a single architectural layer — the node execution layer — with `SubWorkflowNode` being the primary change target. The estimated file change surface is 2–3 files: `sub_workflow_node.py` (all changes), possibly `WorkflowExecutionService` or `WorkflowService` if the child-output retrieval requires a new query method, and the test file. No changes to `BaseNode`, `WorkflowExecutor`, or the context_store reducer are required by the task specification. The two sub-problems (input selection and output capture) are independent and can be reasoned about separately.

The input-selection fix is moderately novel: it requires replacing an unreliable empty-dict heuristic with a START-node identity check using `PREVIOUS_EXECUTION_STATE_NAMES`. The constant value for the LangGraph START sentinel (`"__start__"`) is well-known and already present in `constants.py` as `END_STATE`'s counterpart, but the exact runtime value in `PREVIOUS_EXECUTION_STATE_NAMES` when the sub-workflow node is the first real node should be confirmed. The output-capture fix is straightforward in intent but has a structural complication: `WorkflowExecution.output` is never written on the success path, so retrieving the child's last-node output requires either a targeted DB query for the last `WorkflowExecutionState` of the child, or a coordinated change to `WorkflowExecutionService.finish()`. The latter approach has broader regression surface because `finish()` is invoked by all workflow types.

Test coverage for the affected area is partially established (happy-path execute, input fallback heuristic, pool integration) but all three behaviors the task introduces are currently untested — START-node identity detection, previous-node output extraction, and child-output propagation into parent context_store. This means the implementation cannot rely on regression-test safety from existing tests; new test cases must be written alongside the code changes. Overall complexity is moderate: the logic changes are small and well-bounded, but the output-capture path requires careful diagnosis of the `WorkflowExecution.output` nullability issue to avoid introducing a silent no-op.
