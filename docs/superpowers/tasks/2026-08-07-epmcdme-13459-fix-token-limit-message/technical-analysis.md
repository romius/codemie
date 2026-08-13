# Technical Research

**Task**: token limit truncation error message langgraph agent exceptions
**Generated**: 2026-08-07T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

Chat displays misleading TOKEN LIMIT EXCEEDED message when the user is not out of tokens. When the model hits max_output_tokens during tool argument generation (finish_reason=length), the system raises TokenLimitExceededException with a message that says TOKEN LIMIT EXCEEDED — implying user quota exhaustion. The actual cause is the model output buffer being too small for the tool arguments. The user-facing message exposes raw technical details: finish_reason=length, model name, sub-agent function names, and a FIX: config instruction. The fix must: (1) replace the misleading heading with accurate copy, (2) strip raw technical internals from the user-facing string, (3) keep the real-quota exhaustion path reporting correctly, (4) update the existing unit tests to assert the new message shape.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/agents/langgraph_agent.py` — sole raise site of `TokenLimitExceededException`; contains three key functions:
  - `_get_truncation_indicator(response_metadata)` (line ~1224): extracts raw API field as a string, e.g. `"finish_reason=length"` or `"stop_reason=max_tokens"`. This is machine-readable signal, not user copy.
  - `_log_incomplete_tool_calls(message)` (line ~1246): returns a context string exposing sub-agent tool names, e.g. `"while generating jira_tool, slack_tool arguments"` or `"before tool arguments could be generated"`.
  - `_check_for_truncated_response(message)` (line ~1274): the only raise site. Currently builds a multi-line `error_message` containing the misleading heading `"⚠️ TOKEN LIMIT EXCEEDED"`, raw `truncation_indicator`, `self.llm_model`, tool names via `tool_context`, and the config instruction `"🔧 FIX: Increase 'max_output_tokens'"`. Raises `TokenLimitExceededException(message=error_message, model=self.llm_model, truncation_reason=truncation_indicator)`.
  - `_safe_check_for_truncation` (line ~1202): wraps the above; re-raises `TokenLimitExceededException`, swallows all other exceptions.
- `src/codemie/core/exceptions.py` — `TokenLimitExceededException(ValueError)` (line 170): stores `.message`, `.model`, `.truncation_reason`; `super().__init__(message)` so `str(exception)` returns the full raw message.
- `src/codemie/core/errors.py` — `AgentErrorClassifier._try_token_limit(msg)` (line ~652): matches `str(exception).lower()` against `("max_output_tokens", "truncat", "token limit", "token limit exceeded")`; routes to `build_agent_error_dict(AGENT_TOKEN_LIMIT)` which sets `agent_error.message = config.AGENT_MSG_TOKEN_LIMIT` (the clean string). The current raw message triggers this match via `"token limit exceeded"` and `"max_output_tokens"`.
- `src/codemie/core/error_constants.py` — `AGENT_ERROR_FRIENDLY_MESSAGES` maps `ErrorCode.AGENT_TOKEN_LIMIT → config.AGENT_MSG_TOKEN_LIMIT` (line ~280).
- `src/codemie/configs/config.py` — `AGENT_MSG_TOKEN_LIMIT` (line 815) = `"The configured output token limit was reached. Please try a shorter conversation or reduce context."` — the correct user-facing string, already defined, currently unused in the default (non-hidden) code path. Also `CODEMIE_SUPPORT` (line 556) = `"https://epa.ms/codemie-support"` — a support link that currently appears in the raw error message.
- `src/codemie/agents/tools/agent.py` — `extended_error()` (line ~40): the branching function for user-facing text in the default `HIDE_AGENT_STREAMING_EXCEPTIONS=False` path. Has explicit branches for `INTERNAL` error category, budget-exceeded, and bad-request. For all other error codes including `AGENT_TOKEN_LIMIT`, falls through to `return str(exception)` — which returns the raw misleading message.
- `tests/codemie/agents/test_langgraph_truncation.py` — all existing tests assert properties of the current buggy message (see Testing Landscape section).

### Architecture and Layers Affected

**Service / Business Logic layer** (`langgraph_agent.py`):
- `_check_for_truncated_response` — constructs the exception message (the root of the bug)

**Error Classification / Infrastructure layer** (`errors.py`, `exceptions.py`, `error_constants.py`):
- `AgentErrorClassifier._try_token_limit` — keyword-matching classifier; its keyword list is a dependency for any message change
- `TokenLimitExceededException` — the exception class, whose string form is what the classifier and `extended_error` consume

**Presentation / API Surface layer** (`agents/tools/agent.py`):
- `extended_error()` — the function that decides what string the user sees in the non-hidden streaming path; currently has no special case for `AGENT_TOKEN_LIMIT`

**Configuration layer** (`config.py`, `error_constants.py`):
- `AGENT_MSG_TOKEN_LIMIT` — the correct copy already exists and is wired through the classifier but bypassed by `extended_error`

### Integration Points

**Internal dependencies:**
- `langgraph_agent.py` → raises `TokenLimitExceededException` → caught by generic `except Exception` at line ~745
- Generic handler → `ExceptionClassificationPipeline` → `AgentErrorClassifier._try_token_limit` → `build_agent_error_dict(AGENT_TOKEN_LIMIT)` → sets `agent_error.message = AGENT_MSG_TOKEN_LIMIT`
- Classification result → `extended_error(error_response, e)` → currently ignores `agent_error.message`, returns `str(e)` for this code
- `HIDE_AGENT_STREAMING_EXCEPTIONS=True` path → uses `error_response.get_error().message` (the clean classifier string) — already correct
- `HIDE_AGENT_STREAMING_EXCEPTIONS=False` path (default) → uses `extended_error` → returns raw string — this is the bug

**Real-quota exhaustion path (unaffected):**
- Context-window exhaustion goes through `LiteLLMErrorClassifier` → `ErrorCode.LITE_LLM_CONTEXT_WINDOW_EXCEEDED_ERROR` → `LITELLM_MSG_CONTEXT_LENGTH`. This path does not touch `TokenLimitExceededException` and is entirely separate from the truncation path.

### Patterns and Conventions

- User-facing error strings live exclusively in `config.py` as `AGENT_MSG_*` / `LITELLM_MSG_*` constants. They are never built by ad-hoc f-string formatting at raise sites — this is the pattern the current bug violates.
- Technical details (raw API codes, model names, exception types) belong in structured exception attributes or logs, not in the message passed to `super().__init__()`.
- `extended_error()` is the authoritative per-error-code display customisation point for the non-hidden path. All existing special cases in it follow the pattern: use `err.message` (the classifier-derived clean string) optionally augmented with a sanitised `details` field.
- Exception attributes (`.model`, `.truncation_reason`) are the correct place for structured machine-readable data; they are set on `TokenLimitExceededException` and can be safely preserved or logged without surfacing them to the user.

---

## 3. Documentation Findings

### Guides and Architecture Docs

No guides found — conventions derived from code exploration.

### Architectural Decisions

No ADRs found in the codebase. The convention of keeping user-facing strings in `config.py` constants is established by the existing `AGENT_MSG_*` / `LITELLM_MSG_*` naming pattern and its consistent use across `error_constants.py` and the classification pipeline.

### Derived Conventions

- Error message constants are centralised in `config.py`; they are referenced from `error_constants.py` mapping dicts, not embedded in exception raise sites.
- The `extended_error()` function in `agents/tools/agent.py` is the canonical place to add per-error-code display logic for the non-hidden path. Adding a case there for `AGENT_TOKEN_LIMIT` that returns `err.message` follows the existing pattern exactly and is the lowest-risk fix location.
- Classifier keyword matching in `_try_token_limit` is fragile — it depends on the raw message containing specific substrings. If the exception message is changed (Option A below), at minimum one of `("max_output_tokens", "truncat", "token limit", "token limit exceeded")` must remain present, OR the classifier must be augmented with an `isinstance(exc_obj, TokenLimitExceededException)` type check to make it robust.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/agents/test_langgraph_truncation.py` — `TestTruncationDetection` class covering `_check_for_truncated_response` end-to-end. All tests assert properties of the current buggy message format.

