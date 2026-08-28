# Technical Research

**Task**: workflow execution error propagation background tasks interrupt
**Generated**: 2026-07-23T00:00:00
**Research path**: filesystem

---

## 1. Original Context

Fix silent workflow hangs where workflow steps remain stuck in IN_PROGRESS status indefinitely due to background failures not being propagated. JIRA ticket EPMCDME-10973. The issue: during workflow execution, certain steps display a loading indicator for 30-60 minutes (normally 2-3 min) with no error surfaced. Caused by exceptions being swallowed in background threads, background tasks (FastAPI BackgroundTasks), and node execution paths. Key findings already identified from codebase analysis:

1. summarize_conversation_node.py:112-114 — except Exception: return "" swallows all LLM errors silently
2. routers/utils.py:176 — background_tasks.add_task(stream) future discarded, no error recovery
3. workflow_executions.py:351,458 — same BackgroundTasks pattern  
4. workflow_execution_service.py:486-497 — _interrupt_predecessor_state only updates SUCCEEDED states, not IN_PROGRESS
5. base_node.py:265-273 — if finish_state() throws, raise e is skipped, state stays IN_PROGRESS
6. workflow_execution_service.py:57-65 — fail()/finish() return silently when execution not found
7. workflow.py:875-884 — InterruptedException path never calls finish()
8. thought_consumer.py — if ThoughtConsumer crashes, no finish_state(FAILED) called

---

## 2. Codebase Findings

### Existing Implementations

**Core execution orchestration:**
- `/Users/bohdan_maliar/Projects/codemie-dev/codemie/src/codemie/workflows/workflow.py` — `WorkflowExecutor` class; owns `_execute_workflow_stream()`, `stream()`, `stream_to_client()`, `_handle_interrupt()`, `_handle_task_exception()`
- `/Users/bohdan_maliar/Projects/codemie-dev/codemie/src/codemie/service/workflow_execution/workflow_execution_service.py` — `WorkflowExecutionService`; owns `start_state()`, `finish_state()`, `fail()`, `finish()`, `interrupt()`, `abort()`, `_interrupt_predecessor_state()`
- `/Users/bohdan_maliar/Projects/codemie-dev/codemie/src/codemie/workflows/nodes/base_node.py` — `BaseNode.__call__()`, the single node lifecycle entry point: start_state → execute → finish_state
- `/Users/bohdan_maliar/Projects/codemie-dev/codemie/src/codemie/service/workflow_execution/thought_consumer.py` — `ThoughtConsumer.consume()` runs in a separate thread from `consumer_executor` pool; saves DB thought records
- `/Users/bohdan_maliar/Projects/codemie-dev/codemie/src/codemie/workflows/nodes/summarize_conversation_node.py` — `SummarizeConversationCommandNode` and `SummarizeConversationNode`

**API layer:**
- `/Users/bohdan_maliar/Projects/codemie-dev/codemie/src/codemie/rest_api/routers/workflow_executions.py` — `create_workflow_execution` (line 350–351) and `resume_workflow_execution` (line 458); both call `background_tasks.add_task(stream)` for non-streaming mode
- `/Users/bohdan_maliar/Projects/codemie-dev/codemie/src/codemie/rest_api/routers/utils.py` — thread pool executors: `producer_executor`, `consumer_executor`, `assistant_executor`; `_serve_workflow_stream()` which calls `run_producer_in_thread_pool(workflow.stream_to_client)` and discards the future

**Status enum:**
- `WorkflowExecutionStatusEnum`: `IN_PROGRESS`, `SUCCEEDED`, `FAILED`, `ABORTED`, `INTERRUPTED`, `AUTHENTICATION_REQUIRED`

### Architecture and Layers Affected

**API layer** (`workflow_executions.py`)
- `create_workflow_execution` dispatches `workflow.stream` via `background_tasks.add_task()` (non-streaming path) or via `run_producer_in_thread_pool()` (streaming path). The streaming future is captured indirectly in `_serve_workflow_stream`; the background-tasks future is fully discarded.
- `resume_workflow_execution` has the identical pattern at line 458.

