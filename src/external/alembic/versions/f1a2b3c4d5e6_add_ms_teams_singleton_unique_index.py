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

"""add_ms_teams_singleton_unique_index

Revision ID: f1a2b3c4d5e6
Revises: c3d4e5f6a7b8
Create Date: 2026-08-27 00:00:00.000000

Backs the application-level check_ms_teams_exist read-then-write check with a
DB-level partial unique index, so two concurrent create requests for the same
project cannot both insert an ms_teams settings row.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add a partial unique index enforcing at most one ms_teams row per project."""
    op.create_index(
        "uix_settings_project_ms_teams_singleton",
        "settings",
        ["project_name"],
        unique=True,
        postgresql_where=sa.text("credential_type = 'MS_TEAMS'"),
    )


def downgrade() -> None:
    """Drop the ms_teams singleton partial unique index."""
    op.drop_index(
        "uix_settings_project_ms_teams_singleton",
        table_name="settings",
    )
