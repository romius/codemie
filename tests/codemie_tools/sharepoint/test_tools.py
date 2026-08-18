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

from unittest.mock import patch

import pytest
import requests
from langchain_core.tools import ToolException

from codemie_tools.sharepoint.models import SharePointConfig
from codemie_tools.sharepoint.tools import SharePointInput, SharePointTool

from .conftest import DOCX_BYTES, DOCX_MIME, file_object, graph_response

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
TOKEN_URL = "https://login.microsoftonline.com/tenant-id/oauth2/v2.0/token"


class TestSharePointToolDefinition:
    def test_tool_identity(self, sharepoint_tool):
        assert sharepoint_tool.name == "sharepoint_site"
        assert sharepoint_tool.args_schema is SharePointInput

    def test_description_is_populated(self, sharepoint_tool):
        """Without a description the LLM gets no usage recipes for the Graph API."""
        assert sharepoint_tool.description
        assert "relative_url" in sharepoint_tool.description


class TestValidateConfig:
    def test_valid_app_config_passes(self, sharepoint_tool):
        sharepoint_tool._validate_config()

    def test_missing_app_credentials_lists_fields(self):
        tool = SharePointTool(config=SharePointConfig(url="https://contoso.sharepoint.com"))

        with pytest.raises(ValueError) as e:
            tool._validate_config()

        message = str(e.value)
        assert "tenant id" in message
        assert "client id" in message
        assert "client secret" in message

    @pytest.mark.parametrize("auth_type", ["oauth", "oauth_codemie", "oauth_custom"])
    def test_delegated_config_without_tokens_asks_user_to_sign_in(self, auth_type):
        """App-auth fields are legitimately absent here, so the message must not mention them."""
        tool = SharePointTool(config=SharePointConfig(url="https://contoso.sharepoint.com", auth_type=auth_type))

        with pytest.raises(ToolException) as e:
            tool._validate_config()

        assert "not signed in" in str(e.value)
        assert "client secret" not in str(e.value)

    @pytest.mark.parametrize("auth_type", ["oauth", "oauth_codemie", "oauth_custom"])
    def test_delegated_config_with_token_passes(self, auth_type):
        """Missing tenant/client/secret must not block a delegated integration."""
        tool = SharePointTool(
            config=SharePointConfig(
                url="https://contoso.sharepoint.com", auth_type=auth_type, access_token="delegated-token"
            )
        )

        tool._validate_config()

    def test_unknown_auth_type_is_rejected(self):
        tool = SharePointTool(config=SharePointConfig(url="https://contoso.sharepoint.com", auth_type="saml"))

        with pytest.raises(ToolException) as e:
            tool._validate_config()

        assert "unsupported authentication type" in str(e.value)


class TestValidateRelativeUrl:
    @pytest.mark.parametrize(
        "relative_url",
        [
            "//evil.com/sites",  # protocol-relative: would escape the Graph host
            "https://evil.com/sites",  # absolute URL
            "sites/site-id/lists",  # missing leading slash
            "",
        ],
    )
    def test_rejects_non_graph_paths(self, relative_url):
        with pytest.raises(ToolException) as e:
            SharePointTool._validate_relative_url(relative_url)

        assert "beginning with a single '/'" in str(e.value)

    @pytest.mark.parametrize(
        "relative_url, expected",
        [
            ("/sites/site-id/lists", "/sites/site-id/lists"),
            ("  /sites/site-id  ", "/sites/site-id"),
        ],
    )
    def test_accepts_and_strips_graph_paths(self, relative_url, expected):
        assert SharePointTool._validate_relative_url(relative_url) == expected


