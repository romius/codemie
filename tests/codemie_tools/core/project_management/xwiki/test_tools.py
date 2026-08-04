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

import base64
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools import ToolException

from codemie_tools.core.project_management.xwiki.models import XWikiConfig
from codemie_tools.core.project_management.xwiki.tools import (
    CreatePageAttachmentTool,
    CreatePageCommentTool,
    CreatePageTool,
    DeletePageAttachmentTool,
    DeletePageTool,
    GetPageAttachmentTool,
    GetPageCommentTool,
    GetPageTool,
    GetSpaceTool,
    GetWikiTool,
    ListPageAttachmentsTool,
    ListPageChildrenTool,
    ListPageCommentsTool,
    ListPageTagsTool,
    ListPagesTool,
    ListSpacesTool,
    ListWikiPagesTool,
    ListWikiTagsTool,
    ListWikisTool,
    ModifyPageTool,
    ReadPageAttachmentContentTool,
    SearchSpaceTool,
    SearchWikiTool,
    SetPageTagsTool,
)

BASE_URL = "https://wiki.example.com"


def _mock_response(status: int = 200, reason: str = "OK", text: str = "{}"):
    r = MagicMock()
    r.status_code = status
    r.reason_phrase = reason
    r.text = text
    return r


def _tool(cls, config):
    tool = cls(config=config)
    tool._request = MagicMock(return_value=(_mock_response(), "{}"))
    return tool


# ---------------------------------------------------------------------------
# Auth headers
# ---------------------------------------------------------------------------


class TestBuildAuthHeaders:
    def test_basic_auth(self, xwiki_config):
        tool = ListWikisTool(config=xwiki_config)
        headers = tool._build_auth_headers()
        expected = base64.b64encode(f"{xwiki_config.username}:{xwiki_config.token}".encode()).decode()
        assert headers["Authorization"] == f"Basic {expected}"

    def test_basic_auth_no_username(self):
        config = XWikiConfig(url=BASE_URL, token="tok")
        tool = ListWikisTool(config=config)
        headers = tool._build_auth_headers()
        expected = base64.b64encode(f":{config.token}".encode()).decode()
        assert headers["Authorization"] == f"Basic {expected}"

    def test_bearer_auth(self, xwiki_config_bearer):
        tool = ListWikisTool(config=xwiki_config_bearer)
        headers = tool._build_auth_headers()
        assert headers["Authorization"] == f"Bearer {xwiki_config_bearer.token}"


# ---------------------------------------------------------------------------
# _format_result
# ---------------------------------------------------------------------------


class TestFormatResult:
    def test_plain_result(self, xwiki_config):
        tool = ListWikisTool(config=xwiki_config)
        resp = _mock_response(200, "OK", '{"wikis": []}')
        result = tool._format_result("GET", "http://x/rest/wikis", resp, '{"wikis": []}')
        assert "HTTP: GET http://x/rest/wikis -> 200 OK" in result
        assert '{"wikis": []}' in result

    def test_markdown_conversion_on_success(self, xwiki_config):
        tool = ListWikisTool(config=xwiki_config)
        resp = _mock_response(200, "OK", "<h1>Title</h1>")
        result = tool._format_result("GET", "http://x", resp, "<h1>Title</h1>", is_markdown=True)
        assert "# Title" in result

    def test_no_markdown_conversion_on_error_status(self, xwiki_config):
        tool = ListWikisTool(config=xwiki_config)
        resp = _mock_response(404, "Not Found", "<h1>Not Found</h1>")
        result = tool._format_result("GET", "http://x", resp, "<h1>Not Found</h1>", is_markdown=True)
        assert "<h1>Not Found</h1>" in result


# ---------------------------------------------------------------------------
# _request
# ---------------------------------------------------------------------------


class TestRequest:
    @patch("codemie_tools.core.project_management.xwiki.tools.httpx.Client")
    def test_get_calls_client_get(self, mock_client_cls, xwiki_config):
        from unittest.mock import ANY

        mock_client = MagicMock()
        mock_resp = _mock_response(text="body")
        mock_client.get.return_value = mock_resp
        mock_client_cls.return_value.__enter__.return_value = mock_client

        tool = ListWikisTool(config=xwiki_config)
        resp, text = tool._request("GET", "http://x/rest/wikis", params={"number": 10})

        mock_client.get.assert_called_once_with("http://x/rest/wikis", params={"number": 10}, headers=ANY)
        assert resp is mock_resp

    @patch("codemie_tools.core.project_management.xwiki.tools.httpx.Client")
    def test_request_error_raises_tool_exception(self, mock_client_cls, xwiki_config):
        import httpx as _httpx

        mock_client = MagicMock()
        mock_client.get.side_effect = _httpx.RequestError("timeout")
        mock_client_cls.return_value.__enter__.return_value = mock_client

        tool = ListWikisTool(config=xwiki_config)
        with pytest.raises(ToolException, match="xWiki request failed"):
            tool._request("GET", "http://x/rest/wikis")


