# MCP npx Connection-Closed Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the generic "Please, check the configuration." error with typed error classification and category-specific actionable hints when an npx-based MCP server fails to initialize.

**Architecture:** Add `MCPErrorCategory(str, Enum)` and `MCPBridgeError(Exception)` to `models.py`; update `client.py` to raise `MCPBridgeError` instead of bare `ValueError`; update `_sanitize_exception_for_log()` in `toolkit_service.py` to preserve the full bridge error message in logs; add `_build_hint_for_category()` in `mcp_tester.py` to surface actionable hints. Four source files, three test files.

**Tech Stack:** Python 3.11+, pytest, pytest-asyncio, unittest.mock

## Global Constraints

- All new test classes use pytest style (no `unittest.TestCase`) per `.ai-run/guides/testing/testing-patterns.md`
- `MCPBridgeError` MUST NOT subclass `ValueError` — `_sanitize_exception_for_log()` strips all `ValueError` message bodies to the type name only, which would silence the diagnostic text in logs
- `classify_mcp_error()` MUST check `status_code in {408, 504}` at priority 1, before any text matching — a bridge 504 response body typically contains `"McpError: Connection closed"` and would be misclassified as `CONNECTION_CLOSED` without this precedence rule
- No new fields may be added to `MCPToolInvocationRequest` — the bridge uses `extra="forbid"` on its body model
- Branch: `EPMCDME-13900_mcp-npx-connection-closed-diagnostics`

---

### Task 1: MCPErrorCategory, MCPBridgeError, classify_mcp_error, and MCPToolLoadException.category

**Test-first: yes — `TestMCPErrorClassification.test_connection_closed_text_classifies_as_connection_closed` fails with `ImportError: cannot import name 'MCPErrorCategory'`**

**Files:**
- Modify: `src/codemie/service/mcp/models.py` (after line 29, before `class MCPExecutionContext`)
- Modify: `src/codemie/service/mcp/models.py` (before `class MCPToolLoadException` at line 459)
- Modify: `src/codemie/service/mcp/models.py` (`MCPToolLoadException.__init__`, lines 467–487)
- Test: `tests/codemie/service/mcp/test_models.py` (new class appended at end)

**Interfaces:**
- Produces:
  - `MCPErrorCategory` — `str` enum with values `CONNECTION_CLOSED`, `TIMEOUT`, `STARTUP_FAILURE`, `AUTH_REQUIRED`, `UNKNOWN`
  - `classify_mcp_error(error_text: str, status_code: int | None = None) -> MCPErrorCategory`
  - `MCPBridgeError(message: str, status_code: int)` — `Exception` subclass with `.status_code` attribute
  - `MCPToolLoadException.category: MCPErrorCategory` — auto-set in `__init__`

- [ ] **Step 1: Write the failing tests**

In `tests/codemie/service/mcp/test_models.py`, append this new class at the end of the file:

