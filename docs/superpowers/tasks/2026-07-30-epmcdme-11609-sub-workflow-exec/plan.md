# EPMCDME-11609 Increment 2a — SubWorkflowNode Execution Logic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the sub-workflow execution path so that a LangGraph node with `workflow_id` set synchronously runs a child `WorkflowExecution` and maps its output back to the parent's `context_store`.

**Architecture:** A new `SubWorkflowNode(BaseNode)` handles the execution lifecycle: it creates a child `WorkflowExecution` (with lineage fields), runs a child `WorkflowExecutor` with an isolated `ThoughtQueue`, reads the child's output from the DB after completion, and returns it for the base class to write to the `output_key`. Feature flag `ENABLE_SUB_WORKFLOW_NODE` gates all execution paths. Nesting depth is enforced in `WorkflowService` before the child is created.

**Tech Stack:** Python 3.12, SQLModel, LangGraph `StateGraph`, Jinja2 (template rendering), pytest + `unittest.mock`, Poetry.

## Global Constraints

- Run all tests with `poetry run python -m pytest <path> -v` from repo root.
- Commit messages: `feat(EPMCDME-11609): <description>` or `test(EPMCDME-11609): <description>`.
- Never skip linting — run `make ruff` (or `poetry run ruff check src/`) after each task that touches production code.
- `ENABLE_SUB_WORKFLOW_NODE` defaults to `False` everywhere — nothing in this increment enables it in production.
- All new production imports must come from within the existing `codemie` package; no new third-party packages beyond `jinja2` (already a transitive dep).

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `src/codemie/core/exceptions.py` | Modify | Add `FeatureDisabledError`, `WorkflowNestingDepthExceededError`, `SubWorkflowExecutionError` |
| `src/codemie/service/workflow_service.py` | Modify | Add `get_nesting_depth()`, extend `create_workflow_execution()` with `parent_execution_id` |
| `src/codemie/workflows/nodes/sub_workflow_node.py` | Create | `SubWorkflowNode(BaseNode)` — full execution lifecycle |
| `src/codemie/workflows/nodes/__init__.py` | Modify | Export `SubWorkflowNode` |
| `src/codemie/workflows/workflow.py` | Modify | Add `elif state.workflow_id:` branch + `init_sub_workflow_node()` factory |
| `src/codemie/workflows/validation/resources.py` | Modify | Flag guard at top of `_validate_sub_workflow_availability()` |
| `deploy-templates/values.yaml` | Modify | Add 4 new env vars |
| `tests/codemie/service/test_workflow_service.py` | Modify | Tests for `get_nesting_depth` and lineage extension |
| `tests/codemie/workflows/nodes/test_sub_workflow_node.py` | Create | 6 tests for `SubWorkflowNode.execute()` |
| `tests/codemie/workflows/test_config_resources_validation.py` | Modify | Test for flag guard in `_validate_sub_workflow_availability` |

---

### Task 1: Exception types

**Files:**
- Modify: `src/codemie/core/exceptions.py`

**Interfaces:**
- Produces: `FeatureDisabledError(message: str)`, `WorkflowNestingDepthExceededError(current_depth: int, max_depth: int)`, `SubWorkflowExecutionError(execution_id: str, message: str)`

- [ ] **Step 1: Append three exception classes to `exceptions.py`**

Add at the end of `src/codemie/core/exceptions.py`:

```python
class FeatureDisabledError(Exception):
    def __init__(self, message: str = "Feature is disabled"):
        self.message = message
        super().__init__(message)


class WorkflowNestingDepthExceededError(Exception):
    def __init__(self, current_depth: int, max_depth: int):
        self.current_depth = current_depth
        self.max_depth = max_depth
        super().__init__(f"Nesting depth {current_depth} exceeds maximum allowed depth {max_depth}")


class SubWorkflowExecutionError(Exception):
    def __init__(self, execution_id: str, message: str = "Sub-workflow execution failed"):
        self.execution_id = execution_id
        self.message = message
        super().__init__(f"Sub-workflow {execution_id}: {message}")
```

- [ ] **Step 2: Verify import works**

```bash
poetry run python -c "from codemie.core.exceptions import FeatureDisabledError, WorkflowNestingDepthExceededError, SubWorkflowExecutionError; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/codemie/core/exceptions.py
git commit -m "feat(EPMCDME-11609): add FeatureDisabledError, WorkflowNestingDepthExceededError, SubWorkflowExecutionError"
```

---

### Task 2: WorkflowService.get_nesting_depth()

**Files:**
- Modify: `src/codemie/service/workflow_service.py`
- Test: `tests/codemie/service/test_workflow_service.py`

