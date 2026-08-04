# Spec: EPMCDME-13264 — Personal LiteLLM key requests must not inject model_kwargs["user"]

## Problem

When a user has a personal LiteLLM key configured, requests made on the direct-SDK
path still set `model_kwargs["user"]` before calling `AzureChatOpenAI`. LiteLLM
interprets this field as the caller's user ID for budget attribution, causing costs
to be attributed to the wrong budget.

The HTTP proxy-forwarding path (`proxy_router.py`) was corrected to suppress `user`
injection for personal keys under commit `c8e6529e7` (EPMCDME-11961: Fix more budget
defects). That commit reduced `_resolve_bypass_mode_stream` to a pure passthrough and
removed the tests that had asserted injection. The equivalent logic in
`llm_factory.py` was not updated at the same time. This fix completes that correction.

## Root Cause

`_configure_direct_runtime_overrides` in `llm_factory.py` (L433–451) contains a
bypass branch for personal-key requests that intentionally calls
`_resolve_direct_project_budget_runtime` to obtain a `project_runtime_user`, then
sets `model_kwargs["user"] = project_runtime_user` if the value is non-None.

**History:** commit `e4f1d6dfc` (EPMCDME-11961: Fix budget enforcement for user
overrides and proxy routing) added identical `user` injection to both `proxy_router.py`
and `llm_factory.py`. A follow-up commit `c8e6529e7` (EPMCDME-11961: Fix more budget
defects) removed it from the proxy path but did not touch the duplicate logic in
`llm_factory.py`. This ticket completes that removal; it does not revert the intent
of EPMCDME-11961, it aligns the direct-SDK path with the correction already applied to
the proxy path.

`_resolve_direct_project_budget_runtime` also hardcodes `has_user_litellm_credentials=False`
at L746 (`llm_factory.py`) when calling `select_runtime_budget_mode`, causing the mode
decision to always ignore the actual personal-key state. That hardcoded flag is not
fixed here because it becomes unreachable from bypass mode once this fix lands.

## Fix

### Production change (`src/codemie/enterprise/litellm/llm_factory.py`)

Replace lines 433–451 (the entire `if creds:` block) with the pure early-return block
below. The code block already keeps the `return` statement; deleting through 451 without
the replacement would cause the bypass branch to fall through into the non-bypass
budget-check path.

```python
if creds:
    logger.info(
        f"budget_event=runtime_mode_selected component=litellm_llm_factory "
        f"user_id={user_id!r} username={user_email!r} "
        f"mode={RuntimeBudgetMode.USER_CREDENTIALS_BYPASS.value!r} reason=own_credentials"
    )
    return
```

The `_resolve_direct_project_budget_runtime` call, its result destructuring, the
conditional `model_kwargs` assignment, and the associated comment are deleted.
The override-customer spend visibility for personal-key requests was already removed
from the proxy path by `c8e6529e7`; this change makes the two paths consistent rather
than removing behavior the proxy path still has.

**Side effects eliminated by this change:**
- `ensure_project_member_runtime_ready_sync` no longer runs for personal-key requests.
- `budget_resolution_service.resolve_sync` and `dispatch_runtime_sync` no longer run for personal-key requests.

Both are unnecessary for personal-key users whose costs are tracked by LiteLLM via
the personal key itself.

**Scope:** `create_litellm_embedding_model` shares the same
`_configure_direct_runtime_overrides` call path and is fixed automatically.

### No other production files change

| File | Change |
|---|---|
| `src/codemie/enterprise/litellm/llm_factory.py` | Replace `if creds:` block (L433–451) with pure early-return; hardcoded `has_user_litellm_credentials=False` at L746 is also in this file but is now unreachable from bypass mode |
| `src/codemie/enterprise/litellm/proxy_router.py` | No change — assumed correct for server-side injection (accepted assumption; see below) |
| External `codemie_enterprise` wheel | No change |

## Test Changes (`tests/enterprise/litellm/test_llm_factory.py`)

### 1. `test_skips_budget_check_when_has_credentials` — no change needed

The existing test calls `create_litellm_chat_model` without a `user_id` argument.
`_resolve_direct_project_budget_runtime` early-returns when `user_id` is `None`
(L686), so `project_runtime_user` is already `None` before the fix. Adding a
`model_kwargs` assertion there would pass both before and after the fix and does not
close the regression gap. Leave this test as-is.

### 2. New test: `test_skips_user_injection_when_has_personal_credentials`

Class: `TestCreateLiteLLMChatModel`

To be a genuine regression test (fails before fix, passes after), the test must
provide a `user_id` **and** a `litellm_context` with `current_project` set — otherwise
`_resolve_direct_project_budget_runtime` would early-return and return `None` even
without the fix.

Setup:
- `user_email="test@example.com"`, `user_id="user-123"` (non-None)
- `litellm_context` with `credentials=LiteLLMCredentials(...)` and `current_project="test-project"`
- Patch `_resolve_direct_project_budget_runtime` at the `llm_factory` module level
- Patch `AzureChatOpenAI`

Assertions:
- `_resolve_direct_project_budget_runtime` is **not called** (primary assertion)
- `model_kwargs.get("user")` is absent or `None` in the captured `AzureChatOpenAI` call args

### 3. New test: `test_sets_user_injection_when_no_personal_credentials`

Positive regression test — no personal key, project member tracking enabled:
- No `creds` / `litellm_context.credentials`; `user_id` non-None; project budget resolves to member-tracking mode
- Assert `model_kwargs["user"]` IS set in `request_params`

This is the other side of the acceptance-criteria contract ("existing flows that rely
on `user_id` for non-personal key scenarios continue to work as expected").

## Acceptance Criteria Mapping

| Criterion | How it is met |
|---|---|
| Personal-key requests do not send a populated `user_id` (`model_kwargs["user"]`) | Bypass branch returns early; no `model_kwargs` assignment |
| Budget attribution for personal-key requests is correct | LiteLLM sees no platform `user` field; cost tracked via personal key |
| Non-personal key flows continue to work | Non-bypass branch is untouched; regression test confirms |
| Regression coverage for both paths | Tests 2 and 3 above |

## Accepted Assumptions

**Proxy path:** The proxy path correctly suppresses server-side `user` injection for
personal-key requests. It does, however, forward a client-supplied `user` body field
untouched (pure passthrough), and the `user-id` authentication header is not in
`PROXY_HOP_BY_HOP_HEADERS`. These are accepted as out-of-scope on the basis that
CodeMie clients do not send these fields; no change is required to the proxy path for
this ticket.

## Out of Scope

- Adding direct unit tests for `runtime_budget_selection.select_runtime_budget_mode`
  (zero coverage exists; a separate ticket captures this gap).
- Fixing the hardcoded `has_user_litellm_credentials=False` at `llm_factory.py:746`
  (now unreachable from bypass mode after this fix; cleanup in a dedicated ticket).
- Any changes to the external `codemie_enterprise` wheel.
