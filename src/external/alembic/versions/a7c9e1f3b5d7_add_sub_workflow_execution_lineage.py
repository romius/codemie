"""add_sub_workflow_execution_lineage

Revision ID: a7c9e1f3b5d7
Revises: t1u2v3w4x5z7
Create Date: 2026-07-30 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a7c9e1f3b5d7"
down_revision: Union[str, None] = "t1u2v3w4x5z7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "workflow_executions",
        sa.Column("parent_execution_id", sa.String(), nullable=True),
    )
    op.add_column(
        "workflow_executions",
        sa.Column("active_sub_execution_id", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_workflow_executions_parent_execution_id",
        "workflow_executions",
        ["parent_execution_id"],
    )
    op.create_index(
        "ix_workflow_executions_active_sub_execution_id",
        "workflow_executions",
        ["active_sub_execution_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_workflow_executions_active_sub_execution_id", table_name="workflow_executions")
    op.drop_index("ix_workflow_executions_parent_execution_id", table_name="workflow_executions")
    op.drop_column("workflow_executions", "active_sub_execution_id")
    op.drop_column("workflow_executions", "parent_execution_id")
