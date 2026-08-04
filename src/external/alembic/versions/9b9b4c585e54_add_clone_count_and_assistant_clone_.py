"""add clone_count and assistant_clone_event

Revision ID: 9b9b4c585e54
Revises: i1n2t3e4r5a6
Create Date: 2026-07-27 19:56:31.074909

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '9b9b4c585e54'
down_revision: Union[str, None] = 't1u2v3w4x5y6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "assistants",
        sa.Column("clone_count", sa.Integer(), server_default="0", nullable=True),
    )

    op.create_table(
        "assistant_clone_event",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("assistant_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assistant_clone_event_assistant_id", "assistant_clone_event", ["assistant_id"])
    op.create_index("ix_assistant_clone_event_user_id", "assistant_clone_event", ["user_id"])
    op.create_index(
        "ix_assistant_clone_event_assistant_user_created",
        "assistant_clone_event",
        ["assistant_id", "user_id", "created_at"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_assistant_clone_event_assistant_user_created", table_name="assistant_clone_event")
    op.drop_index("ix_assistant_clone_event_user_id", table_name="assistant_clone_event")
    op.drop_index("ix_assistant_clone_event_assistant_id", table_name="assistant_clone_event")
    op.drop_table("assistant_clone_event")
    op.drop_column("assistants", "clone_count")
