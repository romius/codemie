# Technical Research

**Task**: conversation naming chat service LLM
**Generated**: 2026-07-23
**Research path**: filesystem (verified — Explore subagent with direct Read/Grep access confirmed all file paths, line numbers, and code excerpts below against current source. Supersedes an earlier degraded pass that had no tool access.)

---

## 1. Original Context

EPMCDME-11647: LLM-Based Contextual Chat Naming (Feature-Flagged). Outcome: new conversations get concise, LLM-generated topical names (from first user message + assistant response) instead of the verbatim initial message. Gated behind feature flag `CHAT_CONTEXTUAL_NAMING_ENABLED` (default off everywhere). Fallback to legacy behavior (initial user message truncated) on LLM failure or disabled flag. Naming prompt + LLM model configurable via env vars, no code deploy to tune. No frontend or API schema changes. Acceptance criteria: LLM naming active only when flag=true; when enabled, new conversations named by LLM via configurable prompt, hard cap <=60 chars; on any failure or disabled flag, fallback to user's original message; flag/prompt/model settable via env vars/dynamic config, no code deployment; automated tests cover logic branches, model fallback, legacy non-regression; no frontend/API consumer impact; operational docs updated; ships with flag off by default. Affected areas: ConversationService, new ChatNamingService, conversation-naming env vars, testing, operational docs. Related: EPMCDME-12777 (in progress, separate ticket for LLM-generated export filenames).

---

## 2. Codebase Findings

### Existing Implementations

- **`src/codemie/service/conversation_service.py`** — `_truncate_name` (L111-114):
  ```python
  @staticmethod
  def _truncate_name(msg: str | None) -> str:
      msg = msg or ""
      return (msg[:50] + "...") if len(msg) > 50 else msg
  ```
  **3 call sites** (not 4 as earlier assumed):
  | Line | Function | Path |
  |---|---|---|
  | L155 | `upsert_chat_history`, `if not conversation:` branch — new `Conversation(...)`, `conversation_name=cls._truncate_name(request.text)` | Hot path, synchronous, every new conversation |
  | L167 | `upsert_chat_history`, `elif not conversation.conversation_name and not conversation.history:` branch — pre-created nameless conversation | Hot path, synchronous, second branch of same method |
  | L411 | `_create_conversation_with_history` (called from `upsert_conversation_with_history`), uses `request.history[0].message` | Bulk/import API, not the chat/streaming path |

- **`upsert_chat_history`** — classmethod, L129-229 (signature L130-143): takes `assistant_response`, `time_elapsed`, `tokens_usage`, `request: AssistantChatRequest`, `assistant: Assistant`, `user: User`, `thoughts`, `status`, `user_message_received_at`, `interactive_request`, `request_id`. Name assigned at L155/L167; conversation persisted at L228.

- **`src/codemie/rest_api/handlers/assistant_handlers.py`** — `save_chat_history` (instance method, L367-405) calls `ConversationService.upsert_chat_history(...)` synchronously (L392-404). Called synchronously (no `background_tasks.add_task`) from 4+ sites: `_save_history_for_disconnect` (L614, disconnect path), `_serve_data` (L688, normal end-of-stream), `_return_security_error_response` (L753, security-block, no LLM call happened), plus additional handler subclass overrides (L857, L908, L1072, L1103). `process_request` exists in 3 places, all with `background_tasks: BackgroundTasks` param — but `background_tasks.add_task` is used only in `_handle_background` (L771-798) for a fully different purpose (running background-mode assistant execution), **not** for offloading `save_chat_history`. **Naming logic today runs synchronously on the request-serving thread**, after streaming completes. No existing background offload for chat-history persistence — would need to be added if the LLM naming call shouldn't add latency to the response path.