```python
class TestMCPErrorClassification:
    """Tests for MCPErrorCategory, classify_mcp_error, and MCPBridgeError."""

    def test_connection_closed_text_classifies_as_connection_closed(self):
        from codemie.service.mcp.models import MCPErrorCategory, classify_mcp_error
        assert classify_mcp_error("Failed to create MCP client: McpError: Connection closed") == MCPErrorCategory.CONNECTION_CLOSED

    def test_mcperror_text_classifies_as_connection_closed(self):
        from codemie.service.mcp.models import MCPErrorCategory, classify_mcp_error
        assert classify_mcp_error("McpError: something went wrong") == MCPErrorCategory.CONNECTION_CLOSED

    def test_timed_out_text_classifies_as_timeout(self):
        from codemie.service.mcp.models import MCPErrorCategory, classify_mcp_error
        assert classify_mcp_error("Request timed out after 30s") == MCPErrorCategory.TIMEOUT

    def test_timeout_text_classifies_as_timeout(self):
        from codemie.service.mcp.models import MCPErrorCategory, classify_mcp_error
        assert classify_mcp_error("gateway timeout waiting for server") == MCPErrorCategory.TIMEOUT

    def test_status_504_overrides_connection_closed_text(self):
        """Bridge 504 timeout response body often contains 'connection closed' — must classify as TIMEOUT."""
        from codemie.service.mcp.models import MCPErrorCategory, classify_mcp_error
        assert classify_mcp_error("McpError: Connection closed", status_code=504) == MCPErrorCategory.TIMEOUT

    def test_status_408_classifies_as_timeout(self):
        from codemie.service.mcp.models import MCPErrorCategory, classify_mcp_error
        assert classify_mcp_error("request timeout", status_code=408) == MCPErrorCategory.TIMEOUT

    def test_enoent_classifies_as_startup_failure(self):
        from codemie.service.mcp.models import MCPErrorCategory, classify_mcp_error
        assert classify_mcp_error("ENOENT: no such file or directory") == MCPErrorCategory.STARTUP_FAILURE

    def test_command_not_found_classifies_as_startup_failure(self):
        from codemie.service.mcp.models import MCPErrorCategory, classify_mcp_error
        assert classify_mcp_error("command not found: node") == MCPErrorCategory.STARTUP_FAILURE

    def test_cannot_find_module_classifies_as_startup_failure(self):
        from codemie.service.mcp.models import MCPErrorCategory, classify_mcp_error
        assert classify_mcp_error("Cannot find module 'some-mcp-server'") == MCPErrorCategory.STARTUP_FAILURE

    def test_npm_err_classifies_as_startup_failure(self):
        from codemie.service.mcp.models import MCPErrorCategory, classify_mcp_error
        assert classify_mcp_error("npm err: missing peer dependency") == MCPErrorCategory.STARTUP_FAILURE

    def test_npx_not_found_classifies_as_startup_failure(self):
        from codemie.service.mcp.models import MCPErrorCategory, classify_mcp_error
        assert classify_mcp_error("npx: not found") == MCPErrorCategory.STARTUP_FAILURE

    def test_status_401_with_uninformative_text_classifies_as_auth_required(self):
        from codemie.service.mcp.models import MCPErrorCategory, classify_mcp_error
        assert classify_mcp_error("Unauthorized", status_code=401) == MCPErrorCategory.AUTH_REQUIRED

    def test_unknown_text_and_no_status_classifies_as_unknown(self):
        from codemie.service.mcp.models import MCPErrorCategory, classify_mcp_error
        assert classify_mcp_error("something completely unexpected") == MCPErrorCategory.UNKNOWN

    def test_classification_is_case_insensitive(self):
        from codemie.service.mcp.models import MCPErrorCategory, classify_mcp_error
        assert classify_mcp_error("CONNECTION CLOSED") == MCPErrorCategory.CONNECTION_CLOSED
        assert classify_mcp_error("TIMED OUT") == MCPErrorCategory.TIMEOUT
        assert classify_mcp_error("ENOENT") == MCPErrorCategory.STARTUP_FAILURE

    def test_mcp_bridge_error_is_not_value_error(self):
        from codemie.service.mcp.models import MCPBridgeError
        err = MCPBridgeError("some error", status_code=500)
        assert not isinstance(err, ValueError)
        assert isinstance(err, Exception)

    def test_mcp_bridge_error_preserves_message_and_status_code(self):
        from codemie.service.mcp.models import MCPBridgeError
        err = MCPBridgeError("McpError: Connection closed", status_code=500)
        assert str(err) == "McpError: Connection closed"
        assert err.status_code == 500

    def test_mcp_tool_load_exception_category_from_plain_exception(self):
        from codemie.service.mcp.models import MCPErrorCategory, MCPToolLoadException
        original = Exception("Failed to create MCP client: McpError: Connection closed")
        exc = MCPToolLoadException("my-server", original)
        assert exc.category == MCPErrorCategory.CONNECTION_CLOSED

    def test_mcp_tool_load_exception_category_from_bridge_error_504(self):
        from codemie.service.mcp.models import MCPErrorCategory, MCPBridgeError, MCPToolLoadException
        original = MCPBridgeError("McpError: Connection closed", status_code=504)
        exc = MCPToolLoadException("my-server", original)
        assert exc.category == MCPErrorCategory.TIMEOUT

    def test_mcp_tool_load_exception_category_from_bridge_error_500_connection_closed(self):
        from codemie.service.mcp.models import MCPErrorCategory, MCPBridgeError, MCPToolLoadException
        original = MCPBridgeError("Failed to create MCP client: McpError: Connection closed", status_code=500)
        exc = MCPToolLoadException("my-server", original)
        assert exc.category == MCPErrorCategory.CONNECTION_CLOSED

    def test_mcp_tool_load_exception_category_defaults_to_unknown(self):
        from codemie.service.mcp.models import MCPErrorCategory, MCPToolLoadException
        exc = MCPToolLoadException("my-server", Exception("unexpected error"))
        assert exc.category == MCPErrorCategory.UNKNOWN
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/isa_kuliev/Work/codemie
poetry run pytest tests/codemie/service/mcp/test_models.py::TestMCPErrorClassification -v 2>&1 | tail -20
```

Expected: FAIL with `ImportError: cannot import name 'MCPErrorCategory' from 'codemie.service.mcp.models'`

- [ ] **Step 3: Implement — add `from enum import Enum` to models.py imports**

