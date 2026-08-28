# WorkflowPool Service — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pre-compile sub-workflow `CompiledStateGraph` objects into a thread-safe singleton pool so `SubWorkflowNode.execute()` acquires a warm graph instead of rebuilding one per execution.

**Architecture:** Split `WorkflowExecutor._init_workflow()` into `_compile_graph()` (adds NodeSlot placeholders, user-agnostic) and `_inject_user_context()` (creates real callables, user-dependent). `WorkflowPool` manages a deque per `(workflow_id, config_hash)` key, a background warmup thread, and `acquire()`/`release()` lifecycle. `SubWorkflowNode` integrates pool on the create-path only.

**Tech Stack:** Python 3.11+, LangGraph `CompiledStateGraph`, `collections.deque`, `threading.Lock`, `threading.Event`, `hashlib.sha256`, SQLModel/SQLAlchemy for warmup DB query.

## Global Constraints

- All pool activation requires `config.ENABLE_SUB_WORKFLOW_NODE=True AND config.WORKFLOW_POOL_ENABLED=True AND workflow_config.pool_config.enabled=True`. Any False → compile on demand.
- Pool ceiling per workflow = `min(workflow_config.pool_config.max_size, config.SUBWORKFLOW_POOL_MAX_SIZE)`.
- Pool entries must have all `NodeSlot` delegates cleared before returning to the pool.
- Resume path in `SubWorkflowNode.execute()` is NEVER pooled — create-path only.
- `CompiledStateGraph` is not JSON-serializable; pool lives in-process memory only.
- Pre-work already committed: `NodeSlot` at `src/codemie/workflows/nodes/node_slot.py`, `WorkflowPoolConfig` at `src/codemie/core/workflow_models/workflow_models.py:361`, `pool_config` JSONB column on `WorkflowConfigBase`, `SUBWORKFLOW_POOL_MAX_SIZE`, `SUBWORKFLOW_POOL_WARMUP_INTERVAL_SECONDS`, `WORKFLOW_POOL_ENABLED` in `src/codemie/configs/config.py`.
- New imports in `sub_workflow_node.py` must use lazy (`from ... import ...` inside the function) to avoid circular import `workflow.py → nodes/ → workflow.py`.
- Run tests with: `poetry run pytest tests/codemie/workflows/nodes/test_sub_workflow_node.py tests/codemie/service/test_workflow_pool.py -v`
- Run full suite quality gate: `make ruff && make test`

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `src/codemie/workflows/workflow.py` | Add `_compile_graph()`, `_initialize_node_slots()`, `_inject_user_context()`; refactor `_init_workflow()`; add `compiled_graph` param to `__init__` + `create_executor` + `_run_workflow_execution` |
| Create | `src/codemie/service/workflow_pool.py` | `WorkflowPool` singleton — pool dict, acquire/release, watcher daemon |
| Modify | `src/codemie/rest_api/main.py` | Hook `workflow_pool.initialize()` in `_initialize_optional_features()` |
| Modify | `src/codemie/workflows/nodes/sub_workflow_node.py` | Pool acquire/release on create-path |
| Create | `tests/codemie/service/test_workflow_pool.py` | Pool unit tests |
| Modify | `tests/codemie/workflows/nodes/test_sub_workflow_node.py` | Pool integration variants on create/resume paths |

---

### Task 1: Split `WorkflowExecutor._init_workflow()` into compile and inject phases

**Files:**
- Modify: `src/codemie/workflows/workflow.py` (lines ~105–181, 345–357, 987–993)
- Test: `tests/codemie/workflows/nodes/test_sub_workflow_node.py` (add to existing; no new file needed for this task)

**Interfaces:**
- Produces:
  - `WorkflowExecutor._initialize_node_slots(workflow: StateGraph, workflow_config: WorkflowConfig) -> dict[str, NodeSlot]`
  - `WorkflowExecutor._compile_graph(workflow_config: WorkflowConfig) -> CompiledStateGraph` — attaches `._node_slots` dict to returned graph
  - `WorkflowExecutor._inject_user_context(compiled_graph: CompiledStateGraph) -> None` — sets slot delegates using `self.user`, `self.callbacks`, etc.
  - `WorkflowExecutor.__init__(..., compiled_graph: Optional[CompiledStateGraph] = None)` — stores as `self._compiled_graph`
  - `WorkflowExecutor.create_executor(..., compiled_graph: Optional[CompiledStateGraph] = None)` — passes through to `cls()`
  - `_init_workflow()` behavior: unchanged externally; internally uses new split methods

**Test-first: yes — verify `_compile_graph()` attaches `._node_slots`**

- [ ] **Step 1: Write failing test**

Add to `tests/codemie/workflows/nodes/test_sub_workflow_node.py`:

```python
# ── WorkflowExecutor compile/inject split ──────────────────────────────────

class TestWorkflowExecutorCompileInjectSplit:
    def test_create_executor_accepts_compiled_graph_param(self):
        """create_executor must accept compiled_graph kwarg without error."""
        from codemie.workflows.workflow import WorkflowExecutor

        mock_config = MagicMock()
        mock_config.mode = None  # avoid AUTONOMOUS branch
        mock_user = MagicMock()
        mock_graph = MagicMock()
        mock_graph._node_slots = {}

        with patch("codemie.workflows.workflow.WorkflowExecutor.__init__", return_value=None):
            # Just ensure the signature accepts the param — will raise if not present
            try:
                WorkflowExecutor.create_executor(
                    mock_config, "input", mock_user, compiled_graph=mock_graph
                )
            except TypeError as e:
                pytest.fail(f"create_executor does not accept compiled_graph: {e}")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie/workflows/nodes/test_sub_workflow_node.py::TestWorkflowExecutorCompileInjectSplit -v
```

