# Sub-workflow Foundation + DB/Config (task1 + task6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish the data-model and persistence foundation for sub-workflow nodes — no execution logic, only schema, validation, and config changes that later tasks build on.

**Architecture:** Add `workflow_id` as a fourth exclusive discriminant to `WorkflowState`; add a `WorkflowPoolConfig` Pydantic model; extend `WorkflowConfigBase` with `pool_config` (JSONB via PydanticType) and `max_nesting_level`; add lineage fields to `WorkflowExecution`; extend the YAML schema and resource-availability validator; add feature-gate config vars; create two Alembic migrations.

**Tech Stack:** Python 3.12, Pydantic v2, SQLModel, PostgreSQL JSONB, Alembic, pytest, jsonschema (Draft202012Validator)

## Global Constraints

- All new fields have `Optional` type with `None` defaults — zero impact on existing rows.
- `PydanticType` is the correct SQLModel column type for nested Pydantic objects in JSONB columns — mirror the `bedrock` / `retry_policy` pattern.
- `workflow_id` must be a **pure fourth exclusive discriminant** — `check_state_type` must reject any state that provides more than one of `assistant_id`, `custom_node_id`, `tool_id`, `workflow_id`.
- Do NOT modify `config_yaml_validation.py` (the old file at `src/codemie/workflows/`) — the real validator is `src/codemie/workflows/validation/schema.py`.
- Do NOT modify `config_resources_validation.py` (the old proxy) — the real validator is `src/codemie/workflows/validation/resources.py`.
- All test imports use the `codemie.workflows.validation.resources` and `codemie.workflows.validation.schema` paths.
- Migration revision IDs must chain: existing tail is `"s9t0u1v2w3x4"` (`s9t0u1v2w3x4_migrate_use_custom_config_field.py`).

---

### Task 1: WorkflowPoolConfig model + WorkflowState fourth discriminant

**Files:**
- Modify: `src/codemie/core/workflow_models/workflow_models.py:361-402`
- Modify: `src/codemie/core/workflow_models/__init__.py`
- Test: `tests/codemie/core/workflow_models/test_workflow_models.py`

**Test-first: yes — test `WorkflowState(workflow_id=...)` raises no error and all mutual-exclusion combos raise `ValidationError`**

**Interfaces:**
- Produces: `WorkflowPoolConfig` (Pydantic `BaseModel` with `enabled`, `min_size`, `max_size`, `refill_interval_seconds`); exported from `codemie.core.workflow_models`
- Produces: `WorkflowState.workflow_id: Optional[str]` and `WorkflowState.input_mapping: Optional[dict[str, str]]`; `check_state_type` accepts all four discriminants exclusively

- [ ] **Step 1: Write the failing tests**

In `tests/codemie/core/workflow_models/test_workflow_models.py`, append after the existing `TestWorkflowState` class:

```python
from pydantic import ValidationError
from codemie.core.workflow_models import WorkflowPoolConfig


class TestWorkflowPoolConfig:
    def test_default_values(self):
        cfg = WorkflowPoolConfig()
        assert cfg.enabled is False
        assert cfg.min_size == 2
        assert cfg.max_size == 5
        assert cfg.refill_interval_seconds == 30

    def test_min_size_lower_bound(self):
        with pytest.raises(ValidationError):
            WorkflowPoolConfig(min_size=0)

    def test_min_size_upper_bound(self):
        with pytest.raises(ValidationError):
            WorkflowPoolConfig(min_size=21)

    def test_max_size_upper_bound(self):
        with pytest.raises(ValidationError):
            WorkflowPoolConfig(max_size=51)

    def test_refill_interval_lower_bound(self):
        with pytest.raises(ValidationError):
            WorkflowPoolConfig(refill_interval_seconds=4)


class TestWorkflowStateWorkflowId:
    def test_workflow_id_is_valid_fourth_discriminant(self):
        state = WorkflowState(
            id="s1",
            workflow_id="wf-123",
            next=WorkflowNextState(state_id="s2"),
        )
        assert state.workflow_id == "wf-123"

    def test_input_mapping_field(self):
        state = WorkflowState(
            id="s1",
            workflow_id="wf-123",
            input_mapping={"summary": "{{context_summary}}"},
            next=WorkflowNextState(state_id="s2"),
        )
        assert state.input_mapping == {"summary": "{{context_summary}}"}

    def test_workflow_id_mutual_exclusion_with_assistant_id(self):
        with pytest.raises(ValidationError):
            WorkflowState(
                id="s1",
                workflow_id="wf-123",
                assistant_id="asst-1",
                next=WorkflowNextState(state_id="s2"),
            )

    def test_workflow_id_mutual_exclusion_with_tool_id(self):
        with pytest.raises(ValidationError):
            WorkflowState(
                id="s1",
                workflow_id="wf-123",
                tool_id="tool-1",
                next=WorkflowNextState(state_id="s2"),
            )

    def test_workflow_id_mutual_exclusion_with_custom_node_id(self):
        with pytest.raises(ValidationError):
            WorkflowState(
                id="s1",
                workflow_id="wf-123",
                custom_node_id="node-1",
                next=WorkflowNextState(state_id="s2"),
            )

    def test_no_discriminant_still_raises(self):
        with pytest.raises(ValidationError):
            WorkflowState(id="s1", next=WorkflowNextState(state_id="s2"))
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd src && python -m pytest ../tests/codemie/core/workflow_models/test_workflow_models.py::TestWorkflowPoolConfig ../tests/codemie/core/workflow_models/test_workflow_models.py::TestWorkflowStateWorkflowId -v 2>&1 | tail -20
```

