# EPMCDME-11609 Increments 2b + 2c Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `GET /workflows/selectable` endpoint (2b) and fix `SubWorkflowNode.execute()` interrupt/resume wiring (2c).

**Architecture:** 2b adds a new `ExcludeSelfModifier` to `WorkflowConfigIndexService` and wires it in a new thin route; 2c fixes three bugs in `SubWorkflowNode.execute()`: setting `active_sub_execution_id` before stream, handling `INTERRUPTED` child status, and detecting the resume path at re-entry.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy, pytest-asyncio, httpx, LangGraph.

## Global Constraints

- All new behaviour guarded by `ENABLE_SUB_WORKFLOW_NODE` feature flag.
- No new DB migrations.
- Follow existing `QueryModifier` pattern in `WorkflowConfigIndexService`.
- Tests use `unittest.mock.patch` with full dotted module paths.
- Router tests use `httpx.AsyncClient` + `ASGITransport(app=app)` + `@pytest.mark.asyncio`.
- Commit message format: `feat(EPMCDME-11609): <description>`.
- Run `make ruff` before each commit.

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `src/codemie/service/workflow_config/workflow_config_index_service.py` | Modify | Add `ExcludeSelfModifier` class; add `extra_modifiers` param to `run()` |
| `src/codemie/rest_api/routers/workflow.py` | Modify | Add `GET /workflows/selectable` handler |
| `src/codemie/workflows/nodes/sub_workflow_node.py` | Modify | Three 2c sub-fixes in `execute()` |
| `tests/codemie/service/workflow_config/test_workflow_config_index_service.py` | Create | Unit tests for `ExcludeSelfModifier` |
| `tests/codemie/rest_api/routers/test_workflow_selectable.py` | Create | Async router tests for `GET /workflows/selectable` |
| `tests/codemie/workflows/nodes/test_sub_workflow_node.py` | Modify | Add 2c tests |

---

## Task 1: ExcludeSelfModifier + run() extension

**Files:**
- Modify: `src/codemie/service/workflow_config/workflow_config_index_service.py:75-116`
- Create: `tests/codemie/service/workflow_config/test_workflow_config_index_service.py`

**Interfaces:**
- Produces: `ExcludeSelfModifier(workflow_id: str)` class; `WorkflowConfigIndexService.run(..., extra_modifiers: list[QueryModifier] | None = None)` extended signature.

- [ ] **Step 1: Write the failing test for ExcludeSelfModifier**

Create `tests/codemie/service/workflow_config/test_workflow_config_index_service.py`:

```python
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

from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import select

from codemie.core.workflow_models import WorkflowConfig
from codemie.service.workflow_config.workflow_config_index_service import (
    ExcludeSelfModifier,
    WorkflowConfigIndexService,
    QueryModifier,
)


def test_exclude_self_modifier_adds_where_clause():
    """ExcludeSelfModifier appends WHERE id != <workflow_id>."""
    query = select(WorkflowConfig)
    modifier = ExcludeSelfModifier("wf-abc")
    new_query = modifier.modify_query(query)
    # The compiled SQL should contain the NOT EQUAL condition
    compiled = str(new_query.compile(compile_kwargs={"literal_binds": True}))
    assert "wf-abc" in compiled


def test_run_accepts_extra_modifiers():
    """WorkflowConfigIndexService.run() passes extra_modifiers to _query_postgres."""
    from codemie.rest_api.security.user import User
    user = User(id="u1", username="u1", name="u1")
    extra_modifier = MagicMock(spec=QueryModifier)
    extra_modifier.modify_query.side_effect = lambda q: q

    with patch.object(WorkflowConfigIndexService, "_query_postgres", return_value=([], 0)) as mock_qp:
        WorkflowConfigIndexService.run(
            user=user,
            filter_by_user=False,
            page=0,
            per_page=10,
            extra_modifiers=[extra_modifier],
        )
    # extra_modifier must have been included in the modifier list passed to _query_postgres
    called_modifiers = mock_qp.call_args.kwargs.get("query_modifiers") or mock_qp.call_args[1]["query_modifiers"]
    assert extra_modifier in called_modifiers
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie/service/workflow_config/test_workflow_config_index_service.py -v
```
Expected: `ImportError: cannot import name 'ExcludeSelfModifier'` (class does not exist yet).

