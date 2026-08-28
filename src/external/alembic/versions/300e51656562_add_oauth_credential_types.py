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

"""add_oauth_credential_types

Adds the per-user OAuth credential types (GITLAB_OAUTH, JIRA_OAUTH, CONFLUENCE_OAUTH) to the
``credentialtypes`` enum in a single step. Replaces the three separate per-provider migrations so
the feature lands as one linear migration with a single down revision on top of main's head.

Revision ID: 300e51656562
Revises: f1g2h3i4j5k6
Create Date: 2026-08-26 08:29:50.000000

"""

from typing import Sequence, Union

from alembic import op
from alembic_postgresql_enum import TableReference

revision: str = "300e51656562"
down_revision: Union[str, Sequence[str], None] = "f1g2h3i4j5k6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ENUM_VALUES_WITHOUT_OAUTH = [
    'JIRA',
    'CONFLUENCE',
    'GIT',
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
    'XWIKI',
    'GOOGLE_OAUTH',
    'DIAL',
    'SHAREPOINT',
    'SVN',
]
_OAUTH_CREDENTIAL_TYPES = ['GITLAB_OAUTH', 'JIRA_OAUTH', 'CONFLUENCE_OAUTH']
_ENUM_VALUES_WITH_OAUTH = _ENUM_VALUES_WITHOUT_OAUTH + _OAUTH_CREDENTIAL_TYPES

_AFFECTED_COLUMNS = [TableReference(table_schema='codemie', table_name='settings', column_name='credential_type')]


def upgrade() -> None:
    """Add GITLAB_OAUTH, JIRA_OAUTH and CONFLUENCE_OAUTH to the credentialtypes enum."""
    op.sync_enum_values(
        enum_schema='codemie',
        enum_name='credentialtypes',
        new_values=_ENUM_VALUES_WITH_OAUTH,
        affected_columns=_AFFECTED_COLUMNS,
        enum_values_to_rename=[],
    )


def downgrade() -> None:
    """Remove the OAuth credential types from the credentialtypes enum.

    WARNING — DESTRUCTIVE: an enum value cannot be dropped while any row references it, so this
    rollback removes every settings row whose ``credential_type`` is one of the OAuth types. To make
    it recoverable rather than a silent, irreversible data loss, the affected rows are first archived
    into ``codemie.settings_oauth_downgrade_backup`` (with ``credential_type`` converted to ``text``
    so the backup does not pin the enum). Restore by re-applying the upgrade and re-inserting from
    that table; otherwise every affected user must re-authenticate.
    """
    op.execute(
        "CREATE TABLE IF NOT EXISTS codemie.settings_oauth_downgrade_backup AS "
        "SELECT * FROM codemie.settings "
        "WHERE credential_type IN ('GITLAB_OAUTH', 'JIRA_OAUTH', 'CONFLUENCE_OAUTH')"
    )
    op.execute(
        "ALTER TABLE codemie.settings_oauth_downgrade_backup "
        "ALTER COLUMN credential_type TYPE text USING credential_type::text"
    )
    op.execute(
        "DELETE FROM codemie.settings " "WHERE credential_type IN ('GITLAB_OAUTH', 'JIRA_OAUTH', 'CONFLUENCE_OAUTH')"
    )
    op.sync_enum_values(
        enum_schema='codemie',
        enum_name='credentialtypes',
        new_values=_ENUM_VALUES_WITHOUT_OAUTH,
        affected_columns=_AFFECTED_COLUMNS,
        enum_values_to_rename=[],
    )
