"""add_file_attachment_enabled

Revision ID: v3w4x5y6z7a8
Revises: f1a2b3c4d5e6
Create Date: 2026-08-10 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "v3w4x5y6z7a8"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("assistants", sa.Column("file_attachment_enabled", sa.Boolean(), nullable=True))
    op.add_column("assistant_configurations", sa.Column("file_attachment_enabled", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("assistant_configurations", "file_attachment_enabled")
    op.drop_column("assistants", "file_attachment_enabled")