# ---------------------------------------------------------------------------
# _healthcheck
# ---------------------------------------------------------------------------


class TestHealthcheck:
    @patch("codemie_tools.core.project_management.xwiki.tools.httpx.Client")
    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_healthcheck_success(self, mock_validate, mock_client_cls, xwiki_config):
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_response(200)
        mock_client_cls.return_value.__enter__.return_value = mock_client

        tool = ListWikisTool(config=xwiki_config)
        tool._healthcheck()

        mock_validate.assert_called_once_with(xwiki_config)
        mock_client.get.assert_called_once()

    @patch("codemie_tools.core.project_management.xwiki.tools.httpx.Client")
    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_healthcheck_non_200_raises(self, mock_validate, mock_client_cls, xwiki_config):
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_response(401, "Unauthorized")
        mock_client_cls.return_value.__enter__.return_value = mock_client

        tool = ListWikisTool(config=xwiki_config)
        with pytest.raises(AssertionError, match="healthcheck failed"):
            tool._healthcheck()


# ---------------------------------------------------------------------------
# Wiki tools
# ---------------------------------------------------------------------------


class TestWikiTools:
    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_list_wikis_url(self, mock_validate, xwiki_config):
        tool = _tool(ListWikisTool, xwiki_config)
        tool.execute(number=10, start=5)
        url = tool._request.call_args[0][1]
        assert url == f"{BASE_URL}/rest/wikis"
        tool._request.assert_called_once_with("GET", url, params={"number": 10, "start": 5})

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_list_wikis_result_format(self, mock_validate, xwiki_config):
        tool = _tool(ListWikisTool, xwiki_config)
        result = tool.execute()
        assert "HTTP: GET" in result

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_get_wiki_url(self, mock_validate, xwiki_config):
        tool = _tool(GetWikiTool, xwiki_config)
        tool.execute(wiki="teamwiki")
        url = tool._request.call_args[0][1]
        assert url == f"{BASE_URL}/rest/wikis/teamwiki"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_get_wiki_default_wiki(self, mock_validate, xwiki_config):
        tool = _tool(GetWikiTool, xwiki_config)
        tool.execute()
        url = tool._request.call_args[0][1]
        assert url.endswith("/rest/wikis/xwiki")


# ---------------------------------------------------------------------------
# Space tools
# ---------------------------------------------------------------------------


