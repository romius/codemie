# Technical Research

**Task**: sub_workflow workflow_pool interrupt resume test
**Generated**: 2026-08-10T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Fix the failing test "FAILED tests/codemie/workflows/test_workflow_state_transitions.py::test_tc_wst_010_sub_workflow_interrupt_and_resume - TypeError: object supporting the buffer API required". The test validates sub-workflow cross-boundary interrupt and resume behavior. Key files already located: tests/codemie/workflows/test_workflow_state_transitions.py (test at line 513), src/codemie/workflows/nodes/sub_workflow_node.py, src/codemie/service/workflow_pool.py. Error traceback: the test calls node.execute(state_schema, {}) which reaches workflow_pool.acquire() → _config_hash() → hashlib.sha256(yaml.encode()) fails because yaml is a MagicMock (not a string). The test uses child_config = MagicMock(max_nesting_level=None) but does not configure yaml_config, so getattr returns a MagicMock. Additionally, the test asserts parent_exec.active_sub_execution_id == "child-exec-id" and parent_exec.save.assert_called_once() but the production code in sub_workflow_node.py never sets active_sub_execution_id on parent_exec before streaming.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/workflows/nodes/sub_workflow_node.py` — `SubWorkflowNode.execute()` orchestrates create-or-resume logic for child workflows; two branches gated on `parent_exec.active_sub_execution_id`
- `src/codemie/service/workflow_pool.py` — singleton `WorkflowPool`; `acquire()` returns a pre-compiled `CompiledStateGraph` keyed by `(workflow_id, sha256(yaml_config)[:16])`; `_config_hash()` at lines 104–106 performs the hash
- `src/codemie/service/workflow_service.py` — `create_workflow_execution()` at line 266 is the **only current write site** for `parent_exec.active_sub_execution_id`; it sets it to the new child `execution_id` as a DB side effect, then calls `parent_exec.save()`
- `src/codemie/core/workflow_models/workflow_execution.py` — `WorkflowExecution` SQLModel; declares `active_sub_execution_id: Optional[str]` at line 174 (indexed, default `None`)
- `src/codemie/core/workflow_models/workflow_config.py` — `WorkflowConfigBase` at line 70; `yaml_config: Optional[str]` declared at line 96 (TEXT column, default `None`)
- `tests/codemie/workflows/test_workflow_state_transitions.py` — failing test `test_tc_wst_010_sub_workflow_interrupt_and_resume` at line 513
- `tests/codemie/workflows/nodes/test_sub_workflow_node.py` — dedicated `SubWorkflowNode` unit tests with autouse `_patch_workflow_pool` fixture (lines 32–36); contains `test_execute_sets_active_sub_execution_id_before_stream` as the canonical reference for how to assert this behaviour

### Architecture and Layers Affected

- **Node layer** (`sub_workflow_node.py`): `execute()` create path needs 2 explicit lines added — set `parent_exec.active_sub_execution_id` and call `parent_exec.save()` before `child_executor.stream()`
- **Test layer** (`test_workflow_state_transitions.py`): Phase 1 context manager is missing a patch for `workflow_pool`; Phase 2 is missing `return_value` configuration for `find_last_execution_state_output`
- **Pool/infrastructure layer** (`workflow_pool.py`): not changed; root cause of TypeError is test gap, not production bug in this file
- **Service layer** (`workflow_service.py`): not changed; the existing write in `create_workflow_execution()` remains, but the node must no longer rely on it as the sole mechanism

### Integration Points

- `sub_workflow_node.py` → `workflow_pool` singleton (module-level import: `from codemie.service.workflow_pool import workflow_pool`): `acquire()` called on create path; must be patched in unit tests
- `sub_workflow_node.py` → `WorkflowService.create_workflow_execution()`: returns `child_exec`; in production also sets `parent_exec.active_sub_execution_id` as side effect; in unit tests the mock suppresses this side effect
- `sub_workflow_node.py` → `WorkflowService.find_last_execution_state_output()`: called on terminal path to produce return value; not configured in Phase 2 of the failing test
- `sub_workflow_node.py` → `WorkflowExecutor`: instantiated with `resume_execution=True` on resume path

### Patterns and Conventions

- `test_sub_workflow_node.py` defines an autouse fixture `_patch_workflow_pool` that patches `codemie.workflows.nodes.sub_workflow_node.workflow_pool` for every test in that module — this is the established pattern to prevent pool compilation in unit tests; the failing test in `test_workflow_state_transitions.py` does not follow this pattern
- `Mock(spec=WorkflowConfig)` with explicit field assignment is the established pattern for config mocks in `test_workflow_state_transitions.py`; `MagicMock(max_nesting_level=None)` without setting `yaml_config` is the root cause of Bug 1
- `find_workflow_execution_by_id.side_effect = [...]` ordered-list pattern is used throughout both test files for multi-call sequencing

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/workflows/langgraph-workflows.md` — documents `WorkflowExecutor` and `StateGraph` at a high level; no sub-workflow or interrupt/resume specifics
- `.ai-run/guides/testing/testing-patterns.md` — documents test directory conventions and the seam-test rule (each callsite needs a boundary-observing test per branch); directly applicable here
- `.ai-run/guides/testing/testing-service-patterns.md` — documents mocking of service/repository boundaries with `pytest-asyncio` for async paths; applicable since `execute()` is async