**Interfaces:**
- Consumes: `WorkflowService.find_workflow_execution_by_id(execution_id: str) -> WorkflowExecution`; `config.WORKFLOW_MAX_NESTING_DEPTH: int`
- Produces: `WorkflowService.get_nesting_depth(execution_id: str) -> int` — returns 0 for top-level, 1 for direct child, etc.; capped at `WORKFLOW_MAX_NESTING_DEPTH + 1`

- [ ] **Step 1: Write the failing tests**

In `tests/codemie/service/test_workflow_service.py`, add a new test class `TestGetNestingDepth`:

```python
class TestGetNestingDepth:
    def test_top_level_returns_zero(self):
        top = MagicMock()
        top.parent_execution_id = None
        with patch.object(WorkflowService, 'find_workflow_execution_by_id', return_value=top):
            assert WorkflowService.get_nesting_depth("exec-0") == 0

    def test_one_parent_returns_one(self):
        child = MagicMock(parent_execution_id="parent-id")
        parent = MagicMock(parent_execution_id=None)

        def _find(eid):
            return child if eid == "child-id" else parent

        with patch.object(WorkflowService, 'find_workflow_execution_by_id', side_effect=_find):
            assert WorkflowService.get_nesting_depth("child-id") == 1

    def test_depth_capped_at_max_plus_one(self):
        always_has_parent = MagicMock(parent_execution_id="same")
        with patch.object(WorkflowService, 'find_workflow_execution_by_id', return_value=always_has_parent):
            with patch('codemie.service.workflow_service.config') as mock_cfg:
                mock_cfg.WORKFLOW_MAX_NESTING_DEPTH = 2
                result = WorkflowService.get_nesting_depth("any")
        assert result == 3  # capped at max + 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run python -m pytest tests/codemie/service/test_workflow_service.py::TestGetNestingDepth -v
```

Expected: `AttributeError: type object 'WorkflowService' has no attribute 'get_nesting_depth'`

- [ ] **Step 3: Implement get_nesting_depth**

Add after `find_workflow_execution_by_id` in `workflow_service.py`:

```python
@classmethod
def get_nesting_depth(cls, execution_id: str) -> int:
    depth = 0
    cap = config.WORKFLOW_MAX_NESTING_DEPTH + 1
    current_id = execution_id
    while depth <= cap:
        execution = cls.find_workflow_execution_by_id(current_id)
        if not execution or not execution.parent_execution_id:
            return depth
        current_id = execution.parent_execution_id
        depth += 1
    return depth
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run python -m pytest tests/codemie/service/test_workflow_service.py::TestGetNestingDepth -v
```

Expected: 3 PASSED

- [ ] **Step 5: Lint**

```bash
poetry run ruff check src/codemie/service/workflow_service.py
```

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/workflow_service.py tests/codemie/service/test_workflow_service.py
git commit -m "feat(EPMCDME-11609): add WorkflowService.get_nesting_depth()"
```

---

### Task 3: create_workflow_execution lineage extension

**Files:**
- Modify: `src/codemie/service/workflow_service.py`
- Test: `tests/codemie/service/test_workflow_service.py`

**Interfaces:**
- Consumes: `WorkflowService.find_workflow_execution_by_id`; `WorkflowExecution` model with `parent_execution_id` and `active_sub_execution_id` fields (both from Increment 1)
- Produces: `WorkflowService.create_workflow_execution(..., parent_execution_id: Optional[str] = None)` — when `parent_execution_id` is set, writes it to the child row and sets `active_sub_execution_id` on the parent row

- [ ] **Step 1: Write the failing test**

In `tests/codemie/service/test_workflow_service.py`, add to class `TestCreateWorkflowExecution` (or as a standalone function if the class doesn't exist):

```python
def test_create_workflow_execution_sets_lineage_when_parent_given(mock_workflow_config, mock_user):
    parent_exec = MagicMock()
    parent_exec.active_sub_execution_id = None

    with patch('codemie.service.workflow_service.WorkflowExecution') as MockExec, \
         patch.object(WorkflowService, 'find_workflow_execution_by_id', return_value=parent_exec), \
         patch('codemie.service.workflow_service.uuid') as mock_uuid, \
         patch.object(WorkflowService, '_augment_user_input_with_history', return_value="input"):
        mock_uuid.uuid4.return_value = "child-id"
        mock_instance = MagicMock()
        mock_instance.execution_id = "child-id"
        MockExec.return_value = mock_instance

        WorkflowService.create_workflow_execution(
            mock_workflow_config,
            mock_user,
            "task input",
            parent_execution_id="parent-exec-id",
        )

    # Child row must have parent_execution_id set
    call_kwargs = MockExec.call_args[1]
    assert call_kwargs.get('parent_execution_id') == "parent-exec-id"

    # Parent row must have active_sub_execution_id set to child id
    assert parent_exec.active_sub_execution_id == "child-id"
    parent_exec.save.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run python -m pytest tests/codemie/service/test_workflow_service.py::test_create_workflow_execution_sets_lineage_when_parent_given -v
