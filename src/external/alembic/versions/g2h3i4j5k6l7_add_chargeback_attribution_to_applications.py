"""add_chargeback_attribution_to_applications

Revision ID: g2h3i4j5k6l7
Revises: 4146195d4b94
Create Date: 2026-08-26 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "g2h3i4j5k6l7"
down_revision: Union[str, None] = "4146195d4b94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "applications",
        sa.Column("chargeback_attribution", sa.String(), server_default=sa.text("'project'"), nullable=False),
    )
    op.create_check_constraint(
        "ck_applications_chargeback_attribution",
        "applications",
        "chargeback_attribution IN ('project', 'cost_center')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_applications_chargeback_attribution", "applications", type_="check")
    op.drop_column("applications", "chargeback_attribution")