In `src/codemie/service/mcp/models.py`, the imports at line 26 start with `import os`. Add the enum import:

```python
import os
from enum import Enum
from typing import Any
```

- [ ] **Step 4: Implement — add MCPErrorCategory, classify_mcp_error, MCPBridgeError after the imports block (before class MCPExecutionContext at line 35)**

Insert after the existing imports, before `class MCPExecutionContext(BaseModel):`:

```python

class MCPErrorCategory(str, Enum):
    CONNECTION_CLOSED = "CONNECTION_CLOSED"
    TIMEOUT = "TIMEOUT"
    STARTUP_FAILURE = "STARTUP_FAILURE"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    UNKNOWN = "UNKNOWN"


def classify_mcp_error(error_text: str, status_code: int | None = None) -> MCPErrorCategory:
    """Classify a bridge error into a diagnostic category.

    Status-code checks run before text matching: a bridge 504 response body
    typically contains 'connection closed', which would be misclassified as
    CONNECTION_CLOSED without this precedence rule.
    """
    lower = error_text.lower()

    if status_code in {408, 504}:
        return MCPErrorCategory.TIMEOUT

    if "connection closed" in lower or "mcperror" in lower:
        return MCPErrorCategory.CONNECTION_CLOSED

    if "timed out" in lower or "timeout" in lower or "did not respond" in lower:
        return MCPErrorCategory.TIMEOUT

    if any(
        marker in lower
        for marker in ("enoent", "no such file", "command not found", "cannot find module", "npm err", "npx: not found")
    ):
        return MCPErrorCategory.STARTUP_FAILURE

    if status_code in {401, 403}:
        return MCPErrorCategory.AUTH_REQUIRED

    return MCPErrorCategory.UNKNOWN


class MCPBridgeError(Exception):
    """Raised by MCPConnectClient when the MCP-Connect bridge returns a non-auth HTTP error.

    Does NOT subclass ValueError. _sanitize_exception_for_log() in toolkit_service.py
    strips all ValueError message bodies to just the type name; this class stays as a
    plain Exception so that handler can preserve the full message text.
    """

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code: int = status_code
```

- [ ] **Step 5: Implement — add category auto-classification to MCPToolLoadException.__init__**

In `src/codemie/service/mcp/models.py`, find `MCPToolLoadException.__init__` at approximately line 467. The current body is:

```python
        self.server_name = server_name
        self.original_error = original_error
        self.assistant_name = assistant_name
        self.assistant_id = assistant_id
        super().__init__(self._build_message())
```

Replace it with:

```python
        self.server_name = server_name
        self.original_error = original_error
        self.assistant_name = assistant_name
        self.assistant_id = assistant_id
        _status_code = getattr(original_error, "status_code", None)
        self.category: MCPErrorCategory = classify_mcp_error(str(original_error), _status_code)
        super().__init__(self._build_message())
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd /Users/isa_kuliev/Work/codemie
poetry run pytest tests/codemie/service/mcp/test_models.py::TestMCPErrorClassification -v 2>&1 | tail -25
```

Expected: all tests PASS

- [ ] **Step 7: Run full models test suite to verify no regressions**

```bash
cd /Users/isa_kuliev/Work/codemie
poetry run pytest tests/codemie/service/mcp/test_models.py -v 2>&1 | tail -20
```

Expected: all existing tests still PASS

- [ ] **Step 8: Commit**

```bash
git add src/codemie/service/mcp/models.py tests/codemie/service/mcp/test_models.py
git commit -m "feat(EPMCDME-13900): add MCPErrorCategory, MCPBridgeError, and classify_mcp_error to models"
```

---

### Task 2: Update client.py to raise MCPBridgeError

**Test-first: yes — `TestMCPBridgeError.test_list_tools_raises_mcp_bridge_error_not_value_error` fails because `list_tools` still raises `ValueError`**

**Files:**
- Modify: `src/codemie/service/mcp/client.py` (line 184)
- Test: `tests/codemie/service/mcp/test_client.py` (line 225 update + new `TestMCPBridgeError` class appended at end)

**Interfaces:**
- Consumes: `MCPBridgeError(message, status_code)` from `codemie.service.mcp.models` (Task 1)
- Produces: `list_tools()` now raises `MCPBridgeError` (not `ValueError`) on HTTP 4xx/5xx non-auth errors

- [ ] **Step 1: Write the new test class for MCPBridgeError in test_client.py**

Append this class at the end of `tests/codemie/service/mcp/test_client.py`:

```python
class TestMCPBridgeError:
    """Verify that list_tools raises MCPBridgeError (not ValueError) on bridge HTTP errors."""

    @pytest.mark.asyncio
    async def test_list_tools_raises_mcp_bridge_error_not_value_error(self, server_config):
        """list_tools must raise MCPBridgeError, not ValueError, on bridge HTTP 500."""
        from codemie.service.mcp.models import MCPBridgeError
        client = MCPConnectClient()

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.reason_phrase = "Internal Server Error"
        mock_response.text = '{"error": "Test error message"}'
        mock_response.json.return_value = {"error": "Test error message"}
        mock_request = MagicMock()

        http_error = httpx.HTTPStatusError("Error", request=mock_request, response=mock_response)
        mock_http_response = MagicMock()
        mock_http_response.raise_for_status.side_effect = http_error

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_http_response)

            with pytest.raises(MCPBridgeError, match="Test error message") as exc_info:
                await client.list_tools(server_config)

        assert exc_info.value.status_code == 500
        assert not isinstance(exc_info.value, ValueError)

    @pytest.mark.asyncio
    async def test_list_tools_bridge_error_preserves_status_code(self, server_config):
        """MCPBridgeError.status_code reflects the HTTP status from the bridge response."""
        from codemie.service.mcp.models import MCPBridgeError
        client = MCPConnectClient()

        mock_response = MagicMock()
        mock_response.status_code = 502
        mock_response.reason_phrase = "Bad Gateway"
        mock_response.text = "upstream connect error"
        mock_response.json.side_effect = Exception("not json")
        mock_request = MagicMock()

        http_error = httpx.HTTPStatusError("Error", request=mock_request, response=mock_response)
        mock_http_response = MagicMock()
        mock_http_response.raise_for_status.side_effect = http_error

        with patch("codemie.service.mcp.client.httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_http_response)

            with pytest.raises(MCPBridgeError) as exc_info:
                await client.list_tools(server_config)

        assert exc_info.value.status_code == 502
```

- [ ] **Step 2: Run the new tests to see them fail**

```bash
cd /Users/isa_kuliev/Work/codemie
poetry run pytest tests/codemie/service/mcp/test_client.py::TestMCPBridgeError -v 2>&1 | tail -15
```

Expected: FAIL because `list_tools` still raises `ValueError`, not `MCPBridgeError`

- [ ] **Step 3: Update the existing test at line 225 to expect MCPBridgeError**

In `tests/codemie/service/mcp/test_client.py`, find `test_list_tools_http_error` at approximately line 204. Change line 225:

```python
# before
            with pytest.raises(ValueError, match="Test error message"):

# after
            with pytest.raises(MCPBridgeError, match="Test error message"):
```

Also add `MCPBridgeError` to the imports from `codemie.service.mcp.models` at approximately line 32. The existing import is:
```python
from codemie.service.mcp.models import (
    MCPServerConfig,
    MCPToolDefinition,
    MCPToolInvocationResponse,
    MCPExecutionContext,
)
```

Extend it to:
```python
from codemie.service.mcp.models import (
    MCPBridgeError,
    MCPServerConfig,
    MCPToolDefinition,
    MCPToolInvocationResponse,
    MCPExecutionContext,
)
```

- [ ] **Step 4: Implement — update client.py line 184**

In `src/codemie/service/mcp/client.py`, add `MCPBridgeError` to the imports. Find the import at the top of the file (around line 30 area) and update. The file already imports from `codemie.service.mcp.models`; if there is no such import, add one. Check with:

```bash
grep -n "from codemie.service.mcp.models" /Users/isa_kuliev/Work/codemie/src/codemie/service/mcp/client.py
```

If it exists, add `MCPBridgeError` to it. If it doesn't, add:
```python
from codemie.service.mcp.models import MCPBridgeError
```

Then in `list_tools()`, change line 184:
```python
# before
                raise ValueError(_error_message_from_http_status_error(e)) from e

# after
                error_text = _error_message_from_http_status_error(e)
                raise MCPBridgeError(error_text, e.response.status_code) from e
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/isa_kuliev/Work/codemie
poetry run pytest tests/codemie/service/mcp/test_client.py::TestMCPConnectClientListTools::test_list_tools_http_error tests/codemie/service/mcp/test_client.py::TestMCPBridgeError -v 2>&1 | tail -15
```

Expected: all PASS

- [ ] **Step 6: Run the full client test suite to check for regressions**

```bash
cd /Users/isa_kuliev/Work/codemie
poetry run pytest tests/codemie/service/mcp/test_client.py -v 2>&1 | tail -20
```

Expected: all PASS (auth-challenge and BrokerAuth paths are unchanged)

- [ ] **Step 7: Commit**

```bash
git add src/codemie/service/mcp/client.py tests/codemie/service/mcp/test_client.py
git commit -m "feat(EPMCDME-13900): raise MCPBridgeError instead of ValueError in list_tools"
```

