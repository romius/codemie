# Spec: EPMCDME-11609 task1+task6 — Sub-workflow Foundation + DB/Config

**Parent ticket:** EPMCDME-11609  
**Stories covered:** task1 (node type foundation), task6 (DB migration and configuration)  
**Branch:** EPMCDME-11609_sub-workflow-node  
**Date:** 2026-07-29

---

## 1. Purpose

Establish the data-model and persistence foundation for the sub-workflow feature. No execution logic, pool lifecycle, or interrupt handling — those belong to later tasks (task2–task5). When this task is merged, the engine is *aware* of sub-workflow states so they parse and validate correctly, and the DB schema is ready for subsequent tasks to build on.

---

## 2. Design Decisions (locked before this spec)

| Decision | Choice |
|---|---|
| Pool DB schema | Hybrid: `pool_config JSONB` column + separate `max_nesting_level INTEGER NULL` column |
| Pool entry lifecycle | Single-use (enforced in task4; recorded here for context) |
| Interrupt bridge key | Per-node-name: `__sub_wf_exec_id__<node_name>` (enforced in task3; recorded here) |

---

## 3. WorkflowState Model Changes

**File:** `src/codemie/core/workflow_models/workflow_models.py`

### New fields

```python
workflow_id: Optional[str] = None
input_mapping: Optional[dict[str, str]] = None
```

`workflow_id` becomes the **fourth exclusive discriminant** alongside `assistant_id`, `custom_node_id`, and `tool_id`.

`input_mapping` is an optional dict of `{output_key: jinja_template_string}` rendered against the parent's `context_store` (e.g. `{"doc_summary": "{{summary}}"}`). If set, it takes precedence over `task` for generating the sub-workflow input (JSON-serialized). If absent, the state's `task` string is used.

### Validator update

```python
_TYPE_UNDEFINED_ERROR = "One of 'assistant_id', 'custom_node_id', 'tool_id', or 'workflow_id' must be provided."
_TYPE_OVERDEFINED_ERROR = "Only one of 'assistant_id', 'custom_node_id', 'tool_id', or 'workflow_id' can be provided."

@model_validator(mode='after')
def check_state_type(cls, values: WorkflowState) -> WorkflowState:
    type_fields = [values.assistant_id, values.custom_node_id, values.tool_id, values.workflow_id]
    if not any(type_fields):
        raise ValueError(cls._TYPE_UNDEFINED_ERROR)
    if sum(f is not None for f in type_fields) > 1:
        raise ValueError(cls._TYPE_OVERDEFINED_ERROR)
    return values
```

`interrupt_before` is **not restricted** on `workflow_id` states — it controls interruption inside the sub-workflow at YAML definition time.

---

## 4. WorkflowPoolConfig Model

**File:** `src/codemie/core/workflow_models/workflow_models.py`  
**Export:** `src/codemie/core/workflow_models/__init__.py`

```python
class WorkflowPoolConfig(BaseModel):
    """Per-workflow pre-instantiation pool configuration."""
    enabled: bool = False
    min_size: int = Field(default=2, ge=1, le=20)
    max_size: int = Field(default=5, ge=1, le=50)
    refill_interval_seconds: int = Field(default=30, ge=5)
```

---

## 5. WorkflowConfigBase Changes

**File:** `src/codemie/core/workflow_models/workflow_config.py`

### New fields

```python
pool_config: Optional[WorkflowPoolConfig] = SQLField(
    default=None, sa_column=Column(PydanticType(WorkflowPoolConfig))
)
max_nesting_level: Optional[int] = SQLField(default=None)
```

`pool_config` uses `PydanticType` (same pattern as `bedrock` and `retry_policy`). `max_nesting_level` is a plain nullable integer; `None` means "use the `WORKFLOW_MAX_NESTING_DEPTH` global default".

### parse_execution_config() extension

```python
pool_cfg = yaml_data.get("pool_config")
self.pool_config = WorkflowPoolConfig(**pool_cfg) if pool_cfg else None
self.max_nesting_level = yaml_data.get("max_nesting_level")
```

---

## 6. WorkflowExecution Model Changes

**File:** `src/codemie/core/workflow_models/workflow_execution.py`

```python
parent_execution_id: Optional[str] = SQLField(default=None, index=True)
active_sub_execution_id: Optional[str] = SQLField(default=None, index=True)
```

- `parent_execution_id` — set when a sub-workflow execution is created; `None` for top-level executions.
- `active_sub_execution_id` — populated when the parent is suspended waiting for a sub-workflow interrupt; cleared on sub-workflow completion or abort.

Both default to `None` — fully backward compatible with existing rows.

---

## 7. YAML Execution Config Schema

**File:** `src/codemie/workflows/execution_config_schema.yaml`

### State.$defs additions

In `$defs.State.properties`, add:
```yaml
workflow_id: {type: string}
input_mapping:
  type: object
  additionalProperties: {type: string}
```

In `$defs.State.oneOf`, add fourth branch:
```yaml
- required: [workflow_id]
```

### Top-level additions

```yaml
pool_config:
  type: object
  properties:
    enabled:                 {type: boolean, default: false}
    min_size:                {type: integer, minimum: 1, maximum: 20, default: 2}
    max_size:                {type: integer, minimum: 1, maximum: 50, default: 5}
    refill_interval_seconds: {type: integer, minimum: 5, default: 30}

max_nesting_level:
  type: integer
  minimum: 1
```

---

## 8. Validation Layer

### 8.1 YAML cross-reference validation (`config_yaml_validation.py`)

**No changes.** `_validate_workflow_execution_config_cross_references()` validates YAML-internal references (assistants, tools, custom nodes against their YAML sections). `workflow_id` references another DB row — there is no `workflows` section in the YAML to check against, so the function naturally skips it. This is correct behavior.

