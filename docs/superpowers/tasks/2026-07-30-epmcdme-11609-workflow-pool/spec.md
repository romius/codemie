# WorkflowPool Service — Spec

**Ticket**: EPMCDME-11609 Task 4  
**Branch**: EPMCDME-11609_sub-workflow-node  
**Status**: approved

---

## Goal

Pre-compile `CompiledStateGraph` objects for sub-workflows and hold them in a thread-safe pool, so `SubWorkflowNode.execute()` can acquire a warm, ready-to-use graph instead of rebuilding one from scratch per execution. User-dependent content (agent construction) is injected at execution time, not at compile time.

---

## Pre-work Already Committed

The following are on the branch and require no further changes:

| Artifact | Location | Notes |
|---|---|---|
| `NodeSlot` | `src/codemie/workflows/nodes/node_slot.py` | Callable placeholder; `set_delegate()` / `clear()` / `__call__()` |
| `WorkflowPoolConfig` | `src/codemie/core/workflow_models/workflow_models.py:361` | `enabled`, `min_size`, `max_size`, `refill_interval_seconds` |
| `pool_config` column | `src/codemie/core/workflow_models/workflow_config.py:115` | Nullable JSONB on `WorkflowConfigBase` |
| `SUBWORKFLOW_POOL_MAX_SIZE` | `src/codemie/configs/config.py:506` | Default 5 |
| `SUBWORKFLOW_POOL_WARMUP_INTERVAL_SECONDS` | `src/codemie/configs/config.py:507` | Default 60 |
| `WORKFLOW_POOL_ENABLED` | `src/codemie/configs/config.py:172` | Default True; pool-specific kill-switch |

---

## Architecture

### Compile/inject split in `WorkflowExecutor`

`_init_workflow()` is split into two phases:

```
_compile_graph(workflow_config) → CompiledStateGraph
    Adds NodeSlot wrappers for every node (user-agnostic).
    Adds edges. Calls StateGraph.compile(interrupt_before, checkpointer).
    Attaches ._node_slots dict to the compiled graph.

_inject_user_context(compiled_graph) → None
    Creates real AgentNode / ToolNode / SubWorkflowNode / etc for self.user.
    Calls slot.set_delegate(real_node) for each slot.

_init_workflow() → CompiledStateGraph     [unchanged behaviour]
    return self._inject_user_context(self._compile_graph(self.workflow_config))
```

`_compile_graph` is an instance method. The pool calls it on a shell executor (created with `object.__new__(WorkflowExecutor)` + `shell.workflow_config = workflow_config`) to avoid the expensive full `__init__` (no user, no DB session, no callbacks).

The edge lambdas in `init_workflow_edges()` capture `self` (the shell), but they only call `self.continue_iteration()` and `evaluate()`, both of which are user-agnostic — correct at execution time.

### `WorkflowPool` singleton

Located at `src/codemie/service/workflow_pool.py`. Follows the `FileProcessPoolManager` pattern.

```
WorkflowPool
  _instance          class attr — singleton ref
  _initialized       class attr — init guard
  _pool_lock         threading.Lock — protects _pools and _watcher
  _pools             dict[(workflow_id, config_hash), deque[CompiledStateGraph]]
  _watcher_thread    threading.Thread (daemon=True)

  initialize()
    guard with _initialized
    start _watcher_thread
    atexit.register(self.shutdown)

  acquire(workflow_id, workflow_config) → CompiledStateGraph
    if not pool_config.enabled or not config.WORKFLOW_POOL_ENABLED:
        return self._compile_for(workflow_config)     [on-demand, no pool]
    key = (workflow_id, _config_hash(workflow_config))
    with _pool_lock:
        if key in _pools and _pools[key]:
            return _pools[key].popleft()
    return self._compile_for(workflow_config)         [pool miss]

  release(workflow_id, workflow_config, graph)
    # clear user context regardless of run outcome
    for slot in graph._node_slots.values():
        slot.clear()
    if not pool_config.enabled or not config.WORKFLOW_POOL_ENABLED:
        return                                         [discard]
    key = (workflow_id, _config_hash(workflow_config))
    ceiling = min(workflow_config.pool_config.max_size, config.SUBWORKFLOW_POOL_MAX_SIZE)
    with _pool_lock:
        deque = _pools.setdefault(key, collections.deque())
        if len(deque) < ceiling:
            deque.append(graph)

  shutdown()
    signal _watcher_thread to stop; join with timeout

  _compile_for(workflow_config) → CompiledStateGraph [private]
    creates shell executor; calls shell._compile_graph(workflow_config)

  _config_hash(workflow_config) → str [private]
    hashlib.sha256(workflow_config.yaml_config or "").hexdigest()[:16]
    invalidated automatically when yaml_config changes
```

### Background watcher

Daemon thread started in `initialize()`. Runs every `config.SUBWORKFLOW_POOL_WARMUP_INTERVAL_SECONDS` seconds.

