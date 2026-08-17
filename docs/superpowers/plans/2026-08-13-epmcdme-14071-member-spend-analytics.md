# Per-Project Member Spend Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two analytics endpoints returning per-project spend for a user and per-member spend for a project, backed by existing `member_budget` spend rows with lazy global refresh from LiteLLM.

**Architecture:** A new `member_spend_service` mirrors the existing `budget_usage_service` lazy-refresh pattern: read `member_budget` rows from `project_spend_tracking`, check staleness against the newest such row table-wide, refresh globally from LiteLLM's bulk `/customer/list` when stale, and fall back to DB rows on any provider failure. Rows are projected from membership (`user_projects`) with spend and allocation limits left-joined on, so sparse spend data never makes projects or members disappear.

**Tech Stack:** Python 3.11, FastAPI, SQLModel/SQLAlchemy async, PostgreSQL, pytest + pytest-asyncio, Ruff.

**Spec:** `docs/superpowers/specs/2026-08-13-epmcdme-14071-member-spend-analytics-design.md`

## Global Constraints

- Categories are exactly the `BudgetCategory` enum values: `platform`, `cli`, `premium_models` (`src/codemie/service/budget/budget_enums.py:20-23`). Row keys must match these strings exactly.
- Columns must be **generated from the enum**, never hardcoded, so a fourth category needs no frontend release.
- Money columns carry `format: "currency"`. Amounts are JSON numbers, full precision, never preformatted strings.
- Zero spend returns `0`. A missing limit returns `null` — **never `0`**, which renders as fully consumed.
- Empty results return `200` with `rows: []`, never `404`.
- No `total` field in these tables. Categories only.
- Never fail a request because LiteLLM is unavailable — log and serve stale DB data.
- Every new file starts with the Apache 2.0 EPAM copyright header copied verbatim from `src/codemie/service/analytics/handlers/budget_usage_service.py:1-13`.
- Do not modify `ProjectMemberBudgetAssignment.spend` or `.last_synced_at` — vestigial columns, out of scope.
- Commit messages are prefixed `EPMCDME-14071:` and end with the two-line AI attribution used by this repo.
- **Never run the full test suite.** Run only the specific test files named in your task. `make ruff` is fine; a bare `pytest` over `tests/` is not.

---

### Task 1: Repository read methods for member spend

Three new methods on `ProjectSpendTrackingRepository`. Everything downstream depends on these, so they land first.

**Files:**
- Modify: `src/codemie/repository/project_spend_tracking_repository.py` (add after `get_latest_by_budget_ids`, which ends at line 595)
- Test: `tests/codemie/repository/test_project_spend_tracking_repository.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces, all on the existing singleton-instantiable `ProjectSpendTrackingRepository`:
  - `async get_latest_member_rows_for_user(session: AsyncSession, user_id: str) -> dict[tuple[str, str], ProjectSpendTracking]` — keyed `(project_name, budget_category)`
  - `async get_latest_member_rows_for_project(session: AsyncSession, project_name: str) -> dict[tuple[str, str], ProjectSpendTracking]` — keyed `(user_id, budget_category)`
  - `async get_newest_member_spend_date(session: AsyncSession) -> datetime | None` — table-wide max, `None` when no rows exist

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/repository/test_project_spend_tracking_repository.py`. Follow the existing async-session mocking style in that file.

```python
class TestGetLatestMemberRowsForUser:
    """Tests for get_latest_member_rows_for_user."""

    @pytest.mark.asyncio
    async def test_returns_empty_dict_when_no_rows(self):
        repo = ProjectSpendTrackingRepository()
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=result_mock)

        result = await repo.get_latest_member_rows_for_user(session, "u-1")

        assert result == {}

    @pytest.mark.asyncio
    async def test_keys_rows_by_project_and_category(self):
        repo = ProjectSpendTrackingRepository()
        row = ProjectSpendTracking(
            id=uuid4(),
            project_name="atlas-core",
            spend_date=datetime(2026, 8, 13, tzinfo=timezone.utc),
            daily_spend=Decimal("1.0"),
            cumulative_spend=Decimal("5.0"),
            budget_period_spend=Decimal("4.0"),
            budget_id="b-1",
            budget_category="cli",
            user_id="u-1",
            spend_subject_type="member_budget",
        )
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [row]
        session.execute = AsyncMock(return_value=result_mock)

        result = await repo.get_latest_member_rows_for_user(session, "u-1")

        assert result == {("atlas-core", "cli"): row}


class TestGetLatestMemberRowsForProject:
    """Tests for get_latest_member_rows_for_project."""

    @pytest.mark.asyncio
    async def test_keys_rows_by_user_and_category(self):
        repo = ProjectSpendTrackingRepository()
        row = ProjectSpendTracking(
            id=uuid4(),
            project_name="atlas-core",
            spend_date=datetime(2026, 8, 13, tzinfo=timezone.utc),
            daily_spend=Decimal("1.0"),
            cumulative_spend=Decimal("5.0"),
            budget_period_spend=Decimal("4.0"),
            budget_id="b-1",
            budget_category="platform",
            user_id="u-7",
            spend_subject_type="member_budget",
        )
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [row]
        session.execute = AsyncMock(return_value=result_mock)

        result = await repo.get_latest_member_rows_for_project(session, "atlas-core")

        assert result == {("u-7", "platform"): row}


class TestGetNewestMemberSpendDate:
    """Tests for get_newest_member_spend_date."""

    @pytest.mark.asyncio
    async def test_returns_none_when_table_empty(self):
        repo = ProjectSpendTrackingRepository()
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result_mock)

        assert await repo.get_newest_member_spend_date(session) is None

    @pytest.mark.asyncio
    async def test_returns_max_spend_date(self):
        expected = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
        repo = ProjectSpendTrackingRepository()
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = expected
        session.execute = AsyncMock(return_value=result_mock)

        assert await repo.get_newest_member_spend_date(session) == expected
```

