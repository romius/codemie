# EPMCDME-13962: Allow Project Admins to Change Spend Bucket Distribution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow project admins to update spend bucket (category) distribution for budget groups they administer, while maintainers retain full access.

**Architecture:** A new FastAPI dependency `project_admin_budget_group_write_access` is added to `authentication.py`. It gates the `PUT /v1/admin/project-budget-groups/{group_id}` endpoint by loading the group from DB, verifying the group's `project_name` is in `user.admin_project_names`, and passing through for maintainers. The audit event is enriched with old and new category percentages.

**Tech Stack:** Python, FastAPI, SQLModel/SQLAlchemy async, pytest, AsyncMock

**Spec:** `docs/superpowers/tasks/2026-08-18-epmcdme-13962-spend-bucket-distribution/spec.md`

## Global Constraints

- Only `PUT /v1/admin/project-budget-groups/{group_id}` changes its access guard. All other mutating endpoints remain gated on `maintainer_access_only`.
- No new DB models or migrations.
- Distribution validation (`_validate_group_categories`) is unchanged.
- Follow existing auth dependency patterns in `src/codemie/rest_api/security/authentication.py`.

---

### Task 1: Add `project_admin_budget_group_write_access` dependency

**Files:**
- Modify: `src/codemie/rest_api/security/authentication.py`
- Test: `tests/codemie/rest_api/security/test_authentication.py` (create if absent; add to existing file otherwise)

**Interfaces:**
- Produces: `async def project_admin_budget_group_write_access(group_id: str, request: Request, session: AsyncSession = Depends(get_async_session)) -> User`
  - Returns the authenticated `User` on success
  - Raises `ExtendedHTTPException(code=404)` if group not found or soft-deleted
  - Raises `ExtendedHTTPException(code=403)` if user is neither a maintainer nor a project admin for the group's project

- [ ] **Step 1: Write the failing tests**

Add to the relevant authentication test file:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import Request
from types import SimpleNamespace

from codemie.rest_api.security.authentication import project_admin_budget_group_write_access
from codemie.core.exceptions import ExtendedHTTPException
from codemie.configs import config


def _make_request(user) -> Request:
    request = MagicMock(spec=Request)
    request.state.user = user
    return request


def _maintainer_user():
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        from codemie.rest_api.security.user import User
        return User(id="maint-1", username="maint@example.com", email="maint@example.com", is_maintainer=True)


def _project_admin(projects: list[str]):
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        from codemie.rest_api.security.user import User
        return User(
            id="proj-admin-1",
            username="proj-admin@example.com",
            email="proj-admin@example.com",
            is_admin=False,
            admin_project_names=projects,
            project_names=projects,
        )


def _other_user():
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        from codemie.rest_api.security.user import User
        return User(id="other-1", username="other@example.com", email="other@example.com")


@pytest.mark.asyncio
async def test_project_admin_budget_group_write_access_maintainer_passes():
    """Maintainer passes through regardless of project affiliation."""
    user = _maintainer_user()
    request = _make_request(user)
    mock_session = AsyncMock()

    result = await project_admin_budget_group_write_access(
        group_id="group-1", request=request, session=mock_session
    )

    assert result == user
    mock_session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_project_admin_budget_group_write_access_own_project_passes():
    """Project admin passes when group belongs to their project."""
    from codemie.service.budget.budget_models import ProjectBudgetGroup

    user = _project_admin(["project-alpha"])
    request = _make_request(user)

    group = SimpleNamespace(id="group-1", project_name="project-alpha", deleted_at=None)

    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = group

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    result = await project_admin_budget_group_write_access(
        group_id="group-1", request=request, session=mock_session
    )
    assert result == user


@pytest.mark.asyncio
async def test_project_admin_budget_group_write_access_other_project_raises_403():
    """Project admin is denied when group belongs to a different project."""
    user = _project_admin(["project-alpha"])
    request = _make_request(user)

    group = SimpleNamespace(id="group-1", project_name="project-beta", deleted_at=None)

    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = group

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(ExtendedHTTPException) as exc_info:
        await project_admin_budget_group_write_access(
            group_id="group-1", request=request, session=mock_session
        )
    assert exc_info.value.code == 403


@pytest.mark.asyncio
async def test_project_admin_budget_group_write_access_missing_group_raises_404():
    """Returns 404 when group does not exist."""
    user = _project_admin(["project-alpha"])
    request = _make_request(user)

    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = None

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(ExtendedHTTPException) as exc_info:
        await project_admin_budget_group_write_access(
            group_id="missing-group", request=request, session=mock_session
        )
    assert exc_info.value.code == 404


