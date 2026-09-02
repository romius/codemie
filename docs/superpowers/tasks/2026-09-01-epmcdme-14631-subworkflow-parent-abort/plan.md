# EPMCDME-14631 — Sub-workflow abort cascade: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cascade abort to any in-progress sub-workflow child execution when a parent workflow execution is aborted.

**Architecture:** Add a private `_abort_active_sub_execution()` method to `WorkflowExecutionService` that reads `self.workflow_execution.active_sub_execution_id`, looks up the child execution, and calls `abort()` on a fresh service instance for the child. `abort()` calls this at the end after its own cleanup. Recursion for grandchildren is free — each child's `abort()` does the same.

**Tech Stack:** Python, SQLModel, `unittest.mock`, pytest

---

## File map

| Action | Path |
|---|---|
| Modify | `src/codemie/service/workflow_execution/workflow_execution_service.py` |
| Modify (add class) | `tests/codemie/service/workflow_execution/test_workflow_execution_service.py` |

---

### Task 1: Write failing tests for `_abort_active_sub_execution`

**Files:**
- Modify: `tests/codemie/service/workflow_execution/test_workflow_execution_service.py`

- [ ] **Step 1: Add `TestAbortSubWorkflowCascade` class at the bottom of the test file**

The existing `service` fixture (lines 46–57) is reused. Tests call `_abort_active_sub_execution()` directly so they stay focused on cascade logic alone.

Two patch paths to know:
- `WorkflowService` lives in `codemie.service.workflow_service`; it is deferred-imported inside `_abort_active_sub_execution`, so patch it on its home module.
- `WorkflowExecutionService` is in the same module as `_abort_active_sub_execution`; patching it there intercepts the child-service constructor call without touching the already-instantiated `service` fixture.

Append after the last line of the file:

```python
class TestAbortSubWorkflowCascade:
    """_abort_active_sub_execution cascades abort to the active child execution."""

    def test_cascades_to_in_progress_child(self, service):
        service.workflow_execution.active_sub_execution_id = "child-exec-001"

        child_execution = MagicMock()
        child_execution.overall_status = WorkflowExecutionStatusEnum.IN_PROGRESS
        child_execution.workflow_id = "child-wf-id"

        child_svc = MagicMock()

        with (
            patch("codemie.service.workflow_service.WorkflowService") as mock_ws,
            patch(
                "codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionService",
                return_value=child_svc,
            ),
        ):
            mock_ws.find_workflow_execution_by_id.return_value = child_execution
            mock_ws.return_value.get_workflow.return_value = MagicMock()
            service._abort_active_sub_execution()

        child_svc.abort.assert_called_once()

    def test_no_cascade_when_no_active_sub_execution(self, service):
        service.workflow_execution.active_sub_execution_id = None

        with patch("codemie.service.workflow_service.WorkflowService") as mock_ws:
            service._abort_active_sub_execution()

        mock_ws.find_workflow_execution_by_id.assert_not_called()

    def test_no_cascade_when_child_already_aborted(self, service):
        service.workflow_execution.active_sub_execution_id = "child-exec-002"

        child_execution = MagicMock()
        child_execution.overall_status = WorkflowExecutionStatusEnum.ABORTED

        with (
            patch("codemie.service.workflow_service.WorkflowService") as mock_ws,
            patch(
                "codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionService",
            ) as mock_svc_cls,
        ):
            mock_ws.find_workflow_execution_by_id.return_value = child_execution
            service._abort_active_sub_execution()

        mock_svc_cls.assert_not_called()

    def test_no_cascade_when_child_succeeded(self, service):
        service.workflow_execution.active_sub_execution_id = "child-exec-003"

        child_execution = MagicMock()
        child_execution.overall_status = WorkflowExecutionStatusEnum.SUCCEEDED

        with (
            patch("codemie.service.workflow_service.WorkflowService") as mock_ws,
            patch(
                "codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionService",
            ) as mock_svc_cls,
        ):
            mock_ws.find_workflow_execution_by_id.return_value = child_execution
            service._abort_active_sub_execution()

        mock_svc_cls.assert_not_called()

    def test_child_abort_error_does_not_propagate(self, service):
        service.workflow_execution.active_sub_execution_id = "child-exec-004"

        child_execution = MagicMock()
        child_execution.overall_status = WorkflowExecutionStatusEnum.IN_PROGRESS
        child_execution.workflow_id = "child-wf-id"

        with (
            patch("codemie.service.workflow_service.WorkflowService") as mock_ws,
            patch(
                "codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionService",
                side_effect=RuntimeError("db down"),
            ),
        ):
            mock_ws.find_workflow_execution_by_id.return_value = child_execution
            mock_ws.return_value.get_workflow.return_value = MagicMock()
            service._abort_active_sub_execution()  # must not raise
```

