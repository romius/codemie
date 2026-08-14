"""reset_stale_lifecycle_state_on_deploy

Revision ID: b5c6d7e8f9a0
Revises: d46339b96e47
Create Date: 2026-07-29 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5c6d7e8f9a0"
down_revision: Union[str, None] = "d46339b96e47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Reset all STALE datasources to ACTIVE and clear their marked_stale_at timestamp."""
    op.execute("""
        UPDATE index_info
        SET lifecycle_state = 'ACTIVE',
            marked_stale_at = NULL
        WHERE lifecycle_state = 'STALE'
    """)


def downgrade() -> None:
    """No-op: stale marks that existed before this migration are not recoverable."""
    pass