Expected: FAIL — `TypeError: create_executor() got an unexpected keyword argument 'compiled_graph'`

- [ ] **Step 3: Add `_initialize_node_slots()` to `WorkflowExecutor`**

In `src/codemie/workflows/workflow.py`, add this import at the top (after line 82):

```python
from codemie.workflows.nodes.node_slot import NodeSlot
```

Then add `_initialize_node_slots()` as a method of `WorkflowExecutor` (place after `_init_workflow()` at ~line 358):

```python
def _initialize_node_slots(
    self, workflow: StateGraph, workflow_config: WorkflowConfig
) -> dict[str, "NodeSlot"]:
    slots: dict[str, NodeSlot] = {}
    convergence_nodes = self.find_convergence_nodes(workflow_config)
    map_states = self.find_map_nodes()

    for state in workflow_config.states:
        slot = NodeSlot(state.id)
        retry_policy = workflow_config.get_effective_retry_policy(state=state)
        is_convergence = state.id in convergence_nodes
        workflow.add_node(state.id, slot, retry=retry_policy, defer=is_convergence)
        slots[state.id] = slot

    summarize_slot = NodeSlot(SUMMARIZE_MEMORY_NODE)
    workflow.add_node(SUMMARIZE_MEMORY_NODE, summarize_slot)
    slots[SUMMARIZE_MEMORY_NODE] = summarize_slot

    if workflow_config.enable_summarization_node:
        finalizer_slot = NodeSlot(RESULT_FINALIZER_NODE)
        workflow.add_node(RESULT_FINALIZER_NODE, finalizer_slot)
        slots[RESULT_FINALIZER_NODE] = finalizer_slot

    return slots
```

- [ ] **Step 4: Add `_compile_graph()` to `WorkflowExecutor`**

Add after `_initialize_node_slots()`:

```python
def _compile_graph(self, workflow_config: WorkflowConfig) -> CompiledStateGraph:
    workflow = self.init_state_graph()
    entry_point = workflow_config.states[0].id
    workflow.set_entry_point(entry_point)

    slots = self._initialize_node_slots(workflow, workflow_config)
    self.init_workflow_edges(workflow, workflow_config)

    compile_args: dict = {"debug": config.verbose}
    interrupt_states = [s.id for s in workflow_config.states if s.interrupt_before]
    if interrupt_states:
        compile_args["interrupt_before"] = interrupt_states
        compile_args["checkpointer"] = CheckpointSaver()

    compiled = workflow.compile(**compile_args)
    compiled._node_slots = slots
    return compiled
```

- [ ] **Step 5: Add `_inject_user_context()` to `WorkflowExecutor`**

Add after `_compile_graph()`:

```python
def _inject_user_context(self, compiled_graph: CompiledStateGraph) -> None:
    slots: dict[str, NodeSlot] = compiled_graph._node_slots
    map_states = self.find_map_nodes()

    for state in self.workflow_config.states:
        slot = slots.get(state.id)
        if slot is None:
            continue
        if state.assistant_id:
            node = self.init_agent_node(state, map_states)
        elif state.custom_node_id:
            node = self.init_custom_node(state)
        elif state.tool_id:
            node = self.init_tool_node(state, map_states)
        elif state.workflow_id:
            node = self.init_sub_workflow_node(state)
        else:
            raise ValueError(f"Invalid state configuration: {state}")
        slot.set_delegate(node)

    summarize_slot = slots.get(SUMMARIZE_MEMORY_NODE)
    if summarize_slot:
        summarize_slot.set_delegate(
            SummarizeConversationCommandNode(
                self.callbacks,
                self.workflow_execution_service,
                self.thought_queue,
                self.workflow_config,
                node_name=SUMMARIZE_MEMORY_NODE,
                execution_id=self.execution_id,
            )
        )

    if self.workflow_config.enable_summarization_node:
        finalizer_slot = slots.get(RESULT_FINALIZER_NODE)
        if finalizer_slot:
            finalizer_slot.set_delegate(
                ResultFinalizerNode(
                    self.callbacks,
                    self.workflow_execution_service,
                    self.thought_queue,
                    workflow_config=self.workflow_config,
                    execution_id=self.execution_id,
                    node_name=RESULT_FINALIZER_NODE,
                )
            )
```

- [ ] **Step 6: Refactor `_init_workflow()` to use the new split**

Replace the existing `_init_workflow()` (lines 345–357) with:

```python
def _init_workflow(self) -> CompiledStateGraph:
    compiled = self._compile_graph(self.workflow_config)
    self._inject_user_context(compiled)
    return compiled
```

- [ ] **Step 7: Add `compiled_graph` parameter to `__init__` and `create_executor`**

