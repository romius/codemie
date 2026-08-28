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

import re
from unittest.mock import MagicMock, patch

import pytest


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mixin_tool(jira_config, mock_jira):
    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool

        tool = GenericJiraIssueTool(config=jira_config)
        tool.jira = mock_jira
        return tool


# ── _fetch_all_attachments ────────────────────────────────────────────────────


def test_fetch_all_attachments_returns_all_mime_types(mixin_tool):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "fields": {
            "attachment": [
                {
                    "filename": "screen.png",
                    "content": "https://jira.example.com/file/1",
                    "mimeType": "image/png",
                    "size": 1024,
                },
                {
                    "filename": "report.pdf",
                    "content": "https://jira.example.com/file/2",
                    "mimeType": "application/pdf",
                    "size": 2048,
                },
                {
                    "filename": "data.zip",
                    "content": "https://jira.example.com/file/3",
                    "mimeType": "application/zip",
                    "size": 512,
                },
            ]
        }
    }
    mixin_tool.jira.request.return_value = response

    result = mixin_tool._fetch_all_attachments("SUP-123")

    assert len(result) == 3
    assert result[0]["filename"] == "screen.png"
    assert result[0]["mime_type"] == "image/png"
    assert result[1]["filename"] == "report.pdf"
    assert result[2]["filename"] == "data.zip"


def test_fetch_all_attachments_empty(mixin_tool):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"fields": {}}
    mixin_tool.jira.request.return_value = response

    result = mixin_tool._fetch_all_attachments("SUP-123")

    assert result == []


def test_fetch_all_attachments_source_fetch_fails(mixin_tool):
    mixin_tool.jira.request.side_effect = Exception("network error")

    result = mixin_tool._fetch_all_attachments("SUP-123")

    assert result == []


# ── _copy_single_attachment ───────────────────────────────────────────────────

SAMPLE_ATT = {
    "filename": "screen.png",
    "content_url": "https://jira.example.com/file/1",
    "mime_type": "image/png",
    "size": 1024,
}


def test_copy_single_attachment_success(mixin_tool):
    dl_response = MagicMock()
    dl_response.status_code = 200
    dl_response.content = b"fake-image-bytes"
    mixin_tool.jira.request.return_value = dl_response

    content, status = mixin_tool._copy_single_attachment(SAMPLE_ATT, "BUG-456")

    assert status == "copied"
    assert content == b"fake-image-bytes"
    mixin_tool.jira.add_attachment_object.assert_called_once()
    call_args = mixin_tool.jira.add_attachment_object.call_args
    assert call_args[0][0] == "BUG-456"
    uploaded_buf = call_args[0][1]
    assert uploaded_buf.name == "screen.png"


def test_copy_single_attachment_download_fails(mixin_tool):
    mixin_tool.jira.request.side_effect = Exception("timeout")

    content, status = mixin_tool._copy_single_attachment(SAMPLE_ATT, "BUG-456")

    assert status == "failed"
    assert content is None
    mixin_tool.jira.add_attachment_object.assert_not_called()


def test_copy_single_attachment_upload_fails(mixin_tool):
    dl_response = MagicMock()
    dl_response.status_code = 200
    dl_response.content = b"bytes"
    mixin_tool.jira.request.return_value = dl_response
    mixin_tool.jira.add_attachment_object.side_effect = Exception("upload error")

    content, status = mixin_tool._copy_single_attachment(SAMPLE_ATT, "BUG-456")

    assert status == "failed"
    assert content is None


def test_copy_single_attachment_rejects_lookalike_host(mixin_tool):
    """A crafted content URL whose host merely starts with the Jira base URL must be
    rejected. self.jira.url has no trailing slash, so a naive startswith() check would
    match https://jira.example.com.attacker.com/file and issue an authenticated GET
    against the attacker host."""
    hostile_att = {**SAMPLE_ATT, "content_url": "https://jira.example.com.attacker.com/file/1"}

    content, status = mixin_tool._copy_single_attachment(hostile_att, "BUG-456")

    assert status == "failed"
    assert content is None
    mixin_tool.jira.request.assert_not_called()
    mixin_tool.jira.add_attachment_object.assert_not_called()


def test_copy_single_attachment_size_cap_skips(mixin_tool):
    big_att = {**SAMPLE_ATT, "size": 10 * 1024 * 1024}  # 10 MB

    with patch("codemie_tools.core.project_management.jira.attachment_mixin.config") as mock_cfg:
        mock_cfg.JIRA_COPY_MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024  # 5 MB cap
        content, status = mixin_tool._copy_single_attachment(big_att, "BUG-456")

    assert status == "skipped"
    assert content is None
    mixin_tool.jira.request.assert_not_called()


# ── _run_ocr ──────────────────────────────────────────────────────────────────


