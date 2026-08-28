# Sub-workflow Node Input/Output Propagation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix SubWorkflowNode so the child receives the preceding parent-node's output as its input (or the raw user input when the sub-workflow is the first node), and the child's last-node output flows back to the parent through the standard BaseNode context_store merge.

**Architecture:** Two independent bugs. `_render_input()` replaces a fragile `not context_store` heuristic with a `PREVIOUS_EXECUTION_STATE_ID is None` check — None means no real node ran before us (first after LangGraph START), which is a reliable, version-stable signal. `execute()` replaces the always-null `finished_child.output` with a targeted DB query for the child's last completed `WorkflowExecutionState.output`. Both fixes delegate DB access to two new `WorkflowService` classmethods so the node stays thin.

**Tech Stack:** Python 3.12, pytest 8.3, pytest-mock 3.14, LangGraph graph-state constants, Elasticsearch-backed `WorkflowExecutionState` model.

## Global Constraints

- Node code must not access DB models directly; use `WorkflowService` classmethods only.
- `input_mapping` branch in `_render_input` is unchanged (it has priority).
- Pool acquire/release symmetry in `execute()` must be preserved exactly.
- No new module-level imports in `sub_workflow_node.py` that touch `WorkflowExecutor` or the broader workflow layer — a circular-import guard is already in place via a local import inside `execute()`.
- `_EXECUTION_ID_KEYWORD = 'execution_id.keyword'` is defined as a private module constant in `workflow_service.py`; do not import it from `workflow_execution_service.py`.

---

### Task 1: Add WorkflowService state-lookup helpers

**Files:**
- Modify: `src/codemie/service/workflow_service.py`
- Test: `tests/codemie/service/test_workflow_service.py`

**Interfaces:**
- Produces:
  - `WorkflowService.find_execution_state_output(state_id: str) -> Optional[str]`
  - `WorkflowService.find_last_execution_state_output(execution_id: str) -> Optional[str]`

- [ ] **Step 1: Write the failing tests**

Add these two test classes to `tests/codemie/service/test_workflow_service.py` (after the existing imports/fixtures):

```python
from codemie.core.workflow_models import WorkflowExecutionStatusEnum


class TestFindExecutionStateOutput:
    def test_returns_output_for_valid_state_id(self):
        mock_state = MagicMock()
        mock_state.output = "previous node result"
        with patch(
            "codemie.service.workflow_service.WorkflowExecutionState"
        ) as mock_cls:
            mock_cls.get_by_id.return_value = mock_state
            result = WorkflowService.find_execution_state_output("state-abc")
        assert result == "previous node result"
        mock_cls.get_by_id.assert_called_once_with(id_="state-abc")

    def test_returns_none_when_state_not_found(self):
        with patch(
            "codemie.service.workflow_service.WorkflowExecutionState"
        ) as mock_cls:
            mock_cls.get_by_id.return_value = None
            result = WorkflowService.find_execution_state_output("missing-id")
        assert result is None

    def test_returns_none_on_exception(self):
        with patch(
            "codemie.service.workflow_service.WorkflowExecutionState"
        ) as mock_cls:
            mock_cls.get_by_id.side_effect = Exception("ES unavailable")
            result = WorkflowService.find_execution_state_output("err-id")
        assert result is None


class TestFindLastExecutionStateOutput:
    def test_returns_output_of_last_succeeded_state(self):
        succeeded = MagicMock()
        succeeded.status = WorkflowExecutionStatusEnum.SUCCEEDED
        succeeded.output = "child final answer"
        with patch(
            "codemie.service.workflow_service.WorkflowExecutionState"
        ) as mock_cls:
            mock_cls.get_all_by_fields.return_value = [succeeded]
            result = WorkflowService.find_last_execution_state_output("exec-123")
        assert result == "child final answer"
        mock_cls.get_all_by_fields.assert_called_once_with(
            fields={"execution_id.keyword": "exec-123"},
            order_by="update_date",
            order_desc=True,
        )

    def test_skips_non_succeeded_states(self):
        failed_state = MagicMock()
        failed_state.status = WorkflowExecutionStatusEnum.FAILED
        succeeded_state = MagicMock()
        succeeded_state.status = WorkflowExecutionStatusEnum.SUCCEEDED
        succeeded_state.output = "real output"
        with patch(
            "codemie.service.workflow_service.WorkflowExecutionState"
        ) as mock_cls:
            mock_cls.get_all_by_fields.return_value = [failed_state, succeeded_state]
            result = WorkflowService.find_last_execution_state_output("exec-xyz")
        assert result == "real output"

    def test_returns_none_when_no_succeeded_state(self):
        with patch(
            "codemie.service.workflow_service.WorkflowExecutionState"
        ) as mock_cls:
            mock_cls.get_all_by_fields.return_value = []
            result = WorkflowService.find_last_execution_state_output("exec-empty")
        assert result is None

    def test_returns_none_on_exception(self):
        with patch(
            "codemie.service.workflow_service.WorkflowExecutionState"
        ) as mock_cls:
            mock_cls.get_all_by_fields.side_effect = Exception("timeout")
            result = WorkflowService.find_last_execution_state_output("exec-err")
        assert result is None
```

