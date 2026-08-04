"""add_sort_indexes_to_assistants

Revision ID: t1u2v3w4x5y6
Revises: i1n2t3e4r5a6
Create Date: 2026-07-23 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "t1u2v3w4x5y6"
down_revision: Union[str, None] = "i1n2t3e4r5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_assistants_unique_likes_count", "assistants", ["unique_likes_count"])
    op.create_index("ix_assistants_unique_dislikes_count", "assistants", ["unique_dislikes_count"])
    op.create_index("ix_assistants_name_btree", "assistants", ["name"])


def downgrade() -> None:
    op.drop_index("ix_assistants_name_btree", table_name="assistants")
    op.drop_index("ix_assistants_unique_dislikes_count", table_name="assistants")
    op.drop_index("ix_assistants_unique_likes_count", table_name="assistants")
