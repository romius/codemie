# Classify Chrome Extension LiteLLM Spend Separately from CLI Traffic

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix `_is_cli_request` in the LiteLLM proxy router and monitoring service to classify Chrome extension traffic as `BudgetCategory.PLATFORM` (not `BudgetCategory.CLI`) by keying on `client_type` instead of the presence of the `X-CodeMie-CLI` header.

**Architecture:** Two independent `_is_cli_request` predicates must be aligned — one in `proxy_router.py` (governs budget category selection) and one in `LLMProxyMonitoringService` (governs the `cli_request` Elasticsearch field). Both currently return `True` for any non-empty `X-CodeMie-CLI` header, which misclassifies Chrome extension requests (`client_type=codemie-chrome-extension`) as CLI traffic. The fix keys on `client_type` only (`codemie-cli` / `codemie_cli`, case-insensitive) and leaves no other caller needing changes.

**Tech Stack:** Python 3.12, pytest, `codemie.core.constants.CLIENT_TYPE`, `codemie.enterprise.litellm.budget_categories.BudgetCategory`

## Global Constraints

- `client_type` matching must be case-insensitive (AC#7)
- No new `BudgetCategory` enum value — use existing `BudgetCategory.PLATFORM` for Chrome extension traffic (AC#3)
- `CODEMIE_CLI` import must stay in `proxy_router.py` (still used for header extraction at line 299) and in the monitoring service (still used by `_extract_request_info`)
- Commit message format: `EPMCDME-14260: <description>`

---

## File Structure

| File | Change |
|------|--------|
| `src/codemie/enterprise/litellm/proxy_router.py` | Modify `_is_cli_request` (lines 515–519) |
| `src/codemie/service/monitoring/llm_proxy_monitoring_service.py` | Modify `_is_cli_request` (lines 432–435) |
| `tests/enterprise/litellm/test_proxy_router.py` | Add `TestIsCliRequest` class; update `test_cli_header_request_uses_cli_budget` |
| `tests/codemie/service/monitoring/test_llm_proxy_monitoring_service.py` | Rewrite `TestIsCliRequest`; update `cli_request_info` fixture and `test_cli_request_sets_cli_flag_true` |

---

### Task 1: Fix `proxy_router._is_cli_request` and align its tests

**Test-first: yes — failing tests: `pytest tests/enterprise/litellm/test_proxy_router.py::TestIsCliRequest -v`**

**Files:**
- Modify: `tests/enterprise/litellm/test_proxy_router.py`
- Modify: `src/codemie/enterprise/litellm/proxy_router.py:515-519`

**Interfaces:**
- Produces: `_is_cli_request(request_info: dict) -> bool` — `True` iff `request_info["client_type"].lower() in {"codemie-cli", "codemie_cli"}`

- [ ] **Step 1: Add `_is_cli_request` to the import block in the test file**

Open `tests/enterprise/litellm/test_proxy_router.py`. The import block starting at line 43 currently imports from `codemie.enterprise.litellm.proxy_router`. Add `_is_cli_request` to that import:

```python
from codemie.enterprise.litellm.proxy_router import (
    LITELLM_CUSTOMER_ID_HEADER,
    _build_premium_budget_error_body,
    _check_cli_version,
    _extract_model,
    _extract_request_info,
    _get_integration_api_key,
    _handle_error_response,
    _is_cli_request,          # ← add this
    _prepare_proxy_headers,
    _proxy_to_llm_proxy,
    _resolve_non_premium_tracking_identity,
    _resolve_tracking_identity,
    _read_request_body,
    _resolve_project_budget_runtime,
    register_proxy_endpoints,
)
```

Also add `CLIENT_TYPE` to the `codemie.core.constants` import block at the top of the test file:

```python
from codemie.core.constants import (
    CLIENT_TYPE,          # ← add this
    CODEMIE_CLI,
    LLM_MODEL,
    PROJECT,
    HEADER_CODEMIE_CLI,
    HEADER_CODEMIE_CLI_BRANCH,
    HEADER_CODEMIE_CLI_MODEL,
    HEADER_CODEMIE_CLI_REPOSITORY,
    HEADER_CODEMIE_CLIENT,
    HEADER_CODEMIE_CLI_PROJECT,
    HEADER_CODEMIE_INTEGRATION,
    HEADER_CODEMIE_REQUEST_ID,
    HEADER_CODEMIE_SESSION_ID,
)
```

- [ ] **Step 2: Add `TestIsCliRequest` class to `test_proxy_router.py`**

Insert a new class after the existing `TestExtractRequestInfo` class (around line 283, before `class TestResolveProjectBudgetRuntime`):

```python
class TestIsCliRequest:
    """Tests for proxy_router._is_cli_request predicate."""

    def test_codemie_cli_client_type_returns_true(self):
        assert _is_cli_request({CLIENT_TYPE: "codemie-cli"}) is True

    def test_codemie_cli_underscore_client_type_returns_true(self):
        assert _is_cli_request({CLIENT_TYPE: "codemie_cli"}) is True

    def test_cli_client_type_case_insensitive_upper(self):
        assert _is_cli_request({CLIENT_TYPE: "CODEMIE-CLI"}) is True

    def test_cli_client_type_case_insensitive_mixed(self):
        assert _is_cli_request({CLIENT_TYPE: "Codemie-Cli"}) is True

    def test_chrome_extension_client_type_returns_false(self):
        assert _is_cli_request({CLIENT_TYPE: "codemie-chrome-extension"}) is False

    def test_non_empty_cli_header_alone_does_not_classify_as_cli(self):
        # Non-empty X-CodeMie-CLI header with non-cli client_type must NOT be CLI
        assert _is_cli_request({CODEMIE_CLI: "codemie-chrome-extension/1.0", CLIENT_TYPE: "codemie-chrome-extension"}) is False

    def test_unrecognized_client_type_with_nonempty_cli_header_returns_false(self):
        assert _is_cli_request({CODEMIE_CLI: "some-tool/1.0", CLIENT_TYPE: "some-other-tool"}) is False

    def test_missing_client_type_returns_false(self):
        assert _is_cli_request({}) is False

    def test_none_client_type_returns_false(self):
        assert _is_cli_request({CLIENT_TYPE: None}) is False

    def test_empty_client_type_returns_false(self):
        assert _is_cli_request({CLIENT_TYPE: ""}) is False
```

- [ ] **Step 3: Update `test_cli_header_request_uses_cli_budget` to reflect correct post-fix behaviour**

The existing test at line ~455 uses `client_type: "web"` + `CODEMIE_CLI: "codemie-cli/1.2.3"`. After the fix, this is NOT a CLI request (client_type wins). Update the test to assert the correct (PLATFORM) outcome and rename it:

```python
def test_cli_header_with_non_cli_client_type_uses_platform_budget(self):
    """Header alone no longer classifies as CLI; client_type governs."""
    user = MagicMock()
    user.username = "user@example.com"
    request_info = {
        LLM_MODEL: "gpt-4.1-mini",
        CLIENT_TYPE: "web",
        CODEMIE_CLI: "codemie-cli/1.2.3",
    }
    category_budget_ids = {
        BudgetCategory.PLATFORM.value: "platform-budget",
        BudgetCategory.CLI.value: "cli-budget",
    }

    with patch(
        "codemie.enterprise.litellm.proxy_router.get_category_budget_id",
        return_value="platform-budget",
    ):
        category, username, budget_id, llm_model = _resolve_non_premium_tracking_identity(
            user=user,
            request_info=request_info,
            category_budget_ids=category_budget_ids,
            llm_model="gpt-4.1-mini",
        )

    assert category == BudgetCategory.PLATFORM
    assert username == "user@example.com"
    assert budget_id == "platform-budget"
    assert llm_model == "gpt-4.1-mini"
```

Also add a test for Chrome extension specifically using PLATFORM budget:

```python
def test_chrome_extension_client_type_uses_platform_budget(self):
    user = MagicMock()
    user.username = "user@example.com"
    request_info = {
        LLM_MODEL: "gpt-4.1-mini",
        CLIENT_TYPE: "codemie-chrome-extension",
        CODEMIE_CLI: "codemie-chrome-extension/1.0",
    }
    category_budget_ids = {
        BudgetCategory.PLATFORM.value: "platform-budget",
        BudgetCategory.CLI.value: "cli-budget",
    }

    with patch(
        "codemie.enterprise.litellm.proxy_router.get_category_budget_id",
        return_value="platform-budget",
    ):
        category, username, budget_id, llm_model = _resolve_non_premium_tracking_identity(
            user=user,
            request_info=request_info,
            category_budget_ids=category_budget_ids,
            llm_model="gpt-4.1-mini",
        )

    assert category == BudgetCategory.PLATFORM
    assert username == "user@example.com"
    assert budget_id == "platform-budget"
```

- [ ] **Step 4: Run the new and updated tests — expect failures**

```bash
pytest tests/enterprise/litellm/test_proxy_router.py::TestIsCliRequest tests/enterprise/litellm/test_proxy_router.py::TestResolveNonPremiumTrackingIdentity::test_cli_header_with_non_cli_client_type_uses_platform_budget tests/enterprise/litellm/test_proxy_router.py::TestResolveNonPremiumTrackingIdentity::test_chrome_extension_client_type_uses_platform_budget -v
```

Expected: failures. `TestIsCliRequest` tests fail because `_is_cli_request` still checks the header. The `_resolve_non_premium` tests fail because the header-based path still routes to CLI.

- [ ] **Step 5: Implement the fix in `proxy_router.py`**

In `src/codemie/enterprise/litellm/proxy_router.py`, replace lines 515–519:

```python
# Before:
def _is_cli_request(request_info: dict) -> bool:
    return bool(request_info.get(CODEMIE_CLI)) or request_info.get(CLIENT_TYPE) in {
        "codemie-cli",
        "codemie_cli",
    }

# After:
def _is_cli_request(request_info: dict) -> bool:
    client_type = (request_info.get(CLIENT_TYPE) or "").lower()
    return client_type in {"codemie-cli", "codemie_cli"}
```

Do not remove the `CODEMIE_CLI` import — it is still used at line 299 inside `_extract_request_info`.

- [ ] **Step 6: Run the full proxy_router test suite — expect all pass**

```bash
pytest tests/enterprise/litellm/test_proxy_router.py -v
```

Expected: all tests pass, including the previously failing `TestIsCliRequest` suite and updated `TestResolveNonPremiumTrackingIdentity` tests.

- [ ] **Step 7: Commit**

```bash
git add src/codemie/enterprise/litellm/proxy_router.py \
        tests/enterprise/litellm/test_proxy_router.py
git commit -m "EPMCDME-14260: Fix _is_cli_request in proxy_router to key on client_type"
```

---

### Task 2: Fix `LLMProxyMonitoringService._is_cli_request` and align its tests

**Test-first: yes — failing tests: `pytest tests/codemie/service/monitoring/test_llm_proxy_monitoring_service.py::TestIsCliRequest -v`**

**Files:**
- Modify: `tests/codemie/service/monitoring/test_llm_proxy_monitoring_service.py`
- Modify: `src/codemie/service/monitoring/llm_proxy_monitoring_service.py:432-435`

**Interfaces:**
- Consumes: `CLIENT_TYPE` constant from `codemie.core.constants` (already imported in the service at line 26)
- Produces: `LLMProxyMonitoringService._is_cli_request(request_info: dict) -> bool` — same semantics as Task 1: `True` iff `client_type.lower() in {"codemie-cli", "codemie_cli"}`

- [ ] **Step 1: Add `CLIENT_TYPE` to the test file import and rewrite `TestIsCliRequest`**

In `tests/codemie/service/monitoring/test_llm_proxy_monitoring_service.py`, add `CLIENT_TYPE` to the constants import:

```python
from codemie.core.constants import CLIENT_TYPE, CODEMIE_CLI
```

Replace the entire `TestIsCliRequest` class (lines 62–75) with:

```python
class TestIsCliRequest:
    """Tests for LLMProxyMonitoringService._is_cli_request predicate."""

    def test_codemie_cli_client_type_returns_true(self):
        assert LLMProxyMonitoringService._is_cli_request({CLIENT_TYPE: "codemie-cli"}) is True

    def test_codemie_cli_underscore_client_type_returns_true(self):
        assert LLMProxyMonitoringService._is_cli_request({CLIENT_TYPE: "codemie_cli"}) is True

    def test_cli_client_type_case_insensitive(self):
        assert LLMProxyMonitoringService._is_cli_request({CLIENT_TYPE: "CODEMIE-CLI"}) is True

    def test_chrome_extension_client_type_returns_false(self):
        assert LLMProxyMonitoringService._is_cli_request({CLIENT_TYPE: "codemie-chrome-extension"}) is False

    def test_nonempty_cli_header_with_non_cli_client_type_returns_false(self):
        assert LLMProxyMonitoringService._is_cli_request(
            {CODEMIE_CLI: "codemie-chrome-extension/1.0", CLIENT_TYPE: "codemie-chrome-extension"}
        ) is False

    def test_unrecognized_client_type_with_nonempty_cli_header_returns_false(self):
        assert LLMProxyMonitoringService._is_cli_request(
            {CODEMIE_CLI: "some-tool/1.0", CLIENT_TYPE: "some-other-tool"}
        ) is False

    def test_missing_client_type_returns_false(self):
        assert LLMProxyMonitoringService._is_cli_request({}) is False

    def test_none_client_type_returns_false(self):
        assert LLMProxyMonitoringService._is_cli_request({CLIENT_TYPE: None}) is False

    def test_empty_client_type_returns_false(self):
        assert LLMProxyMonitoringService._is_cli_request({CLIENT_TYPE: ""}) is False
```

- [ ] **Step 2: Update the `cli_request_info` fixture and `test_cli_request_sets_cli_flag_true`**

The `cli_request_info` fixture currently uses `client_type: "codemie-claude"` which is NOT a CLI client type. Update it to use a genuine CLI client:

```python
@pytest.fixture
def cli_request_info():
    return {
        CLIENT_TYPE: "codemie-cli",
        "session_id": "session-123",
        "request_id": "request-456",
        "llm_model": "claude-sonnet-4-5",
        "user_agent": "codemie-code/1.2.0",
        CODEMIE_CLI: "codemie-cli/1.2.0",
    }
```

The existing `test_cli_request_sets_cli_flag_true` test at line ~142 should still pass after the fixture update — no body change needed. But verify it explicitly expects `cli_request is True`.

- [ ] **Step 3: Run the updated tests — expect failures**

```bash
pytest tests/codemie/service/monitoring/test_llm_proxy_monitoring_service.py::TestIsCliRequest -v
```

Expected: failures. The old `_is_cli_request` still returns `True` for any non-empty `CODEMIE_CLI` header.

- [ ] **Step 4: Implement the fix in `llm_proxy_monitoring_service.py`**

In `src/codemie/service/monitoring/llm_proxy_monitoring_service.py`, replace lines 432–435:

```python
# Before:
@staticmethod
def _is_cli_request(request_info: dict) -> bool:
    """Return True if the request originated from the codemie-code CLI."""
    return bool(request_info.get(CODEMIE_CLI))

# After:
@staticmethod
def _is_cli_request(request_info: dict) -> bool:
    """Return True if the request originated from the codemie-cli client."""
    client_type = (request_info.get(CLIENT_TYPE) or "").lower()
    return client_type in {"codemie-cli", "codemie_cli"}
```

`CLIENT_TYPE` is already imported at line 26 of the monitoring service. Do not remove `CODEMIE_CLI` from the import — it is used in `_extract_request_info` and `track_usage`.

- [ ] **Step 5: Run the full monitoring service test suite — expect all pass**

```bash
pytest tests/codemie/service/monitoring/test_llm_proxy_monitoring_service.py -v
```

Expected: all tests pass.

- [ ] **Step 6: Run the full test suite for both changed files**

```bash
pytest tests/enterprise/litellm/test_proxy_router.py tests/codemie/service/monitoring/test_llm_proxy_monitoring_service.py -v
```

Expected: green.

- [ ] **Step 7: Commit**

```bash
git add src/codemie/service/monitoring/llm_proxy_monitoring_service.py \
        tests/codemie/service/monitoring/test_llm_proxy_monitoring_service.py
git commit -m "EPMCDME-14260: Fix LLMProxyMonitoringService._is_cli_request to key on client_type"
```