In `__init__` (find the `__init__` signature and body — it ends before `_init_workflow` at ~line 344), add `compiled_graph: Optional[CompiledStateGraph] = None` as the last parameter and store it:

```python
# At the end of __init__ body, add:
self._compiled_graph = compiled_graph
```

In `create_executor` (lines 110–181), add `compiled_graph: Optional[CompiledStateGraph] = None` to the signature, and in the `cls(...)` instantiation (line ~168), pass `compiled_graph=compiled_graph`. The `SupervisorWorkflowExecutor` branch does NOT receive `compiled_graph` — pool is not used for autonomous mode.

- [ ] **Step 8: Update `_run_workflow_execution()` to use pre-compiled graph when supplied**

Replace lines 987–993:

```python
def _run_workflow_execution(self, graph_config: RunnableConfig, chunks_collector: list):
    """Execute the workflow and collect output chunks."""
    inputs = self.on_workflow_start()
    if self._compiled_graph is not None:
        self._inject_user_context(self._compiled_graph)
        workflow = self._compiled_graph
    else:
        workflow = self._init_workflow()
    self._inject_resume_input(workflow, graph_config)
    self._process_workflow_chunks(workflow, inputs, graph_config, chunks_collector)
    self._check_for_interruption(workflow, graph_config)
```

- [ ] **Step 9: Run tests**

```bash
poetry run pytest tests/codemie/workflows/nodes/test_sub_workflow_node.py -v
```

Expected: All 11 existing tests PASS; new `TestWorkflowExecutorCompileInjectSplit` test PASS.

- [ ] **Step 10: Commit**

```bash
git add src/codemie/workflows/workflow.py tests/codemie/workflows/nodes/test_sub_workflow_node.py
git commit -m "feat(EPMCDME-11609): split WorkflowExecutor._init_workflow into _compile_graph and _inject_user_context"
```

---

### Task 2: WorkflowPool singleton service

**Files:**
- Create: `src/codemie/service/workflow_pool.py`
- Create: `tests/codemie/service/test_workflow_pool.py`

**Interfaces:**
- Consumes: `WorkflowExecutor._compile_graph(workflow_config)` (Task 1)
- Produces:
  - `workflow_pool.acquire(workflow_id: str, workflow_config: WorkflowConfig) -> CompiledStateGraph`
  - `workflow_pool.release(workflow_id: str, workflow_config: WorkflowConfig, graph: CompiledStateGraph) -> None`
  - `workflow_pool.initialize() -> None`
  - `workflow_pool.shutdown() -> None`
  - Module-level: `workflow_pool = WorkflowPool()`

**Test-first: yes — pool miss compiles, warm pool returns existing, release clears slots**

- [ ] **Step 1: Write failing tests**

Create `tests/codemie/service/test_workflow_pool.py`:

```python
import collections
import hashlib
import threading
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


@pytest.fixture(autouse=True)
def reset_pool_state():
    """Reset WorkflowPool class-level state between tests."""
    from codemie.service.workflow_pool import WorkflowPool

    original_initialized = WorkflowPool._initialized
    original_instance = WorkflowPool._instance
    original_pools = WorkflowPool._pools
    yield
    WorkflowPool._initialized = original_initialized
    WorkflowPool._instance = original_instance
    WorkflowPool._pools = original_pools


def _make_config(yaml_config: str = "yaml: value", pool_enabled: bool = True,
                 min_size: int = 2, max_size: int = 3) -> MagicMock:
    cfg = MagicMock()
    cfg.yaml_config = yaml_config
    pc = MagicMock()
    pc.enabled = pool_enabled
    pc.min_size = min_size
    pc.max_size = max_size
    cfg.pool_config = pc
    return cfg


def _make_graph(slot_ids=("node_a",)):
    graph = MagicMock()
    slots = {sid: MagicMock() for sid in slot_ids}
    graph._node_slots = slots
    return graph, slots


class TestWorkflowPoolAcquire:
    def test_acquire_pool_miss_compiles(self):
        """Cold pool: acquire() calls _compile_for() and returns graph."""
        from codemie.service.workflow_pool import WorkflowPool

        pool = WorkflowPool()
        pool._initialized = True
        cfg = _make_config()
        fake_graph, _ = _make_graph()

        with patch.object(pool, "_compile_for", return_value=fake_graph) as mock_compile:
            with patch("codemie.service.workflow_pool.config") as mock_cfg:
                mock_cfg.WORKFLOW_POOL_ENABLED = True
                mock_cfg.SUBWORKFLOW_POOL_MAX_SIZE = 5
                result = pool.acquire("wf-1", cfg)

        mock_compile.assert_called_once_with(cfg)
        assert result is fake_graph

    def test_acquire_warm_returns_from_deque(self):
        """Warm pool: acquire() pops from deque without calling _compile_for()."""
        from codemie.service.workflow_pool import WorkflowPool

        pool = WorkflowPool()
        pool._initialized = True
        cfg = _make_config()
        fake_graph, _ = _make_graph()

        yaml = cfg.yaml_config or ""
        config_hash = hashlib.sha256(yaml.encode()).hexdigest()[:16]
        key = ("wf-1", config_hash)
        pool._pools[key] = collections.deque([fake_graph])

        with patch.object(pool, "_compile_for") as mock_compile:
            with patch("codemie.service.workflow_pool.config") as mock_cfg:
                mock_cfg.WORKFLOW_POOL_ENABLED = True
                result = pool.acquire("wf-1", cfg)

        mock_compile.assert_not_called()
        assert result is fake_graph

    def test_acquire_disabled_per_workflow_compiles_without_pooling(self):
        """pool_config.enabled=False: acquire compiles fresh, never touches pool."""
        from codemie.service.workflow_pool import WorkflowPool

        pool = WorkflowPool()
        pool._initialized = True
        cfg = _make_config(pool_enabled=False)
        fake_graph, _ = _make_graph()

        with patch.object(pool, "_compile_for", return_value=fake_graph) as mock_compile:
            with patch("codemie.service.workflow_pool.config") as mock_cfg:
                mock_cfg.WORKFLOW_POOL_ENABLED = True
                result = pool.acquire("wf-1", cfg)

        mock_compile.assert_called_once()
        # Graph acquired but not put into any pool deque
        assert len(pool._pools) == 0
        assert result is fake_graph

    def test_acquire_global_flag_disabled_compiles_without_pooling(self):
        """WORKFLOW_POOL_ENABLED=False: acquire compiles fresh, never touches pool."""
        from codemie.service.workflow_pool import WorkflowPool

        pool = WorkflowPool()
        pool._initialized = True
        cfg = _make_config(pool_enabled=True)
        fake_graph, _ = _make_graph()

        with patch.object(pool, "_compile_for", return_value=fake_graph) as mock_compile:
            with patch("codemie.service.workflow_pool.config") as mock_cfg:
                mock_cfg.WORKFLOW_POOL_ENABLED = False
                result = pool.acquire("wf-1", cfg)

        mock_compile.assert_called_once()
        assert len(pool._pools) == 0


class TestWorkflowPoolRelease:
    def test_release_clears_slots_and_returns_to_pool(self):
        """release() clears slot delegates then pushes graph back under ceiling."""
        from codemie.service.workflow_pool import WorkflowPool

        pool = WorkflowPool()
        pool._initialized = True
        cfg = _make_config(max_size=3)
        fake_graph, slots = _make_graph(("a", "b"))

        with patch("codemie.service.workflow_pool.config") as mock_cfg:
            mock_cfg.WORKFLOW_POOL_ENABLED = True
            mock_cfg.SUBWORKFLOW_POOL_MAX_SIZE = 5
            pool.release("wf-1", cfg, fake_graph)

        for slot in slots.values():
            slot.clear.assert_called_once()

        yaml = cfg.yaml_config or ""
        config_hash = hashlib.sha256(yaml.encode()).hexdigest()[:16]
        key = ("wf-1", config_hash)
        assert fake_graph in pool._pools[key]

    def test_release_discards_at_ceiling(self):
        """release() does not push graph when pool is at ceiling."""
        from codemie.service.workflow_pool import WorkflowPool

        pool = WorkflowPool()
        pool._initialized = True
        cfg = _make_config(max_size=2)

        existing1, _ = _make_graph()
        existing2, _ = _make_graph()
        new_graph, slots = _make_graph(("x",))

        yaml = cfg.yaml_config or ""
        config_hash = hashlib.sha256(yaml.encode()).hexdigest()[:16]
        key = ("wf-1", config_hash)
        pool._pools[key] = collections.deque([existing1, existing2])

        with patch("codemie.service.workflow_pool.config") as mock_cfg:
            mock_cfg.WORKFLOW_POOL_ENABLED = True
            mock_cfg.SUBWORKFLOW_POOL_MAX_SIZE = 5
            pool.release("wf-1", cfg, new_graph)

        # Slots cleared even on discard
        slots["x"].clear.assert_called_once()
        # New graph NOT in pool
        assert new_graph not in pool._pools[key]
        assert len(pool._pools[key]) == 2

    def test_release_disabled_clears_slots_does_not_pool(self):
        """pool_config.enabled=False: release clears slots but does not return to pool."""
        from codemie.service.workflow_pool import WorkflowPool

        pool = WorkflowPool()
        pool._initialized = True
        cfg = _make_config(pool_enabled=False)
        fake_graph, slots = _make_graph(("a",))

        with patch("codemie.service.workflow_pool.config") as mock_cfg:
            mock_cfg.WORKFLOW_POOL_ENABLED = True
            mock_cfg.SUBWORKFLOW_POOL_MAX_SIZE = 5
            pool.release("wf-1", cfg, fake_graph)

        slots["a"].clear.assert_called_once()
        assert len(pool._pools) == 0


class TestWorkflowPoolConfigHash:
    def test_different_yaml_produces_different_hash(self):
        """Config hash changes when yaml_config changes → different pool key."""
        from codemie.service.workflow_pool import WorkflowPool

        pool = WorkflowPool()
        cfg_a = _make_config(yaml_config="version: 1")
        cfg_b = _make_config(yaml_config="version: 2")
        graph_a, _ = _make_graph()
        graph_b, _ = _make_graph()

        with patch.object(pool, "_compile_for", side_effect=[graph_a, graph_b]):
            with patch("codemie.service.workflow_pool.config") as mock_cfg:
                mock_cfg.WORKFLOW_POOL_ENABLED = True
                mock_cfg.SUBWORKFLOW_POOL_MAX_SIZE = 5
                r_a = pool.acquire("wf-1", cfg_a)
                r_b = pool.acquire("wf-1", cfg_b)

        assert r_a is graph_a
        assert r_b is graph_b


class TestWorkflowPoolSingleton:
    def test_singleton_returns_same_instance(self):
        from codemie.service.workflow_pool import WorkflowPool

        a = WorkflowPool()
        b = WorkflowPool()
        assert a is b
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie/service/test_workflow_pool.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'codemie.service.workflow_pool'`

