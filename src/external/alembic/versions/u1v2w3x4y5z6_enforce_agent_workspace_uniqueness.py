"""enforce agent workspace uniqueness

Revision ID: u1v2w3x4y5z6
Revises: t1u2v3w4x5y6
Create Date: 2026-07-30 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "u1v2w3x4y5z6"
down_revision: Union[str, None] = "9b9b4c585e54"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    Enforce uniqueness for AgentWorkspace:
      - one workspace per (conversation_id, user_id)
    Enforce uniqueness for AgentWorkspaceFile:
      - one file row per (workspace_id, path)

    The platform previously relied on application-level best effort; under concurrent
    LangGraph supervisor/subagent startup it is possible to create duplicate workspaces
    which then split file visibility.
    """

    # 1) De-dupe workspaces per (conversation_id, user_id) and re-home files onto the winner.
    #
    # Winner selection:
    # - newest update_date, then newest date, then highest id (stable tie-breaker)
    #
    # NOTE: this migration is PostgreSQL-specific (uses window functions).
    op.execute(
        """
        WITH ranked_workspaces AS (
            SELECT
                id,
                conversation_id,
                user_id,
                ROW_NUMBER() OVER (
                    PARTITION BY conversation_id, user_id
                    ORDER BY update_date DESC NULLS LAST, date DESC NULLS LAST, id DESC
                ) AS rn,
                FIRST_VALUE(id) OVER (
                    PARTITION BY conversation_id, user_id
                    ORDER BY update_date DESC NULLS LAST, date DESC NULLS LAST, id DESC
                ) AS winner_id
            FROM agent_workspaces
        ),
        losers AS (
            SELECT id, winner_id
            FROM ranked_workspaces
            WHERE rn > 1
        )
        UPDATE agent_workspace_files f
        SET workspace_id = l.winner_id
        FROM losers l
        WHERE f.workspace_id = l.id;
        """
    )

    op.execute(
        """
        WITH ranked_workspaces AS (
            SELECT
                id,
                conversation_id,
                user_id,
                ROW_NUMBER() OVER (
                    PARTITION BY conversation_id, user_id
                    ORDER BY update_date DESC NULLS LAST, date DESC NULLS LAST, id DESC
                ) AS rn
            FROM agent_workspaces
        )
        DELETE FROM agent_workspaces aw
        USING ranked_workspaces r
        WHERE aw.id = r.id AND r.rn > 1;
        """
    )

    # 2) De-dupe workspace files for (workspace_id, path) (can happen after re-homing).
    #
    # Keep the "latest" row by:
    # - deleted_at NULL first (prefer active rows), then update_date, then version, then id.
    op.execute(
        """
        WITH ranked_files AS (
            SELECT
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY workspace_id, path
                    ORDER BY
                        (deleted_at IS NULL) DESC,
                        update_date DESC NULLS LAST,
                        version DESC NULLS LAST,
                        id DESC
                ) AS rn
            FROM agent_workspace_files
        )
        DELETE FROM agent_workspace_files f
        USING ranked_files r
        WHERE f.id = r.id AND r.rn > 1;
        """
    )

    # 3) Add unique constraints.
    op.create_unique_constraint(
        "uq_agent_workspaces_conversation_user",
        "agent_workspaces",
        ["conversation_id", "user_id"],
    )
    op.create_unique_constraint(
        "uq_agent_workspace_files_workspace_path",
        "agent_workspace_files",
        ["workspace_id", "path"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_agent_workspace_files_workspace_path", "agent_workspace_files", type_="unique")
    op.drop_constraint("uq_agent_workspaces_conversation_user", "agent_workspaces", type_="unique")