**Workflow orchestration layer** (`workflow.py` — `WorkflowExecutor`)
- `_execute_workflow_stream()` is the top-level entry point for both `stream()` (background mode) and `stream_to_client()` (streaming mode).
- The method has a layered try/except/finally structure:
  - `InterruptedException` → `_handle_interrupt()` → calls `workflow_execution_service.interrupt()`
  - `Exception` → `_handle_task_exception()` → calls `workflow_execution_service.fail()`
  - `finally` block → calls `workflow_execution_service.finish()` only when `workflow_succeeded=True`
- `_handle_interrupt()` does NOT call `finish()`.
- `_drain_thought_consumer()` catches and logs `ThoughtConsumer` failures but does not trigger any state cleanup.

**Node execution layer** (`base_node.py` — `BaseNode.__call__()`)
- Full lifecycle: `start_state()` → `execute()` → `finish_state(SUCCEEDED/ABORTED/FAILED)`.
- The inner `except Exception` block (lines 263–273) calls `finish_state(FAILED)` and then `raise e`. If `finish_state()` itself throws (e.g., DB error), the exception propagates before `raise e` — meaning `finish_state` succeeded but re-raise was skipped (actually: if `finish_state` throws, the re-raise IS skipped because the new exception propagates instead; the outer workflow exception handler catches a DB error, not the original node error). More critically: if `finish_state()` throws, `start_state()` has already been called, so the state record sits permanently in `IN_PROGRESS`.

**Service layer** (`workflow_execution_service.py`)
- `fail()` / `finish()`: when `_refresh_workflow_execution()` returns `None` (execution not found), both methods log an error and return immediately — no state change occurs. Any `IN_PROGRESS` step records are not touched. This is a silent no-op.
- `_interrupt_predecessor_state()` (lines 486–497): iterates over execution states looking for predecessors whose `status == SUCCEEDED`. States currently `IN_PROGRESS` (a node mid-execution when interrupt fires) are never updated to `INTERRUPTED`. They stay `IN_PROGRESS` permanently.
- `finish_state()`: no guard against `state` being `None` from `get_by_id` if the record was somehow deleted. A `NoneType` attribute access would throw and the state stays `IN_PROGRESS`.

**Summarization node** (`summarize_conversation_node.py`)
- `SummarizeConversationCommandNode.execute()` lines 112–114: `except Exception as e: logger.error(...); return summary` where `summary = ""`. This returns an empty string as if nothing happened, suppressing all LLM call failures. Because `BaseNode.__call__()` sees a normal return from `execute()`, it calls `finish_state(SUCCEEDED)` with an empty output, masking the failure completely.
- `SummarizeConversationNode.execute()` (the simpler variant): has NO try/except — exceptions propagate up to `BaseNode.__call__()` correctly (so this variant is fine).

**ThoughtConsumer** (`thought_consumer.py`)
- `consume()` has no try/except around its `while True` loop body. If any DB save (`thought.save(refresh=True)`) raises, the exception propagates out of `consume()` and the consumer thread dies. The thread pool future captures this exception but nothing reads it: `_drain_thought_consumer()` calls `consumer_future.result(timeout=30)`, logs the error, then continues. However by the time drain runs, `thought_queue.close()` has already been called — the workflow's `finally` block runs to completion. The workflow-level status transitions still happen. The stuck state risk is lower here, but thoughts are silently lost and the DB contains partial data. Under concurrency, if the consumer crashes before `StopIteration` is processed, `_drain_thought_consumer` will timeout at 30 s and log, but workflow execution continues normally.

### Integration Points