```

Expected: `TypeError` (unexpected keyword argument) or `AssertionError`

- [ ] **Step 3: Extend create_workflow_execution**

In `workflow_service.py`, change the `@staticmethod` signature:

```python
@staticmethod
def create_workflow_execution(
    workflow_config: WorkflowConfig,
    user: UserEntity,
    user_input: Optional[str] = '',
    file_names: Optional[list[str]] = None,
    conversation_id: Optional[str] = None,
    parent_execution_id: Optional[str] = None,
) -> WorkflowExecution:
```

In the `WorkflowExecution(...)` constructor call, add `parent_execution_id=parent_execution_id`:

```python
execution_config = WorkflowExecution(
    workflow_id=workflow_config.id,
    execution_id=execution_id,
    overall_status=WorkflowExecutionStatusEnum.IN_PROGRESS,
    created_by=user,
    history=execution_history,
    project=workflow_config.project,
    prompt=augmented_input,
    file_names=file_names,
    conversation_id=conversation_id if is_chat_execution else None,
    parent_execution_id=parent_execution_id,
)
execution_config.save(refresh=True)
```

After `execution_config.save(refresh=True)`, add:

```python
if parent_execution_id:
    parent_exec = WorkflowService.find_workflow_execution_by_id(parent_execution_id)
    if parent_exec:
        parent_exec.active_sub_execution_id = execution_id
        parent_exec.save()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
poetry run python -m pytest tests/codemie/service/test_workflow_service.py::test_create_workflow_execution_sets_lineage_when_parent_given -v
```

Expected: PASSED

- [ ] **Step 5: Run full workflow_service tests to check no regressions**

```bash
poetry run python -m pytest tests/codemie/service/test_workflow_service.py -v
```

Expected: all pass

- [ ] **Step 6: Lint**

```bash
poetry run ruff check src/codemie/service/workflow_service.py
```

- [ ] **Step 7: Commit**

```bash
git add src/codemie/service/workflow_service.py tests/codemie/service/test_workflow_service.py
git commit -m "feat(EPMCDME-11609): extend create_workflow_execution with parent_execution_id lineage"
```

---

### Task 4: SubWorkflowNode

**Files:**
- Create: `src/codemie/workflows/nodes/sub_workflow_node.py`
- Modify: `src/codemie/workflows/nodes/__init__.py`
- Create: `tests/codemie/workflows/nodes/test_sub_workflow_node.py`

**Interfaces:**
- Consumes: `BaseNode`, `WorkflowService.get_nesting_depth`, `WorkflowService.create_workflow_execution`, `WorkflowService.find_workflow_execution_by_id`, `WorkflowExecutor.create_executor`, `FeatureDisabledError`, `WorkflowNestingDepthExceededError`, `SubWorkflowExecutionError`, `WorkflowExecutionStatusEnum`, `CONTEXT_STORE_VARIABLE`, `config.ENABLE_SUB_WORKFLOW_NODE`, `config.WORKFLOW_MAX_NESTING_DEPTH`
- Produces: `SubWorkflowNode(BaseNode)` in `codemie.workflows.nodes.sub_workflow_node`; exported from `codemie.workflows.nodes`

- [ ] **Step 1: Write the test file**

Create `tests/codemie/workflows/nodes/test_sub_workflow_node.py`:

```python
import json
import pytest
from unittest.mock import MagicMock, patch, call

from codemie.core.exceptions import (
    FeatureDisabledError,
    WorkflowNestingDepthExceededError,
    SubWorkflowExecutionError,
)
from codemie.core.workflow_models import WorkflowExecutionStatusEnum
from codemie.workflows.constants import CONTEXT_STORE_VARIABLE
from codemie.workflows.nodes.sub_workflow_node import SubWorkflowNode


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def workflow_state():
    state = MagicMock()
    state.workflow_id = "child-wf-id"
    state.input_mapping = None
    state.next = MagicMock()
    state.next.output_key = "result"
    return state


@pytest.fixture
def workflow_execution_service():
    svc = MagicMock()
    svc.user = MagicMock()
    svc.workflow_execution = MagicMock()
    return svc


@pytest.fixture
def node(workflow_state, workflow_execution_service):
    return SubWorkflowNode(
        callbacks=[],
        workflow_execution_service=workflow_execution_service,
        thought_queue=MagicMock(),
        workflow_state=workflow_state,
        node_name="sub_wf",
        execution_id="parent-exec-id",
        workflow_config=MagicMock(),
    )


