# Frontend Handoff — EPMCDME-13903: Tool Call Confirmations

**Branch:** `EPMCDME-13903_chat-tool-call-confirmations`
**MR:** https://gitbud.epam.com/epm-cdme/codemie/-/merge_requests/3941
**Date:** 2026-08-06

---

## 1. ⚠️ Breaking Changes

**None.**

All additions are optional/nullable fields. Existing SSE parsers and conversation-fetch code continue to work without changes. Confirmation dialogs are only shown when the assistant's `tool_permissions.require_confirmation` is `true` — assistants without that flag behave identically to pre-MR.

---

## 2. New Endpoints

### `POST /api/assistants/{assistant_id}/model/tool-call/resume`

Resumes a paused conversation after the user approves or denies a tool call.

**Auth:** Bearer token required. The calling user must have WRITE access to the conversation (403 otherwise).

**Request body:**

```typescript
{
  conversation_id: string;        // ID of the paused conversation
  pending_tool_call_id: string;   // Echo of tool_call_pending.pending_tool_call_id from the SSE event
  action: "allow" | "deny";       // User's decision
  stream?: boolean;               // default true — always use true
}
```

**Response:** NDJSON streaming response, identical format to `POST /api/assistants/{id}/model`. Each line is a JSON-serialized `StreamedGenerationResult`.

**Status codes:**

| Code | Meaning |
|------|---------|
| 200  | Stream started; read NDJSON lines until `last: true` |
| 403  | Conversation not found or caller lacks write access |
| 404  | No pending checkpoint for this conversation (already resumed or expired) |
| 422  | Assistant is type A2A — tool call confirmation not supported |

