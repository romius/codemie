# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.workflow_models import WorkflowExecutionStatusEnum
from codemie.service.workflow_execution.startup_recovery import (
    _mark_state_failed_if_still_stuck,
    recover_orphaned_workflow_states,
)

_TERMINAL_NON_INTERRUPTED = (
    WorkflowExecutionStatusEnum.FAILED,
    WorkflowExecutionStatusEnum.SUCCEEDED,
    WorkflowExecutionStatusEnum.ABORTED,
)


def _make_execution(execution_id, overall_status):
    e = MagicMock()
    e.execution_id = execution_id
    e.overall_status = overall_status
    return e


def _make_state(status):
    s = MagicMock()
    s.id = f"state-{id(s)}"
    s.execution_id = "exec-a"
    s.status = status
    s.started_at = datetime.now() - timedelta(minutes=1)
    return s


@pytest.fixture(autouse=True)
def mock_conditional_recovery_update(request):
    """Keep scan tests at the persistence boundary; the helper has a focused SQL test."""
    if request.node.name == "test_conditional_update_guards_snapshot_fields":
        yield
        return

    def update_state(state, _started_before, _expected_parent_status):
        state.save()
        state.status = WorkflowExecutionStatusEnum.FAILED
        state.completed_at = datetime.now()
        return True

    with patch(
        "codemie.service.workflow_execution.startup_recovery._mark_state_failed_if_still_stuck",
        side_effect=update_state,
    ):
        yield


def _states_by_status(mapping):
    def side_effect(*args, **kwargs):
        fields = kwargs.get("fields") or (args[0] if args else {})
        return list(mapping.get(fields.get("status"), []))

    return side_effect


def _fields(call):
    if call.kwargs.get("fields") is not None:
        return call.kwargs["fields"]
    return call.args[0] if call.args else {}


@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecution.get_by_execution_id")
@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecutionState.get_all_by_fields")
@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecution.get_all_by_fields")
def test_scan_starts_from_stuck_states_not_all_terminal_executions(mock_exec_all, mock_state_query, mock_get_parent):
    """Recovery must query stuck children, not every historical terminal execution."""
    orphan = _make_state(WorkflowExecutionStatusEnum.IN_PROGRESS)
    parent = _make_execution("exec-a", WorkflowExecutionStatusEnum.FAILED)
    mock_get_parent.return_value = [parent]
    mock_state_query.side_effect = _states_by_status({WorkflowExecutionStatusEnum.IN_PROGRESS: [orphan]})

    recover_orphaned_workflow_states()

    queried_state_statuses = {_fields(call).get("status") for call in mock_state_query.call_args_list}
    queried_exec_statuses = {
        _fields(call).get("overall_status")
        for call in mock_exec_all.call_args_list
        if "overall_status" in _fields(call)
    }
    assert WorkflowExecutionStatusEnum.IN_PROGRESS in queried_state_statuses
    assert WorkflowExecutionStatusEnum.INTERRUPTED in queried_state_statuses
    assert queried_exec_statuses == set()
    mock_get_parent.assert_called_once_with("exec-a")
    assert orphan.status == WorkflowExecutionStatusEnum.FAILED


@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecution.get_by_execution_id")
@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecutionState.get_all_by_fields")
@pytest.mark.parametrize("parent_status", _TERMINAL_NON_INTERRUPTED)
def test_in_progress_orphans_are_marked_failed(mock_state_query, mock_get_parent, parent_status):
    """IN_PROGRESS children under any terminal parent are marked FAILED."""
    mock_get_parent.return_value = [_make_execution("exec-a", parent_status)]
    orphan_ip = _make_state(WorkflowExecutionStatusEnum.IN_PROGRESS)
    mock_state_query.side_effect = _states_by_status({WorkflowExecutionStatusEnum.IN_PROGRESS: [orphan_ip]})

    recover_orphaned_workflow_states()

    assert orphan_ip.status == WorkflowExecutionStatusEnum.FAILED
    orphan_ip.save.assert_called_once()


@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecution.get_by_execution_id")
@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecutionState.get_all_by_fields")
@pytest.mark.parametrize("parent_status", _TERMINAL_NON_INTERRUPTED)
def test_interrupted_children_recovered_under_terminal_non_interrupted_parent(
    mock_state_query, mock_get_parent, parent_status
):
    """INTERRUPTED children under FAILED/SUCCEEDED/ABORTED parents are marked FAILED."""
    mock_get_parent.return_value = [_make_execution("exec-a", parent_status)]
    orphan_int = _make_state(WorkflowExecutionStatusEnum.INTERRUPTED)
    mock_state_query.side_effect = _states_by_status({WorkflowExecutionStatusEnum.INTERRUPTED: [orphan_int]})

    recover_orphaned_workflow_states()

    assert orphan_int.status == WorkflowExecutionStatusEnum.FAILED
    orphan_int.save.assert_called_once()


