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
        if not pool_config or not pool_config.enabled or not config.SUBWORKFLOW_POOL_ENABLED:
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
        if not pool_config or not pool_config.enabled or not config.SUBWORKFLOW_POOL_ENABLED:
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
