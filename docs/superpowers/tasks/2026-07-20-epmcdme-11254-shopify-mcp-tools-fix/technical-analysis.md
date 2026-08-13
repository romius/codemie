# Technical Research

**Task**: mcp mcp_tester mcp_tools_info_service toolkit_service single_usage npx stdio
**Generated**: 2026-07-20T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-11254 — Shopify Dev MCP: Tools Not Retrieved After Successful Connection Test. The bug: after the connection test passes (green), MCP tools from npx/stdio-based servers like @shopify/dev-mcp@latest are not reliably retrieved in subsequent calls. Root causes identified: (1) MCPServerTester.test() returns (True, 'Success') unconditionally even when zero tools are returned — no empty-tools guard; (2) MCPToolsInfoService.get_mcp_toolkit_info() calls MCPToolkitService.get_mcp_server_tools() without mcp_server_single_usage=True, defaulting to False (cached path), which silently fails for npx/stdio servers when the bridge connection cache is stale. The fix is: add empty-tools guard in mcp_tester.py, and add mcp_server_single_usage=True to mcp_tools_info_service.py. No changes needed to toolkit_service.py (chat path) — the connection test and wizard tool listing are the affected surfaces.

---

## 2. Codebase Findings

### Existing Implementations

**Primary fix targets:**

- `src/codemie/service/mcp/mcp_tester.py`
  - Class `MCPServerTester`, method `test()` (line 32)
  - Constructor takes `MCPServerCheckRequest` and `User` (line 28)
  - Line 37: calls `MCPToolkitService.get_mcp_server_tools(mcp_servers=[self.mcp_server], user_id=self.user.id, mcp_server_single_usage=True)` — `mcp_server_single_usage=True` is already correct here
  - Lines 40–41: logs tool count, then unconditionally returns `(True, 'Success')` — the empty-tools guard is missing
  - Called by: `src/codemie/rest_api/routers/assistant.py` line 1812, inside the `POST /assistants/mcp/test` (`check_mcp_server`) endpoint

- `src/codemie/service/tools/mcp_tools_info_service.py`
  - Class `MCPToolsInfoService`, method `get_mcp_toolkit_info()` (line 36)
  - Lines 58–62: calls `MCPToolkitService.get_mcp_server_tools(mcp_servers=[mcp_server_config], user_id=user.id, project_name=project_name)` — `mcp_server_single_usage` is NOT passed, defaults to `False`
  - Lines 64–72: already has an empty-tools guard that raises `MCPToolsInfoServiceError` when `not tools`
  - Called by: `src/codemie/rest_api/routers/assistant.py` line 615, inside the `POST /v1/assistants/mcp_tools` (`get_mcp_tools`) endpoint (deferred import)

**Core engine (no changes required):**

- `src/codemie/service/mcp/toolkit_service.py` — `MCPToolkitService`
  - `get_mcp_server_tools()` signature (line 184): `mcp_server_single_usage: bool | None = False` (line 192)
  - `_prepare_server_config()` (line 889): sets `server_config.single_usage = mcp_server.config and mcp_server.config.single_usage or mcp_server_single_usage` (line 939)
  - `get_toolkit()` (line 1193): `should_use_cache = use_cache and not server_config.single_usage` (line 1231) — when `single_usage=True`, TTL cache is bypassed
  - `_instances_cache` (lines 170–172): `TTLCache(maxsize=MCP_TOOLKIT_SERVICE_CACHE_SIZE, ttl=MCP_TOOLKIT_SERVICE_CACHE_TTL)`, keyed by `MCPConnectClient.base_url`

- `src/codemie/service/mcp/toolkit.py` — `MCPToolkitFactory`
  - `_toolkit_cache` (lines 686–703): `TTLCache` keyed by SHA-256 of `{command, args, env, headers, auth_token, auth_config, bucket_key}`; bypassed when `server_config.single_usage=True`
  - Cache key comment (lines 811–818) encodes the security decision: all credential fields must be included to prevent cross-user leakage

- `src/codemie/service/mcp/client.py` — `MCPConnectClient`
  - Sends `single_usage=server_config.single_usage` in the JSON body to the bridge
  - Bridge endpoint: `{MCP_CONNECT_URL}/bridge`; bucket routing via `X-MCP-Connect-Bucket` header

- `src/codemie/service/tools/toolkit_service.py` — `ToolkitService` (chat path)
  - `_determine_mcp_server_lifecycle()` (line 813): derives `effective_mcp_server_single_usage` from request and conversation DB record
  - `_get_tools()` (line 835): passes `effective_mcp_server_single_usage` — correct, no change needed

