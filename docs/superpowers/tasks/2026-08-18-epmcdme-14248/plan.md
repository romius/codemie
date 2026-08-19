# Delete LiteLLM Shared Budget on Project Budget Deletion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the LiteLLM shared child budget (`{budget_id}:shared`) when a CodeMie project budget is deleted, closing the resource leak introduced by a stale no-op in the enterprise service.

**Architecture:** Add `delete_managed_budget()` to the enterprise service (mirrors the existing `create_managed_budget`/`update_managed_budget` pattern), add a thin `delete_shared_budget()` adapter method (mirrors `delete_override_budget()`), declare the method in the protocol and noop registry, then wire a call into `_delete_provider_project_budget()` in the core service.

**Tech Stack:** Python 3.12, FastAPI, LiteLLM proxy API (`POST /budget/delete`), pytest + pytest-asyncio, `asyncio.to_thread`, `unittest.mock`

**Spec:** `docs/superpowers/tasks/2026-08-18-epmcdme-14248/spec.md`

## Global Constraints

- Enterprise repo: `C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie-enterprise`
- Core repo: `C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie`
- Enterprise changes must be committed first; core tasks assume enterprise is installed (the repo uses a local editable install — no publish needed between tasks in a local dev setup)
- Fail-open: every provider call in the deletion flow must be wrapped in try/except; CodeMie soft-delete must succeed regardless of LiteLLM outcome
- Prefix guard: `delete_managed_budget()` must refuse IDs that don't start with `codemie:project:`
- 404 from LiteLLM on budget delete counts as success
- Commit message format: `EPMCDME-14248: <description>`
- Test-first: yes on all tasks

---

### Task 1: Add `delete_managed_budget()` to enterprise service

**Files:**
- Modify: `C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie-enterprise\src\codemie_enterprise\litellm\service.py:580`
- Test: `C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie-enterprise\tests\litellm\test_service.py`

**Test-first:** yes — write the three tests, verify they fail, implement, verify they pass.

**Interfaces:**
- Produces: `LiteLLMService.delete_managed_budget(*, budget_id: str) -> bool`
  - Returns `True` when budget deleted or not found
  - Returns `False` on unexpected errors
  - Raises `ValueError` when `budget_id` does not start with `codemie:project:`

- [ ] **Step 1: Write failing tests in `tests/litellm/test_service.py`**

Add after the last existing test:

```python
def test_delete_managed_budget_happy_path():
    config = LiteLLMConfig(
        url="http://localhost:4000",
        master_key="test-key",
        default_budget_id="default-budget",
        default_hard_budget_limit=100.0,
        default_soft_budget_limit=80.0,
        enabled=True,
        budget_check_enabled=True,
    )
    with patch("codemie_enterprise.litellm.service.LiteLLMAPIClient") as mock_client_cls:
        service = LiteLLMService(config)
        service.api_client = MagicMock()
        service.api_client.post.return_value = {"deleted": True}
        result = service.delete_managed_budget(budget_id="codemie:project:proj-1:shared")
    assert result is True
    service.api_client.post.assert_called_once_with("/budget/delete", data={"id": "codemie:project:proj-1:shared"})


def test_delete_managed_budget_not_found_is_success():
    config = LiteLLMConfig(
        url="http://localhost:4000",
        master_key="test-key",
        default_budget_id="default-budget",
        default_hard_budget_limit=100.0,
        default_soft_budget_limit=80.0,
        enabled=True,
        budget_check_enabled=True,
    )
    with patch("codemie_enterprise.litellm.service.LiteLLMAPIClient"):
        service = LiteLLMService(config)
        service.api_client = MagicMock()
        service.api_client.post.side_effect = httpx.HTTPStatusError(
            "Not Found", request=MagicMock(), response=MagicMock(status_code=404)
        )
        result = service.delete_managed_budget(budget_id="codemie:project:proj-1:shared")
    assert result is True


def test_delete_managed_budget_prefix_guard():
    config = LiteLLMConfig(
        url="http://localhost:4000",
        master_key="test-key",
        default_budget_id="default-budget",
        default_hard_budget_limit=100.0,
        default_soft_budget_limit=80.0,
        enabled=True,
        budget_check_enabled=True,
    )
    with patch("codemie_enterprise.litellm.service.LiteLLMAPIClient"):
        service = LiteLLMService(config)
        import pytest as _pytest
        with _pytest.raises(ValueError, match="Refusing to delete"):
            service.delete_managed_budget(budget_id="global-budget-123")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie-enterprise
poetry run pytest tests/litellm/test_service.py::test_delete_managed_budget_happy_path tests/litellm/test_service.py::test_delete_managed_budget_not_found_is_success tests/litellm/test_service.py::test_delete_managed_budget_prefix_guard -v
```

