# Enforce Tool Call Policy via Customer Config — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `tool_permissions` block to `customer-config.yaml` that enforces a minimum `tool_call_policy` floor which cannot be overridden by API callers.

**Architecture:** A new `CustomerToolPermissionsConfig` Pydantic model is added to `customer_config.py` alongside its loading logic. `ToolPermissionsService.get_effective_permissions` reads the singleton `customer_config` directly after its existing priority-chain resolution and clamps the result to the floor when it is stricter.

**Tech Stack:** Python 3.12, Pydantic v2, PyYAML, pytest, existing `ToolCallPolicy` enum from `codemie.core.models`.

## Global Constraints

- Do not modify `ToolPermissionsConfig` in `src/codemie/rest_api/models/assistant.py`.
- Do not modify `assistant_engine_builder.py`.
- No DB migration.
- No API surface change — enforcement is invisible to callers.
- Commit messages must follow `EPMCDME-13903: <description>` format.
- Run `make ruff` before every commit; fix any issues before committing.

---

## File Map

| File | Action | Purpose |
|---|---|---|
| `src/codemie/configs/customer_config.py` | Modify | Add `CustomerToolPermissionsConfig` model and `tool_permissions` field + YAML loading |
| `src/codemie/service/tool_permissions_service.py` | Modify | Add `_is_stricter`, apply floor from `customer_config` |
| `tests/codemie/configs/test_customer_config.py` | Modify | Add tests for `CustomerToolPermissionsConfig` YAML loading |
| `tests/codemie/service/test_tool_permissions_service.py` | Modify | Replace stale `require_confirmation` tests; add floor enforcement tests |

---

### Task 1: Add `CustomerToolPermissionsConfig` to `CustomerConfig`

**Files:**
- Modify: `src/codemie/configs/customer_config.py`
- Test: `tests/codemie/configs/test_customer_config.py`

**Interfaces:**
- Produces: `CustomerToolPermissionsConfig(enabled: bool, tool_call_policy: Optional[ToolCallPolicy])` importable from `codemie.configs.customer_config`
- Produces: `CustomerConfig.tool_permissions: Optional[CustomerToolPermissionsConfig]` — `None` when YAML key is absent

- [ ] **Step 1: Write failing tests**

Add to `tests/codemie/configs/test_customer_config.py` (after existing imports, add `from codemie.core.models import ToolCallPolicy` and `from codemie.configs.customer_config import CustomerToolPermissionsConfig`):

```python
from codemie.core.models import ToolCallPolicy
from codemie.configs.customer_config import CustomerToolPermissionsConfig


class TestCustomerToolPermissionsConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = CustomerToolPermissionsConfig()
        self.assertTrue(cfg.enabled)
        self.assertIsNone(cfg.tool_call_policy)

    def test_explicit_values(self):
        cfg = CustomerToolPermissionsConfig(
            enabled=False,
            tool_call_policy=ToolCallPolicy.ASK_FOR_APPROVAL,
        )
        self.assertFalse(cfg.enabled)
        self.assertEqual(cfg.tool_call_policy, ToolCallPolicy.ASK_FOR_APPROVAL)

    def test_tool_call_policy_parsed_from_string(self):
        cfg = CustomerToolPermissionsConfig(tool_call_policy="auto_approve")
        self.assertEqual(cfg.tool_call_policy, ToolCallPolicy.AUTO_APPROVE)


# Helper that patches CustomerConfig to load from a YAML string.
def _make_config(yaml_text: str) -> "CustomerConfig":
    """Build a CustomerConfig from an inline YAML string (no real file needed)."""
    import io
    import yaml as _yaml
    from unittest.mock import patch, MagicMock

    data = _yaml.safe_load(yaml_text)
    # Minimal valid components required by CustomerConfig validator
    full_data = {
        "components": [{"id": "adminActions", "settings": {"enabled": True}}],
        **data,
    }
    with patch.object(CustomerConfig, "_read_config_file", return_value=_yaml.dump(full_data)):
        return CustomerConfig()


class TestCustomerConfigToolPermissions(unittest.TestCase):
    def test_tool_permissions_absent_gives_none(self):
        cfg = _make_config("")
        self.assertIsNone(cfg.tool_permissions)

    def test_tool_permissions_loaded_with_floor(self):
        cfg = _make_config(
            "tool_permissions:\n  enabled: true\n  tool_call_policy: ask_for_approval\n"
        )
        self.assertIsNotNone(cfg.tool_permissions)
        self.assertTrue(cfg.tool_permissions.enabled)
        self.assertEqual(cfg.tool_permissions.tool_call_policy, ToolCallPolicy.ASK_FOR_APPROVAL)

    def test_tool_permissions_loaded_enabled_only(self):
        cfg = _make_config("tool_permissions:\n  enabled: true\n")
        self.assertIsNotNone(cfg.tool_permissions)
        self.assertTrue(cfg.tool_permissions.enabled)
        self.assertIsNone(cfg.tool_permissions.tool_call_policy)

    def test_tool_permissions_disabled(self):
        cfg = _make_config(
            "tool_permissions:\n  enabled: false\n  tool_call_policy: ask_for_approval\n"
        )
        self.assertFalse(cfg.tool_permissions.enabled)
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /path/to/repo
poetry run pytest tests/codemie/configs/test_customer_config.py::TestCustomerToolPermissionsConfig tests/codemie/configs/test_customer_config.py::TestCustomerConfigToolPermissions -v
```

