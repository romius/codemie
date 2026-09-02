# Per-Request `require_confirmation` Flag

**Date:** 2026-08-10
**Branch:** EPMCDME-13903_chat-tool-call-confirmations

## Context

The existing tool-call confirmation flow is assistant-level: when `assistant.tool_permissions.require_confirmation = True`, every tool call in every request to that assistant must be confirmed. This spec adds a per-request opt-in so callers can require confirmation for a single request without modifying the assistant configuration.

Backend handoff: `POST v1/assistants/{entityId}/model` now accepts an optional `require_confirmation` field. This spec covers implementing that field end-to-end in the backend.

## Priority Rule

```
assistant.tool_permissions.require_confirmation = True  →  always require (hard override)
request.require_confirmation = True                     →  require for this request only
request.require_confirmation = False / None             →  auto-approve (unless assistant overrides)
```

The assistant-level flag is a hard ceiling — the request flag cannot disable it.

## Design

### 1. `AssistantChatRequest` — new field

File: `src/codemie/core/models.py`

Add alongside existing per-request override fields (`enable_web_search`, `enable_code_interpreter`, `disable_cache`):

```python
require_confirmation: Optional[bool] = Field(
    default=None,
    description=(
        "Require tool-call confirmation for this request. "
        "Ignored if the assistant already requires confirmation."
    ),
)
```

No migration needed — this is a request body field, not persisted.

### 2. `ToolPermissionsService.get_effective_permissions()` — priority logic

File: `src/codemie/service/tool_permissions_service.py`

Add `require_confirmation_override: Optional[bool] = None` parameter. Implement priority:

```python
def get_effective_permissions(
    self,
    assistant: AssistantBase,
    user: Optional[User] = None,
    require_confirmation_override: Optional[bool] = None,
) -> ToolPermissionsConfig:
    base = assistant.tool_permissions or ToolPermissionsConfig()
    if base.require_confirmation:
        return base  # assistant-level hard override, request cannot disable it
    if require_confirmation_override:
        return ToolPermissionsConfig(require_confirmation=True)
    return base
```

The new parameter is keyword-only with a default of `None`, so all existing callers continue to work without changes.

### 3. `AssistantEngineBuilder.configure_agent_kwargs()` — thread the override

File: `src/codemie/service/assistant/assistant_engine_builder.py`

Pass `request.require_confirmation` when calling `get_effective_permissions`:

```python
permissions = ToolPermissionsService().get_effective_permissions(
    assistant,
    require_confirmation_override=request.require_confirmation,
)
```

## Data Flow

```
POST /assistants/{id}/model  { "require_confirmation": true }
  → AssistantChatRequest.require_confirmation = True
  → configure_agent_kwargs(request=...)
  → ToolPermissionsService.get_effective_permissions(
        assistant,
        require_confirmation_override=True
    )
  → ToolPermissionsConfig(require_confirmation=True)
  → agent_kwargs["require_tool_confirmation"] = True
  → LangGraph interrupt_before=["tools"] armed
```

## Out of Scope

- No changes to `POST v1/conversations` or the conversation entity.
- No changes to the tool-call resume endpoint.
- No changes to `AssistantConfiguration` (field lives only on `AssistantBase`/request, not config).
- `IdeChatRequest` inherits from `AssistantChatRequest` and picks up the field automatically.

## Files Changed

| File | Change |
|---|---|
| `src/codemie/core/models.py` | Add `require_confirmation: Optional[bool]` to `AssistantChatRequest` |
| `src/codemie/service/tool_permissions_service.py` | Add `require_confirmation_override` param + priority logic |
| `src/codemie/service/assistant/assistant_engine_builder.py` | Pass `request.require_confirmation` to `get_effective_permissions` |
