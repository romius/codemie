# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

from unittest.mock import Mock, MagicMock, patch

import pytest

import base64

from codemie_tools.azure_devops.work_item.models import AzureDevOpsWorkItemConfig
from codemie_tools.azure_devops.work_item.tools import (
    SearchWorkItemsTool,
    CreateWorkItemTool,
    UpdateWorkItemTool,
    GetWorkItemTool,
    GetRelationTypesTool,
    CreateCommentTool,
    GetWorkItemAttachmentContentTool,
)


@pytest.fixture
def mock_config():
    return AzureDevOpsWorkItemConfig(
        organization_url="https://dev.azure.com/org", project="test-project", token="fake-token", limit=5
    )


@pytest.fixture
def mock_client():
    return Mock()


@pytest.fixture
def search_tool(mock_config, mock_client):
    tool = SearchWorkItemsTool(config=mock_config)
    tool._client = mock_client
    return tool


@pytest.fixture
def create_tool(mock_config, mock_client):
    tool = CreateWorkItemTool(config=mock_config)
    tool._client = mock_client
    return tool


@pytest.fixture
def update_tool(mock_config, mock_client):
    tool = UpdateWorkItemTool(config=mock_config)
    tool._client = mock_client
    return tool


@pytest.fixture
def get_tool(mock_config, mock_client):
    tool = GetWorkItemTool(config=mock_config)
    tool._client = mock_client
    return tool


class TestSearchWorkItemsTool:
    def test_search_work_items_success(self, search_tool, mock_client):
        # Arrange
        query = "SELECT [System.Id] FROM WorkItems"
        mock_work_item = MagicMock()
        mock_work_item.id = 1
        mock_client.query_by_wiql.return_value.work_items = [mock_work_item]

        mock_full_item = MagicMock()
        mock_full_item.id = 1
        mock_full_item.fields = {"System.Title": "Test Item"}
        mock_client.get_work_item.return_value = mock_full_item

        # Act
        result = search_tool.execute(query=query)

        # Assert
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["id"] == 1
        mock_client.query_by_wiql.assert_called_once()

    def test_search_work_items_no_results(self, search_tool, mock_client):
        # Arrange
        query = "SELECT [System.Id] FROM WorkItems"
        mock_client.query_by_wiql.return_value.work_items = []

        # Act
        result = search_tool.execute(query=query)

        # Assert
        assert result == "No work items found."


class TestCreateWorkItemTool:
    def test_create_work_item_success(self, create_tool, mock_client):
        # Arrange
        work_item_json = '{"fields": {"System.Title": "Test Item"}}'
        mock_response = MagicMock()
        mock_response.id = 1
        mock_response.url = "http://test-url"
        mock_client.create_work_item.return_value = mock_response

        # Act
        result = create_tool.execute(work_item_json=work_item_json)

        # Assert
        assert "created successfully" in result
        assert "1" in result
        mock_client.create_work_item.assert_called_once()


class TestUpdateWorkItemTool:
    def test_update_work_item_success(self, update_tool, mock_client):
        # Arrange
        work_item_json = '{"fields": {"System.Title": "Updated Title"}}'
        mock_response = MagicMock()
        mock_response.id = 1
        mock_client.update_work_item.return_value = mock_response

        # Act
        result = update_tool.execute(id=1, work_item_json=work_item_json)

        # Assert
        assert "was updated" in result
        mock_client.update_work_item.assert_called_once()


class TestGetWorkItemTool:
    def test_get_work_item_success(self, get_tool, mock_client):
        # Arrange
        mock_work_item = MagicMock()
        mock_work_item.id = 1
        mock_work_item.fields = {"System.Title": "Test Item"}
        mock_work_item.relations = []
        mock_client.get_work_item.return_value = mock_work_item

        # Act
        result = get_tool.execute(id=1)

        # Assert
        assert isinstance(result, dict)
        assert result["id"] == 1
        assert "System.Title" in result
        mock_client.get_work_item.assert_called_once()


class TestCreateCommentTool:
    @pytest.fixture
    def create_comment_tool(self, mock_config, mock_client):
        tool = CreateCommentTool(config=mock_config)
        tool._client = mock_client
        return tool

    def test_create_comment_success(self, create_comment_tool, mock_client):
        mock_comment = MagicMock()
        mock_comment.id = 42
        mock_client.add_comment.return_value = mock_comment

        result = create_comment_tool.execute(work_item_id=123, text="Test comment")

        assert "42" in result
        assert "123" in result
        mock_client.add_comment.assert_called_once()

    def test_create_comment_api_error(self, create_comment_tool, mock_client):
        from langchain_core.tools import ToolException

        mock_client.add_comment.side_effect = Exception("API error")

        with pytest.raises(ToolException, match="Error creating comment"):
            create_comment_tool.execute(work_item_id=123, text="Test comment")