- `WorkflowExecutor` ← `WorkflowExecutionService` (start/finish/fail/interrupt states)
- `WorkflowExecutor` ← `ThoughtConsumer` (via `consumer_executor` thread pool; future returned from `run_consumer_in_thread_pool`)
- `WorkflowExecutor` ← `ThoughtQueue` / `DualQueue` (streaming + persistence queues)
- `workflow_executions.py` ← `FastAPI BackgroundTasks` (non-streaming path) and `producer_executor` thread pool (streaming path)
- `BaseNode.__call__()` ← `WorkflowExecutionService.start_state()` / `finish_state()` / `fail()` directly
- `SummarizeConversationCommandNode` ← LLM via `get_llm_by_credentials()`
- `WorkflowExecutionService` ← `WorkflowExecutionState` (Elasticsearch document — `save()`, `get_by_id()`, `get_all_by_fields()`)
- `WorkflowExecutionService` ← `WorkflowExecution` (Elasticsearch document — `update()`)

### Patterns and Conventions

**Correct terminal state sequence (the contract):**
1. `start_state(...)` → creates `WorkflowExecutionState` with `status=IN_PROGRESS`, returns `execution_state_id`
2. `finish_state(execution_state_id, output, status)` → updates status to `SUCCEEDED`, `FAILED`, or `ABORTED`
3. At workflow level: `fail(...)` or `finish()` or `interrupt(...)` sets `WorkflowExecution.overall_status`

Any path that calls `start_state` but does not reliably reach `finish_state` leaves the state document at `IN_PROGRESS` indefinitely.

**Error propagation contract in `BaseNode.__call__()`:**
```python
# lines 263-273 (simplified)
except Exception as e:
    self.handle_execution_failure(e)          # logs
    self.workflow_execution_service.finish_state(
        execution_state_id,
        output=str(e),
        status=WorkflowExecutionStatusEnum.FAILED,
    )
    for callback in self.callbacks:
        callback.on_node_fail(...)
    raise e   # <-- re-raises to workflow level
```
If `finish_state()` itself raises (e.g., DB outage), the `raise e` is never reached. The new DB exception propagates up, `after_execution` runs via `finally`, and the workflow-level handler catches a DB exception rather than the original node exception — potentially leading to a misleading error message. The state record stays `IN_PROGRESS`.

**`finish()` is only called on success path:**
```python
# workflow.py lines 871-884
workflow_succeeded = False
try:
    self._run_workflow_execution(...)
    workflow_succeeded = True
except InterruptedException as e:
    self._handle_interrupt(...)      # calls interrupt(), never finish()
except Exception as e:
    self._handle_task_exception(...)  # calls fail()
finally:
    self.thought_queue.close()
    self._drain_thought_consumer(consumer_future)
    if workflow_succeeded:
        self.workflow_execution_service.finish()   # only on success
```
`_handle_interrupt()` calls only `interrupt()`, which transitions the workflow execution to `INTERRUPTED` and calls `_interrupt_predecessor_state()`. But `_interrupt_predecessor_state()` only matches states with `status==SUCCEEDED` — any node that was `IN_PROGRESS` at the moment of interruption is orphaned.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `/Users/bohdan_maliar/Projects/codemie-dev/codemie/.ai-run/guides/workflows/langgraph-workflows.md` — covers `WorkflowExecutor` extension pattern and node conventions; does not address error propagation specifics.
- `/Users/bohdan_maliar/Projects/codemie-dev/codemie/.ai-run/guides/development/error-handling.md` — covers typed exceptions and "log internal detail, return safe message" convention; does not cover workflow state lifecycle.

### Architectural Decisions

- The split between `stream()` (background mode, ThoughtConsumer for DB) and `stream_to_client()` (streaming to HTTP client, same ThoughtConsumer) is documented inline in `workflow.py`. Both call `_execute_workflow_stream(enable_verbose_consumer=True)`.
- Thread pool separation (`producer_executor`, `consumer_executor`, `assistant_executor`, `executor`) is documented in `routers/utils.py` lines 36–50: "Workflow streaming producers, assistant execution, and ThoughtConsumer readers must not share a pool because they can block one another under load."
- `record_transition()` is intentionally non-raising (lines 384–390): "Non-raising: observability failures should never kill workflow execution." This is the documented pattern for non-critical observability paths — a useful template for other non-critical paths.

