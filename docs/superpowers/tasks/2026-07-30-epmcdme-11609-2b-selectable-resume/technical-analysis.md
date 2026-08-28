# Technical Research

**Task**: workflow selectable resume interrupt sub-workflow
**Generated**: 2026-07-30T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-11609 Increment 2b and 2c: (2b) Add GET /workflows/selectable endpoint that returns workflows the current user can use as sub-workflow targets — filtered by project, active status, and user access; (2c) Interrupt/resume wiring — when a parent workflow is resumed via the resume endpoint and has active_sub_execution_id set, detect the active child and resume it instead of (or in addition to) the parent, so the interrupt/resume flow works correctly across the parent-child boundary. Both increments build on the sub-workflow foundation already on branch EPMCDME-11609_sub-workflow-node.

---

## 2. Codebase Findings

### Existing Implementations

**API/Router layer:**
- `src/codemie/rest_api/routers/workflow.py` — FastAPI router for workflow CRUD (`GET /workflows`, `GET /workflows/id/{id}`, `POST`, `PUT`, `DELETE`); target file for adding `GET /workflows/selectable`
- `src/codemie/rest_api/routers/workflow_executions.py` — all execution endpoints including `PUT /workflows/{id}/executions/{id}/resume` at line 385; the resume handler (around line 414) fetches `execution` but does NOT yet inspect `execution.active_sub_execution_id` — this is the 2c gap

**Service layer:**
- `src/codemie/service/workflow_config/workflow_config_index_service.py` — `WorkflowConfigIndexService`; uses `QueryModifier` strategy pattern with `VisibleToUserModifierPostgres`, `ExcludeAutonomousWorkflowsModifier`, and `MarketplaceScopeModifier`; this is the canonical extension point for the selectable endpoint
- `src/codemie/service/workflow_service.py` — `WorkflowService`; has `create_workflow_execution` (sets `parent_execution_id` and `active_sub_execution_id`), `find_workflow_execution_by_id`, and `get_nesting_depth`
- `src/codemie/service/workflow_execution/workflow_execution_service.py` — `WorkflowExecutionService`; has `interrupt()`, `resume_states()` (transitions INTERRUPTED → SUCCEEDED before re-run), `abort()`, `finish()`; child resume must call `resume_states()` via this service

**Orchestration layer:**
- `src/codemie/workflows/workflow.py` — `WorkflowExecutor`; `on_workflow_start()` handles `resume_execution=True` (sets `inputs=None` for checkpoint replay, calls `resume_states()`); `_check_for_interruption()` raises `InterruptedException`; `_handle_interrupt()` delegates to `WorkflowExecutionService.interrupt()`
- `src/codemie/workflows/nodes/sub_workflow_node.py` — `SubWorkflowNode`; creates child execution via `WorkflowService.create_workflow_execution(parent_execution_id=self.execution_id)`, sets `active_sub_execution_id` on parent, clears it on child completion; gated by `config.ENABLE_SUB_WORKFLOW_NODE`

**Domain models:**
- `src/codemie/core/workflow_models/workflow_execution.py` — `WorkflowExecution` SQLModel; fields `parent_execution_id` (FK to self, indexed) and `active_sub_execution_id` (indexed) added by migration; `WorkflowExecutionStatusEnum`; `ResumeWorkflowExecutionRequest`
- `src/codemie/core/workflow_models/workflow_config.py` — `WorkflowConfigBase` / `WorkflowConfig`; `project`, `shared`, `mode`, `is_global`, `max_nesting_level`; `is_shared_with(user)` is the user-access predicate; `WorkflowConfigListResponse` for list shapes
- `src/codemie/core/workflow_models/workflow_models.py` — `WorkflowState` Pydantic model; `interrupt_before: bool`, `workflow_id: Optional[str]` (fourth exclusive discriminant on state type, enforced by `check_state_type` validator, added in Increment 1)

**Security/RBAC:**
- `src/codemie/core/ability.py` — `Ability` + `Owned` RBAC framework; `can(Action.READ, obj)` is the standard guard; `WorkflowConfig` READ allowed for `SHARED_WITH | OWNED_BY | MANAGED_BY | ADMIN`
- `src/codemie/rest_api/security/user.py` — `User` model; `project_names`, `admin_project_names`, `is_admin`, `has_access_to_application`

