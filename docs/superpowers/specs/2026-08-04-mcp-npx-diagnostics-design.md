# Design: EPMCDME-13900 — MCP npx Connection-Closed Diagnostics

**Run**: 20260804-1031-EPMCDME-13900  
**Date**: 2026-08-04  
**Status**: Approved (autonomous)

---

## Problem

When a user clicks "Test Connection" for an npx-based MCP server, they receive:

> "Failed to load MCP tools from 'my-server' - Failed to create MCP client: McpError: Connection closed (ValueError). Please, check the configuration."

This tells the user nothing actionable. The error could mean the npx package doesn't exist, npm's stdout pollutes the MCP protocol, the bridge's 30s init timeout fired, a required env var is missing, or a genuine handshake failure. All of these produce the same generic message.

### Error chain (as-is)

```
MCPServerTester.test()
  → MCPToolkitFactory.create_toolkit()
    → MCPConnectClient.list_tools()
      → httpx POST to MCP-Connect bridge
      ← HTTPStatusError (bridge returns 500/504 with body
         "Failed to create MCP client: McpError: Connection closed")
    raise ValueError(extracted_text)        ← client.py:184
  raise MCPToolLoadException(server, e)     ← toolkit_service.py
return False, f"{e}.\nPlease, check..."     ← mcp_tester.py
```

### Constraints

- The MCP-Connect bridge uses `extra="forbid"` on its Pydantic body model — we cannot add new fields (e.g. `init_timeout_seconds`) to `MCPToolInvocationRequest` without a coordinated bridge change.
- The bridge-side init timeout is 30s by default (`MCP_CONNECT_INIT_TIMEOUT`). The Python-side timeout was raised to 300s in EPMCDME-10045 but the bridge is still the binding constraint.
- `_sanitize_exception_for_log()` in `toolkit_service.py` strips all non-httpx exception message bodies to `type(exc).__name__`. A `MCPBridgeError` that subclasses `ValueError` would be silently truncated to just `"MCPBridgeError"` in logs, reintroducing the diagnostic gap.

---

## Scope