### Derived Conventions

1. **Terminal states must always be set.** Every code path that calls `start_state()` must guarantee a corresponding `finish_state()` call, even on exceptions. The `BaseNode` try/except/finally partially enforces this but has a gap when `finish_state()` itself throws.
2. **`raise e` must not be preceded by fallible operations without their own guard.** If `finish_state()` can fail, it must be wrapped in its own try/except so that `raise e` is always reached.
3. **Background futures must be awaited or monitored.** The `run_consumer_in_thread_pool` pattern returns a `Future`. The `ThoughtConsumer` future is properly awaited in `_drain_thought_consumer()`. The `run_producer_in_thread_pool(workflow.stream_to_client)` future in `_serve_workflow_stream()` is NOT awaited — producer errors are invisible to the HTTP response path but ultimately the client receives a closed stream.
4. **Silent returns from `except Exception: return default` are prohibited by the error-handling guide.** The convention is to log with context and let exceptions propagate (or call `finish_state(FAILED)` and re-raise for node-level exceptions).

---

## 4. Testing Landscape

### Existing Coverage

- `/Users/bohdan_maliar/Projects/codemie-dev/codemie/tests/codemie/workflows/test_base_node_lifecycle.py` — comprehensive lifecycle tests for `BaseNode.__call__()`: success path, redirect, abort, exception handling, callback order, output_key, iteration, guardrails. Covers `finish_state(FAILED)` being called when `execute()` raises (test `test_node_execution_failure_with_exception_handling`). Does NOT test the case where `finish_state()` itself raises.
- `/Users/bohdan_maliar/Projects/codemie-dev/codemie/tests/codemie/service/workflow_execution/test_thought_consumer.py` — good coverage of `ThoughtConsumer.consume()` including error on `save()` (verifies it raises). Does NOT test failure propagation back to `WorkflowExecutionService` state cleanup.
- `/Users/bohdan_maliar/Projects/codemie-dev/codemie/tests/codemie/workflows/test_workflow_state_transitions.py` — covers state transitions, iteration, convergence detection. Does NOT cover interrupt state cleanup or background exception propagation.
- `/Users/bohdan_maliar/Projects/codemie-dev/codemie/tests/codemie/workflows/test_auto_delete_execution.py` — covers auto-delete path.
- No tests for: `_interrupt_predecessor_state()` failing to update `IN_PROGRESS` states; `SummarizeConversationCommandNode` swallowing LLM exceptions; `finish()` / `fail()` behavior when execution not found; the `workflow_succeeded=False` + interrupted combined path.

### Testing Framework and Patterns

- pytest with `unittest.mock.Mock` and `patch`
- Fixtures: `mock_workflow_execution_service`, `mock_thought_queue`, `mock_callbacks` in `test_base_node_lifecycle.py`
- `WorkflowExecutionService` is always mocked via `Mock()` with `start_state = Mock(return_value="state_123")` and `finish_state = Mock()`
- `pytest.raises` used for exception propagation verification
- No integration tests for the full background thread execution path

### Coverage Gaps

1. No test for `_interrupt_predecessor_state()` with an `IN_PROGRESS` predecessor state.
2. No test for `SummarizeConversationCommandNode.execute()` LLM failure path — specifically verifying that swallowing the exception does NOT call `finish_state(FAILED)`.
3. No test for `BaseNode.__call__()` when `finish_state()` throws inside the `except Exception` handler.
4. No test for `_handle_interrupt()` verifying that no `finish()` is called (and thus overall_status may not be properly set in non-standard interrupt paths).
5. No test for `WorkflowExecutionService.fail()` / `finish()` when `_refresh_workflow_execution()` returns `None`.
6. No test for `ThoughtConsumer` crash while states are still `IN_PROGRESS`.

