# EPMCDME-11254: Shopify MCP Tools Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two MCP bugs: (1) connection test silently succeeds with zero tools; (2) tool-listing uses a stale process cache instead of a fresh process.

**Architecture:** Two one-line surgical changes in the MCP surface layer, each preceded by a failing test. No new abstractions, no schema changes, no router changes.

**Tech Stack:** Python 3.11+, pytest, unittest.mock, Poetry

## Global Constraints

- Branch: `EPMCDME-11254_shopify-mcp-tools-fix`
- Commit format: `EPMCDME-11254: <description>`
- All new `.py` files must carry the Apache 2.0 license header (see existing test files for template)
- Lint: `make ruff` must pass before committing
- Tests: `make test` must pass before committing
- No changes to workflow callers (`config_resources_validation.py`, `resources.py`) — out of scope

---

### Task 1: Fix `MCPToolsInfoService` — pass `mcp_server_single_usage=True`

**Test-first: yes — `test_get_mcp_server_tools_called_with_single_usage_true` fails because `mcp_server_single_usage` kwarg is absent in the production call**

**Files:**
- Modify: `src/codemie/service/tools/mcp_tools_info_service.py:58-62`
- Test: `tests/codemie/service/tools/test_mcp_tools_info_service.py` (extend existing file)

**Interfaces:**
- Consumes: `MCPToolkitService.get_mcp_server_tools(mcp_servers, user_id, project_name, mcp_server_single_usage)` — all kwargs
- Produces: no interface change; observable only through call-args assertions in tests

- [ ] **Step 1: Write the three failing tests**

Append a new test class to `tests/codemie/service/tools/test_mcp_tools_info_service.py`:

```python
class TestMCPToolsInfoServiceSingleUsageMode:
    """Verify get_mcp_toolkit_info uses mcp_server_single_usage=True."""

    def test_get_mcp_server_tools_called_with_single_usage_true(self, mock_user, mcp_server_config):
        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_tool.description = "A test tool"

        with patch(
            "codemie.service.mcp.toolkit_service.MCPToolkitService.get_mcp_server_tools",
            return_value=[mock_tool],
        ) as mock_get_tools:
            MCPToolsInfoService.get_mcp_toolkit_info(
                mcp_server_config=mcp_server_config,
                user=mock_user,
            )

        assert mock_get_tools.call_args.kwargs.get("mcp_server_single_usage") is True

    def test_success_path_returns_toolkit_dict(self, mock_user, mcp_server_config):
        mock_tool = Mock()
        mock_tool.name = "test_tool"
        mock_tool.description = "A test tool"

        with patch(
            "codemie.service.mcp.toolkit_service.MCPToolkitService.get_mcp_server_tools",
            return_value=[mock_tool],
        ):
            result = MCPToolsInfoService.get_mcp_toolkit_info(
                mcp_server_config=mcp_server_config,
                user=mock_user,
            )

        assert result["toolkit"] == "MCP"
        assert "tools" in result
        assert len(result["tools"]) == 1
        assert result["tools"][0]["name"] == "test_tool"

    def test_empty_tools_raises_service_error(self, mock_user, mcp_server_config):
        with patch(
            "codemie.service.mcp.toolkit_service.MCPToolkitService.get_mcp_server_tools",
            return_value=[],
        ):
            with pytest.raises(MCPToolsInfoServiceError):
                MCPToolsInfoService.get_mcp_toolkit_info(
                    mcp_server_config=mcp_server_config,
                    user=mock_user,
                )
```

- [ ] **Step 2: Run the new tests to confirm they fail**

```
pytest tests/codemie/service/tools/test_mcp_tools_info_service.py::TestMCPToolsInfoServiceSingleUsageMode -v
```

Expected: `test_get_mcp_server_tools_called_with_single_usage_true` FAILS with `AssertionError` (kwarg absent). The other two may pass already — that is fine; only the kwarg assertion must fail.

- [ ] **Step 3: Add `mcp_server_single_usage=True` to the production call**

In `src/codemie/service/tools/mcp_tools_info_service.py`, change lines 58–62 from:

```python
            tools = MCPToolkitService.get_mcp_server_tools(
                mcp_servers=[mcp_server_config],
                user_id=user.id,
                project_name=project_name,
            )
```

to:

```python
            tools = MCPToolkitService.get_mcp_server_tools(
                mcp_servers=[mcp_server_config],
                user_id=user.id,
                project_name=project_name,
                mcp_server_single_usage=True,
            )
```

- [ ] **Step 4: Run the tests to confirm they pass**

```
pytest tests/codemie/service/tools/test_mcp_tools_info_service.py -v
```

Expected: all tests PASS (3 existing + 3 new = 6 total).

- [ ] **Step 5: Lint**

```
make ruff
```

Expected: exits 0 with no new violations.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/tools/mcp_tools_info_service.py \
        tests/codemie/service/tools/test_mcp_tools_info_service.py