@pytest.fixture
def state_schema():
    return {CONTEXT_STORE_VARIABLE: {"key": "value"}, "messages": []}


# ── T1: flag disabled ─────────────────────────────────────────────────────────

def test_execute_raises_when_flag_disabled(node, state_schema):
    with patch('codemie.workflows.nodes.sub_workflow_node.config') as mock_cfg:
        mock_cfg.ENABLE_SUB_WORKFLOW_NODE = False
        with pytest.raises(FeatureDisabledError):
            node.execute(state_schema, {})


# ── T2: nesting depth exceeded ────────────────────────────────────────────────

def test_execute_raises_when_depth_exceeded(node, state_schema):
    child_config = MagicMock()
    child_config.max_nesting_level = 1

    with patch('codemie.workflows.nodes.sub_workflow_node.config') as mock_cfg, \
         patch('codemie.workflows.nodes.sub_workflow_node.WorkflowService') as MockSvc:
        mock_cfg.ENABLE_SUB_WORKFLOW_NODE = True
        mock_cfg.WORKFLOW_MAX_NESTING_DEPTH = 1
        mock_svc_inst = MockSvc.return_value
        mock_svc_inst.get_workflow.return_value = child_config
        MockSvc.get_nesting_depth.return_value = 1  # at the limit

        with pytest.raises(WorkflowNestingDepthExceededError):
            node.execute(state_schema, {})


# ── T3: input_mapping renders Jinja against context_store ─────────────────────

def test_execute_renders_input_mapping(node, workflow_state, state_schema):
    workflow_state.input_mapping = {"task": "Hello {{ key }}"}
    state_schema[CONTEXT_STORE_VARIABLE] = {"key": "world"}

    child_exec = MagicMock(execution_id="child-exec-id")
    child_config = MagicMock(max_nesting_level=None)
    finished_child = MagicMock(
        overall_status=WorkflowExecutionStatusEnum.SUCCEEDED,
        output="child result",
    )
    parent_exec = MagicMock()

    captured_input = {}

    def capture_create(*args, **kwargs):
        captured_input['user_input'] = args[2] if len(args) > 2 else kwargs.get('user_input')
        return child_exec

    with patch('codemie.workflows.nodes.sub_workflow_node.config') as mock_cfg, \
         patch('codemie.workflows.nodes.sub_workflow_node.WorkflowService') as MockSvc, \
         patch('codemie.workflows.nodes.sub_workflow_node.WorkflowExecutor') as MockExecutor:
        mock_cfg.ENABLE_SUB_WORKFLOW_NODE = True
        mock_cfg.WORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc_inst = MockSvc.return_value
        mock_svc_inst.get_workflow.return_value = child_config
        MockSvc.get_nesting_depth.return_value = 0
        MockSvc.create_workflow_execution.side_effect = capture_create
        MockSvc.find_workflow_execution_by_id.side_effect = [finished_child, parent_exec]
        MockExecutor.create_executor.return_value = MagicMock()

        node.execute(state_schema, {})

    rendered = json.loads(captured_input['user_input'])
    assert rendered == {"task": "Hello world"}


# ── T4: no input_mapping → full context_store passed as JSON ──────────────────

def test_execute_passes_full_context_when_no_mapping(node, state_schema):
    state_schema[CONTEXT_STORE_VARIABLE] = {"a": 1, "b": "two"}

    child_exec = MagicMock(execution_id="child-id")
    child_config = MagicMock(max_nesting_level=None)
    finished_child = MagicMock(
        overall_status=WorkflowExecutionStatusEnum.SUCCEEDED,
        output="out",
    )
    parent_exec = MagicMock()

    captured_input = {}

    def capture_create(*args, **kwargs):
        captured_input['user_input'] = args[2] if len(args) > 2 else kwargs.get('user_input')
        return child_exec

    with patch('codemie.workflows.nodes.sub_workflow_node.config') as mock_cfg, \
         patch('codemie.workflows.nodes.sub_workflow_node.WorkflowService') as MockSvc, \
         patch('codemie.workflows.nodes.sub_workflow_node.WorkflowExecutor') as MockExecutor:
        mock_cfg.ENABLE_SUB_WORKFLOW_NODE = True
        mock_cfg.WORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc_inst = MockSvc.return_value
        mock_svc_inst.get_workflow.return_value = child_config
        MockSvc.get_nesting_depth.return_value = 0
        MockSvc.create_workflow_execution.side_effect = capture_create
        MockSvc.find_workflow_execution_by_id.side_effect = [finished_child, parent_exec]
        MockExecutor.create_executor.return_value = MagicMock()

        node.execute(state_schema, {})

    assert json.loads(captured_input['user_input']) == {"a": 1, "b": "two"}


