# Backend protocol map for interactive elements (codemie)

Source: Explore agent, 2026-08-04. Feature = EPMCDME-13259 "Interactive user input in CodeMie agent chat".
Design doc: `docs/superpowers/tasks/2026-07-16-epmcdme-13259-interactive-chat-input/spec.md` (Implemented).

Key decision of the original implementation: **not** AG-UI/CopilotKit — an extension of our own
NDJSON stream protocol with one optional field + one optional field in the chat request.
Pause/resume is turn-based (no LangGraph interrupt/checkpointer, no resume endpoint).

## 1. Protocol schema

Single source module: `src/codemie/core/interactive.py` (695 lines — the entire protocol).

`ELEMENT_REGISTRY` (interactive.py:218-228) — single source of truth; from it are derived the
discriminated union, type map, feature gating, response-kind coverage, and the validation dispatcher.

Catalog of 9 types (discriminator `type`):

| Model | type | Fields | FEATURES | ANSWERABLE_KINDS |
|---|---|---|---|---|
| TextElement (95-98) | text | content | layout | — |
| ColumnElement (202-206) | column | children | layout | — |
| RowElement (208-211) | row | children | layout | — |
| ButtonElement (101-108) | button | id, label, style(primary/secondary/danger) | action_buttons, short_forms | carries action |
| MultipleChoiceElement (116-129) | multiple_choice | id, options, max_allowed_selections>=1 | choice | choice, submit |
| DropdownElement (132-150) | dropdown | id, label, options, placeholder?, required | choice | submit |
| DatePickerElement (153-169) | date_picker | id, label, min?, max? (ISO), required | short_forms | submit |
| TextFieldElement (178-187) | text_field | id, label, validation? | short_forms | form, submit |
| CheckBoxElement (190-199) | checkbox | id, label, validation? | short_forms | form, submit |

Helpers: `ChoiceOption {value,label}`; `FieldValidation {required, regex?, email}`.
There is no radio-button type: single-select = multiple_choice with max_allowed_selections==1.

Wire models:
- `InteractiveRequest {request_id: uuid, surface: list[AnyElement]}` (244-249)
- `InteractiveResponse {request_id, kind: action|choice|form|submit|text_fallback, payload: dict}` (251-256)

Payload by kind: `submit` (primary) `{action: id|null, answers: {id: {selected|value}}}`;
legacy `action`/`choice`/`form`; `text_fallback {text}` — free text.

Server-side validation (the client is not trusted): limits (interactive.py:45-59) —
MAX_FIELD_VALUE_LEN=4096, MAX_REGEX_PATTERN_LEN=512, MAX_PAYLOAD_BYTES=65536,
REGEX_MATCH_TIMEOUT=0.1s, MAX_SURFACE_DEPTH=12, MAX_SURFACE_ELEMENTS=100, safe EMAIL_RE.
Key functions: `enabled_element_types` (303-319), `default_element_catalog` (259-270),
`validate_surface` (368-380), `build_surface_args_schema` (383-423, dynamic args schema
built only from allowed types), `render_interactive_elements_prompt` (432-444),
`validate_response_values` (659-696) + private validators (452-619).

Config: `InteractiveFeaturesConfig {action_buttons, choice, short_forms}` (62-70).

## 2. Lifecycle

1. **Birth**: built-in tool `request_user_input`
   (`src/codemie/agents/tools/interactive/request_user_input.py`, 67 lines), `return_direct=True`
   — the call ends the agent's turn. Dynamic args_schema from allowed types. execute:
   validate_surface → InteractiveRequest → `thread_generator.send(StreamedGenerationResult(interactive_request=...))` → "".
   Registration: `ToolkitService._append_request_user_input_tool_if_enabled` (toolkit_service.py:598-625);
   conditions: assistant.interactive_features.any_enabled() AND thread_generator AND
   customer_config feature `interactiveElements` AND non-empty enabled_element_types.
   System prompt: `AssistantService._prepare_system_prompt` (assistant_service.py:452-466).