- [ ] **Step 2: Run the tests to confirm they all fail (method not yet implemented)**

Run from the `codemie/` repo root:
```bash
pytest tests/codemie/service/workflow_execution/test_workflow_execution_service.py::TestAbortSubWorkflowCascade -v
```

Expected: all 5 tests fail with `AttributeError: ... has no attribute '_abort_active_sub_execution'` or similar (cascade is not called yet).

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/codemie/service/workflow_execution/test_workflow_execution_service.py
git commit -m "EPMCDME-14631: Add failing tests for sub-workflow abort cascade"
```

---

### Task 2: Implement `_abort_active_sub_execution` and wire it into `abort()`

**Files:**
- Modify: `src/codemie/service/workflow_execution/workflow_execution_service.py`

- [ ] **Step 1: Add `_abort_active_sub_execution` after the `abort` method (around line 151)**

Insert the following method immediately after the closing line of `abort()` (after `request_summary_manager.clear_summary(self.workflow_execution_id)`):

```python
def _abort_active_sub_execution(self) -> None:
    """Cascade abort to the active sub-workflow child execution, if one is running.

    Uses a deferred import of WorkflowService to avoid a circular dependency
    between workflow_service and workflow_execution_service.
    """
    if not self.workflow_execution:
        return
    child_execution_id = self.workflow_execution.active_sub_execution_id
    if not child_execution_id:
        return
    try:
        from codemie.service.workflow_service import WorkflowService  # deferred: avoids circular import
        child_execution = WorkflowService.find_workflow_execution_by_id(child_execution_id)
        if not child_execution:
            logger.warning(
                f"Sub-workflow execution {child_execution_id} not found during parent abort "
                f"(parent: {self.workflow_execution_id}); skipping cascade."
            )
            return
        _TERMINAL_STATUSES = {
            WorkflowExecutionStatusEnum.SUCCEEDED,
            WorkflowExecutionStatusEnum.FAILED,
            WorkflowExecutionStatusEnum.ABORTED,
        }
        if child_execution.overall_status in _TERMINAL_STATUSES:
            return
        child_workflow_config = WorkflowService().get_workflow(child_execution.workflow_id)
        WorkflowExecutionService(
            workflow_config=child_workflow_config,
            workflow_execution_id=child_execution_id,
            user=self.user,
        ).abort()
        logger.warning(
            f"Cascade-aborted sub-workflow execution {child_execution_id} "
            f"(parent: {self.workflow_execution_id})."
        )
    except Exception as exc:
        logger.error(
            f"Failed to cascade-abort sub-workflow execution {child_execution_id} "
            f"(parent: {self.workflow_execution_id}): {exc}"
        )
```

- [ ] **Step 2: Add the cascade call at the end of `abort()`**

The current last line of `abort()` is:
```python
        request_summary_manager.clear_summary(self.workflow_execution_id)
```

Add one line immediately after it:
```python
        self._abort_active_sub_execution()
```

The full updated `abort()` tail should look like:

```python
        WorkflowMonitoringService.send_workflow_execution_metric(
            workflow_config=self.workflow_config,
            workflow_execution_config=self.workflow_execution,
            user=self.user,
            request_id=self.workflow_execution_id,
        )
        request_summary_manager.clear_summary(self.workflow_execution_id)
        self._abort_active_sub_execution()
```

- [ ] **Step 3: Run the new tests to confirm they pass**

```bash
pytest tests/codemie/service/workflow_execution/test_workflow_execution_service.py::TestAbortSubWorkflowCascade -v
```

Expected: all 5 tests PASS.

- [ ] **Step 4: Run the full existing `TestAbort` class to confirm no regressions**

```bash
pytest tests/codemie/service/workflow_execution/test_workflow_execution_service.py::TestAbort -v
```

Expected: all 3 existing tests PASS.

- [ ] **Step 5: Run the full test file**

```bash
pytest tests/codemie/service/workflow_execution/test_workflow_execution_service.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit the implementation**

```bash
git add src/codemie/service/workflow_execution/workflow_execution_service.py
git commit -m "EPMCDME-14631: Cascade abort to active sub-workflow child execution"
```

---

### Task 3: Commit planning artifacts

- [ ] **Step 1: Commit task directory**

```bash
git add docs/superpowers/tasks/2026-09-01-epmcdme-14631-subworkflow-parent-abort/
git commit -m "EPMCDME-14631: Add planning artifacts (spec, plan, technical analysis)"
```