@pytest.mark.asyncio
async def test_project_admin_budget_group_write_access_non_admin_raises_403():
    """Regular authenticated user is denied."""
    user = _other_user()
    request = _make_request(user)

    group = SimpleNamespace(id="group-1", project_name="project-alpha", deleted_at=None)

    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = group

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(ExtendedHTTPException) as exc_info:
        await project_admin_budget_group_write_access(
            group_id="group-1", request=request, session=mock_session
        )
    assert exc_info.value.code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/codemie/rest_api/security/ -k "project_admin_budget_group_write_access" -v
```

Expected: `ImportError` or `AttributeError` — `project_admin_budget_group_write_access` not defined yet.

- [ ] **Step 3: Implement the dependency**

In `src/codemie/rest_api/security/authentication.py`, add these imports at the top of the file (with existing imports):

```python
from sqlalchemy.ext.asyncio import AsyncSession
from codemie.clients.postgres import get_async_session
```

Then add the function after `maintainer_access_only` (around line 196):

```python
async def project_admin_budget_group_write_access(
    group_id: str,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> User:
    from sqlmodel import select
    from codemie.service.budget.budget_models import ProjectBudgetGroup

    user: User = request.state.user

    if user.is_maintainer:
        return user

    result = await session.execute(select(ProjectBudgetGroup).where(ProjectBudgetGroup.id == group_id))
    group = result.scalars().first()

    if group is None or group.deleted_at is not None:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message="Project budget group not found",
            details=f"No active group with id '{group_id}'.",
            help="Check the group ID and try again.",
        )

    if group.project_name in user.admin_project_names:
        return user

    logger.warning(
        f"access_denied_budget_group_write: actor_user_id={user.id},"
        f" group_id={group_id}, group_project={group.project_name!r}, domain=budget_management"
    )
    raise ExtendedHTTPException(
        code=status.HTTP_403_FORBIDDEN,
        message=ACCESS_DENIED_MESSAGE,
        details="You can only update budget groups for projects you administer.",
        help="If you believe you should have access, contact your system administrator.",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/codemie/rest_api/security/ -k "project_admin_budget_group_write_access" -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/security/authentication.py tests/codemie/rest_api/security/
git commit -m "EPMCDME-13962: Add project_admin_budget_group_write_access dependency"
```

---

### Task 2: Swap access guard on group update endpoint

**Files:**
- Modify: `src/codemie/rest_api/routers/project_budget_router.py` (line 666)
- Test: `tests/codemie/rest_api/routers/test_project_budget_router.py`

**Interfaces:**
- Consumes: `project_admin_budget_group_write_access` from Task 1
- No interface changes — endpoint signature is unchanged for callers

- [ ] **Step 1: Write the failing router tests**

Add to `tests/codemie/rest_api/routers/test_project_budget_router.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from types import SimpleNamespace

from codemie.rest_api.routers.project_budget_router import update_project_budget_group
from codemie.core.exceptions import ExtendedHTTPException
from codemie.configs import config


def _project_admin_user_for_router(projects: list[str]):
    with patch.object(config, "ENV", "dev"), patch.object(config, "ENABLE_USER_MANAGEMENT", True):
        from codemie.rest_api.security.user import User
        return User(
            id="proj-admin-1",
            username="proj-admin@example.com",
            email="proj-admin@example.com",
            is_admin=False,
            admin_project_names=projects,
            project_names=projects,
        )


@pytest.mark.asyncio
async def test_update_group_project_admin_own_project_succeeds():
    """Project admin can update a group in their own project."""
    user = _project_admin_user_for_router(["project-alpha"])
    group_id = "group-1"

    payload = SimpleNamespace(
        name=None,
        description=None,
        budget_duration=None,
        total_amount=None,
        categories=[SimpleNamespace(budget_category="platform", pct=60.0),
                    SimpleNamespace(budget_category="cli", pct=40.0)],
    )

    group = SimpleNamespace(id=group_id, project_name="project-alpha", deleted_at=None)
    full_result = SimpleNamespace(group=group, categories=[], total_amount=100.0)

    mock_session = AsyncMock()
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("codemie.rest_api.routers.project_budget_router.get_async_session", return_value=mock_ctx),
        patch(
            "codemie.rest_api.routers.project_budget_router.project_budget_service.update_project_budget_group",
            new_callable=AsyncMock,
        ) as mock_update,
        patch(
            "codemie.rest_api.routers.project_budget_router.project_budget_service.get_project_budget_group",
            new_callable=AsyncMock,
            return_value=full_result,
        ),
    ):
        mock_update.return_value = full_result
        result = await update_project_budget_group(
            group_id=group_id, payload=payload, user=user, _=user
        )
    assert result is not None