**Related callers with `mcp_server_single_usage` gaps (outside bug scope but noted):**

- `src/codemie/workflows/config_resources_validation.py` line 181: calls `get_mcp_server_tools()` without `mcp_server_single_usage`
- `src/codemie/workflows/validation/resources.py` line 342: same omission
- These are validation/workflow paths outside the stated bug scope

### Architecture and Layers Affected

**REST Router layer:**
- `src/codemie/rest_api/routers/assistant.py` — both affected endpoints live here:
  - `check_mcp_server()` (line ~1812): calls `MCPServerTester`
  - `get_mcp_tools()` (line ~615): calls `MCPToolsInfoService`

**Service layer:**
- `src/codemie/service/mcp/mcp_tester.py` — Fix 1 target (empty-tools guard)
- `src/codemie/service/tools/mcp_tools_info_service.py` — Fix 2 target (`mcp_server_single_usage=True`)

**Infrastructure/Engine layer (read-only for this fix):**
- `src/codemie/service/mcp/toolkit_service.py` — `MCPToolkitService`
- `src/codemie/service/mcp/toolkit.py` — `MCPToolkitFactory` (two-layer cache)
- `src/codemie/service/mcp/client.py` — `MCPConnectClient` (bridge HTTP client)

**Data model layer (read-only for this fix):**
- `src/codemie/rest_api/models/assistant.py` — `MCPServerDetails` (line 152), `MCPServerCheckRequest` (line 423)
- `src/codemie/service/mcp/models.py` — `MCPServerConfig` (line 142) with `single_usage` field
- `src/codemie/rest_api/models/mcp_config.py` — `MCPServerConfigData` with `single_usage: bool = Field(default=False, ...)`

### Integration Points

**Internal call chain for the two affected surfaces:**

```
POST /assistants/mcp/test
  assistant.py:check_mcp_server()
    → MCPServerTester(request, user).test()           [mcp_tester.py:32]
        → MCPToolkitService.get_mcp_server_tools(
              mcp_server_single_usage=True             ← already correct
          )
        → returns (True, 'Success') unconditionally   ← empty-tools guard missing

POST /v1/assistants/mcp_tools
  assistant.py:get_mcp_tools()
    → MCPToolsInfoService.get_mcp_toolkit_info()      [mcp_tools_info_service.py:36]
        → MCPToolkitService.get_mcp_server_tools(
              # mcp_server_single_usage NOT passed     ← defaults to False, the bug
          )
        → raises MCPToolsInfoServiceError if not tools ← guard exists but never fires
```

**Two-layer cache bypassed by `single_usage=True`:**

- Layer 1 (`MCPToolkitService._instances_cache`): TTLCache by bridge URL; TTL = `MCP_TOOLKIT_SERVICE_CACHE_TTL` (default 3600s)
- Layer 2 (`MCPToolkitFactory._toolkit_cache`): TTLCache by SHA-256 of server config; TTL = `MCP_TOOLKIT_FACTORY_CACHE_TTL` (default 600s). Only Layer 2 is bypassed by `single_usage=True` via the `should_use_cache = use_cache and not server_config.single_usage` check at toolkit_service.py:1231

**Bridge side:** MCP-Connect receives `single_usage` in the request body. When `False`, MCP-Connect may reuse a persistent stdio child-process connection and return its cached tool list without re-spawning `npx`. When `True`, MCP-Connect spawns a fresh process.

### Patterns and Conventions