class TestParseParams:
    def test_none_passes_through(self):
        assert SharePointTool._parse_params(None) is None

    def test_dict_passes_through(self):
        assert SharePointTool._parse_params({"$top": 5}) == {"$top": 5}

    def test_json_string_is_parsed(self):
        assert SharePointTool._parse_params('{"$top": 5}') == {"$top": 5}

    def test_blank_string_is_treated_as_absent(self):
        assert SharePointTool._parse_params("   ") is None

    def test_invalid_json_raises_actionable_error(self):
        with pytest.raises(ToolException) as e:
            SharePointTool._parse_params("{not json}")

        assert "not valid JSON" in str(e.value)

    def test_non_object_json_is_rejected(self):
        with pytest.raises(ToolException) as e:
            SharePointTool._parse_params("[1, 2]")

        assert "must be a JSON object" in str(e.value)


class TestBuildRequestKwargs:
    @pytest.mark.parametrize("method", ["GET", "DELETE"])
    def test_read_methods_send_query_params(self, method):
        assert SharePointTool._build_request_kwargs(method, {"$top": 5}, None) == {"params": {"$top": 5}}

    @pytest.mark.parametrize("method", ["POST", "PATCH", "PUT"])
    def test_write_methods_send_json_body(self, method):
        assert SharePointTool._build_request_kwargs(method, {"fields": {}}, None) == {"json": {"fields": {}}}

    def test_raw_content_overrides_params_as_body(self):
        kwargs = SharePointTool._build_request_kwargs("PUT", {"ignored": True}, "file body")

        assert kwargs["data"] == b"file body"
        assert kwargs["headers"] == {"Content-Type": "application/octet-stream"}
        assert "json" not in kwargs

    def test_raw_content_is_utf8_encoded(self):
        kwargs = SharePointTool._build_request_kwargs("PUT", None, "café — naïve")

        assert kwargs["data"] == "café — naïve".encode("utf-8")

    def test_no_params_sends_nothing(self):
        assert SharePointTool._build_request_kwargs("GET", None, None) == {}


