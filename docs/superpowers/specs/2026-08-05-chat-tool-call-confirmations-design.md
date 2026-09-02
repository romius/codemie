# Design: Chat Tool Call Confirmations

**Work item**: EPMCDME-13903  
**Date**: 2026-08-05  
**Branch**: EPMCDME-13903_chat-tool-call-confirmations

---

## 1. Architecture

When an assistant has `tool_permissions.require_confirmation = True`, the LangGraph agent graph is compiled with `interrupt_before=["tools"]`. Before any tool node executes, LangGraph suspends the graph and persists the checkpoint.

The assistant SSE response closes with a `tool_call_pending` event (see §4). The frontend renders an Allow/Deny dialog. When the user decides, the frontend calls the new resume endpoint (see §5), which loads the checkpoint and resumes the graph.

```
POST /api/assistants/{id}/model
  → LangGraphAgent (interrupt_before=["tools"])
    → graph runs → reaches tool node
    → graph suspends (LangGraph interrupt)
    → checkpoint + tool call info saved to conversations.pending_checkpoint / pending_tool_call
    → SSE stream closes with tool_call_pending + last=true

POST /api/assistants/{id}/model/tool-call/resume
  → ConversationCheckpointService.get(conversation_id)
  → LangGraphAgent.resume_allow()  → graph.stream(None, config)
    OR
  → LangGraphAgent.resume_deny(tool_call_id)  → graph.update_state + graph.stream(None, config)
  → SSE stream resumes normally
```

**Key constraint**: state must survive a server restart. Checkpoints are stored in the `conversations` table (Postgres-backed), not in-memory.

**Non-confirmation path**: assistants without `require_confirmation` continue to use `InMemorySaver` and `thread_id = "thread"` — no change, no overhead.

---

## 2. Data Model

### 2.1 `ToolPermissionsConfig` — new JSONB model

```python
class ToolPermissionsConfig(BaseModel):
    require_confirmation: bool = False
```

Stored as a JSONB column on `AssistantBase`. Structured as an object (not a flat bool) to support future per-user/per-project overrides and additional permission flags (e.g., `remember_choices`).

**Where it lives**: `AssistantBase` (not `AssistantConfiguration`). Matches the pattern of `smart_tool_selection_enabled` — a runtime behavior flag, not a versioned snapshot field.

```python
# src/codemie/rest_api/models/assistant.py — AssistantBase
tool_permissions: Optional[ToolPermissionsConfig] = SQLField(
    default=None,
    sa_column=Column(PydanticType(ToolPermissionsConfig)),
)
```

Requires an Alembic migration to add the column.

### 2.2 `pending_checkpoint` and `pending_tool_call` columns on `Conversation`

```python
# src/codemie/rest_api/models/conversation.py — Conversation
pending_checkpoint: Optional[dict] = SQLField(
    default=None,
    sa_column=Column(JSONB),
)
pending_tool_call: Optional[dict] = SQLField(
    default=None,
    sa_column=Column(JSONB),
)
```

`pending_checkpoint` stores the serialized LangGraph checkpoint for the paused turn (opaque, internal).  
`pending_tool_call` stores a lightweight `ToolCallPendingEvent` (tool name, args, ID) — human-readable, returned in `ConversationResponse` so the frontend can reconstruct the Allow/Deny dialog on page refresh without needing to parse the raw checkpoint.

Both are written together when the interrupt is detected. Both are cleared together on resume. Only one pending tool call per conversation at a time (overwrite on re-interrupt).

Requires an Alembic migration.

---

## 3. Service Layer

### 3.1 `ConversationCheckpointSaver` (LangGraph adapter)

New file: `src/codemie/agents/conversation_checkpoint_saver.py`

Implements `BaseCheckpointSaver`. Uses `conversation_id` as `thread_id`. Delegates to `ConversationCheckpointService` for persistence.

- `get_tuple(config)` → loads from `conversations.pending_checkpoint`
- `put(config, checkpoint, metadata, new_versions)` → saves to `conversations.pending_checkpoint`
- `put_writes(...)` → no-op (same policy as the existing `CheckpointSaver` in `workflows/`)
- `alist(...)` → not required for interrupt/resume

