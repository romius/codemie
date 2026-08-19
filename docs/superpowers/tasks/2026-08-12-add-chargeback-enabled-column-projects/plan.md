# chargeback_enabled Column + Customer Config Feature Flag Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `chargeback_enabled` boolean column to the `applications` table with a migration that backfills `true` for projects with active budget assignments, expose it as a read/write API field, and add a YAML-only customer config feature flag so the front end can check whether to display the chargeback indicator.

**Architecture:** DB column lives on `Application` SQLModel (`applications` table, PK = project name). Migration adds `ADD COLUMN chargeback_enabled BOOLEAN NOT NULL DEFAULT false` then backfills via `UPDATE … WHERE id IN (SELECT DISTINCT project_name FROM project_budget_assignments WHERE deleted_at IS NULL)`. Customer config flag (`features:projectChargeback`) is YAML-only — a named constant is added to `CONFIG_IDS` in `customer_config.py`; no env var, no `_get_runtime_config` change; `is_feature_enabled("projectChargeback")` already checks YAML components and defaults to `False` when absent. API exposure adds `chargeback_enabled` to all project response models and `ProjectUpdateRequest`; visibility service dicts and repository/service update methods carry the value through.

**Tech Stack:** Python 3.12, SQLModel/SQLAlchemy, Alembic, FastAPI, Pydantic v2, pytest.

## Global Constraints

- New field on `Application`: `SQLField(default=False, nullable=False)`.
- Migration `down_revision`: `"9b9b4c585e54"` (current main-chain head).
- Migration revision ID: `f1g2h3i4j5k6`.
- Customer config feature key: `"projectChargeback"` → component id `"features:projectChargeback"`.
- No changes to `config.py`. No changes to `_get_runtime_config`.
- No changes to `ProjectCreateRequest` — new projects default to `False`.
- `ApplicationRepository.create()` unchanged — new field picks up `default=False` automatically.
- Run tests: `poetry run pytest <test-file> -v` from repo root.

---

### Task 1: Application model field + Alembic migration

**Files:**
- Modify: `src/codemie/core/models.py:379-393`
- Create: `src/external/alembic/versions/f1g2h3i4j5k6_add_chargeback_enabled_to_applications.py`
- Test: `tests/codemie/core/test_application_model.py` (new file)

**Interfaces:**
- Produces: `Application.chargeback_enabled: bool` with default `False` on all instances.
- Produces: migration that upgrades `applications` table and downgrades cleanly.

- [ ] **Step 1: Write the failing test**

```python
# tests/codemie/core/test_application_model.py
from codemie.core.models import Application


class TestApplicationChargebackEnabled:
    def test_chargeback_enabled_defaults_to_false(self):
        app = Application(id="test-proj", name="test-proj")
        assert app.chargeback_enabled is False

    def test_chargeback_enabled_can_be_set_true(self):
        app = Application(id="test-proj", name="test-proj", chargeback_enabled=True)
        assert app.chargeback_enabled is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie/core/test_application_model.py -v
```
Expected: `AttributeError` — `chargeback_enabled` not on `Application`

- [ ] **Step 3: Add field to `Application` model**

In `src/codemie/core/models.py`, add after the `deleted_at` line (line 393):

```python
    chargeback_enabled: bool = SQLField(default=False, nullable=False)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
poetry run pytest tests/codemie/core/test_application_model.py -v
```
Expected: 2 PASS

- [ ] **Step 5: Create migration file**

```python
# src/external/alembic/versions/f1g2h3i4j5k6_add_chargeback_enabled_to_applications.py
"""add_chargeback_enabled_to_applications

Revision ID: f1g2h3i4j5k6
Revises: 9b9b4c585e54
Create Date: 2026-08-12 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1g2h3i4j5k6"
down_revision: Union[str, None] = "9b9b4c585e54"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column(
            "chargeback_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE applications
        SET chargeback_enabled = true
        WHERE id IN (
            SELECT DISTINCT project_name
            FROM project_budget_assignments
            WHERE deleted_at IS NULL
        )
        """
    )


def downgrade() -> None:
    op.drop_column("applications", "chargeback_enabled")
```

