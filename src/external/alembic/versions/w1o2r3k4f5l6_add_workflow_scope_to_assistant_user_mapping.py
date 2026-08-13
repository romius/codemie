"""add_workflow_scope_to_assistant_user_mapping

Revision ID: w1o2r3k4f5l6
Revises: u1v2w3x4y5z6
Create Date: 2026-07-29 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'w1o2r3k4f5l6'
down_revision: Union[str, None] = 'u1v2w3x4y5z6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Existing rows take the empty-string default and stay assistant-scoped, so nobody has to
    # re-select an integration after the release.
    op.add_column(
        'assistant_user_mapping',
        sa.Column('workflow_id', sa.String(), nullable=False, server_default=''),
    )
    op.drop_constraint('uix_assistant_user_mapping', 'assistant_user_mapping', type_='unique')
    op.create_unique_constraint(
        'uix_assistant_user_mapping_scope',
        'assistant_user_mapping',
        ['assistant_id', 'user_id', 'workflow_id'],
    )
    op.create_index('ix_assistant_user_mapping_workflow_id', 'assistant_user_mapping', ['workflow_id'])
    _restore_archived_workflow_scoped_rows()


def _restore_archived_workflow_scoped_rows() -> None:
    """Bring back workflow-scoped selections a previous downgrade had to park aside.

    Without this an upgrade → downgrade → upgrade cycle (the local branch-switch flow does exactly
    that) would silently lose every per-workflow selection. Rows the user has re-created in the
    meantime win, and every archived row that was restored or superseded leaves the archive, so a
    second run cannot resurrect a selection the user has since changed. Rows with a NULL scope stay
    behind: they come from an older instance writing during a rolling deploy and belong to no
    workflow, so there is nothing to restore them to.
    """
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('assistant_user_mapping_workflow_scope_backup') IS NULL THEN
                RETURN;
            END IF;

            INSERT INTO assistant_user_mapping
            SELECT * FROM assistant_user_mapping_workflow_scope_backup
            WHERE workflow_id IS NOT NULL AND workflow_id <> ''
            ON CONFLICT ON CONSTRAINT uix_assistant_user_mapping_scope DO NOTHING;

            DELETE FROM assistant_user_mapping_workflow_scope_backup
            WHERE workflow_id IS NOT NULL AND workflow_id <> '';
        EXCEPTION WHEN others THEN
            -- An archive written by a differently-shaped version of the table must not stop the
            -- upgrade: the rows stay archived and can be restored by hand.
            RAISE WARNING 'Could not restore archived workflow-scoped mappings: %', SQLERRM;
        END
        $$;
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_assistant_user_mapping_workflow_id', table_name='assistant_user_mapping')
    op.drop_constraint('uix_assistant_user_mapping_scope', 'assistant_user_mapping', type_='unique')
    # Workflow-scoped rows have no place in the two-column constraint, so they cannot stay — but
    # they are user data, and a rollback (including the local branch-switch flow) must not destroy
    # it silently. Archive them first so a later upgrade can restore them; the NULL branch guards
    # against a row written by an older instance during a rolling deploy, which would otherwise
    # survive and break the recreated constraint.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS assistant_user_mapping_workflow_scope_backup
        AS SELECT * FROM assistant_user_mapping WHERE FALSE
        """
    )
    op.execute(
        """
        INSERT INTO assistant_user_mapping_workflow_scope_backup
        SELECT * FROM assistant_user_mapping WHERE workflow_id IS NULL OR workflow_id <> ''
        """
    )
    op.execute("DELETE FROM assistant_user_mapping WHERE workflow_id IS NULL OR workflow_id <> ''")
    op.create_unique_constraint('uix_assistant_user_mapping', 'assistant_user_mapping', ['assistant_id', 'user_id'])
    op.drop_column('assistant_user_mapping', 'workflow_id')
