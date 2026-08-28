# Fix Sub-Workflow Interrupt/Resume Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `test_tc_wst_010_sub_workflow_interrupt_and_resume` which fails with `TypeError: object supporting the buffer API required` due to two bugs: a missing `workflow_pool` mock in the test and missing production code that sets `active_sub_execution_id` before streaming.

**Architecture:** The fix splits across two layers — production code adds the missing parent-exec bookkeeping in the create path of `SubWorkflowNode.execute()`, and the test adds the missing `workflow_pool` patch plus a return-value configuration for Phase 2's output assertion.

**Tech Stack:** Python, pytest, unittest.mock, LangGraph, SQLModel

## Global Constraints

- Run tests with `poetry run pytest <path> -v` (never bare `pytest`)
- Do not change other passing tests in the file
- Do not add error handling beyond what the test contract requires

---

### Task 1: Production fix — set active_sub_execution_id before streaming

**Files:**
- Modify: `src/codemie/workflows/nodes/sub_workflow_node.py:107-114`

**Interfaces:**
- Consumes: `parent_exec` (fetched at line 74), `child_execution` (created at line 107-112)
- Produces: `parent_exec.active_sub_execution_id` set to `child_execution.execution_id` before `workflow_pool.acquire()` is called

- [ ] **Step 1: Read the current create path**

Open `src/codemie/workflows/nodes/sub_workflow_node.py` lines 95–127. The create path starts at `else:` after the resume `if` block. Note that `child_execution` is assigned at ~line 107 and `workflow_pool.acquire()` is called at ~line 114. There is no code between them that sets `parent_exec.active_sub_execution_id`.

- [ ] **Step 2: Insert the bookkeeping lines**

After the `child_execution = WorkflowService.create_workflow_execution(...)` call and before `graph = workflow_pool.acquire(...)`, add:

```python
            if parent_exec:
                parent_exec.active_sub_execution_id = child_execution.execution_id
                parent_exec.save()
```

The indentation matches the surrounding `else:` block (12 spaces / 3 levels).

- [ ] **Step 3: Verify the surrounding code looks correct**

The create path block should now read:

```python
            child_execution = WorkflowService.create_workflow_execution(
                child_config,
                user.as_user_model(),
                child_input,
                parent_execution_id=self.execution_id,
            )

            if parent_exec:
                parent_exec.active_sub_execution_id = child_execution.execution_id
                parent_exec.save()

            graph = workflow_pool.acquire(self.sub_workflow_id, child_config)
            try:
                child_executor = WorkflowExecutor.create_executor(
                    ...
                )
                child_executor.stream()
            finally:
                workflow_pool.release(self.sub_workflow_id, child_config, graph)
```

- [ ] **Step 4: Commit**

```bash
git add src/codemie/workflows/nodes/sub_workflow_node.py
git commit -m "fix(EPMCDME-11609): set active_sub_execution_id on parent before streaming child"
```

---

### Task 2: Test fix — add workflow_pool mock and Phase 2 output mock

**Files:**
- Modify: `tests/codemie/workflows/test_workflow_state_transitions.py:555-607`

**Interfaces:**
- Consumes: Task 1's production fix (the test now exercises the new code path)
- Produces: passing `test_tc_wst_010_sub_workflow_interrupt_and_resume`

- [ ] **Step 1: Run the test before changes to confirm the current failure**

```bash
poetry run pytest tests/codemie/workflows/test_workflow_state_transitions.py::test_tc_wst_010_sub_workflow_interrupt_and_resume -v
```

Expected: `FAILED … TypeError: object supporting the buffer API required`

- [ ] **Step 2: Add `workflow_pool` mock to Phase 1 `with` block**

In the Phase 1 `with (...)` context manager (around line 556), add a fourth patch:

```python
    with (
        patch("codemie.workflows.nodes.sub_workflow_node.config") as mock_cfg,
        patch("codemie.workflows.nodes.sub_workflow_node.WorkflowService") as mock_svc,
        patch("codemie.workflows.workflow.WorkflowExecutor") as mock_wf_exec,
        patch("codemie.workflows.nodes.sub_workflow_node.workflow_pool") as mock_pool,
    ):
```

Then inside the `with` block (before calling `node.execute`), add:

```python
        mock_pool.acquire.return_value = MagicMock()
```

- [ ] **Step 3: Add `find_last_execution_state_output` mock to Phase 2 setup**

In the Phase 2 `with (...)` block (around line 583), add the output mock inside the block before `result = node.execute(state_schema, {})`:

```python
        mock_svc.find_last_execution_state_output.return_value = "done"
```

- [ ] **Step 4: Run the test to confirm GREEN**

```bash
poetry run pytest tests/codemie/workflows/test_workflow_state_transitions.py::test_tc_wst_010_sub_workflow_interrupt_and_resume -v
```

Expected: `PASSED`

- [ ] **Step 5: Run the full test module to check for regressions**

```bash
poetry run pytest tests/codemie/workflows/test_workflow_state_transitions.py -v
```

Expected: all tests PASSED (no regressions)

- [ ] **Step 6: Commit**

```bash
git add tests/codemie/workflows/test_workflow_state_transitions.py
git commit -m "fix(EPMCDME-11609): fix test_tc_wst_010 — add workflow_pool mock and Phase 2 output setup"
```
