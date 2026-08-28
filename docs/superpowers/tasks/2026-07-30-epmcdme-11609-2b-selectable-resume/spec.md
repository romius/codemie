# EPMCDME-11609 Increments 2b + 2c Spec

## Overview

Two focused increments that build on the sub-workflow foundation (Increment 1):

- **2b** — `GET /workflows/selectable`: returns the SEQUENTIAL workflows a user can target when
  building a sub-workflow node in the YAML editor.
- **2c** — Interrupt/resume wiring: fixes a gap in `SubWorkflowNode.execute()` so that when a
  child workflow is interrupted, the parent is also interrupted and carries a reference to the
  child; on parent resume, the existing child is resumed rather than a new one created.

Both increments are guarded by the existing `ENABLE_SUB_WORKFLOW_NODE` feature flag.

---

## Architecture

### 2b — GET /workflows/selectable

A thin new route in `src/codemie/rest_api/routers/workflow.py` that wraps the existing
`WorkflowConfigIndexService.run()` with the same modifiers already used by the workflow list:
`VisibleToUserModifierPostgres` + `ExcludeAutonomousWorkflowsModifier`. No new DB field is
needed. A SEQUENTIAL workflow visible to the user is, by definition, selectable as a
sub-workflow target.

An optional `exclude_id` query parameter causes a new `ExcludeSelfModifier` to be appended to
the modifier list. This prevents the calling workflow from appearing in the dropdown (self-
reference would be caught by validation anyway, but excluding it at query time is cleaner UX).

### 2c — Interrupt/resume wiring in SubWorkflowNode.execute()

All changes live in `SubWorkflowNode.execute()`. The resume endpoint does not change.

Three sub-fixes, in order of execution:

1. **Set `active_sub_execution_id` on create** — immediately after creating the child
   `WorkflowExecution` and before calling `child_executor.stream()`, persist
   `parent_exec.active_sub_execution_id = child_execution.execution_id` so the reference
   survives an interrupt of the parent thread. (Increment 1 omitted this write.)

2. **Handle `INTERRUPTED` child status** — after `child_executor.stream()` returns, check
   `finished_child.overall_status`. If `INTERRUPTED`: do NOT clear `active_sub_execution_id`
   on the parent row, then raise `InterruptedException(message, self.node_name)`. The parent
   executor's existing `except InterruptedException` handler in `stream()` catches this and
   marks the parent as INTERRUPTED.

3. **Resume path on re-entry** — at the top of `execute()`, fetch `parent_exec`. If
   `parent_exec.active_sub_execution_id` is set, take the resume path instead of the create
   path: fetch the child execution by that ID, build a resume executor
   (`resume_execution=True`) forwarding `json.dumps(state_schema.get(CONTEXT_STORE_VARIABLE,
   {}))` as the child's `user_input`, then stream the child. After the child finishes (any
   terminal status), the normal status-dispatch logic runs (INTERRUPTED → re-raise;
   SUCCEEDED/FAILED/ABORTED → clear `active_sub_execution_id` and raise/return as before).

---

## API

### GET /v1/workflows/selectable

**Guard:** returns `403 FeatureDisabledError` when `ENABLE_SUB_WORKFLOW_NODE=false`.

**Query parameters:**

| Param | Type | Default | Description |
|---|---|---|---|
| `exclude_id` | `str` (UUID) | `null` | Workflow ID to exclude from results |
| `page` | `int` | `0` | Page number |
| `per_page` | `int` | `100` | Page size (larger default than the list endpoint — optimised for a dropdown) |

**Response:** `WorkflowListResponse` (minimal fields: `id`, `name`, `project`, `mode`, `shared`,
`description`, `icon_url`). Uses `minimal_response=True` to skip heavy deferred fields
(`yaml_config`, `states`, `tools`, etc.).

**Example:**
```
GET /v1/workflows/selectable?exclude_id=abc123&per_page=50
```
```json
{
  "data": [
    {"id": "wf-1", "name": "Code Review", "project": "my-project", "mode": "SEQUENTIAL", "shared": true, ...}
  ],
  "pagination": {"page": 0, "pages": 1, "total": 1, "per_page": 50}
}
```

---

## Data Model

No migrations required. Both fields used by 2c already exist:

- `WorkflowExecution.active_sub_execution_id` (added in Increment 1)
- `WorkflowExecutionStatusEnum.INTERRUPTED` (pre-existing)

---

## Error Handling

| Scenario | Behaviour |
|---|---|
| `ENABLE_SUB_WORKFLOW_NODE=false` on GET /selectable | 403 `FeatureDisabledError` |
| Child workflow not found on resume path | `ValueError` (same as create path) |
| Child exits with `INTERRUPTED` | `active_sub_execution_id` stays set; `InterruptedException` raised → parent marked INTERRUPTED |
| Child exits with `FAILED` | `active_sub_execution_id` cleared; `SubWorkflowExecutionError` raised |
| Child exits with `ABORTED` | `active_sub_execution_id` cleared; `ExecutionAbortedException` raised |
| Child exits with `SUCCEEDED` | `active_sub_execution_id` cleared; output returned |

---

## Files Changed

| File | Change |
|---|---|
| `src/codemie/service/workflow_config/workflow_config_index_service.py` | Add `ExcludeSelfModifier(workflow_id)` class |
| `src/codemie/rest_api/routers/workflow.py` | Add `GET /workflows/selectable` handler |
| `src/codemie/workflows/nodes/sub_workflow_node.py` | Fix `execute()` for all three 2c sub-fixes |
| `tests/codemie/workflows/nodes/test_sub_workflow_node.py` | New tests for 2c behaviours |
| `tests/codemie/rest_api/routers/test_workflow_selectable.py` | New tests for 2b endpoint |

---

## Test Contracts

### 2b — /workflows/selectable

```
test_selectable_returns_403_when_flag_disabled
  ENABLE_SUB_WORKFLOW_NODE=False → GET /selectable → 403

test_selectable_returns_visible_sequential_workflows
  User has 2 SEQUENTIAL workflows and 1 AUTONOMOUS → returns 2

test_selectable_excludes_self_when_exclude_id_given
  exclude_id=wf-1 → wf-1 not in response

test_selectable_modifier_excludes_id_from_query
  ExcludeSelfModifier("wf-1").modify_query(q) → WHERE id != 'wf-1'
```

### 2c — SubWorkflowNode.execute()

```
test_execute_sets_active_sub_execution_id_before_stream
  After create_workflow_execution, before stream(), parent.active_sub_execution_id is set

test_execute_raises_interrupted_when_child_interrupted
  finished_child.overall_status=INTERRUPTED → InterruptedException raised

test_execute_does_not_clear_active_id_on_child_interrupt
  finished_child.overall_status=INTERRUPTED → parent.active_sub_execution_id NOT cleared

test_execute_resume_path_resumes_child_with_forwarded_input
  parent.active_sub_execution_id set → create_executor called with resume_execution=True
  and forwarded context_store as user_input

test_execute_resume_path_clears_active_id_on_child_success
  resume path → child SUCCEEDED → parent.active_sub_execution_id = None

test_execute_clears_active_id_on_child_failure_normal_path
  create path → child FAILED → parent.active_sub_execution_id = None (existing, now verified)
```