@pytest.mark.asyncio
async def test_update_group_project_admin_other_project_raises_403():
    """project_admin_budget_group_write_access raises 403 for cross-project access."""
    from codemie.rest_api.security.authentication import project_admin_budget_group_write_access
    from unittest.mock import AsyncMock, MagicMock

    user = _project_admin_user_for_router(["project-alpha"])
    request = MagicMock()
    request.state.user = user

    group = SimpleNamespace(id="group-99", project_name="project-beta", deleted_at=None)
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = group
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    with pytest.raises(ExtendedHTTPException) as exc_info:
        await project_admin_budget_group_write_access(
            group_id="group-99", request=request, session=mock_session
        )
    assert exc_info.value.code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/codemie/rest_api/routers/test_project_budget_router.py -k "update_group_project_admin" -v
```

Expected: tests fail because the endpoint still uses `maintainer_access_only`.

- [ ] **Step 3: Swap the dependency in the router**

In `src/codemie/rest_api/routers/project_budget_router.py`:

Update the import line (~line 30):
```python
# Before:
from codemie.rest_api.security.authentication import authenticate, maintainer_access_only

# After:
from codemie.rest_api.security.authentication import (
    authenticate,
    maintainer_access_only,
    project_admin_budget_group_write_access,
)
```

Update the endpoint (~line 661–667):
```python
# Before:
@group_router.put('/{group_id}', response_model=ProjectBudgetGroupResponse)
async def update_project_budget_group(
    group_id: str,
    payload: ProjectBudgetGroupUpdateRequest,
    user: User = Depends(authenticate),
    _: None = Depends(maintainer_access_only),
):