### Testing Framework and Patterns

- pytest; unittest.mock for patching. Tests instantiate a minimal `LangGraphAgent` stub, inject mocked LLM responses with specific `response_metadata`, call `_check_for_truncated_response`, and assert on `str(exception)` content or exception attributes.

### Coverage Gaps

Assertions that will break and must be updated after the fix:

| Test method | Line | Current assertion (must change) |
|---|---|---|
| `test_detect_truncation_openai_format_finish_reason_length` | ~74 | `"TOKEN LIMIT EXCEEDED"` in msg |
| `test_detect_truncation_openai_format_finish_reason_length` | ~75 | `"finish_reason=length"` in msg |
| `test_detect_truncation_openai_format_finish_reason_length` | ~76 | `"gpt-4.1"` in msg (model name) |
| `test_detect_truncation_claude_stop_reason_max_tokens` | ~91 | `"stop_reason=max_tokens"` in msg |
| `test_detect_truncation_claude_stop_reason_max_tokens` | ~92 | `"claude-3-7"` in msg (model name) |
| `test_detect_truncation_bedrock_camelcase_stop_reason` | ~108 | `"max_tokens"` in msg (depends on new copy) |
| `test_detect_truncation_without_tool_calls` | ~119 | `"before tool arguments could be generated"` in msg |
| `test_exception_includes_tool_names` | ~169–170 | `"jira_tool"` and `"slack_tool"` in msg |
| `test_exception_contains_support_link` | ~181 | `"https://epa.ms/codemie-support"` in msg (survives if link kept) |

Assertions that survive unchanged:
- `e.model == "claude-3-7"` (structured attribute, not message string)
- `e.truncation_reason == "stop_reason=max_tokens"` (structured attribute, not message string)

