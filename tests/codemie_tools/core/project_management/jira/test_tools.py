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

import re
from unittest.mock import MagicMock, patch

import pytest
from atlassian import Jira

from codemie_tools.core.project_management.jira.models import JiraConfig
from codemie_tools.core.project_management.jira.tools import (
    JIRA_TEST_URL,
    JIRA_ERROR_MSG,
    JiraInput,
    GenericJiraIssueTool,
)
from codemie_tools.core.project_management.jira.tools_vars import GENERIC_JIRA_TOOL, get_jira_tool_description


# Create a mock class for testing that avoids the BaseTool validation issues
class MockGenericJiraIssueTool:
    """Mock class for testing GenericJiraIssueTool without BaseTool validation issues."""

    def __init__(self, config):
        self.config = config
        self.name = GENERIC_JIRA_TOOL.name
        self.description = GENERIC_JIRA_TOOL.description
        self.args_schema = JiraInput
        self.issue_search_pattern = r'/rest/api/\d+/search'
        if config.cloud:
            self.issue_search_pattern = r'/rest/api/3/search/jql'
            self.description = get_jira_tool_description(api_version=3)
        self.jira = None

    def execute(self, method, relative_url, params="", *args):
        """Mock execute method."""
        jira = Jira(
            url=self.config.url,
            username=self.config.username if self.config.username else None,
            token=self.config.token if not self.config.cloud else None,
            password=self.config.token if self.config.cloud else None,
            cloud=self.config.cloud,
        )
        self.jira = jira

        from codemie_tools.core.project_management.jira.utils import validate_jira_creds, parse_payload_params

        validate_jira_creds(jira)
        payload_params = parse_payload_params(params)

        if method == "GET":
            response_text, response = self._handle_get_request(relative_url, payload_params)
        else:
            response_text, response = self._handle_non_get_request(method, relative_url, payload_params)

        return f"HTTP: {method} {relative_url} -> {response.status_code} {response.reason} {response_text}"

    def _handle_get_request(self, relative_url, payload_params):
        """Mock _handle_get_request method."""
        response = self.jira.request(
            method="GET",
            path=relative_url,
            params=payload_params,
            advanced_mode=True,
            headers={"content-type": "application/json"},
        )
        self.jira.raise_for_status(response)

        from codemie_tools.core.project_management.jira.utils import process_search_response

        if re.match(self.issue_search_pattern, relative_url):
            response_text = process_search_response(self.jira.url, response, payload_params)
        else:
            response_text = response.text
        return response_text, response

    def _handle_non_get_request(self, method, relative_url, payload_params):
        """Mock _handle_non_get_request method."""
        response = self.jira.request(method=method, path=relative_url, data=payload_params, advanced_mode=True)
        self.jira.raise_for_status(response)
        return response.text, response

    def _healthcheck(self):
        """Mock _healthcheck method."""
        import json

        response = self.jira.request(
            method="GET",
            path=JIRA_TEST_URL,
            params={},
            advanced_mode=True,
            headers={"content-type": "application/json"},
        )
        if response.status_code != 200:
            raise AssertionError(JIRA_ERROR_MSG)
        try:
            data = json.loads(response.text)
        except (json.JSONDecodeError, TypeError):
            raise AssertionError(JIRA_ERROR_MSG)
        if "displayName" not in data:
            raise AssertionError(JIRA_ERROR_MSG)