**Notes:**
- `action="allow"` — the tool executes normally; the LLM receives the tool result and continues.
- `action="deny"` — the tool is skipped; the LLM sees `"Tool call was denied by the user."` and continues (produces a human-readable reply explaining it couldn't complete the action).
- Only one pending tool call per conversation at a time. Calling resume clears the checkpoint.

---

## 3. Modified Endpoints

### `POST /api/assistants/{id}/model` (chat endpoint, and slug variant)

**What changed:** The SSE stream may now terminate with a `tool_call_pending` event instead of a normal completion. See §5 — Streaming Changes.

**What stayed the same:** All existing fields, error handling, and streaming mechanics are unchanged. The new field is additive.

---

### `GET /v1/conversations/{conversation_id}`

**What changed:** `ConversationResponse` has one new optional field:

```typescript
pending_tool_call?: {
  pending_tool_call_id: string;
  tool_name: string;
  tool_args: Record<string, unknown>;
} | null;
```

**Before:** field absent.
**After:** `null` when idle; populated when the conversation is paused waiting for tool call confirmation.

**Affected component:** wherever `GET /v1/conversations/{id}` is fetched on page load or navigation — use `pending_tool_call` to restore the Allow/Deny dialog state after a page refresh.

---

### `POST /api/assistants/{id}/model` — request body

**What changed:** `AssistantRequest` (used by the assistant create/update form) now accepts an optional `tool_permissions` field:

```typescript
tool_permissions?: {
  require_confirmation: boolean;  // default false
} | null;
```

This is not required and defaults to `false` if omitted. Only add UI for it when the feature flag is ready on the frontend.

---

## 4. New/Changed Data Shapes

### `ToolCallPendingEvent`

```typescript
interface ToolCallPendingEvent {
  pending_tool_call_id: string;   // LangGraph tool call ID — echo back verbatim on resume
  tool_name: string;              // Human-readable name of the tool to confirm (e.g. "search_confluence")
  tool_args: Record<string, unknown>;  // Arguments the tool would receive — show to user in dialog
}
```

**Complete example:**

```json
{
  "pending_tool_call_id": "call_abc123",
  "tool_name": "search_confluence",
  "tool_args": { "query": "how to configure SSO" }
}
```

### `ToolPermissionsConfig` (on assistant)

```typescript
interface ToolPermissionsConfig {
  require_confirmation: boolean;  // false = no dialogs; true = pause before every tool call
}
```

Stored on the assistant. Read via `assistant.tool_permissions` in any assistant fetch response. `null` means the same as `{ require_confirmation: false }`.

---

## 5. Streaming / Real-time Changes

### New SSE field: `tool_call_pending`

The `StreamedGenerationResult` frame (every NDJSON line from the chat endpoint) now has an optional field:

```typescript
interface StreamedGenerationResult {
  // ... existing fields unchanged ...
  tool_call_pending?: ToolCallPendingEvent | null;
}
```

**When it fires:** Exactly once per interrupted turn — when the graph pauses before a tool call. This frame always has `last: true`.

**New frame handling logic:**

```typescript
if (chunk.last && chunk.tool_call_pending) {
  // Conversation is PAUSED — show Allow/Deny dialog
  // Store chunk.tool_call_pending.pending_tool_call_id for the resume call
  showToolConfirmationDialog({
    toolName: chunk.tool_call_pending.tool_name,
    toolArgs: chunk.tool_call_pending.tool_args,
    pendingToolCallId: chunk.tool_call_pending.pending_tool_call_id,
  });
} else if (chunk.last) {
  // Normal completion — hide dialog if open, show response
}
```

**Existing frame-handling code update required:** Yes — any code that treats `last: true` as unconditional stream completion needs to branch on `tool_call_pending`. If `tool_call_pending` is present, the SSE stream is closed but the conversation is not done.

**Precedent:** This is the same pattern as `interactive_request` (the `request_user_input` tool). If the frontend already handles `interactive_request`, the `tool_call_pending` shape follows the same flow.

---

## 6. What Requires No Frontend Changes

- `ConversationCheckpointSaver` / `ConversationCheckpointService` — internal backend checkpoint storage; never exposed.
- `pending_checkpoint` column — server-only; never appears in any API response.
- Alembic migration — transparent to the UI; just adds database columns.
- `ToolPermissionsService` — server-side permission resolver; no API surface.
- Assistants without `tool_permissions.require_confirmation = true` — zero behavior change. `InMemorySaver` and `thread_id = "thread"` are still used; the SSE stream closes normally.
- The `ConversationResponse.pending_tool_call` field when it is `null` — no UI action needed; just ignore it.

---

## 7. Frontend Action Checklist

```
- [ ] Update SSE stream handler: when chunk.last === true AND chunk.tool_call_pending is set,
      show the Allow/Deny confirmation dialog instead of treating the stream as complete.

- [ ] Build the Allow/Deny dialog component:
      - Display tool_call_pending.tool_name and tool_call_pending.tool_args to the user.
      - "Allow" button → POST /api/assistants/{id}/model/tool-call/resume
        { conversation_id, pending_tool_call_id, action: "allow", stream: true }
      - "Deny" button  → POST /api/assistants/{id}/model/tool-call/resume
        { conversation_id, pending_tool_call_id, action: "deny",  stream: true }
      - Stream the resume response the same way as the normal chat SSE stream.

- [ ] Page-refresh recovery: after fetching GET /v1/conversations/{id}, if
      response.pending_tool_call is non-null, restore the Allow/Deny dialog
      using pending_tool_call.tool_name, .tool_args, and .pending_tool_call_id.

- [ ] Handle 403 from POST .../tool-call/resume: show an appropriate error
      ("You no longer have access to this conversation").

- [ ] Handle 404 from POST .../tool-call/resume: show an appropriate error
      ("This tool call confirmation has expired or was already acted on").

- [ ] Handle 422 from POST .../tool-call/resume: this means the assistant type
      does not support confirmations; surface as a generic "not supported" message.

- [ ] (When assistant settings UI is ready) Add a toggle for
      tool_permissions.require_confirmation on the assistant create/edit form.
      Field is optional; send null or omit to use default (false).
```
