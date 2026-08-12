# Plan: Nested Iterations Support (EPMCDME-4289)

## Task 1 — Add outer-counter constants

**Files**: `src/codemie/workflows/constants.py`

Add two new module-level constants after the existing `TOTAL_ITERATIONS_KEY`:

```python
OUTER_ITERATION_NODE_NUMBER_KEY = "outer_iteration_node_number"
OUTER_TOTAL_ITERATIONS_KEY      = "outer_total_iterations"
```

Test-first: no — pure constant additions, no behaviour change.

---

## Task 2 — Add typed iteration fields to `AgentMessages`

**Files**: `src/codemie/workflows/models.py`

Extend `AgentMessages` TypedDict with four typed fields (all use `right-wins` reducer):

```python
iteration_node_number:       Annotated[Optional[int], lambda left, right: right if right is not None else left]
total_iterations:            Annotated[Optional[int], lambda left, right: right if right is not None else left]
outer_iteration_node_number: Annotated[Optional[int], lambda left, right: right if right is not None else left]
outer_total_iterations:      Annotated[Optional[int], lambda left, right: right if right is not None else left]
```

Test-first: no — TypedDict extension; existing tests still pass without change.

---

## Task 3 — Fix `continue_iteration` to emit independent inner counters

**Files**: `src/codemie/workflows/workflow.py`

Replace the `ITERATION_NODE_NUMBER_KEY` line in the `Send` list comprehension and add
outer-counter preservation:

Before:
```python
ITERATION_NODE_NUMBER_KEY: iter_number if iter_number else index + 1,
```

After — capture outer before building actions, then always use `index + 1`:
```python
outer_iter_number = iter_number if is_in_iteration else None
outer_total       = state_schema.get(TOTAL_ITERATIONS_KEY) if is_in_iteration else None

# in Send:
ITERATION_NODE_NUMBER_KEY:       index + 1,
TOTAL_ITERATIONS_KEY:            total_iterations,
OUTER_ITERATION_NODE_NUMBER_KEY: outer_iter_number,
OUTER_TOTAL_ITERATIONS_KEY:      outer_total,
```

Also import the two new constants at the top of the file.

Test-first: yes — TC_IMR_013: call `continue_iteration` with `state_schema` that has
`ITERATION_NODE_NUMBER_KEY=2` (simulating outer branch 2 of 3) and `ITER_SOURCE` returning
a 3-item list; assert each inner `Send.args[ITERATION_NODE_NUMBER_KEY]` equals 1, 2, 3.

---

## Task 4 — Fix `_handle_single_state` lambda

**Files**: `src/codemie/workflows/workflow.py`

Replace the iter_key lambda with an explicit, unambiguous form:

Before:
```python
lambda _self=self, _transition=transition: self.continue_iteration(_self, _transition),
```

After:
```python
lambda state, _self=self, _transition=transition: _self.continue_iteration(state, _transition),
```

Test-first: yes — TC_WST_014: build a `_handle_single_state` call with a mock `StateGraph`;
capture the registered callable; call it with a synthetic state dict; assert
`continue_iteration` receives that dict as `state_schema` (not the executor instance).

---

## Task 5 — Propagate outer counters in `BaseNode._add_iteration_state`

**Files**: `src/codemie/workflows/nodes/base_node.py`

After the existing `TOTAL_ITERATIONS_KEY` write, add:

```python
final_state[OUTER_ITERATION_NODE_NUMBER_KEY] = state_schema.get(OUTER_ITERATION_NODE_NUMBER_KEY)
final_state[OUTER_TOTAL_ITERATIONS_KEY]      = state_schema.get(OUTER_TOTAL_ITERATIONS_KEY)
```

Import the two new constants at the top of the file.

Test-first: yes — TC_IMR_017: construct a `MockNode` with `workflow_state.next.iter_key` set;
call `_add_iteration_state` with a state_schema containing `OUTER_ITERATION_NODE_NUMBER_KEY=1`
and `OUTER_TOTAL_ITERATIONS_KEY=3`; assert both keys are present in `final_state`.

---

## Task 6 — Update `AgentNode.get_node_name` for nested display

**Files**: `src/codemie/workflows/nodes/agent_node.py`

When `OUTER_ITERATION_NODE_NUMBER_KEY` is present in state_schema, include outer context:

```
"{name} [{outer_n}-{inner_n}/{inner_total}]"   # nested
"{name} [{n}/{total}]"                          # single-level (unchanged)
```

Test-first: yes — add to `test_agent_node.py`: call `get_node_name` with a state_schema
containing `OUTER_ITERATION_NODE_NUMBER_KEY=1`, `ITERATION_NODE_NUMBER_KEY=2`,
`TOTAL_ITERATIONS_KEY=5`; assert result contains "1-2/5".

---

## Task 7 — Add comprehensive tests (TC_IMR_013–018)

**Files**: `tests/codemie/workflows/test_iteration_map_reduce.py`

| Test ID | Description |
|---|---|
| TC_IMR_013 | Inner branches receive independent counters 1..M (see Task 3) |
| TC_IMR_014 | `OUTER_ITERATION_NODE_NUMBER_KEY` equals outer `iter_number` in every inner `Send` |
| TC_IMR_015 | `OUTER_TOTAL_ITERATIONS_KEY` equals the outer `TOTAL_ITERATIONS_KEY` from schema |
| TC_IMR_016 | Empty inner list (`items_to_process == []`) returns empty `Send` list without error |
| TC_IMR_017 | `_add_iteration_state` propagates outer counter fields (see Task 5) |
| TC_IMR_018 | Single-level (non-nested) call: output unchanged vs. pre-change baseline |

Test-first: yes — write all tests as `FAIL` (exercising code paths not yet fixed in Tasks 3–5)
before implementing those tasks.

---

## Task 8 — Run full test suite and verify

**Command**:
```bash
make ruff && python -m pytest tests/codemie/workflows/test_iteration_map_reduce.py tests/codemie/workflows/test_workflow_state_transitions.py tests/codemie/workflows/test_base_node_lifecycle.py -v
```

All TC_IMR_* and TC_WST_014 must pass. No existing tests may regress.

Test-first: no — validation task.