Expected: `ImportError: cannot import name 'WorkflowPoolConfig'` and `AttributeError: 'WorkflowState' object has no attribute 'workflow_id'`.

- [ ] **Step 3: Add `WorkflowPoolConfig` to `workflow_models.py`**

Find `class WorkflowState(BaseModel):` at line 361. Insert the new model **before** it:

```python
class WorkflowPoolConfig(BaseModel):
    """Per-workflow pre-instantiation pool configuration."""
    enabled: bool = False
    min_size: int = Field(default=2, ge=1, le=20)
    max_size: int = Field(default=5, ge=1, le=50)
    refill_interval_seconds: int = Field(default=30, ge=5)
```

- [ ] **Step 4: Extend `WorkflowState` with new fields and updated validator**

In `WorkflowState` body, add the two new fields after `tool_args`:

```python
workflow_id: Optional[str] = None
input_mapping: Optional[dict[str, str]] = None
```

Replace the three error-string class attributes and `check_state_type` validator (lines 376-402):

```python
_TYPE_UNDEFINED_ERROR = (
    "One of 'assistant_id', 'custom_node_id', 'tool_id', or 'workflow_id' must be provided."
)
_TYPE_OVERDEFINED_ERROR = (
    "Only one of 'assistant_id', 'custom_node_id', 'tool_id', or 'workflow_id' can be provided."
)

@model_validator(mode='after')
def check_state_type(cls, values: WorkflowState) -> WorkflowState:
    type_fields = [values.assistant_id, values.custom_node_id, values.tool_id, values.workflow_id]
    if not any(type_fields):
        raise ValueError(cls._TYPE_UNDEFINED_ERROR)
    if sum(f is not None for f in type_fields) > 1:
        raise ValueError(cls._TYPE_OVERDEFINED_ERROR)
    return values
```

- [ ] **Step 5: Export `WorkflowPoolConfig` from `__init__.py`**

In `src/codemie/core/workflow_models/__init__.py`:

Add `"WorkflowPoolConfig"` to the `__all__` list (alphabetically between `WorkflowNextState` and `WorkflowRetryPolicy`).

Add the import line to the `from .workflow_models import (...)` block:
```python
    WorkflowPoolConfig,
```

- [ ] **Step 6: Run tests to confirm green**

```bash
cd src && python -m pytest ../tests/codemie/core/workflow_models/test_workflow_models.py::TestWorkflowPoolConfig ../tests/codemie/core/workflow_models/test_workflow_models.py::TestWorkflowStateWorkflowId -v 2>&1 | tail -20
```

Expected: all new tests PASS. Confirm existing `TestWorkflowState` tests still pass:

```bash
cd src && python -m pytest ../tests/codemie/core/workflow_models/test_workflow_models.py -v 2>&1 | tail -30
```

- [ ] **Step 7: Commit**

```bash
git add src/codemie/core/workflow_models/workflow_models.py \
        src/codemie/core/workflow_models/__init__.py \
        tests/codemie/core/workflow_models/test_workflow_models.py
git commit -m "feat(EPMCDME-11609): add WorkflowPoolConfig model and WorkflowState workflow_id discriminant"
```

---

### Task 2: WorkflowConfigBase pool fields + parse_execution_config

**Files:**
- Modify: `src/codemie/core/workflow_models/workflow_config.py:69-145` (fields) and `:221-254` (parse)
- Test: `tests/codemie/core/workflow_models/test_workflow_config.py`