- **`src/codemie/service/conversation_analysis/conversation_analysis_service.py`** (`ConversationAnalysisService._analyze_conversation`, L345-425+) — LLM-call analog. Key points:
  - No feature-flag check inside the method itself — gating happens upstream at the scheduler/batch level via `config.CONVERSATION_ANALYSIS_ENABLED`. The new naming feature needs its own **inline** flag check (unlike this analog).
  - Uses `get_llm_by_credentials(llm_model=llm_model, streaming=False, request_id=conversation_id)`, then `llm.with_structured_output(ConversationAnalysisSchema)` — a structured-output pattern (alternative to plain-text invoke).
  - `CONVERSATION_ANALYSIS_LLM_MODEL` is a **plain static `config.py` string** (L719) — not dynamic-config-backed. Only booleans (e.g. replay-v2) go through `DynamicConfigService` today.

- **`src/codemie/service/conversation/history_compaction_service.py`** (`ConversationHistoryCompactionService`) — second LLM-call analog, and the flag-pattern precedent:
  ```python
  def _is_conversation_replay_v2_enabled() -> bool:
      return DynamicConfigService.get_bool_value_safe(
          AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED_KEY,
          default=config.AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED,
      )
  ```
  (L34-38). Note `_is_enabled()` (L558-568) ANDs this replay-v2 flag with several *other* static compaction-specific config checks — it reuses replay-v2 as a shared master switch, so it's not itself a "one feature, one flag" example. The clean precedent for "one feature, one dedicated dynamic-config flag" is the module-level `_is_conversation_replay_v2_enabled()` function shape — write an analogous function for the new `_KEY` constant. LLM calls (`_summarize_text_async`/`_summarize_text`, L234-259 / L343-369) wrap `get_llm_by_credentials(...)` in `try/except Exception`, return `""` on failure, `logger.error(..., exc_info=True)`.

- **`src/codemie/service/constants.py`** (56 lines total) — dynamic config key section (L46-47):
  ```python
  # Dynamic config keys
  AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED_KEY = "AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED"
  ```
  Only entry under this header today. Convention: `<NAME>_KEY = "<NAME>"`. New key: `CHAT_CONTEXTUAL_NAMING_ENABLED_KEY = "CHAT_CONTEXTUAL_NAMING_ENABLED"`, added right after L47.

- **`src/codemie/configs/config.py`** (`Config(BaseSettings)`, class starts L44) — `CONVERSATION_ANALYSIS_*` block, L711-721:
  ```python
  # Conversation Analysis Configuration
  CONVERSATION_HISTORY_STATS_ENABLED: bool = False
  CONVERSATION_ANALYSIS_ENABLED: bool = False
  CONVERSATION_ANALYSIS_SCHEDULE: str = "0 0 * * *"
  CONVERSATION_ANALYSIS_START_DATE: str = "2025-12-01"
  CONVERSATION_ANALYSIS_LOOKBACK_DAYS: int = 1
  CONVERSATION_ANALYSIS_BATCH_SIZE: int = 20
  CONVERSATION_ANALYSIS_MAX_RETRIES: int = 3
  CONVERSATION_ANALYSIS_LLM_MODEL: str = "gemini-3-flash"
  WORKFLOW_GENERATOR_LLM_MODEL: str = ""
  CONVERSATION_ANALYSIS_PROJECTS_FILTER: list[str] = ["demo", "codemie", "epm-cdme"]
  ```
  Plain class attributes, type annotation + default + trailing `#` comment, grouped under a `# <Feature> Configuration` header. No `Field()` usage. Mirror this style for `CHAT_CONTEXTUAL_NAMING_ENABLED: bool = False` and `CHAT_CONTEXTUAL_NAMING_LLM_MODEL: str = "..."`.

- **`src/codemie/templates/conversation_analysis_prompt.py`** (290 lines) — `prompt = """..."""` triple-quoted string (`{{`/`}}` escaping for literal JSON braces, single `{conversation}` placeholder), wrapped via `PromptTemplate.from_template(prompt)`. In the service it's applied via `.template.replace("{conversation}", conversation_text)` (raw string replace, not `.format()`) — done specifically to dodge brace collisions from JSON content. For a **simple text-in/text-out naming prompt with no JSON content**, the simpler `conversation_history_compaction_prompt.format(history_text=batch_text)` pattern (used in `history_compaction_service.py`) is the better analog: plain `PromptTemplate.from_template(prompt)` with e.g. a `{first_message}`/`{assistant_response}` placeholder, called via `.format(...)`.

