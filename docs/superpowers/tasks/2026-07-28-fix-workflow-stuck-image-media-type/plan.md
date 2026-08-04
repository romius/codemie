# Plan: Fix workflow stuck in "in progress" when model receives mismatched image media type

## Requirements

**Ticket**: EPMCDME-11918 (Major)

When a workflow step sends an image with a mismatched media type (e.g., actual GIF declared as `image/png`) to an LLM via Bedrock, the model returns HTTP 400 (`BedrockException: image/png declared but appears to be image/gif`). Currently the workflow never transitions to `FAILED` — it either retries indefinitely or hangs in `IN_PROGRESS`, requiring manual cancellation.

**Acceptance criteria:**
- Workflow transitions to `FAILED` immediately when a `BadRequestError` (HTTP 400) payload error reaches a node — no hang.
- No wasteful retries for unrecoverable 400 payload errors.
- The user-visible error message comes from the original exception (already clear from Bedrock's message).
- No manual cancellation required.

---

## Root cause analysis

Two interacting issues identified in research:

1. **`WorkflowRetryPolicy.custom_retry_on` does not suppress `BadRequestError` retries.** LangGraph retries the node until `max_attempts` is exhausted, wasting LLM budget and adding latency before any terminal state is set.

2. **`BaseNode.__call__()`'s generic `except Exception` block re-raises the exception without calling `workflow_execution_service.fail()`.** It only calls `finish_state(FAILED)` for the individual node state. If LangGraph swallows the re-raised exception internally before it reaches `WorkflowExecutor._handle_task_exception()` — the only place that calls `workflow_execution_service.fail()` — the overall workflow stays `IN_PROGRESS` indefinitely.

**Established fix pattern**: The `MCPAuthenticationRequiredException` handler in `BaseNode.__call__()` already demonstrates the correct approach: handle the specific exception, call `workflow_execution_service.fail()` (or equivalent), return `{NEXT_KEY: [END_NODE]}`, do **NOT** re-raise. This guarantees the workflow reaches a terminal state regardless of LangGraph's internal exception propagation.

---

## Implementation tasks

### Task 1 — Suppress `BadRequestError` retries in `WorkflowRetryPolicy`

**File**: `src/codemie/core/workflow_models/workflow_models.py`

**Change**: In `custom_retry_on()`, after the existing `TaskException` unwrap, add an early-return `False` for `litellm.exceptions.BadRequestError`. Unrecoverable payload errors (HTTP 400) must not be retried.

```python
# After unwrapping TaskException:
from litellm.exceptions import BadRequestError as LiteLLMBadRequestError

if isinstance(exc, LiteLLMBadRequestError):
    return False
```

**Test-first**: yes — `TC_WRP_001`: assert `WorkflowRetryPolicy.custom_retry_on(TaskException(original_exc=BadRequestError(...)))` returns `False`.

**Test file**: `tests/codemie/core/workflow_models/test_workflow_retry_policy.py` (new file).

---

### Task 2 — Guarantee `FAILED` workflow transition on `BadRequestError` in `BaseNode`

**File**: `src/codemie/workflows/nodes/base_node.py`

**Change**: In the generic `except Exception` block of `BaseNode.__call__()`, after the existing node-state finalization (logging, `finish_state(FAILED)`, callbacks), detect whether the exception is a `BadRequestError` (directly or via `TaskException.original_exc`). If so, call `workflow_execution_service.fail()` and return `{NEXT_KEY: [END_NODE]}` instead of re-raising.

```python
except Exception as e:
    self.handle_execution_failure(e)
    self.workflow_execution_service.finish_state(
        execution_state_id,
        output=str(e),
        status=WorkflowExecutionStatusEnum.FAILED,
    )
    for callback in self.callbacks:
        callback.on_node_fail(exception=e, execution_state_id=execution_state_id)

    actual_exc = e.original_exc if isinstance(e, TaskException) and e.original_exc is not None else e
    if isinstance(actual_exc, LiteLLMBadRequestError):
        self.workflow_execution_service.fail(
            error_class=type(actual_exc).__name__,
            error_message=str(actual_exc),
        )
        return {NEXT_KEY: [END_NODE]}

    raise e
```

**Test-first**: yes — `TC_BNL_BAD_REQUEST_001`: when `execute()` raises `TaskException(original_exc=BadRequestError(...))`, assert `workflow_execution_service.fail()` is called once with `error_class="BadRequestError"` and the return value equals `{NEXT_KEY: [END_NODE]}`.

**Test file**: `tests/codemie/workflows/test_base_node_lifecycle.py` (extend existing file).

---

## Files to change

| File | Change |
|---|---|
| `src/codemie/core/workflow_models/workflow_models.py` | Add `BadRequestError` suppression in `custom_retry_on()` |
| `src/codemie/workflows/nodes/base_node.py` | Add `BadRequestError` handler in generic `except` block |
| `tests/codemie/core/workflow_models/test_workflow_retry_policy.py` | New test file for retry policy |
| `tests/codemie/workflows/test_base_node_lifecycle.py` | Add `TC_BNL_BAD_REQUEST_001` test |

## Out of scope

PIL-based proactive mime-type detection at `ImageService.filter_base64_images()` — this would prevent the error from reaching the model but is not required by the ACs and can be a separate follow-up.
