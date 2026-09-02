# Spec: Enforce Tool Call Policy via Customer Config

## Goal

Allow platform operators to set a minimum (floor) `tool_call_policy` in the customer config YAML.
When enabled, this floor cannot be overridden by API callers, conversation settings, or assistant config.

---

## Background

`ToolCallPolicy` has three values in strictness order (most → least restrictive):

1. `ask_for_approval` — user must approve each tool call
2. `approve_for_me` — system approves on behalf of the user
3. `auto_approve` — tools run without any prompt

The existing `ToolPermissionsService` resolves the effective policy via a priority chain:
request-level → conversation-level → assistant config. The customer config floor is applied
**after** that chain and always wins when it is stricter than the resolved value.

---

## Changes

### 1. New `CustomerToolPermissionsConfig` model

File: `src/codemie/configs/customer_config.py`

A dedicated model for the customer-level enforcement block. `tool_call_policy` is optional
so operators can enable the feature without setting a floor yet:

```python
class CustomerToolPermissionsConfig(BaseModel):
    enabled: bool = True                              # feature-wide toggle
    tool_call_policy: Optional[ToolCallPolicy] = None # floor; None = no enforcement
```

- `enabled: false` — feature is off entirely; no enforcement regardless of `tool_call_policy`.
- `enabled: true`, `tool_call_policy: null/omitted` — feature on but no floor set; existing chain applies.
- `enabled: true`, `tool_call_policy: "ask_for_approval"` — floor enforced.

### 2. `CustomerConfig` — new `tool_permissions` section

File: `src/codemie/configs/customer_config.py`

New optional field:
```python
tool_permissions: Optional[CustomerToolPermissionsConfig] = None
```

Loaded in `_load_config` from the YAML key `tool_permissions`. When the key is absent,
field stays `None` and no enforcement is applied.

YAML examples:
```yaml
# enforcement active with a floor
tool_permissions:
  enabled: true
  tool_call_policy: "ask_for_approval"

# feature on, no floor
tool_permissions:
  enabled: true

# feature off
tool_permissions:
  enabled: false
```

### 3. `ToolPermissionsService` — apply floor internally

File: `src/codemie/service/tool_permissions_service.py`

After the existing priority-chain resolution, the service reads `customer_config.tool_permissions`
directly and applies the floor when both conditions hold:

```python
enforcement = customer_config.tool_permissions
if enforcement and enforcement.enabled and enforcement.tool_call_policy:
    if _is_stricter(enforcement.tool_call_policy, effective_policy):
        effective_policy = enforcement.tool_call_policy
```

`_is_stricter(a, b)` is a private module-level function returning `True` when policy `a`
is stricter than policy `b`.

No changes to the method signature or its callers.

---

## Behaviour

| Customer `enabled` | Customer `tool_call_policy` | API request | Effective |
|---|---|---|---|
| — (not set) | — | any | existing chain |
| `false` | any | any | existing chain |
| `true` | not set | any | existing chain |
| `true` | `ask_for_approval` | `auto_approve` | `ask_for_approval` |
| `true` | `ask_for_approval` | `ask_for_approval` | `ask_for_approval` |
| `true` | `approve_for_me` | `auto_approve` | `approve_for_me` |
| `true` | `approve_for_me` | `ask_for_approval` | `ask_for_approval` (already stricter) |

---

## Out of scope

- No DB migration.
- No API surface change — enforcement is invisible to callers.
- `ToolPermissionsConfig` in `assistant.py` is unchanged.
- `assistant_engine_builder.py` requires no change.
- The `allow_override` field on assistant-level config is unchanged; customer enforcement is
  additive and sits above the assistant layer.