- **`get_llm_by_credentials`** — `src/codemie/core/dependecies.py` L221-227:
  ```python
  def get_llm_by_credentials(
      llm_model: str = llm_service.default_llm_model,
      temperature: Optional[float] = None,
      top_p: Optional[float] = None,
      streaming: bool = True,
      request_id: Optional[str] = None,
  ):
  ```
  Confirmed unchanged from prior notes.

- **`src/codemie/service/dynamic_config_service.py`** (555 lines, full read):
  - `get_bool_value_safe(cls, key: str, default: bool = False) -> bool` (L542-554) — thin wrapper over `get_typed_value_safe(key, bool, default=default)`.
  - Generic: `get_typed_value[T: (str, int, float, bool)](cls, key, expected_type, default=None)` (L482-516, raises on type mismatch/missing without default) and `get_typed_value_safe[T](...)` (L518-540, catches `Exception`, logs warning, returns default).
  - **No dedicated `get_str_value_safe` exists yet** — but `get_typed_value_safe(key, str, default=...)` already works today since the generic method is type-parametrized. Adding a named `get_str_value_safe` wrapper (mirroring `get_bool_value_safe`) is trivial if wanted for the prompt/model override, but not required — can call the generic method directly.
  - No in-process caching — every call reads fresh from DB (sync `Session` / async `get_async_session`).

- **New component required**: `ChatNamingService` (does not yet exist) — responsible for building the naming prompt from first user message + assistant response, calling the configured LLM, enforcing the ≤60 char cap, and signaling failure so the caller falls back.

### Architecture and Layers Affected

- **Service layer**: `ConversationService` (3 call sites to rewire), new `ChatNamingService`, `DynamicConfigService` (flag lookup, read-only — no changes needed to this file).
- **Config/constants layer**: `service/constants.py` (new flag-key constant), `configs/config.py` (new static defaults for flag + model).
- **Templates layer**: new prompt template module (mirrors `templates/conversation_analysis_prompt.py` location convention, but simpler `.format()`-based content, per `conversation_history_compaction_prompt`).
- **No API or router layer changes** — confirmed no new endpoints needed; ticket AC explicitly states no frontend/API schema changes.

### Integration Points

- **`DynamicConfigService.get_bool_value_safe(key, default=config.X)`** — established pattern for the enable/disable flag, confirmed working exactly as expected; module-level `_is_conversation_replay_v2_enabled()`-style function is the concrete shape to copy.
- **`get_llm_by_credentials` (core/dependecies.py)** — shared LLM-acquisition path, signature confirmed unchanged.
- **EPMCDME-12777 (export filenames, in-progress, separate ticket)** — grep for `EPMCDME-12777` and `export.*naming`/`naming.*export` under `src/` returned **no hits**. No existing export-filename-naming code to reuse. No coordination/sequencing blocker — can proceed independently, though still worth a heads-up ping to the ticket owner per the original plan (shared prompt-writing *pattern*, not shared code, may still be worth aligning on).

### Patterns and Conventions