git commit -m "EPMCDME-11254: use mcp_server_single_usage=True in MCPToolsInfoService"
```

---

### Task 2: Fix `MCPServerTester` — add empty-tools guard

**Test-first: yes — `test_returns_false_when_tools_empty` fails because `test()` currently returns `(True, 'Success')` unconditionally**

**Files:**
- Create: `tests/codemie/service/mcp/test_mcp_tester.py`
- Modify: `src/codemie/service/mcp/mcp_tester.py:40-41`

**Interfaces:**
- Consumes: `MCPServerTester(request: MCPServerCheckRequest, user: User).test() -> Tuple[bool, str]`
- Produces: `test()` now returns `(False, "Connected to '<name>' but no tools were retrieved. Please check that the MCP server is running and has tools configured.")` when tools list is empty

- [ ] **Step 1: Create the test file**

Create `tests/codemie/service/mcp/test_mcp_tester.py` with this content:

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

"""Unit tests for MCPServerTester."""

from unittest.mock import Mock, patch

import pytest

from codemie.core.exceptions import MCPAuthenticationRequiredException
from codemie.rest_api.models.assistant import MCPServerCheckRequest, MCPServerDetails
from codemie.rest_api.security.user import User
from codemie.service.mcp.mcp_tester import MCPServerTester
from codemie.service.security.token_providers.base_provider import BrokerAuthRequiredException

_PATCH_TARGET = "codemie.service.mcp.toolkit_service.MCPToolkitService.get_mcp_server_tools"


@pytest.fixture
def mock_user():
    user = Mock(spec=User)
    user.id = "test-user-id"
    return user


@pytest.fixture
def mcp_server():
    return MCPServerDetails(name="Test MCP", enabled=True)


@pytest.fixture
def tester(mcp_server, mock_user):
    request = MCPServerCheckRequest(mcp_server=mcp_server)
    return MCPServerTester(request=request, user=mock_user)


class TestMCPServerTesterTest:
    """Verify MCPServerTester.test() behaviour across all outcomes."""

    def test_returns_false_when_tools_empty(self, tester):
        """Empty tools list must produce a False result with the server name in the message."""
        with patch(_PATCH_TARGET, return_value=[]):
            success, message = tester.test()

        assert success is False
        assert "Test MCP" in message

    def test_returns_true_when_tools_found(self, tester):
        """Non-empty tools list must produce (True, 'Success')."""
        with patch(_PATCH_TARGET, return_value=[Mock()]):
            success, message = tester.test()

        assert success is True
        assert message == "Success"

    def test_returns_false_on_generic_exception(self, tester):
        """Generic exceptions must be caught and returned as (False, msg)."""
        with patch(_PATCH_TARGET, side_effect=RuntimeError("connection refused")):
            success, message = tester.test()

        assert success is False
        assert "connection refused" in message

    def test_reraises_broker_auth_exception(self, tester):
        """BrokerAuthRequiredException must propagate unchanged."""
        exc = BrokerAuthRequiredException(
            message="Broker token exchange failed",
            auth_location="https://auth.example.com/login",
            details="HTTP 401",
        )
        with patch(_PATCH_TARGET, side_effect=exc):
            with pytest.raises(BrokerAuthRequiredException) as exc_info:
                tester.test()

        assert exc_info.value is exc

    def test_reraises_mcp_auth_exception(self, tester):
        """MCPAuthenticationRequiredException must propagate unchanged."""
        exc = MCPAuthenticationRequiredException({"error": "authentication_required", "servers": []})
        with patch(_PATCH_TARGET, side_effect=exc):
            with pytest.raises(MCPAuthenticationRequiredException) as exc_info:
                tester.test()

        assert exc_info.value is exc

    def test_calls_get_mcp_server_tools_with_single_usage_true(self, tester):
        """get_mcp_server_tools must be called with mcp_server_single_usage=True."""
        with patch(_PATCH_TARGET, return_value=[Mock()]) as mock_get_tools:
            tester.test()

        assert mock_get_tools.call_args.kwargs.get("mcp_server_single_usage") is True
```

- [ ] **Step 2: Run the new tests to confirm they fail**

```
pytest tests/codemie/service/mcp/test_mcp_tester.py -v
```

Expected: `test_returns_false_when_tools_empty` FAILS (`assert False is False` on the current `True` return). The other tests may pass already — only this one must fail to confirm the guard is absent.

- [ ] **Step 3: Add the empty-tools guard to `mcp_tester.py`**

In `src/codemie/service/mcp/mcp_tester.py`, change lines 38–41 from:

```python
            logger.info(f"Testing passed for MCP tools from {self.mcp_server.name} server. Tools count={len(tools)}")
            return True, 'Success'
```

to:

```python
            if not tools:
                logger.warning(
                    f"Connection to '{self.mcp_server.name}' succeeded but no tools were retrieved."
                )
                return False, (
                    f"Connected to '{self.mcp_server.name}' but no tools were retrieved. "
                    "Please check that the MCP server is running and has tools configured."
                )

            logger.info(f"Testing passed for MCP tools from {self.mcp_server.name} server. Tools count={len(tools)}")
            return True, 'Success'
```

- [ ] **Step 4: Run all new tests to confirm they pass**

```
pytest tests/codemie/service/mcp/test_mcp_tester.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Run the full test suite**

```
make test
```

Expected: exits 0 with no regressions.

- [ ] **Step 6: Lint**

```
make ruff
```

Expected: exits 0.

- [ ] **Step 7: Commit**

```bash
git add src/codemie/service/mcp/mcp_tester.py \
        tests/codemie/service/mcp/test_mcp_tester.py
git commit -m "EPMCDME-11254: add empty-tools guard to MCPServerTester.test()"
```