Expected: FAIL with `AttributeError: 'LiteLLMService' object has no attribute 'delete_managed_budget'`

- [ ] **Step 3: Implement `delete_managed_budget()` in `service.py`**

Insert after `delete_project_budget()` at line 588 (after the closing `return True` and before `sync_project_member_budget_assignment`):

```python
def delete_managed_budget(self, *, budget_id: str) -> bool:
    """Delete a CodeMie-managed LiteLLM budget object by id.

    Only budget ids that start with ``codemie:project:`` are accepted.
    A 404 response (budget already absent) is treated as success.
    Returns False on unexpected errors so callers can log and proceed.
    """
    if not budget_id.startswith("codemie:project:"):
        logger.warning(f"Refusing to delete non-project LiteLLM budget {budget_id!r}")
        raise ValueError(f"Refusing to delete non-project LiteLLM budget {budget_id!r}")
    try:
        self.api_client.post("/budget/delete", data={"id": budget_id})
        logger.debug(f"delete_managed_budget: deleted {budget_id!r}")
        return True
    except httpx.HTTPStatusError as exc:
        if _http_status_code(exc) == 404:
            logger.debug(f"delete_managed_budget: {budget_id!r} not found, treating as success")
            return True
        logger.warning(f"delete_managed_budget: failed to delete {budget_id!r}: {exc}")
        return False
    except Exception as exc:
        logger.warning(f"delete_managed_budget: failed to delete {budget_id!r}: {exc}")
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie-enterprise
poetry run pytest tests/litellm/test_service.py::test_delete_managed_budget_happy_path tests/litellm/test_service.py::test_delete_managed_budget_not_found_is_success tests/litellm/test_service.py::test_delete_managed_budget_prefix_guard -v
```

Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie-enterprise
git add src/codemie_enterprise/litellm/service.py tests/litellm/test_service.py
git commit -m "EPMCDME-14248: Add delete_managed_budget to LiteLLMService"
```

---

### Task 2: Add `delete_shared_budget()` to core provider protocol and noop registry

**Files:**
- Modify: `src/codemie/service/budget/provider.py:354` (after `delete_override_budget`)
- Modify: `src/codemie/service/budget/provider_registry.py:241` (after `delete_override_budget` noop)

**Test-first:** yes — no dedicated test file needed for the protocol stub itself; the noop is covered implicitly by the service tests in Task 4. Mark this task as infrastructure-only; proceed to implementation directly after verifying the import works.

**Interfaces:**
- Produces: `BudgetEnforcementProvider.delete_shared_budget(*, shared_budget_id: str) -> None` (protocol method)
- Produces: `_NoopBudgetEnforcementProvider.delete_shared_budget(*, shared_budget_id: str) -> None` (noop)

- [ ] **Step 1: Add protocol method to `provider.py`**

In `src/codemie/service/budget/provider.py`, insert after the `delete_override_budget` block (after line 354):

```python
    async def delete_shared_budget(
        self,
        *,
        shared_budget_id: str,
    ) -> None: ...
```

- [ ] **Step 2: Add noop stub to `provider_registry.py`**

In `src/codemie/service/budget/provider_registry.py`, insert after the `delete_override_budget` noop (after line 241):

```python
    async def delete_shared_budget(
        self,
        *,
        shared_budget_id: str,
    ) -> None:
        return self._noop_result(shared_budget_id)
```

- [ ] **Step 3: Verify import works**

```bash
cd C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie
poetry run python -c "from codemie.service.budget.provider import BudgetEnforcementProvider; from codemie.service.budget.provider_registry import _NoopBudgetEnforcementProvider; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
cd C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie
git add src/codemie/service/budget/provider.py src/codemie/service/budget/provider_registry.py
git commit -m "EPMCDME-14248: Add delete_shared_budget to provider protocol and noop registry"
```

---

### Task 3: Add `delete_shared_budget()` to LiteLLM adapter

**Files:**
- Modify: `src/codemie/enterprise/litellm/budget_provider_adapter.py:1321` (after `delete_override_budget`)
- Test: `tests/enterprise/litellm/test_budget_provider_adapter.py`

**Test-first:** yes — write two adapter tests, verify they fail, implement, verify they pass.

**Interfaces:**
- Consumes: `LiteLLMService.delete_managed_budget(*, budget_id: str) -> bool` (from Task 1)
- Produces: `LiteLLMBudgetEnforcementProvider.delete_shared_budget(*, shared_budget_id: str) -> None`

- [ ] **Step 1: Write failing tests in `tests/enterprise/litellm/test_budget_provider_adapter.py`**

Add after the last existing test:

```python
@pytest.mark.asyncio
async def test_delete_shared_budget_posts_to_endpoint():
    service = MagicMock()
    service.delete_managed_budget = MagicMock(return_value=True)
    adapter = LiteLLMBudgetEnforcementProvider(service=service)

    with patch("codemie.enterprise.litellm.budget_provider_adapter.asyncio.to_thread", new=AsyncMock(return_value=True)) as mock_thread:
        await adapter.delete_shared_budget(shared_budget_id="codemie:project:proj-1:shared")

    mock_thread.assert_awaited_once()
    call_args = mock_thread.call_args
    assert call_args.args[0] == service.delete_managed_budget
    assert call_args.kwargs == {"budget_id": "codemie:project:proj-1:shared"}