The existing `src/codemie/workflows/checkpoint_saver.py` stays unchanged (it's workflow-scoped and coupled to `WorkflowExecution`). This is a separate, conversation-scoped saver.

### 3.2 `ConversationCheckpointService`

New file: `src/codemie/service/conversation_checkpoint_service.py`

```python
class ConversationCheckpointService:
    def save(self, conversation_id: str, checkpoint: dict, tool_call: ToolCallPendingEvent) -> None
    def get(self, conversation_id: str) -> Optional[dict]
    def get_pending_tool_call(self, conversation_id: str) -> Optional[ToolCallPendingEvent]
    def clear(self, conversation_id: str) -> None
```

Writes/reads both `pending_checkpoint` and `pending_tool_call` together. `clear()` nulls both columns atomically. No business logic — just the persistence boundary so `ConversationCheckpointSaver` stays decoupled from the ORM.

### 3.3 `ToolPermissionsService`

New file: `src/codemie/service/tool_permissions_service.py`

```python
class ToolPermissionsService:
    def get_effective_permissions(
        self, assistant: AssistantBase, user: Optional[User] = None
    ) -> ToolPermissionsConfig
```

V1: returns `assistant.tool_permissions or ToolPermissionsConfig()`. The abstraction exists so per-user and per-project overrides can be layered in later without touching call sites.

### 3.4 `LangGraphAgent` changes

`src/codemie/agents/langgraph_agent.py`:

- `_build_single_agent()`: add `interrupt_before=["tools"]` to `create_smart_react_agent()` when `require_confirmation=True`
- `_get_run_config()`: use `ConversationCheckpointSaver` + `conversation_id` as `thread_id` when `require_confirmation=True`; fall back to current `InMemorySaver` otherwise
- `_stream_graph()`: after graph stream ends, check graph state — if `state.next` contains `"tools"`, extract the pending tool call, call `thread_generator.send(StreamedGenerationResult(last=True, tool_call_pending=...))`, and return without closing normally
- Add `resume_allow(conversation_id)` method → `graph.stream(None, config)` — resumes from checkpoint; graph runs the tool node, then continues. If another tool call is encountered, it interrupts again (correct behavior for multi-tool conversations).
- Add `resume_deny(conversation_id, tool_call_id)` method → `graph.update_state(config, {"messages": [deny_message]}, as_node="tools")`, then `graph.stream(None, config)` — injects denial before the tool node runs, then resumes from that injected state

The deny path injects a synthetic `ToolMessage`:
```python
deny_message = ToolMessage(
    content="Tool call was denied by the user.",
    tool_call_id=tool_call_id,
)
```

This is visible in the LLM's message history (the model knows the tool was denied) but is never rendered in the chat UI — the frontend only renders `generated_chunk` / `generated` content from `StreamedGenerationResult`.

---

## 4. SSE Event Format

The `StreamedGenerationResult` model gains one new optional field:

```python
# src/codemie/chains/base.py

class ToolCallPendingEvent(BaseModel):
    pending_tool_call_id: str   # LangGraph tool call ID, echoed back on resume
    tool_name: str
    tool_args: dict

class StreamedGenerationResult(BaseModel):
    ...
    tool_call_pending: Optional[ToolCallPendingEvent] = None
```

When interrupted, the agent sends exactly one extra SSE chunk:

```json
{
  "last": true,
  "tool_call_pending": {
    "pending_tool_call_id": "call_abc123",
    "tool_name": "search_confluence",
    "tool_args": {"query": "how to configure SSO"}
  }
}
```

`last: true` closes the stream. The frontend detects `tool_call_pending` and renders the Allow/Deny dialog. The `pending_tool_call_id` is stored by the frontend and echoed back in the resume call.

**Precedent**: identical pattern to `interactive_request: Optional[InteractiveRequest]`, which the `request_user_input` tool uses to pause the agent and send structured data to the frontend.

---

## 5. REST Endpoints

### 5.1 Conversation state on refresh — `GET /v1/conversations/{conversation_id}`

`ConversationResponse` gains one new optional field:

```python
pending_tool_call: Optional[ToolCallPendingEvent] = None
```

Populated from `Conversation.pending_tool_call` via `model_validate`. When non-null, the frontend knows the conversation is in an interrupted state and should render the Allow/Deny dialog using the embedded `pending_tool_call_id`, `tool_name`, and `tool_args`.

When the user resumes (allow or deny), `ConversationCheckpointService.clear()` nulls both columns, so subsequent GET responses will have `pending_tool_call: null`.

No changes are needed to the router or service — `ConversationResponse.model_validate(conversation)` will pick up the new field automatically once it exists on both the ORM model and the DTO.

### 5.2 Resume endpoint — `POST /api/assistants/{assistant_id}/model/tool-call/resume`

**Request body** (`ToolCallResumeRequest`):

```python
class ToolCallResumeRequest(BaseModel):
    conversation_id: str
    pending_tool_call_id: str
    action: Literal["allow", "deny"]
    stream: bool = True
```

**Behavior**:

1. Load the assistant (same auth as the chat endpoint — user must own or have access to the assistant)
2. Load `ConversationCheckpointService.get(conversation_id)` — 404 if no checkpoint
3. Build a `LangGraphAgent` in resume mode (inject `ConversationCheckpointSaver`, skip `interrupt_before` re-interrupt on resume)
4. If `action == "allow"`: call `agent.resume_allow(conversation_id)` — graph runs the tool normally
5. If `action == "deny"`: call `agent.resume_deny(conversation_id, pending_tool_call_id)` — injects denial `ToolMessage`, graph skips tool
6. Stream the response using the same `ThreadedGenerator` → `StreamingResponse` path as the normal chat endpoint
7. `ConversationCheckpointService.clear(conversation_id)` after stream completes

**Response**: identical streaming format to `POST /api/assistants/{id}/model`.

**File**: `src/codemie/rest_api/routers/assistant.py`  
**Handler**: `src/codemie/rest_api/handlers/assistant_handlers.py` (new `ToolCallResumeHandler` or extend `StandardAssistantHandler`)

---

## 6. Files Changed

| File | Change |
|---|---|
| `src/codemie/chains/base.py` | Add `ToolCallPendingEvent`, add `tool_call_pending` field to `StreamedGenerationResult` |
| `src/codemie/rest_api/models/assistant.py` | Add `tool_permissions: Optional[ToolPermissionsConfig]` to `AssistantBase` |
| `src/codemie/rest_api/models/conversation.py` | Add `pending_checkpoint`, `pending_tool_call` to `Conversation`; add `pending_tool_call` to `ConversationResponse` |
| `src/codemie/agents/conversation_checkpoint_saver.py` | New — `ConversationCheckpointSaver` |
| `src/codemie/service/conversation_checkpoint_service.py` | New — `ConversationCheckpointService` |
| `src/codemie/service/tool_permissions_service.py` | New — `ToolPermissionsService` |
| `src/codemie/agents/langgraph_agent.py` | Add interrupt support, resume methods |
| `src/codemie/rest_api/routers/assistant.py` | Add `POST .../tool-call/resume` route |
| `src/codemie/rest_api/handlers/assistant_handlers.py` | Add resume handler logic |
| `alembic/versions/xxx_add_tool_permissions_and_pending_checkpoint.py` | New migration |

---

## 7. Acceptance Criteria Mapping

| AC | Where satisfied |
|---|---|
| AC1 — pending state signal in SSE | `StreamedGenerationResult.tool_call_pending` + `last=True` |
| AC2 — Allow/Deny context in SSE | `pending_tool_call_id`, `tool_name`, `tool_args` in `ToolCallPendingEvent` |
| AC3 — Resume endpoint | `POST /api/assistants/{id}/model/tool-call/resume` |
| AC4 — Allow executes tool | `resume_allow()` → `graph.stream(None, config)` |
| AC5 — Deny skips tool | `resume_deny()` → synthetic `ToolMessage` + `graph.stream(None, config)` |
| AC6 — Chat resumes without restart | Checkpoint in DB; `conversation_id` as `thread_id` |
| AC7 — UI communicates waiting | `tool_call_pending` event; frontend displays dialog |
| AC8 — Non-confirmation path unchanged | `InMemorySaver` used when `require_confirmation` not set |