Expected: `ImportError` or `AttributeError` — `CustomerToolPermissionsConfig` not yet defined.

- [ ] **Step 3: Implement `CustomerToolPermissionsConfig` and wire it into `CustomerConfig`**

In `src/codemie/configs/customer_config.py`:

1. Add import at the top of the imports block:
```python
from codemie.core.models import ToolCallPolicy
```

2. Add the new model before the `CustomerConfig` class:
```python
class CustomerToolPermissionsConfig(BaseModel):
    enabled: bool = True
    tool_call_policy: Optional[ToolCallPolicy] = None
```

3. Add field to `CustomerConfig` (after `tool_defaults`):
```python
tool_permissions: Optional[CustomerToolPermissionsConfig] = None
```

4. Load it in `_load_config`, after the `self.tool_defaults = ...` line:
```python
tool_permissions_data = config_data.get("tool_permissions")
if isinstance(tool_permissions_data, dict):
    self.tool_permissions = CustomerToolPermissionsConfig(**tool_permissions_data)
else:
    self.tool_permissions = None
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
poetry run pytest tests/codemie/configs/test_customer_config.py::TestCustomerToolPermissionsConfig tests/codemie/configs/test_customer_config.py::TestCustomerConfigToolPermissions -v
```

Expected: all GREEN.

- [ ] **Step 5: Run ruff and fix any issues**

```bash
make ruff
```

- [ ] **Step 6: Commit**

```bash
git add src/codemie/configs/customer_config.py tests/codemie/configs/test_customer_config.py
git commit -m "EPMCDME-13903: add CustomerToolPermissionsConfig to CustomerConfig"
```

---

### Task 2: Apply floor in `ToolPermissionsService` + fix stale tests

**Files:**
- Modify: `src/codemie/service/tool_permissions_service.py`
- Modify: `tests/codemie/service/test_tool_permissions_service.py`

**Interfaces:**
- Consumes: `CustomerToolPermissionsConfig` from `codemie.configs.customer_config` (Task 1)
- Consumes: `customer_config` singleton from `codemie.configs.customer_config`
- `_is_stricter(a: ToolCallPolicy, b: ToolCallPolicy) -> bool` — private module-level; `True` when `a` is more restrictive than `b`
- `get_effective_permissions` signature unchanged

- [ ] **Step 1: Replace stale tests and add floor tests**

The existing tests in `tests/codemie/service/test_tool_permissions_service.py` reference `require_confirmation` which no longer exists. Replace the entire file content:

```python
import pytest
from unittest.mock import MagicMock, patch
from codemie.core.models import ToolCallPolicy
from codemie.configs.customer_config import CustomerToolPermissionsConfig
from codemie.service.tool_permissions_service import ToolPermissionsService
from codemie.rest_api.models.assistant import ToolPermissionsConfig


@pytest.fixture
def svc():
    return ToolPermissionsService()


def _assistant(policy=ToolCallPolicy.ASK_FOR_APPROVAL, allow_override=True):
    assistant = MagicMock()
    assistant.tool_permissions = ToolPermissionsConfig(
        tool_call_policy=policy,
        allow_override=allow_override,
    )
    return assistant


def _no_enforcement():
    return patch(
        "codemie.service.tool_permissions_service.customer_config",
        tool_permissions=None,
    )


def _with_enforcement(policy: ToolCallPolicy, enabled: bool = True):
    enforcement = CustomerToolPermissionsConfig(enabled=enabled, tool_call_policy=policy)
    return patch(
        "codemie.service.tool_permissions_service.customer_config",
        tool_permissions=enforcement,
    )


# --- existing chain behaviour (no customer enforcement) ---

def test_returns_assistant_policy_when_no_overrides(svc):
    with _no_enforcement():
        result = svc.get_effective_permissions(_assistant(ToolCallPolicy.ASK_FOR_APPROVAL))
    assert result.tool_call_policy == ToolCallPolicy.ASK_FOR_APPROVAL


def test_request_override_beats_assistant(svc):
    with _no_enforcement():
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.ASK_FOR_APPROVAL),
            tool_call_policy_override=ToolCallPolicy.AUTO_APPROVE,
        )
    assert result.tool_call_policy == ToolCallPolicy.AUTO_APPROVE


def test_conversation_policy_beats_assistant(svc):
    with _no_enforcement():
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.ASK_FOR_APPROVAL),
            conversation_policy=ToolCallPolicy.AUTO_APPROVE,
        )
    assert result.tool_call_policy == ToolCallPolicy.AUTO_APPROVE


def test_request_beats_conversation(svc):
    with _no_enforcement():
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.AUTO_APPROVE),
            tool_call_policy_override=ToolCallPolicy.ASK_FOR_APPROVAL,
            conversation_policy=ToolCallPolicy.AUTO_APPROVE,
        )
    assert result.tool_call_policy == ToolCallPolicy.ASK_FOR_APPROVAL


def test_allow_override_false_ignores_request(svc):
    with _no_enforcement():
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.ASK_FOR_APPROVAL, allow_override=False),
            tool_call_policy_override=ToolCallPolicy.AUTO_APPROVE,
        )
    assert result.tool_call_policy == ToolCallPolicy.ASK_FOR_APPROVAL


def test_none_tool_permissions_uses_defaults(svc):
    assistant = MagicMock()
    assistant.tool_permissions = None
    with _no_enforcement():
        result = svc.get_effective_permissions(assistant)
    assert result.tool_call_policy == ToolCallPolicy.ASK_FOR_APPROVAL


# --- customer enforcement floor ---

def test_floor_clamps_weaker_request(svc):
    """customer floor=ask_for_approval, request=auto_approve → ask_for_approval"""
    with _with_enforcement(ToolCallPolicy.ASK_FOR_APPROVAL):
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.AUTO_APPROVE),
            tool_call_policy_override=ToolCallPolicy.AUTO_APPROVE,
        )
    assert result.tool_call_policy == ToolCallPolicy.ASK_FOR_APPROVAL


def test_floor_does_not_override_stricter_request(svc):
    """customer floor=approve_for_me, request=ask_for_approval → ask_for_approval"""
    with _with_enforcement(ToolCallPolicy.APPROVE_FOR_ME):
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.AUTO_APPROVE),
            tool_call_policy_override=ToolCallPolicy.ASK_FOR_APPROVAL,
        )
    assert result.tool_call_policy == ToolCallPolicy.ASK_FOR_APPROVAL


def test_floor_approve_for_me_clamps_auto_approve(svc):
    with _with_enforcement(ToolCallPolicy.APPROVE_FOR_ME):
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.AUTO_APPROVE),
            tool_call_policy_override=ToolCallPolicy.AUTO_APPROVE,
        )
    assert result.tool_call_policy == ToolCallPolicy.APPROVE_FOR_ME


def test_disabled_enforcement_does_not_apply_floor(svc):
    with _with_enforcement(ToolCallPolicy.ASK_FOR_APPROVAL, enabled=False):
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.AUTO_APPROVE),
            tool_call_policy_override=ToolCallPolicy.AUTO_APPROVE,
        )
    assert result.tool_call_policy == ToolCallPolicy.AUTO_APPROVE


def test_enforcement_enabled_but_no_policy_does_not_clamp(svc):
    enforcement = CustomerToolPermissionsConfig(enabled=True, tool_call_policy=None)
    with patch(
        "codemie.service.tool_permissions_service.customer_config",
        tool_permissions=enforcement,
    ):
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.AUTO_APPROVE),
            tool_call_policy_override=ToolCallPolicy.AUTO_APPROVE,
        )
    assert result.tool_call_policy == ToolCallPolicy.AUTO_APPROVE
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
poetry run pytest tests/codemie/service/test_tool_permissions_service.py -v
```