- [ ] **Step 3: Implement ExcludeSelfModifier and extend run()**

In `src/codemie/service/workflow_config/workflow_config_index_service.py`, add the new class after `MarketplaceScopeModifier` (around line 88):

```python
class ExcludeSelfModifier(QueryModifier):
    """Exclude a specific workflow from results to prevent self-reference."""

    def __init__(self, workflow_id: str):
        self.workflow_id = workflow_id

    def modify_query(self, query: Select[_T]) -> Select[_T]:
        return query.where(WorkflowConfig.id != self.workflow_id)
```

Then in `WorkflowConfigIndexService.run()`, add `extra_modifiers` parameter and append it:

```python
@classmethod
def run(
    cls,
    user: User,
    filter_by_user: bool,
    page: int,
    per_page: int,
    filters: dict[str, Any] | None = None,
    minimal_response: bool = False,
    scope: WorkflowScope | None = None,
    extra_modifiers: list[QueryModifier] | None = None,   # ← new
) -> WorkflowListResponse:
    if scope == WorkflowScope.MARKETPLACE:
        query_modifiers: list[QueryModifier] = [
            ExcludeAutonomousWorkflowsModifier(),
            MarketplaceScopeModifier(),
        ]
    else:
        query_modifiers = [
            VisibleToUserModifierPostgres(user, filter_by_user),
            ExcludeAutonomousWorkflowsModifier(),
        ]

    if extra_modifiers:
        query_modifiers.extend(extra_modifiers)   # ← new

    items, total = cls._query_postgres(
        ...
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie/service/workflow_config/test_workflow_config_index_service.py -v
```
Expected: all PASS.

- [ ] **Step 5: Run ruff and commit**

```bash
make ruff
git add src/codemie/service/workflow_config/workflow_config_index_service.py \
        tests/codemie/service/workflow_config/test_workflow_config_index_service.py
git commit -m "feat(EPMCDME-11609): add ExcludeSelfModifier and extra_modifiers param to WorkflowConfigIndexService"
```

---

## Task 2: GET /workflows/selectable endpoint

**Files:**
- Modify: `src/codemie/rest_api/routers/workflow.py`
- Create: `tests/codemie/rest_api/routers/test_workflow_selectable.py`

**Interfaces:**
- Consumes: `ExcludeSelfModifier` from Task 1; `WorkflowConfigIndexService.run(..., extra_modifiers=...)` from Task 1.
- Produces: `GET /v1/workflows/selectable` → `WorkflowListResponse`.

- [ ] **Step 1: Write the failing tests**

Create `tests/codemie/rest_api/routers/test_workflow_selectable.py`:

```python
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

from unittest.mock import MagicMock, patch

import pytest
from fastapi import status
from httpx import AsyncClient, ASGITransport

from codemie.rest_api.main import app
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.workflow_config.workflow_config_index_service import ExcludeSelfModifier

user = User(id="u1", username="u1", name="u1")

REQUEST_HEADERS = {"user-id": user.id, "username": user.username, "name": user.name}


@pytest.fixture(autouse=True)
def override_auth():
    app.dependency_overrides[authenticate] = lambda: user
    yield
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_selectable_returns_403_when_flag_disabled():
    with patch("codemie.rest_api.routers.workflow.config") as mock_cfg:
        mock_cfg.ENABLE_SUB_WORKFLOW_NODE = False
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/workflows/selectable", headers=REQUEST_HEADERS)
    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_selectable_returns_workflows_without_exclude_id():
    mock_result = {
        "data": [{"id": "wf-1", "name": "My Workflow"}],
        "pagination": {"page": 0, "pages": 1, "total": 1, "per_page": 100},
    }
    with (
        patch("codemie.rest_api.routers.workflow.config") as mock_cfg,
        patch(
            "codemie.service.workflow_config.workflow_config_index_service.WorkflowConfigIndexService.run",
            return_value=mock_result,
        ) as mock_run,
    ):
        mock_cfg.ENABLE_SUB_WORKFLOW_NODE = True
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/workflows/selectable", headers=REQUEST_HEADERS)

    assert response.status_code == status.HTTP_200_OK
    call_kwargs = mock_run.call_args[1]
    assert call_kwargs["user"] == user
    assert call_kwargs["filter_by_user"] is False
    assert call_kwargs["minimal_response"] is True
    assert call_kwargs["extra_modifiers"] == []


@pytest.mark.asyncio
async def test_selectable_passes_exclude_self_modifier_when_exclude_id_given():
    mock_result = {
        "data": [],
        "pagination": {"page": 0, "pages": 0, "total": 0, "per_page": 100},
    }
    with (
        patch("codemie.rest_api.routers.workflow.config") as mock_cfg,
        patch(
            "codemie.service.workflow_config.workflow_config_index_service.WorkflowConfigIndexService.run",
            return_value=mock_result,
        ) as mock_run,
    ):
        mock_cfg.ENABLE_SUB_WORKFLOW_NODE = True
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(
                "/v1/workflows/selectable",
                params={"exclude_id": "wf-self"},
                headers=REQUEST_HEADERS,
            )

    assert response.status_code == status.HTTP_200_OK
    call_kwargs = mock_run.call_args[1]
    extra = call_kwargs["extra_modifiers"]
    assert len(extra) == 1
    assert isinstance(extra[0], ExcludeSelfModifier)
    assert extra[0].workflow_id == "wf-self"


@pytest.mark.asyncio
async def test_selectable_honours_pagination_params():
    mock_result = {
        "data": [],
        "pagination": {"page": 2, "pages": 5, "total": 50, "per_page": 10},
    }
    with (
        patch("codemie.rest_api.routers.workflow.config") as mock_cfg,
        patch(
            "codemie.service.workflow_config.workflow_config_index_service.WorkflowConfigIndexService.run",
            return_value=mock_result,
        ) as mock_run,
    ):
        mock_cfg.ENABLE_SUB_WORKFLOW_NODE = True
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get(
                "/v1/workflows/selectable",
                params={"page": 2, "per_page": 10},
                headers=REQUEST_HEADERS,
            )

    assert response.status_code == status.HTTP_200_OK
    call_kwargs = mock_run.call_args[1]
    assert call_kwargs["page"] == 2
    assert call_kwargs["per_page"] == 10
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_workflow_selectable.py -v
```
Expected: `404 Not Found` (endpoint does not exist yet).

- [ ] **Step 3: Implement the endpoint**

In `src/codemie/rest_api/routers/workflow.py`, add the following after `get_workflow_users` (around line 107). Also add `ExcludeSelfModifier` to the import:

Add import near line 52:
```python
from codemie.service.workflow_config.workflow_config_index_service import WorkflowScope, ExcludeSelfModifier
```

Add handler:
```python
@router.get(
    "/workflows/selectable",
    status_code=status.HTTP_200_OK,
    response_model=WorkflowListResponse,
    response_model_by_alias=True,
)
def get_selectable_workflows(
    user: User = Depends(authenticate),
    exclude_id: Optional[str] = None,
    page: int = 0,
    per_page: int = 100,
):
    if not config.ENABLE_SUB_WORKFLOW_NODE:
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Sub-workflow node is disabled",
            details="Set ENABLE_SUB_WORKFLOW_NODE=true to enable this endpoint.",
            help="Contact your administrator to enable the sub-workflow feature.",
        )

    extra_modifiers = [ExcludeSelfModifier(exclude_id)] if exclude_id else []

    return WorkflowConfigIndexService.run(
        user=user,
        filter_by_user=False,
        page=page,
        per_page=per_page,
        minimal_response=True,
        extra_modifiers=extra_modifiers,
    )
```

