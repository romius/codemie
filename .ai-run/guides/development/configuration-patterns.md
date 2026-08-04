# Configuration Patterns

## Central Config

Use `src/codemie/configs/` and existing YAML config trees for runtime settings.

| Avoid | Prefer |
|---|---|
| Reading environment variables directly in feature code | Add or reuse central config values |
| Hardcoding model provider choices | Use `MODELS_ENV` and provider config files |

Evidence: README describes `MODELS_ENV` and `config/llms/` provider files at `README.md:61`.

## Feature Flags

Gate optional behavior through config at assembly points or service boundaries.

| Avoid | Prefer |
|---|---|
| Scattered feature flag checks in unrelated modules | Centralize checks near app/router/service assembly |
| Assuming enterprise features always exist | Use enterprise loader/provider abstractions |

Evidence: user-management routers are gated by config in `src/codemie/rest_api/main.py:706`.

## Chat Contextual Naming

| Setting | Type | Default | Toggle mechanism |
|---|---|---|---|
| `CHAT_CONTEXTUAL_NAMING_ENABLED` | bool | `False` | `DynamicConfigService.get_bool_value_safe`, runtime-togglable via admin REST API `PUT /v1/dynamic-config/CHAT_CONTEXTUAL_NAMING_ENABLED` — no code deploy |
| `CHAT_CONTEXTUAL_NAMING_LLM_MODEL` | str | `"gpt-5-nano-2025-08-07"` | static `config.py` / env var only — requires restart/redeploy to change |

On any failure (LLM error, timeout, empty output) or when the flag is off, the conversation keeps its legacy truncated-first-message name — no user-facing error, no API/frontend surface change.

Known limitation: conversations created via `background_task=True` requests never get the LLM name — the naming task is scheduled from inside `_background_generate`, which itself runs in a separate thread-pool executor after the original request's `BackgroundTasks` object has already finished running, so the scheduled task is never invoked. They still fall back to the legacy name safely.

Evidence: `src/codemie/service/chat_naming_service.py`, `src/codemie/service/conversation_service.py:236-243`.