class TestAcquireAppToken:
    @patch("codemie_tools.sharepoint.tools.requests")
    def test_uses_client_credentials_grant(self, mock_requests, sharepoint_tool, token_response):
        mock_requests.post.return_value = token_response

        assert sharepoint_tool._acquire_app_token() == "graph-token"

        mock_requests.post.assert_called_once()
        assert mock_requests.post.call_args.args[0] == TOKEN_URL
        assert mock_requests.post.call_args.kwargs["data"] == {
            "client_id": "client-id",
            "client_secret": "client-secret",
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_token_request_is_time_bounded(self, mock_requests, sharepoint_tool, token_response):
        """An un-timed token call would block a worker indefinitely."""
        mock_requests.post.return_value = token_response

        sharepoint_tool._acquire_app_token()

        assert mock_requests.post.call_args.kwargs["timeout"] == 60

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_failure_names_the_fields_to_check(self, mock_requests, sharepoint_tool):
        mock_requests.post.return_value = graph_response(status_code=401)

        with pytest.raises(ToolException) as e:
            sharepoint_tool._acquire_app_token()

        assert "HTTP 401" in str(e.value)
        assert "tenant ID, client ID and client secret" in str(e.value)

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_failure_does_not_leak_credentials(self, mock_requests, sharepoint_tool):
        mock_requests.post.return_value = graph_response(status_code=401, text="secret leaked: client-secret")

        with pytest.raises(ToolException) as e:
            sharepoint_tool._acquire_app_token()

        assert "client-secret" not in str(e.value)

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_success_without_access_token_is_an_actionable_error(self, mock_requests, sharepoint_tool):
        """An HTTP 200 whose body carries no token must not surface as a KeyError."""
        response = graph_response(status_code=200)
        response.json.return_value = {"token_type": "Bearer"}
        mock_requests.post.return_value = response

        with pytest.raises(ToolException) as e:
            sharepoint_tool._acquire_app_token()

        assert "no access token" in str(e.value)

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_non_json_success_body_is_an_actionable_error(self, mock_requests, sharepoint_tool):
        response = graph_response(status_code=200, text="<html>gateway error</html>")
        response.json.side_effect = ValueError("not json")
        mock_requests.post.return_value = response

        with pytest.raises(ToolException) as e:
            sharepoint_tool._acquire_app_token()

        assert "no access token" in str(e.value)


class TestDelegatedToken:
    """The platform refreshes a delegated token before the tool runs; the tool only uses it."""

    @staticmethod
    def _delegated_tool(**overrides):
        values = {"url": "https://contoso.sharepoint.com", "auth_type": "oauth"}
        values.update(overrides)
        return SharePointTool(config=SharePointConfig(**values))

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_stored_token_is_used_without_contacting_microsoft(self, mock_requests):
        tool = self._delegated_tool(access_token="delegated-token")

        assert tool._get_token() == "delegated-token"
        mock_requests.post.assert_not_called()

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_missing_token_asks_the_user_to_reconnect(self, mock_requests):
        """The tool cannot mint a delegated token, so it must say what the user should do."""
        tool = self._delegated_tool(refresh_token="refresh-token")

        with pytest.raises(ToolException) as e:
            tool._get_token()

        assert "sign-in has expired" in str(e.value)
        mock_requests.post.assert_not_called()

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_rejected_token_reports_reauthentication_not_a_raw_graph_error(self, mock_requests):
        """A 401 on a delegated token means the sign-in is gone - say so, and do not retry."""
        mock_requests.request.return_value = graph_response(status_code=401, text="unauthorized")
        tool = self._delegated_tool(access_token="expired-token")

        with pytest.raises(ToolException) as e:
            tool.execute("GET", "/sites/site-id")

        assert "reconnect the SharePoint" in str(e.value)
        assert mock_requests.request.call_count == 1, "a delegated token cannot be re-minted here"


class TestExecute:
    @patch("codemie_tools.sharepoint.tools.requests")
    def test_get_sends_query_params_to_graph(self, mock_requests, sharepoint_tool, token_response):
        mock_requests.post.return_value = token_response
        mock_requests.request.return_value = graph_response(text='{"value": []}')

        result = sharepoint_tool.execute("GET", "/sites/site-id/lists", {"$top": 5})

        assert result == '{"value": []}'
        call = mock_requests.request.call_args
        assert call.args == ("GET", f"{GRAPH_BASE}/sites/site-id/lists")
        assert call.kwargs["params"] == {"$top": 5}
        assert call.kwargs["headers"]["Authorization"] == "Bearer graph-token"
        assert call.kwargs["timeout"] == 60

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_create_list_item_sends_json_body(self, mock_requests, sharepoint_tool, token_response):
        mock_requests.post.return_value = token_response
        mock_requests.request.return_value = graph_response(status_code=201, text='{"webUrl": "https://contoso/x"}')

        result = sharepoint_tool.execute("POST", "/sites/site-id/lists/list-id/items", {"fields": {"Title": "New"}})

        assert "webUrl" in result
        assert mock_requests.request.call_args.kwargs["json"] == {"fields": {"Title": "New"}}

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_document_upload_sends_raw_body(self, mock_requests, sharepoint_tool, token_response):
        mock_requests.post.return_value = token_response
        mock_requests.request.return_value = graph_response(status_code=201, text='{"webUrl": "https://contoso/f"}')

        sharepoint_tool.execute("PUT", "/sites/site-id/drive/root:/docs/notes.md:/content", None, "# Notes")

        call = mock_requests.request.call_args
        assert call.kwargs["data"] == b"# Notes"
        assert call.kwargs["headers"]["Content-Type"] == "application/octet-stream"
        assert call.kwargs["headers"]["Authorization"] == "Bearer graph-token"

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_method_is_normalized_to_uppercase(self, mock_requests, sharepoint_tool, token_response):
        mock_requests.post.return_value = token_response
        mock_requests.request.return_value = graph_response()

        sharepoint_tool.execute("post", "/sites/site-id/lists/list-id/items", {"fields": {}})

        assert mock_requests.request.call_args.args[0] == "POST"

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_empty_body_returns_success_confirmation(self, mock_requests, sharepoint_tool, token_response):
        """DELETE returns 204 with no body; the agent still needs a confirmation to relay."""
        mock_requests.post.return_value = token_response
        mock_requests.request.return_value = graph_response(status_code=204, text="")

        result = sharepoint_tool.execute("DELETE", "/sites/site-id/lists/list-id/items/1")

        assert result == "DELETE /sites/site-id/lists/list-id/items/1: HTTP 204 (success)"

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_graph_error_body_is_surfaced_for_self_correction(self, mock_requests, sharepoint_tool, token_response):
        mock_requests.post.return_value = token_response
        mock_requests.request.return_value = graph_response(
            status_code=400, text='{"error": {"message": "Invalid column name"}}'
        )

        with pytest.raises(ToolException) as e:
            sharepoint_tool.execute("POST", "/sites/site-id/lists/list-id/items", {"fields": {}})

        assert "HTTP 400" in str(e.value)
        assert "Invalid column name" in str(e.value)

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_token_is_reused_across_calls(self, mock_requests, sharepoint_tool, token_response):
        mock_requests.post.return_value = token_response
        mock_requests.request.return_value = graph_response()

        sharepoint_tool.execute("GET", "/sites/site-id")
        sharepoint_tool.execute("GET", "/sites/site-id/lists")

        assert mock_requests.post.call_count == 1

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_invalid_relative_url_is_rejected_before_any_request(self, mock_requests, sharepoint_tool, token_response):
        mock_requests.post.return_value = token_response

        with pytest.raises(ToolException):
            sharepoint_tool.execute("GET", "//evil.com/sites")

        mock_requests.request.assert_not_called()
        mock_requests.post.assert_not_called()


class TestAttachmentUpload:
    """Uploading a file the user attached to the chat, including binary formats."""

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_binary_attachment_is_uploaded_byte_for_byte(self, mock_requests, tool_with_attachment, token_response):
        mock_requests.post.return_value = token_response
        mock_requests.request.return_value = graph_response(status_code=201, text='{"webUrl": "https://x"}')

        result = tool_with_attachment.execute(
            "PUT", "/sites/site-id/drive/root:/Docs/report.docx:/content", None, None, "report.docx"
        )

        assert "webUrl" in result
        call = mock_requests.request.call_args
        assert call.kwargs["data"] == DOCX_BYTES, "attachment bytes must not be re-encoded"
        assert call.kwargs["headers"]["Content-Type"] == DOCX_MIME

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_attachment_takes_precedence_over_raw_content(self, mock_requests, tool_with_attachment, token_response):
        mock_requests.post.return_value = token_response
        mock_requests.request.return_value = graph_response(status_code=201)

        tool_with_attachment.execute(
            "PUT",
            "/sites/site-id/drive/root:/Docs/report.docx:/content",
            {"ignored": True},
            "text the model invented",
            "report.docx",
        )

        call = mock_requests.request.call_args
        assert call.kwargs["data"] == DOCX_BYTES
        assert "json" not in call.kwargs

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_unknown_attachment_name_lists_what_is_available(self, mock_requests, tool_with_attachment, token_response):
        """The agent must be able to self-correct after guessing a file name."""
        mock_requests.post.return_value = token_response

        with pytest.raises(ToolException) as e:
            tool_with_attachment.execute(
                "PUT", "/sites/site-id/drive/root:/Docs/x.docx:/content", None, None, "wrong-name.docx"
            )

        assert "wrong-name.docx" in str(e.value)
        assert "report.docx" in str(e.value)
        mock_requests.request.assert_not_called()

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_no_attachments_asks_the_user_to_attach_one(self, mock_requests, sharepoint_tool, token_response):
        mock_requests.post.return_value = token_response

        with pytest.raises(ToolException) as e:
            sharepoint_tool.execute(
                "PUT", "/sites/site-id/drive/root:/Docs/report.docx:/content", None, None, "report.docx"
            )

        assert "attach the file to the chat" in str(e.value)
        mock_requests.request.assert_not_called()

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_attachment_without_mime_type_falls_back_to_octet_stream(
        self, mock_requests, sharepoint_config, token_response
    ):
        mock_requests.post.return_value = token_response
        mock_requests.request.return_value = graph_response(status_code=201)
        sharepoint_config.input_files = [file_object(name="data.bin", mime_type="", content=b"\x00\x01")]
        tool = SharePointTool(config=sharepoint_config)

        tool.execute("PUT", "/sites/site-id/drive/root:/Docs/data.bin:/content", None, None, "data.bin")

        assert mock_requests.request.call_args.kwargs["headers"]["Content-Type"] == "application/octet-stream"

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_named_attachment_is_selected_from_several(self, mock_requests, sharepoint_config, token_response):
        mock_requests.post.return_value = token_response
        mock_requests.request.return_value = graph_response(status_code=201)
        sharepoint_config.input_files = [
            file_object(name="other.pdf", mime_type="application/pdf", content=b"%PDF-1.4"),
            file_object(name="report.docx", content=DOCX_BYTES),
        ]
        tool = SharePointTool(config=sharepoint_config)

        tool.execute("PUT", "/sites/site-id/drive/root:/Docs/report.docx:/content", None, None, "report.docx")

        assert mock_requests.request.call_args.kwargs["data"] == DOCX_BYTES

    @patch("codemie_tools.sharepoint.tools.logger")
    @patch("codemie_tools.sharepoint.tools.requests")
    def test_upload_is_audit_logged_by_name_not_content(
        self, mock_requests, mock_logger, tool_with_attachment, token_response
    ):
        mock_requests.post.return_value = token_response
        mock_requests.request.return_value = graph_response(status_code=201)

        tool_with_attachment.execute(
            "PUT", "/sites/site-id/drive/root:/Docs/report.docx:/content", None, None, "report.docx"
        )

        logged = " ".join(call.args[0] for call in mock_logger.info.call_args_list)
        assert "report.docx" in logged
        assert f"{len(DOCX_BYTES)} bytes" in logged
        assert "binary body" not in logged, "file content must never reach the log"

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_requests_without_file_name_are_unaffected(self, mock_requests, tool_with_attachment, token_response):
        """An attached file must not leak into unrelated calls."""
        mock_requests.post.return_value = token_response
        mock_requests.request.return_value = graph_response()

        tool_with_attachment.execute("GET", "/sites/site-id/lists", {"$top": 5})

        call = mock_requests.request.call_args
        assert "data" not in call.kwargs
        assert call.kwargs["params"] == {"$top": 5}


class TestRetryBehaviour:
    @patch("codemie_tools.sharepoint.tools.requests")
    def test_expired_token_is_reminted_and_request_retried_once(self, mock_requests, sharepoint_tool, token_response):
        mock_requests.post.return_value = token_response
        mock_requests.request.side_effect = [
            graph_response(status_code=401, text="token expired"),
            graph_response(text='{"ok": true}'),
        ]

        result = sharepoint_tool.execute("GET", "/sites/site-id")

        assert result == '{"ok": true}'
        assert mock_requests.post.call_count == 2, "token should be re-minted after a 401"
        assert mock_requests.request.call_count == 2

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_persistent_401_is_surfaced_and_not_retried_forever(self, mock_requests, sharepoint_tool, token_response):
        mock_requests.post.return_value = token_response
        mock_requests.request.return_value = graph_response(status_code=401, text="unauthorized")

        with pytest.raises(ToolException) as e:
            sharepoint_tool.execute("GET", "/sites/site-id")

        assert "HTTP 401" in str(e.value)
        assert mock_requests.request.call_count == 2

    @patch("codemie_tools.sharepoint.tools.time")
    @patch("codemie_tools.sharepoint.tools.requests")
    def test_throttling_honours_retry_after(self, mock_requests, mock_time, sharepoint_tool, token_response):
        mock_requests.post.return_value = token_response
        mock_requests.request.side_effect = [
            graph_response(status_code=429, headers={"Retry-After": "3"}),
            graph_response(text='{"ok": true}'),
        ]

        result = sharepoint_tool.execute("GET", "/sites/site-id")

        assert result == '{"ok": true}'
        mock_time.sleep.assert_called_once_with(3)

    @patch("codemie_tools.sharepoint.tools.time")
    @patch("codemie_tools.sharepoint.tools.requests")
    def test_retry_after_is_capped(self, mock_requests, mock_time, sharepoint_tool, token_response):
        """A hostile or extreme Retry-After must not park a worker for hours."""
        mock_requests.post.return_value = token_response
        mock_requests.request.side_effect = [
            graph_response(status_code=429, headers={"Retry-After": "86400"}),
            graph_response(text="{}"),
        ]

        sharepoint_tool.execute("GET", "/sites/site-id")

        mock_time.sleep.assert_called_once_with(60)

    @patch("codemie_tools.sharepoint.tools.time")
    @patch("codemie_tools.sharepoint.tools.requests")
    def test_throttling_without_retry_after_uses_default(
        self, mock_requests, mock_time, sharepoint_tool, token_response
    ):
        mock_requests.post.return_value = token_response
        mock_requests.request.side_effect = [graph_response(status_code=429), graph_response(text="{}")]

        sharepoint_tool.execute("GET", "/sites/site-id")

        mock_time.sleep.assert_called_once_with(5)

    @patch("codemie_tools.sharepoint.tools.time")
    @patch("codemie_tools.sharepoint.tools.requests")
    def test_http_date_retry_after_falls_back_to_default(
        self, mock_requests, mock_time, sharepoint_tool, token_response
    ):
        """Retry-After may legally be an HTTP-date; it must not crash the retry."""
        mock_requests.post.return_value = token_response
        mock_requests.request.side_effect = [
            graph_response(status_code=429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
            graph_response(text="{}"),
        ]

        sharepoint_tool.execute("GET", "/sites/site-id")

        mock_time.sleep.assert_called_once_with(5)

    @patch("codemie_tools.sharepoint.tools.time")
    @patch("codemie_tools.sharepoint.tools.requests")
    def test_negative_retry_after_is_clamped_to_zero(self, mock_requests, mock_time, sharepoint_tool, token_response):
        mock_requests.post.return_value = token_response
        mock_requests.request.side_effect = [
            graph_response(status_code=429, headers={"Retry-After": "-3"}),
            graph_response(text="{}"),
        ]

        sharepoint_tool.execute("GET", "/sites/site-id")

        mock_time.sleep.assert_called_once_with(0)


class TestAuditLogging:
    """The platform logger does not propagate to caplog, so assert on the module logger directly."""

    @staticmethod
    def _info_messages(mock_logger):
        return [call.args[0] for call in mock_logger.info.call_args_list]

    @patch("codemie_tools.sharepoint.tools.logger")
    @patch("codemie_tools.sharepoint.tools.requests")
    def test_write_operations_are_logged(self, mock_requests, mock_logger, sharepoint_tool, token_response):
        """AC: all SharePoint actions are logged."""
        mock_requests.post.return_value = token_response
        mock_requests.request.return_value = graph_response(status_code=201, text="{}")

        sharepoint_tool.execute("POST", "/sites/site-id/lists/list-id/items", {"fields": {}})

        assert "SharePoint write: POST /sites/site-id/lists/list-id/items -> HTTP 201" in self._info_messages(
            mock_logger
        )

    @patch("codemie_tools.sharepoint.tools.logger")
    @patch("codemie_tools.sharepoint.tools.requests")
    def test_audit_log_never_contains_credentials(self, mock_requests, mock_logger, sharepoint_tool, token_response):
        mock_requests.post.return_value = token_response
        mock_requests.request.return_value = graph_response(status_code=201, text="{}")

        sharepoint_tool.execute("PATCH", "/sites/site-id/lists/list-id/items/1/fields", {"Title": "secret value"})

        logged = " ".join(self._info_messages(mock_logger))
        assert "graph-token" not in logged
        assert "client-secret" not in logged
        assert "secret value" not in logged, "request bodies must not be written to the audit log"

    @patch("codemie_tools.sharepoint.tools.logger")
    @patch("codemie_tools.sharepoint.tools.requests")
    def test_reads_are_audit_logged(self, mock_requests, mock_logger, sharepoint_tool, token_response):
        """AC: all SharePoint actions are logged - reads included."""
        mock_requests.post.return_value = token_response
        mock_requests.request.return_value = graph_response()

        sharepoint_tool.execute("GET", "/sites/site-id")

        messages = self._info_messages(mock_logger)
        assert "SharePoint read: GET /sites/site-id -> HTTP 200" in messages
        assert not any("SharePoint write" in message for message in messages)


class TestHealthcheck:
    @patch("codemie_tools.sharepoint.tools.requests")
    def test_success_probes_the_configured_tenant_site(self, mock_requests, sharepoint_tool, token_response):
        mock_requests.post.return_value = token_response
        mock_requests.get.return_value = graph_response()

        assert sharepoint_tool.healthcheck() == (True, "")
        assert mock_requests.get.call_args.args[0] == f"{GRAPH_BASE}/sites/contoso.sharepoint.com"
        assert mock_requests.get.call_args.kwargs["timeout"] == 60

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_bad_credentials_report_which_fields_to_check(self, mock_requests, sharepoint_tool):
        mock_requests.post.return_value = graph_response(status_code=401)

        ok, message = sharepoint_tool.healthcheck()

        assert ok is False
        assert "tenant ID, client ID and client secret" in message

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_missing_site_permission_is_reported(self, mock_requests, sharepoint_tool, token_response):
        mock_requests.post.return_value = token_response
        forbidden = graph_response(status_code=403)
        forbidden.raise_for_status.side_effect = requests.HTTPError("403 Client Error: Forbidden")
        mock_requests.get.return_value = forbidden

        ok, message = sharepoint_tool.healthcheck()

        assert ok is False
        assert "403" in message

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_delegated_integration_without_tokens_reports_not_signed_in(self, mock_requests):
        tool = SharePointTool(config=SharePointConfig(url="https://contoso.sharepoint.com", auth_type="oauth"))

        ok, message = tool.healthcheck()

        assert ok is False
        assert "not signed in" in message
        mock_requests.post.assert_not_called()

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_delegated_integration_uses_stored_token(self, mock_requests):
        mock_requests.get.return_value = graph_response()
        tool = SharePointTool(
            config=SharePointConfig(
                url="https://contoso.sharepoint.com", auth_type="oauth", access_token="delegated-token"
            )
        )

        assert tool.healthcheck() == (True, "")
        assert mock_requests.get.call_args.kwargs["headers"]["Authorization"] == "Bearer delegated-token"
        mock_requests.post.assert_not_called()

    @patch("codemie_tools.sharepoint.tools.requests")
    def test_bare_hostname_url_still_validates_credentials(self, mock_requests, token_response):
        """A stored url without a scheme has no netloc; token acquisition alone proves the credentials."""
        mock_requests.post.return_value = token_response
        tool = SharePointTool(
            config=SharePointConfig(
                url="contoso.sharepoint.com",
                tenant_id="tenant-id",
                client_id="client-id",
                client_secret="client-secret",
            )
        )

        assert tool.healthcheck() == (True, "")
        assert mock_requests.get.call_args.args[0] == f"{GRAPH_BASE}/sites/contoso.sharepoint.com"