class TestSpaceTools:
    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_list_spaces_url(self, mock_validate, xwiki_config):
        tool = _tool(ListSpacesTool, xwiki_config)
        tool.execute(wiki="xwiki", number=20, start=0)
        url = tool._request.call_args[0][1]
        assert url == f"{BASE_URL}/rest/wikis/xwiki/spaces"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_get_space_simple(self, mock_validate, xwiki_config):
        tool = _tool(GetSpaceTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main")
        url = tool._request.call_args[0][1]
        assert url == f"{BASE_URL}/rest/wikis/xwiki/spaces/Main"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_get_space_nested(self, mock_validate, xwiki_config):
        tool = _tool(GetSpaceTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main.Sandbox")
        url = tool._request.call_args[0][1]
        assert url == f"{BASE_URL}/rest/wikis/xwiki/spaces/Main/spaces/Sandbox"


# ---------------------------------------------------------------------------
# Page tools
# ---------------------------------------------------------------------------


class TestPageTools:
    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_list_pages_url(self, mock_validate, xwiki_config):
        tool = _tool(ListPagesTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main", number=50, start=0)
        url = tool._request.call_args[0][1]
        assert url == f"{BASE_URL}/rest/wikis/xwiki/spaces/Main/pages"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_list_wiki_pages_url(self, mock_validate, xwiki_config):
        tool = _tool(ListWikiPagesTool, xwiki_config)
        tool.execute(wiki="xwiki")
        url = tool._request.call_args[0][1]
        assert url == f"{BASE_URL}/rest/wikis/xwiki/pages"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_list_page_children_url(self, mock_validate, xwiki_config):
        tool = _tool(ListPageChildrenTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main", page="WebHome")
        url = tool._request.call_args[0][1]
        assert url == f"{BASE_URL}/rest/wikis/xwiki/spaces/Main/pages/WebHome/children"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_get_page_url(self, mock_validate, xwiki_config):
        tool = _tool(GetPageTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main", page="MyPage")
        url = tool._request.call_args[0][1]
        assert url == f"{BASE_URL}/rest/wikis/xwiki/spaces/Main/pages/MyPage"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_get_page_markdown(self, mock_validate, xwiki_config):
        resp = _mock_response(200, "OK", "<h1>Title</h1>")
        tool = GetPageTool(config=xwiki_config)
        tool._request = MagicMock(return_value=(resp, "<h1>Title</h1>"))
        result = tool.execute(wiki="xwiki", space="Main", page="MyPage", is_markdown=True)
        assert "# Title" in result

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_create_page_uses_put(self, mock_validate, xwiki_config):
        tool = _tool(CreatePageTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main", page="NewPage", title="New Page", content="Hello")
        method = tool._request.call_args[0][0]
        assert method == "PUT"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_create_page_body(self, mock_validate, xwiki_config):
        tool = _tool(CreatePageTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main", page="NewPage", title="Title", content="Body", syntax="xwiki/2.1")
        body = tool._request.call_args[1]["json_body"]
        assert body == {"title": "Title", "content": "Body", "syntax": "xwiki/2.1"}

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_modify_page_without_title_omits_key(self, mock_validate, xwiki_config):
        tool = _tool(ModifyPageTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main", page="MyPage", content="Updated", title=None)
        body = tool._request.call_args[1]["json_body"]
        assert "title" not in body
        assert body["content"] == "Updated"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_modify_page_with_title_includes_key(self, mock_validate, xwiki_config):
        tool = _tool(ModifyPageTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main", page="MyPage", content="Updated", title="New Title")
        body = tool._request.call_args[1]["json_body"]
        assert body["title"] == "New Title"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_delete_page_uses_delete(self, mock_validate, xwiki_config):
        tool = _tool(DeletePageTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main", page="OldPage")
        method = tool._request.call_args[0][0]
        assert method == "DELETE"


# ---------------------------------------------------------------------------
# Tag tools
# ---------------------------------------------------------------------------


class TestTagTools:
    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_list_wiki_tags_url(self, mock_validate, xwiki_config):
        tool = _tool(ListWikiTagsTool, xwiki_config)
        tool.execute(wiki="xwiki")
        url = tool._request.call_args[0][1]
        assert url == f"{BASE_URL}/rest/wikis/xwiki/tags"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_list_page_tags_url(self, mock_validate, xwiki_config):
        tool = _tool(ListPageTagsTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main", page="MyPage")
        url = tool._request.call_args[0][1]
        assert url == f"{BASE_URL}/rest/wikis/xwiki/spaces/Main/pages/MyPage/tags"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_set_page_tags_body_format(self, mock_validate, xwiki_config):
        tool = _tool(SetPageTagsTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main", page="MyPage", tags=["docs", "api"])
        body = tool._request.call_args[1]["json_body"]
        assert body == {"tags": [{"name": "docs"}, {"name": "api"}]}

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_set_page_tags_empty_list(self, mock_validate, xwiki_config):
        tool = _tool(SetPageTagsTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main", page="MyPage", tags=[])
        body = tool._request.call_args[1]["json_body"]
        assert body == {"tags": []}

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_set_page_tags_uses_put(self, mock_validate, xwiki_config):
        tool = _tool(SetPageTagsTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main", page="MyPage", tags=[])
        method = tool._request.call_args[0][0]
        assert method == "PUT"


# ---------------------------------------------------------------------------
# Comment tools
# ---------------------------------------------------------------------------


class TestCommentTools:
    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_list_page_comments_url(self, mock_validate, xwiki_config):
        tool = _tool(ListPageCommentsTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main", page="MyPage")
        url = tool._request.call_args[0][1]
        assert url == f"{BASE_URL}/rest/wikis/xwiki/spaces/Main/pages/MyPage/comments"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_get_page_comment_url(self, mock_validate, xwiki_config):
        tool = _tool(GetPageCommentTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main", page="MyPage", comment_id=42)
        url = tool._request.call_args[0][1]
        assert url == f"{BASE_URL}/rest/wikis/xwiki/spaces/Main/pages/MyPage/comments/42"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_create_page_comment_uses_post(self, mock_validate, xwiki_config):
        tool = _tool(CreatePageCommentTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main", page="MyPage", text="Nice!")
        method = tool._request.call_args[0][0]
        assert method == "POST"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_create_page_comment_body(self, mock_validate, xwiki_config):
        tool = _tool(CreatePageCommentTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main", page="MyPage", text="Nice!")
        body = tool._request.call_args[1]["json_body"]
        assert body == {"text": "Nice!"}


# ---------------------------------------------------------------------------
# Attachment tools
# ---------------------------------------------------------------------------


class TestAttachmentTools:
    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_list_page_attachments_url(self, mock_validate, xwiki_config):
        tool = _tool(ListPageAttachmentsTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main", page="MyPage")
        url = tool._request.call_args[0][1]
        assert url == f"{BASE_URL}/rest/wikis/xwiki/spaces/Main/pages/MyPage/attachments"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_get_page_attachment_url(self, mock_validate, xwiki_config):
        tool = _tool(GetPageAttachmentTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main", page="MyPage", filename="doc.pdf")
        url = tool._request.call_args[0][1]
        assert url == f"{BASE_URL}/rest/wikis/xwiki/spaces/Main/pages/MyPage/attachments/doc.pdf"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_delete_page_attachment_uses_delete(self, mock_validate, xwiki_config):
        tool = _tool(DeletePageAttachmentTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main", page="MyPage", filename="old.pdf")
        method = tool._request.call_args[0][0]
        assert method == "DELETE"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_create_page_attachment_no_files_raises(self, mock_validate, xwiki_config):
        real_tool = CreatePageAttachmentTool(config=xwiki_config)
        real_tool._resolve_files = MagicMock(return_value={})
        with pytest.raises(ToolException, match="No files available"):
            real_tool.execute(wiki="xwiki", space="Main", page="MyPage")


# ---------------------------------------------------------------------------
# ReadPageAttachmentContentTool content processing
# ---------------------------------------------------------------------------


class TestReadPageAttachmentContentTool:
    def _make_tool(self, xwiki_config):
        return ReadPageAttachmentContentTool(config=xwiki_config)

    def test_detect_mime_type_pdf(self, xwiki_config):
        tool = self._make_tool(xwiki_config)
        assert tool._detect_mime_type("file.pdf") == "application/pdf"

    def test_detect_mime_type_unknown(self, xwiki_config):
        tool = self._make_tool(xwiki_config)
        assert tool._detect_mime_type("file.qzxunknown99") == "application/octet-stream"

    def test_is_text_based_text_mime(self, xwiki_config):
        tool = self._make_tool(xwiki_config)
        assert tool._is_text_based("text/plain", "file.txt") is True

    def test_is_text_based_known_extension(self, xwiki_config):
        tool = self._make_tool(xwiki_config)
        assert tool._is_text_based("application/octet-stream", "file.md") is True

    def test_is_text_based_binary(self, xwiki_config):
        tool = self._make_tool(xwiki_config)
        assert tool._is_text_based("application/zip", "file.zip") is False

    def test_build_base64_response_small(self, xwiki_config):
        tool = self._make_tool(xwiki_config)
        data = b"hello"
        result = tool._build_base64_response(data, "note")
        assert result["content_type"] == "base64"
        assert result["content"] == "aGVsbG8="
        assert result["note"] == "note"

    def test_build_base64_response_too_large(self, xwiki_config):
        tool = self._make_tool(xwiki_config)
        data = b"x" * (tool._MAX_BASE64_BYTES + 1)
        result = tool._build_base64_response(data, "note")
        assert result["content_type"] == "metadata_only"
        assert result["content"] is None
        assert "too large" in result["note"]

    def test_process_content_text_file(self, xwiki_config):
        tool = self._make_tool(xwiki_config)
        result = tool._process_content("notes.txt", b"hello world")
        assert result["content_type"] == "text"
        assert result["content"] == "hello world"

    def test_process_content_json_file(self, xwiki_config):
        tool = self._make_tool(xwiki_config)
        result = tool._process_content("data.json", b'{"key": "value"}')
        assert result["content_type"] == "text"
        assert '"key"' in result["content"]

    def test_process_content_image_returns_base64(self, xwiki_config):
        tool = self._make_tool(xwiki_config)
        result = tool._process_content("photo.png", b"\x89PNG\r\n")
        assert result["content_type"] in ("base64", "metadata_only")

    def test_process_content_xlsb_passes_file_ext(self, xwiki_config):
        mock_processor = MagicMock()
        mock_processor.load.return_value = [{"name": "Sheet1", "data": [["val"]]}]
        mock_processor.convert.return_value = "| val |"
        with patch(
            "codemie_tools.core.project_management.xwiki.tools.XlsxProcessor",
            return_value=mock_processor,
        ):
            tool = self._make_tool(xwiki_config)
            result = tool._process_content("data.xlsb", b"xlsb_bytes")
        mock_processor.load.assert_called_once_with(b"xlsb_bytes", file_ext=".xlsb")
        assert result["content_type"] == "text"
        assert result["content"] == "| val |"

    def test_process_content_xlsb_extension_fallback_when_mime_unknown(self, xwiki_config):
        """In a slim Linux container without /etc/mime.types, guess_type returns None for
        .xlsb; the extension must still route to Excel extraction rather than base64
        (EPMCDME-11738 follow-up item 3)."""
        mock_processor = MagicMock()
        mock_processor.load.return_value = {"Sheet1": MagicMock()}
        mock_processor.convert.return_value = "| val |"
        with patch("mimetypes.guess_type", return_value=(None, None)):
            with patch(
                "codemie_tools.core.project_management.xwiki.tools.XlsxProcessor",
                return_value=mock_processor,
            ):
                tool = self._make_tool(xwiki_config)
                result = tool._process_content("report.xlsb", b"xlsb_bytes")
        assert result["content_type"] == "text"
        assert result["content"] == "| val |"
        assert mock_processor.load.call_args.kwargs.get("file_ext") == ".xlsb"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    @patch("codemie_tools.core.project_management.xwiki.tools.httpx.Client")
    def test_execute_http_error_raises(self, mock_client_cls, mock_validate, xwiki_config):
        mock_client = MagicMock()
        mock_client.get.return_value = _mock_response(404, "Not Found")
        mock_client_cls.return_value.__enter__.return_value = mock_client

        tool = ReadPageAttachmentContentTool(config=xwiki_config)
        with pytest.raises(ToolException, match="Failed to download"):
            tool.execute(wiki="xwiki", space="Main", page="MyPage", filename="missing.pdf")

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    @patch("codemie_tools.core.project_management.xwiki.tools.httpx.Client")
    def test_execute_success_returns_metadata(self, mock_client_cls, mock_validate, xwiki_config):
        mock_client = MagicMock()
        resp = _mock_response(200, "OK")
        resp.content = b"hello world"
        mock_client.get.return_value = resp
        mock_client_cls.return_value.__enter__.return_value = mock_client

        tool = ReadPageAttachmentContentTool(config=xwiki_config)
        result = tool.execute(wiki="xwiki", space="Main", page="MyPage", filename="notes.txt")

        assert result["filename"] == "notes.txt"
        assert result["size_bytes"] == len(b"hello world")
        assert result["content_type"] == "text"
        assert result["content"] == "hello world"


# ---------------------------------------------------------------------------
# Search tools
# ---------------------------------------------------------------------------


class TestSearchTools:
    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_search_wiki_url(self, mock_validate, xwiki_config):
        tool = _tool(SearchWikiTool, xwiki_config)
        tool.execute(wiki="xwiki", query="hello")
        url = tool._request.call_args[0][1]
        assert url == f"{BASE_URL}/rest/wikis/xwiki/search"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_search_wiki_params_no_space(self, mock_validate, xwiki_config):
        tool = _tool(SearchWikiTool, xwiki_config)
        tool.execute(wiki="xwiki", query="hello", scope="content", number=5)
        params = tool._request.call_args[1]["params"]
        assert params["q"] == "hello"
        assert params["scope"] == "content"
        assert "space" not in params

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_search_wiki_with_space_includes_param(self, mock_validate, xwiki_config):
        tool = _tool(SearchWikiTool, xwiki_config)
        tool.execute(wiki="xwiki", query="hello", space="Main")
        params = tool._request.call_args[1]["params"]
        assert params["space"] == "Main"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_search_space_url_uses_spaces_path(self, mock_validate, xwiki_config):
        tool = _tool(SearchSpaceTool, xwiki_config)
        tool.execute(wiki="xwiki", space="Main.Sandbox", query="hello")
        url = tool._request.call_args[0][1]
        assert url == f"{BASE_URL}/rest/wikis/xwiki/spaces/Main/spaces/Sandbox/search"

    @patch("codemie_tools.core.project_management.xwiki.tools.validate_creds")
    def test_search_space_markdown(self, mock_validate, xwiki_config):
        resp = _mock_response(200, "OK", "<h1>Result</h1>")
        tool = SearchSpaceTool(config=xwiki_config)
        tool._request = MagicMock(return_value=(resp, "<h1>Result</h1>"))
        result = tool.execute(wiki="xwiki", space="Main", query="hello", is_markdown=True)
        assert "# Result" in result
