# Personal LiteLLM Key: Remove user injection in bypass mode

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the `model_kwargs["user"]` injection from the personal-key bypass branch in `_configure_direct_runtime_overrides`, so direct-SDK requests using a personal LiteLLM key no longer attribute costs to the wrong budget.

**Architecture:** Replace the `if creds:` block (lines 433–451) in `llm_factory.py` with a pure early-return, eliminating the conditional call to `_resolve_direct_project_budget_runtime` and the subsequent `model_kwargs["user"]` assignment. Two regression tests are added to `TestCreateLiteLLMChatModel`: one that fails before the fix (the genuine regression test) and one that confirms the non-personal-key path still injects the user field.

**Tech Stack:** Python 3.12, pytest 8.3.x, `unittest.mock.patch` / `patch.object`, `langchain_openai.AzureChatOpenAI`

## Global Constraints

- Python environment: `poetry run` or `python -m pytest` inside the `py3.12` virtualenv
- Test file: `tests/enterprise/litellm/test_llm_factory.py` — add new methods to `TestCreateLiteLLMChatModel`
- All imports inside test methods (existing convention in this file)
- Patch targets use full dotted paths (`codemie.enterprise.litellm.llm_factory.*`)
- Production change confined to `src/codemie/enterprise/litellm/llm_factory.py` only
- Branch: `EPMCDME-13264_personal-litellm-key-user-id-header`
- Commit message prefix: `EPMCDME-13264:`

---

### Task 1: TDD — regression test + production fix

**Test-first: yes** — `test_skips_user_injection_when_has_personal_credentials` must fail (RED) before the production change is applied.

**Files:**
- Modify: `tests/enterprise/litellm/test_llm_factory.py` (class `TestCreateLiteLLMChatModel`)
- Modify: `src/codemie/enterprise/litellm/llm_factory.py` (lines 433–451)

**Interfaces:**
- Consumes: `create_litellm_chat_model(llm_model_details, litellm_context, user_email, user_id)` from `codemie.enterprise.litellm.llm_factory`
- Produces: nothing for later tasks to consume — this is the only production task

---

- [ ] **Step 1: Write the failing test**

Add this method inside `class TestCreateLiteLLMChatModel` in `tests/enterprise/litellm/test_llm_factory.py`, after `test_skips_budget_check_when_has_credentials` (currently ending at line 173):

```python
def test_skips_user_injection_when_has_personal_credentials(self):
    """Personal-key bypass must not inject model_kwargs['user'] even when user_id and project are present."""
    from codemie.configs.config import config
    from codemie.rest_api.models.settings import LiteLLMCredentials, LiteLLMContext

    creds = LiteLLMCredentials(api_key="personal-key", url="http://personal:4000")
    litellm_context = LiteLLMContext(credentials=creds, current_project="test-project")

    mock_model_details = MagicMock()
    mock_model_details.base_name = "gpt-4"
    mock_model_details.configuration = None
    mock_model_details.features.streaming = True
    mock_model_details.features.temperature = True
    mock_model_details.features.parallel_tool_calls = True
    mock_model_details.features.max_tokens = True
    mock_model_details.features.top_p = True
    mock_model_details.api_version = None

    with patch.object(config, "LITE_LLM_URL", "http://test:4000"):
        with patch.object(config, "OPENAI_API_VERSION", "2025-04-01-preview"):
            with patch.object(config, "OPENAI_API_TYPE", "azure"):
                with patch.object(config, "AZURE_OPENAI_MAX_RETRIES", 3):
                    with patch.object(config, "LITE_LLM_TAGS_HEADER_VALUE", "default"):
                        with patch.object(config, "LITE_LLM_PROJECTS_TO_TAGS_LIST", ""):
                            with patch(
                                "codemie.enterprise.litellm.llm_factory._resolve_direct_project_budget_runtime"
                            ) as mock_resolver:
                                # Return a non-None user so the bug would inject it before the fix
                                mock_resolver.return_value = ("project-member-123", {}, None, None)
                                with patch(
                                    "codemie.enterprise.litellm.llm_factory.LiteLLMChatOpenAI"
                                ) as mock_model_cls:
                                    from codemie.enterprise.litellm.llm_factory import create_litellm_chat_model

                                    create_litellm_chat_model(
                                        llm_model_details=mock_model_details,
                                        litellm_context=litellm_context,
                                        user_email="test@example.com",
                                        user_id="user-123",
                                    )

                                    # Primary: resolver must not be called in personal-key bypass mode
                                    mock_resolver.assert_not_called()
                                    # Secondary: model_kwargs must not carry a "user" value
                                    call_kwargs = mock_model_cls.call_args.kwargs
                                    assert call_kwargs.get("model_kwargs", {}).get("user") is None
```

