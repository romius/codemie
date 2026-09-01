# Technical Research

**Task**: mcp toolkit client error diagnostics
**Generated**: 2026-08-04T10:31:00Z
**Research path**: codegraph

---

## 1. Original Context

Fix MCP server initialization so that npx-based MCP servers successfully load tools into assistants: add error classification (MCPErrorCategory enum, MCPBridgeError exception) and category-specific diagnostic hints in the test-connection flow. Improve error messages in client.py, models.py, mcp_tester.py; add regression tests.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/service/mcp/client.py` — `MCPConnectClient`: issues `tools/list` and `tools/call` via httpx; `_error_message_from_http_status_error()` extracts bridge error strings; raises `ValueError` or `BrokerAuthRequiredException`
- `src/codemie/service/mcp/toolkit.py` — `MCPToolkitFactory.create_toolkit()`: async entry point calling `list_tools()`, wrapping result; logs and re-raises all exceptions verbatim; `MCPToolExecutionError` for timeout/invocation failures
- `src/codemie/service/mcp/toolkit_service.py` — `MCPToolkitService._process_single_mcp_server()`: wraps failures as `MCPToolLoadException`; `_sanitize_exception_for_log()` strips `ValueError` messages to just the type name (active diagnostic gap); `MCPServerTester.test()`: surfaces errors with generic suffix only
- `src/codemie/service/mcp/models.py` — `MCPToolLoadException`, `MCPServerConfig` (includes `init_timeout_seconds` from EPMCDME-10045), response types for tools/list and tools/call
- `src/codemie/configs/config.py` — all MCP-related environment variable declarations

### Architecture and Layers Affected

- **HTTP/bridge layer** (`client.py`): error originates here — `httpx.HTTPStatusError` or connection errors are caught and converted to `ValueError`
- **Factory/toolkit layer** (`toolkit.py`): `MCPToolkitFactory.create_toolkit()` is the first caller of `client.py`; re-raises exceptions verbatim after logging
- **Service/orchestration layer** (`toolkit_service.py`): wraps all exceptions as `MCPToolLoadException`; `MCPServerTester.test()` is the test-connection entry point for user-facing diagnostics
- **Data/model layer** (`models.py`): `MCPToolLoadException` and `MCPServerConfig` — the new `MCPErrorCategory` enum and `MCPBridgeError` exception class belong here
- **Config layer** (`config.py`): no changes expected for this task

### Integration Points

- Internal chain: `MCPServerTester.test()` → `MCPToolkitFactory.create_toolkit()` → `MCPConnectClient.list_tools()` → httpx bridge at `MCP_CONNECT_URL`
- External bridge: all MCP traffic routes through an HTTP bridge; this bridge may raise HTTP 4xx/5xx or return MCP-level errors in response body (e.g. `"McpError: Connection closed"` for npx-based servers with slow init)
- `langchain.BaseTool` base class used by `MCPTool` — no changes expected here
- `cachetools.TTLCache` used for both factory and service instances — cache invalidation is not in scope

### Patterns and Conventions

- Bridge error extraction: `_error_message_from_http_status_error()` in `client.py` parses response body for bridge error strings before falling back to HTTP status text
- Error wrapping chain: `httpx.HTTPStatusError` → `ValueError` (client layer) → `MCPToolLoadException` (service layer)
- Sanitized logging: `_sanitize_exception_for_log()` in `toolkit_service.py` strips `ValueError` message body to type name only — protects credentials/URLs but accidentally suppresses actionable error text; the new typed `MCPBridgeError` (not a `ValueError`) would bypass this sanitization correctly
- Auth-challenge pass-through: 401/403 with `WWW-Authenticate` header re-raised verbatim; plain 401 raises `BrokerAuthRequiredException`
- `MCP_SERVER_INIT_TIMEOUT` guard: already on this branch from EPMCDME-10045 — the task builds on top of it

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/mcp-integration.md` — primary guide for MCP configuration, bridge usage, and tool patterns; must be read before making changes to the MCP stack

### Architectural Decisions

- EPMCDME-10045 (current branch): established `MCP_SERVER_INIT_TIMEOUT` to guard slow npx server init; this task extends that work with richer error classification rather than just timeout guarding
- Bridge-mediated architecture is a deliberate design decision: all MCP communication goes through an HTTP bridge, not direct stdio/SSE — this shapes where error classification must happen (at the client/bridge boundary)

### Derived Conventions