Note: `WorkflowService` is already imported in this test file; add `WorkflowExecutionStatusEnum` to the existing `workflow_models` import at the top if not already present.

- [ ] **Step 2: Run tests and verify RED**

```bash
cd /home/user/projects/codemie/codemie
poetry run pytest tests/codemie/service/test_workflow_service.py::TestFindExecutionStateOutput tests/codemie/service/test_workflow_service.py::TestFindLastExecutionStateOutput -v
```

Expected: FAIL — `WorkflowService` has neither method yet.

- [ ] **Step 3: Implement the helpers in workflow_service.py**

1. Add `WorkflowExecutionState` to the existing import block (around line 32):

```python
from codemie.core.workflow_models import (
    WorkflowConfig,
    WorkflowConfigTemplate,
    WorkflowErrorFormat,
    WorkflowExecution,
    WorkflowExecutionResponse,
    WorkflowExecutionState,          # ← add this line
    WorkflowExecutionStatusEnum,
    YamlConfigHistory,
)
```

2. Add a module-level constant immediately after `MAX_ITEMS_PER_PAGE = 10_000`:

```python
_EXECUTION_ID_KEYWORD = "execution_id.keyword"
```

3. Check that `Optional` is imported at the top of the file (`from typing import Optional`); add it if absent.

4. Add two classmethods after `get_nesting_depth` (around line 342):

```python
@classmethod
def find_execution_state_output(cls, state_id: str) -> Optional[str]:
    try:
        state = WorkflowExecutionState.get_by_id(id_=state_id)
        return state.output if state else None
    except Exception as e:
        logger.error(f"Failed to fetch execution state output for state_id={state_id}: {e}")
        return None

@classmethod
def find_last_execution_state_output(cls, execution_id: str) -> Optional[str]:
    try:
        states = WorkflowExecutionState.get_all_by_fields(
            fields={_EXECUTION_ID_KEYWORD: execution_id},
            order_by="update_date",
            order_desc=True,
        )
        for state in states:
            if state.status == WorkflowExecutionStatusEnum.SUCCEEDED:
                return state.output
        return None
    except Exception as e:
        logger.error(f"Failed to fetch last execution state output for execution_id={execution_id}: {e}")
        return None
```

- [ ] **Step 4: Run tests and verify GREEN**

```bash
poetry run pytest tests/codemie/service/test_workflow_service.py::TestFindExecutionStateOutput tests/codemie/service/test_workflow_service.py::TestFindLastExecutionStateOutput -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Run full service test file to verify no regressions**

```bash
poetry run pytest tests/codemie/service/test_workflow_service.py -v
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/workflow_service.py tests/codemie/service/test_workflow_service.py
git commit -m "feat(EPMCDME-11609): add WorkflowService helpers for execution state output lookup"
```

---

### Task 2: Fix `_render_input` — use previous node's output or user_input

**Files:**
- Modify: `src/codemie/workflows/nodes/sub_workflow_node.py`
- Modify: `tests/codemie/workflows/nodes/test_sub_workflow_node.py`

**Interfaces:**
- Consumes: `WorkflowService.find_execution_state_output(state_id: str)` from Task 1
- Consumes: `PREVIOUS_EXECUTION_STATE_ID` constant from `codemie.workflows.constants`

- [ ] **Step 1: Write / rewrite the affected tests**

**Rewrite T4** — replace `test_execute_passes_full_context_when_no_mapping` with a test for the non-START path.
The old behavior ("dump full context_store as JSON when no input_mapping") is removed — update the function name and assertions:

```python
# ── T4: no input_mapping + non-START previous node → previous node output ─────