@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecution.get_by_execution_id")
@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecutionState.get_all_by_fields")
def test_interrupted_children_preserved_under_interrupted_parent(mock_state_query, mock_get_parent):
    """INTERRUPTED children of an INTERRUPTED parent are left for resume_states()."""
    mock_get_parent.return_value = [_make_execution("exec-a", WorkflowExecutionStatusEnum.INTERRUPTED)]
    orphan_ip = _make_state(WorkflowExecutionStatusEnum.IN_PROGRESS)
    orphan_int = _make_state(WorkflowExecutionStatusEnum.INTERRUPTED)
    mock_state_query.side_effect = _states_by_status(
        {
            WorkflowExecutionStatusEnum.IN_PROGRESS: [orphan_ip],
            WorkflowExecutionStatusEnum.INTERRUPTED: [orphan_int],
        }
    )

    recover_orphaned_workflow_states()

    assert orphan_ip.status == WorkflowExecutionStatusEnum.FAILED
    orphan_ip.save.assert_called_once()
    assert orphan_int.status == WorkflowExecutionStatusEnum.INTERRUPTED
    orphan_int.save.assert_not_called()


@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecution.get_by_execution_id")
@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecutionState.get_all_by_fields")
def test_live_in_progress_parent_is_not_recovered(mock_state_query, mock_get_parent):
    """IN_PROGRESS children of a still-running parent must not be failed."""
    mock_get_parent.return_value = [_make_execution("exec-a", WorkflowExecutionStatusEnum.IN_PROGRESS)]
    live_state = _make_state(WorkflowExecutionStatusEnum.IN_PROGRESS)
    mock_state_query.side_effect = _states_by_status({WorkflowExecutionStatusEnum.IN_PROGRESS: [live_state]})

    recover_orphaned_workflow_states()

    assert live_state.status == WorkflowExecutionStatusEnum.IN_PROGRESS
    live_state.save.assert_not_called()


@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecution.get_by_execution_id")
@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecutionState.get_all_by_fields")
def test_states_started_after_recovery_cutoff_are_not_modified(mock_state_query, mock_get_parent):
    """A state created after readiness must not be mistaken for a startup orphan."""
    cutoff = datetime.now()
    mock_get_parent.return_value = [_make_execution("exec-a", WorkflowExecutionStatusEnum.INTERRUPTED)]
    active_state = _make_state(WorkflowExecutionStatusEnum.IN_PROGRESS)
    active_state.started_at = cutoff + timedelta(seconds=1)
    mock_state_query.side_effect = _states_by_status({WorkflowExecutionStatusEnum.IN_PROGRESS: [active_state]})

    recover_orphaned_workflow_states(started_before=cutoff)

    assert active_state.status == WorkflowExecutionStatusEnum.IN_PROGRESS
    active_state.save.assert_not_called()


@patch(
    "codemie.service.workflow_execution.startup_recovery._mark_state_failed_if_still_stuck",
    create=True,
    return_value=False,
)
@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecution.get_by_execution_id")
@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecutionState.get_all_by_fields")
def test_concurrent_state_change_is_not_overwritten(mock_state_query, mock_get_parent, mock_conditional_update):
    """A state that no longer matches the query snapshot must not be overwritten."""
    cutoff = datetime.now()
    mock_get_parent.return_value = [_make_execution("exec-a", WorkflowExecutionStatusEnum.INTERRUPTED)]
    stale_state = _make_state(WorkflowExecutionStatusEnum.IN_PROGRESS)
    stale_state.started_at = cutoff - timedelta(seconds=1)
    mock_state_query.side_effect = _states_by_status({WorkflowExecutionStatusEnum.IN_PROGRESS: [stale_state]})

    recover_orphaned_workflow_states(started_before=cutoff)

    mock_conditional_update.assert_called_once_with(
        stale_state,
        cutoff,
        WorkflowExecutionStatusEnum.INTERRUPTED,
    )
    assert stale_state.status == WorkflowExecutionStatusEnum.IN_PROGRESS
    stale_state.save.assert_not_called()


@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecution.get_by_execution_id")
@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecutionState.get_all_by_fields")
def test_no_orphans_produces_no_saves(mock_state_query, mock_get_parent):
    """When no IN_PROGRESS or INTERRUPTED states exist, no saves are performed."""
    mock_state_query.side_effect = _states_by_status({})

    recover_orphaned_workflow_states()

    mock_get_parent.assert_not_called()


