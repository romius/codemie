# Technical Research

**Task**: langgraph workflow execution sub-workflow node interrupt resume api endpoint
**Generated**: 2026-07-30T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Implement the execution logic for the sub-workflow node feature in EPMCDME-11609. Increment 1 (foundation) is already merged on branch EPMCDME-11609_sub-workflow-node and includes: WorkflowPoolConfig model, WorkflowState.workflow_id + input_mapping discriminant fields, WorkflowConfigBase.pool_config + max_nesting_level columns, WorkflowExecution.parent_execution_id + active_sub_execution_id lineage fields, YAML schema extensions, config vars (ENABLE_SUB_WORKFLOW_NODE, WORKFLOW_MAX_NESTING_DEPTH, WORKFLOW_POOL_ENABLED, WORKFLOW_POOL_MAX_AGE_SECONDS), and sub-workflow resource availability validation. Increment 2 must implement: (1) LangGraph sub-workflow node handler that suspends the parent workflow execution, starts a child WorkflowExecution, waits for completion, maps outputs back, and resumes the parent; (2) interrupt/resume wiring so the parent executor knows to poll or be notified when the child finishes; (3) an API endpoint that lists workflows selectable as sub-workflows in the builder (accessible by the current user, excluding the current workflow to prevent self-reference); (4) integration of the ENABLE_SUB_WORKFLOW_NODE feature flag into the execution path so the sub-workflow node type is only active when the flag is enabled.

---

## 2. Codebase Findings

### Existing Implementations

**Core Dispatch and Graph Builder:**
- `src/codemie/workflows/workflow.py` — `WorkflowExecutor` class; `initialize_node()` at line 480 dispatches on `state.assistant_id` / `state.custom_node_id` / `state.tool_id`; falls to `raise ValueError` if none match; the `elif state.workflow_id:` branch does not yet exist; `build_workflow()` calls `initialize_node()` for every state in the config; `_check_for_interruption()` at line 995 reads LangGraph checkpoint state after streaming and raises `InterruptedException`; resume branch at line 1032 sets `inputs = None` to replay from last checkpoint

**Node Infrastructure:**
- `src/codemie/workflows/nodes/base_node.py` — `BaseNode` ABC: full lifecycle hook chain `__call__` → `before_execution` → `execute` → `post_process_output` → `finalize_and_update_state` → `after_execution`; `_is_execution_aborted()` polls DB; handles both plain dict and `Command` return types from `execute()`; all new nodes must subclass this
- `src/codemie/workflows/nodes/agent_node.py` — `AgentNode(BaseNode)`: canonical stateful node pattern
- `src/codemie/workflows/nodes/tool_node.py` — `ToolNode(BaseNode)`: synchronous-call node; resolves config from `WorkflowState.tool_id`; also contains `if not config.MCP_CONNECT_ENABLED` guard at line 124 — direct pattern reference for `ENABLE_SUB_WORKFLOW_NODE` flag usage
- `src/codemie/workflows/nodes/bedrock_flow_node.py` — `BedrockFlowNode(BaseNode)`: **nearest existing analogue to SubWorkflowNode**; resolves a remote service via a config-provided ID, calls an external executor, maps output back to state — study this file first when implementing `SubWorkflowNode`
- `src/codemie/workflows/nodes/__init__.py` — exports all node classes; `SubWorkflowNode` must be added here

**Data Models (all Increment 1 — already on branch):**
- `src/codemie/core/workflow_models/workflow_models.py` — `WorkflowState` at line 370: `workflow_id: Optional[str]` (line 384), `input_mapping: Optional[dict[str, str]]` (line 385); `check_state_type` validator at line 409 enforces exactly one of the four discriminant fields; `WorkflowPoolConfig` model at line 361
- `src/codemie/core/workflow_models/workflow_execution.py` — `WorkflowExecution` SQLModel table; `parent_execution_id: Optional[str]` (line 173, indexed), `active_sub_execution_id: Optional[str]` (line 174, indexed); `WorkflowExecutionStatusEnum` includes `INTERRUPTED`, `SUCCEEDED`, `FAILED`, `ABORTED`
- `src/codemie/core/workflow_models/workflow_config.py` — `WorkflowConfigBase`: `pool_config: Optional[WorkflowPoolConfig]` (line 115), `max_nesting_level: Optional[int]` (line 118); `parse_execution_config()` reads both from YAML
- `src/codemie/workflows/constants.py` — all state-machine key strings (`CONTEXT_STORE_VARIABLE`, `MESSAGES_VARIABLE`, `NEXT_KEY`, `END_NODE`, etc.)
- `src/codemie/core/exceptions.py` — `InterruptedException` at line 66: `message` and `interrupted_state` attributes; raised by `_check_for_interruption()` and caught by the streaming runner
- `src/codemie/workflows/models.py` — `AgentMessages` TypedDict: the state schema fed to `StateGraph(AgentMessages)` and all node `__call__` signatures