---

### Task 3: Update _sanitize_exception_for_log() in toolkit_service.py

**Test-first: yes — `TestSanitizeExceptionForLog.test_mcp_bridge_error_preserves_message_and_status` fails because the sanitizer returns `"MCPBridgeError"` without the message text**

**Files:**
- Modify: `src/codemie/service/mcp/toolkit_service.py` (lines 713–721)

**Interfaces:**
- Consumes: `MCPBridgeError` from `codemie.service.mcp.models` (Task 1)
- Produces: `_sanitize_exception_for_log()` returns full `"MCPBridgeError: <message> (status_code=<n>)"` string for `MCPBridgeError` instances

- [ ] **Step 1: Write the test inline**

There is no dedicated test file for `_sanitize_exception_for_log`. Add a new class to `tests/codemie/service/mcp/test_toolkit_service_init.py` (which already tests toolkit_service internals):

```python
class TestSanitizeExceptionForLog:
    """Verify _sanitize_exception_for_log handles MCPBridgeError without stripping the message."""

    def test_mcp_bridge_error_preserves_message_and_status(self):
        from codemie.service.mcp.models import MCPBridgeError
        from codemie.service.mcp.toolkit_service import MCPToolkitService
        err = MCPBridgeError("Failed to create MCP client: McpError: Connection closed", status_code=500)
        result = MCPToolkitService._sanitize_exception_for_log(err)
        assert "Connection closed" in result
        assert "500" in result

    def test_plain_value_error_is_stripped_to_type_name(self):
        from codemie.service.mcp.toolkit_service import MCPToolkitService
        err = ValueError("sensitive data here")
        result = MCPToolkitService._sanitize_exception_for_log(err)
        assert result == "ValueError"
        assert "sensitive" not in result
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/isa_kuliev/Work/codemie
poetry run pytest tests/codemie/service/mcp/test_toolkit_service_init.py::TestSanitizeExceptionForLog -v 2>&1 | tail -15
```

Expected: `test_mcp_bridge_error_preserves_message_and_status` FAILS (sanitizer currently returns just `"MCPBridgeError"`)

- [ ] **Step 3: Add MCPBridgeError to toolkit_service.py imports**

In `src/codemie/service/mcp/toolkit_service.py`, find the existing import at line 56:

```python
from codemie.service.mcp.models import MCPServerConfig, MCPToolLoadException, MCPExecutionContext
```

Add `MCPBridgeError`:
```python
from codemie.service.mcp.models import MCPBridgeError, MCPServerConfig, MCPToolLoadException, MCPExecutionContext
```

- [ ] **Step 4: Add MCPBridgeError handler to _sanitize_exception_for_log()**

In `src/codemie/service/mcp/toolkit_service.py`, find `_sanitize_exception_for_log` at approximately line 713:

```python
    @classmethod
    def _sanitize_exception_for_log(cls, exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            return (
                f"{exc}: status_code={exc.response.status_code}, "
                f"url={cls._sanitize_url_for_log(str(exc.request.url))}"
            )
        if isinstance(exc, httpx.RequestError):
            return f"{exc}: url={cls._sanitize_url_for_log(str(exc.request.url))}"
        return type(exc).__name__
```

Replace with:

```python
    @classmethod
    def _sanitize_exception_for_log(cls, exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            return (
                f"{exc}: status_code={exc.response.status_code}, "
                f"url={cls._sanitize_url_for_log(str(exc.request.url))}"
            )
        if isinstance(exc, httpx.RequestError):
            return f"{exc}: url={cls._sanitize_url_for_log(str(exc.request.url))}"
        if isinstance(exc, MCPBridgeError):
            return f"MCPBridgeError: {exc} (status_code={exc.status_code})"
        return type(exc).__name__
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/isa_kuliev/Work/codemie
poetry run pytest tests/codemie/service/mcp/test_toolkit_service_init.py::TestSanitizeExceptionForLog -v 2>&1 | tail -10
```

Expected: both PASS

- [ ] **Step 6: Run toolkit service tests to check for regressions**

```bash
cd /Users/isa_kuliev/Work/codemie
poetry run pytest tests/codemie/service/mcp/test_toolkit_service_init.py tests/codemie/service/mcp/test_toolkit_service_integration.py -v 2>&1 | tail -20
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/codemie/service/mcp/toolkit_service.py tests/codemie/service/mcp/test_toolkit_service_init.py
git commit -m "feat(EPMCDME-13900): handle MCPBridgeError in _sanitize_exception_for_log"
```

---

### Task 4: Category-specific hints in mcp_tester.py

