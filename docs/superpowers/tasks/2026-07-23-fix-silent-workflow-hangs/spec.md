# Fix Silent Workflow Hangs — Spec

**Ticket:** EPMCDME-10973  
**Branch:** EPMCDME-10973_fix-silent-workflow-hangs  
**Scope:** Backend only — `codemie/` package

---

## Problem

Workflow steps remain stuck in `IN_PROGRESS` status indefinitely (30–60 minutes) when they normally complete in 2–3 minutes. No error is surfaced to the user. The root cause is a set of error-propagation gaps: exceptions get swallowed or the code paths that call `finish_state(FAILED)` are bypassed, so `WorkflowExecutionState` rows never transition out of `IN_PROGRESS`.

---

## Goals

- Every failed or interrupted workflow step must transition out of `IN_PROGRESS` into an appropriate terminal status (`FAILED`, `INTERRUPTED`, `ABORTED`).
- Orphaned `IN_PROGRESS` rows left by pod restarts or DB outages are cleaned up at application startup.
- No new abstractions, no schema changes, no API changes.

---

## Out of Scope

- Frontend changes (UI already renders `Failed` status correctly once the backend sets it).
- The `background_tasks.add_task(stream)` shutdown-time risk (the `stream()` method has its own finally-block that calls `fail()` or `finish()`; the remaining risk is pod kill at shutdown, which is handled by Fix 5).
- `ThoughtConsumer` DB save failures (these lose thought records but do not leave execution states stuck).

---

## Fix 1 — Remove swallowed exception in `SummarizeConversationCommandNode`

**File:** `src/codemie/workflows/nodes/summarize_conversation_node.py`  
**Lines:** 112–114

**Current behaviour:** `execute()` wraps the LLM call in `except Exception: return ""`. Any LLM error (timeout, connection error, `ValueError`) is caught, logged, and the method returns an empty string. `BaseNode.__call__` then calls `finish_state(SUCCEEDED, output="")` — the node appears successful, and any prior `IN_PROGRESS` states from the same execution are never cleaned up by the failure path.

**Fix:** Remove the try/except block entirely. Exceptions propagate naturally to `BaseNode.__call__`'s existing exception handler, which calls `finish_state(FAILED)` and re-raises. The step correctly transitions to `FAILED` and the failure is visible to the user.

---

## Fix 2 — `_interrupt_predecessor_state` must also update `IN_PROGRESS` states

**File:** `src/codemie/service/workflow_execution/workflow_execution_service.py`  
**Method:** `_interrupt_predecessor_state` (~line 486)

**Current behaviour:** The method filters `state.status == SUCCEEDED` only. A node that was mid-execution (`IN_PROGRESS`) when the interrupt fired is never updated — it stays `IN_PROGRESS` forever.

**Fix:** Extend the filter to `state.status in (SUCCEEDED, IN_PROGRESS)`. Use `INTERRUPTED` (not `FAILED`) for both — the step was stopped, not broken.

---

## Fix 3 — Guard `finish_state()` in `BaseNode.__call__` so `raise e` is unconditional

**File:** `src/codemie/workflows/nodes/base_node.py`  
**Lines:** 263–273

**Current behaviour:** In the `except Exception` handler, `finish_state()` is called before `raise e`. If `finish_state()` itself throws (e.g., transient DB error), the original exception is replaced by the DB error and `raise e` is never reached. The state record stays `IN_PROGRESS`.

**Fix:** Wrap `finish_state()` in its own try/except. If `finish_state()` throws, attempt a minimal direct state write as a fallback (set `status = FAILED`, `completed_at = now()`, save). If even the fallback fails (full DB outage), log and accept the orphan — Fix 5 will clean it up at next startup. The `raise e` at the end is unconditional in all cases.