- **Service layer delegation**: Routers never call `MCPToolkitService` directly; they delegate to thin feature-scoped services (`MCPServerTester`, `MCPToolsInfoService`). This is encoded in `.ai-run/guides/architecture/layered-architecture.md`.
- **Auth exception propagation convention**: `BrokerAuthRequiredException` and `MCPAuthenticationRequiredException` are always re-raised, never swallowed, and bubble up to the router layer — confirmed in existing tests.
- **Empty-tools guard pattern**: `MCPToolsInfoService.get_mcp_toolkit_info()` already uses `if not tools: raise MCPToolsInfoServiceError(...)`. The same pattern is the fix for `MCPServerTester.test()`, returning `(False, "No tools returned...")` instead of raising.
- **`single_usage` resolution priority**: `server_config.single_usage = mcp_server.config and mcp_server.config.single_usage or mcp_server_single_usage` — call-level `mcp_server_single_usage=True` overrides the per-server catalog value.
- **Cache key security invariant**: All credential-bearing fields (`env`, `headers`, `auth_token`, `auth_config`, `bucket_key`) must be in the cache key to prevent cross-user leakage (encoded in toolkit.py lines 811–818).

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/mcp-integration.md` — Two-sentence guide. Single rule: "Keep MCP configuration and authentication behavior behind existing service and router modules." Does not document `single_usage`, caching semantics, or tool-retrieval behavior. Not actionable for this fix.
- `.ai-run/guides/architecture/layered-architecture.md` — Documents the router → service → repository layering rule. Confirms that the two affected endpoints correctly delegate to their respective service classes.
- `.ai-run/guides/architecture/service-layer-patterns.md` — Confirms feature-scoped service class convention.
- `docs/superpowers/specs/2026-06-23-mcp-oauth2-discovered-reauth-fix-design.md` — MCP-adjacent design doc for a different bug (OAuth2 re-auth loop in enterprise auth resolver). Not relevant to EPMCDME-11254.

### Architectural Decisions

- **No ADR or design doc exists** for the `single_usage` parameter, caching strategy, or the "test connection returns success on zero tools" behavior. All relevant decisions are encoded implicitly in code and inline comments.
- Inline comment at toolkit_service.py:1230–1231 encodes the single_usage/cache-bypass decision.
- Inline comment at toolkit.py:811–818 encodes the cache key security requirement.
- Inline comment at models.py:109 records a separate protocol constraint (`user_context` excluded from bridge request fields, per EPMCDME-13546).

### Derived Conventions

- Surfaces that perform on-demand, non-conversational MCP calls (connection test, wizard tool listing) should use `mcp_server_single_usage=True` to avoid returning stale or empty results from the TTL cache. The connection test path already does this; the wizard tool-listing path does not — that asymmetry is the bug.
- When `get_mcp_server_tools` returns successfully but `len(tools) == 0`, this is treated as a failure in the tools-info service but as a success in the tester — that asymmetry is the second bug.
- Auth exceptions always propagate up unmodified; only non-auth exceptions are wrapped in domain-specific error types.

---

## 4. Testing Landscape

### Existing Coverage

**`mcp_tester.py` — Zero test coverage**
No test file for `MCPServerTester` exists anywhere in the `tests/` tree. The router endpoint `check_mcp_server` that calls `MCPServerTester(...).test()` also has no dedicated test. This entire file is untested.

**`mcp_tools_info_service.py` — Partial coverage**
- `tests/codemie/service/tools/test_mcp_tools_info_service.py`: 3 tests covering exception propagation only (`BrokerAuthRequiredException` re-raise, `auth_location` preservation, generic exception wrapping). All 3 tests mock `MCPToolkitService.get_mcp_server_tools` at `codemie.service.mcp.toolkit_service.MCPToolkitService.get_mcp_server_tools`.
- `tests/codemie/rest_api/routers/test_assistant_mcp_tools.py`: 10 tests for the HTTP endpoint `/v1/assistants/mcp_tools`, all patching `MCPToolsInfoService.get_mcp_toolkit_info` at the service boundary.

Neither file asserts that `get_mcp_server_tools` is called with `mcp_server_single_usage=True`.

**`toolkit_service.py` (MCPToolkitService) — Extensive coverage**
- `tests/codemie/service/mcp/test_toolkit_service_init.py`
- `tests/codemie/service/mcp/test_toolkit_service_toolkit.py` — `get_toolkit`/`get_toolkit_async`; cache hit/miss; only exercises `server_config.single_usage=False`
- `tests/codemie/service/mcp/test_toolkit_service_tools.py` — `get_tools`, `use_cache` forwarding
- `tests/codemie/service/mcp/test_toolkit_service_three_state.py`
- `tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py`
- `tests/codemie/service/mcp/test_toolkit_service_headers.py`
- `tests/codemie/service/mcp/test_toolkit_service_add_user_token.py`
- `tests/codemie/service/mcp/test_toolkit_service_integration.py`
- `tests/codemie/service/mcp/test_toolkit_service_user_mapping_isolation.py`
- `tests/codemie/service/mcp/test_toolkit_service_resolve_integration_credentials.py`
- `tests/codemie/service/tools/test_toolkit_service.py` — Tests `ToolkitService._determine_mcp_server_lifecycle()` (lines 477–527); verifies `mcp_server_single_usage` flows from request/conversation to `get_mcp_server_tools`

### Testing Framework and Patterns

- Framework: `pytest` with `unittest.TestCase`-style class grouping (mixed)
- Mocking: `unittest.mock.patch`, `patch.object`, `MagicMock`, `AsyncMock`
- Fixtures: `@pytest.fixture` at module level; session-scoped `mock_database_engine` in `tests/conftest.py`
- Conftest files: `tests/conftest.py` (session-wide DB patch), `tests/codemie/rest_api/routers/conftest.py` (router-level setup)
- MCPToolkitService tests patch the mock at `codemie.service.mcp.toolkit_service.MCPToolkitService.get_mcp_server_tools`

### Coverage Gaps

The fix adds or modifies behavior in two files; neither has tests for the specific scenarios being changed:

| Gap | File | Missing Test |
|---|---|---|
| `MCPServerTester.test()` returns `(False, "...")` when `len(tools) == 0` | `mcp_tester.py` | No test file exists at all |
| `MCPServerTester.test()` returns `(True, 'Success')` when tools are returned | `mcp_tester.py` | No test file exists at all |
| `MCPServerTester` passes `mcp_server_single_usage=True` to `get_mcp_server_tools` | `mcp_tester.py` | No test file exists at all |
| `MCPToolsInfoService.get_mcp_toolkit_info()` passes `mcp_server_single_usage=True` | `mcp_tools_info_service.py` | No test asserts the kwarg value |
| `get_toolkit` cache bypass when `single_usage=True` (only `False` path exercised) | `toolkit_service.py:1231` | `test_toolkit_service_toolkit.py` only exercises `single_usage=False` |
| `_prepare_server_config` sets `server_config.single_usage` from `mcp_server_single_usage=True` | `toolkit_service.py:939` | Not tested end-to-end |

---

## 5. Configuration and Environment

### Environment Variables

MCP-relevant environment variables consumed via `src/codemie/configs/config.py` (`Config(BaseSettings)`). No bare `os.environ` calls exist in MCP service code.

| Variable | Default | Relevance to Bug |
|---|---|---|
| `MCP_CONNECT_ENABLED` | `True` | Master switch; if `False`, entire MCP path skipped |
| `MCP_CONNECT_URL` | `http://localhost:3000` | Bridge URL; must point to the MCP-Connect service |
| `MCP_CONNECT_BUCKETS_COUNT` | `10` | Bridge connection sharding |
| `MCP_CLIENT_TIMEOUT` | `300.0` | Request timeout to bridge (seconds) |
| `MCP_TOOLKIT_SERVICE_CACHE_TTL` | `3600` | TTL for service-level cache; stale npx connections can persist up to 1 hour |
| `MCP_TOOLKIT_SERVICE_CACHE_SIZE` | `100` | LRU max entries for service-level cache |
| `MCP_TOOLKIT_FACTORY_CACHE_TTL` | `600` | TTL for toolkit factory cache; bypassed by `single_usage=True` |
| `MCP_TOOLKIT_FACTORY_CACHE_SIZE` | `50` | LRU max entries for toolkit factory cache |
| `MCP_TOOL_TOKENS_SIZE_LIMIT` | `30000` | Token budget for MCP tools in context |
| `MCP_AUTH_ENABLED` | `False` | Master auth switch; off by default |

