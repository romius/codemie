"""add_is_auditor_to_users

Revision ID: u2v3w4x5y6z7
Revises: b5c6d7e8f9a0
Create Date: 2026-08-12 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

revision = "u2v3w4x5y6z7"
down_revision = "b5c6d7e8f9a0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_auditor", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "is_auditor")