**Service Layer:**
- `src/codemie/service/workflow_execution/workflow_execution_service.py` — `WorkflowExecutionService`: `interrupt(interrupted_state)` sets `INTERRUPTED` on the `WorkflowExecution` row and emits a streaming event (line 136); `resume_states()` transitions all `INTERRUPTED` states back to `SUCCEEDED` before re-entering the graph (line 161); `start_state()` / `finish_state()` track node-level execution; `finish()` sets `SUCCEEDED`; `fail()` sets `FAILED`
- `src/codemie/service/workflow_service.py` — `WorkflowService`: `create_workflow_execution(workflow_config, user, user_input, file_names, conversation_id)` creates a `WorkflowExecution` row with a new UUID `execution_id` — does **not** currently accept `parent_execution_id` or set `active_sub_execution_id`; Increment 2 must add these kwargs; `find_workflow_execution_by_id(execution_id)` for DB lookup; `get_workflow(workflow_id, user)` for config resolution
- `src/codemie/service/workflow_config/workflow_config_index_service.py` — `WorkflowConfigIndexService` with `VisibleToUserModifierPostgres` query modifier: access filter is `project.in_(user.project_names) AND shared=True OR project.in_(user.admin_project_names) OR created_by.user_id == user.id`; used by the existing `GET /workflows` list endpoint

**Validation (Increment 1 — already on branch):**
- `src/codemie/workflows/validation/resources.py` — `_validate_sub_workflow_availability()` at line 616 already checks self-reference and resolves `workflow_id` via `WorkflowService().get_workflow(workflow_id, user)`; no `ENABLE_SUB_WORKFLOW_NODE` guard yet — Increment 2 must add the flag check here too

**API Routers:**
- `src/codemie/rest_api/routers/workflow_executions.py` — `POST /workflows/{workflow_id}/executions` creates executor + `background_tasks.add_task(workflow.stream)`; `PUT .../resume` at line 390 resumes a paused execution; Increment 2 must modify the resume endpoint to detect `active_sub_execution_id` on the parent and route accordingly
- `src/codemie/rest_api/routers/workflow.py` — `GET /workflows` and related CRUD; uses `Depends(authenticate)`, `Ability(user).can(Action.READ, ...)`, `project_access_check`; the new `GET /workflows/selectable` endpoint belongs here

### Architecture and Layers Affected

- **API/Router layer** — `rest_api/routers/workflow.py` (new selectable endpoint), `rest_api/routers/workflow_executions.py` (modified resume logic for sub-execution routing)
- **Service/Business-logic layer** — `service/workflow_service.py` (extend `create_workflow_execution` with lineage params, add selectable listing method, add nesting depth enforcement); `service/workflow_execution/workflow_execution_service.py` (update `active_sub_execution_id` lifecycle: set on child start, clear on child completion)
- **Workflow/Orchestration layer** — `workflows/workflow.py` (new `elif state.workflow_id:` branch in `initialize_node`, feature flag guard); new `workflows/nodes/sub_workflow_node.py` implementing `BaseNode`
- **Repository/Data-access layer** — `WorkflowExecution` table writes: `parent_execution_id`, `active_sub_execution_id` (columns exist, writes not yet wired)
- **Validation layer** — `workflows/validation/resources.py` (add `ENABLE_SUB_WORKFLOW_NODE` short-circuit)

### Integration Points

