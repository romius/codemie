"""create_user_profile_settings_table

Revision ID: b1c2d3e4f5a6
Revises: v4w5x6y7z8a9
Create Date: 2026-07-24 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "v4w5x6y7z8a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_profile_settings",
        sa.Column("user_id", sa.String(), primary_key=True),
        sa.Column(
            "onboarding",
            postgresql.JSONB(),
            nullable=False,
            server_default='{"completed":false,"completed_flows":[],"visited_pages":[]}',
        ),
        sa.Column(
            "recent_assistant_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "last_viewed_release_version",
            postgresql.JSONB(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("user_profile_settings")