- Feature flags: `DynamicConfigService.get_bool_value_safe(KEY, default=...)`, key declared as constant in `service/constants.py` under `# Dynamic config keys`, consumed via a small module-level `_is_X_enabled()` function.
- LLM-backed service shape: method wrapped in `try/except Exception`, explicit fallback (return empty string / signal failure) on any exception, `logger.error(..., exc_info=True)` on failure.
- Prompt templates live in `templates/` as dedicated Python modules; use `.format()` for simple text-only prompts (no embedded JSON/braces).
- Test mocking convention (see `tests/codemie/service/conversation/test_history_compaction_service.py`): import the service module under an alias (`import history_compaction_service as history_compaction_module`), then `monkeypatch.setattr(history_compaction_module.DynamicConfigService, "get_typed_value", lambda *a, **kw: False)` and `monkeypatch.setattr(history_compaction_module.llm_service, "get_model_details", lambda llm_model: SimpleNamespace(...))` — patches the module namespace, not the class directly. Replicate this for a new `conversation_naming_service` test file.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/architecture/service-layer-patterns.md` — for `ChatNamingService` structure relative to `ConversationService`.
- `.ai-run/guides/development/configuration-patterns.md` — env var / dynamic-config conventions.
- `.ai-run/guides/integration/llm-providers.md` — LLM model selection via env var/config.
- `.ai-run/guides/development/error-handling.md` — fallback-on-failure conventions.
- `.ai-run/guides/testing/testing-service-patterns.md` — branch coverage conventions for the automated-test AC.
- Not read in this pass — planner should load directly before writing the plan.

### Architectural Decisions

- Flag-pattern decision already made and now **verified against source**: use `DynamicConfigService.get_bool_value_safe` over a bare config boolean, satisfying the no-redeploy tuning requirement for the enable/disable switch. Settled.
- **Newly identified open point**: the LLM model name and prompt text are NOT currently dynamic-config-backed anywhere in the codebase (`CONVERSATION_ANALYSIS_LLM_MODEL` is a plain static config string). If the ticket's "no code deployment" AC is meant to also cover the *model*/*prompt* (not just the enable flag), that would require either (a) also reading those through `DynamicConfigService.get_typed_value_safe(key, str, default=...)` (works today, no new method needed), or (b) accepting that model/prompt changes need a restart (env var reload) while only the flag is truly hot-swappable. This should be decided explicitly in the spec, not assumed.

### Derived Conventions

- New flag: `CHAT_CONTEXTUAL_NAMING_ENABLED_KEY = "CHAT_CONTEXTUAL_NAMING_ENABLED"` in `service/constants.py`; static default `CHAT_CONTEXTUAL_NAMING_ENABLED: bool = False` in `config.py`.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/test_conversation_service.py` (135 lines, full read) — imports `pytest`, `unittest.mock.{MagicMock, patch}`, plus model imports. Fixtures: `mock_admin_user`, `mock_user`, `mock_assistant`, `mock_conversation`, `mock_conversation_metrics`, `mock_request` (`AssistantChatRequest` with `text="Hello"`), `mock_update_request`. **No existing test exercises `_truncate_name` or `upsert_chat_history`'s naming branch** — the only `conversation_name` assertion (`test_conversation_service_update`, L116-135) covers explicit rename via `update_conversation`, unrelated to auto-naming. `@patch` targets module-qualified paths (e.g. `@patch("codemie.service.conversation_service.ConversationFolder.touch_folder")`). No scaffolding to extend directly — net-new tests needed, but fixture conventions are reusable.
- `tests/codemie/service/conversation/test_history_compaction_service.py` — confirmed exists, and demonstrates the module-alias monkeypatch mocking convention (see Patterns section above) to reuse for the new naming service's tests.

### Testing Framework and Patterns

- pytest, `monkeypatch`/`unittest.mock` mixed usage depending on file. LLM calls and `DynamicConfigService` reads are mocked via `monkeypatch.setattr` on the module namespace alias, not the class import directly.

### Coverage Gaps

- No tests yet for: flag-off → legacy name, flag-on + LLM success, flag-on + LLM failure/fallback, 60-char cap enforcement, env/dynamic-config-driven prompt/model override.

---

## 5. Configuration and Environment

### Environment Variables

- New: `CHAT_CONTEXTUAL_NAMING_ENABLED` (bool, default `False`) — read via `DynamicConfigService.get_bool_value_safe`, constant key in `service/constants.py`.
- New: `CHAT_CONTEXTUAL_NAMING_LLM_MODEL` (str) — static `config.py` default (mirrors `CONVERSATION_ANALYSIS_LLM_MODEL`); optionally also dynamic-config-readable via `get_typed_value_safe(key, str, default=...)` if the spec decides model/prompt need to be hot-swappable too (see open point above).
- New (per AC): prompt-override mechanism — exact name/shape TBD in spec; simplest option is a `CHAT_CONTEXTUAL_NAMING_PROMPT` env var/dynamic-config string, or keep the prompt as a versioned template file and only the model as configurable (needs spec decision).