**Internal:**
- `WorkflowExecutor` → `WorkflowExecutionService` (interrupt/resume lifecycle)
- `SubWorkflowNode` → `WorkflowService.create_workflow_execution()` (create child `WorkflowExecution` with `parent_execution_id`)
- `SubWorkflowNode` → `WorkflowExecutor` (instantiate and run child workflow graph)
- Resume endpoint → `WorkflowExecution.active_sub_execution_id` (route to child if set)
- Nesting depth check → `WorkflowConfigBase.max_nesting_level` + `config.WORKFLOW_MAX_NESTING_DEPTH`
- Input mapping → `WorkflowState.input_mapping` Jinja rendering against parent `context_store` (Increment 1 model; Increment 2 executes the render)
- Output isolation → `workflow_update_output_service.py` (only sub-workflow's designated output key propagates to parent `context_store`)

**External:**
- LangGraph `StateGraph` with `CheckpointSaver`: each execution gets its own `execution_id` as `thread_id`; child workflow must use child `execution_id` as its own `thread_id` to avoid namespace collision
- LangGraph `interrupt_before` list: the `workflow_id` state node name is placed in `interrupt_before` to trigger parent suspension at the correct graph position
- Interrupt bridge key: `__sub_wf_exec_id__<node_name>` written into LangGraph channel state to carry the child `execution_id` across checkpoint boundary; read back on parent resume

### Patterns and Conventions

- **Node subclass pattern**: all node handlers subclass `BaseNode` in `src/codemie/workflows/nodes/`; new `SubWorkflowNode` class goes in `src/codemie/workflows/nodes/sub_workflow_node.py`; exported from `nodes/__init__.py`
- **Node dispatch registration**: `initialize_node()` in `workflow.py` adds `elif state.workflow_id: node = self.init_sub_workflow_node(state, ...)` after the `elif state.tool_id:` block at line 511–513, before the bare `else: raise`
- **Feature flag inline guard**: `if not config.ENABLE_SUB_WORKFLOW_NODE: raise FeatureDisabledError(...)` inside `initialize_node()` (or inside `SubWorkflowNode.execute()`) — matches the pattern at `tool_node.py:124` and `assistant_service.py:576`
- **Interrupt/resume flow**: parent `WorkflowExecutor.build_workflow()` adds the `workflow_id` state node name to `interrupt_before`; after the graph streams to that node, `_check_for_interruption()` raises `InterruptedException`; `WorkflowExecutionService.interrupt()` persists `INTERRUPTED` status; resume endpoint calls `WorkflowExecutor(resume_execution=True)` with `inputs=None` to replay from checkpoint
- **Access control on list endpoints**: `Depends(authenticate)` → `User`; `WorkflowConfigIndexService` + `VisibleToUserModifierPostgres` for filtered listing; the selectable endpoint must additionally exclude the current workflow by `id`
- **Paginated list endpoint**: `WorkflowConfigIndexService.run(user, filter_by_user, page, per_page)` with `QueryModifier` composition — reuse this for the selectable endpoint
- **Request/response models**: typed Pydantic models go in `src/codemie/rest_api/models/`; note inline TODO at `workflow_models.py:419` to move API models there

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/workflows/langgraph-workflows.md` — primary guide: extend `WorkflowExecutor`/nodes/validation utilities; node behavior in `codemie.workflows.nodes`; `WorkflowExecutor` and `StateGraph` are canonical entry points
- `.ai-run/guides/architecture/layered-architecture.md` — HTTP concerns in routers, business logic in services, persistence in repositories; optional routers feature-gated at app assembly in `main.py`; shared exceptions/constants in `codemie.core`
- `.ai-run/guides/architecture/service-layer-patterns.md` — services coordinate repos/providers; `WorkflowExecutor.create_executor` at `workflow.py:112` is the orchestration boundary
- `.ai-run/guides/api/rest-api-patterns.md` — add FastAPI routers under `rest_api/routers/`; register with `app.include_router`; use existing `authenticate` security dependency
- `.ai-run/guides/api/endpoint-conventions.md` — typed Pydantic request/response models under `rest_api/models/`; delegate non-trivial logic to service layer
- `.ai-run/guides/data/repository-patterns.md` — extend matching repository rather than direct storage access; use central storage factories

### Architectural Decisions

From `docs/superpowers/tasks/2026-07-29-epmcdme-11609-sub-workflow-node/decisions.jsonl` and `spec.md`:

- `workflow_id` is the **fourth exclusive discriminant** on `WorkflowState` — enforced by `check_state_type` validator (Increment 1, already implemented)
- Interrupt bridge key: `__sub_wf_exec_id__<node_name>` written into LangGraph channel state to carry child `execution_id` across checkpoint boundary
- Sub-workflows have **isolated context**: do NOT inherit parent execution context; invoked with fresh, explicit input only
- `input_mapping` overrides `task` for sub-workflow input when present; values rendered as Jinja templates against parent `context_store`
- Nesting depth enforcement belongs at the **service layer**, not inside the node, to keep nodes thin
- Output isolation: only the sub-workflow's explicit designated output key is propagated back to parent `context_store`; handled in `workflow_update_output_service.py`
- Checkpoint isolation: child workflow gets its own `execution_id` as LangGraph `thread_id` — no sharing with parent checkpoint namespace
- `ENABLE_SUB_WORKFLOW_NODE: bool = False` gates dispatch; when `False`, `SubWorkflowNode` execution must be rejected with a clear error
- Per-workflow `max_nesting_level` overrides global `WORKFLOW_MAX_NESTING_DEPTH`; effective depth = `workflow_config.max_nesting_level or config.WORKFLOW_MAX_NESTING_DEPTH`
- Pool is in-memory, per-instance; no distributed pool coordination across horizontal replicas
- Selectable endpoint: `GET /workflows/selectable` (or similar) — returns workflows the calling user can select as sub-workflows, excluding the caller's own workflow

### Derived Conventions

- The `elif state.workflow_id:` branch must be placed after `elif state.tool_id:` at `workflow.py:511–513` and before the bare `else: raise ValueError`
- Sub-workflow selectable listing reuses `WorkflowConfigIndexService` + `VisibleToUserModifierPostgres` with an additional `id != current_workflow_id` filter
- `WorkflowService.create_workflow_execution()` extended with `parent_execution_id: Optional[str] = None`; when set, also writes `active_sub_execution_id` on the parent `WorkflowExecution` row
- Resume routing: if `WorkflowExecution.active_sub_execution_id` is set, the resume endpoint routes to the child; once child completes, `active_sub_execution_id` is cleared and the parent is resumed

### Architectural Decisions

- `workflow_models.py:419` — `# API Models, TODO: move to rest_api module`; any new API response models for sub-workflow listing should go directly in `rest_api/models/`, not in `workflow_models.py`

---

## 4. Testing Landscape

### Existing Coverage

**Increment 1 tests (all present on branch):**
- `tests/codemie/core/workflow_models/test_workflow_models.py` — `WorkflowState` validation, `workflow_id` discriminant mutual-exclusion, `input_mapping`, `WorkflowPoolConfig`, `WorkflowRetryPolicy`
- `tests/codemie/core/workflow_models/test_workflow_config.py` — `WorkflowConfig.parse_execution_config`, `pool_config`, `max_nesting_level`, `WorkflowConfigListResponse`
- `tests/codemie/workflows/test_config_resources_validation.py` — `_validate_sub_workflow_availability` (found/not-found/self-reference/access-denied), full `validate_workflow_config_resources_availability` integration
- `tests/codemie/core/workflow_models/test_workflow_execution.py` — `WorkflowExecution` DB model; `TestWorkflowExecutionLineageFields` covers `parent_execution_id` and `active_sub_execution_id` defaults and assignment

**Pre-existing execution and router tests (valuable references for Increment 2):**
- `tests/codemie/service/workflow_execution/test_workflow_execution_service.py` — `interrupt`, `resume_states`, `start_state`, `_send_interrupted_event`, `mark_authentication_required`; uses patched DB model calls
- `tests/codemie/rest_api/routers/test_workflow_executions.py` — full create/resume/delete/export router coverage; `WorkflowExecutor.create_executor` call-arg assertions
- `tests/codemie/rest_api/routers/test_resume_workflow_execution.py` — resume endpoint (`PUT /v1/workflows/{id}/executions/{id}/resume`), `ResumeWorkflowExecutionRequest`, `AsyncClient`+`ASGITransport` pattern
- `tests/codemie/workflows/test_workflow_state_transitions.py` — sequential/conditional/switch/parallel/iterate/interrupt-resume state transitions; `evaluate_next_candidate`, `get_final_state`
- `tests/codemie/workflows/test_supervisor_workflow_executor.py` — `SupervisorWorkflowExecutor.init_state_graph` and `build_workflow`
- `tests/codemie/workflows/nodes/test_bedrock_flow_node.py` — `BedrockFlowNode` test patterns; closest existing test analogue for `SubWorkflowNode`
- `tests/codemie/service/test_workflow_service.py` — `WorkflowService` get/create/update/delete/execution-create methods
- `tests/codemie/rest_api/routers/test_workflow.py` — workflow CRUD router

### Testing Framework and Patterns

- pytest 8.3.x, pytest-asyncio 0.23.x, pytest-mock 3.14.x, pytest-httpx 0.35.x
- Async tests use `@pytest.mark.asyncio` explicitly; no global `asyncio_mode` in `pytest.ini`
- `testpaths = tests`, `pythonpath = src`, `--import-mode=importlib`
- **Global DB mock**: `tests/conftest.py` patches `PostgresClient.get_engine` with `scope="session", autouse=True`; no real DB in any test
- **`@pytest.fixture` factories**: standalone fixture functions build `WorkflowConfig`, `WorkflowState`, `WorkflowExecution`, `User` objects
- **`unittest.mock.patch` + `MagicMock`**: primary mocking strategy; `@patch(...)` decorator stacking and `with patch(...)` context managers; `mock.spec=WorkflowExecutionService` for typed mocks
- **`pytest-mock` `mocker` fixture**: used alongside bare `unittest.mock`
- **`AsyncClient`+`ASGITransport`** with `headers={'user-id': USER_ID}` for async endpoint tests; rate limiter disabled via `codemie.rest_api.rate_limit.limiter.enabled = False` in `tests/codemie/rest_api/routers/conftest.py`
- **Class-based test grouping**: `class TestInterruptPredecessorState`, `class TestResumeStates`, etc.; service fixture shared across methods via parameter injection (not `self`)

### Coverage Gaps

All ten areas below are greenfield for Increment 2:

1. `SubWorkflowNode` handler class — no `tests/codemie/workflows/nodes/test_sub_workflow_node.py` exists; the `elif state.workflow_id:` dispatch branch in `workflow.py` has zero coverage
2. `WorkflowExecutor.build_workflow` with a `workflow_id` state — existing supervisor tests build graphs with `assistants=[]` only; no test builds a graph containing a `workflow_id`-typed state node
3. Child `WorkflowExecution` creation with `parent_execution_id` — no test covers `WorkflowService.create_workflow_execution()` with the lineage kwargs
4. Parent suspension and LangGraph interrupt on `workflow_id` state — no test covers interrupt wiring for a `workflow_id` state, checkpoint storage, or the resume-after-child-completes flow
5. Output mapping from child back to parent `context_store` — `input_mapping` model is unit-tested; the reverse direction (child output → parent context) has no test
6. `ENABLE_SUB_WORKFLOW_NODE` feature flag gating — defined in `config.py` but consumed nowhere in source; no test validates that `workflow_id` states are rejected when flag is `False`
7. `GET /workflows/selectable` (or equivalent) API endpoint — endpoint does not yet exist; no router test covers it
8. `WorkflowService` selectable listing method — method does not yet exist in `workflow_service.py`; no test in `test_workflow_service.py` covers it
9. `active_sub_execution_id` field lifecycle — model-level write is tested (Increment 1); no service/handler test covers setting it on child start, clearing it on child completion, or using it for resume routing
10. `max_nesting_level` enforcement at runtime — config parsing is tested; no test verifies that the sub-workflow node handler rejects execution when current depth exceeds the effective limit

---

## 5. Configuration and Environment

### Environment Variables

- `ENABLE_SUB_WORKFLOW_NODE: bool = False` (config.py line 170) — master switch; gates the `elif state.workflow_id:` dispatch branch; **zero consumption sites** in source as of Increment 1
- `WORKFLOW_MAX_NESTING_DEPTH: int = 1` (config.py line 171) — global nesting depth ceiling; overridden per-workflow by `WorkflowConfigBase.max_nesting_level`; **zero consumption sites** in source as of Increment 1
- `WORKFLOW_POOL_ENABLED: bool = True` (config.py line 172) — global on/off for pre-instantiation pool; pool implementation not yet wired; **zero consumption sites** in source as of Increment 1
- `WORKFLOW_POOL_MAX_AGE_SECONDS: int = 3600` (config.py line 173) — pool entry TTL; **zero consumption sites** in source as of Increment 1

### Configuration Files

- `src/codemie/configs/config.py` — single `pydantic-settings` `BaseSettings` class; governs all env-var-backed runtime configuration; loaded once at module level as the `config` singleton; re-exported from `codemie.configs`; all `ENABLE_*` flags are consumed via direct attribute access: `if config.ENABLE_SUB_WORKFLOW_NODE:`
- `config/customer/customer-config.yaml` — line 269: customer-config entry for the sub-workflow feature flag (Increment 1)
- `deploy-templates/values.yaml` — Helm chart values; env vars injected via `extraEnv`/`customEnv` lists; secrets via Kubernetes `secretKeyRef`; the four new env vars are **not yet present** in this file

### Feature Flags and Deployment Concerns

- **Flag consumption pattern**: direct inline `if config.ENABLE_SUB_WORKFLOW_NODE:` / `if not config.ENABLE_SUB_WORKFLOW_NODE:` — no DI wrapper or `is_enabled()` helper; matches `tool_node.py:124` (`if not config.MCP_CONNECT_ENABLED:`) and `assistant_service.py:576`
- **Four new env vars absent from `deploy-templates/values.yaml`**: must be added as plain-value entries before the feature is enabled in any deployed environment
- **`WORKFLOW_POOL_ENABLED` defaults `True`**: if pool instantiation logic is not wired by the time the branch is deployed, this default could trigger unexpected paths; consider defaulting to `False` until the pool is fully integrated
- **Alembic migrations from Increment 1 must be applied first**: migrations `t1u2v3w4x5y6` and `u2v3w4x5y6z7` add `pool_config` (JSONB) and `max_nesting_level` (Integer) to `workflows`, and `parent_execution_id` / `active_sub_execution_id` (String, indexed) to `workflow_executions`; any Increment 2 migration must chain from the Increment 1 tail revision
- **Secrets management**: HashiCorp Vault used only for KMS/encryption; the four new vars are plain booleans/integers and require no secret management

---

## 6. Risk Indicators

- **`elif state.workflow_id:` branch missing in `workflow.py:505–515`**: any `WorkflowState` with only `workflow_id` set currently falls to `raise ValueError`; this is the primary execution blocker for Increment 2 — it is the first file to change
- **All four new config vars unconsumed**: `ENABLE_SUB_WORKFLOW_NODE`, `WORKFLOW_MAX_NESTING_DEPTH`, `WORKFLOW_POOL_ENABLED`, `WORKFLOW_POOL_MAX_AGE_SECONDS` are defined in `config.py` but have zero call sites in source; Increment 2 must wire all four
- **`validation/resources.py` has no `ENABLE_SUB_WORKFLOW_NODE` guard**: `_validate_sub_workflow_availability()` already runs on every `validate_workflow_config_resources_availability` call; when the flag is `False`, this function should short-circuit early to avoid spurious errors
- **LangGraph checkpoint thread-ID isolation is critical**: parent and child executions must each use their own `execution_id` as LangGraph `thread_id`; sharing a thread_id would corrupt checkpoints; no existing test validates this isolation
- **Resume routing gap**: the existing `PUT .../resume` endpoint has no logic to detect `active_sub_execution_id`; routing a resume to the parent when the child is still running (or vice versa) will corrupt execution state; this coupling between the resume endpoint and the lineage fields is a novel control-flow that has no existing test coverage
- **No existing polling or push-notification mechanism for child completion**: the task description says the parent must "wait for completion" and then "resume"; neither a polling loop nor a NATS-based notification pattern for cross-execution completion exists yet; the implementation strategy (synchronous block? polling? event?) is unresolved and is the highest-risk design decision in Increment 2
- **`active_sub_execution_id` lifecycle is entirely untested**: model-level field write is covered by Increment 1; but the full lifecycle (set on child start → clear on child completion → read for resume routing) has no service or integration test
- **`max_nesting_level` runtime enforcement missing**: config parsing is model-tested; no guard at execution time prevents recursive sub-workflow invocations from exceeding the configured depth; must be implemented in the service layer before the node is callable
- **`WORKFLOW_POOL_ENABLED` defaults to `True` without implementation**: pool pre-instantiation code is not yet wired; the `True` default in `config.py` could cause silent no-ops or attribute errors if pool bootstrap is referenced; consider defaulting `False` until pool is wired
- **10 identified test coverage gaps** (see Section 4): Increment 2 introduces new node, new service method, new endpoint, and new resume routing logic — all greenfield; without tests, correctness of the parent/child lifecycle cannot be verified
- **Four new env vars absent from `deploy-templates/values.yaml`**: deployment to any environment will silently use default values (`ENABLE_SUB_WORKFLOW_NODE=False`) until the Helm values file is updated
- **`BedrockFlowNode` is the only implementation analogue**: while it shares some structural similarity (resolves a remote service by ID, runs external logic, maps output), it does not use LangGraph interrupt/resume; the sub-workflow pattern is novel in this codebase

---

## 7. Summary for Complexity Assessment

Increment 2 touches five distinct architectural layers. At the **API/Router layer**, two files change: `rest_api/routers/workflow.py` gains a new `GET /workflows/selectable` endpoint, and `rest_api/routers/workflow_executions.py` gains resume-routing logic that branches on `WorkflowExecution.active_sub_execution_id`. At the **Service layer**, `workflow_service.py` requires a new selectable-listing method, an extended `create_workflow_execution` signature with lineage params, and a new nesting-depth enforcement function; `workflow_execution_service.py` requires `active_sub_execution_id` lifecycle management (set/clear). At the **Workflow/Orchestration layer**, `workflow.py` needs a new `elif state.workflow_id:` dispatch branch with a feature-flag guard, and a new file `workflows/nodes/sub_workflow_node.py` implementing the full `BaseNode` lifecycle for child execution. The **Validation layer** (`validation/resources.py`) needs a one-line `ENABLE_SUB_WORKFLOW_NODE` short-circuit. The **Data-access layer** requires no schema changes (Increment 1 migrations cover all columns) but does require the first runtime writes to `parent_execution_id` and `active_sub_execution_id`. The estimated file change surface is 6–8 modified files and 1 new file (`sub_workflow_node.py`).

The highest technical novelty — and the highest risk — in this increment is the parent suspension and child-completion notification mechanism. The existing interrupt/resume pattern (`_check_for_interruption` → `InterruptedException` → `WorkflowExecutionService.interrupt` → DB `INTERRUPTED` status → resume endpoint → `WorkflowExecutor(resume_execution=True)`) works for human-in-the-loop pauses where a human triggers the resume. For sub-workflow execution, the "human" is replaced by the child workflow completing — but there is currently no push notification or polling bridge from child completion back to the parent executor. This design gap is unresolved and is the decision with the greatest downstream impact on implementation complexity. The interrupt bridge key convention (`__sub_wf_exec_id__<node_name>`) is specified in the Increment 1 decisions, but the mechanism that reads that key and triggers the parent resume is not yet defined.

Test coverage posture for Increment 2 is entirely greenfield: all 10 identified gaps are net-new areas with no existing tests. The Increment 1 foundation is well-tested (model validators, config parsing, resource validation), which means the data layer is reliable. However, the execution layer — node dispatch, child execution start, parent suspension, resume routing, output propagation, nesting enforcement — has no test coverage. The `test_workflow_execution_service.py` and `test_workflow_executions.py` router tests are the closest structural references for Increment 2 tests and should be studied before writing new ones. The `BedrockFlowNode` tests are the closest node-level reference. Given the 10 uncovered areas, the novel cross-execution lifecycle, the unresolved child-completion notification mechanism, and the multi-layer scope, Increment 2 is materially more complex than Increment 1.