- [ ] **Step 2: Run test to confirm RED**

```bash
cd /Users/Oleksandr_Cherevach/improvements/codemie-dev/codemie
poetry run pytest tests/enterprise/litellm/test_llm_factory.py::TestCreateLiteLLMChatModel::test_skips_user_injection_when_has_personal_credentials -v
```

Expected: **FAILED** — `AssertionError: Expected '_resolve_direct_project_budget_runtime' to not have been called. Called 1 times.`

- [ ] **Step 3: Apply the production fix**

In `src/codemie/enterprise/litellm/llm_factory.py`, replace lines 433–451 (the entire `if creds:` block including its body and `return`) with the pure early-return block below. The new block keeps the log line and the `return` but removes the comment, the `_resolve_direct_project_budget_runtime` call, the result destructuring, and the conditional `model_kwargs` assignment.

**Before** (lines 433–451):
```python
    if creds:
        logger.info(
            f"budget_event=runtime_mode_selected component=litellm_llm_factory "
            f"user_id={user_id!r} username={user_email!r} "
            f"mode={RuntimeBudgetMode.USER_CREDENTIALS_BYPASS.value!r} reason=own_credentials"
        )
        # Even in bypass mode, resolve project member runtime to inject end_user for
        # override-customer spending tracking. Only model_kwargs["user"] is taken from
        # project runtime; api_key/base_url are intentionally ignored — creds from
        # litellm_context take precedence.
        (project_runtime_user, _, _, _) = _resolve_direct_project_budget_runtime(
            llm_model_details=llm_model_details,
            litellm_context=litellm_context,
            user_id=user_id,
            user_email=user_email,
        )
        if project_runtime_user:
            request_params["model_kwargs"] = {"user": project_runtime_user}
        return
```

**After** (replace with exactly this):
```python
    if creds:
        logger.info(
            f"budget_event=runtime_mode_selected component=litellm_llm_factory "
            f"user_id={user_id!r} username={user_email!r} "
            f"mode={RuntimeBudgetMode.USER_CREDENTIALS_BYPASS.value!r} reason=own_credentials"
        )
        return
```

- [ ] **Step 4: Run test to confirm GREEN**

```bash
poetry run pytest tests/enterprise/litellm/test_llm_factory.py::TestCreateLiteLLMChatModel::test_skips_user_injection_when_has_personal_credentials -v
```

Expected: **PASSED**

- [ ] **Step 5: Run the full TestCreateLiteLLMChatModel suite to confirm no regressions**

```bash
poetry run pytest tests/enterprise/litellm/test_llm_factory.py::TestCreateLiteLLMChatModel -v
```

Expected: all tests PASSED

- [ ] **Step 6: Commit**

```bash
git add tests/enterprise/litellm/test_llm_factory.py \
        src/codemie/enterprise/litellm/llm_factory.py
git commit -m "EPMCDME-13264: remove user injection in personal-key bypass mode"
```

---

### Task 2: Positive regression test — member-tracking path still injects user

**Test-first: no** — this test validates unchanged behavior on the non-personal path; it passes immediately after Task 1's commit (the non-personal branch is untouched).

The spec describes the positive test as exercising the "project budget resolves to member-tracking mode" sub-path — i.e. `_resolve_direct_project_budget_runtime` returns a non-None `project_runtime_user`, which `_apply_project_runtime_overrides` (`llm_factory.py:376`) propagates into `model_kwargs["user"]`. This is the `_apply_project_runtime_overrides` branch at L465–490, not the global fallback at L492–505. Constructing `litellm_context` with `credentials=None` and a `current_project`, plus a non-None `user_id`, reaches this sub-path when the resolver mock returns a member user.

Note: `test_skips_budget_check_when_has_credentials` (the existing test) was not extended to close the `model_kwargs` gap because it passes `user_id=None`, which causes `_resolve_direct_project_budget_runtime` to early-return before the injection point regardless of the fix — the test assertion would pass both before and after, proving nothing. This deviation from the spec's original suggestion was resolved during spec review; the new test below is the genuine regression-covering alternative.