class TestGenericJiraIssueTool:
    """Tests for the GenericJiraIssueTool class."""

    @pytest.fixture
    def jira_config(self):
        """Fixture for JiraConfig."""
        return JiraConfig(url="https://jira.example.com", token="abc123")

    @pytest.fixture
    def jira_cloud_config(self):
        """Fixture for JiraConfig with cloud=True."""
        return JiraConfig(url="https://jira.example.com", token="abc123", username="user@example.com", cloud=True)

    @pytest.fixture
    def mock_jira(self):
        """Fixture for mocked Jira client."""
        with patch("atlassian.Jira") as mock_jira_class:
            mock_jira_instance = MagicMock(spec=Jira)
            mock_jira_class.return_value = mock_jira_instance
            yield mock_jira_instance

    def test_init_server(self, jira_config):
        """Test initialization with server config."""
        tool = MockGenericJiraIssueTool(jira_config)

        assert tool.config == jira_config
        assert tool.issue_search_pattern == r'/rest/api/\d+/search'
        assert "JIRA Tool for Official Atlassian JIRA REST API V2" in tool.description

    def test_init_cloud(self, jira_cloud_config):
        """Test initialization with cloud config."""
        tool = MockGenericJiraIssueTool(jira_cloud_config)

        assert tool.config == jira_cloud_config
        assert tool.issue_search_pattern == r'/rest/api/3/search/jql'
        assert "JIRA Tool for Official Atlassian JIRA REST API V3" in tool.description

    @patch("codemie_tools.core.project_management.jira.utils.validate_jira_creds")
    def test_execute_get_request(self, mock_validate_creds, jira_config, mock_jira):
        """Test execute method with GET request."""
        # Setup mock response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.reason = "OK"
        mock_response.text = '{"key": "value"}'

        mock_jira.request.return_value = mock_response

        # Create tool and execute
        tool = MockGenericJiraIssueTool(jira_config)

        with patch.object(
            tool, '_handle_get_request', return_value=(mock_response.text, mock_response)
        ) as mock_handle_get:
            result = tool.execute(method="GET", relative_url="/rest/api/2/issue/TEST-123")

        # Verify
        mock_validate_creds.assert_called_once()
        mock_handle_get.assert_called_once()
        assert "HTTP: GET /rest/api/2/issue/TEST-123 -> 200 OK" in result

    @patch("codemie_tools.core.project_management.jira.utils.validate_jira_creds")
    def test_execute_post_request(self, mock_validate_creds, jira_config, mock_jira):
        """Test execute method with POST request."""
        # Setup mock response
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.reason = "Created"
        mock_response.text = '{"id": "TEST-123"}'

        mock_jira.request.return_value = mock_response

        # Create tool and execute
        tool = MockGenericJiraIssueTool(jira_config)

        with patch.object(
            tool, '_handle_non_get_request', return_value=(mock_response.text, mock_response)
        ) as mock_handle_non_get:
            result = tool.execute(
                method="POST", relative_url="/rest/api/2/issue", params='{"fields": {"summary": "Test Issue"}}'
            )

        # Verify
        mock_validate_creds.assert_called_once()
        mock_handle_non_get.assert_called_once()
        assert "HTTP: POST /rest/api/2/issue -> 201 Created" in result

    @patch("codemie_tools.core.project_management.jira.utils.parse_payload_params")
    def test_handle_get_request_regular(self, mock_parse_params, jira_config, mock_jira):
        """Test _handle_get_request with a regular (non-search) endpoint."""
        # Setup
        mock_parse_params.return_value = {"fields": "summary,description"}

        mock_response = MagicMock()
        mock_response.text = '{"key": "TEST-123", "fields": {"summary": "Test Issue"}}'
        mock_jira.request.return_value = mock_response

        # Create tool and execute
        tool = MockGenericJiraIssueTool(jira_config)
        tool.jira = mock_jira  # Set the jira attribute directly for testing

        response_text, response = tool._handle_get_request(
            "/rest/api/2/issue/TEST-123", {"fields": "summary,description"}
        )

        # Verify
        mock_jira.request.assert_called_once_with(
            method="GET",
            path="/rest/api/2/issue/TEST-123",
            params={"fields": "summary,description"},
            advanced_mode=True,
            headers={"content-type": "application/json"},
        )
        mock_jira.raise_for_status.assert_called_once_with(mock_response)
        assert response_text == '{"key": "TEST-123", "fields": {"summary": "Test Issue"}}'
        assert response == mock_response

    @patch("codemie_tools.core.project_management.jira.utils.parse_payload_params")
    @patch("codemie_tools.core.project_management.jira.utils.process_search_response")
    def test_handle_get_request_search(self, mock_process_search, mock_parse_params, jira_config, mock_jira):
        """Test _handle_get_request with a search endpoint."""
        # Setup
        mock_parse_params.return_value = {"jql": "project = TEST"}

        mock_response = MagicMock()
        mock_response.text = '{"issues": [{"key": "TEST-123"}], "total": 1}'
        mock_jira.request.return_value = mock_response
        mock_jira.url = "https://jira.example.com"

        mock_process_search.return_value = "Processed search response"

        # Create tool and execute
        tool = MockGenericJiraIssueTool(jira_config)
        tool.jira = mock_jira  # Set the jira attribute directly for testing

        response_text, response = tool._handle_get_request("/rest/api/2/search", {"jql": "project = TEST"})

        # Verify
        mock_jira.request.assert_called_once()
        mock_jira.raise_for_status.assert_called_once_with(mock_response)
        mock_process_search.assert_called_once_with(mock_jira.url, mock_response, {"jql": "project = TEST"})
        assert response_text == "Processed search response"
        assert response == mock_response

    @patch("codemie_tools.core.project_management.jira.utils.parse_payload_params")
    def test_handle_non_get_request(self, mock_parse_params, jira_config, mock_jira):
        """Test _handle_non_get_request."""
        # Setup
        mock_parse_params.return_value = {"fields": {"summary": "Test Issue"}}

        mock_response = MagicMock()
        mock_response.text = '{"id": "TEST-123"}'
        mock_jira.request.return_value = mock_response

        # Create tool and execute
        tool = MockGenericJiraIssueTool(jira_config)
        tool.jira = mock_jira  # Set the jira attribute directly for testing

        response_text, response = tool._handle_non_get_request(
            "POST", "/rest/api/2/issue", {"fields": {"summary": "Test Issue"}}
        )

        # Verify
        mock_jira.request.assert_called_once_with(
            method="POST", path="/rest/api/2/issue", data={"fields": {"summary": "Test Issue"}}, advanced_mode=True
        )
        mock_jira.raise_for_status.assert_called_once_with(mock_response)
        assert response_text == '{"id": "TEST-123"}'
        assert response == mock_response

    @patch('codemie_tools.core.project_management.jira.tools.Jira')
    @patch('codemie_tools.core.project_management.jira.tools.validate_jira_creds')
    def test_healthcheck_success(self, mock_validate_creds, mock_jira_class, jira_config):
        """Test _healthcheck passes with valid JSON response containing displayName."""
        mock_jira_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"accountId": "test123", "displayName": "Test User", "active": true}'
        mock_jira_instance.request.return_value = mock_response
        mock_jira_class.return_value = mock_jira_instance

        tool = GenericJiraIssueTool(config=jira_config)
        tool._healthcheck()

        mock_jira_instance.request.assert_called_once_with(
            method="GET",
            path=JIRA_TEST_URL,
            params={},
            advanced_mode=True,
            headers={"content-type": "application/json"},
        )

    @patch('codemie_tools.core.project_management.jira.tools.Jira')
    @patch('codemie_tools.core.project_management.jira.tools.validate_jira_creds')
    def test_healthcheck_failure_unauthorized(self, mock_validate_creds, mock_jira_class, jira_config):
        """Test _healthcheck raises on 401 response."""
        mock_jira_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = '{"error": "Unauthorized"}'
        mock_jira_instance.request.return_value = mock_response
        mock_jira_class.return_value = mock_jira_instance

        tool = GenericJiraIssueTool(config=jira_config)

        with pytest.raises(AssertionError, match="Access denied"):
            tool._healthcheck()

    @patch('codemie_tools.core.project_management.jira.tools.Jira')
    @patch('codemie_tools.core.project_management.jira.tools.validate_jira_creds')
    def test_healthcheck_failure_html_response(self, mock_validate_creds, mock_jira_class, jira_config):
        """Test _healthcheck raises when 200 returns HTML (false positive case)."""
        mock_jira_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '<html><body>Login required</body></html>'
        mock_jira_instance.request.return_value = mock_response
        mock_jira_class.return_value = mock_jira_instance

        tool = GenericJiraIssueTool(config=jira_config)

        with pytest.raises(AssertionError, match="Access denied"):
            tool._healthcheck()

    @patch('codemie_tools.core.project_management.jira.tools.Jira')
    @patch('codemie_tools.core.project_management.jira.tools.validate_jira_creds')
    def test_healthcheck_failure_missing_display_name(self, mock_validate_creds, mock_jira_class, jira_config):
        """Test _healthcheck raises when JSON response lacks expected displayName field."""
        mock_jira_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"message": "Authentication failed"}'
        mock_jira_instance.request.return_value = mock_response
        mock_jira_class.return_value = mock_jira_instance

        tool = GenericJiraIssueTool(config=jira_config)

        with pytest.raises(AssertionError, match="Access denied"):
            tool._healthcheck()


