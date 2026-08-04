# EPMCDME-13794: Premium Model Budget Enforcement for Personal Agents — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce the default premium model budget limits when platform users invoke premium models through personal agents (no project context), mirroring the existing proxy-path enforcement.

**Architecture:** Three-file production change: (1) add a `default_premium_models` config entry that gates enforcement globally, (2) generalize `_mirror_platform_budget_assignment` to accept a `category` param so the DB mirror is accurate for any category, (3) inline a premium guard before the existing PLATFORM fallback in `_configure_direct_runtime_overrides()`. Also fix a stale comment. No new modules. No `proxy_router.py` changes.

**Tech Stack:** Python 3.12, FastAPI, LiteLLM, SQLModel, pytest

---

## File Map

| Action | File |
|---|---|
| Modify | `config/budgets/budgets-config.yaml` |
| Modify | `src/codemie/enterprise/litellm/budget_categories.py` |
| Modify | `src/codemie/enterprise/litellm/llm_factory.py` |
| Modify (tests) | `tests/enterprise/litellm/test_llm_factory.py` |
| Modify (tests) | `tests/enterprise/litellm/test_premium_models_budget.py` |

---

### Task 1: Add `default_premium_models` budget config entry

**Files:**
- Modify: `config/budgets/budgets-config.yaml`

**Test-first: no** — config-only change; verified by the premium tests in Tasks 4 and 5 which depend on `get_category_budget_id(PREMIUM_MODELS)` returning non-None.

- [ ] **Step 1: Add the premium models budget entry**

Open `config/budgets/budgets-config.yaml`. The current content is:

```yaml
predefined_budgets:
  - budget_id: default
    name: Default Budget
    description: Default platform budget for new LiteLLM customers.
    soft_budget: 50.0
    max_budget: 100.0
    budget_duration: 30d
    budget_category: platform
```

Append after the existing `platform` entry so the file reads:

```yaml
predefined_budgets:
  - budget_id: default
    name: Default Budget
    description: Default platform budget for new LiteLLM customers.
    soft_budget: 50.0
    max_budget: 100.0
    budget_duration: 30d
    budget_category: platform
  - budget_id: default_premium_models
    name: Default Premium Models Budget
    description: Default budget for premium model usage across all platform flows.
    soft_budget: 10.0
    max_budget: 20.0
    budget_duration: 30d
    budget_category: premium_models
```

- [ ] **Step 2: Commit**

```bash
git add config/budgets/budgets-config.yaml
git commit -m "config(EPMCDME-13794): add default_premium_models predefined budget entry

Prerequisite gate: activates is_premium_models_enabled() and
get_category_budget_id(PREMIUM_MODELS) for both llm_factory and
proxy_router enforcement paths."
```

---

### Task 2: Fix stale comment in budget_categories.py

**Files:**
- Modify: `src/codemie/enterprise/litellm/budget_categories.py:23`

**Test-first: no** — comment-only change.

- [ ] **Step 1: Fix the stale comment**

At line 23, the current line reads:

```python
    PREMIUM_MODELS = "premium_models"  # costly model spending via CLI
```

Change it to:

```python
    PREMIUM_MODELS = "premium_models"  # premium model spend — applies to platform, proxy, and CLI paths
```

- [ ] **Step 2: Commit**

```bash
git add src/codemie/enterprise/litellm/budget_categories.py
git commit -m "chore(EPMCDME-13794): fix stale comment on PREMIUM_MODELS enum value

PREMIUM_MODELS is enforced on platform/proxy/CLI paths, not CLI-only."
```

---

### Task 3: Generalize `_mirror_platform_budget_assignment` → `_mirror_budget_assignment`

**Files:**
- Modify: `src/codemie/enterprise/litellm/llm_factory.py`

**Test-first: no** — pure refactor; the call site's new `CoreBudgetCategory.PLATFORM` argument is validated by existing `TestConfigureDirectRuntimeOverrides` tests and by Task 4's tests.