class TestGetRelationTypesTool:
    def test_get_relation_types_success(self, mock_config, mock_client):
        # Arrange
        tool = GetRelationTypesTool(config=mock_config)
        tool._client = mock_client

        mock_relation = MagicMock()
        mock_relation.name = "Relates"
        mock_relation.reference_name = "System.LinkTypes.Related"
        mock_client.get_relation_types.return_value = [mock_relation]

        # Act
        result = tool.execute()

        # Assert
        assert isinstance(result, dict)
        assert "Relates" in result
        assert result["Relates"] == "System.LinkTypes.Related"


def _make_relation(rel_type: str, name: str, url: str, comment: str = None):
    """Build a mock work item relation with as_dict() support."""
    rel = Mock()
    attrs = {"name": name}
    if comment:
        attrs["comment"] = comment
    rel.as_dict.return_value = {"rel": rel_type, "url": url, "attributes": attrs}
    return rel


class TestGetWorkItemAttachmentContentTool:
    @pytest.fixture
    def mock_config(self):
        return AzureDevOpsWorkItemConfig(
            organization_url="https://dev.azure.com/org", project="test-project", token="fake-token"
        )

    @pytest.fixture
    def tool(self, mock_config):
        t = GetWorkItemAttachmentContentTool(config=mock_config)
        t._client = Mock()
        return t

    # ------------------------------------------------------------------ mixin

    def test_build_base64_response_small_file(self, tool):
        data = b"small content"
        result = tool._build_base64_response(data, "note")
        assert result["content_type"] == "base64"
        assert result["content"] == base64.b64encode(data).decode()
        assert result["note"] == "note"

    def test_build_base64_response_large_file_returns_metadata_only(self, tool):
        data = b"x" * (tool._MAX_BASE64_BYTES + 1)
        result = tool._build_base64_response(data, "original note")
        assert result["content_type"] == "metadata_only"
        assert result["content"] is None
        assert "too large" in result["note"]

    def test_detect_mime_type_known_extension(self, tool):
        assert tool._detect_mime_type("report.pdf") == "application/pdf"
        assert tool._detect_mime_type("image.png") == "image/png"

    def test_detect_mime_type_unknown_returns_octet_stream(self, tool):
        assert tool._detect_mime_type("file.unknownxyz") == "application/octet-stream"

    def test_is_text_based_text_mime(self, tool):
        assert tool._is_text_based("text/plain", "file.dat") is True

    def test_is_text_based_known_text_extension(self, tool):
        assert tool._is_text_based("application/octet-stream", "notes.md") is True

    def test_is_text_based_binary(self, tool):
        assert tool._is_text_based("application/octet-stream", "binary.exe") is False

    def test_process_content_text_file(self, tool):
        result = tool._process_content("readme.txt", b"Hello world")
        assert result["content_type"] == "text"
        assert result["content"] == "Hello world"

    def test_process_content_unknown_large_returns_metadata_only(self, tool):
        data = b"x" * (tool._MAX_BASE64_BYTES + 1)
        result = tool._process_content("blob.bin", data)
        assert result["content_type"] == "metadata_only"
        assert result["content"] is None

    def test_process_content_unknown_small_returns_base64(self, tool):
        data = b"x" * 100
        result = tool._process_content("blob.bin", data)
        assert result["content_type"] == "base64"

    def test_process_content_xlsb_extension_fallback_when_mime_unknown(self, tool):
        """In a slim Linux container without /etc/mime.types, guess_type returns None for
        .xlsb; the extension must still route to Excel extraction rather than base64
        (EPMCDME-11738 follow-up item 3)."""
        import pandas as pd

        df = pd.DataFrame({"A": ["cell"]})
        with patch("mimetypes.guess_type", return_value=(None, None)):
            with patch("codemie_tools.azure_devops.attachment_content_mixin.XlsxProcessor") as mock_proc:
                instance = mock_proc.return_value
                instance.load.return_value = {"Sheet1": df}
                instance.convert.return_value = "## Sheet1\ncell"
                result = tool._process_content("report.xlsb", b"fake xlsb")
        assert result["content_type"] == "text"
        assert "cell" in result["content"]
        assert instance.load.call_args.kwargs.get("file_ext") == ".xlsb"

    def test_process_content_xls_extension_fallback_when_mime_unknown(self, tool):
        """.xls extension fallback (guess_type None) must also reach Excel extraction."""
        import pandas as pd

        df = pd.DataFrame({"A": ["v"]})
        with patch("mimetypes.guess_type", return_value=(None, None)):
            with patch("codemie_tools.azure_devops.attachment_content_mixin.XlsxProcessor") as mock_proc:
                instance = mock_proc.return_value
                instance.load.return_value = {"Sheet1": df}
                instance.convert.return_value = "content"
                result = tool._process_content("legacy.xls", b"fake xls")
        assert result["content_type"] == "text"

    # ------------------------------------------------ _find_attachment_in_relations

    def test_find_attachment_success(self, tool):
        attachment = _make_relation(
            "AttachedFile",
            "spec.pdf",
            "https://dev.azure.com/org/proj/_apis/wit/attachments/abc-123",
            comment="Initial spec",
        )
        work_item = Mock()
        work_item.relations = [attachment]
        tool._client.get_work_item.return_value = work_item

        filename, url, note = tool._find_attachment_in_relations(42, "spec.pdf")

        assert filename == "spec.pdf"
        assert "abc-123" in url
        assert note == "Initial spec"
        tool._client.get_work_item.assert_called_once_with(id=42, project="test-project", expand="Relations")

    def test_find_attachment_case_insensitive(self, tool):
        attachment = _make_relation(
            "AttachedFile",
            "SPEC.PDF",
            "https://dev.azure.com/org/proj/_apis/wit/attachments/abc",
        )
        work_item = Mock()
        work_item.relations = [attachment]
        tool._client.get_work_item.return_value = work_item

        filename, url, note = tool._find_attachment_in_relations(42, "spec.pdf")
        assert filename == "SPEC.PDF"

    def test_find_attachment_skips_non_attached_file_relations(self, tool):
        link_rel = _make_relation("System.LinkTypes.Related", "ignored", "https://example.com/rel")
        work_item = Mock()
        work_item.relations = [link_rel]
        tool._client.get_work_item.return_value = work_item

        from langchain_core.tools import ToolException

        with pytest.raises(ToolException, match="has no file attachments"):
            tool._find_attachment_in_relations(42, "spec.pdf")

    def test_find_attachment_no_relations_raises(self, tool):
        from langchain_core.tools import ToolException

        work_item = Mock()
        work_item.relations = None
        tool._client.get_work_item.return_value = work_item

        with pytest.raises(ToolException, match="has no file attachments"):
            tool._find_attachment_in_relations(42, "spec.pdf")

    def test_find_attachment_not_found_lists_available(self, tool):
        from langchain_core.tools import ToolException

        attachment = _make_relation("AttachedFile", "other.docx", "https://example.com/att")
        work_item = Mock()
        work_item.relations = [attachment]
        tool._client.get_work_item.return_value = work_item

        with pytest.raises(ToolException, match="other.docx"):
            tool._find_attachment_in_relations(42, "missing.pdf")

    # ----------------------------------------------------------- execute

    def test_execute_with_direct_url_extracts_filename_from_query(self, tool):
        with patch.object(tool, "_download_attachment", return_value=b"hello world"):
            result = tool.execute(
                work_item_id=42,
                attachment_url="https://dev.azure.com/org/proj/_apis/wit/attachments/abc?fileName=notes.txt",
            )

        assert result["work_item_id"] == 42
        assert result["filename"] == "notes.txt"
        assert result["content_type"] == "text"
        assert result["content"] == "hello world"
        assert result["size_bytes"] == 11

    def test_execute_with_direct_url_and_explicit_name(self, tool):
        with patch.object(tool, "_download_attachment", return_value=b"data"):
            result = tool.execute(
                work_item_id=7,
                attachment_url="https://dev.azure.com/org/proj/_apis/wit/attachments/xyz",
                attachment_name="override.txt",
            )

        assert result["filename"] == "override.txt"

    def test_execute_by_name_discovery(self, tool):
        url = "https://dev.azure.com/org/proj/_apis/wit/attachments/abc-123"
        with patch.object(tool, "_find_attachment_in_relations", return_value=("spec.pdf", url, "draft spec")):
            with patch.object(tool, "_download_attachment", return_value=b"PDF content"):
                result = tool.execute(work_item_id=99, attachment_name="spec.pdf")

        assert result["filename"] == "spec.pdf"
        assert result["attachment_note"] == "draft spec"
        assert result["work_item_id"] == 99

    def test_execute_missing_both_url_and_name_raises(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="attachment_url.*attachment_name"):
            from codemie_tools.azure_devops.work_item.models import GetWorkItemAttachmentContentInput

            GetWorkItemAttachmentContentInput(work_item_id=1)

    def test_execute_download_failure_wraps_in_tool_exception(self, tool):
        from langchain_core.tools import ToolException

        with patch.object(tool, "_download_attachment", side_effect=Exception("Network timeout")):
            with pytest.raises(ToolException, match="Failed to retrieve attachment content"):
                tool.execute(
                    work_item_id=42,
                    attachment_url="https://dev.azure.com/org/proj/_apis/wit/attachments/abc",
                )

    def test_execute_returns_full_response_structure(self, tool):
        url = "https://dev.azure.com/org/proj/_apis/wit/attachments/abc?fileName=data.json"
        with patch.object(tool, "_download_attachment", return_value=b'{"key": "value"}'):
            result = tool.execute(work_item_id=5, attachment_url=url)

        assert set(result.keys()) == {
            "work_item_id",
            "filename",
            "attachment_note",
            "mime_type",
            "size_bytes",
            "content_type",
            "content",
            "note",
        }
        assert result["attachment_note"] is None