# ── chat_model field ──────────────────────────────────────────────────────────


def test_tool_chat_model_defaults_none(jira_config, mock_jira):
    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        tool = GenericJiraIssueTool(config=jira_config)
    assert tool.chat_model is None


def test_tool_chat_model_accepts_value(jira_config, mock_jira):
    from langchain_core.language_models import BaseChatModel

    mock_model = MagicMock(spec=BaseChatModel)
    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        tool = GenericJiraIssueTool(config=jira_config)
        tool.chat_model = mock_model
    assert tool.chat_model is mock_model


# ── _is_issue_create_request ──────────────────────────────────────────────────


def test_is_issue_create_request_matches(jira_config, mock_jira):
    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        tool = GenericJiraIssueTool(config=jira_config)

    assert tool._is_issue_create_request("/rest/api/2/issue") is True
    assert tool._is_issue_create_request("/rest/api/3/issue") is True
    assert tool._is_issue_create_request("/rest/api/2/issue/PROJ-123") is False
    assert tool._is_issue_create_request("/rest/api/2/search") is False


# ── _extract_created_issue_key ────────────────────────────────────────────────


def test_extract_created_issue_key_success(jira_config, mock_jira):
    import json

    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        tool = GenericJiraIssueTool(config=jira_config)

    response_text = json.dumps({"id": "10001", "key": "BUG-456", "self": "https://jira.example.com/..."})
    assert tool._extract_created_issue_key(response_text) == "BUG-456"