**Test-first: yes — `TestMCPServerTesterHints.test_mcp_tool_load_exception_uses_category_hint` fails because `MCPServerTester.test()` has no `MCPToolLoadException` handler and returns the generic message**

**Files:**
- Modify: `src/codemie/service/mcp/mcp_tester.py`
- Create: `tests/codemie/service/mcp/test_mcp_tester.py`

**Interfaces:**
- Consumes: `MCPErrorCategory` and `MCPToolLoadException` from `codemie.service.mcp.models` (Task 1); `MCPBridgeError` (Task 1, via MCPToolLoadException.category auto-set)
- Produces:
  - `_get_server_command(mcp_server: MCPServerDetails) -> str | None` — module-level helper
  - `_build_hint_for_category(category: MCPErrorCategory, mcp_server: MCPServerDetails) -> str` — module-level helper
  - `MCPServerTester.test()` separates `MCPToolLoadException` path from generic `Exception` path

- [ ] **Step 1: Create the test file**

Create `tests/codemie/service/mcp/test_mcp_tester.py`:

```python
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import MCPAuthenticationRequiredException
from codemie.service.mcp.models import MCPBridgeError, MCPErrorCategory, MCPToolLoadException
from codemie.service.mcp.mcp_tester import MCPServerTester, _build_hint_for_category, _get_server_command
from codemie.service.security.token_providers.base_provider import BrokerAuthRequiredException


def _make_mcp_server(command=None, config_command=None):
    """Build a minimal MCPServerDetails-like mock."""
    server = MagicMock()
    server.name = "test-server"
    server.command = command
    if config_command is not None:
        server.config = MagicMock()
        server.config.command = config_command
    else:
        server.config = None
    return server


def _make_tester_for_mcp_tool_load_exception(category: MCPErrorCategory, command=None):
    """Return an MCPServerTester whose get_mcp_server_tools raises an MCPToolLoadException
    with the specified category. Uses the real MCPToolLoadException constructor."""
    server = _make_mcp_server(command=command)
    request = MagicMock()
    request.mcp_server = server
    user = MagicMock()
    user.id = "user-1"

    original = MCPBridgeError("simulated bridge error", status_code=500)
    exc = MCPToolLoadException("test-server", original)
    exc.category = category  # override category for precise control in tests

    tester = MCPServerTester(request, user)
    return tester, exc


class TestGetServerCommand:
    def test_returns_top_level_command(self):
        server = _make_mcp_server(command="npx")
        assert _get_server_command(server) == "npx"

    def test_falls_back_to_config_command(self):
        server = _make_mcp_server(command=None, config_command="uvx")
        assert _get_server_command(server) == "uvx"

    def test_returns_none_when_both_absent(self):
        server = _make_mcp_server(command=None, config_command=None)
        assert _get_server_command(server) is None


class TestBuildHintForCategory:
    def test_connection_closed_with_npx_mentions_stdout(self):
        server = _make_mcp_server(command="npx")
        hint = _build_hint_for_category(MCPErrorCategory.CONNECTION_CLOSED, server)
        assert "stdout" in hint.lower()

    def test_connection_closed_with_npx_mentions_mcp_connect_init_timeout(self):
        server = _make_mcp_server(command="npx")
        hint = _build_hint_for_category(MCPErrorCategory.CONNECTION_CLOSED, server)
        assert "MCP_CONNECT_INIT_TIMEOUT" in hint

    def test_connection_closed_without_npx_is_generic(self):
        server = _make_mcp_server(command="uvx")
        hint = _build_hint_for_category(MCPErrorCategory.CONNECTION_CLOSED, server)
        assert "stdout" not in hint.lower()
        assert "MCP_CONNECT_INIT_TIMEOUT" not in hint

    def test_timeout_mentions_mcp_connect_init_timeout(self):
        server = _make_mcp_server(command="npx")
        hint = _build_hint_for_category(MCPErrorCategory.TIMEOUT, server)
        assert "MCP_CONNECT_INIT_TIMEOUT" in hint

    def test_startup_failure_mentions_env_vars_or_command(self):
        server = _make_mcp_server(command="npx")
        hint = _build_hint_for_category(MCPErrorCategory.STARTUP_FAILURE, server)
        # Hint should mention either environment variables or command correctness
        assert "env" in hint.lower() or "command" in hint.lower() or "variable" in hint.lower()

    def test_auth_required_mentions_authentication(self):
        server = _make_mcp_server(command="npx")
        hint = _build_hint_for_category(MCPErrorCategory.AUTH_REQUIRED, server)
        assert "auth" in hint.lower() or "token" in hint.lower() or "credential" in hint.lower()

    def test_unknown_returns_generic_fallback(self):
        server = _make_mcp_server()
        hint = _build_hint_for_category(MCPErrorCategory.UNKNOWN, server)
        assert "Please, check the configuration." in hint


class TestMCPServerTesterHints:
    def test_mcp_tool_load_exception_uses_category_hint(self):
        tester, exc = _make_tester_for_mcp_tool_load_exception(MCPErrorCategory.TIMEOUT, command="npx")
        with patch.object(tester, "_get_tools", side_effect=exc, create=True):
            with patch("codemie.service.mcp.mcp_tester.MCPToolkitService.get_mcp_server_tools", side_effect=exc):
                ok, msg = tester.test()
        assert ok is False
        assert "MCP_CONNECT_INIT_TIMEOUT" in msg

    def test_non_mcp_tool_load_exception_returns_generic_message(self):
        server = _make_mcp_server(command="npx")
        request = MagicMock()
        request.mcp_server = server
        user = MagicMock()
        user.id = "user-1"
        tester = MCPServerTester(request, user)

        with patch("codemie.service.mcp.mcp_tester.MCPToolkitService.get_mcp_server_tools",
                   side_effect=RuntimeError("unexpected")):
            ok, msg = tester.test()
        assert ok is False
        assert "Please, check the configuration." in msg

    def test_broker_auth_required_is_reraised(self):
        server = _make_mcp_server()
        request = MagicMock()
        request.mcp_server = server
        user = MagicMock()
        user.id = "user-1"
        tester = MCPServerTester(request, user)

        with patch("codemie.service.mcp.mcp_tester.MCPToolkitService.get_mcp_server_tools",
                   side_effect=BrokerAuthRequiredException(message="auth required", auth_location="http://example.com")):
            with pytest.raises(BrokerAuthRequiredException):
                tester.test()

    def test_mcp_authentication_required_is_reraised(self):
        server = _make_mcp_server()
        request = MagicMock()
        request.mcp_server = server
        user = MagicMock()
        user.id = "user-1"
        tester = MCPServerTester(request, user)

        with patch("codemie.service.mcp.mcp_tester.MCPToolkitService.get_mcp_server_tools",
                   side_effect=MCPAuthenticationRequiredException({})):
            with pytest.raises(MCPAuthenticationRequiredException):
                tester.test()


class TestMCPServerTesterNpxPath:
    """AC5 regression: exercises MCPServerDetails with command='npx' through the full hint path."""

    def _make_npx_tester(self):
        server = _make_mcp_server(command="npx")
        request = MagicMock()
        request.mcp_server = server
        user = MagicMock()
        user.id = "user-1"
        return MCPServerTester(request, user)

    def test_bridge_500_connection_closed_npx_returns_npx_hint(self):
        tester = self._make_npx_tester()
        original = MCPBridgeError("Failed to create MCP client: McpError: Connection closed", status_code=500)
        exc = MCPToolLoadException("test-server", original)

        with patch("codemie.service.mcp.mcp_tester.MCPToolkitService.get_mcp_server_tools", side_effect=exc):
            ok, msg = tester.test()

        assert ok is False
        assert exc.category == MCPErrorCategory.CONNECTION_CLOSED
        assert "stdout" in msg.lower()

    def test_bridge_504_connection_closed_body_npx_returns_timeout_hint(self):
        """Primary npx failure mode: bridge 504 with 'connection closed' body must classify
        as TIMEOUT (status-code-first rule) and return MCP_CONNECT_INIT_TIMEOUT hint."""
        tester = self._make_npx_tester()
        original = MCPBridgeError("McpError: Connection closed", status_code=504)
        exc = MCPToolLoadException("test-server", original)

        with patch("codemie.service.mcp.mcp_tester.MCPToolkitService.get_mcp_server_tools", side_effect=exc):
            ok, msg = tester.test()

        assert ok is False
        assert exc.category == MCPErrorCategory.TIMEOUT
        assert "MCP_CONNECT_INIT_TIMEOUT" in msg

    def test_bridge_500_enoent_npx_returns_startup_failure_hint(self):
        tester = self._make_npx_tester()
        original = MCPBridgeError("ENOENT: no such file or directory, open '/tmp/node_modules/some-mcp'", status_code=500)
        exc = MCPToolLoadException("test-server", original)

        with patch("codemie.service.mcp.mcp_tester.MCPToolkitService.get_mcp_server_tools", side_effect=exc):
            ok, msg = tester.test()

        assert ok is False
        assert exc.category == MCPErrorCategory.STARTUP_FAILURE
        # startup failure hint must mention command or env vars
        assert "env" in msg.lower() or "command" in msg.lower() or "variable" in msg.lower()
```

