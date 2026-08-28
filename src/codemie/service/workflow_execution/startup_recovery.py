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

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, update
from sqlmodel import Session, select

from codemie.configs import logger
from codemie.core.workflow_models import WorkflowExecution, WorkflowExecutionState, WorkflowExecutionStatusEnum

_TERMINAL_STATUSES = (
    WorkflowExecutionStatusEnum.FAILED,
    WorkflowExecutionStatusEnum.SUCCEEDED,
    WorkflowExecutionStatusEnum.ABORTED,
    WorkflowExecutionStatusEnum.INTERRUPTED,
)
_TERMINAL_NON_INTERRUPTED = (
    WorkflowExecutionStatusEnum.FAILED,
    WorkflowExecutionStatusEnum.SUCCEEDED,
    WorkflowExecutionStatusEnum.ABORTED,
)
_STUCK_CHILD_STATUSES = (
    WorkflowExecutionStatusEnum.IN_PROGRESS,
    WorkflowExecutionStatusEnum.INTERRUPTED,
)


def _mark_state_failed_if_still_stuck(
    state: WorkflowExecutionState,
    started_before: datetime,
    expected_parent_status: WorkflowExecutionStatusEnum,
) -> bool:
    """Atomically fail a state only while it still matches the recovery snapshot."""
    completed_at = datetime.now()
    statement = (
        update(WorkflowExecutionState)
        .where(
            WorkflowExecutionState.id == state.id,
            WorkflowExecutionState.status == state.status,
            or_(
                WorkflowExecutionState.started_at.is_(None),
                WorkflowExecutionState.started_at <= started_before,
            ),
        )
        .values(
            status=WorkflowExecutionStatusEnum.FAILED,
            completed_at=completed_at,
        )
    )
    with Session(WorkflowExecutionState.get_engine()) as session:
        parent_statement = (
            select(WorkflowExecution).where(WorkflowExecution.execution_id == state.execution_id).with_for_update()
        )
        parent = session.exec(parent_statement).first()
        if not parent or parent.overall_status != expected_parent_status:
            return False

        result = session.execute(statement)
        session.commit()

    if result.rowcount != 1:
        return False

    state.status = WorkflowExecutionStatusEnum.FAILED
    state.completed_at = completed_at
    return True


def _recover_single_state(
    execution_id: str,
    state: WorkflowExecutionState,
    started_before: datetime,
    expected_parent_status: WorkflowExecutionStatusEnum,
    recover_interrupted: bool = False,
) -> int:
    is_stuck = state.status == WorkflowExecutionStatusEnum.IN_PROGRESS or (
        recover_interrupted and state.status == WorkflowExecutionStatusEnum.INTERRUPTED
    )
    if not is_stuck or (state.started_at and state.started_at > started_before):
        return 0
    try:
        return int(_mark_state_failed_if_still_stuck(state, started_before, expected_parent_status))
    except Exception as state_exc:
        logger.error(
            f"Startup recovery: failed to mark state as FAILED for execution "
            f"{execution_id}: {state_exc}. State will remain until next restart."
        )
        return 0


def _parent_for_execution(execution_id: str, cache: dict[str, WorkflowExecution | None]) -> WorkflowExecution | None:
    if execution_id in cache:
        return cache[execution_id]
    try:
        parents = WorkflowExecution.get_by_execution_id(execution_id)
    except Exception as exc:
        logger.error(
            f"Startup recovery: failed to query parent execution {execution_id}: {exc}. Skipping its stuck states."
        )
        cache[execution_id] = None
        return None
    parent = parents[0] if parents else None
    cache[execution_id] = parent
    return parent


def recover_orphaned_workflow_states(started_before: datetime | None = None) -> None:
    """Mark stuck step states as FAILED for executions in a terminal status.

    Starts from IN_PROGRESS and INTERRUPTED child rows (the small stuck set), then
    verifies each parent. For INTERRUPTED parent executions, only IN_PROGRESS children
    are recovered; INTERRUPTED children are left for resume_states(). For FAILED,
    SUCCEEDED, and ABORTED parents, both IN_PROGRESS and INTERRUPTED children are
    recovered because resume_states() never runs for those parents.
    Runs once at startup. Idempotent. Never raises — failures are logged per-item so one
    bad record does not block recovery of the rest.
    """
    started_before = started_before or datetime.now()
    recovered = 0
    parent_cache: dict[str, WorkflowExecution | None] = {}
    for child_status in _STUCK_CHILD_STATUSES:
        try:
            states = WorkflowExecutionState.get_all_by_fields(fields={"status": child_status})
        except Exception as exc:
            logger.error(
                f"Startup recovery: failed to query states with status {child_status}: {exc}. "
                "Skipping this status bucket."
            )
            continue
        for state in states:
            parent = _parent_for_execution(state.execution_id, parent_cache)
            if not parent or parent.overall_status not in _TERMINAL_STATUSES:
                continue
            recovered += _recover_single_state(
                parent.execution_id,
                state,
                started_before,
                parent.overall_status,
                parent.overall_status in _TERMINAL_NON_INTERRUPTED,
            )
    if recovered:
        logger.info(f"Startup recovery: marked {recovered} orphaned state(s) as FAILED.")
    else:
        logger.info("Startup recovery: no orphaned workflow states found.")