def test_extract_created_issue_key_invalid_json(jira_config, mock_jira):
    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        tool = GenericJiraIssueTool(config=jira_config)

    assert tool._extract_created_issue_key("not-json") is None


# ── execute() attachment dispatch ─────────────────────────────────────────────


def test_execute_post_issue_with_source_key_triggers_copy(jira_config, mock_jira):
    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        tool = GenericJiraIssueTool(config=jira_config)
        tool.jira = mock_jira

    response = MagicMock()
    response.status_code = 201
    response.reason = "Created"
    response.text = '{"id": "10001", "key": "BUG-456"}'
    mock_jira.request.return_value = response

    # Patch on the class (not instance) because BaseTool is a frozen Pydantic model
    with patch.object(
        GenericJiraIssueTool,
        "copy_attachments_from_issue",
        return_value=[{"filename": "f.png", "size": 100, "status": "copied", "ocr_text": None}],
    ) as mock_copy:
        result = tool.execute(
            "POST",
            "/rest/api/2/issue",
            {
                "fields": {"summary": "Bug"},
                "source_issue_key": "SUP-123",
                "copy_attachments": True,
            },
        )

    mock_copy.assert_called_once_with("SUP-123", "BUG-456", run_ocr=False)
    assert "Attachment transfer: 1/1 copied" in result
    assert "OCR'd" not in result


