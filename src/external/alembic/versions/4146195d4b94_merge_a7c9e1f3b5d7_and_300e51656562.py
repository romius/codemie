"""merge a7c9e1f3b5d7 and 300e51656562

Revision ID: 4146195d4b94
Revises: a7c9e1f3b5d7, 300e51656562
Create Date: 2026-08-13 15:35:26.334073

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '4146195d4b94'
down_revision: Union[str, None] = ('a7c9e1f3b5d7', '300e51656562')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