2. **Delivery**: NDJSON stream (not SSE/WebSocket). Chunk:
   `StreamedGenerationResult.interactive_request` (chains/base.py:143-145). Transport:
   ThreadedGenerator→queue→`AssistantRequestHandler.serve_data` (assistant_handlers.py:645-700),
   chunk interception for persistence (:681-683). Regular chat endpoints, no dedicated one.
3. **Response intake**: `AssistantChatRequest.interactive_response` (core/models.py:590-593) +
   text (display chip) + history_index. Validation: `_validate_interactive_response`
   (assistant_handlers.py:117-138; ownership 403 → intake). Core:
   `validate_interactive_intake` (interactive_intake.py:26-72): lookup of request_id in history
   (422 unknown), "not previously answered" (422 already answered) with an exception for re-answer via
   strict history_index; then validate_response_values → 422.
4. **Return to the LLM**: turn-based, no resume. `Conversation.to_chat_history()`
   (conversation.py:598-620) materializes: user message with response →
   `materialize_interactive_message_text` (intake.py:75-81, display + JSON payload);
   assistant message with request → `materialize_interactive_request_text` (84-96, JSON surface).
   The "answered" state is derived from history by request_id. Re-answer = replace_latest_variant.

## 3. Persistence

PostgreSQL (SQLModel). `GeneratedMessage.interactive_request/.interactive_response`
(conversation.py:134-136) in the JSONB list `Conversation.history` — no DDL for the history.
`ChatTurnData` (conversation.py:48-70). Write path: save_chat_history → ConversationService.upsert_chat_history
(conversation_service.py:129-200). Outbound: ConversationResponse.history (:770) — the frontend
restores state from GET.
Migration: `i1n2t3e4r5a6_add_interactive_features_to_assistants.py` — JSONB column
`assistants.interactive_features` (30 lines).

## 4. Configuration

- Per-assistant: `AssistantRequest.interactive_features` (assistant.py:334), `AssistantBase` JSONB (:655-657), default None.
- Platform flag `features:interactiveElements` (customer-config.yaml:198-212, enabled: true)
  + optional catalog override (commented out). The flag gates tool registration and the prompt;
  intake is deliberately NOT gated (in-flight responses).

## 5. Related surfaces

- `src/codemie_tools/` — does NOT use the protocol. Workflows/LangGraph — no. Skills/MCP — no.
- A2A — intake validation only (assistant_handlers.py:983-984), chunk emission is not supported.
- IDE chat — inherits AssistantChatRequest (formally accepts interactive_response), no special handling.
- Non-streaming/background — the tool is not registered (thread_generator is None).
- Outside the repo: codemie-ui (InteractiveElements/*), codemie-sdk — e2e branch
  `origin/EPMCDME-13259_interactive-input-e2e-tests`; the SDK client has no protocol support.

## 6. Scope (backend)

~1000 lines of product code + ~1430 lines of tests. Fully protocol-dedicated files:
core/interactive.py (695), agents/tools/interactive/request_user_input.py (67),
service/conversation/interactive_intake.py (96), migration (30). Targeted edits:
assistant_handlers.py (~35), conversation.py (~25), toolkit_service.py (~26),
assistant_service.py (~15), assistant.py (~6), core/models.py (~6), chains/base.py (~4),
conversation_service.py (~4), customer_config.py (~13), customer-config.yaml (15).

## 7. Tests

tests/codemie/chains/test_interactive.py (827), agents/tools/test_request_user_input.py (141),
service/conversation/test_interactive_intake.py (145),
rest_api/handlers/test_assistant_handlers_interactive.py (210),
rest_api/models/test_assistant_interactive_features.py (34),
agents/test_assistant_agent/test_interactive_turn_end.py (73),
service/test_assistant_service_methods.py (~30, prompt gating).
E2E: codemie-sdk test-harness (branch EPMCDME-13259_interactive-input-e2e-tests).

## Pitfalls

1. `_match_field_regex`: without the `regex` module, server-side regex validation is silently skipped.
2. Merge migration `m1e2r3g4h5d6_...` exists only as a .pyc — risk of the alembic chain diverging.
3. Intake is not gated by the flag — a deliberate deferral.
4. Currently checked-out branch: feature/EPMCDME-13738-per-workflow-integration-scope (the protocol is already merged in).
