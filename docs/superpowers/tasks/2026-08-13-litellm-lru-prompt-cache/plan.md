# Implementation Plan: LiteLLM LRU Prompt Cache Usage Tracking

Ticket: EPMCDME-14104
Branch: EPMCDME-14104_litellm-lru-prompt-cache

## Clarification assumptions

- “LRU prompt cache” means LiteLLM's bounded local in-memory response cache (`type: local`) configured in the proxy, not a new Redis or application-level cache.
- Cache-hit responses should be returned to clients normally and continue to contribute to request/latency metrics, but should not emit token/cost usage metrics through the LangGraph usage summary or proxy usage tracker.
- Existing provider-native prompt caching represented by `cache_read`/`cache_creation` token details remains tracked and is not treated as a whole-response LiteLLM cache hit.

## Tasks

### 1. Add cache metadata detection utility and LangGraph callback filtering

Test-first: yes — add failing tests proving cache-hit `LLMResult` responses do not call `update_llm_run`, while cache misses and provider prompt-cache token details still do.

- Add a small defensive helper in the agent/LiteLLM integration layer to detect a boolean whole-response cache hit across LangChain/LiteLLM response metadata shapes.
- Update `TokensCalculationCallback.on_llm_end` and `on_llm_error` to skip usage persistence for cache hits.
- Preserve existing token/cost behavior for cache misses and provider-native prompt-cache token accounting.

### 2. Enable bounded LiteLLM local response caching

Test-first: yes — add a configuration-focused test or YAML assertion that the proxy configuration enables local caching with explicit bounds and completion call types.

- Update `litellm_config.yaml` under `litellm_settings` with `cache: true` and `cache_params` using LiteLLM's local cache type, explicit maximum entries, TTL, and supported completion/streaming completion call types.
- Add comments describing process-local scope and the fact that cache-hit usage is intentionally excluded from CodeMie token/cost tracking.

### 3. Detect proxy cache hits and suppress proxy usage tracking

Test-first: yes — add failing streaming/non-streaming proxy tests proving cache-hit responses are forwarded but `LLMProxyMonitoringService.track_usage` is not queued, while cache misses still queue usage tracking.

- Extend the enterprise usage parsing result with a cache-hit indicator, or add a local detector that inspects parsed response payload and downstream metadata before response headers are sanitized.
- Ensure both SSE and JSON response shapes are handled, including LiteLLM cache metadata in response hidden/metadata fields where available.
- Gate only token/cost usage tracking; retain proxy request metrics and response behavior.
- Keep internal `x-litellm-*` metadata hidden from clients.

### 4. Run focused validation and reconcile artifacts

Test-first: no — validation task.

- Run the focused callback and proxy-router test modules.
- Run `make ruff`, `make build`, `make license-check`, and `make test` according to the repository quality-gate guide; report any environment-blocked gates explicitly.
- Review the final diff to ensure unrelated dirty files remain untouched.
