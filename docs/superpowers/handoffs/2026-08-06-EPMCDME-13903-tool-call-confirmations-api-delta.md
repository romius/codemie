# API Delta — EPMCDME-13903: Tool Call Confirmations (post-review changes)

**Branch:** `EPMCDME-13903_chat-tool-call-confirmations`
**Date:** 2026-08-06
**Supersedes sections 2, 5, and 7 of:** `2026-08-06-EPMCDME-13903-tool-call-confirmations-frontend-handoff.md`

This document covers the API surface changes made after the initial handoff doc was written.
Read this alongside the original handoff — only the sections below changed.

---

## ⚠️ Breaking Changes vs. Initial Handoff

### 1. `tool_call_pending` SSE field — REMOVED

The original design added a `tool_call_pending` field to `StreamedGenerationResult`. **This field does not exist in the final implementation.**

| | Original design | Final implementation |
|---|---|---|
| Interrupt signal | `chunk.tool_call_pending` set on the `last: true` frame | `chunk.thought` with `interrupted: true` |
| Field on `StreamedGenerationResult` | `tool_call_pending?: ToolCallPendingEvent` | Not present |

**What to look for instead:**

```typescript
if (chunk.thought?.interrupted === true) {
  // Conversation is PAUSED — show Allow/Deny dialog
  // Use chunk.thought.author_name for tool name
  // Use chunk.thought.input_text (JSON string) for tool args
}
```

### 2. `pending_tool_call_id` removed from resume request body

The original design required the frontend to echo back the tool call ID on resume. **This field has been removed.**

**Before (original design):**
```typescript
{
  conversation_id: string;
  pending_tool_call_id: string;  // ← REMOVED
  action: "allow" | "deny";
  stream?: boolean;
}
```

**After (final implementation):**
```typescript
{
  conversation_id: string;
  action: "allow" | "deny";
  stream?: boolean;
}
```

The server resolves the tool call ID internally from the stored pending state. The frontend does not need to track or echo it.

---

## Updated: SSE Streaming — Interrupt Signal

When a tool call requires confirmation, the agent closes the stream with a `thought` chunk:

```typescript
// The interrupt frame — look for thought.interrupted === true
interface InterruptedThought {
  id: string;
  author_name: string;          // Tool name, e.g. "search_confluence"
  author_type: "Tool";
  input_text: string;           // JSON-encoded tool args, e.g. '{"query":"how to configure SSO"}'
  message: string;              // "Tool call is waiting for user approval."
  in_progress: false;
  interrupted: true;
}
```

**Updated frame-handling logic:**

```typescript
if (chunk.thought?.interrupted === true) {
  const toolName = chunk.thought.author_name;
  const toolArgs = JSON.parse(chunk.thought.input_text ?? '{}');
  showToolConfirmationDialog({ toolName, toolArgs });
} else if (chunk.thought?.aborted === true) {
  // Deny path completed — the thought confirms the tool was rejected
  // author_name = tool name, input_text = tool args (same shape as interrupted)
} else if (chunk.last) {
  // Normal completion
}
```

The `pending_tool_call` field on `GET /v1/conversations/{id}` still exists for page-refresh recovery and still carries `tool_name` and `tool_args` (unchanged from the original handoff).

---

## Updated: Resume Endpoint Request Body

```
POST /api/assistants/{assistant_id}/model/tool-call/resume
```

```typescript
// Final request shape
{
  conversation_id: string;   // required
  action: "allow" | "deny";  // required
  stream?: boolean;           // default true
}
```

Everything else about the endpoint (auth, response format, status codes) is unchanged from the original handoff.

---

## Updated: Frontend Action Checklist (delta only)

```
- [ ] REMOVE any code that reads chunk.tool_call_pending — that field does not exist.

- [ ] Detect interrupt via chunk.thought.interrupted === true instead:
      toolName  = chunk.thought.author_name
      toolArgs  = JSON.parse(chunk.thought.input_text)

- [ ] On deny completion, detect chunk.thought.aborted === true to confirm
      the denial was recorded (optional — the resume response stream covers this).

- [ ] REMOVE pending_tool_call_id from the POST .../tool-call/resume request body.
      Send only { conversation_id, action, stream: true }.

- [ ] Page-refresh recovery via GET /v1/conversations/{id} is UNCHANGED —
      response.pending_tool_call still carries tool_name and tool_args.
      Use those to populate the dialog without needing pending_tool_call_id.
```
