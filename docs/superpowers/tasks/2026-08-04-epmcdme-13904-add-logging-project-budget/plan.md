# EPMCDME-13904: logger.info Gap-Fill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add missing `logger.info` calls to 6 service methods across 3 files so every mutating project and budget operation emits the same two-track audit pattern (logger.info + activity_event_repository) already present in user management.

**Architecture:** Service-layer only. Each target method already has an `activity_event_repository` insert; we add one `logger.info` call per method in the same style already used by other methods in that file. No router, repository, or schema changes.

**Tech Stack:** Python 3.12, FastAPI, SQLModel, pytest, pytest-asyncio, unittest.mock.

## Global Constraints

- Logger import: `from codemie.configs.logger import logger` (project_service) or `from codemie.configs import logger` (budget files). Both resolve to the same singleton — never add a new import if one already exists.
- Log messages must not expose PII, secrets, email addresses, usernames, or budget amounts. IDs, counts, and project names only.
- Message style per file — never mix styles:
  - `project_service.py` → `"project_created: project=<name>, by=<actor_id>"` (matches `project_deleted` in same file)
  - `project_budget_service.py` → `"budget_event=<name> component=project_budget_service budget_id=<id!r> ..."` (matches `budget_event=project_budget_create_completed` in same file)
  - `budget_service.py` → `"budget_event=<name> component=budget_service user_id=<id!r> ..."` (matches `budget_event=budget_create_completed` in same file)
- Test mock path must match the module-level import: `@patch("codemie.service.project.project_service.logger")`, `@patch("codemie.service.budget.project_budget_service.logger")`, `@patch("codemie.service.budget.budget_service.logger")`.
- `async_insert` for async service methods; `insert(..., session)` for sync. Do not swap them.
- All tests must pass: `make ruff && pytest tests/codemie/service/project/ tests/codemie/service/budget/ -v`

---

## Task 0: Discovery scan — find all other service methods missing logger.info

**Files:**
- Read: `src/codemie/service/**/*.py` (grep only, no edits)
- Write: `docs/superpowers/tasks/2026-08-04-epmcdme-13904-add-logging-project-budget/discovery-report.md`

**Interfaces:**
- Produces: `discovery-report.md` — list of service methods that have `activity_event_repository` insert but no `logger.info` in the same method body, beyond the 6 already in scope. Each entry: file path, method name, line number of the activity insert, verdict (in-scope-extension / follow-up / ignore).

- [ ] **Step 1: Find all service files that call activity_event_repository**

```bash
grep -rln "activity_event_repository" src/codemie/service/ --include="*.py"
```

Note the list of files returned.

- [ ] **Step 2: For each file, list methods with activity_event_repository inserts but no logger.info**

Run this pattern per file (replace `<file>` with each path from Step 1):

```bash
python - <<'EOF'
import ast, sys

path = sys.argv[1]
src = open(path).read()
tree = ast.parse(src)
lines = src.splitlines()

for node in ast.walk(tree):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    body_src = "\n".join(lines[node.lineno - 1 : node.end_lineno])
    has_activity = "activity_event_repository" in body_src and (
        ".insert(" in body_src or ".async_insert(" in body_src
    )
    has_logger_info = "logger.info(" in body_src
    if has_activity and not has_logger_info:
        print(f"{path}:{node.lineno}  {node.name}")
EOF
<file>
```

Or run across all files at once:

```bash
for f in $(grep -rln "activity_event_repository" src/codemie/service/ --include="*.py"); do
  python - "$f" <<'PYEOF'
import ast, sys
path = sys.argv[1]
src = open(path).read()
lines = src.splitlines()
tree = ast.parse(src)
for node in ast.walk(tree):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    body = "\n".join(lines[node.lineno-1:node.end_lineno])
    has_act = "activity_event_repository" in body and (".insert(" in body or ".async_insert(" in body)
    has_log = "logger.info(" in body
    if has_act and not has_log:
        print(f"{path}:{node.lineno}  {node.name}")
PYEOF
done
```

- [ ] **Step 3: Exclude the 6 already in scope, classify the rest**