- [ ] **Step 6: Commit**

```bash
git add src/codemie/core/models.py \
        src/external/alembic/versions/f1g2h3i4j5k6_add_chargeback_enabled_to_applications.py \
        tests/codemie/core/test_application_model.py
git commit -m "feat(EPMCDME-14086): add chargeback_enabled column to Application model and migration"
```

---

### Task 2: Customer config feature flag constant

**Files:**
- Modify: `src/codemie/configs/customer_config.py:24-30` (`CONFIG_IDS` dict)
- Test: `tests/codemie/configs/test_customer_config_chargeback.py` (new file)

**Interfaces:**
- Produces: `CONFIG_IDS["projectChargeback"] == "features:projectChargeback"`.
- Produces: `customer_config.is_feature_enabled("projectChargeback")` returns `False` when the YAML component is absent (existing default behaviour of `is_component_enabled`).

- [ ] **Step 1: Write the failing test**

```python
# tests/codemie/configs/test_customer_config_chargeback.py
from codemie.configs.customer_config import CONFIG_IDS


class TestProjectChargebackConfigKey:
    def test_project_chargeback_key_registered(self):
        assert "projectChargeback" in CONFIG_IDS
        assert CONFIG_IDS["projectChargeback"] == "features:projectChargeback"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie/configs/test_customer_config_chargeback.py -v
```
Expected: `AssertionError` — key not in `CONFIG_IDS`

- [ ] **Step 3: Add entry to `CONFIG_IDS`**

In `src/codemie/configs/customer_config.py`, update `CONFIG_IDS`:

```python
CONFIG_IDS = {
    "enterpriseEdition": "features:enterpriseEdition",
    "userManagement": "features:userManagement",
    "idpProvider": "idpProvider",
    "mcpAuthOrigin": "mcpAuthOrigin",
    "chatContextualNaming": "features:chatContextualNaming",
    "projectChargeback": "features:projectChargeback",
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
poetry run pytest tests/codemie/configs/test_customer_config_chargeback.py -v
```
Expected: 1 PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/configs/customer_config.py \
        tests/codemie/configs/test_customer_config_chargeback.py
git commit -m "feat(EPMCDME-14086): register projectChargeback customer config feature flag key"
```

---

### Task 3: Service and repository layers

**Files:**
- Modify: `src/codemie/repository/application_repository.py:692-714`
- Modify: `src/codemie/service/project/project_service.py:173-210`
- Modify: `src/codemie/service/project/project_visibility_service.py:106-119` (list dict) and `184-196` (detail dict)
- Test: `tests/codemie/service/project/test_project_service_chargeback.py` (new file)

**Interfaces:**
- Consumes: `Application.chargeback_enabled` (Task 1).
- Produces: `ApplicationRepository.update_project(..., chargeback_enabled: bool | None = None)`.
- Produces: `ProjectService.update_project(..., chargeback_enabled: bool | None = None)`.
- Produces: both visibility service enriched dicts include `"chargeback_enabled": project.chargeback_enabled`.

- [ ] **Step 1: Write the failing test**

```python
# tests/codemie/service/project/test_project_service_chargeback.py
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


