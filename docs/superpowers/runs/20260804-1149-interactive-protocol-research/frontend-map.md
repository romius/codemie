# Frontend implementation map for interactive elements (codemie-ui)

Source: Explore agent, 2026-08-04. Feature EPMCDME-13259; spec:
`codemie-ui/docs/superpowers/tasks/2026-07-16-epmcdme-13259-interactive-chat-input/spec.md`
("A2UI-like fixed-schema payloads in the NDJSON protocol; CopilotKit/AG-UI rejected").

## 1. Types/schema

`src/types/entity/interactive.ts` (121 lines) — a mirror of backend `core/interactive.py`:
9 element types (text, column, row, button, multiple_choice, dropdown, date_picker,
text_field, checkbox), `InteractiveRequest {request_id, surface}`,
`InteractiveResponse {request_id, kind: action|choice|form|submit|text_fallback, payload}`,
`InteractiveFeaturesConfig {action_buttons, choice, short_forms}`.
The frontend always sends kind='submit' (`{action, answers}`); legacy kinds are declared but unused.

**There is NO Zod/schema validation of the incoming protocol** — only runtime guards
(`Array.isArray`, defensive tree traversal) + `InteractiveErrorBoundary`. NDJSON parser:
`src/utils/stream.ts:77-115` (manual split on `}\s*{`, buffering of the incomplete tail,
returns any[]). Client-side validation of user input is done manually in
`InteractiveElements/utils.ts` (validateTextField/Checkbox/Dropdown/Date, safeRegex with
ReDoS protection, parity with the server-side fullmatch).

## 2. Rendering (3 layers)

1. `registry.ts` (72) — catalog metadata for the assistant configuration UI (not rendering).
2. `elementHandlers/` — one entry per type: `types.ts` (SurfaceContext, ElementHandler
   {render, seed?, validate?, answer?, summary?}, HandlerMap — compile-time mapping),
   `handlers.tsx` (282 lines, 9 handlers).
3. `InteractiveSurface.tsx` (158) — generic container: value-by-id state, errors, shared
   submit, auto Submit button if the surface has no buttons.

Controls are our own wrappers over PrimeReact: RadioButton/Checkbox/Select (=primereact),
DatePicker (react-datepicker), Input and Button — native + Tailwind.
multiple_choice cap<=1 → RadioButton, cap>1 → Checkbox.

Embedding: NOT markdown and NOT a separate message type — an optional field on a regular
assistant message, `ChatMessage.interactiveRequest` (conversation.ts:271). Chain:
ChatAiMessage → (after the Markdown body) ChatAiInteractiveBlock → InteractiveErrorBoundary →
InteractiveSurface.

## 3. Interaction

Submit: validate → answers via handler.answer → buildDisplayText (label: value · …) →
`chatGenerationStore.submitInteractiveResponse` (chatGeneration.ts:560-585, finds the
owner message by request_id for multi-assistant chats) → `createChatGeneration`
with `interactiveResponse` in ChatRequest → regular `POST v1/assistants/{id}/model` (no dedicated
endpoint). Optimistic user chip; rollback on error via `_removeOptimisticTurn`
(chatGeneration.ts:923-954) + toast. Block states: active / submitted (read-only, seed of
previous values, the selected button is marked) / stale (unanswered and not the latest turn →
disabled). Re-answer: an Edit button unlocks the form (not a text editor), submit with
replaceHistoryIndex → turn replacement. Free text (ChatPrompt) is not blocked (text fallback).

## 4. Streaming

`interactive_request` arrives as a whole object in a separate NDJSON chunk (no partial JSON).
`_handleChunk` (chatGeneration.ts:1078-1107): interactive_request / thought /
generated_chunk — mutually exclusive branches. Stream end — only the `last` flag (a terminal
chunk with interactive_request also closes it). While generation is in progress, the form is disabled (isChatBusy).

## 5. State/storage

valtio store (`chatGenerationStore`, 1193 lines; chatsStore: history = ChatMessage[][]).
Form draft — only useState in InteractiveSurface (lost on remount).
"Answered" is derived by request_id (ChatAiInteractiveBlock.tsx:55-68, last group with
a response → last variant). After reload: BE returns camelCase
interactiveRequest/Response (chatHelpers.ts:161-162), the block is read-only with prefill
(handler.seed). User chip: `✓ {text}` instead of the body (ChatUserMessage.tsx:199-214).

## 6. Flags/settings

The `features:interactiveElements` flag only controls visibility of the assistant config section
(InteractiveFeaturesAccordion.tsx:31-35); rendering the block in the chat is not gated by the flag (emission
is gated on the backend). Assistant setting: a single "Enable interactive features" Switch →
all three features true / null (granularity exists in the API, not in the UI).

## 7. Scope (frontend)

Core ~1051 lines (interactive.ts 121, InteractiveSurface 158, registry 72, utils 147,
ErrorBoundary 58, handlers.tsx 282, types.ts 73, index 17, utils/interactive.ts 24,
ChatAiInteractiveBlock 99). Edits to existing files ~200 lines (chatGeneration.ts,
conversation.ts, chatGeneration.ts types, chatHelpers, stream.ts, featureFlags,
ChatAiMessage, ChatUserMessage, ChatHistoryGroup, assistant.ts, AssistantForm,
InteractiveFeaturesAccordion/Section 137, store/utils/assistants). Tests ~1436 lines
(InteractiveSurface.test 551, registry.test 58, chatGeneration.interactive.test 341,
ChatAiInteractiveBlock.test 232, ChatUserMessageChip.test 130,
InteractiveFeaturesSection.integration.test 124).

## Notes

1. Dead code: `isFormSurface` (utils.ts:145-147), `isInteractiveRequestAnswered`
   (only in tests, the production logic is inlined).
2. Unknown element type → getElementHandler undefined → crash → ErrorBoundary
   (the block is replaced with a placeholder) — extending the catalog requires a synchronized BE+FE release.
3. There is no runtime schema validation of the incoming request.
