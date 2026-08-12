# Spec: Nested Iterations Support (EPMCDME-4289)

## Problem

The workflow engine supports single-level fan-out via `iter_key`. When a node inside an outer
parallel branch also defines an `iter_key`, the engine enters a "nested iteration" path but uses
the **outer branch index** for all inner branches instead of issuing independent inner counters.
As a result, requirements 3 and 5 from the ticket are unmet:

- inner branches all share the same `ITERATION_NODE_NUMBER_KEY` value (the outer index)
- the outer counter is overwritten and lost for downstream display / state tracking

## Root Cause

`continue_iteration` constructs each `Send` with:

```python
ITERATION_NODE_NUMBER_KEY: iter_number if iter_number else index + 1,
```

When `is_in_iteration=True` (outer `iter_number > 0`), every inner `Send` receives
`iter_number` (the outer counter), not `index + 1` (an independent inner counter).

A second issue: there is nowhere to store the outer counter and outer total while inner
branches run. `AgentMessages` has no typed fields for them, so they are lost.

## Solution

### 1. New constants (`constants.py`)

Add two new keys for the outer-depth snapshot:

```python
OUTER_ITERATION_NODE_NUMBER_KEY = "outer_iteration_node_number"
OUTER_TOTAL_ITERATIONS_KEY      = "outer_total_iterations"
```

### 2. Typed fields in `AgentMessages` (`models.py`)

Add the four iteration counter keys as typed fields so LangGraph's reducer merges
them deterministically:

```python
iteration_node_number:       Annotated[Optional[int], lambda left, right: right if right is not None else left]
total_iterations:            Annotated[Optional[int], lambda left, right: right if right is not None else left]
outer_iteration_node_number: Annotated[Optional[int], lambda left, right: right if right is not None else left]
outer_total_iterations:      Annotated[Optional[int], lambda left, right: right if right is not None else left]
```

Choosing `right if right is not None else left` ensures later writes win without
accidentally erasing a counter that was set at an outer depth.

### 3. Fix `continue_iteration` (`workflow.py`)

When `is_in_iteration=True`, save the current outer counter before building the
inner `Send` actions and emit `index + 1` as the new inner counter:

```python
is_in_iteration = iter_number is not None and iter_number > 0

outer_iter_number = iter_number if is_in_iteration else None
outer_total       = state_schema.get(TOTAL_ITERATIONS_KEY) if is_in_iteration else None

send_actions = [
    Send(
        send_to_node,
        {
            TASK_KEY:                            item,
            MESSAGES_VARIABLE:                   messages.copy() if not is_in_iteration else messages,
            CONTEXT_STORE_VARIABLE:              parallel_context,
            ITERATION_NODE_NUMBER_KEY:           index + 1,       # always the inner counter
            TOTAL_ITERATIONS_KEY:                total_iterations,
            OUTER_ITERATION_NODE_NUMBER_KEY:     outer_iter_number,
            OUTER_TOTAL_ITERATIONS_KEY:          outer_total,
            FIRST_STATE_IN_ITERATION:            iter_key not in state_schema,
            PREVIOUS_EXECUTION_STATE_ID:         state_schema.get(PREVIOUS_EXECUTION_STATE_ID),
            PREVIOUS_EXECUTION_STATE_NAMES:      state_schema.get(PREVIOUS_EXECUTION_STATE_NAMES),
        },
    )
    for index, item in enumerate(items_to_process)
]
```

Key changes vs. current:
- `ITERATION_NODE_NUMBER_KEY: index + 1` (was `iter_number if iter_number else index + 1`)
- `OUTER_ITERATION_NODE_NUMBER_KEY: outer_iter_number` (new)
- `OUTER_TOTAL_ITERATIONS_KEY: outer_total` (new)

### 4. Fix `_handle_single_state` lambda (`workflow.py`)

The current lambda has confusing variable naming (`_self` captures `self` but then receives
the LangGraph state as a positional override). Replace with a clear, explicit form that
passes `state` explicitly:

```python
lambda state, _self=self, _transition=transition: _self.continue_iteration(state, _transition)
```

### 5. Propagate outer counters in `BaseNode._add_iteration_state` (`base_node.py`)

Extend the iteration-state write to include outer counter fields when present:

```python
final_state[OUTER_ITERATION_NODE_NUMBER_KEY] = state_schema.get(OUTER_ITERATION_NODE_NUMBER_KEY)
final_state[OUTER_TOTAL_ITERATIONS_KEY]      = state_schema.get(OUTER_TOTAL_ITERATIONS_KEY)
```

This ensures outer context survives the full inner iteration chain.

### 6. Update display in `AgentNode.get_node_name` (`agent_node.py`)

When `OUTER_ITERATION_NODE_NUMBER_KEY` is present, include the outer index in the
display string:

```
outer=1/3, inner=2/5 → "node [1-2/5]"   (outer branch 1, inner branch 2 of 5)
```

Exact format: `"{name} [{outer_n}-{inner_n}/{inner_total}]"` when outer counter is set,
`"{name} [{n}/{total}]"` when only a single level.

### 7. Tests

New unit tests in `tests/codemie/workflows/test_iteration_map_reduce.py`:

| ID | Scenario |
|---|---|
| TC_IMR_013 | Nested `iter_key`: inner branches receive independent counters 1..M |
| TC_IMR_014 | Outer counter preserved as `OUTER_ITERATION_NODE_NUMBER_KEY` in each inner `Send` |
| TC_IMR_015 | `OUTER_TOTAL_ITERATIONS_KEY` preserved in each inner `Send` |
| TC_IMR_016 | Empty inner collection (`items_to_process == []`) — empty `Send` list returned |
| TC_IMR_017 | `_add_iteration_state` propagates outer counter fields to node output |
| TC_IMR_018 | First-level (non-nested) calls still produce same output as before (regression guard) |

Tests for the lambda fix in `test_workflow_state_transitions.py`:
| TC_WST_014 | `_handle_single_state` for `iter_key` transition calls `continue_iteration(state, transition)` |

## Out of Scope

- Alembic migration for `WorkflowExecutionState.iteration_number` — the DB column remains a flat integer; nested iterations use the inner counter for `iteration_number` (same as before, functionally unchanged)
- YAML template for nested iteration — follow-up story
- `include_in_iterator_context` whitelist behaviour at inner depth — existing behaviour unchanged (whitelist is skipped for nested iterations; no change)
- Stack-based arbitrary-depth nesting — 2-level flat-pair approach is sufficient per acceptance criteria ("at least two levels")

## Acceptance Criteria Mapping

| AC | Covered by |
|---|---|
| 1. At least two levels | Core fix in `continue_iteration` |
| 2. Each level has its own `iter_key` | Existing config model supports this; no validator change needed |
| 3. Separate counters per level | `OUTER_*` constants + `index + 1` fix |
| 4. Message history preserved | Unchanged (`messages` reused by reference in nested mode) |
| 5. Graph edges correct | Lambda fix in `_handle_single_state` |
| 6. Comprehensive unit tests | TC_IMR_013–018, TC_WST_014 |
| 7. Documentation | This spec; inline docstring updates |
| 8. Performance | No new copies or allocations; outer_total/outer_number are scalar ints |