**Test-first: yes — test `parse_execution_config()` correctly parses `pool_config` and `max_nesting_level` from YAML**

**Interfaces:**
- Consumes: `WorkflowPoolConfig` from Task 1
- Produces: `WorkflowConfigBase.pool_config: Optional[WorkflowPoolConfig]` stored in JSONB column; `WorkflowConfigBase.max_nesting_level: Optional[int]` stored in plain nullable integer column; `parse_execution_config()` reads both from YAML

- [ ] **Step 1: Write the failing tests**

Append to `tests/codemie/core/workflow_models/test_workflow_config.py`:

```python
from codemie.core.workflow_models import WorkflowPoolConfig


class TestWorkflowConfigPoolFields:
    def test_parse_execution_config_pool_config(self):
        yaml_config = """
        pool_config:
          enabled: true
          min_size: 3
          max_size: 10
          refill_interval_seconds: 60
        states: []
        """
        wf = WorkflowConfig(
            name="Test", description="Test", yaml_config=yaml_config
        )
        wf.parse_execution_config()
        assert wf.pool_config is not None
        assert wf.pool_config.enabled is True
        assert wf.pool_config.min_size == 3
        assert wf.pool_config.max_size == 10
        assert wf.pool_config.refill_interval_seconds == 60

    def test_parse_execution_config_no_pool_config_defaults_to_none(self):
        yaml_config = "states: []"
        wf = WorkflowConfig(name="Test", description="Test", yaml_config=yaml_config)
        wf.parse_execution_config()
        assert wf.pool_config is None

    def test_parse_execution_config_max_nesting_level(self):
        yaml_config = "max_nesting_level: 3\nstates: []"
        wf = WorkflowConfig(name="Test", description="Test", yaml_config=yaml_config)
        wf.parse_execution_config()
        assert wf.max_nesting_level == 3

    def test_parse_execution_config_no_max_nesting_level_defaults_to_none(self):
        yaml_config = "states: []"
        wf = WorkflowConfig(name="Test", description="Test", yaml_config=yaml_config)
        wf.parse_execution_config()
        assert wf.max_nesting_level is None

    def test_pool_config_field_defaults_to_none(self):
        wf = WorkflowConfig(name="Test", description="Test")
        assert wf.pool_config is None

    def test_max_nesting_level_defaults_to_none(self):
        wf = WorkflowConfig(name="Test", description="Test")
        assert wf.max_nesting_level is None
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd src && python -m pytest ../tests/codemie/core/workflow_models/test_workflow_config.py::TestWorkflowConfigPoolFields -v 2>&1 | tail -20
```

Expected: `AttributeError: 'WorkflowConfig' object has no attribute 'pool_config'`.

- [ ] **Step 3: Add imports to `workflow_config.py`**

Ensure `WorkflowPoolConfig` is imported. At the top of `workflow_config.py`, find the local import block and add:

```python
from codemie.core.workflow_models.workflow_models import WorkflowPoolConfig
```

(Check that this import doesn't already exist and doesn't create a circular import — if it does, move `WorkflowPoolConfig` higher in `workflow_models.py` or import inside the method.)

- [ ] **Step 4: Add fields to `WorkflowConfigBase`**

Inside `WorkflowConfigBase` class body (after `categories` field at line ~112), add:

```python
pool_config: Optional[WorkflowPoolConfig] = SQLField(
    default=None, sa_column=Column(PydanticType(WorkflowPoolConfig))
)
max_nesting_level: Optional[int] = SQLField(default=None)
```

- [ ] **Step 5: Update `parse_execution_config()`**

At the end of `parse_execution_config()` (after `self.max_iteration_key_output_limit = ...`), add:

```python
pool_cfg = yaml_data.get("pool_config")
self.pool_config = WorkflowPoolConfig(**pool_cfg) if pool_cfg else None
self.max_nesting_level = yaml_data.get("max_nesting_level")
```

- [ ] **Step 6: Run tests to confirm green**

```bash
cd src && python -m pytest ../tests/codemie/core/workflow_models/test_workflow_config.py::TestWorkflowConfigPoolFields -v 2>&1 | tail -20
```

Run the full test_workflow_config.py to check regressions:

```bash
cd src && python -m pytest ../tests/codemie/core/workflow_models/test_workflow_config.py -v 2>&1 | tail -30
```

- [ ] **Step 7: Commit**

