"""normalize_folder_whitespace (reverted - kept as no-op stub)

Revision ID: d46339b96e47
Revises: w1o2r3k4f5l6
Create Date: 2026-08-04 00:00:00.000000

The original migration (MR !3919, EPMCDME-13806) trimmed whitespace from
`conversation_folders.folder_name` and merged trim-collision groups. It has
been reverted along with the surrounding feature code.

This file is retained as a no-op stub so that:
  * environments where the original migration already ran keep a valid entry
    in `alembic_version` (alembic can resolve the recorded revision to a
    file and stops seeing it as a phantom);
  * the down_revision is now `w1o2r3k4f5l6` instead of `9b9b4c585e54`, which
    closes the double-head that the original chain-off-ancestor mistake
    introduced on main;
  * fresh installs still see a linear graph and run this revision as a no-op.

The data changes the original migration performed in prod are not undone;
folder names that were trimmed and merged stay trimmed and merged. That is
irreducible for any revert of a destructive data migration.

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = 'd46339b96e47'
down_revision: Union[str, None] = 'w1o2r3k4f5l6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op: original body reverted; kept as a stub for alembic_version reconciliation."""
    pass


def downgrade() -> None:
    """No-op: nothing to undo."""
    pass
