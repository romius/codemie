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

from unittest.mock import MagicMock

import pytest

from codemie_tools.data_management.sharepoint.models import SharePointConfig
from codemie_tools.data_management.sharepoint.tools import SharePointTool


@pytest.fixture
def sharepoint_config():
    return SharePointConfig(
        url="https://contoso.sharepoint.com",
        tenant_id="tenant-id",
        client_id="client-id",
        client_secret="client-secret",
    )


@pytest.fixture
def sharepoint_tool(sharepoint_config):
    return SharePointTool(config=sharepoint_config)


@pytest.fixture
def token_response():
    """Successful client-credentials token response."""
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"access_token": "graph-token"}
    return response


def graph_response(status_code=200, text="{}", headers=None):
    """Build a mocked Graph API response."""
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.headers = headers or {}
    return response


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# A .docx is a ZIP archive: these bytes are not valid UTF-8 and must survive the tool untouched.
DOCX_BYTES = b"PK\x03\x04\x14\x00\x06\x00\xff\xfe binary body \x00\x01"


def file_object(name="report.docx", mime_type=DOCX_MIME, content=DOCX_BYTES):
    """Build a FileObject-like attachment as delivered by the chat via config.input_files."""
    file_obj = MagicMock()
    file_obj.name = name
    file_obj.mime_type = mime_type
    file_obj.bytes_content.return_value = content
    return file_obj


@pytest.fixture
def tool_with_attachment(sharepoint_config):
    """Tool whose conversation has a binary .docx attached."""
    sharepoint_config.input_files = [file_object()]
    return SharePointTool(config=sharepoint_config)