```bash
git add src/codemie/core/workflow_models/workflow_config.py \
        tests/codemie/core/workflow_models/test_workflow_config.py
git commit -m "feat(EPMCDME-11609): add pool_config and max_nesting_level to WorkflowConfigBase"
```

---

### Task 3: WorkflowExecution lineage fields

**Files:**
- Modify: `src/codemie/core/workflow_models/workflow_execution.py:148-174`
- Test: `tests/codemie/core/workflow_models/test_workflow_execution.py`

**Test-first: yes — test that new fields default to None and can be set**

**Interfaces:**
- Produces: `WorkflowExecution.parent_execution_id: Optional[str]` with DB index; `WorkflowExecution.active_sub_execution_id: Optional[str]` with DB index

- [ ] **Step 1: Write the failing tests**

Append to `tests/codemie/core/workflow_models/test_workflow_execution.py`:

```python
class TestWorkflowExecutionLineageFields:
    def test_parent_execution_id_defaults_to_none(self):
        exec_ = WorkflowExecution(
            workflow_id="wf-1",
            execution_id="exec-1",
        )
        assert exec_.parent_execution_id is None

    def test_active_sub_execution_id_defaults_to_none(self):
        exec_ = WorkflowExecution(
            workflow_id="wf-1",
            execution_id="exec-1",
        )
        assert exec_.active_sub_execution_id is None

    def test_parent_execution_id_can_be_set(self):
        exec_ = WorkflowExecution(
            workflow_id="wf-1",
            execution_id="exec-child",
            parent_execution_id="exec-parent",
        )
        assert exec_.parent_execution_id == "exec-parent"

    def test_active_sub_execution_id_can_be_set(self):
        exec_ = WorkflowExecution(
            workflow_id="wf-1",
            execution_id="exec-parent",
            active_sub_execution_id="exec-child",
        )
        assert exec_.active_sub_execution_id == "exec-child"
```

Check what the file currently imports at the top:
```bash
head -40 tests/codemie/core/workflow_models/test_workflow_execution.py
```
and add the `WorkflowExecution` import if not already there.

- [ ] **Step 2: Run to confirm failure**

```bash
cd src && python -m pytest ../tests/codemie/core/workflow_models/test_workflow_execution.py::TestWorkflowExecutionLineageFields -v 2>&1 | tail -20
```

Expected: `AttributeError: 'WorkflowExecution' object has no attribute 'parent_execution_id'`.

- [ ] **Step 3: Add fields to `WorkflowExecution`**

In `workflow_execution.py`, inside `class WorkflowExecution(BaseModelWithSQLSupport, Owned, table=True):`, after `conversation_id` (the last field before `__table_args__`), add:

```python
parent_execution_id: Optional[str] = SQLField(default=None, index=True)
active_sub_execution_id: Optional[str] = SQLField(default=None, index=True)
```

- [ ] **Step 4: Run tests to confirm green**

```bash
cd src && python -m pytest ../tests/codemie/core/workflow_models/test_workflow_execution.py -v 2>&1 | tail -30
```

- [ ] **Step 5: Commit**

```bash
git add src/codemie/core/workflow_models/workflow_execution.py \
        tests/codemie/core/workflow_models/test_workflow_execution.py
git commit -m "feat(EPMCDME-11609): add parent_execution_id and active_sub_execution_id to WorkflowExecution"
```

---

### Task 4: YAML schema extension + config.py additions

**Files:**
- Modify: `src/codemie/workflows/execution_config_schema.yaml:259-262` (oneOf) and top-level properties
- Modify: `src/codemie/configs/config.py`
- Modify: `config/customer/customer-config.yaml`
- Test: `tests/codemie/workflows/test_config_yaml_validation.py`

**Test-first: yes — test that a YAML with `workflow_id` state passes schema validation**

**Interfaces:**
- Produces: YAML schema accepts `workflow_id`/`input_mapping` in State and `pool_config`/`max_nesting_level` at top level; config has `ENABLE_SUB_WORKFLOW_NODE`, `WORKFLOW_MAX_NESTING_DEPTH`, `WORKFLOW_POOL_ENABLED`, `WORKFLOW_POOL_MAX_AGE_SECONDS`

- [ ] **Step 1: Write the failing tests**

Append to `tests/codemie/workflows/test_config_yaml_validation.py`:

```python
import yaml
from codemie.workflows.validation.schema import (
    _validate_workflow_execution_config_schema,
    WORKFLOW_EXECUTION_CONFIG_SCHEMA,
)


def test_workflow_id_state_passes_schema():
    config = yaml.safe_load("""
    states:
      - id: run_sub
        workflow_id: wf-child-123
        task: "run sub-workflow"
        next:
          state_id: end
    """)
    errors = _validate_workflow_execution_config_schema(WORKFLOW_EXECUTION_CONFIG_SCHEMA, config)
    assert errors == []


def test_workflow_id_with_input_mapping_passes_schema():
    config = yaml.safe_load("""
    states:
      - id: run_sub
        workflow_id: wf-child-123
        input_mapping:
          doc_summary: "{{summary}}"
        task: ""
        next:
          state_id: end
    """)
    errors = _validate_workflow_execution_config_schema(WORKFLOW_EXECUTION_CONFIG_SCHEMA, config)
    assert errors == []


def test_workflow_id_with_assistant_id_fails_schema():
    config = yaml.safe_load("""
    states:
      - id: bad_state
        workflow_id: wf-child-123
        assistant_id: asst-1
        task: ""
        next:
          state_id: end
    """)
    errors = _validate_workflow_execution_config_schema(WORKFLOW_EXECUTION_CONFIG_SCHEMA, config)
    assert len(errors) > 0


def test_pool_config_passes_schema():
    config = yaml.safe_load("""
    pool_config:
      enabled: true
      min_size: 2
      max_size: 10
      refill_interval_seconds: 30
    states:
      - id: s1
        assistant_id: asst-1
        task: ""
        next:
          state_id: end
    """)
    errors = _validate_workflow_execution_config_schema(WORKFLOW_EXECUTION_CONFIG_SCHEMA, config)
    assert errors == []


def test_max_nesting_level_passes_schema():
    config = yaml.safe_load("""
    max_nesting_level: 2
    states:
      - id: s1
        assistant_id: asst-1
        task: ""
        next:
          state_id: end
    """)
    errors = _validate_workflow_execution_config_schema(WORKFLOW_EXECUTION_CONFIG_SCHEMA, config)
    assert errors == []
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd src && python -m pytest ../tests/codemie/workflows/test_config_yaml_validation.py::test_workflow_id_state_passes_schema ../tests/codemie/workflows/test_config_yaml_validation.py::test_pool_config_passes_schema -v 2>&1 | tail -20
```

Expected: schema validation errors because `workflow_id` is not in `oneOf` yet.

- [ ] **Step 3: Extend `execution_config_schema.yaml` — State properties**

In `$defs.State.properties`, after `interrupt_before: {type: boolean, default: false}`:

```yaml
      workflow_id: {type: string}
      input_mapping:
        type: object
        additionalProperties: {type: string}
```

In `$defs.State.oneOf` (currently at lines 259-262):

```yaml
    oneOf:
      - required: [assistant_id]
      - required: [tool_id]
      - required: [custom_node_id]
      - required: [workflow_id]
```

- [ ] **Step 4: Add top-level schema properties**

After the `retry_policy` block in the top-level `properties:` section (after line 74):

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

- [ ] **Step 5: Add config vars to `config.py`**

In `src/codemie/configs/config.py`, inside `class Config(BaseSettings):`, append four new fields (near other workflow-related settings):

```python
ENABLE_SUB_WORKFLOW_NODE: bool = False
"""Feature gate for sub-workflow node execution. When False, SubWorkflowNode dispatch raises an error."""

WORKFLOW_MAX_NESTING_DEPTH: int = 1
"""Global ceiling on sub-workflow nesting depth. Per-workflow WorkflowConfig.max_nesting_level overrides this."""

WORKFLOW_POOL_ENABLED: bool = True
"""Global kill-switch for the warm workflow pool. Overrides all per-workflow pool_config.enabled when False."""

WORKFLOW_POOL_MAX_AGE_SECONDS: int = 3600
"""Maximum age in seconds of a pre-compiled pool entry before eviction."""
```

- [ ] **Step 6: Add feature flag to customer-config.yaml**

Open `config/customer/customer-config.yaml`. Locate the `features:` section and add an entry at the end of the `features` list:

```yaml
  - id: "subWorkflowNode"
    title: "Sub-workflow Node"
    description: "Enable sub-workflow node type"
    settings:
      enabled: false
```

(Follow the exact format of the surrounding feature entries — check the file first.)

- [ ] **Step 7: Run tests to confirm green**

```bash
cd src && python -m pytest ../tests/codemie/workflows/test_config_yaml_validation.py -v -k "workflow_id or pool_config or nesting_level" 2>&1 | tail -20
```

Run the full yaml validation tests to confirm no regressions:

```bash
cd src && python -m pytest ../tests/codemie/workflows/test_config_yaml_validation.py -v 2>&1 | tail -30
```

- [ ] **Step 8: Commit**

```bash
git add src/codemie/workflows/execution_config_schema.yaml \
        src/codemie/configs/config.py \
        config/customer/customer-config.yaml \
        tests/codemie/workflows/test_config_yaml_validation.py
git commit -m "feat(EPMCDME-11609): extend YAML schema for workflow_id state and pool config; add config vars"
```

---

### Task 5: Sub-workflow resource availability validation

**Files:**
- Modify: `src/codemie/workflows/validation/resources.py` (class + new function + call site)
- Test: `tests/codemie/workflows/test_config_resources_validation.py`

**Test-first: yes — test `_validate_sub_workflow_availability` for found/not-found/self-ref cases**

**Interfaces:**
- Consumes: `WorkflowConfig`, `User` types; `WorkflowService` for DB lookup
- Produces: `_validate_sub_workflow_availability(workflow_config, user) -> list[tuple[str, str, str]]`; `WorkflowConfigResourcesValidationError` carries `unavailable_sub_workflows`; `validate_workflow_config_resources_availability` calls the new function

- [ ] **Step 1: Write the failing tests**

Append to `tests/codemie/workflows/test_config_resources_validation.py`:

```python
from codemie.workflows.validation.resources import _validate_sub_workflow_availability


@pytest.fixture
def mock_workflow_config_with_id():
    wf = MagicMock()
    wf.id = "parent-wf-id"
    return wf


def test_validate_sub_workflow_availability_no_workflow_id_states(mock_workflow_config_with_id, mock_user):
    mock_workflow_config_with_id.states = [
        MagicMock(id="s1", workflow_id=None, assistant_id="asst-1"),
    ]
    result = _validate_sub_workflow_availability(mock_workflow_config_with_id, mock_user)
    assert result == []


def test_validate_sub_workflow_availability_found(mock_workflow_config_with_id, mock_user):
    mock_workflow_config_with_id.states = [
        MagicMock(id="s1", workflow_id="child-wf-id"),
    ]
    with patch(
        "codemie.workflows.validation.resources.WorkflowService"
    ) as mock_service_cls:
        mock_service = mock_service_cls.return_value
        mock_service.get_workflow.return_value = MagicMock()
        result = _validate_sub_workflow_availability(mock_workflow_config_with_id, mock_user)
    assert result == []


def test_validate_sub_workflow_availability_not_found(mock_workflow_config_with_id, mock_user):
    mock_workflow_config_with_id.states = [
        MagicMock(id="s1", workflow_id="missing-wf"),
    ]
    with patch(
        "codemie.workflows.validation.resources.WorkflowService"
    ) as mock_service_cls:
        mock_service = mock_service_cls.return_value
        mock_service.get_workflow.return_value = None
        result = _validate_sub_workflow_availability(mock_workflow_config_with_id, mock_user)
    assert len(result) == 1
    state_id, wf_id, reason = result[0]
    assert state_id == "s1"
    assert wf_id == "missing-wf"
    assert "not found" in reason


def test_validate_sub_workflow_availability_self_reference(mock_workflow_config_with_id, mock_user):
    mock_workflow_config_with_id.states = [
        MagicMock(id="s1", workflow_id="parent-wf-id"),
    ]
    result = _validate_sub_workflow_availability(mock_workflow_config_with_id, mock_user)
    assert len(result) == 1
    state_id, wf_id, reason = result[0]
    assert state_id == "s1"
    assert "self-reference" in reason


def test_validate_sub_workflow_availability_access_denied(mock_workflow_config_with_id, mock_user):
    mock_workflow_config_with_id.states = [
        MagicMock(id="s1", workflow_id="restricted-wf"),
    ]
    with patch(
        "codemie.workflows.validation.resources.WorkflowService"
    ) as mock_service_cls:
        mock_service = mock_service_cls.return_value
        mock_service.get_workflow.side_effect = Exception("Access denied")
        result = _validate_sub_workflow_availability(mock_workflow_config_with_id, mock_user)
    assert len(result) == 1
    assert "not found or access denied" in result[0][2]
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd src && python -m pytest ../tests/codemie/workflows/test_config_resources_validation.py::test_validate_sub_workflow_availability_not_found -v 2>&1 | tail -20
```

Expected: `ImportError: cannot import name '_validate_sub_workflow_availability'`.

- [ ] **Step 3: Add `WorkflowService` import and new function to `resources.py`**