@pytest.mark.asyncio
async def test_delete_shared_budget_when_service_none():
    adapter = LiteLLMBudgetEnforcementProvider(service=None)
    # Should return without error and without calling anything
    await adapter.delete_shared_budget(shared_budget_id="codemie:project:proj-1:shared")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie
poetry run pytest tests/enterprise/litellm/test_budget_provider_adapter.py::test_delete_shared_budget_posts_to_endpoint tests/enterprise/litellm/test_budget_provider_adapter.py::test_delete_shared_budget_when_service_none -v
```

Expected: FAIL with `AttributeError: 'LiteLLMBudgetEnforcementProvider' object has no attribute 'delete_shared_budget'`

- [ ] **Step 3: Implement `delete_shared_budget()` in `budget_provider_adapter.py`**

Insert after `delete_override_budget()` (after line 1320, before `_resolve_runtime_core`):

```python
    async def delete_shared_budget(self, *, shared_budget_id: str) -> None:
        service = self._get_service()
        if service is None:
            logger.debug(
                f"budget_event=shared_budget_delete_skipped component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} shared_budget_id={shared_budget_id!r} reason=provider_unavailable"
            )
            return
        try:
            await asyncio.to_thread(service.delete_managed_budget, budget_id=shared_budget_id)
            logger.debug(
                f"budget_event=shared_budget_delete_completed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} shared_budget_id={shared_budget_id!r}"
            )
        except AttributeError:
            logger.debug(
                f"budget_event=shared_budget_delete_skipped component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} shared_budget_id={shared_budget_id!r} reason=method_unavailable"
            )
        except Exception as exc:
            logger.warning(
                f"budget_event=shared_budget_delete_failed component=litellm_budget_provider "
                f"provider={_PROVIDER_NAME!r} shared_budget_id={shared_budget_id!r} error={exc}"
            )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie
poetry run pytest tests/enterprise/litellm/test_budget_provider_adapter.py::test_delete_shared_budget_posts_to_endpoint tests/enterprise/litellm/test_budget_provider_adapter.py::test_delete_shared_budget_when_service_none -v
```

Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
cd C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie
git add src/codemie/enterprise/litellm/budget_provider_adapter.py tests/enterprise/litellm/test_budget_provider_adapter.py
git commit -m "EPMCDME-14248: Add delete_shared_budget to LiteLLM adapter"
```

---

### Task 4: Wire shared budget deletion into core service and update existing test

**Files:**
- Modify: `src/codemie/service/budget/project_budget_service.py:1518` (`_delete_provider_project_budget`)
- Test: `tests/codemie/service/budget/test_project_budget_service_lifecycle.py:92`

**Test-first:** yes — update the existing test to also assert `delete_shared_budget` called, verify the assertion fails, implement, verify it passes. Then add the fail-open test.

**Interfaces:**
- Consumes: `BudgetEnforcementProvider.delete_shared_budget(*, shared_budget_id: str) -> None` (from Task 2)
- Consumes: `build_shared_project_budget_id(main_budget_id: str) -> str` (already imported at line 44)

- [ ] **Step 1: Update existing test to assert `delete_shared_budget` is called**

In `tests/codemie/service/budget/test_project_budget_service_lifecycle.py`, update the `provider` SimpleNamespace at line 107–110 to add `delete_shared_budget`:

```python
    provider = SimpleNamespace(
        delete_member_allocation=AsyncMock(),
        delete_project_budget=AsyncMock(),
        delete_shared_budget=AsyncMock(),
    )
```

Then add this assertion after the existing assertions at line 158:

```python
    provider.delete_shared_budget.assert_awaited_once_with(shared_budget_id="proj-budget-1:shared")
```

- [ ] **Step 2: Run updated test to verify it fails**

```bash
cd C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie
poetry run pytest tests/codemie/service/budget/test_project_budget_service_lifecycle.py::test_delete_project_budget_marks_deleted_and_clears_resolution_cache -v
```

Expected: FAIL with `AssertionError: Expected 'delete_shared_budget' to have been awaited once` (or similar)

- [ ] **Step 3: Wire `delete_shared_budget` into `_delete_provider_project_budget()`**