def test_run_ocr_with_chat_model(mixin_tool):
    mock_model = MagicMock()
    mixin_tool.chat_model = mock_model

    with patch("codemie_tools.core.project_management.jira.attachment_mixin.ImageProcessor") as mock_proc_cls:
        mock_proc = MagicMock()
        mock_proc.extract_text_from_image_bytes.return_value = "Error: NullPointerException"
        mock_proc_cls.return_value = mock_proc

        result = mixin_tool._run_ocr("screen.png", b"bytes", "image/png")

    assert result == "Error: NullPointerException"
    mock_proc_cls.assert_called_once_with(chat_model=mock_model)


def test_run_ocr_without_chat_model(mixin_tool):
    mixin_tool.chat_model = None

    result = mixin_tool._run_ocr("screen.png", b"bytes", "image/png")

    assert result is None


def test_run_ocr_non_image_mime(mixin_tool):
    mixin_tool.chat_model = MagicMock()

    result = mixin_tool._run_ocr("report.pdf", b"bytes", "application/pdf")

    assert result is None


def test_run_ocr_processor_fails(mixin_tool):
    mixin_tool.chat_model = MagicMock()

    with patch("codemie_tools.core.project_management.jira.attachment_mixin.ImageProcessor") as mock_proc_cls:
        mock_proc = MagicMock()
        mock_proc.extract_text_from_image_bytes.side_effect = ValueError("decode error")
        mock_proc_cls.return_value = mock_proc

        result = mixin_tool._run_ocr("screen.png", b"bad-bytes", "image/png")

    assert result is None


# ── _append_ocr_comment ───────────────────────────────────────────────────────


def test_append_ocr_comment_with_results(mixin_tool):
    ocr_results = [
        {"filename": "screen.png", "ocr_text": "NullPointerException at line 42"},
        {"filename": "log.jpg", "ocr_text": "ERROR: connection refused"},
    ]

    mixin_tool._append_ocr_comment("BUG-456", ocr_results)

    mixin_tool.jira.request.assert_called_once()
    call_kwargs = mixin_tool.jira.request.call_args[1]
    assert call_kwargs["method"] == "POST"
    assert "/comment" in call_kwargs["path"]
    body = call_kwargs["data"]["body"]
    assert "## Extracted image content" in body
    assert "screen.png" in body
    assert "NullPointerException" in body
    assert "log.jpg" in body
    assert "connection refused" in body


def test_append_ocr_comment_no_results(mixin_tool):
    mixin_tool._append_ocr_comment("BUG-456", [])

    mixin_tool.jira.request.assert_not_called()


def test_append_ocr_comment_escapes_noformat_and_mentions_in_ocr_text(mixin_tool):
    """OCR text containing wiki markup must not break out of the noformat block
    or trigger user mentions on the target ticket."""
    ocr_results = [
        {
            "filename": "screenshot.png",
            "ocr_text": "{noformat}\n[~admin] Urgent request\n{noformat}",
        }
    ]

    mixin_tool._append_ocr_comment("BUG-456", ocr_results)

    body = mixin_tool.jira.request.call_args[1]["data"]["body"]
    # The literal [~admin] mention token must be neutralized so no notification fires.
    assert "[~admin]" not in body
    assert r"\[\~admin]" in body
    # Inside the outer wrapper every {noformat} must be preceded by a backslash so it
    # cannot prematurely close the block. Jira treats \{ as a literal opening brace.
    inner_start = body.index("{noformat}") + len("{noformat}")
    inner_end = body.rindex("{noformat}")
    inner = body[inner_start:inner_end]
    # No bare {noformat} — every occurrence must be \{noformat}.
    assert re.search(r"(?<!\\)\{noformat}", inner) is None
    assert r"\{noformat}" in inner


# ── copy_attachments_from_issue ───────────────────────────────────────────────