**Migrations (already applied on branch):**
- `src/external/alembic/versions/t1u2v3w4x5y6_add_sub_workflow_pool_config.py` — adds `pool_config` (JSONB) and `max_nesting_level` (Integer) to `workflows`
- `src/external/alembic/versions/u2v3w4x5y6z7_add_sub_workflow_execution_lineage.py` — adds `parent_execution_id` and `active_sub_execution_id` to `workflow_executions` with indexes

### Architecture and Layers Affected

| Layer | Components Touched |
|---|---|
| **API / Router** | `routers/workflow.py` — new `GET /workflows/selectable` route; `routers/workflow_executions.py` — modify resume handler to inspect `active_sub_execution_id` |
| **Service / Business Logic** | `WorkflowConfigIndexService` — new `QueryModifier` subclass; `WorkflowService` — optional helper for selectable query; `WorkflowExecutionService` — child resume path via `resume_states()` |
| **Orchestration / Execution** | `WorkflowExecutor` — `create_executor` may need routing logic to build child executor on resume |
| **Response Models** | New `WorkflowSelectableResponse` Pydantic model in `src/codemie/rest_api/models/` (not in `workflow_models.py` per recorded decision) |
| **DB / Persistence** | No new migrations required; both required columns are already present |

### Integration Points

**Internal dependencies for 2b:**
- `routers/workflow.py` → `WorkflowConfigIndexService` → `WorkflowConfig` ORM
- New modifier reads `WorkflowConfig.project`, `WorkflowConfig.shared`, `WorkflowConfig.is_global` and possibly an `active`/`is_active` field — confirm field name against `WorkflowConfig` before implementing
- `VisibleToUserModifierPostgres` already encapsulates `project.in_(user.project_names) AND shared=True OR admin_project_names OR created_by == user.id` — reuse directly

**Internal dependencies for 2c:**
- `routers/workflow_executions.py` → `WorkflowService.find_workflow_execution_by_id` (fetch parent execution, inspect `active_sub_execution_id`)
- If child is active: `WorkflowService.find_workflow_execution_by_id(child_id)` → `WorkflowExecutor.create_executor(execution_id=child_id, resume_execution=True)`
- Child completion must: call `WorkflowExecutionService.finish()` or let `SubWorkflowNode` clear `active_sub_execution_id` naturally, then re-enter parent from LangGraph checkpoint
- Interrupt bridge key `__sub_wf_exec_id__<node_name>` (written into LangGraph channel state in Increment 1) carries child `execution_id` across checkpoint boundary — 2c resume reads this key to locate the child

**External integrations:**
- LangGraph `CheckpointSaver` — parent and child each have their own `thread_id` (= `execution_id`); no checkpoint namespace is shared
- `codemie-enterprise` observability provider — used in `create_executor` for Langfuse/Phoenix trace context; child executor must also pass this

### Patterns and Conventions