```
while not _stop_event.is_set():
    try:
        for wf_config in _get_pool_enabled_workflows():   [DB query, sync]
            key = (wf_config.id, _config_hash(wf_config))
            with _pool_lock:
                current_size = len(_pools.get(key, []))
                slots_to_fill = max(0, wf_config.pool_config.min_size - current_size)
            for _ in range(slots_to_fill):
                graph = self._compile_for(wf_config)
                with _pool_lock:
                    _pools.setdefault(key, deque()).append(graph)
    except Exception:
        logger.exception("WorkflowPool warmup error")
    _stop_event.wait(timeout=INTERVAL)
```

`_get_pool_enabled_workflows()` queries all `WorkflowConfig` rows where `pool_config` is not null and `pool_config->>'enabled' = 'true'` using a SQLModel session.

### Startup hook in `main.py`

`_initialize_optional_features()` is extended:

```python
if config.ENABLE_SUB_WORKFLOW_NODE and config.WORKFLOW_POOL_ENABLED:
    from codemie.service.workflow_pool import workflow_pool
    workflow_pool.initialize()
```

No APScheduler needed — the watcher manages its own timing with `_stop_event.wait()`.  
`workflow_pool.shutdown()` is registered via `atexit.register` inside `initialize()`; `_shutdown_services()` in `main.py` does not need explicit teardown.

### `SubWorkflowNode.execute()` — pool integration (create-path only)

```python
# Create path only — resume path is unchanged
graph = workflow_pool.acquire(self.sub_workflow_id, child_config)
try:
    child_executor = WorkflowExecutor.create_executor(
        child_config, child_input, user,
        execution_id=child_execution.execution_id,
        thought_queue=ThoughtQueue(),
        compiled_graph=graph,
    )
    child_executor.stream()
finally:
    workflow_pool.release(self.sub_workflow_id, child_config, graph)
```

`WorkflowExecutor.__init__` and `create_executor()` gain an optional `compiled_graph=None` parameter. When set, `_run_workflow_execution()` skips `_init_workflow()` and uses the pre-compiled graph; it calls `_inject_user_context(compiled_graph)` before streaming.

---

## Feature Flags

| Flag | Source | Effect |
|---|---|---|
| `ENABLE_SUB_WORKFLOW_NODE` | `config.py` | Master gate; pool is unreachable if False |
| `WORKFLOW_POOL_ENABLED` | `config.py` | Pool-specific kill-switch; disables pooling globally |
| `pool_config.enabled` | per-workflow JSONB | Opt-in per workflow; False means on-demand compile |

All three must be True for pool acquisition to occur.

---

## Pool entry lifecycle

**Return to pool on every terminal outcome** (success, failure, interrupt). `release()` always:
1. Calls `slot.clear()` on every `NodeSlot` — removes all user references.
2. Returns the graph to the pool deque if below ceiling; discards if at ceiling.

`CheckpointSaver` is stateless (queries DB by `thread_id`); no explicit reset is required between runs. Each execution uses a distinct `execution_id` so checkpoint entries never collide.

---

## `config_version_hash` invalidation

The pool key is `(workflow_id, sha256(yaml_config)[:16])`. When a workflow's YAML is updated, the hash changes → old pool entries become unreachable (orphaned) and are eventually garbage-collected. The watcher pre-warms using the new hash from the next cycle.

---

## Acceptance Criteria

- Warm pool hit: no `StateGraph.compile()` call on the hot path when pool has an entry.
- Pool miss / disabled: behavior is identical to pre-pool execution.
- Thread-safety: concurrent `acquire()`/`release()` on the same `workflow_id` do not race.
- User isolation: released graph carries no user references (all slots cleared).
- Config invalidation: updating `yaml_config` naturally produces a new pool key; old entries are not served.
- Watcher fills to `min_size` without blocking the request thread.
- `shutdown()` stops the watcher cleanly at application exit.
- Workflows with `pool_config.enabled=False` are unaffected; latency unchanged.

---

## Files Changed

| File | Change |
|---|---|
| `src/codemie/workflows/nodes/node_slot.py` | Pre-work — no changes |
| `src/codemie/workflows/workflow.py` | Add `_compile_graph()`, `_inject_user_context()`; refactor `_init_workflow()`; add `compiled_graph` param to `__init__` + `create_executor` + `_run_workflow_execution` |
| `src/codemie/service/workflow_pool.py` | New — `WorkflowPool` singleton |
| `src/codemie/rest_api/main.py` | Hook `workflow_pool.initialize()` in `_initialize_optional_features()` |
| `src/codemie/workflows/nodes/sub_workflow_node.py` | Pool acquire/release on create-path |
| `src/codemie/configs/config.py` | Pre-work — no changes |
| `tests/codemie/service/test_workflow_pool.py` | New — pool unit tests |
| `tests/codemie/workflows/nodes/test_sub_workflow_node.py` | Add pool integration variants |

---

## Out of Scope

- `SupervisorWorkflowExecutor` (autonomous mode): pool is gated to sequential workflows only; autonomous mode bypasses SubWorkflowNode entirely.
- Redis / shared-memory cache: graphs live in-process only (`CompiledStateGraph` is not serializable).
- Per-execution TTL (`WORKFLOW_POOL_MAX_AGE_SECONDS`): pool entries have no expiry beyond config-hash invalidation; TTL enforcement is deferred.