@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecutionState.get_all_by_fields")
def test_db_error_does_not_raise(mock_state_query):
    """A DB failure during recovery must be swallowed — startup must not be blocked."""
    mock_state_query.side_effect = OSError("DB unavailable")

    recover_orphaned_workflow_states()  # must not raise


@patch("codemie.service.workflow_execution.startup_recovery.logger")
@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecutionState.get_all_by_fields")
def test_db_failure_during_query_logs_at_error_not_warning(mock_state_query, mock_logger):
    """A DB failure while querying a status bucket must be logged at ERROR, not WARNING."""
    mock_state_query.side_effect = OSError("ES timeout")

    recover_orphaned_workflow_states()

    assert mock_logger.error.called, "DB query failure must be logged at ERROR level"
    warning_msgs = [str(c) for c in mock_logger.warning.call_args_list]
    assert not any("failed to query" in msg for msg in warning_msgs), "DB query failure must not be logged as WARNING"


@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecution.get_by_execution_id")
@patch("codemie.service.workflow_execution.startup_recovery.WorkflowExecutionState.get_all_by_fields")
def test_save_error_does_not_abort_scan(mock_state_query, mock_get_parent):
    """A save() failure on one state must not abort recovery of the remaining states."""
    mock_get_parent.return_value = [_make_execution("exec-a", WorkflowExecutionStatusEnum.FAILED)]
    bad_state = _make_state(WorkflowExecutionStatusEnum.IN_PROGRESS)
    bad_state.save.side_effect = OSError("constraint violation")
    good_state = _make_state(WorkflowExecutionStatusEnum.IN_PROGRESS)
    mock_state_query.side_effect = _states_by_status({WorkflowExecutionStatusEnum.IN_PROGRESS: [bad_state, good_state]})

    recover_orphaned_workflow_states()  # must not raise

    bad_state.save.assert_called_once()
    good_state.save.assert_called_once()  # scan continues past the error


@patch("codemie.service.workflow_execution.startup_recovery.Session")
def test_conditional_update_guards_snapshot_fields(mock_session):
    """Recovery must lock the expected parent before conditionally updating its child."""
    state = _make_state(WorkflowExecutionStatusEnum.IN_PROGRESS)
    cutoff = datetime.now()
    result = MagicMock(rowcount=1)
    session = mock_session.return_value.__enter__.return_value
    session.exec.return_value.first.return_value = MagicMock(overall_status=WorkflowExecutionStatusEnum.INTERRUPTED)
    session.execute.return_value = result

    assert (
        _mark_state_failed_if_still_stuck(
            state,
            cutoff,
            WorkflowExecutionStatusEnum.INTERRUPTED,
        )
        is True
    )

    parent_statement = session.exec.call_args.args[0]
    assert "FOR UPDATE" in str(parent_statement)
    statement = session.execute.call_args.args[0]
    where_sql = str(statement.whereclause)
    assert "workflow_execution_states.id" in where_sql
    assert "workflow_execution_states.status" in where_sql
    assert "workflow_execution_states.started_at" in where_sql
    session.commit.assert_called_once()


@patch("codemie.service.workflow_execution.startup_recovery.Session")
def test_conditional_update_zero_rows_does_not_mutate_snapshot(mock_session):
    """A concurrent child or parent transition must leave the detached snapshot unchanged."""
    state = _make_state(WorkflowExecutionStatusEnum.IN_PROGRESS)
    cutoff = datetime.now()
    original_completed_at = state.completed_at
    session = mock_session.return_value.__enter__.return_value
    session.exec.return_value.first.return_value = MagicMock(overall_status=WorkflowExecutionStatusEnum.INTERRUPTED)
    session.execute.return_value = MagicMock(rowcount=0)

    assert (
        _mark_state_failed_if_still_stuck(
            state,
            cutoff,
            WorkflowExecutionStatusEnum.INTERRUPTED,
        )
        is False
    )
    assert state.status == WorkflowExecutionStatusEnum.IN_PROGRESS
    assert state.completed_at is original_completed_at


@patch("codemie.service.workflow_execution.startup_recovery.Session")
def test_changed_parent_status_skips_child_update(mock_session):
    """Recovery must stop after locking a parent that no longer matches its snapshot."""
    state = _make_state(WorkflowExecutionStatusEnum.IN_PROGRESS)
    session = mock_session.return_value.__enter__.return_value
    session.exec.return_value.first.return_value = MagicMock(overall_status=WorkflowExecutionStatusEnum.IN_PROGRESS)

    assert (
        _mark_state_failed_if_still_stuck(
            state,
            datetime.now(),
            WorkflowExecutionStatusEnum.INTERRUPTED,
        )
        is False
    )
    session.execute.assert_not_called()
    session.commit.assert_not_called()
