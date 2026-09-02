"""add interactive_enabled to assistants

Revision ID: e7f8a9b0c1d2
Revises: 9b9b4c585e54
Create Date: 2026-08-10 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e7f8a9b0c1d2'
down_revision: Union[str, None] = 'e13959a1b2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "assistants",
        sa.Column("interactive_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    # Backfill from the legacy per-group JSONB toggle: any enabled group -> on.
    # The interactive_features column itself is kept (dropped later with the
    # legacy interactive model removal).
    op.execute(
        """
        UPDATE assistants
        SET interactive_enabled = true
        WHERE interactive_features IS NOT NULL
          AND (
            COALESCE((interactive_features ->> 'action_buttons')::boolean, false)
            OR COALESCE((interactive_features ->> 'choice')::boolean, false)
            OR COALESCE((interactive_features ->> 'short_forms')::boolean, false)
          )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("assistants", "interactive_enabled")