Note: `get_selectable_workflows` must be placed BEFORE the `get_workflow_by_id` route that uses the `{id}` path segment, otherwise FastAPI may route `/selectable` into the ID handler. Place it right after `get_workflow_users`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_workflow_selectable.py -v
```
Expected: all PASS.

- [ ] **Step 5: Run full workflow test suite to check for regressions**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_workflow.py -v
```
Expected: all PASS (no regressions from the new import).

- [ ] **Step 6: Run ruff and commit**

```bash
make ruff
git add src/codemie/rest_api/routers/workflow.py \
        tests/codemie/rest_api/routers/test_workflow_selectable.py
git commit -m "feat(EPMCDME-11609): add GET /workflows/selectable endpoint"
```

---

## Task 3: SubWorkflowNode interrupt/resume wiring (2c)

**Files:**
- Modify: `src/codemie/workflows/nodes/sub_workflow_node.py`
- Modify: `tests/codemie/workflows/nodes/test_sub_workflow_node.py`

**Interfaces:**
- Consumes: `WorkflowExecutionStatusEnum.INTERRUPTED` (already exists in `workflow_execution.py:34`); `InterruptedException(message: str, interrupted_state: str)` (already in `exceptions.py:66`); `WorkflowExecutor.create_executor(..., resume_execution=True, execution_id=...)`.
- Produces: fixed `SubWorkflowNode.execute()` with three corrections.

- [ ] **Step 1: Write the failing tests for all three sub-fixes**

Append to `tests/codemie/workflows/nodes/test_sub_workflow_node.py`:

```python
# ── T7: active_sub_execution_id set on parent before stream ──────────────────


def test_execute_sets_active_sub_execution_id_before_stream(node, state_schema):
    child_exec = MagicMock(execution_id="child-id")
    child_config = MagicMock(max_nesting_level=None)
    finished_child = MagicMock(
        overall_status=WorkflowExecutionStatusEnum.SUCCEEDED,
        output="done",
    )
    parent_exec = MagicMock()
    parent_exec.active_sub_execution_id = None

    set_calls = []

    def track_set(val):
        set_calls.append(val)

    type(parent_exec).active_sub_execution_id = property(
        lambda self: set_calls[-1] if set_calls else None,
        lambda self, v: set_calls.append(v),
    )

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.config") as mock_cfg,
        patch("codemie.workflows.nodes.sub_workflow_node.WorkflowService") as mock_svc,
        patch("codemie.workflows.workflow.WorkflowExecutor") as mock_executor,
    ):
        mock_cfg.ENABLE_SUB_WORKFLOW_NODE = True
        mock_cfg.WORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc_inst = mock_svc.return_value
        mock_svc_inst.get_workflow.return_value = child_config
        mock_svc.get_nesting_depth.return_value = 0
        mock_svc.create_workflow_execution.return_value = child_exec
        mock_svc.find_workflow_execution_by_id.side_effect = [parent_exec, finished_child, parent_exec]
        child_executor = MagicMock()
        mock_executor.create_executor.return_value = child_executor

        node.execute(state_schema, {})

    # active_sub_execution_id must be set to child id (one of the set_calls)
    assert "child-id" in set_calls


# ── T8: INTERRUPTED child → InterruptedException raised, id not cleared ──────


def test_execute_raises_interrupted_when_child_interrupted(node, state_schema):
    from codemie.core.exceptions import InterruptedException

    child_exec = MagicMock(execution_id="child-id")
    child_config = MagicMock(max_nesting_level=None)
    interrupted_child = MagicMock(
        overall_status=WorkflowExecutionStatusEnum.INTERRUPTED,
        output=None,
    )
    parent_exec = MagicMock()
    parent_exec.active_sub_execution_id = None

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.config") as mock_cfg,
        patch("codemie.workflows.nodes.sub_workflow_node.WorkflowService") as mock_svc,
        patch("codemie.workflows.workflow.WorkflowExecutor") as mock_executor,
    ):
        mock_cfg.ENABLE_SUB_WORKFLOW_NODE = True
        mock_cfg.WORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc_inst = mock_svc.return_value
        mock_svc_inst.get_workflow.return_value = child_config
        mock_svc.get_nesting_depth.return_value = 0
        mock_svc.create_workflow_execution.return_value = child_exec
        mock_svc.find_workflow_execution_by_id.side_effect = [parent_exec, interrupted_child]
        mock_executor.create_executor.return_value = MagicMock()

        with pytest.raises(InterruptedException):
            node.execute(state_schema, {})


def test_execute_does_not_clear_active_id_on_child_interrupt(node, state_schema):
    from codemie.core.exceptions import InterruptedException

    child_exec = MagicMock(execution_id="child-id")
    child_config = MagicMock(max_nesting_level=None)
    interrupted_child = MagicMock(
        overall_status=WorkflowExecutionStatusEnum.INTERRUPTED,
        output=None,
    )
    parent_exec = MagicMock()
    parent_exec.active_sub_execution_id = None

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.config") as mock_cfg,
        patch("codemie.workflows.nodes.sub_workflow_node.WorkflowService") as mock_svc,
        patch("codemie.workflows.workflow.WorkflowExecutor") as mock_executor,
    ):
        mock_cfg.ENABLE_SUB_WORKFLOW_NODE = True
        mock_cfg.WORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc_inst = mock_svc.return_value
        mock_svc_inst.get_workflow.return_value = child_config
        mock_svc.get_nesting_depth.return_value = 0
        mock_svc.create_workflow_execution.return_value = child_exec
        mock_svc.find_workflow_execution_by_id.side_effect = [parent_exec, interrupted_child]
        mock_executor.create_executor.return_value = MagicMock()

        with pytest.raises(InterruptedException):
            node.execute(state_schema, {})

    # parent_exec.save must NOT have been called (active_sub_execution_id not cleared)
    parent_exec.save.assert_not_called()


# ── T9: resume path — parent.active_sub_execution_id set → resume child ──────


def test_execute_resume_path_resumes_child_with_forwarded_input(node, state_schema):
    child_execution_id = "existing-child-id"
    child_exec_record = MagicMock(
        execution_id=child_execution_id,
        workflow_id="child-wf-id",
        overall_status=WorkflowExecutionStatusEnum.SUCCEEDED,
        output="resumed output",
    )
    parent_exec = MagicMock()
    parent_exec.active_sub_execution_id = child_execution_id
    child_config = MagicMock(max_nesting_level=None)
    finished_child = MagicMock(
        overall_status=WorkflowExecutionStatusEnum.SUCCEEDED,
        output="resumed output",
    )
    parent_exec_after = MagicMock()

    state_schema["context_store"] = {"key": "val"}

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.config") as mock_cfg,
        patch("codemie.workflows.nodes.sub_workflow_node.WorkflowService") as mock_svc,
        patch("codemie.workflows.workflow.WorkflowExecutor") as mock_executor,
    ):
        mock_cfg.ENABLE_SUB_WORKFLOW_NODE = True
        mock_cfg.WORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc_inst = mock_svc.return_value
        mock_svc_inst.get_workflow.return_value = child_config
        mock_svc.find_workflow_execution_by_id.side_effect = [
            parent_exec,        # initial parent fetch
            child_exec_record,  # fetch child by active_sub_execution_id
            finished_child,     # post-stream fetch of finished child
            parent_exec_after,  # fetch parent again to clear active_sub_execution_id
        ]
        child_executor = MagicMock()
        mock_executor.create_executor.return_value = child_executor

        result = node.execute(state_schema, {})

    # Must have used resume_execution=True
    create_kwargs = mock_executor.create_executor.call_args[1]
    assert create_kwargs.get("resume_execution") is True
    assert create_kwargs.get("execution_id") == child_execution_id
    # Must NOT have called create_workflow_execution (resume path skips child creation)
    mock_svc.create_workflow_execution.assert_not_called()
    assert result == "resumed output"


def test_execute_resume_path_clears_active_id_on_child_success(node, state_schema):
    child_execution_id = "existing-child-id"
    child_exec_record = MagicMock(
        execution_id=child_execution_id,
        workflow_id="child-wf-id",
    )
    parent_exec = MagicMock()
    parent_exec.active_sub_execution_id = child_execution_id
    child_config = MagicMock(max_nesting_level=None)
    finished_child = MagicMock(
        overall_status=WorkflowExecutionStatusEnum.SUCCEEDED,
        output="done",
    )
    parent_exec_after = MagicMock()

    with (
        patch("codemie.workflows.nodes.sub_workflow_node.config") as mock_cfg,
        patch("codemie.workflows.nodes.sub_workflow_node.WorkflowService") as mock_svc,
        patch("codemie.workflows.workflow.WorkflowExecutor") as mock_executor,
    ):
        mock_cfg.ENABLE_SUB_WORKFLOW_NODE = True
        mock_cfg.WORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc_inst = mock_svc.return_value
        mock_svc_inst.get_workflow.return_value = child_config
        mock_svc.find_workflow_execution_by_id.side_effect = [
            parent_exec,
            child_exec_record,
            finished_child,
            parent_exec_after,
        ]
        mock_executor.create_executor.return_value = MagicMock()

        node.execute(state_schema, {})

    assert parent_exec_after.active_sub_execution_id is None
    parent_exec_after.save.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie/workflows/nodes/test_sub_workflow_node.py::test_execute_sets_active_sub_execution_id_before_stream \
    tests/codemie/workflows/nodes/test_sub_workflow_node.py::test_execute_raises_interrupted_when_child_interrupted \
    tests/codemie/workflows/nodes/test_sub_workflow_node.py::test_execute_does_not_clear_active_id_on_child_interrupt \
    tests/codemie/workflows/nodes/test_sub_workflow_node.py::test_execute_resume_path_resumes_child_with_forwarded_input \
    tests/codemie/workflows/nodes/test_sub_workflow_node.py::test_execute_resume_path_clears_active_id_on_child_success \
    -v
```
Expected: 3–5 FAILs (tests fail because bugs are not fixed).