### Configuration Files

- `src/codemie/configs/config.py` — Central `Config(BaseSettings)` class; all MCP settings defined here
- `src/codemie/rest_api/models/mcp_config.py` — `MCPServerConfigData` model; `single_usage: bool = Field(default=False, ...)` at line 134; `type` field distinguishes stdio (null) from HTTP (`"streamable-http"`)
- `config/mcp/mcp-commands-config.yaml` — Allowlist of permitted MCP command binaries; includes `npx` and `npx.cmd`; loaded fail-closed by `MCPCommandsConfig`
- `src/codemie/configs/mcp_commands_config.py` — Loads the YAML above; raises `ValueError` on missing/malformed file
- `src/codemie/configs/managed_mcp_config.py` — Loads `managed-mcp-servers.yaml` from `CUSTOMER_CONFIG_DIR` (per-deployment via ConfigMap); only supports `transport: Literal["http", "sse"]` — stdio/npx servers are NOT representable as managed servers; missing file degrades silently to empty list

### Feature Flags and Deployment Concerns

- `MCPServerConfigData.single_usage` (per-server field, default `False`) — the operative flag for this bug; not a global env var; stored per-server in the DB and per-conversation in the `mcp_server_single_usage` column (added by migration `974fb16cd138`)
- `MCP_CONNECT_ENABLED` — global on/off switch for the MCP bridge path
- `MCP_AUTH_ENABLED`, `MCP_AUTH_TMS_ENABLED` — auth subsystem toggles (off by default)