### Architectural Decisions

- Sub-workflow interrupt/resume design is encoded exclusively in source and migrations; no ADR or design doc found
- Migration `u2v3w4x5y6z7_add_sub_workflow_execution_lineage.py`: adds `parent_execution_id` and `active_sub_execution_id` columns (nullable String, indexed) to `workflow_executions`
- Migration `t1u2v3w4x5z7_add_sub_workflow_pool_config.py`: adds `pool_config` (JSONB) and `max_nesting_level` (Integer) to `workflows`
- Comment at `sub_workflow_node.py:133`: "Do NOT clear active_sub_execution_id — parent needs it when it is resumed" — confirms the field must persist across interrupt boundaries

### Derived Conventions

- The node layer must not rely on service-layer side effects to mutate objects it passes to downstream calls; state visible to the test must be set explicitly in the node
- `workflow_pool` must always be patched in sub-workflow unit tests; the pattern is established in `test_sub_workflow_node.py`

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/workflows/nodes/test_sub_workflow_node.py` — dedicated unit tests T1–T11 plus `TestSubWorkflowNodePoolIntegration`; covers create path, resume path, interrupt detection, `active_sub_execution_id` lifecycle, and pool interaction; all use the autouse `_patch_workflow_pool` fixture
- `tests/codemie/workflows/test_workflow_state_transitions.py` — integration-style state-transition tests; covers sequential, conditional, switch, parallel, map-reduce, and (intended) sub-workflow interrupt/resume scenarios; currently failing on the sub-workflow test

### Testing Framework and Patterns

- pytest with `unittest.mock` (`MagicMock`, `Mock`, `patch`)
- `pytest-asyncio` for async node execution
- Autouse fixture pattern for pool patching (`_patch_workflow_pool` in `test_sub_workflow_node.py`)
- `find_workflow_execution_by_id.side_effect = [...]` for multi-call sequencing
- `pytest.raises(InterruptedException)` for interrupt path assertions

### Coverage Gaps

- `test_workflow_state_transitions.py` does not patch `workflow_pool` — any test in that file that exercises the sub-workflow create path will reach real pool code
- `test_workflow_state_transitions.py::test_tc_wst_010` Phase 2 does not configure `find_last_execution_state_output.return_value` — result assertion will fail even after Bugs 1 and 2 are fixed

---

## 5. Configuration and Environment

### Environment Variables

- `ENABLE_SUB_WORKFLOW_NODE: bool = False` — feature flag; sub-workflow node disabled by default (set True in test environment as needed)
- `WORKFLOW_POOL_ENABLED: bool = True` — master switch; when True, `acquire()` is always reached, making the missing patch in the test fatal
- `WORKFLOW_POOL_MAX_AGE_SECONDS: int = 3600`
- `SUBWORKFLOW_POOL_MAX_SIZE: int = 5`
- `SUBWORKFLOW_POOL_WARMUP_INTERVAL_SECONDS: int = 60`

### Configuration Files

- `src/codemie/configs/config.py` — declares all env vars above via Pydantic settings

### Feature Flags and Deployment Concerns

- `WORKFLOW_POOL_ENABLED=True` by default means test code that does not patch `workflow_pool` will always hit real pool logic, including `_config_hash()`; this is the environmental condition that makes Bug 1 fatal regardless of other test isolation

---

## 6. Risk Indicators

- **Bug 1 (test gap — TypeError, root cause of reported failure)**: `test_tc_wst_010` Phase 1 does not patch `codemie.workflows.nodes.sub_workflow_node.workflow_pool`. With `WORKFLOW_POOL_ENABLED=True` (default), `acquire()` is always called, reaching `_config_hash()`. `child_config = MagicMock(max_nesting_level=None)` leaves `child_config.yaml_config` as an auto-created child MagicMock (truthy). In `_config_hash`: `getattr(workflow_config, "yaml_config", None) or ""` — the MagicMock is truthy so `or ""` never fires; `hashlib.sha256(MagicMock().encode())` raises `TypeError: object supporting the buffer API required`. **Fix**: add `patch("codemie.workflows.nodes.sub_workflow_node.workflow_pool")` to the Phase 1 `with patch(...)` block — identical to the autouse pattern in `test_sub_workflow_node.py`.

- **Bug 2 (production code gap — assertion failure)**: `sub_workflow_node.execute()` create path never directly sets `parent_exec.active_sub_execution_id` or calls `parent_exec.save()`. Setting this field is currently a side effect of `WorkflowService.create_workflow_execution()` at `workflow_service.py:266`. Since the test mocks the entire service, the side effect never occurs, and `parent_exec_p1.active_sub_execution_id` remains a bare MagicMock attribute (not `"child-exec-id"`). Test assertions at lines 573–574 fail. **Fix**: in `sub_workflow_node.py`, after the `child_exec = WorkflowService.create_workflow_execution(...)` call, add explicit assignment and save:
  ```python
  parent_exec.active_sub_execution_id = child_exec.execution_id
  await parent_exec.save()
  ```
  before calling `child_executor.stream()`. The service-layer write at `workflow_service.py:266` can remain (idempotent when production code runs in full), but the node must not rely on it.

- **Bug 3 (test gap — assertion failure)**: Phase 2 asserts `result == "done"` but `mock_svc.find_last_execution_state_output` has no `return_value` configured. It returns a MagicMock (truthy), so `WorkflowService.find_last_execution_state_output(...) or ""` returns the MagicMock, not `"done"`. **Fix**: add `mock_svc.find_last_execution_state_output.return_value = "done"` to the Phase 2 setup block.

- **No conftest fixtures for sub-workflow tests in `tests/codemie/workflows/`**: each test must configure its own patches; any new test that omits the `workflow_pool` patch will silently reach real pool code.

- **`active_sub_execution_id` write responsibility split across two layers**: `workflow_service.py:266` sets it, and `sub_workflow_node.py:142` clears it. After Bug 2 fix, the node will also set it. This split is a maintenance risk — future refactors of either file must stay aware of the other.

---

## 7. Summary for Complexity Assessment

The failing test exposes three distinct defects in a single test function (`test_tc_wst_010_sub_workflow_interrupt_and_resume`). Two are test gaps (missing mock patches, missing return-value configuration) and one is a production code gap (node does not explicitly persist `active_sub_execution_id` on the parent execution before streaming). The layers affected are narrow: the node layer (`sub_workflow_node.py`, 2–4 lines added) and the test file (`test_workflow_state_transitions.py`, 3–5 lines added across Phase 1 and Phase 2 setup). No model changes, no migrations, no API changes are needed.

The fix follows established patterns exactly: `test_sub_workflow_node.py` already demonstrates the correct `workflow_pool` patch approach via its autouse `_patch_workflow_pool` fixture (lines 32–36), and `test_execute_sets_active_sub_execution_id_before_stream` in that file demonstrates the canonical assertion style for this behaviour. The production code change mirrors the teardown pattern already present in `sub_workflow_node.py:142` (clear on terminal state), applying the same explicit assignment pattern to the setup path.

Test coverage posture for the affected domain is healthy: `test_sub_workflow_node.py` provides thorough unit coverage of `SubWorkflowNode` and the pool interaction with all paths covered. The failing test is an integration-style scenario that fell through because it was written without the pool-patch convention established in the dedicated unit test file. Key risk factors: (1) `WORKFLOW_POOL_ENABLED=True` by default means any test without the patch is one config flip away from hitting real pool code; (2) `active_sub_execution_id` write responsibility is currently split across service and node layers — the fix consolidates the node's responsibility but the service write at `workflow_service.py:266` should be reviewed for whether it is still needed or should be removed to avoid dual writes.
