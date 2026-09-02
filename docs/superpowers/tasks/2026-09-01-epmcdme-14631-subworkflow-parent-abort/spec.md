# EPMCDME-14631 — Sub-workflow abort cascade

## Problem

When a parent workflow execution is aborted, any in-progress sub-workflow child execution is left running. The backend `WorkflowExecutionService.abort()` marks the parent execution and its states as `Aborted` but never touches the child execution stored in `parent_execution.active_sub_execution_id`.

## Chosen approach

Fix the cascade at the backend service layer. `active_sub_execution_id` is already stored on `WorkflowExecution`. After aborting the parent, `abort()` reads this field, looks up the child execution, and recursively aborts it. No frontend changes are needed — the parent's existing state polling will reflect the correct state after the backend fix lands.

## What changes

### `codemie/src/codemie/service/workflow_execution/workflow_execution_service.py`

Extend `abort()` to cascade to the active sub-execution after the parent is marked aborted:

```
def abort(self):
    # existing logic — marks parent ABORTED, cleans up inflight states
    ...
    # NEW: cascade to active sub-workflow execution if one is running
    self._abort_active_sub_execution()
```

Add a new private method `_abort_active_sub_execution()`:

- Reads `self.workflow_execution.active_sub_execution_id` (may be `None`).
- If `None` or the workflow execution itself was not found: no-op.
- Looks up the child `WorkflowExecution` via `WorkflowService.find_workflow_execution_by_id()`. Uses a local (deferred) import to avoid the known circular import between `workflow_service` and `workflow_execution_service`.
- If the child is not found or its `overall_status` is already terminal (`Succeeded`, `Failed`, `Aborted`) — i.e. not one of `In Progress`, `Not Started`, `Interrupted`, `AUTHENTICATION_REQUIRED` — : no-op.
- Constructs a new `WorkflowExecutionService` with the child's `workflow_id` config and calls `.abort()`. This recurses naturally for grandchildren.
- Wraps the cascade in a `try/except`, logs errors, and never lets a child-abort failure propagate up to the caller — parent abort must always succeed.

### No other files change

`WorkflowExecutionResponse`, the REST router, and the frontend are unaffected.

## Error handling

Child abort errors are caught, logged at `ERROR` level, and swallowed. The parent execution is already aborted before the cascade attempt, so the parent abort result is unaffected.

## Recursion / depth

`WorkflowExecutionService.abort()` already works per-execution-id. Because the method calls itself via a fresh service instance on the child, the cascade handles arbitrarily nested sub-workflows without additional depth tracking.

## Testing

New test class `TestAbortSubWorkflowCascade` in `tests/codemie/service/test_workflow_execution_service.py` (create file if it does not exist, otherwise add the class):

| Test | What it verifies |
|---|---|
| `test_abort_cascades_to_active_sub_execution` | Parent has `active_sub_execution_id`; child is `In Progress`; child's `abort()` is called |
| `test_abort_no_cascade_when_no_sub_execution` | `active_sub_execution_id` is `None`; child service never constructed |
| `test_abort_no_cascade_when_child_already_terminal` | Child is `Succeeded`; child `abort()` not called |
| `test_abort_child_error_does_not_fail_parent` | Child `abort()` raises; parent abort still completes cleanly |

Test pattern: mock `WorkflowExecution` and `WorkflowConfig` as in `tests/codemie/workflows/nodes/test_sub_workflow_node.py`. Use `unittest.mock.patch` for `WorkflowService.find_workflow_execution_by_id` and `WorkflowService.get_workflow`.

## Out of scope

- Exposing `active_sub_execution_id` in `WorkflowExecutionResponse` — not needed for this fix.
- Frontend changes — none required.
- Abort cascade for the `fail()` path — separate concern; not part of this ticket.