def test_execute_passes_previous_node_output_when_no_mapping(node, state_schema):
    """When PREVIOUS_EXECUTION_STATE_ID is set (a real node preceded this one),
    child input is that state's output — not the full context_store JSON."""
    from codemie.workflows.constants import PREVIOUS_EXECUTION_STATE_ID

    state_schema[PREVIOUS_EXECUTION_STATE_ID] = "prev-state-uuid"
    state_schema[CONTEXT_STORE_VARIABLE] = {"irrelevant": "context_store_is_not_used_here"}

    child_exec = MagicMock(execution_id="child-id")
    child_config = MagicMock(max_nesting_level=None)
    finished_child = MagicMock(overall_status=WorkflowExecutionStatusEnum.SUCCEEDED)
    parent_exec = MagicMock()
    parent_exec_initial = MagicMock()
    parent_exec_initial.active_sub_execution_id = None

    captured_input = {}

    def capture_create(*args, **kwargs):
        captured_input["user_input"] = args[2] if len(args) > 2 else kwargs.get("user_input")
        return child_exec

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.config") as mock_cfg,
        patch("codemie.workflows.nodes.sub_workflow_node.WorkflowService") as mock_svc,
        patch("codemie.workflows.workflow.WorkflowExecutor") as mock_executor,
    ):
        mock_cfg.ENABLE_SUB_WORKFLOW_NODE = True
        mock_cfg.WORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc.return_value.get_workflow.return_value = child_config
        mock_svc.get_nesting_depth.return_value = 0
        mock_svc.create_workflow_execution.side_effect = capture_create
        mock_svc.find_execution_state_output.return_value = "output from previous node"
        mock_svc.find_last_execution_state_output.return_value = ""
        mock_svc.find_workflow_execution_by_id.side_effect = [
            parent_exec_initial, finished_child, parent_exec
        ]
        mock_executor.create_executor.return_value = MagicMock()

        node.execute(state_schema, {})

    assert captured_input["user_input"] == "output from previous node"
    mock_svc.find_execution_state_output.assert_called_once_with("prev-state-uuid")
```

**Rewrite T4b** — replace `test_execute_passes_user_input_when_context_store_empty` with a START-detection test.
The key change: the condition is now `PREVIOUS_EXECUTION_STATE_ID absent/None`, NOT `context_store empty`.
Use a NON-EMPTY context_store to prove this:

```python
# ── T4b: no input_mapping + first node after START → USER_INPUT ───────────────


def test_execute_passes_user_input_when_first_after_start(node, state_schema):
    """When PREVIOUS_EXECUTION_STATE_ID is absent (sub-workflow is the first
    node after START), child input is USER_INPUT — even when context_store
    is non-empty (proving the condition is not the empty-dict heuristic)."""
    # Do NOT set PREVIOUS_EXECUTION_STATE_ID — its absence signals first-after-START.
    state_schema[CONTEXT_STORE_VARIABLE] = {"some": "accumulated context"}
    state_schema[USER_INPUT] = "original user message"

    child_exec = MagicMock(execution_id="child-id")
    child_config = MagicMock(max_nesting_level=None)
    finished_child = MagicMock(overall_status=WorkflowExecutionStatusEnum.SUCCEEDED)
    parent_exec = MagicMock()
    parent_exec_initial = MagicMock()
    parent_exec_initial.active_sub_execution_id = None

    captured_input = {}

    def capture_create(*args, **kwargs):
        captured_input["user_input"] = args[2] if len(args) > 2 else kwargs.get("user_input")
        return child_exec

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.config") as mock_cfg,
        patch("codemie.workflows.nodes.sub_workflow_node.WorkflowService") as mock_svc,
        patch("codemie.workflows.workflow.WorkflowExecutor") as mock_executor,
    ):
        mock_cfg.ENABLE_SUB_WORKFLOW_NODE = True
        mock_cfg.WORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc.return_value.get_workflow.return_value = child_config
        mock_svc.get_nesting_depth.return_value = 0
        mock_svc.create_workflow_execution.side_effect = capture_create
        mock_svc.find_last_execution_state_output.return_value = ""
        mock_svc.find_workflow_execution_by_id.side_effect = [
            parent_exec_initial, finished_child, parent_exec
        ]
        mock_executor.create_executor.return_value = MagicMock()

        node.execute(state_schema, {})

    assert captured_input["user_input"] == "original user message"
    mock_svc.find_execution_state_output.assert_not_called()
```

- [ ] **Step 2: Run tests and verify RED**

```bash
poetry run pytest \
  "tests/codemie/workflows/nodes/test_sub_workflow_node.py::test_execute_passes_previous_node_output_when_no_mapping" \
  "tests/codemie/workflows/nodes/test_sub_workflow_node.py::test_execute_passes_user_input_when_first_after_start" \
  -v