**Files:**
- Modify: `tests/enterprise/litellm/test_llm_factory.py` (class `TestCreateLiteLLMChatModel`)

**Interfaces:**
- Consumes: `create_litellm_chat_model` (same as Task 1)
- Produces: nothing

---

- [ ] **Step 1: Write the positive regression test**

Add this method inside `class TestCreateLiteLLMChatModel` immediately after the test from Task 1:

```python
def test_sets_user_injection_when_no_personal_credentials(self):
    """Member-tracking path must still inject model_kwargs['user'] = project runtime user."""
    from codemie.configs.config import config
    from codemie.rest_api.models.settings import LiteLLMContext

    # No personal key (credentials=None) but project context present so member-tracking runs
    litellm_context = LiteLLMContext(credentials=None, current_project="test-project")

    mock_model_details = MagicMock()
    mock_model_details.base_name = "gpt-4"
    mock_model_details.configuration = None
    mock_model_details.features.streaming = True
    mock_model_details.features.temperature = True
    mock_model_details.features.parallel_tool_calls = True
    mock_model_details.features.max_tokens = True
    mock_model_details.features.top_p = True
    mock_model_details.api_version = None

    with patch.object(config, "LITE_LLM_URL", "http://test:4000"):
        with patch.object(config, "LITE_LLM_APP_KEY", "test-key"):
            with patch.object(config, "OPENAI_API_VERSION", "2025-04-01-preview"):
                with patch.object(config, "OPENAI_API_TYPE", "azure"):
                    with patch.object(config, "AZURE_OPENAI_MAX_RETRIES", 3):
                        with patch.object(config, "LITE_LLM_TAGS_HEADER_VALUE", "default"):
                            with patch.object(config, "LITE_LLM_PROJECTS_TO_TAGS_LIST", ""):
                                with patch(
                                    "codemie.enterprise.litellm.llm_factory._resolve_direct_project_budget_runtime",
                                    return_value=("member-user-123", {}, None, None),
                                ):
                                    with patch(
                                        "codemie.enterprise.litellm.llm_factory.LiteLLMChatOpenAI"
                                    ) as mock_model_cls:
                                        from codemie.enterprise.litellm.llm_factory import create_litellm_chat_model

                                        create_litellm_chat_model(
                                            llm_model_details=mock_model_details,
                                            litellm_context=litellm_context,
                                            user_email="test@example.com",
                                            user_id="user-123",
                                        )

                                        call_kwargs = mock_model_cls.call_args.kwargs
                                        assert call_kwargs.get("model_kwargs", {}).get("user") == "member-user-123"
```

- [ ] **Step 2: Run the new test**

```bash
poetry run pytest tests/enterprise/litellm/test_llm_factory.py::TestCreateLiteLLMChatModel::test_sets_user_injection_when_no_personal_credentials -v
```

Expected: **PASSED** (member-tracking branch is unchanged)

- [ ] **Step 3: Run the full relevant test suite**

```bash
poetry run pytest tests/enterprise/litellm/test_llm_factory.py -v
```

Expected: all tests PASSED

- [ ] **Step 4: Run ruff**

```bash
make ruff
```

Expected: no issues

- [ ] **Step 5: Commit**

```bash
git add tests/enterprise/litellm/test_llm_factory.py
git commit -m "EPMCDME-13264: add regression tests for personal and non-personal key user injection"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| Replace lines 433–451 with pure early-return | Task 1, Step 3 |
| `_resolve_direct_project_budget_runtime` not called in bypass mode | Task 1, Step 1 (assertion) |
| `model_kwargs["user"]` absent in personal-key bypass | Task 1, Step 1 (assertion) |
| Non-personal-key member-tracking path still sets `model_kwargs["user"]` | Task 2, Step 1 — uses `_resolve_direct_project_budget_runtime` mock returning a member user via `_apply_project_runtime_overrides` (L376) |
| `test_skips_budget_check_when_has_credentials` extension | Deviating per earlier review feedback: that test passes `user_id=None`, so the resolver early-returns before the injection point regardless of the fix — no regression value. Not extended. |
| Regression coverage for both paths | Tasks 1 and 2 |

**Placeholder scan:** No TBD/TODO/placeholder in any step. All code blocks are complete.

**Type consistency:** `create_litellm_chat_model` signature used identically in both tasks (`user_id=` kwarg explicitly passed).
