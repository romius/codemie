# Fix Misleading TOKEN LIMIT EXCEEDED Message — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the raw internal error string shown in chat when the model hits max_output_tokens with the clean user-facing message already defined in `config.AGENT_MSG_TOKEN_LIMIT`.

**Architecture:** Add one branch in `extended_error()` (`src/codemie/agents/tools/agent.py`) for `ErrorCode.AGENT_TOKEN_LIMIT` that returns `err.message` instead of falling through to `str(exception)`. Update the existing test file to assert the corrected behaviour and add one new test covering this path.

**Tech Stack:** Python 3.12, pytest, unittest.mock

## Global Constraints

- Do not change `_check_for_truncated_response` in `langgraph_agent.py` — the raw exception message is required for classifier keyword matching and log records.
- Do not touch the real-quota exhaustion path (`LiteLLMErrorClassifier → LITE_LLM_CONTEXT_WINDOW_EXCEEDED_ERROR`).
- Do not touch the `HIDE_AGENT_STREAMING_EXCEPTIONS=True` path — it already works.
- Run tests with: `poetry run pytest tests/codemie/agents/test_langgraph_truncation.py -v`
- Commit message format: `EPMCDME-13459: <description>`

---

### Task 1: Add failing test for `extended_error()` AGENT_TOKEN_LIMIT path

**Test-first: yes** — write a test that calls `extended_error()` with an `AGENT_TOKEN_LIMIT` error code and asserts the return value equals `config.AGENT_MSG_TOKEN_LIMIT`. This test fails until Task 2 adds the branch.

**Files:**
- Modify: `tests/codemie/agents/test_langgraph_truncation.py`

**Interfaces:**
- Consumes: `AbstractAgent.extended_error(error_response, exception) -> str` from `src/codemie/agents/tools/agent.py:40`
- Produces: `TestExtendedError` class with `test_extended_error_returns_clean_message_for_agent_token_limit`

- [ ] **Step 1: Add the new test class at the bottom of the file**

Open `tests/codemie/agents/test_langgraph_truncation.py` and append this class after the last existing class:

```python
class TestExtendedError:
    """Tests for AbstractAgent.extended_error() with AGENT_TOKEN_LIMIT."""

    def test_extended_error_returns_clean_message_for_agent_token_limit(self):
        """AGENT_TOKEN_LIMIT must return the clean config string, not str(exception)."""
        from unittest.mock import Mock
        from codemie.agents.tools.agent import AbstractAgent
        from codemie.core.error_constants import ErrorCategory, ErrorCode
        from codemie.configs import config

        agent = AbstractAgent()

        err_mock = Mock()
        err_mock.error_code = ErrorCode.AGENT_TOKEN_LIMIT
        err_mock.message = config.AGENT_MSG_TOKEN_LIMIT
        err_mock.details = {}

        error_response = Mock()
        error_response.get_error.return_value = err_mock
        error_response.category = ErrorCategory.AGENT  # not INTERNAL

        raw_exception = Exception(
            "\n⚠️ TOKEN LIMIT EXCEEDED\nAPI Response: finish_reason=length\n"
            "Model: 'claude-sonnet-5'\n"
            "The configured max_output_tokens limit was reached while generating tool arguments.\n"
        )

        result = agent.extended_error(error_response, raw_exception)

        assert result == config.AGENT_MSG_TOKEN_LIMIT
        assert "TOKEN LIMIT EXCEEDED" not in result
        assert "finish_reason" not in result
        assert "claude-sonnet-5" not in result
        assert "max_output_tokens" not in result
```

- [ ] **Step 2: Run the new test — verify it FAILS**

```bash
poetry run pytest tests/codemie/agents/test_langgraph_truncation.py::TestExtendedError -v
```

Expected output (FAIL):
```
FAILED tests/codemie/agents/test_langgraph_truncation.py::TestExtendedError::test_extended_error_returns_clean_message_for_agent_token_limit
AssertionError: assert '...\n⚠️ TOKEN LIMIT EXCEEDED\n...' == 'The configured output token limit was reached...'
```

Do not commit yet — the fix comes in Task 2.

---

### Task 2: Implement the `extended_error()` fix

**Test-first: no** — the failing test was written in Task 1. This task adds the 3-line production branch that makes it pass.

**Files:**
- Modify: `src/codemie/agents/tools/agent.py:64-67`

**Interfaces:**
- Consumes: `ErrorCode.AGENT_TOKEN_LIMIT` from `codemie.core.error_constants`; `err.message` (str, already set to `config.AGENT_MSG_TOKEN_LIMIT` by the classifier pipeline)
- Produces: `extended_error()` returns `err.message` for `AGENT_TOKEN_LIMIT` instead of `str(exception)`

- [ ] **Step 1: Add the branch in `extended_error()`**

In `src/codemie/agents/tools/agent.py`, locate the block starting at line 64:

```python
        if error_code == ErrorCode.LITE_LLM_BAD_REQUEST_ERROR and details.get("schema_validation_context"):
            return f"{err.message}\n{details['schema_validation_context']}"

        return str(exception)
```

Replace it with:

```python
        if error_code == ErrorCode.LITE_LLM_BAD_REQUEST_ERROR and details.get("schema_validation_context"):
            return f"{err.message}\n{details['schema_validation_context']}"

        if error_code == ErrorCode.AGENT_TOKEN_LIMIT:
            return err.message

        return str(exception)
```

No import changes needed — `ErrorCode` is already imported at line 29.

- [ ] **Step 2: Run the new test — verify it PASSES**

```bash
poetry run pytest tests/codemie/agents/test_langgraph_truncation.py::TestExtendedError -v
```

Expected output:
```
PASSED tests/codemie/agents/test_langgraph_truncation.py::TestExtendedError::test_extended_error_returns_clean_message_for_agent_token_limit
```

- [ ] **Step 3: Commit**

```bash
git add src/codemie/agents/tools/agent.py tests/codemie/agents/test_langgraph_truncation.py
git commit -m "EPMCDME-13459: return clean AGENT_MSG_TOKEN_LIMIT in extended_error for AGENT_TOKEN_LIMIT"
```

---

### Task 3: Update existing test assertions in `test_langgraph_truncation.py`

**Test-first: no** — these tests currently pass by asserting the wrong (buggy) behaviour. This task corrects them to assert the right behaviour: the raw internals must NOT be in `str(exception)`, and the exception must still be raised with correct structured attributes.

**Files:**
- Modify: `tests/codemie/agents/test_langgraph_truncation.py`

**Interfaces:**
- Consumes: `TokenLimitExceededException.model`, `TokenLimitExceededException.truncation_reason` — structured attributes that survive unchanged
- Produces: corrected test suite where no test asserts raw internals in the exception message string

- [ ] **Step 1: Update `test_detect_truncation_openai_format_finish_reason_length`**

Current (lines ~74-76):
```python
        assert "TOKEN LIMIT EXCEEDED" in str(exc_info.value)
        assert "finish_reason=length" in str(exc_info.value)
        assert "gpt-4.1" in str(exc_info.value)
```

Replace with — keep only the exception-is-raised assertion (the `pytest.raises` block above already covers that) and verify structured attributes:
```python
        assert exc_info.value.truncation_reason == "finish_reason=length"
```

- [ ] **Step 2: Update `test_detect_truncation_claude_stop_reason_max_tokens`**

Current (lines ~91-92):
```python
        assert "stop_reason=max_tokens" in str(exc_info.value)
        assert "claude-3-7" in str(exc_info.value)
```

Replace with:
```python
        assert exc_info.value.truncation_reason == "stop_reason=max_tokens"
        assert exc_info.value.model == "claude-3-7"
```

- [ ] **Step 3: Update `test_detect_truncation_bedrock_camelcase_stop_reason`**

Current (line ~108):
```python
        assert "max_tokens" in str(exc_info.value)
```

Replace with:
```python
        assert exc_info.value.truncation_reason == "stop_reason=max_tokens"
```

- [ ] **Step 4: Update `test_detect_truncation_without_tool_calls`**

Current (line ~119):
```python
        assert "before tool arguments could be generated" in str(exc_info.value)
```

Remove this line entirely. The test already verifies the exception is raised via `pytest.raises`; the internal context string is a log detail, not user-facing copy.

- [ ] **Step 5: Update `test_exception_contains_support_link`**

Current (line ~181):
```python
        assert "https://epa.ms/codemie-support" in str(exc_info.value)
```

Remove this line. The support link is an internal implementation detail in the exception message; it must not appear in user-facing copy.

- [ ] **Step 6: Invert `test_exception_includes_tool_names`**

Current (lines ~169-170):
```python
        assert "jira_tool" in error_msg
        assert "slack_tool" in error_msg
```

Replace with:
```python
        assert "jira_tool" not in error_msg
        assert "slack_tool" not in error_msg
```

Also update the docstring from:
```python
        """Test that exception message includes tool names."""
```
to:
```python
        """Test that tool names are NOT exposed in the user-facing exception message."""
```

- [ ] **Step 7: Run the full test class — verify all pass**

```bash
poetry run pytest tests/codemie/agents/test_langgraph_truncation.py -v
```

Expected: all tests in `TestTruncationDetection`, `TestTruncationHelperMethods`, `TestStaticMethods`, `TestSubAssistantNameTruncation`, `TestSubAssistantNameMapping`, and `TestExtendedError` pass. Zero failures.

- [ ] **Step 8: Commit**

```bash
git add tests/codemie/agents/test_langgraph_truncation.py
git commit -m "EPMCDME-13459: update test assertions to reflect clean user-facing error message"
```
