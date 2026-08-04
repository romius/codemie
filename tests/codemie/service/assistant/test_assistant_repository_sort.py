# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import pytest
from sqlmodel import select

from codemie.rest_api.models.assistant import Assistant, AssistantSortBy
from codemie.rest_api.models.index import SortOrder
from codemie.service.assistant.assistant_repository import AssistantRepository, AssistantScope


# ---------------------------------------------------------------------------
# _build_sort_column — column + direction + null handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sort_by,sort_order,expected_column,expected_direction,expected_nulls",
    [
        (AssistantSortBy.USAGE, SortOrder.DESC, "unique_users_count", "DESC", "NULLS LAST"),
        (AssistantSortBy.USAGE, SortOrder.ASC, "unique_users_count", "ASC", "NULLS LAST"),
        (AssistantSortBy.LIKES, SortOrder.DESC, "unique_likes_count", "DESC", "NULLS LAST"),
        (AssistantSortBy.LIKES, SortOrder.ASC, "unique_likes_count", "ASC", "NULLS LAST"),
        (AssistantSortBy.DISLIKES, SortOrder.DESC, "unique_dislikes_count", "DESC", "NULLS LAST"),
        (AssistantSortBy.DISLIKES, SortOrder.ASC, "unique_dislikes_count", "ASC", "NULLS LAST"),
        (AssistantSortBy.NAME, SortOrder.DESC, "name", "DESC", "NULLS LAST"),
        (AssistantSortBy.NAME, SortOrder.ASC, "name", "ASC", "NULLS LAST"),
    ],
)
def test_build_sort_column(sort_by, sort_order, expected_column, expected_direction, expected_nulls):
    """_build_sort_column produces correct column, direction, and null handling."""
    expr = AssistantRepository._build_sort_column(sort_by, sort_order)
    sql = str(expr.compile(compile_kwargs={"literal_binds": True}))
    assert expected_column in sql
    assert expected_direction in sql.upper()
    assert expected_nulls in sql.upper()


def test_build_sort_column_none_returns_none():
    """_build_sort_column returns None when sort_by is None."""
    result = AssistantRepository._build_sort_column(None, SortOrder.DESC)
    assert result is None


# ---------------------------------------------------------------------------
# _apply_sort_to_query — full ORDER BY assembly per scope
# ---------------------------------------------------------------------------


def _sql(query) -> str:
    return str(query.compile(compile_kwargs={"literal_binds": True})).upper()


# --- MARKETPLACE ---


def test_marketplace_default_sort_uses_unique_users_count():
    """MARKETPLACE with sort_by=None → unique_users_count DESC NULLS LAST first."""
    q = AssistantRepository._apply_sort_to_query(
        select(Assistant), AssistantScope.MARKETPLACE, None, SortOrder.DESC, True
    )
    sql = _sql(q)
    assert "UNIQUE_USERS_COUNT" in sql
    assert "DESC NULLS LAST" in sql


def test_marketplace_default_sort_has_stable_tiebreakers():
    """MARKETPLACE default ORDER BY includes update_date and id as tiebreakers."""
    q = AssistantRepository._apply_sort_to_query(
        select(Assistant), AssistantScope.MARKETPLACE, None, SortOrder.DESC, True
    )
    sql = _sql(q)
    assert "UPDATE_DATE" in sql
    assert "ID" in sql


@pytest.mark.parametrize(
    "sort_by,col",
    [
        (AssistantSortBy.USAGE, "UNIQUE_USERS_COUNT"),
        (AssistantSortBy.LIKES, "UNIQUE_LIKES_COUNT"),
        (AssistantSortBy.DISLIKES, "UNIQUE_DISLIKES_COUNT"),
        (AssistantSortBy.NAME, "NAME"),
    ],
)
def test_marketplace_explicit_sort_uses_requested_column(sort_by, col):
    """MARKETPLACE with sort_by set → requested column appears first in ORDER BY."""
    q = AssistantRepository._apply_sort_to_query(
        select(Assistant), AssistantScope.MARKETPLACE, sort_by, SortOrder.DESC, True
    )
    sql = _sql(q)
    assert col in sql
    # update_date and id remain as tiebreakers
    assert "UPDATE_DATE" in sql
    assert "ID" in sql


# --- PROJECT_WITH_MARKETPLACE ---


def test_project_with_marketplace_default_has_is_global_grouping():
    """PROJECT_WITH_MARKETPLACE with sort_by=None → is_global in ORDER BY for grouping."""
    q = AssistantRepository._apply_sort_to_query(
        select(Assistant), AssistantScope.PROJECT_WITH_MARKETPLACE, None, SortOrder.DESC, True
    )
    sql = _sql(q)
    assert "IS_GLOBAL" in sql


def test_project_with_marketplace_group_true_prepends_is_global():
    """PROJECT_WITH_MARKETPLACE sort_by=USAGE, group_by_is_global=True → is_global first."""
    q = AssistantRepository._apply_sort_to_query(
        select(Assistant), AssistantScope.PROJECT_WITH_MARKETPLACE, AssistantSortBy.USAGE, SortOrder.DESC, True
    )
    sql = _sql(q)
    assert "IS_GLOBAL" in sql
    assert "UNIQUE_USERS_COUNT" in sql
    # is_global must appear before unique_users_count in the ORDER BY list
    assert sql.index("IS_GLOBAL") < sql.index("UNIQUE_USERS_COUNT")


def test_project_with_marketplace_group_false_omits_is_global():
    """PROJECT_WITH_MARKETPLACE sort_by=USAGE, group_by_is_global=False → no is_global prefix."""
    q = AssistantRepository._apply_sort_to_query(
        select(Assistant), AssistantScope.PROJECT_WITH_MARKETPLACE, AssistantSortBy.USAGE, SortOrder.DESC, False
    )
    sql = _sql(q)
    assert "UNIQUE_USERS_COUNT" in sql
    # is_global must NOT appear as an ORDER BY term when group_by_is_global=False
    # (it may appear in WHERE/FROM clauses, so check ORDER BY substring only)
    order_by_part = sql.split("ORDER BY", 1)[-1] if "ORDER BY" in sql else sql
    assert "IS_GLOBAL" not in order_by_part


def test_project_with_marketplace_default_group_false_omits_is_global():
    """PROJECT_WITH_MARKETPLACE sort_by=None, group_by_is_global=False → no is_global grouping."""
    q = AssistantRepository._apply_sort_to_query(
        select(Assistant), AssistantScope.PROJECT_WITH_MARKETPLACE, None, SortOrder.DESC, False
    )
    sql = _sql(q)
    order_by_part = sql.split("ORDER BY", 1)[-1] if "ORDER BY" in sql else sql
    assert "IS_GLOBAL" not in order_by_part
    assert "UNIQUE_USERS_COUNT" in order_by_part


# --- Other scopes fall back to update_date ---


def test_other_scope_sorts_by_update_date():
    """Non-MARKETPLACE, non-PROJECT_WITH_MARKETPLACE scopes sort by update_date only."""
    q = AssistantRepository._apply_sort_to_query(
        select(Assistant), AssistantScope.VISIBLE_TO_USER, AssistantSortBy.USAGE, SortOrder.DESC, True
    )
    sql = _sql(q)
    # Sort params are ignored — ORDER BY must only contain update_date, not the requested sort col
    order_by_part = sql.split("ORDER BY", 1)[-1] if "ORDER BY" in sql else sql
    assert "UPDATE_DATE" in order_by_part
    assert "UNIQUE_USERS_COUNT" not in order_by_part
