"""add_tool_call_policy_to_conversations

Revision ID: v4w5x6y7z8a9
Revises: u3v4w5x6y7z8
Create Date: 2026-08-17 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "v4w5x6y7z8a9"
down_revision: Union[str, None] = "u3v4w5x6y7z8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("tool_call_policy", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "tool_call_policy")
