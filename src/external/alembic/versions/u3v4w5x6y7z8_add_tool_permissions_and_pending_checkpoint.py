"""add_tool_permissions_and_pending_checkpoint

Revision ID: u3v4w5x6y7z8
Revises: f1g2h3i4j5k6
Create Date: 2026-08-05 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "u3v4w5x6y7z8"
down_revision: Union[str, None] = "d1c2b3a4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("assistants", sa.Column("tool_permissions", postgresql.JSONB(), nullable=True))
    op.add_column("conversations", sa.Column("pending_checkpoint", postgresql.JSONB(), nullable=True))
    op.add_column("conversations", sa.Column("pending_tool_call", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "pending_tool_call")
    op.drop_column("conversations", "pending_checkpoint")
    op.drop_column("assistants", "tool_permissions")
