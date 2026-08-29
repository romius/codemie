# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""add_deployment_versions_table

Revision ID: 3b1f28a9315a
Revises: g2h3i4j5k6l7
Create Date: 2026-08-03 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "3b1f28a9315a"
down_revision: Union[str, None] = "g2h3i4j5k6l7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create deployment_versions — one row per version, records first-start timestamp."""
    op.create_table(
        "deployment_versions",
        sa.Column("id", sa.VARCHAR(36), nullable=False),
        sa.Column("version", sa.VARCHAR(64), nullable=False, unique=True),
        sa.Column(
            "deployed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deployment_versions_version", "deployment_versions", ["version"])


def downgrade() -> None:
    op.drop_index("ix_deployment_versions_version", table_name="deployment_versions")
    op.drop_table("deployment_versions")