- [ ] **Step 2: Run tests to see them fail**

```bash
cd /Users/isa_kuliev/Work/codemie
poetry run pytest tests/codemie/service/mcp/test_mcp_tester.py -v 2>&1 | tail -20
```

Expected: ImportError or AttributeError since `_build_hint_for_category` and `_get_server_command` don't exist yet

- [ ] **Step 3: Implement mcp_tester.py**

Replace the full content of `src/codemie/service/mcp/mcp_tester.py` with:

```python
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from typing import Tuple

from codemie.configs import logger
from codemie.core.exceptions import MCPAuthenticationRequiredException
from codemie.rest_api.models.assistant import MCPServerDetails, MCPServerCheckRequest
from codemie.rest_api.security.user import User
from codemie.service.mcp.models import MCPErrorCategory, MCPToolLoadException
from codemie.service.mcp.toolkit_service import MCPToolkitService
from codemie.service.security.token_providers.base_provider import BrokerAuthRequiredException


def _get_server_command(mcp_server: MCPServerDetails) -> str | None:
    """Return the MCP server's launch command, checking top-level field before config."""
    if mcp_server.command:
        return mcp_server.command
    if mcp_server.config and mcp_server.config.command:
        return mcp_server.config.command
    return None


def _build_hint_for_category(category: MCPErrorCategory, mcp_server: MCPServerDetails) -> str:
    """Return a category-specific diagnostic hint for the user."""
    command = _get_server_command(mcp_server)
    is_npx = command == "npx"

    if category == MCPErrorCategory.CONNECTION_CLOSED:
        if is_npx:
            return (
                "The npx server closed the MCP connection. Common causes:\n"
                "1. npm may write progress or error output to stdout — "
                "MCP requires clean stdout (only JSON-RPC messages). "
                "Run 'npx <package> --version' locally to check for unexpected output.\n"
                "2. The package may not exist or may require a specific environment. "
                "Confirm the package name and that all required environment variables are set.\n"
                "3. If this server downloads packages on first use, the MCP bridge's "
                "MCP_CONNECT_INIT_TIMEOUT (default 30s) may expire before initialization completes."
            )
        return "Check that the server process starts correctly and produces only valid MCP JSON-RPC output."

    if category == MCPErrorCategory.TIMEOUT:
        return (
            "The MCP bridge timed out waiting for server initialization. "
            "If this server downloads packages on first use, "
            "increase MCP_CONNECT_INIT_TIMEOUT on the bridge service."
        )

    if category == MCPErrorCategory.STARTUP_FAILURE:
        return (
            "The server process failed to start. "
            "Verify the command path, that all required environment variables are set "
            "in the MCP server configuration, and that the package or binary is accessible."
        )

    if category == MCPErrorCategory.AUTH_REQUIRED:
        return (
            "Authentication is required. "
            "Check that authentication tokens or credentials are present "
            "in the MCP server environment configuration."
        )

    return "Please, check the configuration."


class MCPServerTester:
    mcp_server: MCPServerDetails

    def __init__(self, request: MCPServerCheckRequest, user: User):
        self.mcp_server = request.mcp_server
        self.user = user

    def test(self) -> Tuple[bool, str]:
        try:
            tools = MCPToolkitService.get_mcp_server_tools(
                mcp_servers=[self.mcp_server], user_id=self.user.id, mcp_server_single_usage=True
            )

            logger.info(f"Testing passed for MCP tools from {self.mcp_server.name} server. Tools count={len(tools)}")
            return True, 'Success'
        except BrokerAuthRequiredException:
            raise
        except MCPAuthenticationRequiredException:
            raise
        except MCPToolLoadException as e:
            hint = _build_hint_for_category(e.category, self.mcp_server)
            return False, f"{str(e)}.\n{hint}"
        except Exception as e:
            return False, f"{str(e)}.\nPlease, check the configuration."
```

- [ ] **Step 4: Run all mcp_tester tests**

```bash
cd /Users/isa_kuliev/Work/codemie
poetry run pytest tests/codemie/service/mcp/test_mcp_tester.py -v 2>&1 | tail -25
```

Expected: all PASS

- [ ] **Step 5: Run the full MCP test suite to confirm no regressions**

```bash
cd /Users/isa_kuliev/Work/codemie
poetry run pytest tests/codemie/service/mcp/ -v 2>&1 | tail -30
```

Expected: all PASS

- [ ] **Step 6: Run ruff**

```bash
cd /Users/isa_kuliev/Work/codemie
make ruff
```

Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add src/codemie/service/mcp/mcp_tester.py tests/codemie/service/mcp/test_mcp_tester.py
git commit -m "feat(EPMCDME-13900): add category-specific diagnostic hints in MCPServerTester.test()"
```