The 6 already planned:
- `project_service.py` → `create_shared_project`, `update_project`
- `project_budget_service.py` → `override_member_allocation`, `clear_member_override`
- `budget_service.py` → `assign_budget_to_user`, `bulk_set_user_budgets`

For every other method found, classify as one of:
- **extend** — is a user-facing mutating admin operation that should have logger.info (add a task to this plan)
- **follow-up** — should be covered but is complex enough to be a separate ticket
- **ignore** — internal helper, system/background operation, or non-mutating

- [ ] **Step 4: Write discovery-report.md**

```markdown
# logger.info Discovery Report — EPMCDME-13904

**Scanned**: src/codemie/service/
**Date**: YYYY-MM-DD

## Gaps found beyond the 6 in spec

| File | Method | Line | Classification | Notes |
|---|---|---|---|---|
| ... | ... | ... | extend / follow-up / ignore | ... |

## Extended task list (classification=extend)
<list any methods to add to this plan, or "None — all gaps are follow-up or ignore">

## Follow-up ticket candidates
<list methods deferred to a future ticket>
```

- [ ] **Step 5: If any "extend" methods found, add tasks to this plan for them before continuing to Task 1**

Add a new task section after Task 3 for each extend method, following the same TDD structure as Tasks 1-3.

- [ ] **Step 6: Commit the report**

```bash
git add docs/superpowers/tasks/2026-08-04-epmcdme-13904-add-logging-project-budget/discovery-report.md
git commit -m "EPMCDME-13904: Add logger.info discovery report"
```

---

## Task 1: Add logger.info to project_service.py — create_shared_project and update_project

**Files:**
- Modify: `src/codemie/service/project/project_service.py` (lines ~149-162 for create, ~196-222 for update)
- Modify: `tests/codemie/service/project/test_project_service.py` (class `TestProjectServiceCreateSharedProject`)
- Modify: `tests/codemie/service/project/test_project_service_delete_update.py` (class `TestProjectServiceUpdateProject`)

**Interfaces:**
- Consumes: nothing from other tasks
- Produces: `create_shared_project` emits `"project_created: project=<name>, by=<actor_id>"`; `update_project` emits `"project_updated: project=<name>, by=<actor_id>"`

**Test-first: yes — add logger mock + assertion to existing success tests; they fail because logger.info is not yet called**

- [ ] **Step 1: Write the failing test for create_shared_project**

In `tests/codemie/service/project/test_project_service.py`, update `test_create_shared_project_success` inside `TestProjectServiceCreateSharedProject`:

```python
@patch("codemie.service.project.project_service.activity_event_repository")
@patch("codemie.service.project.project_service.logger")
@patch("codemie.service.project.project_service.user_project_repository")
@patch("codemie.service.project.project_service.application_repository")
@patch("codemie.service.project.project_service.user_repository")
@patch("codemie.service.project.project_service.cost_center_service")
@patch("codemie.service.project.project_service.get_session")
def test_create_shared_project_success(
    self,
    mock_get_session,
    mock_cost_center_service,
    mock_user_repository,
    mock_application_repository,
    mock_user_project_repository,
    mock_logger,
    mock_activity,
    regular_user,
):
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    mock_user_repository.get_active_by_id.return_value = MagicMock(project_limit=3)
    mock_cost_center_service.ensure_exists_for_project.return_value = None
    mock_application_repository.count_shared_projects_created_by_user.return_value = 1
    mock_application_repository.get_by_name_case_insensitive.return_value = None
    project = SimpleNamespace(
        name="data-pipeline",
        description="Analytics pipeline",
        project_type="shared",
        created_by="user-1",
        date=datetime(2026, 2, 10, tzinfo=UTC),
    )
    mock_application_repository.create.return_value = project
    mock_activity.insert = MagicMock()

    result = ProjectService.create_shared_project(
        user=regular_user,
        project_name="data-pipeline",
        description="Analytics pipeline",
    )

    assert result is project
    mock_logger.info.assert_called_once()
    assert "project_created" in mock_logger.info.call_args[0][0]
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
pytest tests/codemie/service/project/test_project_service.py::TestProjectServiceCreateSharedProject::test_create_shared_project_success -v
```

