# Fix Silent Workflow Hangs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate silent IN_PROGRESS hangs by closing all five exception-propagation gaps in the workflow execution path.

**Architecture:** Five surgical fixes across four files: remove a swallowed exception in the summarize node, broaden the interrupt predecessor filter, harden the BaseNode exception handler with a best-effort fallback and unconditional re-raise, add state cleanup to `fail()` to mirror `abort()`, and add a startup scan that recovers orphaned states after pod restarts.

**Tech Stack:** Python, Elasticsearch (document model via `save()`/`get_by_id()`), FastAPI `lifespan`, pytest + `unittest.mock`.

## Global Constraints

- No schema changes — `WorkflowExecutionState` and `WorkflowExecution` ES document shapes are unchanged.
- No API changes — no new endpoints, no changed response shapes.
- No new classes or layers — helpers are module-level functions only.
- `WorkflowExecutionStatusEnum` values are unchanged.
- All `completed_at` assignments use `datetime.now()` (naive, consistent with existing `finish_state()` at line 322).
- Branch: `EPMCDME-10973_fix-silent-workflow-hangs`
- Commit prefix: `EPMCDME-10973:`

---

### Task 1: Fix 1 — Remove swallowed exception in `SummarizeConversationCommandNode`

**Test-first: yes — `test_llm_error_propagates_to_base_node_handler`**

**Files:**
- Modify: `src/codemie/workflows/nodes/summarize_conversation_node.py:81–114`
- Create: `tests/codemie/workflows/test_summarize_conversation_node.py`

**Interfaces:**
- Produces: `execute()` now raises on LLM failure → `BaseNode.__call__()` exception handler fires → `finish_state(FAILED)` is called by BaseNode (no change to BaseNode; just removes the suppression).

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/workflows/test_summarize_conversation_node.py`:

```python
import pytest
from unittest.mock import Mock, patch, MagicMock
from codemie.workflows.nodes.summarize_conversation_node import SummarizeConversationCommandNode
from codemie.core.workflow_models import WorkflowConfig, WorkflowState, WorkflowNextState


@pytest.fixture
def mock_service():
    svc = Mock()
    svc.start_state = Mock(return_value="state-001")
    svc.finish_state = Mock()
    svc.workflow_execution_id = "exec-001"
    return svc


@pytest.fixture
def mock_workflow_config():
    return WorkflowConfig(
        id="wf-1",
        name="Test",
        description="",
        states=[WorkflowState(id="summarize", assistant_id="a1", task="", next=WorkflowNextState(state_id="end"))],
    )


@pytest.fixture
def node(mock_service, mock_workflow_config):
    return SummarizeConversationCommandNode(
        callbacks=[],
        workflow_execution_service=mock_service,
        thought_queue=Mock(),
        workflow_config=mock_workflow_config,
    )


@patch("codemie.workflows.nodes.summarize_conversation_node.should_summarize_memory")
@patch("codemie.workflows.nodes.summarize_conversation_node.get_llm_by_credentials")
def test_llm_error_propagates_to_base_node_handler(mock_llm, mock_should_summarize, node, mock_service):
    """LLM exception must propagate out of execute() so BaseNode calls finish_state(FAILED)."""
    mock_should_summarize.return_value = (5000, True)
    mock_llm.return_value.invoke.side_effect = ConnectionError("LLM timeout")

    from codemie.workflows.constants import MESSAGES_VARIABLE, CONTEXT_STORE_VARIABLE
    from codemie.core.workflow_models import WorkflowExecutionStatusEnum

    state_schema = {MESSAGES_VARIABLE: [], CONTEXT_STORE_VARIABLE: {}}

    with pytest.raises(ConnectionError, match="LLM timeout"):
        node(state_schema)

    # BaseNode's except-handler must have called finish_state(FAILED)
    finish_call = mock_service.finish_state.call_args
    assert finish_call is not None
    called_status = finish_call[1].get("status") or finish_call[0][2]
    assert called_status == WorkflowExecutionStatusEnum.FAILED
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/bohdan_maliar/Projects/codemie-dev/codemie
poetry run pytest tests/codemie/workflows/test_summarize_conversation_node.py::test_llm_error_propagates_to_base_node_handler -v
```

Expected: FAIL — `finish_state` is NOT called with FAILED (current code swallows the exception and returns `""`).

- [ ] **Step 3: Remove the try/except from `SummarizeConversationCommandNode.execute()`**

In `src/codemie/workflows/nodes/summarize_conversation_node.py`, replace lines 81–114:

```python
    def execute(self, state_schema: AgentMessages, execution_context: dict):
        messages = get_messages_from_state_schema(state_schema=state_schema)
        total_tokens, should_summarize = should_summarize_memory(self.workflow_config, messages)

        if not should_summarize:
            return None

        logger.info(f"Summarizing workflow conversation because it's too long, next: {state_schema.get(NEXT_KEY)}")
        if total_tokens > MAX_TOKENS_LIMIT:
            messages_to_process = messages[1:]
            message_batches = _create_message_batches(messages=messages_to_process, max_tokens=MAX_TOKENS_LIMIT)
            batch_summaries = []
            llm = get_llm_by_credentials(request_id=self.request_id)
            for batch in message_batches:
                response = llm.invoke(batch + [HumanMessage(content=result_summarizer_prompt)])
                batch_summaries.append(str(response.content))
            return "\n\nCombined Summary:\n" + "\n".join(batch_summaries)
        else:
            llm = get_llm_by_credentials(request_id=self.request_id)
            response = llm.invoke(messages + [HumanMessage(content=result_summarizer_prompt)])
            return str(response.content)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