In `src/codemie/service/budget/project_budget_service.py`, in `_delete_provider_project_budget()`, add after the existing try/except block that ends at line 1529:

```python
        shared_budget_id = build_shared_project_budget_id(budget_id)
        try:
            await provider.delete_shared_budget(shared_budget_id=shared_budget_id)
        except Exception as exc:
            logger.warning(
                f"budget_event=provider_shared_budget_delete_failed component=project_budget_service "
                f"provider={getattr(provider, 'provider_name', 'unknown')!r} "
                f"budget_id={budget_id!r} shared_budget_id={shared_budget_id!r} error={exc}"
            )
```

- [ ] **Step 4: Run updated test to verify it passes**

```bash
cd C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie
poetry run pytest tests/codemie/service/budget/test_project_budget_service_lifecycle.py::test_delete_project_budget_marks_deleted_and_clears_resolution_cache -v
```

Expected: PASS

- [ ] **Step 5: Add fail-open test**

Add a new test after the existing delete test in `test_project_budget_service_lifecycle.py`:

```python
@pytest.mark.asyncio
async def test_delete_project_budget_shared_budget_failure_does_not_block_soft_delete():
    service = ProjectBudgetService()
    session = AsyncMock()
    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        budget_type="project",
        budget_category="cli",
        budget_reset_at="2026-04-22T10:00:00Z",
        provider_metadata={"provider": "litellm", "provider_budget_ref": "provider-budget-1", "sync_status": "ok"},
    )
    assignment = SimpleNamespace(id="assignment-1", project_name="proj-a", budget_category="cli")
    allocations = []
    provider = SimpleNamespace(
        delete_member_allocation=AsyncMock(),
        delete_project_budget=AsyncMock(),
        delete_shared_budget=AsyncMock(side_effect=RuntimeError("LiteLLM unavailable")),
    )

    with (
        patch(
            "codemie.service.budget.project_budget_service.budget_repository.get_by_id",
            new=AsyncMock(return_value=budget),
        ),
        patch(
            "codemie.service.budget.project_budget_service.project_budget_assignment_repository.get_active_by_budget_id",
            new=AsyncMock(return_value=assignment),
        ),
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.get_active_by_budget_id",
            new=AsyncMock(return_value=allocations),
        ),
        patch(
            "codemie.service.budget.project_budget_service.budget_repository.list_active_child_budgets",
            new=AsyncMock(return_value=[]),
        ),
        patch("codemie.service.budget.project_budget_service.get_active_provider", return_value=provider),
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.soft_delete_all_by_budget_id",
            new=AsyncMock(),
        ) as mock_soft_delete_allocations,
        patch(
            "codemie.service.budget.project_budget_service.project_budget_assignment_repository.soft_delete",
            new=AsyncMock(),
        ) as mock_soft_delete_assignment,
        patch(
            "codemie.service.budget.project_budget_service.budget_repository.update",
            new=AsyncMock(),
        ),
    ):
        # Must not raise even though delete_shared_budget raises
        await service.delete_project_budget(session=session, budget_id="proj-budget-1", actor_id="actor-1")

    mock_soft_delete_allocations.assert_awaited_once_with(session, "proj-budget-1")
    mock_soft_delete_assignment.assert_awaited_once_with(session, "assignment-1")
```

- [ ] **Step 6: Run fail-open test to verify it passes**

```bash
cd C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie
poetry run pytest tests/codemie/service/budget/test_project_budget_service_lifecycle.py::test_delete_project_budget_shared_budget_failure_does_not_block_soft_delete -v
```

Expected: PASS

- [ ] **Step 7: Run full lifecycle test suite to check for regressions**

```bash
cd C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie
poetry run pytest tests/codemie/service/budget/ -v
```

Expected: all pass

- [ ] **Step 8: Commit**

```bash
cd C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie
git add src/codemie/service/budget/project_budget_service.py tests/codemie/service/budget/test_project_budget_service_lifecycle.py
git commit -m "EPMCDME-14248: Wire delete_shared_budget into project budget deletion flow"
```

---

### Task 5: Run full quality gates

- [ ] **Step 1: Run ruff lint and format check**

```bash
cd C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie
make ruff
```

Expected: no errors. Fix any issues, re-stage, re-commit before proceeding.

- [ ] **Step 2: Run full budget test suite**

```bash
cd C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie
poetry run pytest tests/codemie/service/budget/ tests/enterprise/litellm/test_budget_provider_adapter.py -v
```

Expected: all pass

- [ ] **Step 3: Run enterprise tests**

```bash
cd C:\Users\kostiantyn_pshenych1\Documents\cdme\codemie-enterprise
poetry run pytest tests/litellm/test_service.py -v
```

Expected: all pass