# ── T5: happy path ────────────────────────────────────────────────────────────

def test_execute_happy_path_clears_active_sub_execution_id(node, state_schema):
    child_exec = MagicMock(execution_id="child-id")
    child_config = MagicMock(max_nesting_level=None)
    finished_child = MagicMock(
        overall_status=WorkflowExecutionStatusEnum.SUCCEEDED,
        output="child output",
    )
    parent_exec = MagicMock()

    with patch('codemie.workflows.nodes.sub_workflow_node.config') as mock_cfg, \
         patch('codemie.workflows.nodes.sub_workflow_node.WorkflowService') as MockSvc, \
         patch('codemie.workflows.nodes.sub_workflow_node.WorkflowExecutor') as MockExecutor:
        mock_cfg.ENABLE_SUB_WORKFLOW_NODE = True
        mock_cfg.WORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc_inst = MockSvc.return_value
        mock_svc_inst.get_workflow.return_value = child_config
        MockSvc.get_nesting_depth.return_value = 0
        MockSvc.create_workflow_execution.return_value = child_exec
        MockSvc.find_workflow_execution_by_id.side_effect = [finished_child, parent_exec]
        child_executor = MagicMock()
        MockExecutor.create_executor.return_value = child_executor

        result = node.execute(state_schema, {})

    child_executor.stream.assert_called_once()
    assert parent_exec.active_sub_execution_id is None
    parent_exec.save.assert_called_once()
    assert result == "child output"


# ── T6: child fails → SubWorkflowExecutionError ───────────────────────────────

def test_execute_raises_when_child_fails(node, state_schema):
    child_exec = MagicMock(execution_id="child-id")
    child_config = MagicMock(max_nesting_level=None)
    failed_child = MagicMock(
        overall_status=WorkflowExecutionStatusEnum.FAILED,
        output="error message",
    )
    parent_exec = MagicMock()

    with patch('codemie.workflows.nodes.sub_workflow_node.config') as mock_cfg, \
         patch('codemie.workflows.nodes.sub_workflow_node.WorkflowService') as MockSvc, \
         patch('codemie.workflows.nodes.sub_workflow_node.WorkflowExecutor') as MockExecutor:
        mock_cfg.ENABLE_SUB_WORKFLOW_NODE = True
        mock_cfg.WORKFLOW_MAX_NESTING_DEPTH = 3
        mock_svc_inst = MockSvc.return_value
        mock_svc_inst.get_workflow.return_value = child_config
        MockSvc.get_nesting_depth.return_value = 0
        MockSvc.create_workflow_execution.return_value = child_exec
        MockSvc.find_workflow_execution_by_id.side_effect = [failed_child, parent_exec]
        MockExecutor.create_executor.return_value = MagicMock()

        with pytest.raises(SubWorkflowExecutionError):
            node.execute(state_schema, {})

    # active_sub_execution_id must still be cleared on failure
    assert parent_exec.active_sub_execution_id is None
    parent_exec.save.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run python -m pytest tests/codemie/workflows/nodes/test_sub_workflow_node.py -v