poetry run pytest tests/codemie/workflows/test_summarize_conversation_node.py::test_llm_error_propagates_to_base_node_handler -v
```

Expected: PASS.

- [ ] **Step 5: Run the base node lifecycle suite to confirm no regression**

```bash
poetry run pytest tests/codemie/workflows/test_base_node_lifecycle.py -v
```

Expected: all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/workflows/nodes/summarize_conversation_node.py \
        tests/codemie/workflows/test_summarize_conversation_node.py
git commit -m "EPMCDME-10973: remove swallowed exception in SummarizeConversationCommandNode"
```

---

### Task 2: Fix 2 — `_interrupt_predecessor_state` must update `IN_PROGRESS` states

**Test-first: yes — `TestInterruptPredecessorState::test_marks_in_progress_predecessor_as_interrupted`**

**Files:**
- Modify: `src/codemie/service/workflow_execution/workflow_execution_service.py:493–496`
- Modify: `tests/codemie/service/workflow_execution/test_workflow_execution_service.py` — update `test_skips_non_succeeded_predecessor` and add one new test

**Interfaces:**
- Produces: `_interrupt_predecessor_state(interrupted_state_id)` now transitions predecessor states with status `SUCCEEDED` **or** `IN_PROGRESS` to `INTERRUPTED`.

- [ ] **Step 1: Add the failing test**

In `tests/codemie/service/workflow_execution/test_workflow_execution_service.py`, add inside `TestInterruptPredecessorState`:

```python
@patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
def test_marks_in_progress_predecessor_as_interrupted(self, mock_get_states, service):
    """A predecessor that was mid-execution when interrupt fired must become INTERRUPTED."""
    state_a = _make_state("state_a", WorkflowExecutionStatusEnum.IN_PROGRESS)
    mock_get_states.return_value = [state_a]

    service._interrupt_predecessor_state("state_b")

    assert state_a.status == WorkflowExecutionStatusEnum.INTERRUPTED
    state_a.save.assert_called_once()
```

- [ ] **Step 2: Run the new test to verify it fails**

```bash
poetry run pytest tests/codemie/service/workflow_execution/test_workflow_execution_service.py::TestInterruptPredecessorState::test_marks_in_progress_predecessor_as_interrupted -v
```

Expected: FAIL — `state_a.status` is still `IN_PROGRESS` (not updated by current code).

- [ ] **Step 3: Update `_interrupt_predecessor_state` to include `IN_PROGRESS` in the filter**

In `src/codemie/service/workflow_execution/workflow_execution_service.py`, replace line 494:

```python
        for state in states:
            if state.state_id in predecessor_ids and state.status in (
                WorkflowExecutionStatusEnum.SUCCEEDED,
                WorkflowExecutionStatusEnum.IN_PROGRESS,
            ):
                state.status = WorkflowExecutionStatusEnum.INTERRUPTED
                state.save()
                return state.id
```

- [ ] **Step 4: Update the existing test that asserted the old broken behaviour**

`test_skips_non_succeeded_predecessor` (line 89) verified that `IN_PROGRESS` was NOT updated — this was describing the bug. Update it to assert the correct post-fix behaviour:

```python
@patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
def test_skips_non_predecessor_with_in_progress_status(self, mock_get_states, service):
    """Non-predecessor states must never be updated, regardless of status."""
    # state_a does not transition to "end" — it is not a predecessor of the interrupted state
    state_a = _make_state("state_a", WorkflowExecutionStatusEnum.IN_PROGRESS)
    mock_get_states.return_value = [state_a]

    service._interrupt_predecessor_state("end")

    assert state_a.status == WorkflowExecutionStatusEnum.IN_PROGRESS
    state_a.save.assert_not_called()
```

- [ ] **Step 5: Run the full `TestInterruptPredecessorState` class to verify all tests pass**

```bash
poetry run pytest tests/codemie/service/workflow_execution/test_workflow_execution_service.py::TestInterruptPredecessorState -v
```

Expected: all pass (including the updated test and the new test).

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/workflow_execution/workflow_execution_service.py \
        tests/codemie/service/workflow_execution/test_workflow_execution_service.py
git commit -m "EPMCDME-10973: interrupt IN_PROGRESS predecessor states on workflow interrupt"
```

---

### Task 3: Fix 3 — Guard `finish_state()` in `BaseNode.__call__` so `raise e` is unconditional

**Test-first: yes — `test_original_exception_raised_when_finish_state_throws`**

**Files:**
- Modify: `src/codemie/workflows/nodes/base_node.py:263–273`
- Modify: `tests/codemie/workflows/test_base_node_lifecycle.py` — add two tests

**Interfaces:**
- Consumes: `WorkflowExecutionState.get_by_id(id_)` — returns the ES document or `None`
- Consumes: `WorkflowExecutionStatusEnum.IN_PROGRESS`, `WorkflowExecutionStatusEnum.FAILED`
- Produces: The original node exception (`e`) is always re-raised. `finish_state(FAILED)` is attempted first; if it throws, a direct ES write is attempted as fallback; if that also throws, the orphan is logged and startup recovery (Task 5) will clean it up.

- [ ] **Step 1: Write two failing tests**

In `tests/codemie/workflows/test_base_node_lifecycle.py`, add after the existing `test_node_execution_failure_with_exception_handling`:

```python
def test_original_exception_raised_when_finish_state_throws(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """Original node exception must be re-raised even when finish_state() itself throws."""
    from codemie.core.workflow_models import WorkflowExecutionStatusEnum
    from codemie.core.workflow_models.workflow_execution import WorkflowExecutionState

    workflow_state = WorkflowState(
        id="test_node", task="Test", assistant_id="a1", next=WorkflowNextState(state_id="next"),
    )
    state_schema = {MESSAGES_VARIABLE: [], CONTEXT_STORE_VARIABLE: {}}

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )

    original_error = ValueError("node failed")

    def failing_execute(*args, **kwargs):
        raise original_error

    node.execute = failing_execute
    mock_workflow_execution_service.finish_state.side_effect = OSError("DB unavailable")

    fake_state = Mock()
    fake_state.status = WorkflowExecutionStatusEnum.IN_PROGRESS

    with patch.object(WorkflowExecutionState, "get_by_id", return_value=fake_state):
        with pytest.raises(ValueError, match="node failed"):
            node(state_schema)

    # The direct fallback must have marked the state FAILED
    assert fake_state.status == WorkflowExecutionStatusEnum.FAILED
    fake_state.save.assert_called_once()


def test_original_exception_raised_when_both_finish_state_and_fallback_throw(
    mock_workflow_execution_service, mock_thought_queue, mock_callbacks
):
    """Original node exception must be re-raised even when finish_state AND fallback both fail."""
    from codemie.core.workflow_models.workflow_execution import WorkflowExecutionState

    workflow_state = WorkflowState(
        id="test_node", task="Test", assistant_id="a1", next=WorkflowNextState(state_id="next"),
    )
    state_schema = {MESSAGES_VARIABLE: [], CONTEXT_STORE_VARIABLE: {}}

    node = MockNode(
        callbacks=mock_callbacks,
        workflow_execution_service=mock_workflow_execution_service,
        thought_queue=mock_thought_queue,
        workflow_state=workflow_state,
    )

    original_error = RuntimeError("catastrophic failure")

    def failing_execute(*args, **kwargs):
        raise original_error

    node.execute = failing_execute
    mock_workflow_execution_service.finish_state.side_effect = OSError("DB unavailable")

    with patch.object(WorkflowExecutionState, "get_by_id", side_effect=OSError("ES also down")):
        with pytest.raises(RuntimeError, match="catastrophic failure"):
            node(state_schema)