Expected: FAIL — `AssertionError: Expected 'info' to have been called once. Called 0 times.`

- [ ] **Step 3: Add logger.info to create_shared_project**

In `src/codemie/service/project/project_service.py`, inside the `try` block of `create_shared_project`, add after `invalidate_user_from_cache(user.id)` and before `return project`:

```python
logger.info(f"project_created: project={validated_name!r}, by={user.id}")
return project
```

The surrounding context (find by searching for `invalidate_user_from_cache`):
```python
                session.commit()
                invalidate_user_from_cache(user.id)
                logger.info(f"project_created: project={validated_name!r}, by={user.id}")   # ADD THIS
                return project
```

- [ ] **Step 4: Run to confirm PASS**

```bash
pytest tests/codemie/service/project/test_project_service.py::TestProjectServiceCreateSharedProject::test_create_shared_project_success -v
```

Expected: PASS.

- [ ] **Step 5: Write the failing test for update_project**

In `tests/codemie/service/project/test_project_service_delete_update.py`, find the class `TestProjectServiceUpdateProject` and its first success test (the one that updates description at line ~470). Add a new dedicated logger test alongside it:

```python
@patch("codemie.service.project.project_service.activity_event_repository")
@patch("codemie.service.project.project_service.logger")
@patch("codemie.service.project.project_service.user_project_repository")
@patch("codemie.service.project.project_service.application_repository")
@patch("codemie.service.project.project_service.get_session")
def test_update_project_logs_on_success(
    self,
    mock_get_session,
    mock_app_repo,
    mock_user_project_repo,
    mock_logger,
    mock_activity,
):
    mock_session = MagicMock()
    mock_get_session.return_value.__enter__.return_value = mock_session
    project = _make_app("my-project")
    project.cost_center_id = None
    mock_app_repo.get_by_name.return_value = project
    updated = _make_app("my-project")
    updated.description = "new desc"
    mock_app_repo.update_project.return_value = updated
    mock_activity.insert = MagicMock()

    ProjectService.update_project(
        user=self._make_super_admin(),
        project_name="my-project",
        description="new desc",
    )

    mock_logger.info.assert_called_once()
    assert "project_updated" in mock_logger.info.call_args[0][0]
```

Note: `_make_super_admin` is a private helper in the test class. Check the class for its definition. If it does not exist, add it:
```python
@staticmethod
def _make_super_admin():
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        from codemie.rest_api.security.user import User
        return User(id="admin-1", username="admin", email="admin@example.com", is_admin=True, is_maintainer=True)
```

- [ ] **Step 6: Run to confirm FAIL**

```bash
pytest "tests/codemie/service/project/test_project_service_delete_update.py::TestProjectServiceUpdateProject::test_update_project_logs_on_success" -v
```

Expected: FAIL — `AssertionError: Expected 'info' to have been called once. Called 0 times.`

- [ ] **Step 7: Add logger.info to update_project**

In `src/codemie/service/project/project_service.py`, in `update_project`, after `session.refresh(project)` and before `cls._resync_member_allocations_if_needed`:

```python
            session.refresh(project)
            logger.info(f"project_updated: project={project.name!r}, by={user.id}")   # ADD THIS
            cls._resync_member_allocations_if_needed(project.name, enforce_member_spend_limits)
            return project
```

- [ ] **Step 8: Run both project_service tests**

```bash
pytest tests/codemie/service/project/test_project_service.py tests/codemie/service/project/test_project_service_delete_update.py -v
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add src/codemie/service/project/project_service.py \
        tests/codemie/service/project/test_project_service.py \
        tests/codemie/service/project/test_project_service_delete_update.py
git commit -m "EPMCDME-13904: Add logger.info to project create and update"
```

---

## Task 2: Add logger.info to project_budget_service.py — override_member_allocation and clear_member_override

**Files:**
- Modify: `src/codemie/service/budget/project_budget_service.py` (~line 1350 for override, ~line 1434 for clear)
- Modify: `tests/codemie/service/budget/test_project_budget_service_lifecycle.py`

