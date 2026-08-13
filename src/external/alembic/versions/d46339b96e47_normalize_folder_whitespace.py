"""normalize_folder_whitespace

Revision ID: d46339b96e47
Revises: 9b9b4c585e54
Create Date: 2026-08-04 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import ARRAY, String, bindparam, text


# revision identifiers, used by Alembic.
revision: str = 'd46339b96e47'
down_revision: Union[str, None] = '9b9b4c585e54'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DETECT_GROUPS_SQL = text("""
    WITH normalized AS (
        SELECT id, user_id, folder_name, date, TRIM(folder_name) AS trimmed_name
        FROM conversation_folders
    ),
    candidate_groups AS (
        SELECT user_id, trimmed_name, COUNT(*) AS row_count,
               array_agg(id ORDER BY date ASC) AS ids_by_date,
               array_agg(folder_name) AS original_names
        FROM normalized
        WHERE trimmed_name != 'Default'
        GROUP BY user_id, trimmed_name
        HAVING COUNT(*) > 1
            OR MAX(folder_name) != trimmed_name
    )
    SELECT * FROM candidate_groups
    LIMIT 1000
""")

_LOCK_ROWS_SQL = text("SELECT id FROM conversation_folders WHERE id = ANY(:ids) FOR UPDATE SKIP LOCKED").bindparams(
    bindparam("ids", type_=ARRAY(String))
)

_REPOINT_CONVERSATIONS_SQL = text("""
    UPDATE conversations
    SET folder = :trimmed_name
    WHERE user_id = :user_id AND folder = ANY(:original_names)
""").bindparams(bindparam("original_names", type_=ARRAY(String)))

_UPDATE_CANONICAL_FOLDER_SQL = text("UPDATE conversation_folders SET folder_name = :trimmed_name WHERE id = :id")

_DELETE_LOSING_FOLDERS_SQL = text("DELETE FROM conversation_folders WHERE id = ANY(:losing_ids)").bindparams(
    bindparam("losing_ids", type_=ARRAY(String))
)


def upgrade() -> None:
    """Normalize whitespace in conversation_folders.folder_name and repoint conversations.folder.

    For a trim-collision group (multiple folder_name rows for the same user that trim to the
    same string), the row with the earliest `date` is canonical: its folder_name is set to the
    trimmed value, the other row(s) are deleted, and every conversations.folder value that
    matched any name in the group is repointed to the canonical trimmed name. For a
    non-colliding whitespace-only name, folder_name is simply trimmed and matching
    conversations.folder rows are updated to match. Groups whose trimmed name is the reserved
    'Default' folder name are skipped entirely (never merged or renamed) to avoid colliding
    with the application-level forbidden-name guard.

    Chunked (1 000 groups per batch). Only the specific row ids selected by the current batch
    are locked via a bounded FOR UPDATE SKIP LOCKED — not the whole table — adapted from
    234f8f339638_backfill_conversation_names.py's batching intent (that precedent is
    single-table with no merge step, so the merge logic here is new). A group with any row
    that another transaction is concurrently holding is skipped this iteration and retried on
    the next.
    """
    connection = op.get_bind()

    while True:
        groups = connection.execute(_DETECT_GROUPS_SQL).fetchall()

        if not groups:
            break

        all_ids = [row_id for group in groups for row_id in group.ids_by_date]
        locked_rows = connection.execute(_LOCK_ROWS_SQL, {"ids": all_ids}).fetchall()
        locked_ids = {row.id for row in locked_rows}

        for group in groups:
            if not all(row_id in locked_ids for row_id in group.ids_by_date):
                # Another transaction holds a row in this group — retry next iteration.
                continue

            canonical_id = group.ids_by_date[0]
            losing_ids = group.ids_by_date[1:]
            trimmed_name = group.trimmed_name
            original_names = list(set(group.original_names))

            # Repoint conversations.folder for every original name in this group to the
            # canonical trimmed name, before mutating conversation_folders.
            connection.execute(
                _REPOINT_CONVERSATIONS_SQL,
                {"trimmed_name": trimmed_name, "user_id": group.user_id, "original_names": original_names},
            )

            connection.execute(_UPDATE_CANONICAL_FOLDER_SQL, {"trimmed_name": trimmed_name, "id": canonical_id})

            if losing_ids:
                connection.execute(_DELETE_LOSING_FOLDERS_SQL, {"losing_ids": losing_ids})


def downgrade() -> None:
    """No-op — merged/trimmed folder names cannot be safely restored (rows were deleted)."""
    pass