---

## 5. Configuration and Environment

### Environment Variables

None specific to this fix. Thread pool sizing from `config.THREAD_POOL_MAX_WORKERS` and `config.ASSISTANT_THREAD_POOL_MAX_WORKERS` (see `routers/utils.py` lines 38, 44) affects concurrency under load but not the correctness of error propagation.

### Configuration Files

- `/Users/bohdan_maliar/Projects/codemie-dev/codemie/src/codemie/configs/` — application config, controls thread pool sizes.
- No feature flags controlling error propagation paths.

### Feature Flags and Deployment Concerns

- No feature flags gate the error propagation paths being fixed. Changes apply to all workflow executions in all environments.
- `delete_on_completion` flag: when `True`, `_auto_delete_execution()` runs in the `finally` block — only for terminal states (SUCCEEDED / FAILED). This works correctly today and is not implicated in the bug.
- The fix must be backward-compatible: no changes to `WorkflowExecutionStatusEnum` values, no DB schema changes. All fixes are behavioral (exception handling, state transition logic).

---

## 6. Risk Indicators

- **`SummarizeConversationCommandNode.execute()` at `summarize_conversation_node.py:112-114` swallows all LLM exceptions silently.** The `except Exception: return summary` (where `summary=""`) means: (a) `BaseNode` records `finish_state(SUCCEEDED, output="")` masking the failure, (b) the overall workflow execution proceeds as if summarization succeeded with empty output, (c) downstream nodes receive broken state. The correct fix is to remove the try/except so `BaseNode`'s existing exception handler fires.

- **`_interrupt_predecessor_state()` at `workflow_execution_service.py:486-497` only transitions `SUCCEEDED` states to `INTERRUPTED`.** The filter `state.status == WorkflowExecutionStatusEnum.SUCCEEDED` (line 494) means any node executing concurrently with the interrupt (status `IN_PROGRESS`) is never updated. These states remain `IN_PROGRESS` forever. Fix: also match `IN_PROGRESS` states and set them to `INTERRUPTED` (or `FAILED` — must decide semantics with product).

- **`finish_state()` throw inside `BaseNode.__call__()` exception handler prevents `raise e` at `base_node.py:265-273`.** If `finish_state()` raises (DB outage, ES timeout), the original node exception is replaced by the DB exception. The state record stays `IN_PROGRESS`. Fix: wrap the `finish_state()` call in its own try/except so `raise e` is always reached.

- **`_handle_interrupt()` at `workflow.py:875-876` never calls `finish()`.** After `interrupt()` is called, the `finally` block only calls `finish()` if `workflow_succeeded=True` — which is `False` for the interrupt path. So `WorkflowExecution.overall_status` is correctly set to `INTERRUPTED` by `interrupt()`, but the `_drain_thought_consumer()` and cleanup still run. This path is likely correct for the interrupt case but should be explicitly verified — the `InterruptedException` path does NOT leave the overall execution in `IN_PROGRESS` (it calls `interrupt()` which sets `INTERRUPTED`). The real gap is the predecessor state leaving nodes `IN_PROGRESS` (see above).

- **`fail()` and `finish()` return silently when `workflow_execution` is `None` at `workflow_execution_service.py:57-65, 198-206`.** If the DB record is deleted or never found, `IN_PROGRESS` step records are never cleaned up. The log entry exists but no automatic remediation occurs. This is a defensive design choice but leaves orphaned state records on race conditions.

- **`background_tasks.add_task(stream)` at `workflow_executions.py:351, 458` uses FastAPI's `BackgroundTasks`.** FastAPI runs these after the response is sent; if the task raises, the exception is logged by Starlette's background task handler but no client notification occurs and no workflow state cleanup runs. The `stream()` method itself has a try/except/finally that calls `fail()` or `finish()`, so the workflow-level status does get set correctly in most cases. The risk is that the FastAPI BackgroundTasks scheduler can silently drop the task if the application is restarting or under shutdown pressure.