def test_execute_post_issue_with_ocr_flag_runs_ocr(jira_config, mock_jira):
    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        tool = GenericJiraIssueTool(config=jira_config)
        tool.jira = mock_jira

    response = MagicMock()
    response.status_code = 201
    response.reason = "Created"
    response.text = '{"id": "10001", "key": "BUG-456"}'
    mock_jira.request.return_value = response

    with patch.object(
        GenericJiraIssueTool,
        "copy_attachments_from_issue",
        return_value=[{"filename": "f.png", "size": 100, "status": "copied", "ocr_text": "text"}],
    ) as mock_copy:
        result = tool.execute(
            "POST",
            "/rest/api/2/issue",
            {
                "fields": {"summary": "Bug"},
                "source_issue_key": "SUP-123",
                "copy_attachments": True,
                "ocr_images": True,
            },
        )

    mock_copy.assert_called_once_with("SUP-123", "BUG-456", run_ocr=True)
    assert "1 image(s) OCR'd" in result


def test_execute_post_issue_without_source_key_no_copy(jira_config, mock_jira):
    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        tool = GenericJiraIssueTool(config=jira_config)
        tool.jira = mock_jira

    response = MagicMock()
    response.status_code = 201
    response.reason = "Created"
    response.text = '{"id": "10001", "key": "BUG-456"}'
    mock_jira.request.return_value = response

    with patch.object(GenericJiraIssueTool, "copy_attachments_from_issue") as mock_copy:
        tool.execute("POST", "/rest/api/2/issue", {"fields": {"summary": "Bug"}})

    mock_copy.assert_not_called()


def test_execute_post_issue_source_key_without_copy_flag_no_copy(jira_config, mock_jira):
    """source_issue_key alone must not trigger a copy — copy_attachments=True is required."""
    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        tool = GenericJiraIssueTool(config=jira_config)
        tool.jira = mock_jira

    response = MagicMock()
    response.status_code = 201
    response.reason = "Created"
    response.text = '{"id": "10001", "key": "BUG-456"}'
    mock_jira.request.return_value = response

    with patch.object(GenericJiraIssueTool, "copy_attachments_from_issue") as mock_copy:
        tool.execute(
            "POST",
            "/rest/api/2/issue",
            {"fields": {"summary": "Bug"}, "source_issue_key": "SUP-123"},
        )

    mock_copy.assert_not_called()


# ── _handle_file_attachments upload path ─────────────────────────────────────


def test_handle_file_attachments_single_file(jira_config, mock_jira):
    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        tool = GenericJiraIssueTool(config=jira_config)
        tool.jira = mock_jira

    files = {"report.pdf": (b"pdf-bytes", "application/pdf")}
    result = tool._handle_file_attachments("/rest/api/2/issue/PROJ-123/attachments", None, files)

    mock_jira.add_attachment_object.assert_called_once()
    assert "report.pdf" in result
    assert "PROJ-123" in result


def test_handle_file_attachments_multiple_files(jira_config, mock_jira):
    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        tool = GenericJiraIssueTool(config=jira_config)
        tool.jira = mock_jira

    files = {
        "a.png": (b"bytes-a", "image/png"),
        "b.txt": (b"bytes-b", "text/plain"),
    }
    result = tool._handle_file_attachments("/rest/api/2/issue/PROJ-456/attachments", None, files)

    assert mock_jira.add_attachment_object.call_count == 2
    assert "a.png" in result
    assert "b.txt" in result


def test_handle_file_attachments_upload_raises(jira_config, mock_jira):
    from langchain_core.tools import ToolException

    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        tool = GenericJiraIssueTool(config=jira_config)
        tool.jira = mock_jira

    mock_jira.add_attachment_object.side_effect = Exception("403 Forbidden")
    files = {"screen.png": (b"bytes", "image/png")}

    with pytest.raises(ToolException, match="Failed to attach files"):
        tool._handle_file_attachments("/rest/api/2/issue/PROJ-789/attachments", None, files)
