# EPMCDME-11647: LLM-Based Contextual Chat Naming (Feature-Flagged)

## Outcome

New conversations get a concise, LLM-generated topical name (from the first user message + assistant response) instead of the verbatim initial message, gated behind a feature flag that defaults off everywhere. Any failure or disabled flag falls back to the existing legacy behavior (truncated first message). No frontend or API schema changes.

## Scope

In scope: the two live-chat call sites in `ConversationService.upsert_chat_history` (new conversation, and pre-created nameless conversation).

Out of scope: `_create_conversation_with_history` (bulk/import upsert-history API) — not "a user starting a new chat," keeps legacy truncation unchanged. No new background-task plumbing added to a non-request-scoped bulk path.

## Architecture

- **`ChatNamingService`** (new, `src/codemie/service/chat_naming_service.py`) — single source of truth for "should I rename, and to what." Owns its own feature-flag check internally.
- **`ConversationService`** (`src/codemie/service/conversation_service.py`) — legacy naming behavior is unchanged at all 3 existing `_truncate_name` call sites. The 2 live-chat sites (currently L155, L167) additionally schedule a background rename.
- **Prompt template** (new, `src/codemie/templates/chat_naming_prompt.py`) — simple `.format()`-style template (mirrors `conversation_history_compaction_prompt`, not `conversation_analysis_prompt`'s raw-replace style — no JSON/brace content here).
- **Trigger mechanism**: thread `background_tasks: BackgroundTasks | None` through the existing call chain (`process_request` → `save_chat_history` → `upsert_chat_history`). At the two spots in `upsert_chat_history` that already assign the legacy name, additionally call `background_tasks.add_task(ChatNamingService.rename_conversation, ...)`. This keeps "is this conversation new/nameless" detection in the one place it already lives correctly, rather than re-deriving it in the handler layer from conversation history length (rejected as fragile/duplicative).

## Resolved design decisions

1. **Model/prompt configurability**: static config only. `CHAT_CONTEXTUAL_NAMING_LLM_MODEL` is a plain `config.py` string + env var, matching the existing `CONVERSATION_ANALYSIS_LLM_MODEL` precedent exactly. Only the enable/disable flag is hot-swappable via `DynamicConfigService`. No new dynamic-config-for-strings pattern introduced.
2. **Sync vs background**: background task. The LLM naming call is offloaded via `background_tasks.add_task` so it adds no latency to the user's first streamed response.
3. **Placeholder naming**: legacy name first, then rename. The conversation is created/persisted synchronously with the legacy truncated name exactly as it is today (identical to the flag-off path); the background task renames it in place once the LLM call succeeds. A brief visible flicker from legacy name to LLM name is acceptable UX.
4. **Bulk/import call site**: out of scope (see Scope section above).

## Components

1. **`ChatNamingService.rename_conversation(conversation_id, first_message, assistant_response, request_id)`** — the background-task entrypoint.
   - Checks `_is_chat_contextual_naming_enabled()` (module-level function: `DynamicConfigService.get_bool_value_safe(CHAT_CONTEXTUAL_NAMING_ENABLED_KEY, default=config.CHAT_CONTEXTUAL_NAMING_ENABLED)`). No-op if disabled.
   - Calls `generate_name(...)`. If it returns a name, updates the conversation row's `conversation_name`. If it returns `None` (any failure), no-op — the legacy name (already persisted synchronously) stands untouched.

2. **`ChatNamingService.generate_name(first_message, assistant_response, request_id) -> str | None`**
   - Formats the naming prompt with `first_message` and `assistant_response`.
   - Calls `get_llm_by_credentials(llm_model=config.CHAT_CONTEXTUAL_NAMING_LLM_MODEL, streaming=False, request_id=request_id).invoke(...)`.
   - Strips whitespace/quotes from the LLM output, truncates to 60 chars (`name[:60]`, plain slice — no ellipsis, ticket only requires a hard length cap).
   - Wrapped in `try/except Exception` → `logger.error(..., exc_info=True)` → returns `None` on any failure (timeout, API error, empty/malformed response).

3. **Config additions**
   - `src/codemie/service/constants.py`: `CHAT_CONTEXTUAL_NAMING_ENABLED_KEY = "CHAT_CONTEXTUAL_NAMING_ENABLED"`, added under the existing `# Dynamic config keys` header (after L47).
   - `src/codemie/configs/config.py`: `CHAT_CONTEXTUAL_NAMING_ENABLED: bool = False` and `CHAT_CONTEXTUAL_NAMING_LLM_MODEL: str = "gemini-3-flash"`, added under a new `# Chat Contextual Naming Configuration` header (after the `CONVERSATION_ANALYSIS_*` block, L721).

## Data flow

1. User sends the first message in a new conversation. `upsert_chat_history` runs synchronously as today: assigns the legacy truncated name, persists the conversation. This behavior is byte-for-byte unchanged regardless of the flag.
2. Because this was a new/nameless conversation (one of the two existing branches), `background_tasks.add_task(ChatNamingService.rename_conversation, ...)` is scheduled. It fires after the response finishes streaming.
3. The background task checks the flag; if on, it calls the LLM and updates `conversation.conversation_name` in place on success.
4. Subsequent messages in the same conversation never re-trigger naming — only the two "new/nameless" branches ever schedule the task — satisfying "name persists, does not change."

## Error handling

Every failure mode (flag off, LLM timeout/error, empty or malformed response) collapses to the same no-op: the legacy name stands, nothing is surfaced to the user or API consumer. Matches the "no frontend/API consumer impact" acceptance criterion.

## Testing

- New `tests/codemie/service/test_chat_naming_service.py` — reuses the module-alias `monkeypatch` mocking convention from `tests/codemie/service/conversation/test_history_compaction_service.py`. Covers: flag-off no-op, LLM-success rename, LLM-exception fallback, 60-char cap enforcement.
- Extend `tests/codemie/service/test_conversation_service.py`: confirm `background_tasks.add_task` is scheduled with correct args at both live-chat branches (L155, L167 equivalents); confirm it is NOT scheduled at the bulk/import call site (`_create_conversation_with_history`); confirm legacy name assignment is unchanged regardless of flag state (non-regression baseline).

## Operational docs

Add a section to `.ai-run/guides/development/configuration-patterns.md` and/or `.ai-run/guides/integration/llm-providers.md` documenting: `CHAT_CONTEXTUAL_NAMING_ENABLED` (DynamicConfigService key + config.py default, toggled via admin REST API `PUT /v1/dynamic-config/{key}` — no code deploy needed), and `CHAT_CONTEXTUAL_NAMING_LLM_MODEL` (static config, needs redeploy to change).

## Non-goals

- No changes to `_create_conversation_with_history` / bulk-import naming.
- No new `DynamicConfigService.get_str_value_safe` convenience wrapper — not needed since model/prompt stay static.
- No frontend or API schema changes.