```

Expected: `ModuleNotFoundError: No module named 'codemie.workflows.nodes.sub_workflow_node'`

- [ ] **Step 3: Create sub_workflow_node.py**

Create `src/codemie/workflows/nodes/sub_workflow_node.py`:

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

import json
from typing import Optional

import jinja2

from codemie.configs import config
from codemie.core.exceptions import (
    FeatureDisabledError,
    SubWorkflowExecutionError,
    WorkflowNestingDepthExceededError,
)
from codemie.core.thought_queue import ThoughtQueue
from codemie.core.workflow_models import WorkflowConfig, WorkflowExecutionStatusEnum, WorkflowState
from codemie.core.workflow_models.workflow_execution import ExecutionAbortedException
from codemie.service.workflow_execution import WorkflowExecutionService
from codemie.service.workflow_service import WorkflowService
from codemie.workflows.callbacks.base_callback import BaseCallback
from codemie.workflows.constants import CONTEXT_STORE_VARIABLE
from codemie.workflows.models import AgentMessages
from codemie.workflows.nodes.base_node import BaseNode


class SubWorkflowNode(BaseNode):
    def __init__(
        self,
        callbacks: list[BaseCallback],
        workflow_execution_service: WorkflowExecutionService,
        thought_queue: ThoughtQueue,
        workflow_state: WorkflowState,
        node_name: Optional[str] = "",
        execution_id: Optional[str] = None,
        workflow_config: Optional[WorkflowConfig] = None,
        *args,
        **kwargs,
    ):
        super().__init__(
            callbacks,
            workflow_execution_service,
            thought_queue,
            node_name,
            execution_id,
            workflow_state,
            workflow_config,
            *args,
            **kwargs,
        )
        self.sub_workflow_id: str = workflow_state.workflow_id
        self.input_mapping: Optional[dict] = workflow_state.input_mapping

    def get_task(self, state_schema: AgentMessages, *args, **kwargs) -> str:
        return f"Executing sub-workflow {self.sub_workflow_id}"

    def execute(self, state_schema: AgentMessages, execution_context: dict):
        if not config.ENABLE_SUB_WORKFLOW_NODE:
            raise FeatureDisabledError("Sub-workflow node is disabled. Set ENABLE_SUB_WORKFLOW_NODE=true to enable.")

        user = self.workflow_execution_service.user

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

        from codemie.workflows.workflow import WorkflowExecutor

        child_thought_queue = ThoughtQueue()
        child_executor = WorkflowExecutor.create_executor(
            child_config,
            child_input,
            user,
            execution_id=child_execution.execution_id,
            thought_queue=child_thought_queue,
        )

        child_executor.stream()

        finished_child = WorkflowService.find_workflow_execution_by_id(child_execution.execution_id)

        parent_exec = WorkflowService.find_workflow_execution_by_id(self.execution_id)
        parent_exec.active_sub_execution_id = None
        parent_exec.save()

        if finished_child.overall_status == WorkflowExecutionStatusEnum.ABORTED:
            raise ExecutionAbortedException("Sub-workflow was aborted")

        if finished_child.overall_status == WorkflowExecutionStatusEnum.FAILED:
            raise SubWorkflowExecutionError(child_execution.execution_id)

        return finished_child.output or ""

    def post_process_output(self, state_schema, task, output) -> str:
        return str(output)

    def _render_input(self, state_schema: AgentMessages) -> str:
        context_store: dict = state_schema.get(CONTEXT_STORE_VARIABLE, {})
        if self.input_mapping:
            rendered = {
                key: jinja2.Template(template).render(**context_store)
                for key, template in self.input_mapping.items()
            }
            return json.dumps(rendered)
        return json.dumps(context_store)
```

- [ ] **Step 4: Export from nodes/__init__.py**

In `src/codemie/workflows/nodes/__init__.py`, add the import and export:

```python
from .sub_workflow_node import SubWorkflowNode
```

And add `'SubWorkflowNode'` to `__all__`.

- [ ] **Step 5: Run tests to verify they pass**

```bash
poetry run python -m pytest tests/codemie/workflows/nodes/test_sub_workflow_node.py -v
```

Expected: 6 PASSED

- [ ] **Step 6: Lint**

```bash
poetry run ruff check src/codemie/workflows/nodes/sub_workflow_node.py src/codemie/workflows/nodes/__init__.py
```

- [ ] **Step 7: Commit**

```bash
git add src/codemie/workflows/nodes/sub_workflow_node.py src/codemie/workflows/nodes/__init__.py tests/codemie/workflows/nodes/test_sub_workflow_node.py
git commit -m "feat(EPMCDME-11609): implement SubWorkflowNode with synchronous block execution"
```

---

### Task 5: workflow.py dispatch branch

**Files:**
- Modify: `src/codemie/workflows/workflow.py`
- Test: `tests/codemie/workflows/test_workflow_state_transitions.py` (or a new test class in an appropriate file)

**Interfaces:**
- Consumes: `SubWorkflowNode` (from `codemie.workflows.nodes`), `config.ENABLE_SUB_WORKFLOW_NODE`, `FeatureDisabledError`
- Produces: `WorkflowExecutor.init_sub_workflow_node(state: WorkflowState) -> SubWorkflowNode`; `WorkflowExecutor.initialize_node` dispatches `state.workflow_id` states

- [ ] **Step 1: Write the failing tests**

In `tests/codemie/workflows/test_workflow_state_transitions.py` (or a new file `tests/codemie/workflows/test_workflow_executor_dispatch.py`), add:

```python
class TestSubWorkflowDispatch:
    def _make_executor(self):
        """Create a minimal WorkflowExecutor for testing dispatch."""
        from codemie.workflows.workflow import WorkflowExecutor
        executor = object.__new__(WorkflowExecutor)
        executor.callbacks = []
        executor.workflow_execution_service = MagicMock()
        executor.thought_queue = MagicMock()
        executor.workflow_config = MagicMock()
        executor.user = MagicMock()
        executor.execution_id = "test-exec-id"
        executor.user_input = "test"
        executor.resume_execution = False
        executor.request_headers = {}
        executor.file_names = []
        return executor

    def test_initialize_node_dispatches_workflow_id_when_flag_enabled(self):
        from codemie.workflows.workflow import WorkflowExecutor
        from codemie.workflows.nodes.sub_workflow_node import SubWorkflowNode

        state = MagicMock()
        state.assistant_id = None
        state.custom_node_id = None
        state.tool_id = None
        state.workflow_id = "child-wf-id"
        state.id = "sub_wf_state"
        state.input_mapping = None

        executor = self._make_executor()
        mock_workflow = MagicMock()

        with patch('codemie.workflows.workflow.config') as mock_cfg:
            mock_cfg.ENABLE_SUB_WORKFLOW_NODE = True
            executor.initialize_node(state, mock_workflow, executor.workflow_config, [], set())

        mock_workflow.add_node.assert_called_once()
        added_node = mock_workflow.add_node.call_args[0][1]
        assert isinstance(added_node, SubWorkflowNode)

    def test_initialize_node_raises_when_flag_disabled(self):
        from codemie.workflows.workflow import WorkflowExecutor
        from codemie.core.exceptions import FeatureDisabledError

        state = MagicMock()
        state.assistant_id = None
        state.custom_node_id = None
        state.tool_id = None
        state.workflow_id = "child-wf-id"
        state.id = "sub_wf_state"

        executor = self._make_executor()

        with patch('codemie.workflows.workflow.config') as mock_cfg:
            mock_cfg.ENABLE_SUB_WORKFLOW_NODE = False
            with pytest.raises(FeatureDisabledError):
                executor.initialize_node(state, MagicMock(), executor.workflow_config, [], set())
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run python -m pytest tests/codemie/workflows/test_workflow_state_transitions.py::TestSubWorkflowDispatch -v
```

Expected: `ValueError: Invalid state configuration` (falls through to bare raise)

- [ ] **Step 3: Add elif branch and init_sub_workflow_node to workflow.py**

In `initialize_node()` in `workflow.py`, replace the bare `else: raise ValueError(...)` with:

```python
elif state.workflow_id:
    if not config.ENABLE_SUB_WORKFLOW_NODE:
        raise FeatureDisabledError(
            f"Sub-workflow node '{state.id}' is disabled. Set ENABLE_SUB_WORKFLOW_NODE=true to enable."
        )
    node = self.init_sub_workflow_node(state)
    workflow.add_node(state.id, node, retry=retry_policy, defer=is_convergence_node)
else:
    raise ValueError(f"Invalid state configuration. {state}")
```

Add the import at the top of `workflow.py` (with the other exception imports):

```python
from codemie.core.exceptions import FeatureDisabledError
```

Add `init_sub_workflow_node` method to `WorkflowExecutor` (near the other `init_*` methods):

```python
def init_sub_workflow_node(self, state: WorkflowState):
    from codemie.workflows.nodes.sub_workflow_node import SubWorkflowNode
    return SubWorkflowNode(
        callbacks=self.callbacks,
        workflow_execution_service=self.workflow_execution_service,
        thought_queue=self.thought_queue,
        workflow_state=state,
        node_name=state.id,
        execution_id=self.execution_id,
        workflow_config=self.workflow_config,
        user=self.user,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run python -m pytest tests/codemie/workflows/test_workflow_state_transitions.py::TestSubWorkflowDispatch -v
```

Expected: 2 PASSED

- [ ] **Step 5: Run broader workflow tests for regressions**

```bash
poetry run python -m pytest tests/codemie/workflows/ -v
```

Expected: all pass (or pre-existing failures only)

- [ ] **Step 6: Lint**

```bash
poetry run ruff check src/codemie/workflows/workflow.py
```

- [ ] **Step 7: Commit**

```bash
git add src/codemie/workflows/workflow.py tests/codemie/workflows/test_workflow_state_transitions.py
git commit -m "feat(EPMCDME-11609): add workflow_id dispatch branch and init_sub_workflow_node to WorkflowExecutor"
```

---

### Task 6: Validation flag guard

**Files:**
- Modify: `src/codemie/workflows/validation/resources.py`
- Test: `tests/codemie/workflows/test_config_resources_validation.py`

**Interfaces:**
- Consumes: `config.ENABLE_SUB_WORKFLOW_NODE`
- Produces: `_validate_sub_workflow_availability` returns `[]` immediately when flag is `False`

- [ ] **Step 1: Write the failing test**

In `tests/codemie/workflows/test_config_resources_validation.py`, add:

```python
def test_validate_sub_workflow_availability_short_circuits_when_flag_disabled(
    mock_workflow_config_with_id, mock_user
):
    mock_workflow_config_with_id.states = [MagicMock(id="s1", workflow_id="some-wf")]

    with patch('codemie.workflows.validation.resources.config') as mock_cfg, \
         patch('codemie.workflows.validation.resources.WorkflowService') as mock_svc_cls:
        mock_cfg.ENABLE_SUB_WORKFLOW_NODE = False

        result = _validate_sub_workflow_availability(mock_workflow_config_with_id, mock_user)

    assert result == []
    mock_svc_cls.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run python -m pytest "tests/codemie/workflows/test_config_resources_validation.py::test_validate_sub_workflow_availability_short_circuits_when_flag_disabled" -v
```

Expected: `AssertionError` (WorkflowService was called, or result is non-empty)

- [ ] **Step 3: Add flag guard**

At the top of `_validate_sub_workflow_availability()` in `resources.py`, add:

```python
def _validate_sub_workflow_availability(workflow_config: WorkflowConfig, user: User) -> list[tuple[str, str, str]]:
    if not config.ENABLE_SUB_WORKFLOW_NODE:
        return []
    # ... rest of existing implementation unchanged
```

Also add the import if not already present:

```python
from codemie.configs import config
```

- [ ] **Step 4: Run test to verify it passes**

```bash
poetry run python -m pytest "tests/codemie/workflows/test_config_resources_validation.py::test_validate_sub_workflow_availability_short_circuits_when_flag_disabled" -v
```

Expected: PASSED

- [ ] **Step 5: Run full validation test suite for regressions**

```bash
poetry run python -m pytest tests/codemie/workflows/test_config_resources_validation.py -v
```

Expected: all pass

- [ ] **Step 6: Lint**

```bash
poetry run ruff check src/codemie/workflows/validation/resources.py
```

- [ ] **Step 7: Commit**

```bash
git add src/codemie/workflows/validation/resources.py tests/codemie/workflows/test_config_resources_validation.py
git commit -m "feat(EPMCDME-11609): add ENABLE_SUB_WORKFLOW_NODE flag guard to sub-workflow validation"
```

---

### Task 7: Deploy template env vars

**Files:**
- Modify: `deploy-templates/values.yaml`

**Interfaces:**
- Produces: 4 new env vars present in Helm values with safe defaults

- [ ] **Step 1: Locate the env var section in values.yaml**

```bash
grep -n "ENABLE_\|customEnv\|extraEnv\|MCP_CONNECT_ENABLED" deploy-templates/values.yaml | head -20
```

Identify the block where app env vars are defined (look for existing `ENABLE_*` flags as the pattern to follow).

- [ ] **Step 2: Add the four env vars**

Add after the last existing `ENABLE_*` env var entry:

```yaml
- name: ENABLE_SUB_WORKFLOW_NODE
  value: "false"
- name: WORKFLOW_MAX_NESTING_DEPTH
  value: "1"
- name: WORKFLOW_POOL_ENABLED
  value: "false"
- name: WORKFLOW_POOL_MAX_AGE_SECONDS
  value: "3600"
```

- [ ] **Step 3: Verify YAML is valid**

```bash
python3 -c "import yaml; yaml.safe_load(open('deploy-templates/values.yaml'))" && echo "YAML OK"
```

Expected: `YAML OK`

- [ ] **Step 4: Commit**

```bash
git add deploy-templates/values.yaml
git commit -m "feat(EPMCDME-11609): add sub-workflow env vars to deploy-templates/values.yaml"
```

---

## Self-Review

**Spec coverage check:**
- ✅ `SubWorkflowNode(BaseNode)` — Task 4
- ✅ `elif state.workflow_id:` dispatch — Task 5
- ✅ `ENABLE_SUB_WORKFLOW_NODE` flag guard (dispatch + validation) — Tasks 5, 6
- ✅ `WorkflowService.get_nesting_depth()` — Task 2
- ✅ `create_workflow_execution(parent_execution_id)` — Task 3
- ✅ Input mapping (Jinja + fallback to full context_store) — Task 4, T3/T4
- ✅ Child executor with isolated ThoughtQueue — Task 4
- ✅ `active_sub_execution_id` set on create (Task 3), cleared after completion (Task 4, T5)
- ✅ Child failure → `SubWorkflowExecutionError` — Task 4, T6
- ✅ Exception types — Task 1
- ✅ Deploy template — Task 7
- ✅ `nodes/__init__.py` export — Task 4

**Type consistency:**
- `WorkflowService.get_nesting_depth(execution_id: str) -> int` — consistent across Task 2 and Task 4 usage
- `WorkflowService.create_workflow_execution(..., parent_execution_id: Optional[str] = None) -> WorkflowExecution` — consistent across Task 3 definition and Task 4 call
- `SubWorkflowNode.execute(state_schema, execution_context) -> str` — consistent with `post_process_output` consuming a `str`
