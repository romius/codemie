# Spec: Fix Misleading TOKEN LIMIT EXCEEDED Message

**Ticket**: EPMCDME-13459  
**Complexity**: XS (7/36)

---

## Problem

When the model hits `max_output_tokens` during tool-argument generation (`finish_reason=length`), the chat UI displays a raw exception string containing "⚠️ TOKEN LIMIT EXCEEDED", `finish_reason=length`, the model name, sub-agent tool names, and a `🔧 FIX: Increase 'max_output_tokens'` config instruction. This implies the user has exhausted their quota and exposes internal technical details as the primary user-facing message.

The correct user-facing string already exists: `config.AGENT_MSG_TOKEN_LIMIT = "The configured output token limit was reached. Please try a shorter conversation or reduce context."` It is wired through the error classifier but bypassed by `extended_error()` in the non-hidden streaming path.

---

## Goal

The chat UI must display `config.AGENT_MSG_TOKEN_LIMIT` — and nothing else — when the agent hits the model output generation limit. No raw API codes, no model name, no tool names, no config instructions.

---

## Out of Scope

- Changing the exception message at the raise site (`_check_for_truncated_response`) — the raw message is still needed for log records and classifier keyword matching.
- The real user-quota exhaustion path (`LiteLLMErrorClassifier → LITE_LLM_CONTEXT_WINDOW_EXCEEDED_ERROR`).
- The `HIDE_AGENT_STREAMING_EXCEPTIONS=True` path — it already uses `err.message` and is correct.

---

## Fix

### Production code — `src/codemie/agents/tools/agent.py`

Add one branch in `extended_error()` before the final `return str(exception)` fall-through:

```python
if error_code == ErrorCode.AGENT_TOKEN_LIMIT:
    return err.message
```

The classifier already resolves `err.message` to `config.AGENT_MSG_TOKEN_LIMIT` for this error code. No other changes to production code.

---

## Tests — `tests/codemie/agents/test_langgraph_truncation.py`

### Assertions to remove (raw internals no longer in user-facing string)

| Test | Assertion to remove |
|---|---|
| `test_detect_truncation_openai_format_finish_reason_length` | `"TOKEN LIMIT EXCEEDED" in str(exc_info.value)` |
| `test_detect_truncation_openai_format_finish_reason_length` | `"finish_reason=length" in str(exc_info.value)` |
| `test_detect_truncation_openai_format_finish_reason_length` | `"gpt-4.1" in str(exc_info.value)` |
| `test_detect_truncation_claude_stop_reason_max_tokens` | `"stop_reason=max_tokens" in str(exc_info.value)` |
| `test_detect_truncation_claude_stop_reason_max_tokens` | `"claude-3-7" in str(exc_info.value)` |
| `test_detect_truncation_bedrock_camelcase_stop_reason` | `"max_tokens" in str(exc_info.value)` |
| `test_detect_truncation_without_tool_calls` | `"before tool arguments could be generated" in str(exc_info.value)` |
| `test_exception_contains_support_link` | `"https://epa.ms/codemie-support" in str(exc_info.value)` |

All eight tests still assert that `TokenLimitExceededException` is raised and that its structured attributes (`.model`, `.truncation_reason`) are correct — those assertions survive unchanged.

### Assertion to invert

`test_exception_includes_tool_names` — currently asserts tool names ARE in the message. Invert to assert they are NOT:

```python
assert "jira_tool" not in str(exc_info.value)
assert "slack_tool" not in str(exc_info.value)
```

### New test — `extended_error` path

Add `TestExtendedError` class with one test that:
1. Stubs `error_response` with `err.error_code = ErrorCode.AGENT_TOKEN_LIMIT` and `err.message = config.AGENT_MSG_TOKEN_LIMIT`.
2. Calls `agent.extended_error(error_response, exception)`.
3. Asserts the return value equals `config.AGENT_MSG_TOKEN_LIMIT`.
4. Asserts the return value does not contain `"TOKEN LIMIT EXCEEDED"`, `"finish_reason"`, or any model/tool name.

---

## Acceptance Criteria

- Chat displays `"The configured output token limit was reached. Please try a shorter conversation or reduce context."` when the model output limit is reached.
- Chat does not display `"TOKEN LIMIT EXCEEDED"`, `finish_reason=length`, the model name, sub-agent function names, or any config instruction in this scenario.
- Real user-quota exhaustion continues to report correctly (separate path, untouched).
- `HIDE_AGENT_STREAMING_EXCEPTIONS=True` path continues to work correctly (untouched).
- All existing tests pass with updated assertions. One new test covers the `extended_error` branch for `AGENT_TOKEN_LIMIT`.