- New exception classes should be placed in `models.py` alongside `MCPToolLoadException` and `MCPToolExecutionError`
- Enum types for error categories follow Python `enum.Enum` patterns present in the models layer
- Category-specific hint strings should be injected at the `MCPServerTester.test()` level, not deep in the client layer
- Test files follow a naming convention: `test_<module>_<class|feature>.py` under `tests/codemie/service/mcp/`

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/mcp/test_client.py` — `MCPConnectClient` unit tests; covers main request paths
- `tests/codemie/service/mcp/test_toolkit_mcp_toolkit_factory.py` — `MCPToolkitFactory` caching, key generation, credential isolation (unittest-style)
- `tests/codemie/service/mcp/test_toolkit_mcp_toolkit_init.py` — `MCPToolkit` init, tool creation, error handling
- `tests/codemie/service/mcp/test_toolkit_service_init.py` — service singleton initialization
- `tests/codemie/service/mcp/test_toolkit_service_integration.py` — integration-style service tests
- Additional test files covering three-state auth, tool filtering, auth resolver, execution context, access control

### Testing Framework and Patterns

- Primary framework: pytest
- Some factory tests use `unittest.TestCase` style — mixed framework usage in same package
- Async tests use `pytest-asyncio` or equivalent async fixtures
- Mocking pattern: `unittest.mock.patch` / `AsyncMock` for httpx client and bridge responses

### Coverage Gaps

- `MCPServerTester.test()` error-category paths: **no existing tests** for category-specific diagnostic hint emission
- `_error_message_from_http_status_error()` edge cases: not fully covered (e.g. bridge returning `McpError: Connection closed` in body)
- `_sanitize_exception_for_log()` behavior with the new `MCPBridgeError` type: needs a regression test to confirm it does not strip the message
- New `MCPErrorCategory` enum and `MCPBridgeError` exception class: entirely new — need unit tests for construction and category assignment

---

## 5. Configuration and Environment

### Environment Variables

- `MCP_CONNECT_URL` (default `http://localhost:3000`) — bridge base URL
- `MCP_CLIENT_TIMEOUT` (default `300.0`) — httpx request timeout
- `MCP_SERVER_INIT_TIMEOUT` — guards slow server initialization (added EPMCDME-10045)
- `MCP_TOOLKIT_FACTORY_CACHE_SIZE` / `MCP_TOOLKIT_FACTORY_CACHE_TTL` — factory TTLCache sizing
- `MCP_TOOLKIT_SERVICE_CACHE_SIZE` / `MCP_TOOLKIT_SERVICE_CACHE_TTL` — service TTLCache sizing
- `MCP_CONNECT_BUCKETS_COUNT` — bucket routing modulus for bridge

### Configuration Files

- `src/codemie/configs/config.py` — all MCP env vars declared and defaulted here; no changes expected for this task

### Feature Flags and Deployment Concerns

- No feature flags found for the MCP error classification path
- `MCP_SERVER_INIT_TIMEOUT` is the closest existing runtime switch; error classification logic is not gated — it will be always-on

---

## 6. Risk Indicators

- `_sanitize_exception_for_log()` in `toolkit_service.py` drops `ValueError` message body (logs only `"ValueError"`), so the actionable bridge error string (e.g. `"McpError: Connection closed"`) never reaches server logs — the new `MCPBridgeError` typed exception must not be a `ValueError` subclass to avoid this sanitization bypass
- `MCPServerTester.test()` appends a generic `"Please, check the configuration."` suffix regardless of error type; no branching on error category exists — the proposed category-specific hints are entirely new logic with no existing scaffold
- `MCPToolLoadException` has no `error_category` field; callers cannot branch on error type without brittle string parsing — adding the field is a model change that must be backward-compatible (existing callers that construct or catch this exception will need review)
- Mixed test frameworks (pytest + unittest) in `tests/codemie/service/mcp/` — new regression tests should use pytest consistently per `.ai-run/guides/testing/testing-patterns.md`
- No existing tests for `MCPServerTester.test()` error paths — any regression test suite for this task is being written from scratch; the surface is larger than it appears
- The task spans four files (`client.py`, `models.py`, `mcp_tester.py`/`toolkit_service.py`, plus test files) across three architectural layers — moderate coordination surface
- `codegraph` returned results for all dimensions — no indexing gaps detected

---

## 7. Summary for Complexity Assessment

The task touches three architectural layers: the HTTP/bridge client layer (`client.py`), the data/model layer (`models.py`), and the service/orchestration layer (`toolkit_service.py`, which hosts `MCPServerTester`). The estimated file change surface is 4–6 files: `models.py` (new `MCPErrorCategory` enum and `MCPBridgeError` exception), `client.py` (raise `MCPBridgeError` instead of `ValueError` with category assignment), `toolkit_service.py` (category-specific hint branching in `MCPServerTester.test()` and updated `_sanitize_exception_for_log()` behavior), plus 2–3 new or extended test files. The changes are incremental additions to an established error-wrapping chain rather than architectural rewrites.

Technical novelty is low-to-medium. The error classification pattern (`MCPErrorCategory` enum + typed exception) is new to this domain but is a standard Python idiom; no unfamiliar frameworks or external integrations are introduced. The trickiest design decision is ensuring `MCPBridgeError` is not a `ValueError` subclass, because `_sanitize_exception_for_log()` already strips `ValueError` message content — getting the inheritance wrong would silently reintroduce the diagnostic gap the task is meant to fix. This is a subtle but well-defined constraint.

Test coverage posture is mixed: `client.py` and `toolkit.py` have meaningful existing tests, but `MCPServerTester.test()` error paths have no coverage at all. All regression tests for the new error-category behavior must be written from scratch. The task explicitly requires these tests, so the testing surface is a first-class deliverable. The primary risk is scope creep into the `MCPToolLoadException` model change propagating to other callers across the service layer — that change should be made backward-compatible (optional `error_category` field) to avoid cascading updates.