### Configuration Files

- `src/codemie/service/constants.py` — flag-key constant (after L47).
- `src/codemie/configs/config.py` — static defaults (after L721, following `CONVERSATION_ANALYSIS_*` block).

### Feature Flags and Deployment Concerns

- Flag defaults to off everywhere per ticket — deployment-safe by construction.
- The enable/disable flag is confirmed hot-swappable via `DynamicConfigService` (Postgres-backed, admin REST API `PUT /v1/dynamic-config/{key}`, no code deploy). Model/prompt hot-swappability is not yet decided — see open point above.

---

## 6. Risk Indicators

- **Resolved** (was a risk in the degraded pass): `_truncate_name` call-site count confirmed as **3**, not 4 — locations verified (L155, L167, L411).
- **Resolved**: EPMCDME-12777 overlap checked directly — no existing export-naming code found, no reuse/sequencing blocker.
- **Open, newly identified**: whether the ticket's "no code deployment to tune" AC extends to the model/prompt (not just the flag) needs an explicit spec decision — `DynamicConfigService` can technically support string values via its generic `get_typed_value_safe`, but no precedent uses it for strings yet, and doing so for the prompt template (potentially long text) may be awkward via a key-value config row vs. a template file.
- `ChatNamingService` is entirely greenfield — no existing file, no existing tests. Full component from scratch: prompt module, service class, flag wiring, fallback wiring, 3 call-site changes in `ConversationService`.
- 60-char hard cap needs explicit truncation/validation logic in code — prompt instructions alone won't reliably constrain LLM output length.
- Naming currently happens synchronously on the request-serving thread (in `_serve_data`, after streaming completes) — adding an LLM call here adds latency to every new-conversation response unless explicitly offloaded (no existing `background_tasks.add_task` precedent for chat-history persistence; would be new plumbing if async offload is wanted). Spec should decide sync-inline vs. background-task explicitly.
- Operational docs update is an explicit AC — target `.ai-run/guides/development/configuration-patterns.md` and/or `.ai-run/guides/integration/llm-providers.md`.

---

## 7. Summary for Complexity Assessment

This task touches the service layer only: a new `ChatNamingService` (LLM call, prompt template, ≤60-char enforcement, failure handling) plus 3 confirmed call-site edits in `ConversationService` (`upsert_chat_history` x2, `_create_conversation_with_history` x1), a new flag constant in `service/constants.py`, and new static config defaults in `config.py`. No API, router, or frontend surface is touched. Expected file-change surface: ~6-7 files (new service, new prompt template, constants.py edit, config.py edit, conversation_service.py edit, new/extended test file, operational docs edit).

Technical novelty is low-to-moderate and now more precisely bounded: the LLM-call-with-fallback shape is doubly precedented (`ConversationAnalysisService`, `ConversationHistoryCompactionService`), and the boolean flag-read pattern is precedented and confirmed working as expected. The one real open design question — now narrowed by verification — is whether model/prompt need to be dynamic-config-hot-swappable (no precedent, but mechanically simple via the existing generic `get_typed_value_safe`) or just env-var/static-config (simpler, matches `CONVERSATION_ANALYSIS_LLM_MODEL`'s existing treatment, needs redeploy to change). This is a spec decision, not a research gap.

A second design decision surfaced by this pass: whether the LLM naming call should run inline (synchronous, adds latency to the first response) or be offloaded to a background task (no existing precedent in this codebase for chat-history persistence, would be new plumbing). Both are viable; the spec should pick one explicitly.

Test coverage posture: `ConversationService`'s legacy path has an existing test file to protect but zero current coverage of the naming branches specifically — a moderate, not large, testing lift given the reusable module-alias monkeypatch mocking convention already established in `test_history_compaction_service.py`. No blocking coordination risk with EPMCDME-12777 — checked directly, no overlap found.
