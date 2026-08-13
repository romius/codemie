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

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.dialects import postgresql

from external.alembic.versions import d46339b96e47_normalize_folder_whitespace as migration


@pytest.fixture
def mock_connection():
    return MagicMock()


def _make_group(user_id="user-1", trimmed_name="FAQ", ids=("f1", "f2"), original_names=("FAQ ", " FAQ")):
    group = MagicMock()
    group.user_id = user_id
    group.trimmed_name = trimmed_name
    group.ids_by_date = list(ids)
    group.original_names = list(original_names)
    return group


@pytest.mark.parametrize(
    "stmt",
    [
        migration._DETECT_GROUPS_SQL,
        migration._LOCK_ROWS_SQL,
        migration._REPOINT_CONVERSATIONS_SQL,
        migration._UPDATE_CANONICAL_FOLDER_SQL,
        migration._DELETE_LOSING_FOLDERS_SQL,
    ],
)
def test_migration_sql_compiles_against_postgres_dialect_with_typed_array_binds(stmt):
    """Every raw statement, including the ANY(:param) array binds, must compile cleanly
    under the real Postgres dialect. The repo's test suite mocks the DB engine for every
    test (see tests/conftest.py::mock_database_engine), so a live-driver execution test
    isn't available here; compiling under the actual dialect is the strongest available
    check that the array bind types (added to address untyped ANY(:param) params) are
    valid and the statement is well-formed SQL.
    """
    compiled = stmt.compile(dialect=postgresql.dialect())
    assert str(compiled)


@patch("external.alembic.versions.d46339b96e47_normalize_folder_whitespace.op")
def test_upgrade_excludes_default_folder_name(mock_op, mock_connection):
    mock_op.get_bind.return_value = mock_connection

    detect_result = MagicMock()
    detect_result.fetchall.return_value = []
    mock_connection.execute.return_value = detect_result

    migration.upgrade()

    executed_sql = str(mock_connection.execute.call_args_list[0].args[0]).upper()
    assert "TRIMMED_NAME != 'DEFAULT'" in executed_sql


@patch("external.alembic.versions.d46339b96e47_normalize_folder_whitespace.op")
def test_upgrade_applies_collision_group(mock_op, mock_connection):
    mock_op.get_bind.return_value = mock_connection

    detect_result = MagicMock()
    detect_result.fetchall.return_value = [_make_group()]

    lock_result = MagicMock()
    lock_row_1, lock_row_2 = MagicMock(), MagicMock()
    lock_row_1.id, lock_row_2.id = "f1", "f2"
    lock_result.fetchall.return_value = [lock_row_1, lock_row_2]

    empty_result = MagicMock()
    empty_result.fetchall.return_value = []

    # detect batch -> lock rows -> repoint conversations -> update canonical folder
    # -> delete losing rows -> detect batch (loop exit)
    mock_connection.execute.side_effect = [
        detect_result,
        lock_result,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        empty_result,
    ]

    migration.upgrade()

    executed_sql = " ".join(str(call.args[0]) for call in mock_connection.execute.call_args_list).upper()
    assert "FOR UPDATE SKIP LOCKED" in executed_sql
    assert "UPDATE CONVERSATIONS" in executed_sql
    assert "UPDATE CONVERSATION_FOLDERS" in executed_sql
    assert "DELETE FROM CONVERSATION_FOLDERS" in executed_sql


@patch("external.alembic.versions.d46339b96e47_normalize_folder_whitespace.op")
def test_upgrade_applies_non_colliding_whitespace_only_group(mock_op, mock_connection):
    """Single whitespace-only name (no collision): trims in place, no DELETE statement is
    issued since there's no losing row.
    """
    mock_op.get_bind.return_value = mock_connection

    detect_result = MagicMock()
    detect_result.fetchall.return_value = [_make_group(trimmed_name="Notes", ids=("f1",), original_names=("Notes ",))]

    lock_result = MagicMock()
    lock_row = MagicMock()
    lock_row.id = "f1"
    lock_result.fetchall.return_value = [lock_row]

    empty_result = MagicMock()
    empty_result.fetchall.return_value = []

    mock_connection.execute.side_effect = [
        detect_result,
        lock_result,
        MagicMock(),  # repoint conversations
        MagicMock(),  # update canonical folder
        empty_result,  # detect batch (loop exit)
    ]

    migration.upgrade()

    executed_sql = " ".join(str(call.args[0]) for call in mock_connection.execute.call_args_list).upper()
    assert "UPDATE CONVERSATIONS" in executed_sql
    assert "UPDATE CONVERSATION_FOLDERS" in executed_sql
    assert "DELETE FROM CONVERSATION_FOLDERS" not in executed_sql


@patch("external.alembic.versions.d46339b96e47_normalize_folder_whitespace.op")
def test_upgrade_skips_group_with_row_locked_by_another_transaction(mock_op, mock_connection):
    mock_op.get_bind.return_value = mock_connection

    detect_result = MagicMock()
    detect_result.fetchall.return_value = [_make_group(ids=("f1", "f2"))]

    # Only f1 got locked; f2 is held by another transaction — group must be skipped.
    lock_result = MagicMock()
    lock_row = MagicMock()
    lock_row.id = "f1"
    lock_result.fetchall.return_value = [lock_row]

    empty_result = MagicMock()
    empty_result.fetchall.return_value = []

    mock_connection.execute.side_effect = [detect_result, lock_result, empty_result]

    migration.upgrade()

    executed_sql = " ".join(str(call.args[0]) for call in mock_connection.execute.call_args_list).upper()
    assert "UPDATE CONVERSATIONS" not in executed_sql
    assert "UPDATE CONVERSATION_FOLDERS" not in executed_sql
    assert "DELETE FROM" not in executed_sql


def test_downgrade_is_noop():
    # Should not raise
    migration.downgrade()