- **`QueryModifier` strategy**: add `ExcludeCurrentWorkflowModifier(workflow_id: str)` and an active-status modifier following `ExcludeAutonomousWorkflowsModifier` as the template; pass all modifiers to `WorkflowConfigIndexService.run()`
- **`Ability(user).can(Action.READ, obj)` guard** on every endpoint before any business logic
- **`WorkflowConfigListResponse` minimal shape** for list endpoints — omit heavy YAML/states fields
- **Pagination params**: `page: int = 0`, `per_page: int = 10` — same as `GET /workflows`
- **Feature flag inline guard**: `if not config.ENABLE_SUB_WORKFLOW_NODE: raise FeatureDisabledError(...)` — matches pattern at `tool_node.py:124`
- **`filter_by_user=False`** when calling `WorkflowConfigIndexService` for the selectable endpoint — shared/project workflows must be included, not just user-owned ones
- **`resume_execution=True` + `inputs=None`** instructs `WorkflowExecutor.on_workflow_start()` to replay from last LangGraph checkpoint; child must use the same pattern
- **`WorkflowExecutionService.resume_states()`** must be called on the child's execution service before creating the child executor

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/workflows/langgraph-workflows.md` — extend `WorkflowExecutor` and node classes only; node behavior belongs in `codemie.workflows.nodes`; avoid separate graph execution paths
- `.ai-run/guides/api/rest-api-patterns.md` — add routers under `src/codemie/rest_api/routers/`; use `authenticate` dependency; raise shared exceptions (`ValidationException`, `ExtendedHTTPException`)
- `.ai-run/guides/api/endpoint-conventions.md` — typed Pydantic request/response models in `src/codemie/rest_api/models/`; router validates only, service orchestrates
- `.ai-run/guides/architecture/layered-architecture.md` — HTTP → service → repository; register optional routers with feature gates at `main.py` assembly
- `.ai-run/guides/architecture/service-layer-patterns.md` — services coordinate repos/providers; `WorkflowExecutor.create_executor` is the orchestration boundary
- `.ai-run/guides/data/repository-patterns.md` — extend existing repository; return domain entities
- `.ai-run/guides/data/database-patterns.md` — SQLModel/SQLAlchemy expressions only; Alembic migrations in `src/external/alembic/versions/`

Existing task docs on the branch:
- `docs/superpowers/tasks/2026-07-29-epmcdme-11609-sub-workflow-node/spec.md` — Increment 1 spec; defines interrupt bridge key, `workflow_id` state discriminant, input_mapping rendering, and isolated child context decisions
- `docs/superpowers/tasks/2026-07-30-epmcdme-11609-sub-workflow-exec/technical-analysis.md` — prior research for an adjacent increment

### Architectural Decisions

- **`workflow_id` is the fourth exclusive discriminant on `WorkflowState`** — enforced by `check_state_type` validator; already implemented in Increment 1; do not add a fifth type without updating the validator
- **Interrupt bridge key `__sub_wf_exec_id__<node_name>`** written into LangGraph channel state carries child `execution_id` across checkpoint boundary; 2c resume must read this key from parent execution state to locate the child
- **Isolated child context** — child does NOT inherit parent execution context; invoked with fresh, explicit input; child gets its own `execution_id` as LangGraph `thread_id`
- **Nesting depth enforcement belongs in the service layer** (`WorkflowService.get_nesting_depth`), not inside the node
- **Selectable endpoint must exclude self-reference** — filter out the calling workflow by `id` to prevent infinite nesting loops; mirrors `_validate_sub_workflow_availability` logic
- **New API response models go in `rest_api/models/`** — NOT in `workflow_models.py` (recorded as TODO at `workflow_models.py:419`)
- **2a used synchronous blocking** (`child_executor.stream()` blocks in-thread) rather than interrupt-based approach — 2c resume wiring must align with this; child resume therefore means running the child executor synchronously from within the parent resume call

### Derived Conventions

- All `GET /workflows/*` list endpoints in `workflow.py` use `WorkflowConfigIndexService` with modifier composition — selectable endpoint must follow this pattern, not bypass it with direct ORM queries
- `Ability.can()` checks happen at the router level immediately after authentication; service methods do not repeat access checks
- `ExcludeAutonomousWorkflowsModifier` restricts to `WorkflowMode.SEQUENTIAL` — include this in the selectable modifier list to prevent recursive autonomous workflows

### TODOs

- `src/codemie/core/workflow_models/workflow_models.py:419` — `# API Models, TODO: move to rest_api module` — confirms new selectable response model belongs in `rest_api/models/`

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/workflows/nodes/test_sub_workflow_node.py` — `SubWorkflowNode` unit tests: feature flag disabled, nesting depth exceeded, Jinja input_mapping rendering, context_store passthrough, happy-path (clears `active_sub_execution_id`), child failure propagation
- `tests/codemie/rest_api/routers/test_resume_workflow_execution.py` — resume router: request model field validation, `file_names` forwarding, default empty list
- `tests/codemie/rest_api/routers/test_workflow_executions.py` — execution router: create/access-denied/500, export, output update, delete, resume with/without user_input, session_id, tags, `append_user_message_on_resume`
- `tests/codemie/workflows/test_workflow_resume_input.py` — `WorkflowExecutor._inject_resume_input`: injects HumanMessage, no-ops on empty/None, merges JSON input into `context_store`
- `tests/codemie/service/test_workflow_service.py` — WorkflowService: YAML parsing, execution CRUD, nesting depth, `active_sub_execution_id` set on parent at child creation, `append_user_message_on_resume`
- `tests/codemie/service/workflow_execution/test_workflow_execution_service.py` — `_interrupt_predecessor_state`, `resume_states` (INTERRUPTED → SUCCEEDED), `start_state`, `_send_interrupted_event`
- `tests/codemie/core/workflow_models/test_workflow_execution.py` (lines 265-294) — lineage field defaults (`parent_execution_id`, `active_sub_execution_id`)
- `tests/codemie/rest_api/routers/test_workflow.py` — workflow CRUD router: list, get-by-id, create, update, delete, project access checks

### Testing Framework and Patterns

- pytest 8.3.x, pytest-asyncio 0.23.x, pytest-mock 3.14.x, pytest-httpx 0.35.x
- `pytest.ini`: `pythonpath = src`, `testpaths = tests`, `--import-mode=importlib`
- Router tests: `httpx.AsyncClient` + `ASGITransport(app=app)` + `@pytest.mark.asyncio`
- Mocking: `unittest.mock.patch` (decorator or context manager) and `MagicMock`; `mocker` fixture (pytest-mock) for inline patching
- Service-layer tests: `patch.object(Model, 'save'/'update'/'delete')` to avoid DB I/O
- `conftest.py` at `tests/codemie/rest_api/routers/conftest.py` provides `_inject_request_uuid` autouse fixture and disables the rate limiter globally
- `patch` targets use full dotted module paths (e.g., `codemie.rest_api.routers.workflow_executions.WorkflowService`)
- `@pytest.mark.parametrize` used extensively for status/field variant coverage

### Coverage Gaps

1. **`GET /workflows/selectable`** — zero tests anywhere; endpoint, service method, and modifier do not yet exist; full new test file needed
2. **Resume router — parent-child delegation (2c)** — no tests for: detecting `active_sub_execution_id` on resume, routing to child executor, child-already-SUCCEEDED edge case, child-FAILED edge case
3. **Cross-boundary interrupt propagation** — no tests for parent surfacing or handling an interrupt that originated in a child sub-workflow
4. **`WorkflowExecutor` child routing** — no tests for the code path that would build a child executor (or dual executor) instead of/in addition to the parent on resume

---

## 5. Configuration and Environment

### Environment Variables

- `ENABLE_SUB_WORKFLOW_NODE` (default: `False` in `values.yaml`, `False` in code) — feature gate; both `SubWorkflowNode.execute()` and `WorkflowExecutor` graph-build raise `FeatureDisabledError` if false; must be `true` for 2b and 2c to function
- `WORKFLOW_MAX_NESTING_DEPTH` (default: `1`) — global ceiling; `SubWorkflowNode` uses per-workflow `max_nesting_level` first, falls back to this
- `WORKFLOW_POOL_ENABLED` (default: `True` in code, `false` in `values.yaml`) — pool is off in Helm-deployed environments; affects child-workflow startup latency under 2c inline `stream()` call
- `WORKFLOW_POOL_MAX_AGE_SECONDS` (default: `3600`) — pool instance eviction age
- `WORKFLOW_MAX_CONCURRENCY` (default: `5`) — per-workflow concurrency cap
- `WORKFLOW_DEFAULT_CONCURRENCY` (default: `2`) — default concurrency for workflows without explicit value

### Configuration Files

- `src/codemie/configs/config.py` — central `BaseSettings`; all workflow execution limits and feature flags declared here
- `deploy-templates/values.yaml` — Helm chart values; lines 57-64 declare the four sub-workflow env vars with deployment-time defaults; `ENABLE_SUB_WORKFLOW_NODE` is hardcoded `"false"` here

### Feature Flags and Deployment Concerns

- `ENABLE_SUB_WORKFLOW_NODE` must be explicitly set to `"true"` in any environment where 2b or 2c is to be exercised; it is off by default in both dev and Helm deployments
- `WORKFLOW_POOL_ENABLED` default mismatch (code: `True`, `values.yaml`: `false`) — pool is effectively disabled in Helm-deployed environments; this is a pre-existing inconsistency flagged in the Increment 1 spec as a risk
- Both migrations (`t1u2v3w4x5y6` and `u2v3w4x5y6z7`) are already applied on the current branch; deploying without running them would cause `AttributeError` on `WorkflowExecution.active_sub_execution_id` at runtime — but this risk is resolved for this branch
- No new migrations are required for 2b or 2c

---

## 6. Risk Indicators

- **No test coverage for `GET /workflows/selectable`** — endpoint does not exist yet; full test file must be authored as part of implementation
- **No test coverage for parent-child resume routing (2c)** — the most complex scenario (detect child, route resume) has zero test scaffolding; risk of regression in existing resume behavior if the detection logic is misplaced
- **`ENABLE_SUB_WORKFLOW_NODE=false` default** — both 2b and 2c are silently disabled in any environment unless the flag is explicitly enabled; integration tests must set this flag
- **`WORKFLOW_POOL_ENABLED` default mismatch** between code (`True`) and `values.yaml` (`false`) — pre-existing inconsistency; child workflow startup latency under 2c's synchronous `stream()` call may be higher than expected in Helm environments
- **Active status field name unconfirmed** — `WorkflowConfig` must have an `active`/`is_active` field for the selectable endpoint's active-status filter; exact field name must be verified before implementing the modifier (risk: field may not exist yet or may have a different name)
- **Interrupt bridge key is a string convention** — `__sub_wf_exec_id__<node_name>` written into LangGraph channel state; if the key naming convention deviates between Increment 1's `SubWorkflowNode` and 2c's resume handler, the child execution ID will not be found
- **2c design assumption: synchronous blocking** — Increment 1 used synchronous `child_executor.stream()` (blocking in-thread); 2c resume must confirm this is still the execution model; if 2a changed to async/interrupt-based, the resume wiring approach changes significantly
- **Child resume edge cases are unspecified** — ticket does not define behavior when child is already `SUCCEEDED`, `FAILED`, or `ABORTED` when parent is resumed; these must be explicitly handled to avoid incorrect state transitions
- **`ExcludeCurrentWorkflowModifier` excludes self-reference** — documented architectural decision from Increment 1 spec; must be included in selectable modifier list; omitting it would allow infinite self-referencing loops
- **No existing modifier excludes non-active workflows** — `WorkflowConfigIndexService` has `ExcludeAutonomousWorkflowsModifier` but no general active-status modifier; a new modifier must be added or the `active` field filter must be inline; if `active` is not a column on `WorkflowConfig`, schema work would be needed (low probability, but unconfirmed)

---

## 7. Summary for Complexity Assessment

Increment 2b (`GET /workflows/selectable`) is a moderate-complexity, well-pattern-matched addition. The `QueryModifier` strategy in `WorkflowConfigIndexService` is specifically designed for this kind of composable filtering, and three working examples (`VisibleToUserModifierPostgres`, `ExcludeAutonomousWorkflowsModifier`, `MarketplaceScopeModifier`) exist to follow. The implementation surface is 3–5 files: one new route in `routers/workflow.py`, one or two new `QueryModifier` subclasses, one new Pydantic response model in `rest_api/models/`, and one new test file. The primary risk is confirming the exact field name for active-status filtering on `WorkflowConfig` and ensuring `ExcludeCurrentWorkflowModifier` is included. No new migrations are needed.

Increment 2c (interrupt/resume parent-child wiring) is high complexity. It requires modifying the resume handler in `workflow_executions.py` to inspect `active_sub_execution_id`, then constructing a child `WorkflowExecutor` (with `resume_execution=True` and the child's `execution_id` as `thread_id`) and calling `WorkflowExecutionService.resume_states()` on the child before streaming. The interrupt bridge key `__sub_wf_exec_id__<node_name>` (stored in LangGraph channel state by `SubWorkflowNode` in Increment 1) is the lookup mechanism, and 2c must read it reliably. The architecture assumes synchronous blocking (`child_executor.stream()` in-thread); if this assumption is violated, the wiring approach changes. Edge cases — child already SUCCEEDED, FAILED, or ABORTED when the parent is resumed — are unspecified in the ticket and must be explicitly handled. The execution layer touches the orchestration boundary (`WorkflowExecutor`), the service layer (`WorkflowExecutionService`), and the router, spanning 4–6 files.

Test posture across both increments is poor for the new functionality: zero existing coverage for the selectable endpoint, zero for parent-child resume routing, and zero for cross-boundary interrupt propagation. The existing resume and sub-workflow-node tests provide solid patterns to follow, but both increments require new test files authored from scratch. The combination of a greenfield API endpoint (2b, lower risk) and a stateful orchestration wiring task with unspecified edge cases (2c, higher risk) places the overall complexity in the medium-high range.