**Interfaces:**
- Consumes: nothing from other tasks
- Produces: `override_member_allocation` emits `"budget_event=member_allocation_override_completed component=project_budget_service ..."`, `clear_member_override` emits `"budget_event=member_allocation_override_cleared component=project_budget_service ..."`

**Test-first: yes**

- [ ] **Step 1: Write new success test for override_member_allocation (no existing success test)**

In `tests/codemie/service/budget/test_project_budget_service_lifecycle.py`, add a new test after the existing `test_override_member_allocation_raises_404_when_member_missing`:

```python
@pytest.mark.asyncio
async def test_override_member_allocation_logs_on_success():
    """override_member_allocation must emit a logger.info on success."""
    from types import SimpleNamespace
    service = ProjectBudgetService()
    session = AsyncMock()

    allocation = SimpleNamespace(
        id="alloc-1",
        user_id="user-a",
        override_budget_id=None,
        shared_budget_id="proj-budget-1:shared",
        effective_budget_id="proj-budget-1:shared",
        allocation_mode="equal",
        allocated_max_budget=None,
        allocated_soft_budget=None,
    )
    budget = SimpleNamespace(
        budget_id="proj-budget-1",
        budget_type="project",
        max_budget=100.0,
        soft_budget=80.0,
        budget_duration="30d",
        budget_reset_at=None,
        budget_category="cli",
    )
    assignment = SimpleNamespace(project_name="proj-a", budget_category="cli")

    provider_state = BudgetProviderMemberState(
        provider="litellm",
        provider_member_ref="ref-1",
        provider_budget_id="override-bud-1",
        sync_status=SyncStatus.OK,
    )

    with (
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.update_member_override",
            new=AsyncMock(return_value=allocation),
        ),
        patch.object(service, "get_project_budget", new=AsyncMock(return_value=(budget, assignment, [allocation]))),
        patch.object(service, "_ensure_override_child_budget", new=AsyncMock(return_value=SimpleNamespace(budget_id="override-bud-1"))),
        patch.object(service, "_persist_child_budget_provider_state", new=AsyncMock()),
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.update_member_budget_routing",
            new=AsyncMock(return_value=allocation),
        ),
        patch(
            "codemie.service.budget.project_budget_service.project_member_budget_assignment_repository.update_provider_metadata",
            new=AsyncMock(),
        ),
        patch(
            "codemie.service.budget.project_budget_service.get_active_provider",
            return_value=AsyncMock(sync_member_allocation=AsyncMock(return_value=provider_state)),
        ),
        patch.object(service, "rebalance_project_budget", new=AsyncMock()),
        patch(
            "codemie.service.budget.project_budget_service.activity_event_repository.async_insert",
            new=AsyncMock(),
        ),
        patch("codemie.service.budget.project_budget_service.logger") as mock_logger,
    ):
        await service.override_member_allocation(
            session=session,
            budget_id="proj-budget-1",
            user_id="user-a",
            allocated_max_budget=20.0,
            allocated_soft_budget=15.0,
            override_reason="manual",
            actor_id="actor-1",
        )

    mock_logger.info.assert_called_once()
    assert "member_allocation_override_completed" in mock_logger.info.call_args[0][0]
```

Note: `_ensure_override_child_budget` is the correct internal method name (verified in source at line 1329).

- [ ] **Step 2: Run to confirm FAIL**

```bash
pytest "tests/codemie/service/budget/test_project_budget_service_lifecycle.py::test_override_member_allocation_logs_on_success" -v
```

Expected: FAIL — `AssertionError: Expected 'info' to have been called once. Called 0 times.`

- [ ] **Step 3: Add logger.info to override_member_allocation**

In `src/codemie/service/budget/project_budget_service.py`, find the `override_member_allocation` method. After the `await activity_event_repository.async_insert(...)` call and before `return allocation`, add:

```python
        await activity_event_repository.async_insert(
            ActivityEventCreate(
                domain=ActivityDomain.BUDGET_MANAGEMENT,
                event_type=BudgetManagementEvent.MEMBER_ALLOCATION_OVERRIDDEN,
                ...
            ),
            session,
        )
        logger.info(                                                          # ADD THIS
            f"budget_event=member_allocation_override_completed "
            f"component=project_budget_service "
            f"budget_id={budget_id!r} user_id={user_id!r} actor_id={actor_id!r}"
        )
        return allocation
```

- [ ] **Step 4: Run to confirm PASS**

```bash
pytest "tests/codemie/service/budget/test_project_budget_service_lifecycle.py::test_override_member_allocation_logs_on_success" -v
```

Expected: PASS.

- [ ] **Step 5: Write failing test for clear_member_override (add logger assertion to existing success test)**

In `tests/codemie/service/budget/test_project_budget_service_lifecycle.py`, update `test_clear_member_override_syncs_litellm_with_shared_child_value` to add a logger assertion. The test uses `with (patch(...), ...)` context managers. Add the logger patch inside that `with` block:

```python
        patch("codemie.service.budget.project_budget_service.logger") as mock_logger,
        patch(
            "codemie.service.budget.project_budget_service.activity_event_repository.async_insert",
            new=AsyncMock(),
        ),
```

Then after the `await service.clear_member_override(...)` call, add:

```python
    mock_logger.info.assert_called_once()
    assert "member_allocation_override_cleared" in mock_logger.info.call_args[0][0]
```

- [ ] **Step 6: Run to confirm FAIL**

```bash
pytest "tests/codemie/service/budget/test_project_budget_service_lifecycle.py::test_clear_member_override_syncs_litellm_with_shared_child_value" -v
```

Expected: FAIL — `AssertionError: Expected 'info' to have been called once. Called 0 times.`

- [ ] **Step 7: Add logger.info to clear_member_override**

In `src/codemie/service/budget/project_budget_service.py`, find the `clear_member_override` method. After the `await activity_event_repository.async_insert(...)` call and before `return allocation`, add:

```python
        await activity_event_repository.async_insert(
            ActivityEventCreate(
                domain=ActivityDomain.BUDGET_MANAGEMENT,
                event_type=BudgetManagementEvent.MEMBER_ALLOCATION_OVERRIDE_CLEARED,
                ...
            ),
            session,
        )
        logger.info(                                                          # ADD THIS
            f"budget_event=member_allocation_override_cleared "
            f"component=project_budget_service "
            f"budget_id={budget_id!r} user_id={user_id!r} actor_id={actor_id!r}"
        )
        return allocation
```

- [ ] **Step 8: Run all project_budget_service lifecycle tests**

```bash
pytest tests/codemie/service/budget/test_project_budget_service_lifecycle.py -v
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add src/codemie/service/budget/project_budget_service.py \
        tests/codemie/service/budget/test_project_budget_service_lifecycle.py
git commit -m "EPMCDME-13904: Add logger.info to override and clear member allocation"
```

---

## Task 3: Add logger.info to budget_service.py — assign_budget_to_user and bulk_set_user_budgets

**Files:**
- Modify: `src/codemie/service/budget/budget_service.py` (~line 1143 for assign, ~line 1170 for bulk)
- Modify: `tests/codemie/service/budget/test_budget_service_activity.py` (add to existing assign test, add new bulk test)

**Interfaces:**
- Consumes: nothing from other tasks
- Produces: `assign_budget_to_user` emits `"budget_event=user_budget_assignment_completed component=budget_service ..."`, `bulk_set_user_budgets` emits `"budget_event=bulk_user_budget_assignment_completed component=budget_service ..."`

**Test-first: yes**

- [ ] **Step 1: Write the failing test for assign_budget_to_user**

In `tests/codemie/service/budget/test_budget_service_activity.py`, update `TestAssignBudgetToUserActivityEvent.test_assign_budget_to_user_emits_user_budget_assigned_event` to also add a logger patch and assertion. The existing test patches: `activity_event_repository`, `get_active_provider`, `budget_repository`. Add a logger patch:

```python
@pytest.mark.asyncio
@patch("codemie.service.budget.budget_service.logger")
@patch("codemie.service.budget.budget_service.activity_event_repository")
@patch("codemie.service.budget.budget_service.get_active_provider")
@patch("codemie.service.budget.budget_service.budget_repository")
async def test_assign_budget_to_user_emits_user_budget_assigned_event(
    self, mock_budget_repo, mock_get_provider, mock_activity, mock_logger
):
    """assign_budget_to_user must emit USER_BUDGET_ASSIGNED activity event and a completion logger.info."""
    mock_activity.async_insert = AsyncMock()
    session = AsyncMock()

    from codemie.rest_api.models.user_management import UserDB

    db_user = MagicMock(spec=UserDB)
    db_user.id = "user-123"
    db_user.username = "testuser"

    scalars_mock = MagicMock()
    scalars_mock.first.return_value = db_user
    result_mock = MagicMock()
    result_mock.scalars.return_value = scalars_mock
    session.execute = AsyncMock(return_value=result_mock)

    mock_budget_repo.upsert_user_category_assignment = AsyncMock()
    mock_budget_repo.delete_user_category_assignment = AsyncMock()

    provider = AsyncMock()
    provider.assign_user_budget = AsyncMock()
    mock_get_provider.return_value = provider

    service = _make_service()

    with patch.object(service, "validate_assignment_budget_categories", AsyncMock()):
        await service.assign_budget_to_user(
            session,
            user_id="user-123",
            assignments={BudgetCategory.PLATFORM: "test-budget"},
            actor_id="admin-1",
        )

    # existing activity event assertion
    mock_activity.async_insert.assert_called_once()
    call_args = mock_activity.async_insert.call_args[0]
    event_dto = call_args[0]
    assert event_dto.domain == ActivityDomain.BUDGET_MANAGEMENT
    assert event_dto.event_type == BudgetManagementEvent.USER_BUDGET_ASSIGNED
    assert event_dto.entity_id == "user-123"

    # new logger assertion
    mock_logger.info.assert_called_once()
    assert "user_budget_assignment_completed" in mock_logger.info.call_args[0][0]
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
pytest "tests/codemie/service/budget/test_budget_service_activity.py::TestAssignBudgetToUserActivityEvent::test_assign_budget_to_user_emits_user_budget_assigned_event" -v
```

Expected: FAIL — `AssertionError: Expected 'info' to have been called once. Called 0 times.`

- [ ] **Step 3: Add logger.info to assign_budget_to_user**

In `src/codemie/service/budget/budget_service.py`, find the `assign_budget_to_user` method. After the closing of the `for category, budget_id in assignments.items():` loop (after all the try/except blocks inside), add before the method ends (no explicit return):

```python
        logger.info(                                                          # ADD THIS
            f"budget_event=user_budget_assignment_completed component=budget_service "
            f"user_id={user_id!r} categories={sorted(c.value for c in assignments)!r} "
            f"actor_id={actor_id!r}"
        )
```

The insertion point is right after the last line of the for loop body (including the inner try/except). Confirm by checking indentation: the `logger.info` should be at the same indent level as the `for` statement (inside the method, outside the loop).

- [ ] **Step 4: Run to confirm PASS**

```bash
pytest "tests/codemie/service/budget/test_budget_service_activity.py::TestAssignBudgetToUserActivityEvent::test_assign_budget_to_user_emits_user_budget_assigned_event" -v
```

Expected: PASS.

- [ ] **Step 5: Write new test for bulk_set_user_budgets (no existing test)**

In `tests/codemie/service/budget/test_budget_service_activity.py`, add a new class or test function after `TestAssignBudgetToUserActivityEvent`:

