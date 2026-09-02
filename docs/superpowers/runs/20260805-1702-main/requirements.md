# Requirements — 20260805-1702-main

**Source**: ticket:https://jiraeu.epam.com/browse/EPMCDME-13903
**Work Item**: docs/superpowers/work-items/EPMCDME-13903.md
**Original input**: |
  https://jiraeu.epam.com/browse/EPMCDME-13903;

  we need to implement allow/deny for assistants tool calls;

  key things:
  1. add the tool_permission or with other name config for assistants or for assistant configurations? investigate
  2. store the asistant conversation state and interrupt on a tool call; use langrraph interruption if possible? investigate the workflow interruptions
  3. add the new endpoint for assistants chat to resume the tool call with approe/deny

## Goal

Add a `require_tool_confirmation` flag to the `Assistant` model and implement a LangGraph interrupt/resume flow that pauses assistant execution before tool calls and lets the user allow or deny them via a new REST endpoint.

## Acceptance Criteria

1. When an assistant has `require_tool_confirmation = True` and initiates a tool call, the chat response communicates that a confirmation is pending (pending state signal in SSE stream).
2. The confirmation response includes enough context for the frontend to display an **Allow / Deny** dialog: tool name, tool arguments, and a `pending_tool_call_id` the client must echo back.
3. A new endpoint `POST /api/assistants/{assistant_id}/model/tool-call/resume` accepts `{ conversation_id, pending_tool_call_id, action: "allow" | "deny" }` and resumes the paused agent.
4. If `action = "allow"`, the agent executes the tool and returns the normal streaming response.
5. If `action = "deny"`, the agent skips the tool, injects a synthetic tool result indicating denial, and continues the LLM response without restarting the conversation.
6. After either action the chat flow resumes from where it was interrupted — the user does not restart the conversation.
7. Assistants without `require_tool_confirmation` (default `False`) behave exactly as before — no new overhead.
8. The `require_tool_confirmation` flag is surfaced in `AssistantRequest` / `AssistantBase` so it can be set via the existing assistant create/update endpoints.

## Context

### Codebase findings

#### 1. Where to add the flag
`src/codemie/rest_api/models/assistant.py` — `AssistantBase` (line 629) is the canonical place for assistant behavior flags (e.g., `smart_tool_selection_enabled` at line 657). Add `require_tool_confirmation: Optional[bool] = False` there. The `AssistantConfiguration` (line 1226) tracks versioned snapshot fields (model, prompt, toolkits); this flag should NOT live there — it controls runtime behaviour and matches the pattern of other non-versioned behaviour flags.

#### 2. LangGraph interrupt mechanism
`src/codemie/agents/langgraph_agent.py` — `LangGraphAgent` builds its graph with `InMemorySaver` and a static `thread_id = "thread"` (line 1014). For interrupt/resume to work across HTTP requests we need:
- A **persistent checkpointer** for the assistant agent (the workflow executor already has `CheckpointSaver` backed by Postgres in `src/codemie/workflows/checkpoint_saver.py`; adapt it or write a lightweight sibling scoped to `conversation_id`).
- Compile the agent graph with `interrupt_before=["tools"]` when `require_tool_confirmation=True`.
- Use `conversation_id` as the `thread_id` so state is reachable on resume.

The existing `WorkflowExecutor._interrupt_before_states` / `_handle_interrupt` / `_check_for_interruption` pattern in `src/codemie/workflows/workflow.py` (lines 1080–1090) provides the conceptual template; assistant agent must implement equivalent logic.

#### 3. New resume endpoint
Add to `src/codemie/rest_api/routers/assistant.py`:
```
POST /api/assistants/{assistant_id}/model/tool-call/resume
```
Accepts `ToolCallResumeRequest` body:
```python
class ToolCallResumeRequest(BaseModel):
    conversation_id: str
    pending_tool_call_id: str
    action: Literal["allow", "deny"]
    stream: bool = True
```
Returns the same streaming response as the normal chat endpoint.

#### 4. Interrupted-state signal
When the agent is interrupted before a tool call, the SSE stream must emit a typed event before closing:
```json
{"type": "tool_call_pending", "pending_tool_call_id": "<id>", "tool_name": "<name>", "tool_args": {...}}
```
The frontend uses this to render the Allow/Deny dialog and store the `pending_tool_call_id` for the resume call.

## Open questions

(none)