At the top of `src/codemie/workflows/validation/resources.py`, in the imports block, add:

```python
from codemie.service.workflow.workflow_service import WorkflowService
```

(Verify the actual import path: `grep -r "class WorkflowService" src/` to find the right module.)

Then add the new function after `_validate_datasources_availability` and before `validate_workflow_config_resources_availability`:

```python
def _validate_sub_workflow_availability(
    workflow_config: WorkflowConfig, user: User
) -> list[tuple[str, str, str]]:
    """Returns list of (state_id, workflow_id, reason) for unavailable sub-workflow references."""
    unavailable = []
    for state in workflow_config.states or []:
        if not getattr(state, 'workflow_id', None):
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

**Important:** Verify `WorkflowService().get_workflow(id, user)` is the correct call signature. Run:
```bash
grep -n "def get_workflow" src/codemie/service/workflow/workflow_service.py 2>/dev/null | head -5
```
Adjust the call signature if needed.

- [ ] **Step 4: Add `unavailable_sub_workflows` to `WorkflowConfigResourcesValidationError`**

In `resources.py`, in `WorkflowConfigResourcesValidationError.__init__()`, add a new keyword parameter with default `None`:

```python
unavailable_sub_workflows: list[tuple[str, str, str]] = None,
```

Add it as the last parameter (after `missing_integration_tools`). Then store it:

```python
self.unavailable_sub_workflows = unavailable_sub_workflows or []
```

Also add an error message entry in the `self.messages` list:

```python
WorkflowConfigResourcesValidationError._format_map_message(
    "Sub-workflows do not exist or are not accessible", self.unavailable_sub_workflows
),
```

- [ ] **Step 5: Call `_validate_sub_workflow_availability` from `validate_workflow_config_resources_availability`**

In `validate_workflow_config_resources_availability`, add the call after `unavailable_datasources`:

```python
unavailable_sub_workflows = _validate_sub_workflow_availability(workflow_config, user)
```

Update the `unavailable_resources` tuple to include it:

```python
unavailable_resources = (
    unavailable_assistants,
    unavailable_tools,
    unavailable_tools_from_asst_integrations,
    unavailable_datasources,
)
```

And update both `raise WorkflowConfigResourcesValidationError(...)` calls (lines ~666 and ~685) to include `unavailable_sub_workflows=unavailable_sub_workflows` as a keyword argument.

Also update the condition check:

```python
if any(unavailable_resources) or any(unavailable_sub_workflows) or invalid_integration_tools or missing_integration_tools:
```

- [ ] **Step 6: Run tests to confirm green**

```bash
cd src && python -m pytest ../tests/codemie/workflows/test_config_resources_validation.py -v -k "sub_workflow" 2>&1 | tail -20
```

Run all resource validation tests:

```bash
cd src && python -m pytest ../tests/codemie/workflows/test_config_resources_validation.py -v 2>&1 | tail -30
```

- [ ] **Step 7: Commit**

```bash
git add src/codemie/workflows/validation/resources.py \
        tests/codemie/workflows/test_config_resources_validation.py
git commit -m "feat(EPMCDME-11609): add sub-workflow availability validation to resource validator"
```

---

### Task 6: Alembic migration — workflows table

**Prerequisite:** Task 2 model changes must be committed and a local PostgreSQL DB running at `alembic upgrade head`.

**Files:**
- Create: `src/external/alembic/versions/<generated-id>_add_sub_workflow_pool_config.py` (ID generated by Alembic)

**Test-first: no — structural migration; verify with upgrade/downgrade roundtrip**

**Interfaces:**
- Produces: `workflows.pool_config JSONB NULL` and `workflows.max_nesting_level INTEGER NULL` columns; fully reversible downgrade

- [ ] **Step 1: Confirm the DB is at the current head**

```bash
cd src/external/alembic && poetry run alembic current
```

If behind, run `poetry run alembic upgrade head` to apply pending migrations.

- [ ] **Step 2: Generate the migration via autogenerate**

All Alembic commands must run from `src/external/alembic/`:

```bash
cd src/external/alembic && poetry run alembic revision --autogenerate -m "add_sub_workflow_pool_config"
```

Alembic generates a file in `versions/` with an auto-assigned revision ID (e.g. `a1b2c3d4e5f6_add_sub_workflow_pool_config.py`).

- [ ] **Step 3: Review and trim the generated file**

Open the new file. Verify the `upgrade()` body contains exactly these two column additions and nothing else — autogenerate sometimes picks up unrelated changes if the DB has drifted:

```python
def upgrade() -> None:
    op.add_column('workflows', sa.Column('pool_config', postgresql.JSONB(), nullable=True))
    op.add_column('workflows', sa.Column('max_nesting_level', sa.Integer(), nullable=True))