```python
class TestBulkSetUserBudgetsLogger:
    @pytest.mark.asyncio
    @patch("codemie.service.budget.budget_service.logger")
    @patch("codemie.service.budget.budget_service.activity_event_repository")
    @patch("codemie.service.budget.budget_service.get_active_provider")
    @patch("codemie.service.budget.budget_service.budget_repository")
    async def test_bulk_set_user_budgets_logs_completion(
        self, mock_budget_repo, mock_get_provider, mock_activity, mock_logger
    ):
        """bulk_set_user_budgets must emit a completion logger.info after all users are processed."""
        mock_activity.async_insert = AsyncMock()
        session = AsyncMock()

        from codemie.rest_api.models.user_management import UserDB
        from sqlmodel import select as sa_select

        db_user_1 = MagicMock(spec=UserDB)
        db_user_1.id = "user-1"
        db_user_1.username = "user1"
        db_user_2 = MagicMock(spec=UserDB)
        db_user_2.id = "user-2"
        db_user_2.username = "user2"

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [db_user_1, db_user_2]
        result_mock = MagicMock()
        result_mock.scalars.return_value = scalars_mock
        session.execute = AsyncMock(return_value=result_mock)

        mock_budget_repo.upsert_user_category_assignment = AsyncMock()
        mock_get_provider.return_value = AsyncMock(assign_user_budget=AsyncMock())

        service = _make_service()

        with patch.object(service, "validate_assignment_budget_categories", AsyncMock()):
            await service.bulk_set_user_budgets(
                session,
                user_ids=["user-1", "user-2"],
                assignments={BudgetCategory.PLATFORM: "shared-budget"},
                actor_id="admin-1",
            )

        mock_logger.info.assert_called_once()
        assert "bulk_user_budget_assignment_completed" in mock_logger.info.call_args[0][0]
```

Note: `_make_service()` is a module-level factory already used in this test file. Check what it returns and use it. If it does not exist in `test_budget_service_activity.py`, find the pattern used in that file to instantiate the `BudgetService` and use the same approach.

- [ ] **Step 6: Run to confirm FAIL**

```bash
pytest "tests/codemie/service/budget/test_budget_service_activity.py::TestBulkSetUserBudgetsLogger::test_bulk_set_user_budgets_logs_completion" -v
```

Expected: FAIL — `AssertionError: Expected 'info' to have been called once. Called 0 times.`

- [ ] **Step 7: Add logger.info to bulk_set_user_budgets**

In `src/codemie/service/budget/budget_service.py`, find `bulk_set_user_budgets`. After the last line (`await self._propagate_bulk_budget_assignments(db_users, assignments)`), add:

```python
        db_users = await self._load_bulk_budget_users(session, user_ids, select, UserDB)
        await self._persist_bulk_budget_assignments(session, user_ids, assignments, actor_id)
        await self._propagate_bulk_budget_assignments(db_users, assignments)
        logger.info(                                                          # ADD THIS
            f"budget_event=bulk_user_budget_assignment_completed component=budget_service "
            f"user_count={len(user_ids)} categories={sorted(c.value for c in assignments)!r} "
            f"actor_id={actor_id!r}"
        )
```

- [ ] **Step 8: Run all budget_service_activity tests**

```bash
pytest tests/codemie/service/budget/test_budget_service_activity.py -v
```

Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add src/codemie/service/budget/budget_service.py \
        tests/codemie/service/budget/test_budget_service_activity.py
git commit -m "EPMCDME-13904: Add logger.info to assign_budget_to_user and bulk_set_user_budgets"
```

---

## Task 4: Full validation

**Files:**
- Read-only: run the full project + budget service test suite

**Interfaces:**
- Consumes: all changes from Tasks 1-3
- Produces: green test suite and passing linter

- [ ] **Step 1: Run linter**

```bash
make ruff
```

Expected: no errors. If `make ruff` fails with `poetry: No such file or directory`, see `.ai-run/guides/development/setup-guide.md` — the Claude Code Stop hook must be configured.

- [ ] **Step 2: Run the full affected test suite**

```bash
pytest tests/codemie/service/project/ tests/codemie/service/budget/ -v
```

Expected: all tests pass. No regressions in activity_event_repository assertions.

- [ ] **Step 3: Commit planning artifacts**

```bash
git add docs/superpowers/tasks/2026-08-04-epmcdme-13904-add-logging-project-budget/
git commit -m "EPMCDME-13904: Add planning artifacts"
```