- **`ThoughtConsumer.consume()` has no exception guard on its loop body.** A DB save failure terminates the consumer thread (verified by `test_error_handling_during_save`). The `_drain_thought_consumer()` calls `consumer_future.result(timeout=30)`, catches the exception, and logs it — so the workflow continues. States are not left `IN_PROGRESS` by ThoughtConsumer failure alone. However, any `in_progress=True` thoughts in the cache at time of crash are permanently lost and their DB records never written. More importantly, the `task_done()` call for the item that caused the crash is never made (no finally in `consume()`), which could cause `queue.join()` calls elsewhere to hang indefinitely. `_serve_workflow_stream` uses `queue.get()` and `task_done()` directly, not `queue.join()`, so this specific hang risk does not materialize in the current streaming path.

- **`routers/utils.py:176` — `run_producer_in_thread_pool(workflow.stream_to_client)` future is not captured.** The future from `run_producer_in_thread_pool` is discarded in `_serve_workflow_stream`. Producer failures (e.g., unhandled exception in `stream_to_client` outside the try/except/finally) would be invisible. However, `stream_to_client()` calls `_execute_workflow_stream(enable_verbose_consumer=True)` which has the try/except/finally — so workflow-level errors DO call `fail()` or `interrupt()`. The residual risk is exceptions thrown before `_execute_workflow_stream` is entered or in the `@pyroscope_profile` decorator.

- **No tests verify `IN_PROGRESS` → `INTERRUPTED` transition when interrupt fires mid-execution.** This is the highest-impact gap given the 30–60 minute hang symptom.

---

## 7. Summary for Complexity Assessment

The task targets eight distinct defect sites across five files, all in the workflow execution error propagation path. The primary symptom — steps stuck `IN_PROGRESS` for 30–60 minutes — has multiple independent root causes that must all be fixed: (1) `SummarizeConversationCommandNode` swallowing LLM exceptions; (2) `_interrupt_predecessor_state()` skipping `IN_PROGRESS` states; (3) `BaseNode.__call__()` exception handler being broken if `finish_state()` itself throws; (4) silent early returns in `fail()`/`finish()` when execution not found. Each fix is localized to 1–5 lines but requires careful reasoning about concurrency, exception semantics, and the start/finish lifecycle contract.

The architecture is well-structured: `BaseNode` owns the per-node lifecycle (start_state → execute → finish_state), `WorkflowExecutor` owns the workflow lifecycle (start → execute nodes → fail/finish/interrupt), and `WorkflowExecutionService` is the single write authority for both `WorkflowExecution` and `WorkflowExecutionState` records. All fixes stay within these boundaries — no architectural changes are needed. The most surgical fix is `SummarizeConversationCommandNode` (remove the try/except block: 3 lines deleted). The most impactful is `_interrupt_predecessor_state()` (add `IN_PROGRESS` to the status filter: 1 line changed). The most defensively important is the `BaseNode` exception handler (`finish_state()` in its own try/except so `raise e` is always reached).

Test coverage for the affected paths is partially present for the happy-path lifecycle but gaps exist for all the specific error scenarios that cause the hangs. New tests should cover: `_interrupt_predecessor_state` with `IN_PROGRESS` predecessors; `SummarizeConversationCommandNode` LLM failure masking; `BaseNode` behavior when `finish_state()` itself throws; and `WorkflowExecutionService.fail()` behavior when execution not found. Overall complexity is medium: the changes are small and localized, but the execution threading model (background threads, thread pools, FastAPI BackgroundTasks, ThoughtConsumer) requires careful validation to avoid introducing new race conditions. An implementer unfamiliar with the `start_state`/`finish_state` contract will need to study `base_node.py:146-275` and `workflow_execution_service.py:54-91, 198-243, 261-343, 486-529` before making changes.