Ensure the test module imports `uuid4`, `Decimal`, `datetime`, `timezone`, `MagicMock`, `AsyncMock`, `pytest`, `ProjectSpendTrackingRepository`, and `ProjectSpendTracking`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/repository/test_project_spend_tracking_repository.py -k "MemberRows or NewestMemberSpendDate" -v`
Expected: FAIL with `AttributeError: 'ProjectSpendTrackingRepository' object has no attribute 'get_latest_member_rows_for_user'`

- [ ] **Step 3: Implement the three methods**

Insert into `src/codemie/repository/project_spend_tracking_repository.py` after `get_latest_by_budget_ids`. The latest-per-group pattern mirrors `get_latest_before_by_member_budget_ids` at line 236.

```python
    async def get_latest_member_rows_for_user(
        self,
        session: AsyncSession,
        user_id: str,
    ) -> dict[tuple[str, str], ProjectSpendTracking]:
        """Return the most recent member_budget row per (project_name, budget_category) for a user.

        Rows are sparse: the collector skips zero-delta snapshots, so a missing key
        means "spend unchanged since the last recorded snapshot", not "no spend".
        """
        latest_subq = (
            select(
                ProjectSpendTracking.project_name,
                ProjectSpendTracking.budget_category,
                func.max(ProjectSpendTracking.spend_date).label("max_spend_date"),
            )
            .where(ProjectSpendTracking.user_id == user_id)
            .where(ProjectSpendTracking.spend_subject_type == "member_budget")
            .group_by(ProjectSpendTracking.project_name, ProjectSpendTracking.budget_category)
            .subquery()
        )
        stmt = (
            select(ProjectSpendTracking)
            .join(
                latest_subq,
                (ProjectSpendTracking.project_name == latest_subq.c.project_name)
                & (ProjectSpendTracking.budget_category == latest_subq.c.budget_category)
                & (ProjectSpendTracking.spend_date == latest_subq.c.max_spend_date),
            )
            .where(ProjectSpendTracking.user_id == user_id)
            .where(ProjectSpendTracking.spend_subject_type == "member_budget")
        )
        result = await session.execute(stmt)
        return {(row.project_name, row.budget_category): row for row in result.scalars().all()}

    async def get_latest_member_rows_for_project(
        self,
        session: AsyncSession,
        project_name: str,
    ) -> dict[tuple[str, str], ProjectSpendTracking]:
        """Return the most recent member_budget row per (user_id, budget_category) for a project."""
        latest_subq = (
            select(
                ProjectSpendTracking.user_id,
                ProjectSpendTracking.budget_category,
                func.max(ProjectSpendTracking.spend_date).label("max_spend_date"),
            )
            .where(ProjectSpendTracking.project_name == project_name)
            .where(ProjectSpendTracking.spend_subject_type == "member_budget")
            .group_by(ProjectSpendTracking.user_id, ProjectSpendTracking.budget_category)
            .subquery()
        )
        stmt = (
            select(ProjectSpendTracking)
            .join(
                latest_subq,
                (ProjectSpendTracking.user_id == latest_subq.c.user_id)
                & (ProjectSpendTracking.budget_category == latest_subq.c.budget_category)
                & (ProjectSpendTracking.spend_date == latest_subq.c.max_spend_date),
            )
            .where(ProjectSpendTracking.project_name == project_name)
            .where(ProjectSpendTracking.spend_subject_type == "member_budget")
        )
        result = await session.execute(stmt)
        return {(row.user_id, row.budget_category): row for row in result.scalars().all()}

    async def get_newest_member_spend_date(self, session: AsyncSession) -> datetime | None:
        """Return the newest spend_date across all member_budget rows, or None if there are none.

        This is the lazy-refresh freshness marker. It is deliberately table-wide rather
        than per-slice: the refresh is global, so the newest row anywhere answers
        "when did we last check?". A per-slice max would read as permanently stale for
        members with no recent spend, since zero-delta rows are never written.
        """
        stmt = select(func.max(ProjectSpendTracking.spend_date)).where(
            ProjectSpendTracking.spend_subject_type == "member_budget"
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
```

Verify `func` and `datetime` are already imported in this module; add them to the existing import block if not.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/repository/test_project_spend_tracking_repository.py -k "MemberRows or NewestMemberSpendDate" -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Lint**

Run: `make ruff`
Expected: no findings in the changed files. If it fails with `poetry: No such file or directory`, load `.ai-run/guides/development/setup-guide.md` § Claude Code Stop Hook.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/repository/project_spend_tracking_repository.py tests/codemie/repository/test_project_spend_tracking_repository.py
git commit -m "EPMCDME-14071: Add member_budget spend read methods to tracking repository"
```

---

### Task 2: Staleness config

**Files:**
- Modify: `src/codemie/configs/config.py` (beside `BUDGET_USAGE_STALENESS_THRESHOLD_MS` at line 750)

**Interfaces:**
- Consumes: nothing.
- Produces: `config.MEMBER_SPEND_STALENESS_THRESHOLD_MS: int`, default `600000`.

- [ ] **Step 1: Add the setting**

```python
    MEMBER_SPEND_STALENESS_THRESHOLD_MS: int = 600000  # 10 minutes — lazy-refresh threshold for member spend analytics
```

- [ ] **Step 2: Verify it loads**

Run: `poetry run python -c "from codemie.configs.config import config; print(config.MEMBER_SPEND_STALENESS_THRESHOLD_MS)"`
Expected: `600000`

- [ ] **Step 3: Commit**

```bash
git add src/codemie/configs/config.py
git commit -m "EPMCDME-14071: Add MEMBER_SPEND_STALENESS_THRESHOLD_MS config"
```

---

### Task 3: Row builders and column generation

Pure functions, no I/O. Written and tested standalone so the service task can focus on orchestration.

**Files:**
- Create: `src/codemie/service/analytics/handlers/member_spend_service.py`
- Test: `tests/codemie/service/analytics/handlers/test_member_spend_service.py`

**Interfaces:**
- Consumes: `BudgetCategory` from `codemie.service.budget.budget_enums`.
- Produces (module-level, all importable by later tasks):
  - `_category_columns(key_column_id: str, key_column_label: str) -> list[dict]`
  - `_spend_value(spend_row: Any | None) -> float`
  - `_limit_value(allocation: Any | None) -> float | None`
  - `_build_row(key_values: dict[str, Any], spend_by_category: dict[str, Any], allocation_by_category: dict[str, Any]) -> dict[str, Any]`

- [ ] **Step 1: Write the failing tests**

Create `tests/codemie/service/analytics/handlers/test_member_spend_service.py` with the Apache header, then:

```python
"""Unit tests for member_spend_service helpers and MemberSpendService."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from codemie.service.analytics.handlers.member_spend_service import (
    MemberSpendService,
    _build_row,
    _category_columns,
    _limit_value,
    _spend_value,
)
from codemie.service.spend_tracking.spend_models import ProjectSpendTracking


def _member_row(project_name: str, category: str, user_id: str, spend: str, spend_date: datetime):
    return ProjectSpendTracking(
        id=uuid4(),
        project_name=project_name,
        spend_date=spend_date,
        daily_spend=Decimal("0"),
        cumulative_spend=Decimal(spend),
        budget_period_spend=Decimal(spend),
        budget_id="b-1",
        budget_category=category,
        user_id=user_id,
        spend_subject_type="member_budget",
    )


class TestCategoryColumns:
    """Tests for _category_columns."""

    def test_first_column_is_the_key_column(self):
        columns = _category_columns("project_name", "Project")
        assert columns[0] == {
            "id": "project_name",
            "label": "Project",
            "type": "string",
            "format": None,
        }

    def test_emits_one_currency_column_per_budget_category(self):
        columns = _category_columns("user_id", "User")
        ids = [c["id"] for c in columns[1:]]
        assert ids == ["platform", "cli", "premium_models"]
        assert all(c["type"] == "number" for c in columns[1:])
        assert all(c["format"] == "currency" for c in columns[1:])

    def test_no_total_column(self):
        assert "total" not in [c["id"] for c in _category_columns("user_id", "User")]


class TestSpendValue:
    """Tests for _spend_value."""

    def test_missing_row_is_zero_not_none(self):
        assert _spend_value(None) == 0

    def test_reads_budget_period_spend_as_float(self):
        row = _member_row("p", "cli", "u", "12.50", datetime.now(timezone.utc))
        assert _spend_value(row) == 12.5


class TestLimitValue:
    """Tests for _limit_value."""

    def test_missing_allocation_is_none_not_zero(self):
        assert _limit_value(None) is None

    def test_reads_allocated_max_budget(self):
        assert _limit_value(SimpleNamespace(allocated_max_budget=500.0)) == 500.0

    def test_zero_limit_is_preserved_as_zero(self):
        assert _limit_value(SimpleNamespace(allocated_max_budget=0.0)) == 0.0


class TestBuildRow:
    """Tests for _build_row."""

    def test_includes_key_values_verbatim(self):
        row = _build_row({"project_name": "atlas-core", "display_name": None}, {}, {})
        assert row["project_name"] == "atlas-core"
        assert row["display_name"] is None

    def test_absent_categories_render_as_zero_spend_and_null_limit(self):
        row = _build_row({"user_id": "u-1"}, {}, {})
        assert row["platform"] == 0
        assert row["cli"] == 0
        assert row["premium_models"] == 0
        assert row["platform_limit"] is None
        assert row["cli_limit"] is None
        assert row["premium_models_limit"] is None

    def test_populates_spend_and_limit_per_category(self):
        spend = {"cli": _member_row("atlas-core", "cli", "u-1", "40.00", datetime.now(timezone.utc))}
        allocations = {"cli": SimpleNamespace(allocated_max_budget=100.0)}
        row = _build_row({"user_id": "u-1"}, spend, allocations)
        assert row["cli"] == 40.0
        assert row["cli_limit"] == 100.0
        assert row["platform"] == 0
        assert row["platform_limit"] is None

    def test_no_total_key(self):
        assert "total" not in _build_row({"user_id": "u-1"}, {}, {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/analytics/handlers/test_member_spend_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codemie.service.analytics.handlers.member_spend_service'`

- [ ] **Step 3: Create the module with the pure helpers**

Create `src/codemie/service/analytics/handlers/member_spend_service.py`. Start with the Apache 2.0 EPAM header copied verbatim from `budget_usage_service.py:1-13`, then:

```python
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from codemie.service.budget.budget_enums import BudgetCategory

logger = logging.getLogger(__name__)

_CATEGORY_LABELS = {
    BudgetCategory.PLATFORM.value: "Platform",
    BudgetCategory.CLI.value: "CLI",
    BudgetCategory.PREMIUM_MODELS.value: "Premium models",
}


def _category_columns(key_column_id: str, key_column_label: str) -> list[dict]:
    """Build the column descriptors: one key column followed by one column per budget category.

    Generated from BudgetCategory rather than hardcoded so a new category appears in
    both columns[] and the rows without a frontend release.
    """
    columns: list[dict] = [
        {"id": key_column_id, "label": key_column_label, "type": "string", "format": None}
    ]
    for category in BudgetCategory:
        columns.append(
            {
                "id": category.value,
                "label": _CATEGORY_LABELS.get(category.value, category.value),
                "type": "number",
                "format": "currency",
            }
        )
    return columns


def _spend_value(spend_row: Any | None) -> float:
    """Return the row's current-period spend, or 0 when no snapshot exists.

    Absent rows mean "no recorded spend", which must render as 0 rather than being
    omitted — omitting it would make the project or member disappear from the table.
    """
    if spend_row is None:
        return 0
    return float(spend_row.budget_period_spend)


def _limit_value(allocation: Any | None) -> float | None:
    """Return the allocated max budget, or None when no allocation is configured.

    None means "no limit configured". Never substitute 0 — 0 is a real limit and
    would render as fully consumed.
    """
    if allocation is None:
        return None
    return allocation.allocated_max_budget


def _build_row(
    key_values: dict[str, Any],
    spend_by_category: dict[str, Any],
    allocation_by_category: dict[str, Any],
) -> dict[str, Any]:
    """Build one table row: key columns, then per-category spend and limit."""
    row: dict[str, Any] = dict(key_values)
    for category in BudgetCategory:
        name = category.value
        row[name] = _spend_value(spend_by_category.get(name))
        row[f"{name}_limit"] = _limit_value(allocation_by_category.get(name))
    return row
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/analytics/handlers/test_member_spend_service.py -v`
Expected: PASS (12 tests). The `MemberSpendService` import will fail — comment out that one import line temporarily, or add the empty class stub from Task 4 Step 3 now.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/analytics/handlers/member_spend_service.py tests/codemie/service/analytics/handlers/test_member_spend_service.py
git commit -m "EPMCDME-14071: Add member spend row builders and category column generation"
```

---

### Task 4: Lazy refresh orchestration

**Files:**
- Modify: `src/codemie/service/analytics/handlers/member_spend_service.py`
- Test: `tests/codemie/service/analytics/handlers/test_member_spend_service.py`

**Interfaces:**
- Consumes: Task 1's three repository methods; Task 2's config value; Task 3's `_build_row` and `_category_columns`.
- Produces:
  - `class MemberSpendService` with `_needs_refresh(newest_spend_date: datetime | None) -> bool`, `_is_litellm_enabled() -> bool` (static), `_refresh_all_member_spend(session: AsyncSession) -> None`, `_ensure_fresh(session: AsyncSession) -> None`
  - Module singleton `member_spend_service = MemberSpendService()`

- [ ] **Step 1: Write the failing tests**

Append to `tests/codemie/service/analytics/handlers/test_member_spend_service.py`:

```python
class TestNeedsRefresh:
    """Tests for MemberSpendService._needs_refresh."""

    def test_empty_table_needs_refresh(self):
        assert MemberSpendService()._needs_refresh(None) is True

    def test_recent_snapshot_does_not_need_refresh(self):
        recent = datetime.now(timezone.utc) - timedelta(minutes=2)
        assert MemberSpendService()._needs_refresh(recent) is False

    def test_old_snapshot_needs_refresh(self):
        old = datetime.now(timezone.utc) - timedelta(hours=3)
        assert MemberSpendService()._needs_refresh(old) is True

    def test_naive_datetime_is_treated_as_utc(self):
        naive_recent = (datetime.now(timezone.utc) - timedelta(minutes=2)).replace(tzinfo=None)
        assert MemberSpendService()._needs_refresh(naive_recent) is False

    def test_quiet_member_does_not_force_refresh_when_table_is_fresh(self):
        """Table-wide freshness, not per-slice: sparse rows must not defeat the TTL."""
        table_fresh = datetime.now(timezone.utc) - timedelta(minutes=1)
        assert MemberSpendService()._needs_refresh(table_fresh) is False


class TestEnsureFresh:
    """Tests for MemberSpendService._ensure_fresh."""

    @pytest.mark.asyncio
    async def test_skips_refresh_when_data_is_fresh(self):
        service = MemberSpendService()
        session = MagicMock()
        fresh = datetime.now(timezone.utc) - timedelta(minutes=1)

        with patch.object(service, "_refresh_all_member_spend", new=AsyncMock()) as refresh, patch(
            "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
            "get_newest_member_spend_date",
            new=AsyncMock(return_value=fresh),
        ):
            await service._ensure_fresh(session)

        refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_refresh_when_litellm_disabled(self):
        service = MemberSpendService()
        session = MagicMock()

        with patch.object(service, "_refresh_all_member_spend", new=AsyncMock()) as refresh, patch.object(
            MemberSpendService, "_is_litellm_enabled", return_value=False
        ), patch(
            "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
            "get_newest_member_spend_date",
            new=AsyncMock(return_value=None),
        ):
            await service._ensure_fresh(session)

        refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_refreshes_when_stale_and_litellm_enabled(self):
        service = MemberSpendService()
        session = MagicMock()
        stale = datetime.now(timezone.utc) - timedelta(hours=3)

        with patch.object(service, "_refresh_all_member_spend", new=AsyncMock()) as refresh, patch.object(
            MemberSpendService, "_is_litellm_enabled", return_value=True
        ), patch(
            "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
            "get_newest_member_spend_date",
            new=AsyncMock(return_value=stale),
        ):
            await service._ensure_fresh(session)

        refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_provider_failure_does_not_raise(self):
        """LiteLLM being unavailable must degrade to stale data, never fail the request."""
        service = MemberSpendService()
        session = MagicMock()
        session.rollback = AsyncMock()
        stale = datetime.now(timezone.utc) - timedelta(hours=3)

        with patch.object(
            service, "_refresh_all_member_spend", new=AsyncMock(side_effect=RuntimeError("litellm down"))
        ), patch.object(MemberSpendService, "_is_litellm_enabled", return_value=True), patch(
            "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
            "get_newest_member_spend_date",
            new=AsyncMock(return_value=stale),
        ):
            await service._ensure_fresh(session)  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/analytics/handlers/test_member_spend_service.py -k "NeedsRefresh or EnsureFresh" -v`
Expected: FAIL with `ImportError: cannot import name 'MemberSpendService'`

- [ ] **Step 3: Implement the service**

Append to `src/codemie/service/analytics/handlers/member_spend_service.py`:

```python
class MemberSpendService:
    """Per-project member spend analytics with lazy global refresh from LiteLLM.

    Mirrors BudgetUsageService: read from the DB, refresh from the provider only when
    the data is stale, and fall back to whatever the DB holds if the provider fails.

    The refresh is global because the upstream call (/customer/list) is global — it
    returns every customer regardless of what was asked for. Writing all of it means
    one admin click warms the cache for every subsequent request.
    """

    async def _ensure_fresh(self, session: AsyncSession) -> None:
        """Refresh all member spend from LiteLLM if the stored data is stale.

        Never raises: a provider failure degrades to serving stale DB rows.
        """
        from codemie.repository.project_spend_tracking_repository import ProjectSpendTrackingRepository

        tracking_repo = ProjectSpendTrackingRepository()
        newest = await tracking_repo.get_newest_member_spend_date(session)

        if not (self._needs_refresh(newest) and self._is_litellm_enabled()):
            logger.info(f"Returning cached member spend from DB (newest snapshot={newest})")
            return

        logger.info(f"Refreshing member spend from LiteLLM (newest snapshot={newest})")
        try:
            await self._refresh_all_member_spend(session)
        except Exception as e:
            logger.warning(f"Member spend refresh failed: {e}. Returning stale DB data.")
            await session.rollback()

    def _needs_refresh(self, newest_spend_date: datetime | None) -> bool:
        """Return True when there is no data at all, or the newest snapshot exceeds the TTL."""
        from codemie.configs.config import config

        if newest_spend_date is None:
            return True
        latest = newest_spend_date
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        age_ms = (datetime.now(timezone.utc) - latest).total_seconds() * 1000
        return age_ms > config.MEMBER_SPEND_STALENESS_THRESHOLD_MS

    @staticmethod
    def _is_litellm_enabled() -> bool:
        try:
            from codemie.enterprise.litellm.dependencies import is_litellm_enabled

            return is_litellm_enabled()
        except ImportError:
            return False

    async def _refresh_all_member_spend(self, session: AsyncSession) -> None:
        """Fetch every member snapshot from LiteLLM and persist the ones whose spend moved.

        Reuses the budget provider's bulk collector and the spend collector's delta helpers
        so the lazy path and the nightly batch job write identical rows.
        """
        from uuid import uuid4

        from codemie.repository.project_spend_tracking_repository import ProjectSpendTrackingRepository
        from codemie.service.analytics.handlers.budget_usage_service import _compute_spend_delta
        from codemie.service.budget.provider_registry import get_active_provider
        from codemie.service.spend_tracking.spend_models import ProjectSpendTracking

        provider = get_active_provider()
        if provider is None:
            logger.info("No budget provider configured; skipping member spend refresh")
            return

        snapshots = await provider.collect_member_budget_spend()
        if not snapshots:
            logger.info("Provider returned no member spend snapshots")
            return

        tracking_repo = ProjectSpendTrackingRepository()
        now = datetime.now(timezone.utc)
        triples = [(s.project_name, s.budget_id, s.user_id) for s in snapshots]
        prev_rows = await tracking_repo.get_latest_before_by_member_budget_ids(session, triples, now)

        rows: list[ProjectSpendTracking] = []
        for snapshot in snapshots:
            prev_row = prev_rows.get((snapshot.project_name, snapshot.budget_id, snapshot.user_id))
            daily_spend, cumulative_spend = _compute_spend_delta(
                snapshot.spend, prev_row, None, now
            )
            rows.append(
                ProjectSpendTracking(
                    id=uuid4(),
                    project_name=snapshot.project_name,
                    spend_date=now,
                    daily_spend=daily_spend,
                    cumulative_spend=(
                        snapshot.cumulative_spend if snapshot.cumulative_spend is not None else cumulative_spend
                    ),
                    budget_period_spend=snapshot.spend,
                    budget_id=snapshot.budget_id,
                    budget_category=snapshot.budget_category.value,
                    user_id=snapshot.user_id,
                    provider_subject_id=snapshot.provider_subject_id,
                    spend_subject_type="member_budget",
                )
            )

        await tracking_repo.insert_member_budget_entries(session, rows)
        logger.info(f"Persisted {len(rows)} member spend rows from LiteLLM")


member_spend_service = MemberSpendService()
```

The provider accessor is `get_active_provider()` from `codemie.service.budget.provider_registry` — the same one the collector uses (`spend_collector_service.py:31,173`). `collect_member_budget_spend()` takes no arguments and performs the bulk `/customer/list` fetch (`budget_provider_adapter.py:1568-1570`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/analytics/handlers/test_member_spend_service.py -v`
Expected: PASS (all tests, including Task 3's)

- [ ] **Step 5: Lint**

Run: `make ruff`
Expected: no findings in changed files.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/analytics/handlers/member_spend_service.py tests/codemie/service/analytics/handlers/test_member_spend_service.py
git commit -m "EPMCDME-14071: Add lazy global refresh for member spend from LiteLLM"
```

---

### Task 5: Public service methods returning columns and rows

**Files:**
- Modify: `src/codemie/service/analytics/handlers/member_spend_service.py`
- Test: `tests/codemie/service/analytics/handlers/test_member_spend_service.py`

**Interfaces:**
- Consumes: Tasks 1, 3, 4.
- Produces, on `MemberSpendService`:
  - `async get_user_project_spend(session: AsyncSession, user_id: str) -> tuple[list[dict], list[dict]]`
  - `async get_project_member_spend(session: AsyncSession, project_name: str) -> tuple[list[dict], list[dict]]`

  Both return `(columns, rows)`, matching `BudgetUsageService.get_budget_usage`'s contract.

- [ ] **Step 1: Write the failing tests**

Append to the service test file:

```python
class TestGetUserProjectSpend:
    """Tests for MemberSpendService.get_user_project_spend."""

    @pytest.mark.asyncio
    async def test_returns_empty_rows_when_user_has_no_projects(self):
        service = MemberSpendService()
        session = MagicMock()

        with patch.object(service, "_ensure_fresh", new=AsyncMock()), patch(
            "codemie.repository.user_project_repository.user_project_repository.aget_by_user_id",
            new=AsyncMock(return_value=[]),
        ):
            columns, rows = await service.get_user_project_spend(session, "u-1")

        assert rows == []
        assert columns[0]["id"] == "project_name"

    @pytest.mark.asyncio
    async def test_project_with_no_spend_still_returns_a_row_of_zeros(self):
        """Membership drives rows; sparse spend must never remove a project."""
        service = MemberSpendService()
        session = MagicMock()
        memberships = [SimpleNamespace(project_name="atlas-core", user_id="u-1")]

        with patch.object(service, "_ensure_fresh", new=AsyncMock()), patch(
            "codemie.repository.user_project_repository.user_project_repository.aget_by_user_id",
            new=AsyncMock(return_value=memberships),
        ), patch(
            "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
            "get_latest_member_rows_for_user",
            new=AsyncMock(return_value={}),
        ), patch(
            "codemie.repository.project_budget_repository.project_member_budget_assignment_repository."
            "get_active_by_user",
            new=AsyncMock(return_value=[]),
        ):
            _, rows = await service.get_user_project_spend(session, "u-1")

        assert len(rows) == 1
        assert rows[0]["project_name"] == "atlas-core"
        assert rows[0]["platform"] == 0
        assert rows[0]["cli"] == 0
        assert rows[0]["platform_limit"] is None

    @pytest.mark.asyncio
    async def test_joins_spend_and_limits_onto_membership_rows(self):
        service = MemberSpendService()
        session = MagicMock()
        memberships = [SimpleNamespace(project_name="atlas-core", user_id="u-1")]
        spend = {("atlas-core", "cli"): _member_row("atlas-core", "cli", "u-1", "40.00", datetime.now(timezone.utc))}
        allocations = [
            SimpleNamespace(
                project_name="atlas-core", budget_category="cli", user_id="u-1", allocated_max_budget=100.0
            )
        ]

        with patch.object(service, "_ensure_fresh", new=AsyncMock()), patch(
            "codemie.repository.user_project_repository.user_project_repository.aget_by_user_id",
            new=AsyncMock(return_value=memberships),
        ), patch(
            "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
            "get_latest_member_rows_for_user",
            new=AsyncMock(return_value=spend),
        ), patch(
            "codemie.repository.project_budget_repository.project_member_budget_assignment_repository."
            "get_active_by_user",
            new=AsyncMock(return_value=allocations),
        ):
            _, rows = await service.get_user_project_spend(session, "u-1")

        assert rows[0]["cli"] == 40.0
        assert rows[0]["cli_limit"] == 100.0
        assert rows[0]["platform"] == 0
        assert rows[0]["platform_limit"] is None


class TestGetProjectMemberSpend:
    """Tests for MemberSpendService.get_project_member_spend."""

    @pytest.mark.asyncio
    async def test_returns_empty_rows_for_project_with_no_members(self):
        service = MemberSpendService()
        session = MagicMock()

        with patch.object(service, "_ensure_fresh", new=AsyncMock()), patch(
            "codemie.repository.user_project_repository.user_project_repository.aget_by_project_name",
            new=AsyncMock(return_value=[]),
        ):
            columns, rows = await service.get_project_member_spend(session, "atlas-core")

        assert rows == []
        assert columns[0]["id"] == "user_id"

    @pytest.mark.asyncio
    async def test_member_with_no_spend_still_returns_a_row(self):
        service = MemberSpendService()
        session = MagicMock()
        members = [SimpleNamespace(user_id="u-7", project_name="atlas-core")]

        with patch.object(service, "_ensure_fresh", new=AsyncMock()), patch(
            "codemie.repository.user_project_repository.user_project_repository.aget_by_project_name",
            new=AsyncMock(return_value=members),
        ), patch(
            "codemie.repository.project_spend_tracking_repository.ProjectSpendTrackingRepository."
            "get_latest_member_rows_for_project",
            new=AsyncMock(return_value={}),
        ), patch(
            "codemie.repository.project_budget_repository.project_member_budget_assignment_repository."
            "get_active_by_project",
            new=AsyncMock(return_value=[]),
        ):
            _, rows = await service.get_project_member_spend(session, "atlas-core")

        assert len(rows) == 1
        assert rows[0]["user_id"] == "u-7"
        assert rows[0]["premium_models"] == 0
        assert rows[0]["premium_models_limit"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/analytics/handlers/test_member_spend_service.py -k "GetUserProjectSpend or GetProjectMemberSpend" -v`
Expected: FAIL with `AttributeError: 'MemberSpendService' object has no attribute 'get_user_project_spend'`

- [ ] **Step 3: Implement the two methods**

Insert into `MemberSpendService`, before `_ensure_fresh`:

```python
    async def get_user_project_spend(
        self,
        session: AsyncSession,
        user_id: str,
    ) -> tuple[list[dict], list[dict]]:
        """Return (columns, rows) — one row per project the user belongs to.

        Rows are projected from user_projects membership, not from spend rows: the
        spend table is sparse, so driving from it would silently drop projects whose
        spend has not changed recently.
        """
        from codemie.repository.project_budget_repository import project_member_budget_assignment_repository
        from codemie.repository.project_spend_tracking_repository import ProjectSpendTrackingRepository
        from codemie.repository.user_project_repository import user_project_repository

        await self._ensure_fresh(session)

        memberships = await user_project_repository.aget_by_user_id(session, user_id)
        columns = _category_columns("project_name", "Project")
        if not memberships:
            return columns, []

        tracking_repo = ProjectSpendTrackingRepository()
        spend_map = await tracking_repo.get_latest_member_rows_for_user(session, user_id)
        allocations = await project_member_budget_assignment_repository.get_active_by_user(session, user_id)

        allocation_map: dict[tuple[str, str], Any] = {
            (a.project_name, a.budget_category): a for a in allocations
        }

        rows = []
        for membership in memberships:
            project_name = membership.project_name
            spend_by_category = {
                category.value: spend_map.get((project_name, category.value)) for category in BudgetCategory
            }
            allocation_by_category = {
                category.value: allocation_map.get((project_name, category.value)) for category in BudgetCategory
            }
            rows.append(
                _build_row(
                    {"project_name": project_name, "display_name": None},
                    spend_by_category,
                    allocation_by_category,
                )
            )
        return columns, rows

    async def get_project_member_spend(
        self,
        session: AsyncSession,
        project_name: str,
    ) -> tuple[list[dict], list[dict]]:
        """Return (columns, rows) — one row per member of the project, keyed by user_id.

        user_id matches the id returned by GET /v1/admin/users, which is the join key
        the frontend uses against the existing member rows.
        """
        from codemie.repository.project_budget_repository import project_member_budget_assignment_repository
        from codemie.repository.project_spend_tracking_repository import ProjectSpendTrackingRepository
        from codemie.repository.user_project_repository import user_project_repository

        await self._ensure_fresh(session)

        members = await user_project_repository.aget_by_project_name(session, project_name)
        columns = _category_columns("user_id", "User")
        if not members:
            return columns, []

        tracking_repo = ProjectSpendTrackingRepository()
        spend_map = await tracking_repo.get_latest_member_rows_for_project(session, project_name)
        allocations = await project_member_budget_assignment_repository.get_active_by_project(session, project_name)

        allocation_map: dict[tuple[str, str], Any] = {
            (a.user_id, a.budget_category): a for a in allocations
        }

        rows = []
        for member in members:
            user_id = member.user_id
            spend_by_category = {
                category.value: spend_map.get((user_id, category.value)) for category in BudgetCategory
            }
            allocation_by_category = {
                category.value: allocation_map.get((user_id, category.value)) for category in BudgetCategory
            }
            rows.append(_build_row({"user_id": user_id}, spend_by_category, allocation_by_category))
        return columns, rows
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/analytics/handlers/test_member_spend_service.py -v`
Expected: PASS

- [ ] **Step 5: Lint**

Run: `make ruff`

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/analytics/handlers/member_spend_service.py tests/codemie/service/analytics/handlers/test_member_spend_service.py
git commit -m "EPMCDME-14071: Add user-project and project-member spend service methods"
```

---

### Task 6: The two endpoints

**Files:**
- Modify: `src/codemie/rest_api/routers/analytics.py` (add after `get_user_budget_usage`, which ends at line 3019)
- Test: `tests/codemie/rest_api/routers/test_analytics_member_spending.py`

**Interfaces:**
- Consumes: Task 5's `member_spend_service`; existing `_resolve_admin_budget_subject` (`analytics.py:2911`), `_authorize_admin_budget_view` (`analytics.py:588`), `ResponseFormatter.format_tabular_response`.
- Produces: `GET /v1/analytics/user-project-spending`, `GET /v1/analytics/project-member-spending`.

- [ ] **Step 1: Write the failing tests**

Create `tests/codemie/rest_api/routers/test_analytics_member_spending.py` with the Apache header. Mirror the fixture and client setup in the sibling `test_analytics_budget_usage_labels.py`.

```python
"""Tests for the member spend analytics endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


class TestUserProjectSpendingEndpoint:
    """Tests for GET /v1/analytics/user-project-spending."""

    @pytest.mark.asyncio
    async def test_returns_200_with_empty_rows_when_user_has_no_projects(self, client, admin_user):
        columns = [{"id": "project_name", "label": "Project", "type": "string", "format": None}]
        with patch(
            "codemie.service.analytics.handlers.member_spend_service.member_spend_service.get_user_project_spend",
            new=AsyncMock(return_value=(columns, [])),
        ):
            response = client.get("/v1/analytics/user-project-spending?users=nobody@example.com")

        assert response.status_code == 200
        assert response.json()["data"]["rows"] == []

    @pytest.mark.asyncio
    async def test_includes_pagination_block(self, client, admin_user):
        columns = [{"id": "project_name", "label": "Project", "type": "string", "format": None}]
        rows = [{"project_name": "atlas-core", "platform": 0, "cli": 0, "premium_models": 0}]
        with patch(
            "codemie.service.analytics.handlers.member_spend_service.member_spend_service.get_user_project_spend",
            new=AsyncMock(return_value=(columns, rows)),
        ):
            response = client.get("/v1/analytics/user-project-spending?users=someone@example.com")

        body = response.json()
        assert body["pagination"]["total_count"] == 1
        assert body["pagination"]["has_more"] is False

    @pytest.mark.asyncio
    async def test_requires_users_param(self, client, admin_user):
        response = client.get("/v1/analytics/user-project-spending")
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_non_admin_caller_is_forbidden(self, client, plain_user):
        response = client.get("/v1/analytics/user-project-spending?users=other@example.com")
        assert response.status_code == 403


class TestProjectMemberSpendingEndpoint:
    """Tests for GET /v1/analytics/project-member-spending."""

    @pytest.mark.asyncio
    async def test_returns_200_with_empty_rows_for_project_with_no_members(self, client, admin_user):
        columns = [{"id": "user_id", "label": "User", "type": "string", "format": None}]
        with patch(
            "codemie.service.analytics.handlers.member_spend_service.member_spend_service.get_project_member_spend",
            new=AsyncMock(return_value=(columns, [])),
        ):
            response = client.get("/v1/analytics/project-member-spending?projects=empty-project")

        assert response.status_code == 200
        assert response.json()["data"]["rows"] == []

    @pytest.mark.asyncio
    async def test_paginates_rows(self, client, admin_user):
        columns = [{"id": "user_id", "label": "User", "type": "string", "format": None}]
        rows = [{"user_id": f"u-{i}", "platform": 0, "cli": 0, "premium_models": 0} for i in range(5)]
        with patch(
            "codemie.service.analytics.handlers.member_spend_service.member_spend_service.get_project_member_spend",
            new=AsyncMock(return_value=(columns, rows)),
        ):
            response = client.get("/v1/analytics/project-member-spending?projects=atlas-core&page=0&per_page=2")

        body = response.json()
        assert len(body["data"]["rows"]) == 2
        assert body["pagination"]["total_count"] == 5
        assert body["pagination"]["has_more"] is True

    @pytest.mark.asyncio
    async def test_non_admin_caller_is_forbidden(self, client, plain_user):
        response = client.get("/v1/analytics/project-member-spending?projects=atlas-core")
        assert response.status_code == 403
```

Read `tests/codemie/rest_api/routers/test_analytics_budget_usage_labels.py` first and reuse its `client` / user-override fixtures verbatim; if it names them differently, match the existing names rather than introducing new ones.

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_analytics_member_spending.py -v`
Expected: FAIL with 404 responses — the routes do not exist yet.

- [ ] **Step 3: Implement both routes**

Append to `src/codemie/rest_api/routers/analytics.py` after `get_user_budget_usage`:

```python
@router.get(
    "/user-project-spending",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get a user's spend broken down by project",
    description=(
        "Returns one row per project the user belongs to, with spend and configured limit "
        "per budget category for the current budget cycle. Requires the caller to be a global "
        "admin, maintainer, or project admin for at least one project the target user belongs to."
    ),
)
@handle_analytics_errors("user project spending analytics")
async def get_user_project_spending(
    user: User = Depends(authenticate),
    users: str = Query(..., min_length=1, description="Target user email. Exactly one."),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> TabularResponse:
    from datetime import timezone

    from codemie.clients.postgres import get_async_session, get_session
    from codemie.repository.user_project_repository import user_project_repository
    from codemie.service.analytics.handlers.member_spend_service import member_spend_service
    from codemie.service.analytics.response_formatter import ResponseFormatter
    from codemie.service.user.user_management_service import UserManagementService

    start_time = datetime.now(timezone.utc)
    target_email = users.split(",")[0].strip()

    def _lookup():
        with get_session() as session:
            db_user = UserManagementService.get_user_by_email(session, target_email)
            project_names = (
                user_project_repository.get_project_names_for_user(session, db_user.id) if db_user else set()
            )
        return db_user, project_names

    target_user, target_projects = await asyncio.to_thread(_lookup)
    if target_user is None:
        raise ExtendedHTTPException(
            code=status.HTTP_404_NOT_FOUND,
            message=f"User {target_email} not found.",
            help="Verify the email and try again.",
        )
    _authorize_admin_budget_view(user, target_projects)

    async with get_async_session() as session:
        columns, rows = await member_spend_service.get_user_project_spend(session, str(target_user.id))

    total_count = len(rows)
    page_rows = rows[page * per_page : (page + 1) * per_page]
    execution_time_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000

    return ResponseFormatter.format_tabular_response(
        columns=columns,
        rows=page_rows,
        filters_applied={"users": target_email},
        execution_time_ms=execution_time_ms,
        totals=None,
        page=page,
        per_page=per_page,
        total_count=total_count,
    )


@router.get(
    "/project-member-spending",
    status_code=status.HTTP_200_OK,
    response_model=TabularResponse,
    response_model_by_alias=True,
    summary="Get each project member's spend in that project",
    description=(
        "Returns one row per project member, keyed by user_id, with spend and configured limit "
        "per budget category for the current budget cycle. Requires the caller to be a global "
        "admin, maintainer, or admin of the requested project."
    ),
)
@handle_analytics_errors("project member spending analytics")
async def get_project_member_spending(
    user: User = Depends(authenticate),
    projects: str = Query(..., min_length=1, description="Target project name. Exactly one."),
    page: int = Query(0, ge=0),
    per_page: int = Query(config.ANALYTICS_DEFAULT_PAGE_SIZE, ge=1, le=1000),
) -> TabularResponse:
    from datetime import timezone

    from codemie.clients.postgres import get_async_session
    from codemie.service.analytics.handlers.member_spend_service import member_spend_service
    from codemie.service.analytics.response_formatter import ResponseFormatter

    start_time = datetime.now(timezone.utc)
    project_name = projects.split(",")[0].strip()

    _authorize_admin_budget_view(user, {project_name})

    async with get_async_session() as session:
        columns, rows = await member_spend_service.get_project_member_spend(session, project_name)

    total_count = len(rows)
    page_rows = rows[page * per_page : (page + 1) * per_page]
    execution_time_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000

    return ResponseFormatter.format_tabular_response(
        columns=columns,
        rows=page_rows,
        filters_applied={"projects": project_name},
        execution_time_ms=execution_time_ms,
        totals=None,
        page=page,
        per_page=per_page,
        total_count=total_count,
    )
```

`UserManagementService.get_user_by_email(session, email)` exists as used (`user_management_service.py:145`) and returns `Optional[UserDB]`. It is a sync method, hence the `asyncio.to_thread` wrapper — the same pattern `_resolve_admin_budget_subject` uses at `analytics.py:2923-2929`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_analytics_member_spending.py -v`
Expected: PASS

- [ ] **Step 5: Check the sibling budget_usage tests still pass**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_analytics_budget_usage_labels.py tests/codemie/service/analytics/handlers/test_budget_usage_service.py -q`
Expected: PASS. These are the only existing tests that touch code paths adjacent to the new routes. Do not run the full suite.

- [ ] **Step 6: Lint**

Run: `make ruff`

- [ ] **Step 7: Commit**

```bash
git add src/codemie/rest_api/routers/analytics.py tests/codemie/rest_api/routers/test_analytics_member_spending.py
git commit -m "EPMCDME-14071: Add user-project-spending and project-member-spending endpoints"
```

---

### Task 7: Frontend handoff response

The spec answers the frontend's five open questions, and one answer contradicts their assumption. That has to reach them, not just sit in a spec file.

**Files:**
- Create: `docs/superpowers/handoffs/2026-08-13-epmcdme-14071-backend-answers.md`

- [ ] **Step 1: Write the answers document**

```markdown
# EPMCDME-14071 — Backend Answers to Frontend Handoff

**Date**: 2026-08-13
**Responding to**: `codemie-ui-next/docs/superpowers/specs/2026-08-13-epmcdme-14071-backend-handoff.md`

## Your five open questions

| # | Question | Answer |
|---|---|---|
| 1 | Include spend from projects the user has left? | **Omit** — confirmed. Rows come from current membership. |
| 2 | Include personal-project spend? | **Include** where the personal project appears in membership. |
| 3 | Is project-unattributable spend possible? | **Yes.** See below — this contradicts your assumption. |
| 4 | Include inactive users in member spending? | **Include** — membership rows persist after deactivation. |
| 5 | Can per-category budget cycles differ? | **Yes** — each category follows its own budget's reset schedule. |

## Question 3 needs a product decision

Personal-budget spend is metered against the user, not against any project. It cannot appear in
a per-project breakdown, because no project is attached to it.

**Consequence:** the per-project rows will not sum to the global Budgets column shown on the same
users page. This is not a bug and cannot be fixed by the backend without changing how personal
spend is metered.

You assumed this was impossible. Please decide how to present the difference — options include an
explanatory tooltip, an "Unassigned" row (backend can add one if you want it), or leaving the two
figures visually separated so no one reads them as a sum.

## Deviation from your contract: pagination on Endpoint 1

Your example response for `user-project-spending` includes a `pagination` block. The shared
formatter omits that block unless pagination arguments are supplied, and `budget_usage` supplies
none. **We supply them, so the block is present** on both endpoints, as your example shows.

## Everything else

Confirmed as specified: no `total` field, `BudgetCategory` row keys, authoritative `columns[]`,
optional `*_limit` (null never zero), zero spend as `0`, empty results as `200` with `rows: []`,
JSON numbers, server-side authorization.
```

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/handoffs/2026-08-13-epmcdme-14071-backend-answers.md
git commit -m "EPMCDME-14071: Add backend answers to frontend handoff questions"
```

---

## Self-Review

**Spec coverage** — every section maps to a task:

| Spec section | Task |
|---|---|
| Endpoints, routes, pagination deviation | 6 |
| Column generation from enum | 3 |
| Service layer, new-service rationale | 4, 5 |
| Global refresh | 4 |
| Staleness: table-wide max, no marker table | 1 (`get_newest_member_spend_date`), 4 (`_needs_refresh`) |
| Row construction from membership | 5 |
| Zero vs null | 3 |
| Authorization | 6 |
| Answers to the five questions | 7 |
| Testing | tests in 1, 3, 4, 5, 6 |
| Out of scope | not implemented, correctly |

**Type consistency** — `get_latest_member_rows_for_user` returns `(project_name, budget_category)` keys and is consumed that way in Task 5; `get_latest_member_rows_for_project` returns `(user_id, budget_category)` keys, consumed likewise. `_build_row(key_values, spend_by_category, allocation_by_category)` has the same three-argument shape in Tasks 3 and 5. Both public service methods return `(columns, rows)`, matching what Task 6's routes unpack.

**External symbols verified against source** — no guesses left for the implementer:

| Symbol | Location |
|---|---|
| `get_active_provider()` | `src/codemie/service/budget/provider_registry.py`, used at `spend_collector_service.py:31,173` |
| `collect_member_budget_spend()` | `budget_provider_adapter.py:1568-1570`, no arguments, bulk fetch |
| `MemberBudgetSpendSnapshot` fields | `src/codemie/service/budget/provider.py:109-120` |
| `insert_member_budget_entries` | `project_spend_tracking_repository.py:437-443` |
| `get_latest_before_by_member_budget_ids` | `project_spend_tracking_repository.py:236` |
| `_compute_spend_delta` | `budget_usage_service.py:30` |
| `aget_by_user_id` / `aget_by_project_name` | `user_project_repository.py:522,536` |
| `get_active_by_user` / `get_active_by_project` | `project_budget_repository.py:774,499` |
| `get_user_by_email` | `user_management_service.py:145` |
| `_authorize_admin_budget_view` | `analytics.py:588-602` |

**Code blocks are copy-ready.** Every block was checked for import ordering and undefined names; the only remaining implementer judgment is matching existing test fixture names (Task 6 Step 1) and confirming `func`/`datetime` are already imported in the repository module (Task 1 Step 3).