- [ ] **Step 1: Replace the function definition**

In `llm_factory.py`, find the `_mirror_platform_budget_assignment` function (starts near line 379 with the docstring "Best-effort: mirror the PLATFORM budget assignment"). Replace the entire function with:

```python
def _mirror_budget_assignment(*, user_id: str | None, customer: Any | None, category: "CoreBudgetCategory") -> None:
    """Best-effort: mirror a budget assignment into user_budget_assignments.

    Called from the synchronous direct-runtime path after check_user_budget() creates
    the LiteLLM customer.  Dispatches to the main event loop without blocking so that
    the model-creation call-site is not affected if the DB write is slow or fails.

    Uses the budget_id from the actual LiteLLM customer record so that users with a
    custom budget get the correct ID mirrored.  Falls back to the configured default
    budget_id for the given category when the customer has no budget table attached.
    """
    if not user_id:
        return
    import asyncio

    from codemie.core.event_loop import _main_event_loop
    from codemie.service.budget.budget_service import budget_service

    budget_table = getattr(customer, "litellm_budget_table", None) if customer is not None else None
    budget_id: str | None = getattr(budget_table, "budget_id", None) if budget_table is not None else None

    if not budget_id:
        from .budget_categories import BudgetCategory as LiteLLMBudgetCategory
        from .dependencies import get_category_budget_id

        budget_id = get_category_budget_id(LiteLLMBudgetCategory(category.value))

    if not budget_id:
        return
    loop = _main_event_loop
    if loop is None or not loop.is_running():
        return
    asyncio.run_coroutine_threadsafe(
        budget_service.track_proxy_budget_assignment_for_request(
            user_id=user_id,
            category=category,
            budget_id=budget_id,
        ),
        loop,
    )
```

- [ ] **Step 2: Update the PLATFORM fallback block**

Still in `llm_factory.py`, find the existing PLATFORM fallback block (the last block inside `_configure_direct_runtime_overrides`, after the project-runtime-overrides early return). It currently reads:

```python
    from .budget_categories import BudgetCategory as LiteLLMBudgetCategory
    from .dependencies import check_user_budget, get_category_budget_id

    platform_budget_id = (
        _get_direct_request_category_budget_id(user_id, LiteLLMBudgetCategory.PLATFORM) if user_id else None
    ) or get_category_budget_id(LiteLLMBudgetCategory.PLATFORM)
    logger.info(
        f"budget_event=runtime_mode_selected component=litellm_llm_factory "
        f"user_id={user_id!r} username={user_email!r} model={llm_model_details.base_name!r} "
        f"mode={RuntimeBudgetMode.GLOBAL_OR_PERSONAL_BUDGET.value!r} "
        f"litellm_customer_key={user_email!r}"
    )
    customer = check_user_budget(user_email=user_email, user_id=user_id, budget_id=platform_budget_id)
    request_params["model_kwargs"] = {"user": user_email}
    _mirror_platform_budget_assignment(user_id=user_id, customer=customer)
```

Replace it with (only two changes: add `CoreBudgetCategory` import and update the last call):

```python
    from codemie.service.budget.budget_enums import BudgetCategory as CoreBudgetCategory
    from .budget_categories import BudgetCategory as LiteLLMBudgetCategory
    from .dependencies import check_user_budget, get_category_budget_id

    platform_budget_id = (
        _get_direct_request_category_budget_id(user_id, LiteLLMBudgetCategory.PLATFORM) if user_id else None
    ) or get_category_budget_id(LiteLLMBudgetCategory.PLATFORM)
    logger.info(
        f"budget_event=runtime_mode_selected component=litellm_llm_factory "
        f"user_id={user_id!r} username={user_email!r} model={llm_model_details.base_name!r} "
        f"mode={RuntimeBudgetMode.GLOBAL_OR_PERSONAL_BUDGET.value!r} "
        f"litellm_customer_key={user_email!r}"
    )
    customer = check_user_budget(user_email=user_email, user_id=user_id, budget_id=platform_budget_id)
    request_params["model_kwargs"] = {"user": user_email}
    _mirror_budget_assignment(user_id=user_id, customer=customer, category=CoreBudgetCategory.PLATFORM)
```