New test coverage needed:
- Assert tool names are NOT present in the user-facing `str(exception)` (anti-regression)
- Assert model name is NOT present in the user-facing `str(exception)` (anti-regression)
- Assert raw API reason codes (`finish_reason=`, `stop_reason=`) are NOT in `str(exception)` (anti-regression)
- Assert the `extended_error()` path returns `AGENT_MSG_TOKEN_LIMIT` for `AGENT_TOKEN_LIMIT` error code (currently no test for this path)

---

## 5. Configuration and Environment

### Environment Variables

- `HIDE_AGENT_STREAMING_EXCEPTIONS` — boolean flag that determines which display path is used. `True`: uses classifier-derived `agent_error.message` (already correct). `False` (default): uses `extended_error()` (currently broken for this error code).

### Configuration Files

- `src/codemie/configs/config.py` — defines `AGENT_MSG_TOKEN_LIMIT` (line 815) and `CODEMIE_SUPPORT` (line 556). The correct user-facing string already exists here.
- `src/codemie/core/error_constants.py` — maps `ErrorCode.AGENT_TOKEN_LIMIT → config.AGENT_MSG_TOKEN_LIMIT` (line ~280). This mapping is already correct and used by the classifier.

### Feature Flags and Deployment Concerns

- `HIDE_AGENT_STREAMING_EXCEPTIONS`: the fix must work correctly for both `True` and `False` values. The `True` path already works correctly and must not be broken. The `False` path is the buggy one that needs the fix.

---

## 6. Risk Indicators

- **Classifier keyword dependency**: `AgentErrorClassifier._try_token_limit` matches on substring keywords in `str(exception)`. If Option A (rewriting the exception message at the raise site) removes all matching keywords, the classifier falls through to `InternalErrorClassifier` and emits an `INTERNAL` error instead of `AGENT_TOKEN_LIMIT`. Either (a) the new exception message must retain at least one keyword from `("max_output_tokens", "truncat", "token limit", "token limit exceeded")`, or (b) the classifier must be updated to also match `isinstance(exc_obj, TokenLimitExceededException)` directly. Option B (fixing `extended_error`) avoids this risk entirely.
- **Two fix surfaces, one preferred**: Option A modifies the raise site message; Option B adds a branch in `extended_error`. Option B is lower risk because it follows the existing `extended_error` pattern exactly, doesn't disturb the exception message used in logs and classifier matching, and requires only a 3–5 line change.
- **No test for `extended_error` with `AGENT_TOKEN_LIMIT`**: the gap in the default non-hidden path is currently untested, meaning the bug exists silently. A new integration-style test is needed.
- **Support link assertion is fragile**: `test_exception_contains_support_link` asserts the URL in `str(exception)`. If the support link is moved to logs rather than kept in the message, this test must be updated to reflect that.
- **`test_exception_includes_tool_names` is misleadingly named**: after the fix, tool names should NOT be in the user-facing message. This test should be inverted to assert their absence, and the tool names should be validated via a separate logging or attribute assertion.
- **`HIDE_AGENT_STREAMING_EXCEPTIONS=True` path must not regress**: this path already uses the clean `AGENT_MSG_TOKEN_LIMIT` string. Both fix options leave it untouched, but regression tests covering it would be prudent.
- **Single raise site reduces blast radius**: only one location in production code raises `TokenLimitExceededException` (`_check_for_truncated_response`), which means the change surface is well-contained.

---

## 7. Summary for Complexity Assessment

This task touches three architectural layers: the Service/Business Logic layer (exception construction in `langgraph_agent.py`), the Error Classification/Infrastructure layer (classifier keyword matching in `errors.py`), and the Presentation layer (the `extended_error` display function in `agents/tools/agent.py`). The file change surface is small — 2–3 production files and 1 test file — but the changes are interconnected: altering the exception message at the raise site has downstream effects on the classifier's keyword matching, while fixing `extended_error` instead is self-contained but requires understanding the full classification pipeline.

The fix follows an established codebase pattern (centralise user-facing strings in `config.py`; use `extended_error` as the display hook per error code) and therefore does not introduce technical novelty. The correct user-facing string (`AGENT_MSG_TOKEN_LIMIT`) and the correct routing infrastructure already exist; the bug is that `extended_error` lacks a case for `AGENT_TOKEN_LIMIT` in the non-hidden path. Option B — adding that case — is the idiomatic fix and should be favoured unless there is a specific reason to also change the exception message at the raise site.

Test coverage for the affected area exists but asserts the wrong (buggy) behaviour: 7–8 individual assertions across `test_langgraph_truncation.py` must be updated to assert the new, correct message shape. Several assertions must be inverted (tool names and model names must be absent from the user-facing string, not present). One new test path — covering `extended_error` with `AGENT_TOKEN_LIMIT` — has no existing test and needs to be added. The overall complexity is moderate: the logic change is small, but the test update requires careful judgment about what each assertion should now validate.
