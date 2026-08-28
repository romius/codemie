"""add_sub_workflow_pool_config

Revision ID: t1u2v3w4x5z7
Revises: i1n2t3e4r5a6
Create Date: 2026-07-30 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "t1u2v3w4x5z7"
down_revision: Union[str, None] = "9b9b4c585e54"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("workflows", sa.Column("pool_config", postgresql.JSONB(), nullable=True))
    op.add_column("workflows", sa.Column("max_nesting_level", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("workflows", "max_nesting_level")
    op.drop_column("workflows", "pool_config")