```

- [ ] **Step 2: Run the two new tests to verify they fail**

```bash
poetry run pytest tests/codemie/workflows/test_base_node_lifecycle.py::test_original_exception_raised_when_finish_state_throws tests/codemie/workflows/test_base_node_lifecycle.py::test_original_exception_raised_when_both_finish_state_and_fallback_throw -v
```

Expected: both FAIL — current code lets the `OSError` from `finish_state()` escape, so `pytest.raises(ValueError)` doesn't match.

- [ ] **Step 3: Update the `except Exception` handler in `BaseNode.__call__()`**

In `src/codemie/workflows/nodes/base_node.py`, replace lines 263–273:

```python
        except Exception as e:
            self.handle_execution_failure(e)
            try:
                self.workflow_execution_service.finish_state(
                    execution_state_id,
                    output=str(e),
                    status=WorkflowExecutionStatusEnum.FAILED,
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

Add these two imports to `base_node.py` (neither is currently present — verified):

```python
from datetime import datetime
from codemie.core.workflow_models.workflow_execution import WorkflowExecutionState
```

Add them directly after the existing `from codemie.core.workflow_models import WorkflowExecutionStatusEnum, WorkflowState, WorkflowConfig` line (line 24).

- [ ] **Step 4: Run all three tests (two new + the existing failure test) to verify they pass**

```bash
poetry run pytest tests/codemie/workflows/test_base_node_lifecycle.py::test_original_exception_raised_when_finish_state_throws tests/codemie/workflows/test_base_node_lifecycle.py::test_original_exception_raised_when_both_finish_state_and_fallback_throw tests/codemie/workflows/test_base_node_lifecycle.py::test_node_execution_failure_with_exception_handling -v
```

Expected: all three PASS.

- [ ] **Step 5: Run the full base node lifecycle suite**

```bash
poetry run pytest tests/codemie/workflows/test_base_node_lifecycle.py -v
```

Expected: all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/workflows/nodes/base_node.py \
        tests/codemie/workflows/test_base_node_lifecycle.py
git commit -m "EPMCDME-10973: guard finish_state in BaseNode so raise e is unconditional"
```

---

### Task 4: Fix 4 — `fail()` must clean up in-flight states

**Test-first: yes — `TestFail::test_fail_marks_in_progress_states_as_failed`**

**Files:**
- Modify: `src/codemie/service/workflow_execution/workflow_execution_service.py:54–91` (`fail()` method)
- Modify: `tests/codemie/service/workflow_execution/test_workflow_execution_service.py` — add a new test class

**Interfaces:**
- Consumes: `WorkflowExecutionState.get_all_by_fields(fields={EXECUTION_ID_KEYWORD: ...})` — same call already used by `abort()` (line 113)
- Produces: after `fail()` returns, all `WorkflowExecutionState` rows for the execution with status `IN_PROGRESS` or `INTERRUPTED` have status `FAILED` and a `completed_at` set.

- [ ] **Step 1: Write the failing tests**

In `tests/codemie/service/workflow_execution/test_workflow_execution_service.py`, add a new class after `TestInterruptPredecessorState`:

```python
class TestFail:
    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowMonitoringService")
    def test_fail_marks_in_progress_states_as_failed(self, mock_monitoring, mock_get_states, service):
        """fail() must transition IN_PROGRESS step states to FAILED."""
        in_progress_state = _make_state("state_a", WorkflowExecutionStatusEnum.IN_PROGRESS)
        succeeded_state = _make_state("state_b", WorkflowExecutionStatusEnum.SUCCEEDED)
        mock_get_states.return_value = [in_progress_state, succeeded_state]

        service.fail(error_class="ValueError", error_message="something broke")

        assert in_progress_state.status == WorkflowExecutionStatusEnum.FAILED
        in_progress_state.save.assert_called_once()
        # SUCCEEDED step must not be touched
        assert succeeded_state.status == WorkflowExecutionStatusEnum.SUCCEEDED
        succeeded_state.save.assert_not_called()

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowMonitoringService")
    def test_fail_marks_interrupted_states_as_failed(self, mock_monitoring, mock_get_states, service):
        """fail() must transition INTERRUPTED step states to FAILED (mirrors abort())."""
        interrupted_state = _make_state("state_a", WorkflowExecutionStatusEnum.INTERRUPTED)
        mock_get_states.return_value = [interrupted_state]

        service.fail(error_class="RuntimeError", error_message="boom")

        assert interrupted_state.status == WorkflowExecutionStatusEnum.FAILED
        interrupted_state.save.assert_called_once()

    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowExecutionState.get_all_by_fields")
    @patch("codemie.service.workflow_execution.workflow_execution_service.WorkflowMonitoringService")
    def test_fail_state_cleanup_error_does_not_prevent_metric(self, mock_monitoring, mock_get_states, service):
        """A DB error during state cleanup must not block the monitoring metric call."""
        broken_state = _make_state("state_a", WorkflowExecutionStatusEnum.IN_PROGRESS)
        broken_state.save.side_effect = OSError("ES unavailable")
        mock_get_states.return_value = [broken_state]

        # Must not raise
        service.fail(error_class="SomeError", error_message="err")

        mock_monitoring.send_workflow_execution_metric.assert_called_once()
```

- [ ] **Step 2: Run the new tests to verify they fail**

```bash
poetry run pytest tests/codemie/service/workflow_execution/test_workflow_execution_service.py::TestFail -v
```

Expected: all three FAIL — `fail()` currently does not iterate states.

- [ ] **Step 3: Add state cleanup to `fail()`**

In `src/codemie/service/workflow_execution/workflow_execution_service.py`, update the `fail()` method. After `self.workflow_execution.update(refresh=True)` (line 75), add the state cleanup loop inside the `with self.workflow_execution_lock:` block:

```python
    def fail(self, error_class: str, error_message: str):
        with self.workflow_execution_lock:
            self._refresh_workflow_execution()
            if not self.workflow_execution:
                logger.error(
                    f"Workflow execution not found for execution_id: {self.workflow_execution_id}. "
                    "Cannot mark workflow as failed."
                )
            else:
                logger.warning(
                    f"Overall status for Execution ID: {self.workflow_execution.execution_id} "
                    f"set to {WorkflowExecutionStatusEnum.FAILED}, Reason = {error_message}"
                )

                self.workflow_execution.overall_status = WorkflowExecutionStatusEnum.FAILED
                self.workflow_execution.tokens_usage = self._calculate_tokens_usage(self.workflow_execution_id)
                self.workflow_execution.output = error_message

                # Update assistant response in history
                self._update_assistant_response_in_history(error_message)

                self.workflow_execution.update(refresh=True)

                try:
                    states = WorkflowExecutionState.get_all_by_fields(
                        fields={EXECUTION_ID_KEYWORD: self.workflow_execution_id}
                    )
                    for state in states:
                        if state.status in (
                            WorkflowExecutionStatusEnum.IN_PROGRESS,
                            WorkflowExecutionStatusEnum.INTERRUPTED,
                        ):
                            state.status = WorkflowExecutionStatusEnum.FAILED
                            state.completed_at = datetime.now()
                            state.save()
                except Exception as cleanup_exc:
                    logger.error(
                        f"Failed to clean up in-flight states for execution {self.workflow_execution_id}: {cleanup_exc}"
                    )

        if not self.workflow_execution:
            self._flush_llm_usage_metric(
                WorkflowExecutionStatusEnum.FAILED,
                additional_attributes={"error_class": error_class, "error_cause": error_message},
            )
            return

        WorkflowMonitoringService.send_workflow_execution_metric(
            workflow_config=self.workflow_config,
            workflow_execution_config=self.workflow_execution,
            user=self.user,
            request_id=self.workflow_execution_id,
            additional_attributes={"error_class": error_class, "error_cause": error_message},
        )
        request_summary_manager.clear_summary(self.workflow_execution_id)
```

`from datetime import datetime` is already present at line 16 of `workflow_execution_service.py` — no import changes needed.

- [ ] **Step 4: Run the new test class to verify all three tests pass**

```bash
poetry run pytest tests/codemie/service/workflow_execution/test_workflow_execution_service.py::TestFail -v
```

Expected: all three PASS.

- [ ] **Step 5: Run the full workflow execution service test suite**

```bash
poetry run pytest tests/codemie/service/workflow_execution/test_workflow_execution_service.py -v
```

Expected: all existing tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/workflow_execution/workflow_execution_service.py \
        tests/codemie/service/workflow_execution/test_workflow_execution_service.py
git commit -m "EPMCDME-10973: fail() cleans up IN_PROGRESS and INTERRUPTED step states"
```

---

### Task 5: Fix 5 — Startup recovery scan for orphaned `IN_PROGRESS` states

**Test-first: yes — `test_orphaned_states_are_marked_failed`**

**Files:**
- Create: `src/codemie/service/workflow_execution/startup_recovery.py`
- Create: `tests/codemie/service/workflow_execution/test_startup_recovery.py`
- Modify: `src/codemie/rest_api/main.py` — call `recover_orphaned_workflow_states()` from `lifespan` before `yield`

**Interfaces:**
- Consumes: `WorkflowExecution.get_all_by_fields(fields={"overall_status": status.value})` for each terminal status
- Consumes: `WorkflowExecutionState.get_all_by_fields(fields={EXECUTION_ID_KEYWORD: execution.execution_id})`
- Produces: `recover_orphaned_workflow_states()` — a module-level function; logs recovery count; non-raising.

- [ ] **Step 1: Write the failing tests**

Create `tests/codemie/service/workflow_execution/test_startup_recovery.py`:

```python
import pytest
from unittest.mock import MagicMock, patch, call

from codemie.core.workflow_models import WorkflowExecutionStatusEnum
from codemie.service.workflow_execution.startup_recovery import recover_orphaned_workflow_states

TERMINAL_STATUSES = [
    WorkflowExecutionStatusEnum.FAILED,
    WorkflowExecutionStatusEnum.SUCCEEDED,
    WorkflowExecutionStatusEnum.ABORTED,
    WorkflowExecutionStatusEnum.INTERRUPTED,
]


def _make_execution(execution_id):
    ex = MagicMock()
    ex.execution_id = execution_id
    return ex


def _make_state(exec_id, status):
    s = MagicMock()
    s.execution_id = exec_id
    s.status = status
    return s


@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecutionState.get_all_by_fields")
@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecution.get_all_by_fields")
def test_orphaned_states_are_marked_failed(mock_get_executions, mock_get_states):
    """IN_PROGRESS states under terminal executions are marked FAILED at startup."""
    execution = _make_execution("exec-1")
    mock_get_executions.return_value = [execution]

    orphan = _make_state("exec-1", WorkflowExecutionStatusEnum.IN_PROGRESS)
    clean = _make_state("exec-1", WorkflowExecutionStatusEnum.SUCCEEDED)
    mock_get_states.return_value = [orphan, clean]

    recover_orphaned_workflow_states()

    assert orphan.status == WorkflowExecutionStatusEnum.FAILED
    orphan.save.assert_called_once()
    assert clean.status == WorkflowExecutionStatusEnum.SUCCEEDED
    clean.save.assert_not_called()


@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecutionState.get_all_by_fields")
@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecution.get_all_by_fields")
def test_no_orphans_is_noop(mock_get_executions, mock_get_states):
    """When no orphaned states exist, nothing is written."""
    execution = _make_execution("exec-2")
    mock_get_executions.return_value = [execution]
    mock_get_states.return_value = [_make_state("exec-2", WorkflowExecutionStatusEnum.SUCCEEDED)]

    recover_orphaned_workflow_states()  # must not raise


@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecutionState.get_all_by_fields")
@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecution.get_all_by_fields")
def test_es_error_is_caught_and_does_not_block_startup(mock_get_executions, mock_get_states):
    """A DB/ES error during scan must not raise — startup must not be blocked."""
    mock_get_executions.side_effect = OSError("ES unavailable")

    recover_orphaned_workflow_states()  # must not raise
```

- [ ] **Step 2: Run the tests to verify they fail (module does not exist)**

```bash
poetry run pytest tests/codemie/service/workflow_execution/test_startup_recovery.py -v
```

Expected: `ModuleNotFoundError` / FAIL — the module doesn't exist yet.

- [ ] **Step 3: Create `startup_recovery.py`**

Create `src/codemie/service/workflow_execution/startup_recovery.py`:

```python
from datetime import datetime

from codemie.configs import logger
from codemie.core.workflow_models import WorkflowExecutionStatusEnum
from codemie.core.workflow_models.workflow_execution import WorkflowExecution, WorkflowExecutionState
from codemie.service.workflow_execution.workflow_execution_service import EXECUTION_ID_KEYWORD

_TERMINAL_STATUSES = (
    WorkflowExecutionStatusEnum.FAILED,
    WorkflowExecutionStatusEnum.SUCCEEDED,
    WorkflowExecutionStatusEnum.ABORTED,
    WorkflowExecutionStatusEnum.INTERRUPTED,
)


def recover_orphaned_workflow_states() -> None:
    """Mark IN_PROGRESS step states under terminal executions as FAILED.

    Runs once at startup to clean up states orphaned by pod restarts or
    full-DB-outage scenarios where in-process recovery (Fix 3) could not write.
    """
    try:
        recovered = 0
        for terminal_status in _TERMINAL_STATUSES:
            executions = WorkflowExecution.get_all_by_fields(
                fields={"overall_status": terminal_status.value}
            )
            for execution in executions:
                states = WorkflowExecutionState.get_all_by_fields(
                    fields={EXECUTION_ID_KEYWORD: execution.execution_id}
                )
                for state in states:
                    if state.status == WorkflowExecutionStatusEnum.IN_PROGRESS:
                        state.status = WorkflowExecutionStatusEnum.FAILED
                        state.completed_at = datetime.now()
                        state.save()
                        recovered += 1
        if recovered:
            logger.warning(f"Startup recovery: marked {recovered} orphaned IN_PROGRESS state(s) as FAILED.")
        else:
            logger.debug("Startup recovery: no orphaned workflow states found.")
    except Exception as exc:
        logger.error(f"Startup recovery scan failed: {exc}. Orphaned states will persist until next restart.")
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/codemie/service/workflow_execution/test_startup_recovery.py -v
```

Expected: all three PASS.

- [ ] **Step 5: Wire the recovery call into `lifespan` in `main.py`**

In `src/codemie/rest_api/main.py`, add the import near the other service imports (after the existing imports section):

```python
from codemie.service.workflow_execution.startup_recovery import recover_orphaned_workflow_states
```

Then in the `lifespan` function body, add the call after `_initialize_preconfigured_content()` (approximately line 661) and before the OTEL instrumentation block:

```python
    # Recover workflow states orphaned by pod restarts or DB outages
    recover_orphaned_workflow_states()
```

- [ ] **Step 6: Run the full workflow execution service test suite to confirm no regression**

```bash
poetry run pytest tests/codemie/service/workflow_execution/ -v
```

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/codemie/service/workflow_execution/startup_recovery.py \
        tests/codemie/service/workflow_execution/test_startup_recovery.py \
        src/codemie/rest_api/main.py
git commit -m "EPMCDME-10973: startup recovery scan for orphaned IN_PROGRESS workflow states"
```

---

## Self-Review

### Spec coverage

| Spec requirement | Task |
|---|---|
| Fix 1 — remove swallowed exception in SummarizeConversationCommandNode | Task 1 |
| Fix 2 — `_interrupt_predecessor_state` updates IN_PROGRESS states | Task 2 |
| Fix 3 — `finish_state()` guarded, `raise e` unconditional, direct fallback | Task 3 |
| Fix 4 — `fail()` cleans up IN_PROGRESS and INTERRUPTED step states | Task 4 |
| Fix 5 — startup recovery scan for orphaned states | Task 5 |
| New unit tests cover each fix | Tasks 1–5 each include dedicated tests |
| All existing workflow execution tests pass | Verified in each task's regression step |

### Acceptance criteria mapping

| Criterion | Task | Test |
|---|---|---|
| SummarizeConversationCommandNode LLM error → FAILED | Task 1 | `test_llm_error_propagates_to_base_node_handler` |
| IN_PROGRESS at interrupt time → INTERRUPTED | Task 2 | `test_marks_in_progress_predecessor_as_interrupted` |
| `raise e` unconditional when `finish_state()` throws | Task 3 | `test_original_exception_raised_when_finish_state_throws` |
| `raise e` unconditional when both finish_state and fallback throw | Task 3 | `test_original_exception_raised_when_both_finish_state_and_fallback_throw` |
| `fail()` transitions IN_PROGRESS and INTERRUPTED step states | Task 4 | `TestFail::test_fail_marks_in_progress_states_as_failed`, `test_fail_marks_interrupted_states_as_failed` |
| Startup scan recovers orphaned states and logs count | Task 5 | `test_orphaned_states_are_marked_failed` |
| ES error during startup scan does not block startup | Task 5 | `test_es_error_is_caught_and_does_not_block_startup` |