```

Expected: both FAIL (the methods don't exist yet / old logic still in place).

- [ ] **Step 3: Fix `_render_input` in sub_workflow_node.py**

1. Update the constants import at line 32:

```python
from codemie.workflows.constants import CONTEXT_STORE_VARIABLE, PREVIOUS_EXECUTION_STATE_ID, USER_INPUT
```

2. Replace the entire `_render_input` method (lines 158–169):

```python
def _render_input(self, state_schema) -> str:
    context_store: dict = state_schema.get(CONTEXT_STORE_VARIABLE, {})
    if self.input_mapping:
        rendered = {
            key: jinja2.Template(template).render(**context_store)
            for key, template in self.input_mapping.items()
        }
        return json.dumps(rendered)

    prev_state_id = state_schema.get(PREVIOUS_EXECUTION_STATE_ID)
    if not prev_state_id:
        # First node after START — no preceding DB execution state exists.
        return state_schema.get(USER_INPUT) or ""

    # Preceding real node exists — pass its output as the child's input.
    prev_output = WorkflowService.find_execution_state_output(prev_state_id)
    return prev_output or state_schema.get(USER_INPUT) or ""
```

- [ ] **Step 4: Run new tests and verify GREEN**

```bash
poetry run pytest \
  "tests/codemie/workflows/nodes/test_sub_workflow_node.py::test_execute_passes_previous_node_output_when_no_mapping" \
  "tests/codemie/workflows/nodes/test_sub_workflow_node.py::test_execute_passes_user_input_when_first_after_start" \
  -v
```

Expected: both PASS.

- [ ] **Step 5: Run full node test suite**

```bash
poetry run pytest tests/codemie/workflows/nodes/test_sub_workflow_node.py -v
```

Check: the old `test_execute_passes_full_context_when_no_mapping` and `test_execute_passes_user_input_when_context_store_empty` are gone (replaced). All remaining tests pass. If any test that calls `execute()` now fails because `find_execution_state_output` or `find_last_execution_state_output` is not mocked, add the missing mock (e.g., `mock_svc.find_execution_state_output.return_value = None` to the failing test's `with` block).

- [ ] **Step 6: Commit**

```bash
git add src/codemie/workflows/nodes/sub_workflow_node.py \
        tests/codemie/workflows/nodes/test_sub_workflow_node.py
git commit -m "fix(EPMCDME-11609): use previous node output as child input in _render_input"
```

---

### Task 3: Fix `execute` — return child's actual last-node output

**Files:**
- Modify: `src/codemie/workflows/nodes/sub_workflow_node.py`
- Modify: `tests/codemie/workflows/nodes/test_sub_workflow_node.py`

**Interfaces:**
- Consumes: `WorkflowService.find_last_execution_state_output(execution_id: str)` from Task 1

- [ ] **Step 1: Write the failing test**

Add this test after T5:

```python
# ── T5b: execute returns last child execution state output ────────────────────


def test_execute_returns_last_child_state_output(node, state_schema):
    """execute() must return WorkflowService.find_last_execution_state_output,
    NOT the always-None finished_child.output (success path never writes it)."""
    child_exec = MagicMock(execution_id="child-id")
    child_config = MagicMock(max_nesting_level=None)
    finished_child = MagicMock(
        overall_status=WorkflowExecutionStatusEnum.SUCCEEDED,
        output=None,  # intentionally None — the bug being fixed
    )
    parent_exec_initial = MagicMock()
    parent_exec_initial.active_sub_execution_id = None
    parent_exec_final = MagicMock()

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.config") as mock_cfg,
        patch("codemie.workflows.nodes.sub_workflow_node.WorkflowService") as mock_svc,
        patch("codemie.workflows.workflow.WorkflowExecutor") as mock_executor,
    ):
        mock_cfg.ENABLE_SUB_WORKFLOW_NODE = True
        mock_cfg.WORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc.return_value.get_workflow.return_value = child_config
        mock_svc.get_nesting_depth.return_value = 0
        mock_svc.create_workflow_execution.return_value = child_exec
        mock_svc.find_workflow_execution_by_id.side_effect = [
            parent_exec_initial, finished_child, parent_exec_final
        ]
        mock_svc.find_last_execution_state_output.return_value = "real child answer"
        mock_executor.create_executor.return_value = MagicMock()

        result = node.execute(state_schema, {})

    assert result == "real child answer"
    mock_svc.find_last_execution_state_output.assert_called_once_with("child-id")