```

Verify the `downgrade()` body drops them in reverse order:

```python
def downgrade() -> None:
    op.drop_column('workflows', 'max_nesting_level')
    op.drop_column('workflows', 'pool_config')
```

Remove any additional generated statements that do not relate to these two columns.

- [ ] **Step 4: Test the roundtrip**

```bash
cd src/external/alembic && poetry run alembic upgrade head
cd src/external/alembic && poetry run alembic downgrade -1
cd src/external/alembic && poetry run alembic upgrade head
```

Both `upgrade` and `downgrade` must complete without errors.

- [ ] **Step 5: Commit**

```bash
git add src/external/alembic/versions/
git commit -m "feat(EPMCDME-11609): add pool_config and max_nesting_level columns to workflows table (migration)"
```

---

### Task 7: Alembic migration — workflow_executions table

**Prerequisite:** Task 3 model changes must be committed; DB must be at Task 6's head.

**Files:**
- Create: `src/external/alembic/versions/<generated-id>_add_sub_workflow_execution_lineage.py` (ID generated by Alembic)

**Test-first: no — structural migration; verify with upgrade/downgrade roundtrip**

**Interfaces:**
- Produces: `workflow_executions.parent_execution_id VARCHAR NULL` with index; `workflow_executions.active_sub_execution_id VARCHAR NULL` with index; fully reversible downgrade

- [ ] **Step 1: Confirm the DB is at head (Task 6 applied)**

```bash
cd src/external/alembic && poetry run alembic current
```

- [ ] **Step 2: Generate the migration**

```bash
cd src/external/alembic && poetry run alembic revision --autogenerate -m "add_sub_workflow_execution_lineage"
```

- [ ] **Step 3: Review and trim the generated file**

Open the new file. Verify `upgrade()` contains exactly:

```python
def upgrade() -> None:
    op.add_column('workflow_executions',
        sa.Column('parent_execution_id', sa.String(), nullable=True))
    op.create_index(
        'ix_workflow_executions_parent_execution_id',
        'workflow_executions',
        ['parent_execution_id'],
    )
    op.add_column('workflow_executions',
        sa.Column('active_sub_execution_id', sa.String(), nullable=True))
    op.create_index(
        'ix_workflow_executions_active_sub_execution_id',
        'workflow_executions',
        ['active_sub_execution_id'],
    )
```

Verify `downgrade()` reverses in the correct order (drop indexes before dropping columns):

```python
def downgrade() -> None:
    op.drop_index(
        'ix_workflow_executions_active_sub_execution_id',
        table_name='workflow_executions',
    )
    op.drop_column('workflow_executions', 'active_sub_execution_id')
    op.drop_index(
        'ix_workflow_executions_parent_execution_id',
        table_name='workflow_executions',
    )
    op.drop_column('workflow_executions', 'parent_execution_id')
```

Remove any additional generated statements unrelated to these two columns.

- [ ] **Step 4: Test the roundtrip**

```bash
cd src/external/alembic && poetry run alembic upgrade head
cd src/external/alembic && poetry run alembic downgrade -1
cd src/external/alembic && poetry run alembic upgrade head
```

- [ ] **Step 5: Commit**

```bash
git add src/external/alembic/versions/
git commit -m "feat(EPMCDME-11609): add parent_execution_id and active_sub_execution_id to workflow_executions (migration)"
```

---

## Post-implementation Checklist

- [ ] Run full test suite targeting changed areas:

```bash
cd src && python -m pytest \
  ../tests/codemie/core/workflow_models/test_workflow_models.py \
  ../tests/codemie/core/workflow_models/test_workflow_config.py \
  ../tests/codemie/core/workflow_models/test_workflow_execution.py \
  ../tests/codemie/workflows/test_config_yaml_validation.py \
  ../tests/codemie/workflows/test_config_resources_validation.py \
  -v 2>&1 | tail -50
```

- [ ] Confirm no regressions in existing tests by running the broader test suite:

```bash
cd src && python -m pytest ../tests/codemie/core/ ../tests/codemie/workflows/ -v 2>&1 | tail -50
```

- [ ] Verify migration revision chain is unbroken:
  - `s9t0u1v2w3x4` → `t0u1v2w3x4y5` → `u1v2w3x4y5z6`