class TestProjectServiceChargebackEnabled:
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_project_passes_chargeback_enabled_to_repository(
        self, mock_get_session, mock_app_repo
    ):
        from codemie.service.project.project_service import ProjectService

        mock_session = MagicMock()

        @contextmanager
        def _ctx():
            yield mock_session

        mock_get_session.return_value = _ctx()

        mock_project = SimpleNamespace(
            id="my-project",
            name="my-project",
            display_name=None,
            description="desc",
            cost_center_id=None,
            chargeback_enabled=False,
        )
        mock_app_repo.update_project.return_value = mock_project

        with patch(
            "codemie.service.project.project_service.ProjectService._get_project_for_update",
            return_value=mock_project,
        ), patch(
            "codemie.service.project.project_service.ProjectService._resolve_updated_name",
            return_value=None,
        ), patch(
            "codemie.service.project.project_service.ProjectService._validate_project_description",
            return_value=None,
        ), patch(
            "codemie.service.project.project_service.ProjectService._resolve_updated_display_name",
            return_value=None,
        ), patch(
            "codemie.service.project.project_service.ProjectService._resolve_updated_cost_center_id",
            return_value=None,
        ), patch(
            "codemie.service.project.project_service.SettingsService"
        ):
            ProjectService.update_project(
                user=MagicMock(id="admin-1", is_admin=True),
                project_name="my-project",
                chargeback_enabled=True,
            )

        call_kwargs = mock_app_repo.update_project.call_args.kwargs
        assert call_kwargs.get("chargeback_enabled") is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie/service/project/test_project_service_chargeback.py -v
```
Expected: `AssertionError` — `chargeback_enabled` not in call kwargs

- [ ] **Step 3: Update `ApplicationRepository.update_project`**

```python
    def update_project(
        self,
        session: Session,
        application: Application,
        *,
        name: Optional[str] = None,
        display_name: Optional[str] = None,
        description: Optional[str] = None,
        cost_center_id: UUID | None = None,
        chargeback_enabled: bool | None = None,
    ) -> Application:
        """Update mutable project fields."""
        if name is not None:
            application.id = name
            application.name = name
        application.display_name = display_name
        if description is not None:
            application.description = description
        application.cost_center_id = cost_center_id
        if chargeback_enabled is not None:
            application.chargeback_enabled = chargeback_enabled
        application.update_date = datetime.now()
        session.add(application)
        session.flush()
        session.refresh(application)
        return application
```

- [ ] **Step 4: Update `ProjectService.update_project` signature**

Add `chargeback_enabled: bool | None = None` to the signature (after `enforce_member_spend_limits`):

```python
    @classmethod
    def update_project(
        cls,
        user: User,
        project_name: str,
        *,
        name: str | None = None,
        display_name: str | None = None,
        clear_display_name: bool = False,
        description: str | None = None,
        cost_center_id: UUID | None = None,
        clear_cost_center: bool = False,
        enforce_member_spend_limits: bool | None = None,
        chargeback_enabled: bool | None = None,
    ) -> Application:
```

Pass it to the repository call (the `application_repository.update_project(...)` call around line 197):

```python
            project = application_repository.update_project(
                session,
                project,
                name=validated_name,
                display_name=resolved_display_name,
                description=validated_description,
                cost_center_id=resolved_cost_center_id,
                chargeback_enabled=chargeback_enabled,
            )
```

- [ ] **Step 5: Add `chargeback_enabled` to visibility service list dict**

In `src/codemie/service/project/project_visibility_service.py`, in the `list_visible_projects_paginated` enriched dict (around line 106):

```python
            enriched.append(
                {
                    "name": project.name,
                    "display_name": project.display_name,
                    "description": project.description,
                    "project_type": project.project_type,
                    "created_by": project.created_by,
                    "created_at": project.date,
                    "user_count": user_count,
                    "admin_count": admin_count,
                    "counters": entity_counts.get(project.name) if include_counters else None,
                    "cost_center_id": project.cost_center_id,
                    "cost_center_name": cost_center.name if cost_center else None,
                    "chargeback_enabled": project.chargeback_enabled,
                }
            )