Expected: failures on the floor tests (`_with_enforcement` patch target not found yet, or floor logic not applied).

- [ ] **Step 3: Implement `_is_stricter` and the floor logic**

Replace the content of `src/codemie/service/tool_permissions_service.py`:

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

from typing import Optional

from codemie.configs.customer_config import customer_config
from codemie.core.models import ToolCallPolicy
from codemie.rest_api.models.assistant import AssistantBase, ToolPermissionsConfig
from codemie.rest_api.security.user import User

_STRICTNESS: dict[ToolCallPolicy, int] = {
    ToolCallPolicy.ASK_FOR_APPROVAL: 2,
    ToolCallPolicy.APPROVE_FOR_ME: 1,
    ToolCallPolicy.AUTO_APPROVE: 0,
}


def _is_stricter(a: ToolCallPolicy, b: ToolCallPolicy) -> bool:
    return _STRICTNESS[a] > _STRICTNESS[b]


class ToolPermissionsService:
    """Resolves effective tool permission settings for an assistant.

    Priority when allow_override=True:
      request-level > conversation-level > assistant config > customer floor
    """

    def get_effective_permissions(
        self,
        assistant: AssistantBase,
        user: Optional[User] = None,
        tool_call_policy_override: Optional[ToolCallPolicy] = None,
        conversation_policy: Optional[ToolCallPolicy] = None,
    ) -> ToolPermissionsConfig:
        base = assistant.tool_permissions or ToolPermissionsConfig()
        if not base.allow_override:
            return base

        effective_policy = tool_call_policy_override or conversation_policy or base.tool_call_policy

        enforcement = customer_config.tool_permissions
        if enforcement and enforcement.enabled and enforcement.tool_call_policy:
            if _is_stricter(enforcement.tool_call_policy, effective_policy):
                effective_policy = enforcement.tool_call_policy

        return ToolPermissionsConfig(
            tool_call_policy=effective_policy,
            allow_override=base.allow_override,
        )
```

- [ ] **Step 4: Run all service tests**

```bash
poetry run pytest tests/codemie/service/test_tool_permissions_service.py -v
```

Expected: all GREEN.

- [ ] **Step 5: Run the full customer config test suite to ensure no regressions**

```bash
poetry run pytest tests/codemie/configs/test_customer_config.py tests/codemie/service/test_tool_permissions_service.py -v
```

Expected: all GREEN.

- [ ] **Step 6: Run ruff**

```bash
make ruff
```

- [ ] **Step 7: Commit**

```bash
git add src/codemie/service/tool_permissions_service.py tests/codemie/service/test_tool_permissions_service.py
git commit -m "EPMCDME-13903: enforce customer-level tool_call_policy floor in ToolPermissionsService"
```
