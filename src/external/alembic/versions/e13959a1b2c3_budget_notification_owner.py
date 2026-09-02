"""epmcdme_13959_budget_notification_owner

Adds three columns to budgets for soft-limit email notifications:
- notification_owner_email (varchar(320)): free-form email; may be a group alias.
- soft_limit_notified_at (TIMESTAMP tz): dedup marker for soft-limit emails.
- soft_limit_notify_once (boolean, default false): when true, fires only once
  per budget edit cycle (resets to NULL when the budget is saved).

Human-run verification after review:
  alembic upgrade head
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e13959a1b2c3"
down_revision: Union[str, None] = "v3w4x5y6z7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "budgets",
        sa.Column("notification_owner_email", sa.String(length=320), nullable=True),
    )
    op.add_column(
        "budgets",
        sa.Column("soft_limit_notified_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "budgets",
        sa.Column(
            "soft_limit_notify_once",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("budgets", "soft_limit_notify_once")
    op.drop_column("budgets", "soft_limit_notified_at")
    op.drop_column("budgets", "notification_owner_email")