- [ ] **Step 3: Rewrite SubWorkflowNode.execute() with all three fixes**

Replace the body of `execute()` in `src/codemie/workflows/nodes/sub_workflow_node.py`:

```python
def execute(self, state_schema, execution_context: dict):
    if not config.ENABLE_SUB_WORKFLOW_NODE:
        raise FeatureDisabledError("Sub-workflow node is disabled. Set ENABLE_SUB_WORKFLOW_NODE=true to enable.")

    from codemie.core.exceptions import InterruptedException
    from codemie.core.thought_queue import ThoughtQueue

    user = self.workflow_execution_service.user

    parent_exec = WorkflowService.find_workflow_execution_by_id(self.execution_id)

    if parent_exec and parent_exec.active_sub_execution_id:
        # Resume path: child was interrupted; parent re-entered after checkpoint replay.
        child_execution = WorkflowService.find_workflow_execution_by_id(
            parent_exec.active_sub_execution_id
        )
        if not child_execution:
            raise ValueError(
                f"Sub-workflow execution {parent_exec.active_sub_execution_id} not found on resume"
            )
        child_config = WorkflowService().get_workflow(child_execution.workflow_id, user)
        if not child_config:
            raise ValueError(f"Sub-workflow {child_execution.workflow_id} not found on resume")

        resume_input = json.dumps(state_schema.get(CONTEXT_STORE_VARIABLE, {}))

        from codemie.workflows.workflow import WorkflowExecutor

        child_executor = WorkflowExecutor.create_executor(
            child_config,
            resume_input,
            user,
            execution_id=child_execution.execution_id,
            thought_queue=ThoughtQueue(),
            resume_execution=True,
        )
    else:
        # Create path: normal first-time execution.
        child_config = WorkflowService().get_workflow(self.sub_workflow_id, user)
        if not child_config:
            raise ValueError(f"Sub-workflow {self.sub_workflow_id} not found")

        effective_max_depth = child_config.max_nesting_level or config.WORKFLOW_MAX_NESTING_DEPTH
        current_depth = WorkflowService.get_nesting_depth(self.execution_id)
        if current_depth >= effective_max_depth:
            raise WorkflowNestingDepthExceededError(current_depth, effective_max_depth)

        child_input = self._render_input(state_schema)
        child_execution = WorkflowService.create_workflow_execution(
            child_config,
            user.as_user_model(),
            child_input,
            parent_execution_id=self.execution_id,
        )

        # Set reference BEFORE streaming so it survives an interrupt of this thread.
        if parent_exec:
            parent_exec.active_sub_execution_id = child_execution.execution_id
            parent_exec.save()

        from codemie.workflows.workflow import WorkflowExecutor

        child_executor = WorkflowExecutor.create_executor(
            child_config,
            child_input,
            user,
            execution_id=child_execution.execution_id,
            thought_queue=ThoughtQueue(),
        )

    child_executor.stream()

    finished_child = WorkflowService.find_workflow_execution_by_id(child_execution.execution_id)

    if finished_child.overall_status == WorkflowExecutionStatusEnum.INTERRUPTED:
        # Do NOT clear active_sub_execution_id — parent needs it when it is resumed.
        raise InterruptedException(
            message="Sub-workflow was interrupted",
            interrupted_state=self.node_name,
        )

    # Terminal: clear the reference on the parent row.
    parent_exec = WorkflowService.find_workflow_execution_by_id(self.execution_id)
    parent_exec.active_sub_execution_id = None
    parent_exec.save()

    if finished_child.overall_status == WorkflowExecutionStatusEnum.ABORTED:
        raise ExecutionAbortedException("Sub-workflow was aborted")

    if finished_child.overall_status == WorkflowExecutionStatusEnum.FAILED:
        raise SubWorkflowExecutionError(child_execution.execution_id)

    return finished_child.output or ""
```