```

- [ ] **Step 6: Add `chargeback_enabled` to visibility service detail dict**

In `get_visible_project_with_members` return dict (around line 184):

```python
        return {
            "name": project.name,
            "display_name": project.display_name,
            "description": project.description,
            "project_type": project.project_type,
            "created_by": project.created_by,
            "created_at": project.date,
            "user_count": user_count,
            "admin_count": admin_count,
            "cost_center_id": project.cost_center_id,
            "cost_center_name": cost_center.name if cost_center else None,
            "members": member_list,
            "is_project_admin": bool(current_membership.is_project_admin) if current_membership else False,
            "chargeback_enabled": project.chargeback_enabled,
        }
```

- [ ] **Step 7: Run test to verify it passes**

```bash
poetry run pytest tests/codemie/service/project/test_project_service_chargeback.py -v
```
Expected: 1 PASS

- [ ] **Step 8: Commit**

```bash
git add src/codemie/repository/application_repository.py \
        src/codemie/service/project/project_service.py \
        src/codemie/service/project/project_visibility_service.py \
        tests/codemie/service/project/test_project_service_chargeback.py
git commit -m "feat(EPMCDME-14086): propagate chargeback_enabled through repository, service, and visibility service"
```

---

### Task 4: API layer — response/request models and router handlers

**Files:**
- Modify: `src/codemie/rest_api/routers/projects.py`
  - `ProjectListItem` (line 154) — add field
  - `ProjectDetailResponse` (line 213) — add field
  - `ProjectCreateResponse` (line 239) — add field
  - `ProjectUpdateRequest` (line 251) + `validate_non_empty` validator — add field
  - `create_project` handler (~line 664) — return `project.chargeback_enabled`
  - `_build_project_detail_response` (~line 810) — read from dict
  - `update_project` handler (~line 989) — pass payload field + return from project
- Test: add `TestChargebackEnabledField` class to `tests/codemie/rest_api/routers/test_projects_router.py`

**Interfaces:**
- Consumes: `ProjectService.update_project(..., chargeback_enabled)` (Task 3).
- Consumes: visibility service dicts with `"chargeback_enabled"` key (Task 3).
- Produces: all project response models include `chargeback_enabled: bool = False`.
- Produces: `ProjectUpdateRequest` accepts `chargeback_enabled: Optional[bool] = None`.

- [ ] **Step 1: Write the failing tests**

Add at end of `tests/codemie/rest_api/routers/test_projects_router.py`:

```python
class TestChargebackEnabledField:
    @patch("codemie.rest_api.routers.projects.SettingsService")
    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    @patch("codemie.rest_api.routers.projects._resolve_cost_center_name")
    def test_create_project_returns_chargeback_enabled(
        self,
        mock_resolve_cost_center_name,
        mock_project_service,
        mock_config,
        mock_settings_service,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_resolve_cost_center_name.return_value = None
        mock_settings_service.get_enforce_member_spend_limits.return_value = False
        mock_project_service.create_shared_project.return_value = SimpleNamespace(
            name="billing-proj",
            display_name=None,
            description="desc",
            project_type="shared",
            created_by="user-1",
            date=datetime(2026, 2, 10, tzinfo=UTC),
            chargeback_enabled=True,
        )

        response = create_project(
            payload=ProjectCreateRequest(name="billing-proj", description="desc"),
            user=MagicMock(id="user-1"),
        )

        assert response.chargeback_enabled is True

    @patch("codemie.rest_api.routers.projects.SettingsService")
    @patch("codemie.rest_api.routers.projects.config")
    @patch("codemie.rest_api.routers.projects.project_service")
    @patch("codemie.rest_api.routers.projects._resolve_cost_center_name")
    def test_update_project_passes_chargeback_enabled_to_service(
        self,
        mock_resolve_cost_center_name,
        mock_project_service,
        mock_config,
        mock_settings_service,
    ):
        mock_config.ENABLE_USER_MANAGEMENT = True
        mock_resolve_cost_center_name.return_value = None
        mock_settings_service.get_enforce_member_spend_limits.return_value = False
        mock_project_service.update_project.return_value = SimpleNamespace(
            name="billing-proj",
            display_name=None,
            description="desc",
            project_type="shared",
            created_by="user-1",
            date=datetime(2026, 2, 10, tzinfo=UTC),
            chargeback_enabled=True,
        )

        update_project(
            payload=ProjectUpdateRequest(chargeback_enabled=True),
            project_name="billing-proj",
            user=MagicMock(id="user-1"),
        )

        call_kwargs = mock_project_service.update_project.call_args.kwargs
        assert call_kwargs.get("chargeback_enabled") is True

    def test_update_request_accepts_chargeback_enabled_alone(self):
        payload = ProjectUpdateRequest(chargeback_enabled=False)
        assert payload.chargeback_enabled is False

    def test_project_list_item_includes_chargeback_enabled(self):
        item = ProjectListItem(
            name="p", project_type="shared", user_count=0, admin_count=0,
            chargeback_enabled=True,
        )
        assert item.chargeback_enabled is True

    def test_project_detail_response_includes_chargeback_enabled(self):
        resp = ProjectDetailResponse(
            name="p", project_type="shared", user_count=0, admin_count=0,
            members=[], chargeback_enabled=True,
        )
        assert resp.chargeback_enabled is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_projects_router.py::TestChargebackEnabledField -v
```
Expected: multiple failures

- [ ] **Step 3: Add `chargeback_enabled: bool = False` to `ProjectListItem`**

```python
class ProjectListItem(BaseModel):
    """Project list response item with member counts (Story 16)"""

    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    project_type: str
    created_by: Optional[str] = None
    user_count: int
    admin_count: int
    created_at: Optional[datetime] = None
    counters: Optional[ProjectCounters] = None
    cost_center_id: Optional[UUID] = None
    cost_center_name: Optional[str] = None
    spending: Optional[ProjectSpendingSummary] = None
    budgets: Optional[list[ProjectAssignedBudgetSummary]] = None
    chargeback_enabled: bool = False
```

- [ ] **Step 4: Add `chargeback_enabled: bool = False` to `ProjectDetailResponse`**

```python
class ProjectDetailResponse(BaseModel):
    """Project detail response with member list (Story 16)"""

    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    project_type: str
    created_by: Optional[str] = None
    user_count: int
    admin_count: int
    created_at: Optional[datetime] = None
    cost_center_id: Optional[UUID] = None
    cost_center_name: Optional[str] = None
    enforce_member_spend_limits: bool = False
    chargeback_enabled: bool = False
    members: list[ProjectMember]
    spending: Optional[ProjectSpendingDetail] = None
    spending_widget: Optional[ProjectSpendingWidget] = None
```

- [ ] **Step 5: Add `chargeback_enabled: bool = False` to `ProjectCreateResponse`**

```python
class ProjectCreateResponse(BaseModel):
    name: str
    display_name: Optional[str] = None
    description: str
    project_type: str
    created_by: str
    created_at: datetime
    cost_center_id: Optional[UUID] = None
    cost_center_name: Optional[str] = None
    enforce_member_spend_limits: bool = False
    chargeback_enabled: bool = False
```

- [ ] **Step 6: Add `chargeback_enabled: Optional[bool] = None` to `ProjectUpdateRequest` and update validator**

```python
class ProjectUpdateRequest(BaseModel):
    name: Optional[str] = None
    display_name: Optional[str] = None
    clear_display_name: bool = False
    description: Optional[str] = None
    cost_center_id: Optional[UUID] = None
    clear_cost_center: bool = False
    enforce_member_spend_limits: Optional[bool] = None
    chargeback_enabled: Optional[bool] = None

    @model_validator(mode="after")
    def validate_non_empty(self):
        if (
            self.name is None
            and self.display_name is None
            and self.description is None
            and self.cost_center_id is None
            and self.enforce_member_spend_limits is None
            and self.chargeback_enabled is None
            and not self.clear_cost_center
            and not self.clear_display_name
        ):
            raise ValueError("At least one mutable field must be provided")
        if self.cost_center_id is not None and self.clear_cost_center:
            raise ValueError("Provide either cost_center_id or clear_cost_center")
        if self.display_name is not None and self.clear_display_name:
            raise ValueError("Provide either display_name or clear_display_name")
        return self
```

- [ ] **Step 7: Update `create_project` handler return**

Add `chargeback_enabled=project.chargeback_enabled` to the `ProjectCreateResponse(...)` call (~line 664):

```python
    return ProjectCreateResponse(
        name=project.name,
        display_name=project.display_name,
        description=project.description or payload.description,
        project_type=project.project_type,
        created_by=project.created_by or user.id,
        created_at=project.date,
        cost_center_id=getattr(project, "cost_center_id", None),
        cost_center_name=_resolve_cost_center_name(getattr(project, "cost_center_id", None)),
        enforce_member_spend_limits=SettingsService.get_enforce_member_spend_limits(project.name),
        chargeback_enabled=project.chargeback_enabled,
    )
```

- [ ] **Step 8: Update `_build_project_detail_response`**

Add `chargeback_enabled=project_detail.get("chargeback_enabled", False)` (~line 810):

```python
def _build_project_detail_response(project_detail: dict, project_name: str) -> ProjectDetailResponse:
    return ProjectDetailResponse(
        name=project_detail["name"],
        display_name=project_detail.get("display_name"),
        description=project_detail["description"],
        project_type=project_detail["project_type"],
        created_by=project_detail["created_by"],
        created_at=project_detail["created_at"],
        user_count=project_detail["user_count"],
        admin_count=project_detail["admin_count"],
        cost_center_id=project_detail.get("cost_center_id"),
        cost_center_name=project_detail.get("cost_center_name"),
        enforce_member_spend_limits=SettingsService.get_enforce_member_spend_limits(project_name),
        chargeback_enabled=project_detail.get("chargeback_enabled", False),
        members=[ProjectMember(**m) for m in project_detail["members"]],
    )
```

- [ ] **Step 9: Update `update_project` handler**

Pass `chargeback_enabled=payload.chargeback_enabled` to service call (~line 989) and add `chargeback_enabled=project.chargeback_enabled` to return (~line 1001):

```python
    project = project_service.update_project(
        user=user,
        project_name=project_name,
        name=payload.name,
        display_name=payload.display_name,
        clear_display_name=payload.clear_display_name,
        description=payload.description,
        cost_center_id=None if payload.clear_cost_center else payload.cost_center_id,
        clear_cost_center=payload.clear_cost_center,
        enforce_member_spend_limits=payload.enforce_member_spend_limits,
        chargeback_enabled=payload.chargeback_enabled,
    )

    return ProjectCreateResponse(
        name=project.name,
        display_name=project.display_name,
        description=project.description or "",
        project_type=project.project_type,
        created_by=project.created_by or user.id,
        created_at=project.date or datetime.now(UTC),
        cost_center_id=getattr(project, "cost_center_id", None),
        cost_center_name=_resolve_cost_center_name(getattr(project, "cost_center_id", None)),
        enforce_member_spend_limits=SettingsService.get_enforce_member_spend_limits(project.name),
        chargeback_enabled=project.chargeback_enabled,
    )
```

- [ ] **Step 10: Run new tests to verify they pass**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_projects_router.py::TestChargebackEnabledField -v
```
Expected: 5 PASS

- [ ] **Step 11: Run full router test suite for regressions**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_projects_router.py -v
```
Expected: all existing tests PASS

- [ ] **Step 12: Commit**

```bash
git add src/codemie/rest_api/routers/projects.py \
        tests/codemie/rest_api/routers/test_projects_router.py
git commit -m "feat(EPMCDME-14086): expose chargeback_enabled in project API response/request models"
```
