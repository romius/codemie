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

"""add_ms_teams_credential_type

Revision ID: 8b2c1a4d5e6f
Revises: 3b1f28a9315a
Create Date: 2026-08-27 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
from alembic_postgresql_enum import TableReference

# revision identifiers, used by Alembic.
revision: str = '8b2c1a4d5e6f'
down_revision: Union[str, None] = '3b1f28a9315a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add MS_TEAMS to credentialtypes enum."""
    op.sync_enum_values(
        enum_schema='codemie',
        enum_name='credentialtypes',
        new_values=[
            'JIRA',
            'CONFLUENCE',
            'GIT',
            'SVN',
            'KUBERNETES',
            'AWS',
            'GCP',
            'KEYCLOAK',
            'AZURE',
            'ELASTIC',
            'OPEN_API',
            'PLUGIN',
            'FILE_SYSTEM',
            'SCHEDULER',
            'WEBHOOK',
            'EMAIL',
            'AZURE_DEVOPS',
            'SONAR',
            'SQL',
            'TELEGRAM',
            'ZEPHYR_SCALE',
            '_ZEPHYR_CLOUD',
            'ZEPHYR_SQUAD',
            'XRAY',
            'SERVICENOW',
            'REPORT_PORTAL',
            'ENVIRONMENT_VARS',
            'AUTH_TOKEN',
            'A2A',
            'LITE_LLM',
            'SHAREPOINT',
            'XWIKI',
            'GOOGLE_OAUTH',
            'DIAL',
            'MS_TEAMS',
        ],
        affected_columns=[TableReference(table_schema='codemie', table_name='settings', column_name='credential_type')],
        enum_values_to_rename=[],
    )


def downgrade() -> None:
    """Remove MS_TEAMS from credentialtypes enum."""
    op.execute("DELETE FROM codemie.settings WHERE credential_type = 'MS_TEAMS'")
    op.sync_enum_values(
        enum_schema='codemie',
        enum_name='credentialtypes',
        new_values=[
            'JIRA',
            'CONFLUENCE',
            'GIT',
            'SVN',
            'KUBERNETES',
            'AWS',
            'GCP',
            'KEYCLOAK',
            'AZURE',
            'ELASTIC',
            'OPEN_API',
            'PLUGIN',
            'FILE_SYSTEM',
            'SCHEDULER',
            'WEBHOOK',
            'EMAIL',
            'AZURE_DEVOPS',
            'SONAR',
            'SQL',
            'TELEGRAM',
            'ZEPHYR_SCALE',
            '_ZEPHYR_CLOUD',
            'ZEPHYR_SQUAD',
            'XRAY',
            'SERVICENOW',
            'REPORT_PORTAL',
            'ENVIRONMENT_VARS',
            'AUTH_TOKEN',
            'A2A',
            'LITE_LLM',
            'SHAREPOINT',
            'XWIKI',
            'GOOGLE_OAUTH',
            'DIAL',
        ],
        affected_columns=[TableReference(table_schema='codemie', table_name='settings', column_name='credential_type')],
        enum_values_to_rename=[],
    )