```python
except Exception as e:
    self.handle_execution_failure(e)
    try:
        self.workflow_execution_service.finish_state(
            execution_state_id, output=str(e), status=WorkflowExecutionStatusEnum.FAILED
        )
    except Exception as finish_exc:
        logger.error(
            f"finish_state failed for state {execution_state_id}: {finish_exc}. "
            "Attempting direct state update."
        )
        try:
            state = WorkflowExecutionState.get_by_id(execution_state_id)
            if state and state.status == WorkflowExecutionStatusEnum.IN_PROGRESS:
                state.status = WorkflowExecutionStatusEnum.FAILED
                state.completed_at = datetime.now()
                state.save()
        except Exception as direct_exc:
            logger.error(
                f"Direct state update also failed for {execution_state_id}: {direct_exc}. "
                "State will be recovered at startup."
            )
    for callback in self.callbacks:
        callback.on_node_fail(exception=e, execution_state_id=execution_state_id)
    raise e
```

---

## Fix 4 — `fail()` must clean up in-flight states (mirrors `abort()`)

**File:** `src/codemie/service/workflow_execution/workflow_execution_service.py`  
**Method:** `fail()`

**Current behaviour:** `fail()` marks `WorkflowExecution.overall_status = FAILED` but never touches individual `WorkflowExecutionState` rows. After an unhandled exception, the overall execution shows `FAILED` in the UI but individual steps still show `IN_PROGRESS` spinners. `abort()` already iterates states and updates them — `fail()` should do the same.

**Fix:** After marking the overall execution as `FAILED`, iterate all `WorkflowExecutionState` rows for this execution and mark any with status `IN_PROGRESS` or `INTERRUPTED` as `FAILED` with `completed_at = now()`. This matches `abort()` exactly (lines 113–122), which transitions both `IN_PROGRESS` and `INTERRUPTED` step states. Wrap the state-cleanup loop in try/except so a DB failure here does not prevent the metric from being sent.

---

## Fix 5 — Startup recovery scan for orphaned `IN_PROGRESS` states

**Location:** `src/codemie/rest_api/main.py` — `lifespan` async context manager (line 631), before the `yield` statement. Follow the same pattern as `budget_startup_reconciliation_service` (`src/codemie/service/budget/startup_reconciliation_service.py`), which provides a direct structural template.

**Current behaviour:** If a pod is killed (OOM, restart), or the DB was unavailable when Fix 3's fallback ran, `WorkflowExecutionState` rows are left with `status = IN_PROGRESS` permanently. No in-process code can reach them after the process exits.

**Fix:** At application startup, run a one-time synchronous query:

1. Find all `WorkflowExecution` rows with a terminal `overall_status` (`FAILED`, `SUCCEEDED`, `ABORTED`, `INTERRUPTED`).
2. For each, find any associated `WorkflowExecutionState` rows with `status = IN_PROGRESS`.
3. Mark them `FAILED` with `completed_at = now()` and save.
4. Log the count of recovered states.

The scan is idempotent (safe to run on every restart), fast (terminal executions with in-progress states are the edge case), and non-blocking (completes before request handling begins, but should not block startup if it fails — wrap in try/except with a logged warning).

---

## Acceptance Criteria

- [ ] A workflow step that fails due to an LLM error in `SummarizeConversationCommandNode` transitions to `FAILED` and shows an error message in the UI.
- [ ] When a workflow is interrupted, all steps that were `IN_PROGRESS` at interrupt time transition to `INTERRUPTED`.
- [ ] When `finish_state()` throws inside `BaseNode.__call__`, the original node exception is still re-raised unconditionally (even when both `finish_state()` and the direct fallback write fail).
- [ ] When `finish_state()` throws and the direct fallback write also fails, the step is recovered to `FAILED` at next application startup by Fix 5.
- [ ] When an unhandled exception causes `workflow_execution_service.fail()` to be called, all in-flight step states transition to `FAILED` (same as `abort()` already does for `ABORTED`).
- [ ] At application startup, any `WorkflowExecutionState` rows with `status = IN_PROGRESS` whose parent execution is in a terminal status are marked `FAILED` and the count is logged.
- [ ] All existing workflow execution tests pass.
- [ ] New unit tests cover each fix.