# After:
@group_router.put('/{group_id}', response_model=ProjectBudgetGroupResponse)
async def update_project_budget_group(
    group_id: str,
    payload: ProjectBudgetGroupUpdateRequest,
    user: User = Depends(authenticate),
    _: User = Depends(project_admin_budget_group_write_access),
):
```

Note: `authenticate` still runs first to populate `request.state.user`; `project_admin_budget_group_write_access` reads from `request.state.user`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/codemie/rest_api/routers/test_project_budget_router.py -k "update_group_project_admin" -v
```

Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/routers/project_budget_router.py tests/codemie/rest_api/routers/test_project_budget_router.py
git commit -m "EPMCDME-13962: Swap maintainer_access_only with project_admin_budget_group_write_access on group PUT"
```

---

### Task 3: Enrich audit event with old and new category percentages

**Files:**
- Modify: `src/codemie/service/budget/project_budget_service.py` (lines 1884–1894)
- Test: `tests/codemie/service/budget/test_project_budget_service.py`

**Interfaces:**
- Consumes: `current_result` (already computed at line 1865 before the audit call) — `current_result.categories` is a list of `ProjectBudgetGroupCategoryResult` with `.assignment.budget_category` and `.assignment.pct`
- No interface changes to `update_project_budget_group` signature

- [ ] **Step 1: Write the failing test**

Add to `tests/codemie/service/budget/test_project_budget_service.py`:

```python
@pytest.mark.asyncio
async def test_update_project_budget_group_audit_event_includes_old_and_new_categories():
    """PROJECT_BUDGET_GROUP_UPDATED event attributes include old_categories and new_categories."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch, call
    from codemie.service.budget.project_budget_service import project_budget_service
    from codemie.service.activity.activity_models import BudgetManagementEvent

    group_id = "group-1"
    actor_id = "actor-1"

    group = SimpleNamespace(
        id=group_id,
        project_name="project-alpha",
        deleted_at=None,
        budget_duration="30d",
        name="Test Group",
        description=None,
    )

    old_category = SimpleNamespace(
        assignment=SimpleNamespace(budget_category="platform", pct=50.0),
    )
    new_category = SimpleNamespace(
        assignment=SimpleNamespace(budget_category="platform", pct=70.0),
    )
    cli_category = SimpleNamespace(
        assignment=SimpleNamespace(budget_category="cli", pct=30.0),
    )

    old_full_result = SimpleNamespace(
        group=group,
        categories=[old_category],
        total_amount=100.0,
    )
    new_full_result = SimpleNamespace(
        group=group,
        categories=[new_category, cli_category],
        total_amount=100.0,
    )

    payload = SimpleNamespace(
        name=None,
        description=None,
        budget_duration=None,
        total_amount=None,
        categories=[
            SimpleNamespace(budget_category="platform", pct=70.0),
            SimpleNamespace(budget_category="cli", pct=30.0),
        ],
    )

    captured_events = []

    async def fake_activity_insert(event, session):
        captured_events.append(event)

    mock_session = AsyncMock()

    with (
        patch.object(
            project_budget_service,
            "_load_group_full_result",
            new_callable=AsyncMock,
            side_effect=[old_full_result, new_full_result],
        ),
        patch.object(
            project_budget_service,
            "_update_group_scalar_fields",
            new_callable=AsyncMock,
        ),
        patch.object(
            project_budget_service,
            "_validate_group_categories",
        ),
        patch.object(
            project_budget_service,
            "_update_group_categories",
            new_callable=AsyncMock,
        ),
        patch(
            "codemie.service.budget.project_budget_service.project_budget_group_repository.get_by_id",
            new_callable=AsyncMock,
            return_value=group,
        ),
        patch(
            "codemie.service.budget.project_budget_service.activity_event_repository.async_insert",
            side_effect=fake_activity_insert,
        ),
    ):
        await project_budget_service.update_project_budget_group(
            mock_session, group_id=group_id, data=payload, actor_id=actor_id
        )

    assert len(captured_events) == 1
    event = captured_events[0]
    assert event.event_type == BudgetManagementEvent.PROJECT_BUDGET_GROUP_UPDATED
    assert "old_categories" in event.attributes
    assert "new_categories" in event.attributes
    assert event.attributes["old_categories"] == {"platform": 50.0}
    assert event.attributes["new_categories"] == {"platform": 70.0, "cli": 30.0}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/codemie/service/budget/test_project_budget_service.py -k "test_update_project_budget_group_audit_event_includes_old_and_new" -v
```

Expected: FAIL — `old_categories` and `new_categories` not in event attributes.

- [ ] **Step 3: Implement the enrichment**

In `src/codemie/service/budget/project_budget_service.py`, update `update_project_budget_group`.

Before the early-return path (around line 1858), capture `current_result` for the old snapshot. The `current_result` variable is already loaded at line 1865 for the category update path, but not for the scalar-only path. Add capture at line 1864 (before the early return for scalar-only updates) and pass it to the audit event:

```python
# Around line 1849 — after updated_fields is built, before _update_group_scalar_fields:
# Capture old categories for audit — load before any mutations
_old_result = await self._load_group_full_result(session, group)
_old_categories = {r.assignment.budget_category: r.assignment.pct for r in _old_result.categories}
```

Then update the `activity_event_repository.async_insert` call (around line 1884):

```python
# After the final session.refresh(group), compute new categories from reloaded result:
_new_result_for_audit = await self._load_group_full_result(session, group)
_new_categories = {r.assignment.budget_category: r.assignment.pct for r in _new_result_for_audit.categories}

await activity_event_repository.async_insert(
    ActivityEventCreate(
        domain=ActivityDomain.BUDGET_MANAGEMENT,
        event_type=BudgetManagementEvent.PROJECT_BUDGET_GROUP_UPDATED,
        entity_type=ActivityEntityType.PROJECT_BUDGET_GROUP,
        entity_id=group_id,
        actor_id=actor_id,
        attributes={
            "project_name": group.project_name,
            "old_categories": _old_categories,
            "new_categories": _new_categories,
        },
    ),
    session,
)
return _new_result_for_audit
```

Also update the early-return path (scalar-only, line ~1857–1863) to include the same audit event emission with old/new categories captured before scalar update, so all update paths emit a complete audit trail:

```python
if data.categories is None and data.total_amount is None:
    logger.info(...)
    await activity_event_repository.async_insert(
        ActivityEventCreate(
            domain=ActivityDomain.BUDGET_MANAGEMENT,
            event_type=BudgetManagementEvent.PROJECT_BUDGET_GROUP_UPDATED,
            entity_type=ActivityEntityType.PROJECT_BUDGET_GROUP,
            entity_id=group_id,
            actor_id=actor_id,
            attributes={
                "project_name": group.project_name,
                "old_categories": _old_categories,
                "new_categories": _old_categories,  # no category change
            },
        ),
        session,
    )
    return await self._load_group_full_result(session, group)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/codemie/service/budget/test_project_budget_service.py -k "test_update_project_budget_group_audit_event_includes_old_and_new" -v
```

Expected: PASS.

- [ ] **Step 5: Run full budget service tests to catch regressions**

```bash
python -m pytest tests/codemie/service/budget/ -v
```

Expected: all existing tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/budget/project_budget_service.py tests/codemie/service/budget/test_project_budget_service.py
git commit -m "EPMCDME-13962: Enrich PROJECT_BUDGET_GROUP_UPDATED event with old/new category percentages"
```

---

## Final Validation

- [ ] Run the full test suite for touched modules:

```bash
python -m pytest tests/codemie/rest_api/security/ tests/codemie/rest_api/routers/test_project_budget_router.py tests/codemie/service/budget/ -v
```

Expected: all tests PASS, no regressions.
