"""drop interactive_features from assistants

Revision ID: d1c2b3a4e5f6
Revises: e7f8a9b0c1d2
Create Date: 2026-08-10 18:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd1c2b3a4e5f6'
down_revision: Union[str, None] = 'e7f8a9b0c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop the legacy per-group interactive toggle column.

    The A2UI protocol replaced the custom interactive protocol; the only surviving
    switch is the plain boolean ``interactive_enabled`` (already backfilled from this
    column by revision e7f8a9b0c1d2).
    """
    op.drop_column("assistants", "interactive_features")


# The legacy column held one flag per interactive feature group, but the UI drove all
# three from a single switch, so an enabled assistant always stored exactly this object.
# Restoring it is lossless for anything the product could actually produce.
_ENABLED_GROUPS = '{"action_buttons": true, "choice": true, "short_forms": true}'

_RESTORE_INTERACTIVE_FEATURES = f"""
    UPDATE assistants
    SET interactive_features = '{_ENABLED_GROUPS}'::jsonb
    WHERE interactive_enabled = true
"""


def downgrade() -> None:
    """Re-add the column and move the toggle back into it.

    ``interactive_enabled`` is dropped by revision e7f8a9b0c1d2, which runs after this
    one, so the boolean has to be reversed here or the setting is lost: without this
    backfill every assistant would come back with interactive features off.
    Assistants that had it off keep the NULL ``add_column`` leaves behind.
    """
    op.add_column(
        "assistants",
        sa.Column("interactive_features", postgresql.JSONB(), nullable=True),
    )
    op.execute(_RESTORE_INTERACTIVE_FEATURES)