**Deployment concern — npx not in the `codemie` container:**
The `Dockerfile` installs Python, Poetry, and system utilities but does NOT install Node.js, npm, or npx. npx/stdio MCP servers run inside the separate MCP-Connect bridge service. The `docker-compose.yml` does not define an `mcp-connect` service and does not set `MCP_CONNECT_URL` in the `codemie` service's environment block. In containerized deployments, `MCP_CONNECT_URL` must be explicitly set to reach the bridge (e.g. `http://host.docker.internal:3000`); the default `http://localhost:3000` will fail to resolve inside the container.

---

## 6. Risk Indicators

- **`mcp_tester.py` has zero test coverage.** `MCPServerTester.test()` has never been tested; adding a test file is required as part of the fix. The fix adds a new code path (empty-tools guard returning `(False, ...)`), which is untestable without a new test file.

- **`mcp_tools_info_service.py` existing tests do not assert `mcp_server_single_usage` kwarg.** The three existing tests patch `get_mcp_server_tools` at the boundary but never assert how it is called. Without adding a new test that uses `assert_called_with` or a `call_args` assertion, the `mcp_server_single_usage=True` fix can be silently reverted with no test failure.

- **Two additional callers of `get_mcp_server_tools` also omit `mcp_server_single_usage`:** `workflows/config_resources_validation.py:181` and `workflows/validation/resources.py:342`. These are outside the stated fix scope but follow the same defect pattern. Noted as potential follow-up candidates.

- **`MCPToolkitFactory._toolkit_cache` single_usage=True branch is untested.** `test_toolkit_service_toolkit.py` only exercises `server_config.single_usage=False`. The cache-bypass path (`should_use_cache = False`) has no direct test coverage. The fix relies on this branch behaving correctly.

- **No global env-var escape hatch for `single_usage`.** There is no `MCP_FORCE_SINGLE_USAGE=True` flag. All managed MCP server records in DB have `"single_usage": false` hardcoded (confirmed in Alembic migration data). If the code fix is not applied, there is no deployment-level workaround.

- **`MCP_CONNECT_URL` not set in `docker-compose.yml` environment block.** In a containerized deployment, the default `http://localhost:3000` will fail silently. Not a regression from this fix but a latent deployment risk.

- **npx/stdio server is not representable as a managed MCP server** (managed config only allows `"http"` or `"sse"` transport). Shopify Dev MCP must be registered via the user-facing UI/API, not managed config injection.

- **`_prepare_server_config` uses `or` logic** (`mcp_server.config and mcp_server.config.single_usage or mcp_server_single_usage`) which has Python operator precedence subtleties. If `mcp_server.config.single_usage` is truthy and `mcp_server_single_usage` is `False`, the result is `True` (correct). If both are `False`, the result is `False`. If `mcp_server.config` is `None`, the short-circuit falls through to `mcp_server_single_usage`. This logic is correct for the fix but worth verifying in the test.

---

## 7. Summary for Complexity Assessment

The fix touches exactly two files in the Service layer — `src/codemie/service/mcp/mcp_tester.py` and `src/codemie/service/tools/mcp_tools_info_service.py` — and requires new test additions. No changes are needed in the router layer, the core engine (`MCPToolkitService`, `MCPToolkitFactory`, `MCPConnectClient`), data models, migrations, or config. The total production code change surface is two one-to-five-line additions. The architectural layers touched are: Service (two classes in two files) only.

Both fixes follow established patterns already present in the codebase. The `mcp_server_single_usage=True` pattern is already used correctly in `MCPServerTester.test()` at line 37; the fix in `mcp_tools_info_service.py` is a one-argument addition matching an identical pattern. The empty-tools guard pattern is already used in `MCPToolsInfoService.get_mcp_toolkit_info()` at lines 64–72; the fix in `mcp_tester.py` is a symmetrical guard returning `(False, "...")` instead of raising. Neither fix introduces a new pattern; both reconcile an asymmetry with an established one. Technical novelty is zero.

Test coverage posture is the primary risk. `mcp_tester.py` has no test file at all — the fix requires creating `tests/codemie/service/mcp/test_mcp_tester.py` from scratch. `test_mcp_tools_info_service.py` exists but lacks kwarg-level assertions; a new test case must be added to assert `mcp_server_single_usage=True` is passed. Using the project's existing pytest + `unittest.mock.patch` patterns, both test additions are straightforward. The two additional callers of `get_mcp_server_tools` that also omit `mcp_server_single_usage` (`workflows/config_resources_validation.py:181`, `workflows/validation/resources.py:342`) are outside scope but should be tracked as a follow-up to avoid the same class of stale-cache bug in validation flows.