Also add the missing imports at the top of `sub_workflow_node.py`:

```python
from codemie.core.workflow_models import WorkflowConfig, WorkflowExecutionStatusEnum, WorkflowState
```

(`WorkflowExecutionStatusEnum` needs to be added to this import — verify it isn't already there.)

- [ ] **Step 4: Run all SubWorkflowNode tests to verify they pass**

```bash
poetry run pytest tests/codemie/workflows/nodes/test_sub_workflow_node.py -v
```
Expected: all PASS (including the 6 existing tests from Increment 1 + 5 new ones).

- [ ] **Step 5: Run ruff and commit**

```bash
make ruff
git add src/codemie/workflows/nodes/sub_workflow_node.py \
        tests/codemie/workflows/nodes/test_sub_workflow_node.py
git commit -m "feat(EPMCDME-11609): fix SubWorkflowNode interrupt/resume wiring (set active id, handle INTERRUPTED, resume path)"
```

---

## Final validation

- [ ] **Run full affected test suite**

```bash
poetry run pytest \
    tests/codemie/service/workflow_config/test_workflow_config_index_service.py \
    tests/codemie/rest_api/routers/test_workflow_selectable.py \
    tests/codemie/rest_api/routers/test_workflow.py \
    tests/codemie/workflows/nodes/test_sub_workflow_node.py \
    tests/codemie/workflows/test_config_resources_validation.py \
    -v
```
Expected: all PASS.

- [ ] **Run ruff on all changed files**

```bash
make ruff
```
Expected: no errors.
