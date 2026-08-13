# Spec — EPMCDME-11254: Shopify Dev MCP Tools Not Retrieved After Successful Connection Test

## Problem

Two bugs in the MCP integration surface cause tools to silently disappear after a successful connection test:

1. **`MCPServerTester.test()` returns `(True, 'Success')` unconditionally** even when zero tools are retrieved. The connection test passes but no tools are available for the assistant.

2. **`MCPToolsInfoService.get_mcp_toolkit_info()` calls `get_mcp_server_tools()` with `mcp_server_single_usage=False`** (the default/cached path). For stdio-based servers like `@shopify/dev-mcp@latest` launched via `npx`, the bridge caches the process spawned during the connection test (`single_usage=True`). Once that cache entry is evicted (bridge restart, delay, GC), the cached path returns an empty tool list silently.

The connection test and the tool-listing call use different lifecycle modes, so a passing connection test gives no guarantee that tool listing will succeed later.

## Scope

Two files changed. No router, model, migration, workflow, or config changes.

Out of scope (follow-up ticket): `config_resources_validation.py:181` and `resources.py:342` — two other callers of `get_mcp_server_tools` that also use `single_usage=False`. Not fixed here.

## Fix 1 — `mcp_tester.py`: guard against empty tools

**File:** `src/codemie/service/mcp/mcp_tester.py`

After `get_mcp_server_tools()` returns, insert an empty-tools guard before the success return:

```python
if not tools:
    logger.warning(
        f"Connection to '{self.mcp_server.name}' succeeded but no tools were retrieved."
    )
    return False, (
        f"Connected to '{self.mcp_server.name}' but no tools were retrieved. "
        "Please check that the MCP server is running and has tools configured."
    )
```

The `mcp_server_single_usage=True` on line 37 is already correct and stays unchanged.

**After fix, `test()` behaviour:**

| Condition | Return |
|---|---|
| `get_mcp_server_tools` raises `BrokerAuthRequiredException` | re-raised |
| `get_mcp_server_tools` raises `MCPAuthenticationRequiredException` | re-raised |
| `get_mcp_server_tools` raises any other exception | `(False, "<msg>.\nPlease, check the configuration.")` |
| `tools` is empty list | `(False, "Connected to '<name>' but no tools were retrieved. Please check that the MCP server is running and has tools configured.")` |
| `tools` is non-empty | `(True, 'Success')` |

## Fix 2 — `mcp_tools_info_service.py`: use single-use mode

**File:** `src/codemie/service/tools/mcp_tools_info_service.py`

Add `mcp_server_single_usage=True` as a keyword argument to the `get_mcp_server_tools()` call at lines 58–62:

```python
tools = MCPToolkitService.get_mcp_server_tools(
    mcp_servers=[mcp_server_config],
    user_id=user.id,
    project_name=project_name,
    mcp_server_single_usage=True,
)
```

The existing empty-tools guard at lines 64–72 (raises `MCPToolsInfoServiceError`) is already correct and fires reliably once the tool list is fetched fresh.

## Tests

### New file: `tests/codemie/service/mcp/test_mcp_tester.py`

Six test cases in class `TestMCPServerTesterTest`:

| Test | Scenario | Expected |
|---|---|---|
| `test_returns_false_when_tools_empty` | `get_mcp_server_tools` returns `[]` | `(False, msg)` where `msg` contains server name |
| `test_returns_true_when_tools_found` | `get_mcp_server_tools` returns non-empty list | `(True, 'Success')` |
| `test_returns_false_on_generic_exception` | `get_mcp_server_tools` raises `RuntimeError` | `(False, msg)` containing exception text |
| `test_reraises_broker_auth_exception` | `get_mcp_server_tools` raises `BrokerAuthRequiredException` | exception re-raised |
| `test_reraises_mcp_auth_exception` | `get_mcp_server_tools` raises `MCPAuthenticationRequiredException` | exception re-raised |
| `test_calls_get_mcp_server_tools_with_single_usage_true` | normal call | `get_mcp_server_tools` called with `mcp_server_single_usage=True` |

Patch target: `"codemie.service.mcp.toolkit_service.MCPToolkitService.get_mcp_server_tools"`

Fixtures: `mock_user` (Mock spec=User, id="test-user-id"), `mcp_server` (MCPServerDetails(name="Test MCP", enabled=True)), `tester` (MCPServerTester built from MCPServerCheckRequest + mock_user).

### Extended file: `tests/codemie/service/tools/test_mcp_tools_info_service.py`

New class `TestMCPToolsInfoServiceSingleUsageMode` with three tests:

| Test | Scenario | Expected |
|---|---|---|
| `test_get_mcp_server_tools_called_with_single_usage_true` | normal call | `get_mcp_server_tools` called with `mcp_server_single_usage=True` |
| `test_success_path_returns_toolkit_dict` | returns 1 tool | result is dict with `toolkit`, `label`, `tools` keys |
| `test_empty_tools_raises_service_error` | returns `[]` | raises `MCPToolsInfoServiceError` |

Patch target: `"codemie.service.mcp.toolkit_service.MCPToolkitService.get_mcp_server_tools"`

## Acceptance criteria

1. Connection test on an MCP server that returns zero tools reports failure with a descriptive message (not silent success).
2. Tool listing in the wizard uses `single_usage=True`, bypassing any stale bridge process cache.
3. All new and existing tests pass under `make test`.
4. `make ruff` reports no new violations.