### 8.2 Resource availability validation (`config_resources_validation.py`)

Add `_validate_sub_workflow_availability()`:

```python
def _validate_sub_workflow_availability(
    workflow_config: WorkflowConfig, user: User
) -> list[tuple[str, str, str]]:
    """
    Returns list of (state_id, workflow_id, reason) for unavailable references.
    """
    unavailable = []
    for state in workflow_config.states or []:
        if not state.workflow_id:
            continue
        if state.workflow_id == str(workflow_config.id):
            unavailable.append((state.id, state.workflow_id, "self-reference not allowed"))
            continue
        try:
            sub_wf = WorkflowService().get_workflow(state.workflow_id, user)
            if sub_wf is None:
                unavailable.append((state.id, state.workflow_id, "not found"))
        except Exception:
            unavailable.append((state.id, state.workflow_id, "not found or access denied"))
    return unavailable
```

`unavailable_sub_workflows` field added to `WorkflowConfigResourcesValidationError.__init__()`.  
`_validate_sub_workflow_availability()` called from `validate_workflow_config_resources_availability()`.

---

## 9. Configuration

**File:** `src/codemie/configs/config.py`

```python
ENABLE_SUB_WORKFLOW_NODE: bool = False
"""Feature gate for staged rollout. When False, SubWorkflowNode execution is rejected with a clear error (enforced in task2 dispatch)."""

WORKFLOW_MAX_NESTING_DEPTH: int = 1
"""Global ceiling on sub-workflow nesting depth. Default 1 = parent can call sub, but sub cannot call further sub-workflows. Per-workflow override via WorkflowConfig.max_nesting_level."""

WORKFLOW_POOL_ENABLED: bool = True
"""Global kill-switch for the warm workflow pool. Overrides all per-workflow pool_config.enabled settings when False."""

WORKFLOW_POOL_MAX_AGE_SECONDS: int = 3600
"""Maximum age (seconds) of a pre-compiled pool entry before eviction."""
```

**File:** `config/customer/customer-config.yaml` (if present)

```yaml
features:
  subWorkflow:
    enabled: false
```

---

## 10. Database Migrations

Two Alembic migration files in `src/external/alembic/versions/`:

### Migration 1 — workflows table

Adds `pool_config JSONB NULL` and `max_nesting_level INTEGER NULL`.

```python
def upgrade():
    op.add_column('workflows', sa.Column('pool_config', postgresql.JSONB(), nullable=True))
    op.add_column('workflows', sa.Column('max_nesting_level', sa.Integer(), nullable=True))

def downgrade():
    op.drop_column('workflows', 'max_nesting_level')
    op.drop_column('workflows', 'pool_config')
```

### Migration 2 — workflow_executions table

Adds `parent_execution_id VARCHAR NULL` and `active_sub_execution_id VARCHAR NULL`, both with indexes.

```python
def upgrade():
    op.add_column('workflow_executions',
        sa.Column('parent_execution_id', sa.String(), nullable=True))
    op.create_index('ix_workflow_executions_parent_execution_id',
        'workflow_executions', ['parent_execution_id'])
    op.add_column('workflow_executions',
        sa.Column('active_sub_execution_id', sa.String(), nullable=True))
    op.create_index('ix_workflow_executions_active_sub_execution_id',
        'workflow_executions', ['active_sub_execution_id'])

def downgrade():
    op.drop_index('ix_workflow_executions_active_sub_execution_id',
        table_name='workflow_executions')
    op.drop_column('workflow_executions', 'active_sub_execution_id')
    op.drop_index('ix_workflow_executions_parent_execution_id',
        table_name='workflow_executions')
    op.drop_column('workflow_executions', 'parent_execution_id')
```

---

## 11. Test Plan

| Test file | Coverage |
|---|---|
| `tests/codemie/core/workflow_models/test_workflow_config.py` | `WorkflowState` fourth discriminant valid; mutual exclusion with 4 options; `input_mapping` field; `WorkflowConfigBase` `pool_config` and `max_nesting_level` round-trips through SQLModel |
| `tests/codemie/core/workflow_models/test_workflow_models.py` | `WorkflowPoolConfig` Pydantic bounds validation (min_size/max_size ge/le) |
| `tests/codemie/workflows/test_config_yaml_validation.py` | YAML schema accepts `workflow_id` arm; rejects combined discriminators (e.g. `workflow_id`+`tool_id`); `pool_config` block validates; `max_nesting_level` field accepted |
| `tests/codemie/workflows/test_config_resources_validation.py` | `_validate_sub_workflow_availability`: happy path (sub-workflow found); not-found sub-workflow; self-reference guard; access-denied case |

All existing tests must continue to pass unchanged.

---

## 12. Backward Compatibility

| Change | Impact |
|---|---|
| `WorkflowState.workflow_id`, `input_mapping` | Optional, `None` default — existing YAMLs unaffected |
| `WorkflowConfigBase.pool_config`, `max_nesting_level` | Optional, `None` default — existing workflow configs unaffected |
| `WorkflowExecution.parent_execution_id`, `active_sub_execution_id` | Optional, `None` default — existing execution rows unaffected |
| YAML schema extensions | Additive only — all existing configs remain valid |
| `validate_workflow_config_resources_availability()` new check | Only activates when `workflow_id` is set in a state — no impact on existing validations |

---

## 13. Out of Scope (later tasks)

- `SubWorkflowNode` class and execution (task2)
- Interrupt/resume bridge (task3)
- Pool lifecycle service and background watcher (task4)
- Nesting depth enforcement at execution time (task5)
- `WorkflowService.create_workflow_execution()` `parent_execution_id` parameter — added when task2 creates sub-workflow executions
