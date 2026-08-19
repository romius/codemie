"""add_chargeback_enabled_to_applications

Revision ID: f1g2h3i4j5k6
Revises: 9b9b4c585e54
Create Date: 2026-08-12 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1g2h3i4j5k6"
down_revision: Union[str, None] = "u2v3w4x5y6z7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column(
            "chargeback_enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE applications
        SET chargeback_enabled = true
        WHERE id IN (
            SELECT DISTINCT project_name
            FROM project_budget_assignments
            WHERE deleted_at IS NULL
        )
        """
    )


def downgrade() -> None:
    op.drop_column("applications", "chargeback_enabled")
