# Spec: EPMCDME-11609 Increment 2a — SubWorkflowNode Execution Logic

## Overview

Implement the synchronous-block execution path for the sub-workflow node type. When a `WorkflowState` has `workflow_id` set, the parent workflow suspends at that node while the child workflow runs to completion inline (same thread, blocking). The parent resumes once the child finishes and its output is mapped back to the parent context.

Increment 1 foundation (models, migrations, config vars, resource validation) is already on the branch. Increment 2a wires the execution logic only.

---

## Components

### 1. `SubWorkflowNode` — `src/codemie/workflows/nodes/sub_workflow_node.py`

Subclass of `BaseNode`. Registered via `nodes/__init__.py`.

**Constructor** — stores `workflow_state.workflow_id`, `workflow_state.input_mapping`, `user`, and `execution_id` (the parent's). Accepts all standard `BaseNode` kwargs.

**`get_task(state_schema, **kwargs) -> str`**
Returns `"Executing sub-workflow {child_workflow_name}"`.

**`execute(state_schema, execution_context) -> str`**

Sequence:
1. **Flag check** — `if not config.ENABLE_SUB_WORKFLOW_NODE: raise FeatureDisabledError("Sub-workflow node is disabled")`
2. **Resolve child config** — `WorkflowService().get_workflow(self.sub_workflow_id, self.user)`; raise `ValueError` if not found.
3. **Nesting depth check** — call `WorkflowService.get_nesting_depth(self.execution_id)` to walk the `parent_execution_id` chain. Effective max = `child_config.max_nesting_level or config.WORKFLOW_MAX_NESTING_DEPTH`. Raise `WorkflowNestingDepthExceededError` if `depth >= effective_max`.
4. **Render child input**:
   - If `self.input_mapping` is set: for each `key: template` pair, render `jinja2.Template(template).render(**context_store)`. Serialize the rendered dict as JSON. Pass as `user_input`.
   - Otherwise: serialize the full parent `context_store` as JSON. Pass as `user_input`. This gives the child its initial context.
5. **Create child execution** — `WorkflowService.create_workflow_execution(child_config, self.user, child_input, parent_execution_id=self.execution_id)`. This call (a) writes `parent_execution_id` on the child row and (b) sets `active_sub_execution_id` on the parent row.
6. **Create child executor** — `WorkflowExecutor.create_executor(child_config, self.user, execution_id=child_execution.execution_id, user_input=child_input)` with an isolated `ThoughtQueue` (not connected to the parent's stream).
7. **Run child synchronously** — `child_executor.stream()`. Blocks until the child workflow completes (success, failure, or abort).
8. **Check child status** — re-fetch `WorkflowService.find_workflow_execution_by_id(child_execution.execution_id)`. If status is `FAILED`, raise `SubWorkflowExecutionError`. If `ABORTED`, raise `ExecutionAbortedException`.
9. **Clear `active_sub_execution_id`** — fetch parent execution row, set `active_sub_execution_id = None`, save.
10. **Return output** — return `finished_child_exec.output or ""`. The base class `_add_output_key` writes this string to `context_store[output_key]` (if `output_key` is configured on the state's `next` transition).

**`post_process_output(state_schema, task, output) -> str`**
Returns `str(output)` unchanged.

---

### 2. `WorkflowService` additions — `src/codemie/service/workflow_service.py`

**`get_nesting_depth(execution_id: str) -> int`** (static or class method)
Walk the `parent_execution_id` chain: fetch execution, check `parent_execution_id`, repeat until `None`. Return the hop count (0 = top-level, 1 = direct child, etc.). Capped at `WORKFLOW_MAX_NESTING_DEPTH + 1` to prevent unbounded DB queries on corrupted data.

**`create_workflow_execution(...)` extension**
Add `parent_execution_id: Optional[str] = None` keyword argument. When set:
- Write `parent_execution_id` onto the new `WorkflowExecution` child row.
- Fetch the parent `WorkflowExecution` row and set `active_sub_execution_id = child_execution.execution_id`, then save.

---

### 3. Dispatch branch — `src/codemie/workflows/workflow.py`

In `initialize_node()`, after `elif state.tool_id:` and before the bare `else: raise ValueError`:

```python
elif state.workflow_id:
    if not config.ENABLE_SUB_WORKFLOW_NODE:
        raise FeatureDisabledError("Sub-workflow node is disabled. Set ENABLE_SUB_WORKFLOW_NODE=true to enable.")
    node = self.init_sub_workflow_node(state)
    workflow.add_node(state.id, node, retry=retry_policy, defer=is_convergence_node)
```

Add `init_sub_workflow_node(self, state: WorkflowState) -> SubWorkflowNode` on `WorkflowExecutor` — constructs `SubWorkflowNode` with standard kwargs plus `workflow_state=state`, `user=self.user`, `execution_id=self.execution_id`.

Add `SubWorkflowNode` to the `nodes/__init__.py` export.

---

### 4. Feature flag guard in resource validation — `src/codemie/workflows/validation/resources.py`

At the top of `_validate_sub_workflow_availability()`:

```python
if not config.ENABLE_SUB_WORKFLOW_NODE:
    return []
```

This prevents spurious validation errors when the feature is disabled (workflows with `workflow_id` states are still valid configs; they are just not executable yet).

---

### 5. Deploy template — `deploy-templates/values.yaml`

Add four new env vars under the existing `extraEnv` / `customEnv` section:

```yaml
- name: ENABLE_SUB_WORKFLOW_NODE
  value: "false"
- name: WORKFLOW_MAX_NESTING_DEPTH
  value: "1"
- name: WORKFLOW_POOL_ENABLED
  value: "false"          # safer default; pool wiring not yet complete
- name: WORKFLOW_POOL_MAX_AGE_SECONDS
  value: "3600"
```

---

## Error Types

| Error | When raised | Where defined |
|---|---|---|
| `FeatureDisabledError` | Flag is `False` at dispatch or in node | `core/exceptions.py` (check if exists; add if not) |
| `WorkflowNestingDepthExceededError` | Depth >= effective max | `core/exceptions.py` |
| `SubWorkflowExecutionError` | Child finished with `FAILED` status | `core/exceptions.py` |
| `ExecutionAbortedException` | Child finished with `ABORTED` status | Already exists in `nodes/base_node.py` |

---

## Child Execution Isolation

- **ThoughtQueue**: child executor is instantiated with a fresh `ThoughtQueue()`. Thoughts and progress events from the child are persisted to DB (via `ThoughtConsumer`) but are not streamed to the parent's client.
- **LangGraph thread_id**: child executor uses `child_execution.execution_id` as its LangGraph `thread_id`. No checkpoint namespace sharing with parent.
- **Context store**: child starts with its own initial context derived from the rendered input (no inheritance of parent's full LangGraph state).

---

## Data Flow Summary

```
Parent node execute() call
  → SubWorkflowNode.execute()
      → flag check
      → nesting depth check
      → render child_input from input_mapping OR full parent context_store
      → WorkflowService.create_workflow_execution(parent_execution_id=parent_id)
          → parent.active_sub_execution_id = child_id  [DB write]
      → WorkflowExecutor.create_executor(isolated ThoughtQueue)
      → child_executor.stream()          [BLOCKS until child done]
      → read child.output from DB
      → parent.active_sub_execution_id = None  [DB write]
      → return child.output
  ← BaseNode._add_output_key writes child.output → context_store[output_key]
```

---

## Testing (TDD order)

All test areas are greenfield. Write failing tests first, implement, confirm green.

| # | Test file | What to cover |
|---|---|---|
| T1 | `tests/.../nodes/test_sub_workflow_node.py` | Flag disabled → `FeatureDisabledError` |
| T2 | `tests/.../nodes/test_sub_workflow_node.py` | Depth exceeded → `WorkflowNestingDepthExceededError` |
| T3 | `tests/.../nodes/test_sub_workflow_node.py` | input_mapping renders Jinja against context_store |
| T4 | `tests/.../nodes/test_sub_workflow_node.py` | input_mapping absent → full context_store passed as JSON |
| T5 | `tests/.../nodes/test_sub_workflow_node.py` | Happy path: child succeeds → output returned, active_sub_execution_id cleared |
| T6 | `tests/.../nodes/test_sub_workflow_node.py` | Child fails → `SubWorkflowExecutionError` raised |
| T7 | `tests/.../service/test_workflow_service.py` | `get_nesting_depth`: top-level = 0, one parent = 1, cap at max+1 |
| T8 | `tests/.../service/test_workflow_service.py` | `create_workflow_execution` with `parent_execution_id` sets lineage fields |
| T9 | `tests/.../workflows/test_config_resources_validation.py` | Flag `False` → `_validate_sub_workflow_availability` returns `[]` without any DB call |
| T10 | `tests/.../workflows/test_workflow_state_transitions.py` | `initialize_node` dispatches `workflow_id` state to `SubWorkflowNode` when flag is `True` |

---

## Out of Scope for 2a

- Interrupt/resume wiring (`active_sub_execution_id` detection in resume endpoint) — Increment 2c
- `GET /workflows/selectable` endpoint — Increment 2b
- Workflow pool (`WORKFLOW_POOL_ENABLED`) — deferred