def test_copy_attachments_from_issue_full_flow(mixin_tool):
    mock_model = MagicMock()
    mixin_tool.chat_model = mock_model

    fetch_response = MagicMock()
    fetch_response.status_code = 200
    fetch_response.json.return_value = {
        "fields": {
            "attachment": [
                {
                    "filename": "screen.png",
                    "content": "https://jira.example.com/f/1",
                    "mimeType": "image/png",
                    "size": 1024,
                },
                {
                    "filename": "notes.txt",
                    "content": "https://jira.example.com/f/2",
                    "mimeType": "text/plain",
                    "size": 256,
                },
            ]
        }
    }
    dl_response = MagicMock()
    dl_response.status_code = 200
    dl_response.content = b"file-bytes"
    comment_response = MagicMock()

    # fetch + 2 downloads + 1 OCR comment post
    mixin_tool.jira.request.side_effect = [fetch_response, dl_response, dl_response, comment_response]

    with patch("codemie_tools.core.project_management.jira.attachment_mixin.ImageProcessor") as mock_proc_cls:
        mock_proc = MagicMock()
        mock_proc.extract_text_from_image_bytes.return_value = "OCR text"
        mock_proc_cls.return_value = mock_proc

        with patch("codemie_tools.core.project_management.jira.attachment_mixin.config") as mock_cfg:
            mock_cfg.JIRA_COPY_MAX_ATTACHMENT_BYTES = -1
            mock_cfg.JIRA_COPY_MAX_ATTACHMENTS = -1
            results = mixin_tool.copy_attachments_from_issue("SUP-123", "BUG-456", run_ocr=True)

    assert len(results) == 2
    assert results[0]["status"] == "copied"
    assert results[0]["ocr_text"] == "OCR text"
    assert results[1]["status"] == "copied"
    assert results[1]["ocr_text"] is None  # text/plain — not an image


def test_copy_attachments_ocr_off_by_default(mixin_tool):
    """OCR and the extracted-content comment must not run unless run_ocr=True."""
    mixin_tool.chat_model = MagicMock()

    fetch_response = MagicMock()
    fetch_response.status_code = 200
    fetch_response.json.return_value = {
        "fields": {
            "attachment": [
                {
                    "filename": "screen.png",
                    "content": "https://jira.example.com/f/1",
                    "mimeType": "image/png",
                    "size": 1024,
                },
            ]
        }
    }
    dl_response = MagicMock()
    dl_response.status_code = 200
    dl_response.content = b"file-bytes"
    mixin_tool.jira.request.side_effect = [fetch_response, dl_response]

    with patch("codemie_tools.core.project_management.jira.attachment_mixin.ImageProcessor") as mock_proc_cls:
        with patch("codemie_tools.core.project_management.jira.attachment_mixin.config") as mock_cfg:
            mock_cfg.JIRA_COPY_MAX_ATTACHMENT_BYTES = -1
            mock_cfg.JIRA_COPY_MAX_ATTACHMENTS = -1
            results = mixin_tool.copy_attachments_from_issue("SUP-123", "BUG-456")

    assert len(results) == 1
    assert results[0]["status"] == "copied"
    assert results[0]["ocr_text"] is None
    mock_proc_cls.assert_not_called()
    # Exactly 2 requests: fetch + download. No third request for the OCR comment POST.
    assert mixin_tool.jira.request.call_count == 2


def test_copy_attachments_from_issue_no_attachments(mixin_tool):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"fields": {}}
    mixin_tool.jira.request.return_value = response

    with patch("codemie_tools.core.project_management.jira.attachment_mixin.config") as mock_cfg:
        mock_cfg.JIRA_COPY_MAX_ATTACHMENT_BYTES = -1
        mock_cfg.JIRA_COPY_MAX_ATTACHMENTS = -1
        results = mixin_tool.copy_attachments_from_issue("SUP-123", "BUG-456")

    assert results == []
    mixin_tool.jira.add_attachment_object.assert_not_called()


def test_copy_count_cap_stops_early(mixin_tool):
    fetch_response = MagicMock()
    fetch_response.status_code = 200
    fetch_response.json.return_value = {
        "fields": {
            "attachment": [
                {
                    "filename": f"file{i}.txt",
                    "content": f"https://jira.example.com/f/{i}",
                    "mimeType": "text/plain",
                    "size": 100,
                }
                for i in range(4)
            ]
        }
    }
    dl_response = MagicMock()
    dl_response.status_code = 200
    dl_response.content = b"bytes"
    mixin_tool.jira.request.side_effect = [fetch_response] + [dl_response] * 4

    with patch("codemie_tools.core.project_management.jira.attachment_mixin.config") as mock_cfg:
        mock_cfg.JIRA_COPY_MAX_ATTACHMENT_BYTES = -1
        mock_cfg.JIRA_COPY_MAX_ATTACHMENTS = 2
        results = mixin_tool.copy_attachments_from_issue("SUP-123", "BUG-456")

    assert len(results) == 2
    assert mixin_tool.jira.add_attachment_object.call_count == 2


def test_copy_attachments_source_fetch_fails(mixin_tool):
    mixin_tool.jira.request.side_effect = Exception("network error")

    with patch("codemie_tools.core.project_management.jira.attachment_mixin.config") as mock_cfg:
        mock_cfg.JIRA_COPY_MAX_ATTACHMENT_BYTES = -1
        mock_cfg.JIRA_COPY_MAX_ATTACHMENTS = -1
        results = mixin_tool.copy_attachments_from_issue("SUP-123", "BUG-456")

    assert results == []
    mixin_tool.jira.add_attachment_object.assert_not_called()