- [ ] **Step 3: Verify no stale references remain**

```bash
grep -rn "_mirror_platform_budget_assignment" src/ tests/
```

Expected: no output (the old name is gone).

- [ ] **Step 4: Run existing tests to confirm refactor did not break anything**

```bash
poetry run pytest tests/enterprise/litellm/test_llm_factory.py::TestConfigureDirectRuntimeOverrides -v 2>&1 | tail -10
```

Expected: PASS (existing 2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/enterprise/litellm/llm_factory.py
git commit -m "refactor(EPMCDME-13794): generalize _mirror_platform_budget_assignment to accept category

Rename to _mirror_budget_assignment and accept CoreBudgetCategory so the
premium model path can mirror with category=PREMIUM_MODELS instead of
hardcoded PLATFORM."
```

---

### Task 4: Inline premium guard in `_configure_direct_runtime_overrides()` + 6 tests

**Files:**
- Modify: `tests/enterprise/litellm/test_llm_factory.py` (add class with 6 tests — write first)
- Modify: `src/codemie/enterprise/litellm/llm_factory.py` (add guard — implement second)

**Test-first: yes** — write all 6 failing tests, confirm RED, then implement the guard.

- [ ] **Step 1: Write 6 failing tests**

Append the following class at the very end of `tests/enterprise/litellm/test_llm_factory.py`:

```python
class TestConfigureDirectRuntimeOverridesPremiumNoProject:
    """Tests for the premium model guard in _configure_direct_runtime_overrides
    when litellm_context.current_project is None (personal agent, no project context).
    """

    @pytest.fixture(autouse=True)
    def clear_caches(self):
        from codemie.enterprise.litellm.dependencies import (
            is_premium_model,
            is_premium_models_enabled,
        )
        is_premium_models_enabled.cache_clear()
        is_premium_model.cache_clear()
        yield
        is_premium_models_enabled.cache_clear()
        is_premium_model.cache_clear()

    def _invoke(self, *, user_email="alice@example.com", user_id="uid-1"):
        """Call _configure_direct_runtime_overrides with no creds and no project overrides."""
        from codemie.enterprise.litellm.llm_factory import _configure_direct_runtime_overrides

        llm_model_details = MagicMock()
        llm_model_details.base_name = "claude-opus-4"
        request_params: dict = {}

        with patch(
            "codemie.enterprise.litellm.llm_factory._resolve_direct_project_budget_runtime",
            return_value=(None, {}, None, None),
        ):
            _configure_direct_runtime_overrides(
                llm_model_details=llm_model_details,
                litellm_context=None,
                user_email=user_email,
                user_id=user_id,
                creds=None,
                merged_headers={},
                request_params=request_params,
            )
        return request_params

    def test_premium_model_no_project_context_uses_premium_budget(self):
        """Premium model + no project context → check_user_budget called with premium username and premium budget."""
        mock_customer = MagicMock()

        with (
            patch(
                "codemie.enterprise.litellm.dependencies.get_premium_username",
                return_value="alice@example.com_codemie_premium_models",
            ),
            patch(
                "codemie.enterprise.litellm.llm_factory._get_direct_request_category_budget_id",
                return_value=None,
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.get_category_budget_id",
                return_value="default_premium_models",
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.check_user_budget",
                return_value=mock_customer,
            ) as mock_check,
            patch("codemie.enterprise.litellm.llm_factory._mirror_budget_assignment"),
        ):
            params = self._invoke()

        mock_check.assert_called_once_with(
            user_email="alice@example.com_codemie_premium_models",
            user_id="uid-1",
            budget_id="default_premium_models",
        )
        assert params["model_kwargs"]["user"] == "alice@example.com_codemie_premium_models"

    def test_non_premium_model_no_project_context_uses_platform_budget(self):
        """Non-premium model → get_premium_username returns None → PLATFORM path unchanged."""
        with (
            patch(
                "codemie.enterprise.litellm.dependencies.get_premium_username",
                return_value=None,
            ),
            patch(
                "codemie.enterprise.litellm.llm_factory._get_direct_request_category_budget_id",
                return_value=None,
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.get_category_budget_id",
                return_value="default",
            ),
            patch("codemie.enterprise.litellm.dependencies.check_user_budget", return_value=MagicMock()),
            patch("codemie.enterprise.litellm.llm_factory._mirror_budget_assignment"),
        ):
            params = self._invoke()

        assert params["model_kwargs"]["user"] == "alice@example.com"

    def test_premium_model_no_budget_id_anywhere_falls_through_to_platform(self):
        """Premium model but no personal assignment and no default config → falls through to PLATFORM."""
        def category_budget_id(category):
            if category.value == "premium_models":
                return None
            return "default"

        with (
            patch(
                "codemie.enterprise.litellm.dependencies.get_premium_username",
                return_value="alice@example.com_codemie_premium_models",
            ),
            patch(
                "codemie.enterprise.litellm.llm_factory._get_direct_request_category_budget_id",
                return_value=None,
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.get_category_budget_id",
                side_effect=category_budget_id,
            ),
            patch("codemie.enterprise.litellm.dependencies.check_user_budget", return_value=MagicMock()),
            patch("codemie.enterprise.litellm.llm_factory._mirror_budget_assignment"),
        ):
            params = self._invoke()

        assert params["model_kwargs"]["user"] == "alice@example.com"

    def test_premium_model_personal_assignment_wins_over_default(self):
        """Personal PREMIUM_MODELS assignment present → used instead of default config budget."""
        with (
            patch(
                "codemie.enterprise.litellm.dependencies.get_premium_username",
                return_value="alice@example.com_codemie_premium_models",
            ),
            patch(
                "codemie.enterprise.litellm.llm_factory._get_direct_request_category_budget_id",
                return_value="personal-premium-budget",
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.get_category_budget_id",
                return_value="default_premium_models",
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.check_user_budget",
                return_value=MagicMock(),
            ) as mock_check,
            patch("codemie.enterprise.litellm.llm_factory._mirror_budget_assignment"),
        ):
            self._invoke()

        mock_check.assert_called_once_with(
            user_email="alice@example.com_codemie_premium_models",
            user_id="uid-1",
            budget_id="personal-premium-budget",
        )

    def test_mirror_budget_assignment_called_with_premium_category(self):
        """Premium model path calls _mirror_budget_assignment with category=PREMIUM_MODELS."""
        from codemie.service.budget.budget_enums import BudgetCategory as CoreBudgetCategory

        with (
            patch(
                "codemie.enterprise.litellm.dependencies.get_premium_username",
                return_value="alice@example.com_codemie_premium_models",
            ),
            patch(
                "codemie.enterprise.litellm.llm_factory._get_direct_request_category_budget_id",
                return_value=None,
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.get_category_budget_id",
                return_value="default_premium_models",
            ),
            patch("codemie.enterprise.litellm.dependencies.check_user_budget", return_value=MagicMock()),
            patch(
                "codemie.enterprise.litellm.llm_factory._mirror_budget_assignment",
            ) as mock_mirror,
        ):
            self._invoke()

        call_kwargs = mock_mirror.call_args.kwargs
        assert call_kwargs["category"] == CoreBudgetCategory.PREMIUM_MODELS
        assert call_kwargs["user_id"] == "uid-1"

    def test_premium_model_personal_assignment_enforced_without_default_config(self):
        """Personal PREMIUM_MODELS assignment is enforced even when no default config entry exists."""
        with (
            patch(
                "codemie.enterprise.litellm.dependencies.get_premium_username",
                return_value="alice@example.com_codemie_premium_models",
            ),
            patch(
                "codemie.enterprise.litellm.llm_factory._get_direct_request_category_budget_id",
                return_value="personal-premium-budget",
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.get_category_budget_id",
                return_value=None,
            ),
            patch(
                "codemie.enterprise.litellm.dependencies.check_user_budget",
                return_value=MagicMock(),
            ) as mock_check,
            patch("codemie.enterprise.litellm.llm_factory._mirror_budget_assignment"),
        ):
            params = self._invoke()

        mock_check.assert_called_once_with(
            user_email="alice@example.com_codemie_premium_models",
            user_id="uid-1",
            budget_id="personal-premium-budget",
        )
        assert params["model_kwargs"]["user"] == "alice@example.com_codemie_premium_models"
```

- [ ] **Step 2: Run the tests to confirm RED**

```bash
poetry run pytest tests/enterprise/litellm/test_llm_factory.py::TestConfigureDirectRuntimeOverridesPremiumNoProject -v 2>&1 | tail -20
```

Expected: all 6 tests FAIL (the guard does not exist yet — `_mirror_budget_assignment` not found or the wrong username is used).

- [ ] **Step 3: Implement the premium guard**

In `llm_factory.py`, find the PLATFORM fallback block (last block in `_configure_direct_runtime_overrides`, updated in Task 3 to start with `from codemie.service.budget.budget_enums import BudgetCategory as CoreBudgetCategory`). Replace it with the full block that includes the premium guard:

```python
    from codemie.service.budget.budget_enums import BudgetCategory as CoreBudgetCategory
    from .budget_categories import BudgetCategory as LiteLLMBudgetCategory
    from .dependencies import check_user_budget, get_category_budget_id, get_premium_username

    if user_email:
        premium_username = get_premium_username(user_email, llm_model_details.base_name)
        if premium_username is not None:
            premium_budget_id = (
                _get_direct_request_category_budget_id(user_id, LiteLLMBudgetCategory.PREMIUM_MODELS)
                if user_id else None
            ) or get_category_budget_id(LiteLLMBudgetCategory.PREMIUM_MODELS)
            if premium_budget_id:
                logger.info(
                    f"budget_event=runtime_mode_selected component=litellm_llm_factory "
                    f"user_id={user_id!r} username={user_email!r} model={llm_model_details.base_name!r} "
                    f"mode={RuntimeBudgetMode.GLOBAL_OR_PERSONAL_BUDGET.value!r} "
                    f"budget_category={LiteLLMBudgetCategory.PREMIUM_MODELS.value!r} "
                    f"litellm_customer_key={premium_username!r}"
                )
                customer = check_user_budget(user_email=premium_username, user_id=user_id, budget_id=premium_budget_id)
                request_params["model_kwargs"] = {"user": premium_username}
                _mirror_budget_assignment(
                    user_id=user_id, customer=customer, category=CoreBudgetCategory.PREMIUM_MODELS
                )
                return

    platform_budget_id = (
        _get_direct_request_category_budget_id(user_id, LiteLLMBudgetCategory.PLATFORM) if user_id else None
    ) or get_category_budget_id(LiteLLMBudgetCategory.PLATFORM)
    logger.info(
        f"budget_event=runtime_mode_selected component=litellm_llm_factory "
        f"user_id={user_id!r} username={user_email!r} model={llm_model_details.base_name!r} "
        f"mode={RuntimeBudgetMode.GLOBAL_OR_PERSONAL_BUDGET.value!r} "
        f"litellm_customer_key={user_email!r}"
    )
    customer = check_user_budget(user_email=user_email, user_id=user_id, budget_id=platform_budget_id)
    request_params["model_kwargs"] = {"user": user_email}
    _mirror_budget_assignment(user_id=user_id, customer=customer, category=CoreBudgetCategory.PLATFORM)
```

- [ ] **Step 4: Run the tests to confirm GREEN**

```bash
poetry run pytest tests/enterprise/litellm/test_llm_factory.py::TestConfigureDirectRuntimeOverridesPremiumNoProject -v 2>&1 | tail -20
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Run the full test file to confirm no regressions**

```bash
poetry run pytest tests/enterprise/litellm/test_llm_factory.py -v 2>&1 | tail -20
```

Expected: all tests in file PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/enterprise/litellm/llm_factory.py tests/enterprise/litellm/test_llm_factory.py
git commit -m "fix(EPMCDME-13794): enforce premium model budget for personal agents in direct path

Add inline premium guard before the PLATFORM fallback in
_configure_direct_runtime_overrides(). When user_email resolves to a premium
username AND a premium_budget_id is found (personal assignment or default
config), routes to check_user_budget with the premium username and premium
budget_id — mirroring proxy_router._resolve_tracking_identity. Falls through
to PLATFORM when no premium budget is available."
```

---

### Task 5: Add proxy-path regression test

**Files:**
- Modify: `tests/enterprise/litellm/test_premium_models_budget.py`

**Test-first: yes** — write the test first, then confirm it is GREEN (the proxy path already works after Task 1's config entry).

- [ ] **Step 1: Write the test**

Append the following class at the very end of `tests/enterprise/litellm/test_premium_models_budget.py`:

```python
class TestDirectPathPersonalAgentPremiumBudget:
    """Regression: proxy _resolve_tracking_identity returns PREMIUM_MODELS when
    project_scopes is empty and a default premium budget is configured.

    The proxy path was already correct before this ticket; this test prevents
    regression by asserting the no-project-context premium enforcement is intact.
    """

    def test_proxy_path_personal_agent_premium_model_enforced(self):
        """project_scopes=set() + default premium budget configured → PREMIUM_MODELS returned."""
        from codemie.enterprise.litellm.budget_categories import BudgetCategory
        from codemie.enterprise.litellm.proxy_router import BudgetAvailability, _resolve_tracking_identity

        mock_user = MagicMock()
        mock_user.id = "user-1"
        mock_user.username = "alice@example.com"

        request_info = {"llm_model": "claude-opus-4"}

        availability = BudgetAvailability(
            user_budget_ids={},
            project_scopes=set(),
        )

        with _patch_budget_name("default_premium_models"), _patch_aliases(["opus"]):
            category, username, budget_id, model = _resolve_tracking_identity(
                user=mock_user,
                request_info=request_info,
                availability=availability,
            )

        assert category == BudgetCategory.PREMIUM_MODELS
        assert username == "alice@example.com_codemie_premium_models"
        assert budget_id == "default_premium_models"
        assert model == "claude-opus-4"
```

- [ ] **Step 2: Run the test to confirm GREEN**

```bash
poetry run pytest "tests/enterprise/litellm/test_premium_models_budget.py::TestDirectPathPersonalAgentPremiumBudget::test_proxy_path_personal_agent_premium_model_enforced" -v 2>&1 | tail -10
```

Expected: PASS (`_patch_budget_name("default_premium_models")` simulates the config entry from Task 1; the proxy path was already correct before this ticket).

- [ ] **Step 3: Commit**

```bash
git add tests/enterprise/litellm/test_premium_models_budget.py
git commit -m "test(EPMCDME-13794): add regression test for proxy personal-agent premium enforcement

Verifies _resolve_tracking_identity returns PREMIUM_MODELS category when
project_scopes=set() and a default premium budget is configured. Prevents
regression of the proxy path that Task 1's config fix activates."
```

---

## Final Validation

After all 5 tasks are committed, run the full affected test suite:

```bash
poetry run pytest tests/enterprise/litellm/test_llm_factory.py tests/enterprise/litellm/test_premium_models_budget.py -v 2>&1 | tail -30
```

Expected: all tests PASS with no failures or errors.