This PR delivers **diagnostic classification** — actionable error messages and typed error categories. The functional fix (raising the bridge's 30s init timeout) requires a bridge-side change and is tracked separately.

---

## Design

### 1. New types in `models.py`

#### `MCPErrorCategory(str, Enum)`

```python
class MCPErrorCategory(str, Enum):
    CONNECTION_CLOSED = "CONNECTION_CLOSED"
    TIMEOUT           = "TIMEOUT"
    STARTUP_FAILURE   = "STARTUP_FAILURE"
    AUTH_REQUIRED     = "AUTH_REQUIRED"
    UNKNOWN           = "UNKNOWN"
```

Uses `str` mixin so values serialize naturally in JSON/logs.

#### `classify_mcp_error(error_text: str, status_code: int | None = None) -> MCPErrorCategory`

Module-level function. Status-code checks run **before** text matching to prevent misclassification: the MCP-Connect bridge returns HTTP 504 with a body containing `"McpError: Connection closed"` when the init timeout fires — a timeout event, not a true connection-close. If text matching ran first, that 504 response would be classified as `CONNECTION_CLOSED` and the user would receive the wrong hint (npm stdout advice instead of `MCP_CONNECT_INIT_TIMEOUT`).

Evaluation order (first match wins, all text comparisons case-insensitive):

| Priority | Trigger | Category |
|---|---|---|
| 1 | `status_code in {408, 504}` | `TIMEOUT` |
| 2 | `"connection closed"` or `"mcperror"` in text | `CONNECTION_CLOSED` |
| 3 | `"timed out"`, `"timeout"`, `"did not respond"` in text | `TIMEOUT` |
| 4 | `"enoent"`, `"no such file"`, `"command not found"`, `"cannot find module"`, `"npm err"`, `"npx: not found"` in text | `STARTUP_FAILURE` |
| 5 | `status_code in {401, 403}` | `AUTH_REQUIRED` |
| 6 | Otherwise | `UNKNOWN` |

#### `MCPBridgeError(Exception)` — NOT a ValueError subclass

```python
class MCPBridgeError(Exception):
    """Typed exception raised by MCPConnectClient on bridge HTTP errors.

    Inherits Exception (not ValueError) so _sanitize_exception_for_log()
    can handle it explicitly without stripping the message body.
    """
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code: int = status_code
```

**Why not `ValueError`**: `_sanitize_exception_for_log()` falls through to `return type(exc).__name__` for every non-httpx exception (which includes `ValueError` subclasses). Making `MCPBridgeError` a `ValueError` subclass means the full error text is lost in logs. Making it a plain `Exception` subclass lets us add an explicit handler in the sanitizer that preserves the text.

#### `MCPToolLoadException.category` field

In `MCPToolLoadException.__init__`, add before the `super().__init__()` call:

```python
_status_code = getattr(original_error, "status_code", None)
self.category: MCPErrorCategory = classify_mcp_error(str(original_error), _status_code)
```

The field is always set regardless of original error type, defaulting to `UNKNOWN` for unclassifiable errors.

---

### 2. `client.py` — raise `MCPBridgeError` instead of `ValueError`

In `list_tools()` `except httpx.HTTPStatusError` block:

```python
# before
raise ValueError(_error_message_from_http_status_error(e)) from e

# after
from codemie.service.mcp.models import MCPBridgeError
error_text = _error_message_from_http_status_error(e)
raise MCPBridgeError(error_text, e.response.status_code) from e
```

No other changes to `client.py`. The `BrokerAuthRequiredException` path (401/403 + `WWW-Authenticate`) is unchanged.

---

### 3. `toolkit_service.py` — update `_sanitize_exception_for_log()`

Add an explicit handler for `MCPBridgeError` before the generic fallback:

```python
from codemie.service.mcp.models import MCPBridgeError

@classmethod
def _sanitize_exception_for_log(cls, exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"{exc}: status_code={exc.response.status_code}, url=..."
    if isinstance(exc, httpx.RequestError):
        return f"{exc}: url=..."
    if isinstance(exc, MCPBridgeError):                          # new
        return f"MCPBridgeError: {exc} (status_code={exc.status_code})"
    return type(exc).__name__
```

This ensures the full bridge error message appears in server logs while still filtering other exception types.

---

### 4. `mcp_tester.py` — category-specific hints

Two module-level helper functions added before the class definition:

#### `_get_server_command(mcp_server) -> str | None`

Checks `mcp_server.command` (top-level field on `MCPServerDetails`), falls back to `mcp_server.config.command` if `config` is present. Returns `None` when neither is set.

#### `_build_hint_for_category(category: MCPErrorCategory, mcp_server) -> str`

| Category | Is npx? | Hint |
|---|---|---|
| `CONNECTION_CLOSED` | Yes | Multi-line: (1) npm may write progress to stdout — MCP requires clean stdout; (2) confirm the package exists: `npx <package> --version`; (3) the bridge's `MCP_CONNECT_INIT_TIMEOUT` may be too short if the package downloads on first use |
| `CONNECTION_CLOSED` | No | "Check that the server process starts correctly and writes only valid MCP JSON to stdout." |
| `TIMEOUT` | — | "The MCP bridge timed out waiting for server initialization. If this server downloads packages on first use, increase `MCP_CONNECT_INIT_TIMEOUT` on the bridge service." |
| `STARTUP_FAILURE` | — | "The server process failed to start. Verify the command path, that all required environment variables are set in the MCP server configuration, and that the package or binary is accessible." |
| `AUTH_REQUIRED` | — | "Authentication is required. Check that authentication tokens or credentials are present in the MCP server environment configuration." |
| `UNKNOWN` | — | "Please, check the configuration." (unchanged) |

#### Updated `test()` method

```python
# before (single handler):
except Exception as e:
    return False, f"{str(e)}.\nPlease, check the configuration."

# after (two handlers — MCPToolLoadException first):
except MCPToolLoadException as e:
    hint = _build_hint_for_category(e.category, self.mcp_server)
    return False, f"{str(e)}.\n{hint}"
except Exception as e:
    return False, f"{str(e)}.\nPlease, check the configuration."
```

The existing `BrokerAuthRequiredException` and `MCPAuthenticationRequiredException` re-raise paths that appear before the catch-all are unaffected.

---

## Data Flow (after)

```
MCPServerTester.test()
  → MCPToolkitFactory.create_toolkit()
    → MCPConnectClient.list_tools()
      ← HTTPStatusError
    raise MCPBridgeError("...Connection closed", 500)   ← typed, not ValueError
  raise MCPToolLoadException(server, e)                 ← e.category = CONNECTION_CLOSED
except MCPToolLoadException as e:
  hint = _build_hint_for_category(CONNECTION_CLOSED, server)  ← npx-specific hint
return False, "...\nnpm may write progress to stdout..."
```

---

## Testing

### `tests/codemie/service/mcp/test_models.py` — new class `TestMCPErrorClassification`

- Each pattern row in the classification table → expected category
- Case-insensitivity (uppercase `"CONNECTION CLOSED"` → `CONNECTION_CLOSED`)
- `MCPToolLoadException` wrapping plain `Exception("Connection closed")` → `category == CONNECTION_CLOSED`
- `MCPToolLoadException` wrapping `MCPBridgeError("...", status_code=504)` → `category == TIMEOUT`
- `MCPBridgeError.status_code` attribute preserved
- `MCPBridgeError` is NOT a `ValueError` instance

### `tests/codemie/service/mcp/test_mcp_tester.py` — new file `TestMCPServerTesterHints`

- `CONNECTION_CLOSED` + npx command → hint contains "stdout"
- `CONNECTION_CLOSED` + non-npx → generic connection-closed hint (no npx keywords)
- `TIMEOUT` → hint mentions `MCP_CONNECT_INIT_TIMEOUT`
- `STARTUP_FAILURE` → hint mentions env vars or command path
- `UNKNOWN` → fallback "Please, check the configuration."
- Non-`MCPToolLoadException` → generic "check the configuration" (backwards compat)
- `BrokerAuthRequiredException` is re-raised, not swallowed

### `tests/codemie/service/mcp/test_client.py` — new class `TestMCPBridgeError`

- `list_tools()` raises `MCPBridgeError` (not `ValueError`) on HTTP 500
- `exc.status_code == 500` preserved on raised exception
- Existing `pytest.raises(ValueError)` assertion at line 225 must be updated to `pytest.raises(MCPBridgeError)` since `MCPBridgeError` no longer subclasses `ValueError`

### `tests/codemie/service/mcp/test_mcp_tester.py` — `TestMCPServerTesterNpxPath` (AC5 regression)

A test class that exercises the full classification + hint path with an `MCPServerDetails` whose `command` is `"npx"`:

- Bridge 500 response, body `"Failed to create MCP client: McpError: Connection closed"` + npx command → result contains npx-specific keywords (e.g. "stdout")
- Bridge 504 response, body `"McpError: Connection closed"` + npx command → category is `TIMEOUT`, hint mentions `MCP_CONNECT_INIT_TIMEOUT` (verifies status-code-first classification; this is the primary npx failure mode)
- Bridge 500, body `"ENOENT: no such file"` + npx command → category is `STARTUP_FAILURE`

These tests mock the httpx bridge call and exercise `MCPServerTester.test()` end-to-end to cover AC5's requirement that regression testing covers npx-based MCP server startup.

### Regression: `_sanitize_exception_for_log()` with `MCPBridgeError`

- Inline test confirming the sanitizer returns the full message + status_code string (not just `"MCPBridgeError"`)

---

## Files Changed

| File | Change |
|---|---|
| `src/codemie/service/mcp/models.py` | Add `MCPErrorCategory`, `classify_mcp_error`, `MCPBridgeError`, `MCPToolLoadException.category` |
| `src/codemie/service/mcp/client.py` | Raise `MCPBridgeError` instead of `ValueError` in `list_tools` |
| `src/codemie/service/mcp/toolkit_service.py` | Handle `MCPBridgeError` in `_sanitize_exception_for_log()` |
| `src/codemie/service/mcp/mcp_tester.py` | Category-specific hints via `_build_hint_for_category()` |
| `tests/codemie/service/mcp/test_models.py` | New `TestMCPErrorClassification` class |
| `tests/codemie/service/mcp/test_mcp_tester.py` | New file: `TestMCPServerTesterHints` |
| `tests/codemie/service/mcp/test_client.py` | Update line 225 + new `TestMCPBridgeError` class |

---

## Out of Scope

- Raising the bridge's `MCP_CONNECT_INIT_TIMEOUT` default — bridge-side change, tracked separately
- Adding `init_timeout_seconds` to `MCPToolInvocationRequest` — blocked by bridge `extra="forbid"`
- Changes to `MCPToolExecutionError`, `MCPToolkit`, or any non-MCP code paths
