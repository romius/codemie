# Frontend Handoff — EPMCDME-13903: Chat Tool Call Confirmations

**Branch:** `EPMCDME-13903_chat-tool-call-confirmations`
**Date:** 2026-08-17

---

## 1. ⚠️ Breaking Changes

None for currently-deployed frontend code. All new fields are optional and additive. Existing chat flows continue to work with no changes.

---

## 2. New Endpoints

### `POST /api/v1/assistants/{assistant_id}/model/tool-call/resume`

Resume a paused conversation after the user approves or denies a pending tool call.

```
Request body:
{
  conversation_id: string,   // required — the conversation that is paused
  action: "allow" | "deny",  // required — user's decision
  stream: boolean            // optional, default true — whether to stream the resumed response
}

Response: standard streaming assistant response (same SSE stream as /ask)
Status codes:
  200  — resume started (response streamed)
  404  — no pending tool call found for conversation_id
  403  — user cannot access this assistant
```

**When to call:** call immediately after the user clicks Approve or Deny on the tool confirmation UI. Pass the `conversation_id` from the paused conversation. The response is a streaming SSE response identical in shape to a normal `/ask` call.

---

## 3. Modified Endpoints / Shapes

### Assistant create / update — new `tool_permissions` field

`POST /api/v1/assistants` and `PATCH /api/v1/assistants/{id}` now accept an optional `tool_permissions` object on the assistant body:

```typescript
interface ToolPermissionsConfig {
  tool_call_policy: "auto_approve" | "approve_for_me" | "ask_for_approval";
  // default: "auto_approve"
  allow_override: boolean;
  // default: true — whether API callers / conversation policy can override this
}

// On AssistantCreateRequest / AssistantUpdateRequest:
tool_permissions?: ToolPermissionsConfig | null
```

The field is also returned on `GET /api/v1/assistants/{id}` and list responses (may be `null` when not set).

### `GET /api/v1/conversations/{id}` — new `tool_call_policy` field

`ConversationResponse` now includes:

```typescript
tool_call_policy?: "auto_approve" | "approve_for_me" | "ask_for_approval" | null
```

This is the per-conversation policy override stored when a user starts a conversation with a non-default policy. Null means the assistant's own policy applies.

---

## 4. New / Changed Data Shapes

### `ToolCallPolicy` enum

| Value | Meaning | Recommended UI state |
|---|---|---|
| `auto_approve` | Execute all tool calls immediately — no confirmation prompt | No confirmation UI needed |
| `approve_for_me` | Agent classifies tool safety, confirms only "unsafe" calls automatically; safe calls run freely | Show confirmation UI only when backend interrupts |
| `ask_for_approval` | Every tool call pauses for user confirmation before executing | Always show confirmation UI on interrupt |

The customer-level config can set a **floor** — e.g. if the customer enforces `ask_for_approval`, individual assistant settings can only be stricter (or equal), never more permissive. The frontend does not need to enforce this; it is enforced server-side. Just show the confirmation UI whenever the backend interrupts.

### `ToolCallPendingEvent` — emitted inside the SSE stream on interrupt

When the agent is paused waiting for tool confirmation, the existing SSE `thought` frame is sent with `interrupted: true`:

```typescript
// Inside the existing streaming thought object:
{
  id: string,
  author_name: string,      // tool name (e.g. "web_search")
  author_type: "Tool",
  input_text: string,       // JSON-stringified tool args
  message: "TOOL_CALL_PENDING",   // literal sentinel value
  in_progress: false,
  interrupted: true,        // NEW — signals this is a confirmation pause
  aborted: false
}
```

This is not a new SSE frame type — it arrives via the existing `thought` channel. The new signals are `interrupted: true` and `message === "TOOL_CALL_PENDING"`.

The pending tool call details (tool name, args) are embedded in the thought:
- `author_name` → display name of the tool
- `input_text` → JSON string of the tool's arguments

### `AssistantChatRequest` — new optional `tool_call_policy` field

Per-request policy override (ignored if the assistant has `allow_override: false`):

```typescript
// Added to the chat request body:
tool_call_policy?: "auto_approve" | "approve_for_me" | "ask_for_approval" | null
```

---

## 5. Streaming / Real-time Changes

### Modified: `thought` SSE frame — two new boolean fields

Existing thought frames now carry:

```typescript
interrupted?: boolean   // true = agent paused, waiting for tool confirmation
aborted?: boolean       // true = thought was aborted/cancelled
```

**Existing thought-handling code needs updating:** if you accumulate `thought.message` across chunks, you must now check `interrupted` — when `interrupted` becomes `true`, **reset the accumulated message** to the new chunk rather than appending. Previously, thought messages only ever grew. After an interrupt, the backend sends a fresh message string for the interrupted state.

---

## 6. What Requires No Frontend Changes

- **Customer-config `features:tool_permissions` flag** — the backend enforces the policy floor; the frontend just responds to interrupts as described above.
- **Internal LangGraph checkpoint storage** — transparent persistence of paused graph state; no API surface change.
- **Token cost tracking changes** — LiteLLM cache-hit skipping; no impact on cost display.
- **History sanitization** (`sanitize_rich_history_for_llm`) — internal change to message preprocessing; no API change.
- **`ToolCallPolicy` on `Conversation` DB model** — persisted by the backend, returned in `ConversationResponse.tool_call_policy`; no action needed unless your UI wants to display or restore the per-conversation policy.
- **`approve_for_me` classification** — the backend decides which tools are "safe" vs "unsafe"; the frontend just shows the confirmation UI when an interrupt arrives.

---

## 7. Frontend Action Checklist

- [ ] **Detect tool confirmation interrupt** — when an SSE thought frame arrives with `interrupted: true` and `message === "TOOL_CALL_PENDING"`, show the tool confirmation UI instead of treating it as a normal thought
- [ ] **Show confirmation dialog** with tool name (`author_name`) and arguments (`input_text`, parsed from JSON) to the user
- [ ] **Call `POST /api/v1/assistants/{assistant_id}/model/tool-call/resume`** with `{ conversation_id, action: "allow" | "deny", stream: true }` when the user approves or denies
- [ ] **Handle `404` from resume endpoint** gracefully (e.g. confirmation timed out or was already answered)
- [ ] **Fix thought message accumulation** — when `interrupted: true` arrives, reset the accumulated message rather than appending (see §5)
- [ ] **Add `tool_call_policy` field to the assistant editor** — optional `ToolPermissionsConfig` block with `tool_call_policy` dropdown and `allow_override` toggle, sent on create/update
- [ ] **Display `tool_call_policy` in conversation list / detail** — `ConversationResponse.tool_call_policy` is now returned; show it if the UI displays per-conversation settings
- [ ] **Optionally send `tool_call_policy` on chat request** — if the UI offers a per-request override (e.g. a session-level setting), send it in the chat request body