- [ ] **Step 3: Create `src/codemie/service/workflow_pool.py`**

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

"""Singleton pool for pre-compiled sub-workflow CompiledStateGraph instances."""

import atexit
import collections
import hashlib
import threading
from typing import Optional

from langgraph.graph.state import CompiledStateGraph

from codemie.configs import config, logger
from codemie.core.workflow_models import WorkflowConfig


class WorkflowPool:
    """Thread-safe pool of pre-compiled CompiledStateGraph instances.

    Pool is keyed by (workflow_id, config_hash) so a yaml_config change
    automatically invalidates old entries.
    """

    _instance: Optional["WorkflowPool"] = None
    _initialized: bool = False
    _pool_lock: threading.Lock = threading.Lock()
    _pools: dict[tuple[str, str], collections.deque] = {}
    _stop_event: threading.Event = threading.Event()
    _watcher_thread: Optional[threading.Thread] = None

    def __new__(cls) -> "WorkflowPool":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def initialize(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._stop_event.clear()
        self._watcher_thread = threading.Thread(
            target=self._run_warmup_loop,
            name="workflow-pool-warmup",
            daemon=True,
        )
        self._watcher_thread.start()
        atexit.register(self.shutdown)
        logger.info("WorkflowPool initialized")

    def shutdown(self) -> None:
        self._stop_event.set()
        if self._watcher_thread and self._watcher_thread.is_alive():
            self._watcher_thread.join(timeout=5)
        logger.info("WorkflowPool shut down")

    def acquire(self, workflow_id: str, workflow_config: WorkflowConfig) -> CompiledStateGraph:
        pool_config = getattr(workflow_config, "pool_config", None)
        if not pool_config or not pool_config.enabled or not config.WORKFLOW_POOL_ENABLED:
            return self._compile_for(workflow_config)

        key = (workflow_id, self._config_hash(workflow_config))
        with self._pool_lock:
            deque = self._pools.get(key)
            if deque:
                return deque.popleft()

        return self._compile_for(workflow_config)

    def release(self, workflow_id: str, workflow_config: WorkflowConfig, graph: CompiledStateGraph) -> None:
        slots = getattr(graph, "_node_slots", {})
        for slot in slots.values():
            slot.clear()

        pool_config = getattr(workflow_config, "pool_config", None)
        if not pool_config or not pool_config.enabled or not config.WORKFLOW_POOL_ENABLED:
            return

        key = (workflow_id, self._config_hash(workflow_config))
        ceiling = min(pool_config.max_size, config.SUBWORKFLOW_POOL_MAX_SIZE)
        with self._pool_lock:
            deque = self._pools.setdefault(key, collections.deque())
            if len(deque) < ceiling:
                deque.append(graph)

    def _compile_for(self, workflow_config: WorkflowConfig) -> CompiledStateGraph:
        from codemie.workflows.workflow import WorkflowExecutor

        shell = object.__new__(WorkflowExecutor)
        shell.workflow_config = workflow_config
        return shell._compile_graph(workflow_config)

    def _config_hash(self, workflow_config: WorkflowConfig) -> str:
        yaml = getattr(workflow_config, "yaml_config", None) or ""
        return hashlib.sha256(yaml.encode()).hexdigest()[:16]

    def _run_warmup_loop(self) -> None:
        interval = config.SUBWORKFLOW_POOL_WARMUP_INTERVAL_SECONDS
        while not self._stop_event.is_set():
            try:
                self._refill_all()
            except Exception:
                logger.exception("WorkflowPool warmup error")
            self._stop_event.wait(timeout=interval)

    def _refill_all(self) -> None:
        for wf_config in self._get_pool_enabled_configs():
            wf_id = str(wf_config.id)
            key = (wf_id, self._config_hash(wf_config))
            pool_config = wf_config.pool_config
            with self._pool_lock:
                current_size = len(self._pools.get(key, []))
                slots_to_fill = max(0, pool_config.min_size - current_size)

            for _ in range(slots_to_fill):
                try:
                    graph = self._compile_for(wf_config)
                    ceiling = min(pool_config.max_size, config.SUBWORKFLOW_POOL_MAX_SIZE)
                    with self._pool_lock:
                        deque = self._pools.setdefault(key, collections.deque())
                        if len(deque) < ceiling:
                            deque.append(graph)
                except Exception:
                    logger.exception(f"WorkflowPool warmup failed for workflow {wf_id}")

    def _get_pool_enabled_configs(self) -> list[WorkflowConfig]:
        try:
            from codemie.service.workflow_service import WorkflowService

            return WorkflowService.get_pool_enabled_workflow_configs()
        except Exception:
            logger.exception("WorkflowPool failed to fetch pool-enabled workflow configs")
            return []


workflow_pool = WorkflowPool()
```

- [ ] **Step 4: Add `WorkflowService.get_pool_enabled_workflow_configs()` classmethod**

In `src/codemie/service/workflow_service.py`, add this classmethod (find the class, add alongside other classmethods):

```python
@classmethod
def get_pool_enabled_workflow_configs(cls) -> list:
    """Return all WorkflowConfig rows that have pool_config.enabled=True."""
    from sqlmodel import Session, select, cast
    from sqlalchemy import text
    from codemie.clients.postgres import PostgresClient
    from codemie.core.workflow_models import WorkflowConfig

    with Session(PostgresClient.get_engine()) as session:
        # Filter rows where pool_config JSONB enabled flag is true
        stmt = select(WorkflowConfig).where(
            WorkflowConfig.pool_config.is_not(None)
        )
        rows = session.exec(stmt).all()
        return [r for r in rows if getattr(r.pool_config, "enabled", False)]
```

- [ ] **Step 5: Run pool tests**

```bash
poetry run pytest tests/codemie/service/test_workflow_pool.py -v
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/workflow_pool.py src/codemie/service/workflow_service.py
git add tests/codemie/service/test_workflow_pool.py
git commit -m "feat(EPMCDME-11609): add WorkflowPool singleton with acquire/release/warmup"
```

---

### Task 3: Wire pool acquire/release into `SubWorkflowNode.execute()` create-path

**Files:**
- Modify: `src/codemie/workflows/nodes/sub_workflow_node.py` (lines 93–125)
- Modify: `tests/codemie/workflows/nodes/test_sub_workflow_node.py`

**Interfaces:**
- Consumes: `workflow_pool.acquire(workflow_id, workflow_config) -> CompiledStateGraph` (Task 2)
- Consumes: `WorkflowExecutor.create_executor(..., compiled_graph=graph)` (Task 1)
- Consumes: `workflow_pool.release(workflow_id, workflow_config, graph)` (Task 2)

**Test-first: yes — create-path calls acquire/release; resume-path does not**

- [ ] **Step 1: Write failing tests**

Add to `tests/codemie/workflows/nodes/test_sub_workflow_node.py`:

```python
class TestSubWorkflowNodePoolIntegration:
    """Pool acquire/release wired into create-path only."""

    @pytest.fixture
    def node(self, workflow_state, workflow_execution_service):
        return SubWorkflowNode(
            callbacks=[],
            workflow_execution_service=workflow_execution_service,
            thought_queue=MagicMock(),
            workflow_state=workflow_state,
            node_name="sub_wf",
            execution_id="parent-exec-id",
            workflow_config=MagicMock(),
        )

    def test_create_path_acquires_and_releases_pool_graph(self, node):
        """acquire() called on create-path; release() called in finally."""
        mock_graph = MagicMock()
        mock_graph._node_slots = {}

        with patch("codemie.workflows.nodes.sub_workflow_node.config") as mock_config, \
             patch("codemie.workflows.nodes.sub_workflow_node.WorkflowService") as mock_ws, \
             patch("codemie.workflows.nodes.sub_workflow_node.workflow_pool") as mock_pool, \
             patch("codemie.workflows.workflow.WorkflowExecutor.create_executor") as mock_create:

            mock_config.ENABLE_SUB_WORKFLOW_NODE = True
            mock_config.WORKFLOW_MAX_NESTING_DEPTH = 1
            mock_config.WORKFLOW_POOL_ENABLED = True

            parent_exec = MagicMock()
            parent_exec.active_sub_execution_id = None
            mock_ws.find_workflow_execution_by_id.return_value = parent_exec

            child_config = MagicMock()
            child_config.max_nesting_level = None
            mock_ws.return_value.get_workflow.return_value = child_config
            mock_ws.get_nesting_depth.return_value = 0

            child_exec = MagicMock()
            child_exec.execution_id = "child-exec-1"
            mock_ws.create_workflow_execution.return_value = child_exec

            mock_pool.acquire.return_value = mock_graph
            finished = MagicMock()
            finished.overall_status = WorkflowExecutionStatusEnum.COMPLETED
            finished.output = "result"
            mock_ws.find_workflow_execution_by_id.side_effect = [parent_exec, finished, parent_exec]

            mock_executor = MagicMock()
            mock_create.return_value = mock_executor

            node.execute(MagicMock(), {})

        mock_pool.acquire.assert_called_once_with("child-wf-id", child_config)
        mock_pool.release.assert_called_once_with("child-wf-id", child_config, mock_graph)

    def test_create_path_releases_pool_graph_on_failure(self, node):
        """release() called in finally even when stream() raises."""
        mock_graph = MagicMock()
        mock_graph._node_slots = {}

        with patch("codemie.workflows.nodes.sub_workflow_node.config") as mock_config, \
             patch("codemie.workflows.nodes.sub_workflow_node.WorkflowService") as mock_ws, \
             patch("codemie.workflows.nodes.sub_workflow_node.workflow_pool") as mock_pool, \
             patch("codemie.workflows.workflow.WorkflowExecutor.create_executor") as mock_create:

            mock_config.ENABLE_SUB_WORKFLOW_NODE = True
            mock_config.WORKFLOW_MAX_NESTING_DEPTH = 1
            mock_config.WORKFLOW_POOL_ENABLED = True

            parent_exec = MagicMock()
            parent_exec.active_sub_execution_id = None
            mock_ws.find_workflow_execution_by_id.return_value = parent_exec

            child_config = MagicMock()
            child_config.max_nesting_level = None
            mock_ws.return_value.get_workflow.return_value = child_config
            mock_ws.get_nesting_depth.return_value = 0

            child_exec = MagicMock()
            child_exec.execution_id = "child-exec-fail"
            mock_ws.create_workflow_execution.return_value = child_exec

            mock_pool.acquire.return_value = mock_graph

            mock_executor = MagicMock()
            mock_executor.stream.side_effect = RuntimeError("stream failed")
            mock_create.return_value = mock_executor

            with pytest.raises(RuntimeError):
                node.execute(MagicMock(), {})

        mock_pool.release.assert_called_once_with("child-wf-id", child_config, mock_graph)

    def test_resume_path_does_not_call_pool(self, node):
        """Resume path skips pool entirely."""
        with patch("codemie.workflows.nodes.sub_workflow_node.config") as mock_config, \
             patch("codemie.workflows.nodes.sub_workflow_node.WorkflowService") as mock_ws, \
             patch("codemie.workflows.nodes.sub_workflow_node.workflow_pool") as mock_pool, \
             patch("codemie.workflows.workflow.WorkflowExecutor.create_executor") as mock_create:

            mock_config.ENABLE_SUB_WORKFLOW_NODE = True

            parent_exec = MagicMock()
            parent_exec.active_sub_execution_id = "child-exec-resume"
            child_exec = MagicMock()
            child_exec.execution_id = "child-exec-resume"
            child_exec.workflow_id = "child-wf-id"

            child_config = MagicMock()
            mock_ws.find_workflow_execution_by_id.side_effect = [parent_exec, child_exec, MagicMock()]
            mock_ws.return_value.get_workflow.return_value = child_config

            finished = MagicMock()
            finished.overall_status = WorkflowExecutionStatusEnum.COMPLETED
            finished.output = "ok"
            mock_ws.find_workflow_execution_by_id.side_effect = [
                parent_exec, child_exec, finished, parent_exec
            ]

            mock_executor = MagicMock()
            mock_create.return_value = mock_executor

            node.execute(MagicMock(), {})

        mock_pool.acquire.assert_not_called()
        mock_pool.release.assert_not_called()
```

- [ ] **Step 2: Run to verify tests fail**

```bash
poetry run pytest tests/codemie/workflows/nodes/test_sub_workflow_node.py::TestSubWorkflowNodePoolIntegration -v
```

Expected: FAIL — `ImportError` or `AttributeError` since `workflow_pool` not imported in `sub_workflow_node.py`.

- [ ] **Step 3: Modify `sub_workflow_node.py` create-path**

In `src/codemie/workflows/nodes/sub_workflow_node.py`, add the lazy import and pool integration to the create-path (lines 93–125). Replace the create-path block starting at line 93 with:

```python
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

            from codemie.service.workflow_pool import workflow_pool

            graph = workflow_pool.acquire(self.sub_workflow_id, child_config)
            try:
                child_executor = WorkflowExecutor.create_executor(
                    child_config,
                    child_input,
                    user,
                    execution_id=child_execution.execution_id,
                    thought_queue=ThoughtQueue(),
                    compiled_graph=graph,
                )
                child_executor.stream()
            finally:
                workflow_pool.release(self.sub_workflow_id, child_config, graph)
```

Remove the old `child_executor = WorkflowExecutor.create_executor(...)` and `child_executor.stream()` lines at lines 117–125 (now replaced by the block above). The `child_executor.stream()` call at line 125 was outside the create/resume if-else — replace it entirely with the pool-guarded version inside the else block above.

The resume path (lines 84–92) is unchanged.

The post-stream checks (lines 127–147) remain unchanged and fall through from both paths.

- [ ] **Step 4: Run tests**

```bash
poetry run pytest tests/codemie/workflows/nodes/test_sub_workflow_node.py -v
```

Expected: All tests PASS including the 11 existing tests and new pool tests.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/workflows/nodes/sub_workflow_node.py
git add tests/codemie/workflows/nodes/test_sub_workflow_node.py
git commit -m "feat(EPMCDME-11609): integrate WorkflowPool acquire/release into SubWorkflowNode create-path"
```

---

### Task 4: Wire `WorkflowPool.initialize()` into FastAPI startup

**Files:**
- Modify: `src/codemie/rest_api/main.py` (lines 345–358)

**Interfaces:**
- Consumes: `workflow_pool.initialize()` (Task 2)
- No dedicated test file — startup hook is verified via integration and existing main.py tests.

**Test-first: yes — verify `_initialize_optional_features` calls `workflow_pool.initialize()`**

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/rest_api/test_workflow_pool_startup.py`:

```python
import pytest
from unittest.mock import patch, MagicMock


class TestWorkflowPoolStartupHook:
    def test_initialize_optional_features_calls_pool_init_when_flags_enabled(self):
        """When both ENABLE_SUB_WORKFLOW_NODE and WORKFLOW_POOL_ENABLED are True,
        _initialize_optional_features() must call workflow_pool.initialize()."""
        with patch("codemie.rest_api.main.config") as mock_config, \
             patch("codemie.service.workflow_pool.workflow_pool") as mock_pool:

            mock_config.TOOL_SELECTION_ENABLED = False
            mock_config.PLATFORM_DATASOURCES_SYNC_ENABLED = False
            mock_config.ENABLE_SUB_WORKFLOW_NODE = True
            mock_config.WORKFLOW_POOL_ENABLED = True

            from codemie.rest_api.main import _initialize_optional_features
            _initialize_optional_features()

        mock_pool.initialize.assert_called_once()

    def test_initialize_optional_features_skips_pool_when_sub_workflow_disabled(self):
        """When ENABLE_SUB_WORKFLOW_NODE=False, pool init is skipped."""
        with patch("codemie.rest_api.main.config") as mock_config, \
             patch("codemie.service.workflow_pool.workflow_pool") as mock_pool:

            mock_config.TOOL_SELECTION_ENABLED = False
            mock_config.PLATFORM_DATASOURCES_SYNC_ENABLED = False
            mock_config.ENABLE_SUB_WORKFLOW_NODE = False
            mock_config.WORKFLOW_POOL_ENABLED = True

            from codemie.rest_api.main import _initialize_optional_features
            _initialize_optional_features()

        mock_pool.initialize.assert_not_called()
```

- [ ] **Step 2: Run to verify test fails**

```bash
poetry run pytest tests/codemie/rest_api/test_workflow_pool_startup.py -v
```

Expected: FAIL — `mock_pool.initialize.assert_called_once()` raises `AssertionError` since `_initialize_optional_features` doesn't call it yet.

- [ ] **Step 3: Add pool init hook to `_initialize_optional_features()`**

In `src/codemie/rest_api/main.py`, at the end of `_initialize_optional_features()` (after the `PLATFORM_DATASOURCES_SYNC_ENABLED` block, before the closing brace), add:

```python
    if config.ENABLE_SUB_WORKFLOW_NODE and config.WORKFLOW_POOL_ENABLED:
        from codemie.service.workflow_pool import workflow_pool

        workflow_pool.initialize()
        logger.info("WorkflowPool initialized for sub-workflow pre-compilation")
```

- [ ] **Step 4: Run the startup test**

```bash
poetry run pytest tests/codemie/rest_api/test_workflow_pool_startup.py -v
```

Expected: PASS.

- [ ] **Step 5: Run full test suite**

```bash
poetry run pytest tests/codemie/workflows/nodes/test_sub_workflow_node.py tests/codemie/service/test_workflow_pool.py tests/codemie/rest_api/test_workflow_pool_startup.py -v
```

Expected: All tests PASS.

- [ ] **Step 6: Run quality gates**

```bash
make ruff
make test
```

Expected: ruff clean, all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/codemie/rest_api/main.py tests/codemie/rest_api/test_workflow_pool_startup.py
git commit -m "feat(EPMCDME-11609): hook WorkflowPool.initialize into FastAPI startup optional features"
```

---

## Self-Review

### Spec Coverage Check

| Spec requirement | Task |
|---|---|
| `_compile_graph()` / `_inject_user_context()` split | Task 1 |
| `compiled_graph` param on `create_executor` + `__init__` | Task 1 |
| `_run_workflow_execution` uses pre-compiled graph when set | Task 1 |
| `WorkflowPool` singleton with `_pools` deque | Task 2 |
| `acquire()` → cold compile or warm pop | Task 2 |
| `release()` → clear slots + return under ceiling | Task 2 |
| `_config_hash()` pool key invalidation | Task 2 |
| Background warmup watcher thread | Task 2 |
| `atexit.register` in `initialize()` | Task 2 |
| `SubWorkflowNode` create-path pool acquire/release | Task 3 |
| Resume path unchanged / no pool call | Task 3 |
| `_initialize_optional_features()` startup hook | Task 4 |
| All 3 feature flags checked before pooling | Tasks 2 + 3 |
| Acceptance: warm pool hit skips compile | Task 2 test `test_acquire_warm_returns_from_deque` |
| Acceptance: pool miss behaves identically | Task 2 test `test_acquire_pool_miss_compiles` |
| Acceptance: config invalidation via hash | Task 2 test `test_different_yaml_produces_different_hash` |
| Acceptance: slots cleared after execution | Task 2 test `test_release_clears_slots_and_returns_to_pool` |
| Acceptance: shutdown stops watcher | Task 2 `WorkflowPool.shutdown()` |

### Type Consistency

- `_compile_graph(workflow_config: WorkflowConfig) -> CompiledStateGraph` — matches usage in `_init_workflow()` and `_compile_for()`
- `_inject_user_context(compiled_graph: CompiledStateGraph) -> None` — matches usage in `_run_workflow_execution()`
- `workflow_pool.acquire(workflow_id: str, workflow_config: WorkflowConfig) -> CompiledStateGraph` — matches usage in `sub_workflow_node.py`
- `workflow_pool.release(workflow_id: str, workflow_config: WorkflowConfig, graph: CompiledStateGraph) -> None` — matches usage in `sub_workflow_node.py` finally block
- `compiled_graph._node_slots: dict[str, NodeSlot]` — attached in `_compile_graph()`, read in `_inject_user_context()` and `release()`
