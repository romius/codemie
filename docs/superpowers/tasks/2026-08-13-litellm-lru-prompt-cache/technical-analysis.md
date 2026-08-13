# Technical Analysis: LiteLLM LRU Prompt Cache Usage Tracking

## Task Context

EPMCDME-14104: Enable LiteLLM response caching with an in-memory LRU-style cache, and prevent usage tracking from recording cache-hit responses in both LangGraph callbacks/hooks and the CodeMie LiteLLM proxy router.

## Codebase Findings

### LiteLLM proxy configuration

- `litellm_config.yaml` already contains the `litellm_settings` section with request, streaming-cost, and custom callback configuration.
- The proxy is run by both `docker-compose.yml` and `docker-compose.local.yml` using the same mounted `litellm_config.yaml`.
- The repository pins LiteLLM `1.84` in `pyproject.toml`; the installed package exposes LiteLLM's local in-memory cache and marks cached responses through `_hidden_params["cache_hit"]`.
- LiteLLM's cache configuration supports `litellm_settings.cache: true` and `cache_params.type: local`. Its local cache is bounded by `max_size_in_memory` and uses TTL-based eviction, which is the relevant LRU-style deployment configuration for this task.

### LangGraph / LangChain usage tracking

- `src/codemie/agents/callbacks/tokens_callback.py` aggregates `LLMResult` generation usage, calculates cost, and calls `request_summary_manager.update_llm_run` from `on_llm_end` and `on_llm_error`.
- `src/codemie/agents/langgraph_event_adapter.py` forwards LangGraph LLM completion events to the agent callbacks. The adapter currently forwards every `on_llm_end` response without cache-hit filtering.
- `src/codemie/enterprise/litellm/llm_factory.py` converts streaming chunks into LangChain generation chunks and already preserves/extends `generation_info`; this remains the natural boundary for propagating cache metadata if LiteLLM returns it there.
- Existing LangChain usage metadata handles provider prompt-cache token details (`cache_read` and `cache_creation`), but this is distinct from LiteLLM's whole-response cache hit. A whole-response cache hit must be detected from response/generation metadata rather than inferred from zero token counts.

### Proxy-router usage tracking

- `src/codemie/enterprise/litellm/proxy_router.py` buffers successful streamed responses in `_streaming_response_with_usage_tracking`, parses usage, and queues `LLMProxyMonitoringService.track_usage` when tokens are present.
- The router currently strips all `x-litellm-*` headers before returning responses to clients, but it can inspect downstream headers and the buffered response before filtering.
- The proxy path has separate behavior for streaming and non-streaming responses. The usage-tracking path currently enters for all successful responses when `LLM_PROXY_TRACK_USAGE` is enabled, including cache-hit responses.
- `src/codemie/service/monitoring/llm_proxy_monitoring_service.py` is the final metric emission point; the router should skip scheduling `track_usage` rather than teaching the metric service about transport-specific cache metadata.

### Existing tests and conventions

- `tests/codemie/agents/callbacks/test_tokens_callback.py` covers normal usage, provider prompt-cache token details, proxy cost headers, and invalid cost metadata.
- `tests/enterprise/litellm/test_proxy_router.py` covers streaming usage tracking, zero-token suppression, response parsing, and response-header filtering.
- Quality gates are Makefile-driven: `make ruff`, `make build`, `make license-check`, and `make test` (full `make verify` also includes Docker-based secret scanning).

## Proposed Technical Direction

1. Enable LiteLLM's bounded local cache in `litellm_config.yaml`, with explicit cache size/TTL values and supported completion call types. Keep the cache in the LiteLLM proxy process; do not add a new application cache or persist prompts/responses in CodeMie storage.
2. Add a shared, defensive cache-hit detector for LangChain/LiteLLM response shapes. It recognizes boolean/string `cache_hit` in `generation_info`, response metadata, hidden parameters, and equivalent nested metadata without treating provider prompt-cache token fields as a whole-response hit.
3. Make `TokensCalculationCallback` return before creating/updating an `LLMRun` for cache-hit successful responses. Apply the same rule to partial usage handling in `on_llm_error` when the error callback receives a cached response.
4. Make the proxy usage path expose whether the complete response was a LiteLLM cache hit, based on parsed response metadata and an internal callback-added header. Skip `LLMProxyMonitoringService.track_usage` for cache hits while still forwarding the response and retaining ordinary proxy request metrics.
5. Add focused unit tests for cache-hit and cache-miss behavior in LangGraph callback and proxy streaming paths, plus configuration assertions. Preserve existing provider prompt-cache accounting tests.

## Risk Indicators

- Cache-hit metadata can differ between non-streaming and streaming LiteLLM responses, so detection must be tolerant and covered for both shapes.
- The proxy currently removes internal LiteLLM headers from client responses; detection must happen before that filtering and must not expose new internal metadata.
- A whole-response cache hit should not be confused with provider-native prompt caching (`cache_read`), because the latter still represents a real provider call and existing accounting should remain unchanged.
- Local cache state is process-local; multiple proxy replicas will not share entries. This is acceptable for the requested LRU/local cache but should be documented in configuration comments.
- The repository has a heavily dirty working tree with unrelated files. Only task-owned files should be changed or staged.