```

- [ ] **Step 2: Run the new test and verify RED**

```bash
poetry run pytest \
  "tests/codemie/workflows/nodes/test_sub_workflow_node.py::test_execute_returns_last_child_state_output" \
  -v
```

Expected: FAIL — `execute()` still returns `finished_child.output or ""` which is `""`.

- [ ] **Step 3: Fix `execute()` return value in sub_workflow_node.py**

Replace the final return line (currently `return finished_child.output or ""`):

```python
# BEFORE (line ~153):
return finished_child.output or ""

# AFTER:
return WorkflowService.find_last_execution_state_output(child_execution.execution_id) or ""
```

No other changes to `execute()` — the `child_execution` variable is set in both the create and resume branches before this line, so it is always defined.

- [ ] **Step 4: Run the new test and verify GREEN**

```bash
poetry run pytest \
  "tests/codemie/workflows/nodes/test_sub_workflow_node.py::test_execute_returns_last_child_state_output" \
  -v
```

Expected: PASS.

- [ ] **Step 5: Update tests that assert `result == "..."` using the old `finished_child.output`**

The following existing tests pass `output="something"` on `finished_child` and assert the return value — they now need `mock_svc.find_last_execution_state_output.return_value` set instead:

**T5** (`test_execute_happy_path_clears_active_sub_execution_id`):
- Change `finished_child = MagicMock(..., output="child output")` → `output=None`
- Add `mock_svc.find_last_execution_state_output.return_value = "child output"` in the `with` block
- `assert result == "child output"` stays unchanged

**T9 resume tests** (`test_execute_resume_path_resumes_child_with_forwarded_input`, `test_execute_resume_path_clears_active_id_on_child_success`):
- Same pattern: set `output=None` on `finished_child`, add `mock_svc.find_last_execution_state_output.return_value = "resumed output"` to the `with` block.

**Pool integration tests** (`test_create_path_acquires_and_releases_pool_graph`, `test_create_path_releases_pool_graph_on_failure`):
- These call through to `execute()` — add `mock_svc.find_last_execution_state_output.return_value = "pool output"` to their mock setups. The `test_create_path_releases_pool_graph_on_failure` test expects an exception — verify it still raises correctly with the mock in place.

- [ ] **Step 6: Run full combined test suite**

```bash
poetry run pytest \
  tests/codemie/workflows/nodes/test_sub_workflow_node.py \
  tests/codemie/service/test_workflow_service.py \
  -v
```

Expected: all tests pass. Final count should be the original 16 tests minus 2 replaced (T4, T4b) plus 3 added (T4 rewrite, T4b rewrite, T5b) = 17 tests, plus the 7 new service helper tests.

- [ ] **Step 7: Commit**

```bash
git add src/codemie/workflows/nodes/sub_workflow_node.py \
        tests/codemie/workflows/nodes/test_sub_workflow_node.py
git commit -m "fix(EPMCDME-11609): return child last execution state output from SubWorkflowNode.execute"
```

---

## Self-Review

### Spec coverage

| Requirement | Task |
|---|---|
| Input: first-after-START (`PREVIOUS_EXECUTION_STATE_ID` absent) → `USER_INPUT` | Task 2 |
| Input: non-START previous node → `WorkflowExecutionState.output` of that state | Task 2 |
| `input_mapping` retains priority over both input paths | Task 2 (unchanged branch) |
| Output: child's last-node output returned to parent (not always-null `finished_child.output`) | Task 3 |
| DB access stays in service layer (not in node) | Task 1 |

### Placeholder scan

All steps contain exact code. No TBD, no "add appropriate error handling", no "similar to Task N".

### Type consistency

- `find_execution_state_output(state_id: str) -> Optional[str]` — defined in T1, called in T2 impl as `WorkflowService.find_execution_state_output(prev_state_id)`, asserted in T2 test as `find_execution_state_output.assert_called_once_with("prev-state-uuid")` ✓
- `find_last_execution_state_output(execution_id: str) -> Optional[str]` — defined in T1, called in T3 impl as `WorkflowService.find_last_execution_state_output(child_execution.execution_id)`, asserted in T3 test as `find_last_execution_state_output.assert_called_once_with("child-id")` ✓
- `PREVIOUS_EXECUTION_STATE_ID` — imported from `codemie.workflows.constants` in both implementation and tests ✓
