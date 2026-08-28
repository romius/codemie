# EPMCDME-11264: Attachment Transfer and OCR — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an agent explicitly asks to carry over source-ticket content into a newly-created Jira bug, the tool can (a) copy all attachments from the source ticket to the new bug when the create-issue payload sets `copy_attachments=true` alongside a `source_issue_key`, and (b) additionally OCR image attachments into a single comment on the new bug when it also sets `ocr_images=true`. Both flags default to `false`; on their own, `source_issue_key`, image content, and the source ticket cause no side effects.

**Architecture:** New `JiraAttachmentMixin` in `codemie_tools/core/project_management/jira/attachment_mixin.py` holds all copy and OCR logic. `GenericJiraIssueTool` inherits this mixin and gains a `chat_model` field; its `execute()` method extracts `source_issue_key`, `copy_attachments`, and `ocr_images` from POST params, strips all three before sending to Jira, and — only when `source_issue_key` is set **and** `copy_attachments=true` — calls `copy_attachments_from_issue(source_key, target_key, run_ocr=ocr_images)` after a successful issue-create response. `_copy_single_attachment` validates the attachment URL's `(scheme, netloc)` against `self.jira.url` to prevent SSRF against lookalike hosts; `_append_ocr_comment` runs both filename and OCR text through `_JIRA_MARKUP_ESCAPE` so no wiki markup or `[~user]` mention embedded in image content can render on the target ticket. Config caps (`JIRA_COPY_MAX_ATTACHMENT_BYTES`, `JIRA_COPY_MAX_ATTACHMENTS`) in `config.py` default to `-1` (unlimited), matching the project convention (`NATS_MAX_RECONNECT_ATTEMPTS: int = -1`).

**Tech Stack:** Python 3.12, `atlassian-python-api ^4.0.6`, `langchain-core`, `codemie_tools.utils.image_processor.ImageProcessor` (OpenCV + LLM vision OCR), pytest 8.3.x with `unittest.mock`.

## Global Constraints

- All Jira API calls route through `self.jira` (`atlassian.Jira`). No raw `httpx` or `requests` for Jira traffic.
- `-1` means "no limit" for integer caps (project convention: `NATS_MAX_RECONNECT_ATTEMPTS: int = -1`).
- Per-attachment failures are tolerated (warn + skip). Only whole-operation failures that prevent bug creation raise `ToolException`.
- OCR uses `ImageProcessor.extract_text_from_image_bytes()` exclusively. `pytesseract` must never be called — Tesseract is not installed in the Docker image.
- Logging inside `codemie_tools/` uses `logging.getLogger(__name__)`. Structured positional args: `logger.info("Copied: %s (%d bytes)", filename, size)`.
- Commit messages: `EPMCDME-11264: <description>`.
- Tests follow the existing `conftest.py` fixture pattern: `jira_config` + `mock_jira` (MagicMock via `patch("codemie_tools.core.project_management.jira.tools.Jira")`).

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `src/codemie_tools/core/project_management/jira/attachment_mixin.py` | All cross-ticket copy and OCR logic |
| Modify | `src/codemie/configs/config.py` | Add `JIRA_COPY_MAX_ATTACHMENT_BYTES` and `JIRA_COPY_MAX_ATTACHMENTS` |
| Modify | `src/codemie_tools/core/project_management/jira/tools.py` | Inherit mixin, add `chat_model` field, extend `execute()` |
| Modify | `src/codemie_tools/core/project_management/jira/tools_vars.py` | Document the opt-in attachment-transfer flags (`copy_attachments`, `ocr_images`) alongside `source_issue_key` in the agent-facing description; explain that both flags default to `false` and must be added only when the user explicitly asks for copy / OCR |
| Create | `tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py` | Full coverage for mixin |
| Modify | `tests/codemie_tools/core/project_management/jira/test_tools.py` | Dispatch branch + upload path coverage |

---

## Task 1: Config caps

**Files:**
- Modify: `src/codemie/configs/config.py` (after line 140: `IMAGE_INDEXING_MAX_SIZE_BYTES`)

**Interfaces:**
- Produces: `config.JIRA_COPY_MAX_ATTACHMENT_BYTES: int`, `config.JIRA_COPY_MAX_ATTACHMENTS: int` — consumed by Tasks 2 and 3.

- [ ] **Step 1: Write the failing test**

Add to `tests/codemie_tools/core/project_management/jira/test_tools_additional.py`:

```python
def test_jira_copy_config_defaults():
    """JIRA copy caps default to -1 (unlimited) per project convention."""
    from codemie.configs import config
    assert config.JIRA_COPY_MAX_ATTACHMENT_BYTES == -1
    assert config.JIRA_COPY_MAX_ATTACHMENTS == -1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie_tools/core/project_management/jira/test_tools_additional.py::test_jira_copy_config_defaults -v
```
Expected: `AttributeError: JIRA_COPY_MAX_ATTACHMENT_BYTES`

- [ ] **Step 3: Add fields to config.py**

In `src/codemie/configs/config.py`, after line 140 (`IMAGE_INDEXING_MAX_SIZE_BYTES`):

```python
    IMAGE_INDEXING_MAX_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB
    JIRA_COPY_MAX_ATTACHMENT_BYTES: int = -1  # -1 = unlimited
    JIRA_COPY_MAX_ATTACHMENTS: int = -1  # -1 = unlimited
```

- [ ] **Step 4: Run test to verify it passes**

```bash
poetry run pytest tests/codemie_tools/core/project_management/jira/test_tools_additional.py::test_jira_copy_config_defaults -v
```
Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/codemie/configs/config.py tests/codemie_tools/core/project_management/jira/test_tools_additional.py
git commit -m "EPMCDME-11264: Add JIRA_COPY_MAX_ATTACHMENT_BYTES and JIRA_COPY_MAX_ATTACHMENTS config caps"
```

---

## Task 2: JiraAttachmentMixin

**Files:**
- Create: `src/codemie_tools/core/project_management/jira/attachment_mixin.py`
- Create: `tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py`

**Interfaces:**
- Consumes: `config.JIRA_COPY_MAX_ATTACHMENT_BYTES`, `config.JIRA_COPY_MAX_ATTACHMENTS` (from Task 1); `IMAGE_MIME_TYPES` constant already defined in `tools.py`.
- Produces:
  - `JiraAttachmentMixin.copy_attachments_from_issue(source_key: str, target_key: str, run_ocr: bool = False) -> list[dict]` — returns `[{"filename": str, "size": int, "status": "copied"|"failed"|"skipped", "ocr_text": str|None}]`. OCR and the extracted-content comment run only when `run_ocr=True`.
  - `JiraAttachmentMixin._fetch_all_attachments(issue_key: str) -> list[dict]`
  - `JiraAttachmentMixin._copy_single_attachment(attachment_meta: dict, target_key: str) -> tuple[bytes | None, str]`
  - `JiraAttachmentMixin._run_ocr(filename: str, content_bytes: bytes, mime_type: str) -> str | None`
  - `JiraAttachmentMixin._append_ocr_comment(issue_key: str, ocr_results: list[dict]) -> None`

### Step group A: `_fetch_all_attachments`

- [ ] **Step 1: Write failing tests for _fetch_all_attachments**

Create `tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py`:

```python
import io
import pytest
from unittest.mock import MagicMock, patch, call
from codemie_tools.core.project_management.jira.models import JiraConfig


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def jira_config():
    return JiraConfig(url="https://jira.example.com", token="abc123")


@pytest.fixture
def mock_jira():
    with patch("codemie_tools.core.project_management.jira.tools.Jira") as mock_cls:
        instance = MagicMock()
        mock_cls.return_value = instance
        yield instance


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
                {"filename": "screen.png", "content": "https://jira.example.com/file/1", "mimeType": "image/png", "size": 1024},
                {"filename": "report.pdf", "content": "https://jira.example.com/file/2", "mimeType": "application/pdf", "size": 2048},
                {"filename": "data.zip",   "content": "https://jira.example.com/file/3", "mimeType": "application/zip", "size": 512},
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py -v
```
Expected: `AttributeError: 'GenericJiraIssueTool' has no attribute '_fetch_all_attachments'`

- [ ] **Step 3: Create attachment_mixin.py with _fetch_all_attachments**

Create `src/codemie_tools/core/project_management/jira/attachment_mixin.py`:

```python
import io
import logging
from typing import Optional

logger = logging.getLogger(__name__)

IMAGE_MIME_TYPES: set[str] = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}


class JiraAttachmentMixin:
    """Cross-ticket attachment copy and OCR logic for GenericJiraIssueTool.

    Expects the host class to provide:
    - self.jira: atlassian.Jira instance
    - self.chat_model: Optional[BaseChatModel]
    """

    def _fetch_all_attachments(self, issue_key: str) -> list[dict]:
        """Fetch all attachment metadata from a Jira issue (all MIME types)."""
        try:
            response = self.jira.request(
                method="GET",
                path=f"/rest/api/2/issue/{issue_key}",
                params={"fields": "attachment"},
                advanced_mode=True,
                headers={"content-type": "application/json"},
            )
            self.jira.raise_for_status(response)
            data = response.json()
            attachments = data.get("fields", {}).get("attachment") or []
            return [
                {
                    "filename": att.get("filename", "attachment"),
                    "content_url": att.get("content"),
                    "mime_type": (att.get("mimeType") or "").lower(),
                    "size": att.get("size", 0),
                }
                for att in attachments
                if att.get("content")
            ]
        except Exception as e:
            logger.warning("Failed to fetch attachments from %s: %s", issue_key, e)
            return []
```

- [ ] **Step 4: Inherit mixin in GenericJiraIssueTool (minimal change)**

In `src/codemie_tools/core/project_management/jira/tools.py`, change the class definition and imports:

```python
from codemie_tools.core.project_management.jira.attachment_mixin import JiraAttachmentMixin

# ...

class GenericJiraIssueTool(CodeMieTool, FileToolMixin, JiraAttachmentMixin):
```

- [ ] **Step 5: Run fetch tests to verify they pass**

```bash
poetry run pytest tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py::test_fetch_all_attachments_returns_all_mime_types tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py::test_fetch_all_attachments_empty tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py::test_fetch_all_attachments_source_fetch_fails -v
```
Expected: all 3 `PASSED`

### Step group B: `_copy_single_attachment`

- [ ] **Step 6: Write failing tests for _copy_single_attachment**

Append to `tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py`:

```python
# ── _copy_single_attachment ───────────────────────────────────────────────────

SAMPLE_ATT = {"filename": "screen.png", "content_url": "https://jira.example.com/file/1", "mime_type": "image/png", "size": 1024}


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


def test_copy_single_attachment_size_cap_skips(mixin_tool):
    big_att = {**SAMPLE_ATT, "size": 10 * 1024 * 1024}  # 10 MB

    with patch("codemie_tools.core.project_management.jira.attachment_mixin.config") as mock_cfg:
        mock_cfg.JIRA_COPY_MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024  # 5 MB cap
        content, status = mixin_tool._copy_single_attachment(big_att, "BUG-456")

    assert status == "skipped"
    assert content is None
    mixin_tool.jira.request.assert_not_called()
```

- [ ] **Step 7: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py::test_copy_single_attachment_success -v
```
Expected: `AttributeError: '_copy_single_attachment'`

- [ ] **Step 8: Implement _copy_single_attachment in attachment_mixin.py**

Append to `JiraAttachmentMixin` in `attachment_mixin.py`:

```python
    def _copy_single_attachment(self, attachment_meta: dict, target_key: str) -> tuple[bytes | None, str]:
        """Download one attachment from Jira and upload it to target_key.

        Returns (content_bytes, status) where status is 'copied', 'failed', or 'skipped'.
        """
        from codemie.configs import config

        filename = attachment_meta["filename"]
        size = attachment_meta["size"]
        content_url = attachment_meta["content_url"]

        max_bytes = config.JIRA_COPY_MAX_ATTACHMENT_BYTES
        if max_bytes != -1 and size > max_bytes:
            logger.warning(
                "Skipping %s: %d bytes exceeds JIRA_COPY_MAX_ATTACHMENT_BYTES=%d",
                filename, size, max_bytes,
            )
            return None, "skipped"

        try:
            response = self.jira.request(
                method="GET",
                path=content_url,
                advanced_mode=True,
                absolute=True,
            )
            if response.status_code != 200 or not response.content:
                logger.warning("Failed to download %s: HTTP %d", filename, response.status_code)
                return None, "failed"
            content_bytes = response.content
        except Exception as e:
            logger.warning("Failed to download %s: %s", filename, e)
            return None, "failed"

        try:
            buf = io.BytesIO(content_bytes)
            buf.name = filename
            self.jira.add_attachment_object(target_key, buf)
            logger.info("Copied: %s (%d bytes) to %s", filename, len(content_bytes), target_key)
            return content_bytes, "copied"
        except Exception as e:
            logger.warning("Failed to upload %s to %s: %s", filename, target_key, e)
            return None, "failed"
```

- [ ] **Step 9: Run copy tests to verify they pass**

```bash
poetry run pytest tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py::test_copy_single_attachment_success tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py::test_copy_single_attachment_download_fails tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py::test_copy_single_attachment_upload_fails tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py::test_copy_single_attachment_size_cap_skips -v
```
Expected: all 4 `PASSED`

### Step group C: `_run_ocr`

- [ ] **Step 10: Write failing tests for _run_ocr**

Append to `test_attachment_mixin.py`:

```python
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
```

- [ ] **Step 11: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py::test_run_ocr_with_chat_model -v
```
Expected: `AttributeError: '_run_ocr'`

- [ ] **Step 12: Implement _run_ocr in attachment_mixin.py**

Add to top of `attachment_mixin.py` (imports section):

```python
from codemie_tools.utils.image_processor import ImageProcessor
```

Append to `JiraAttachmentMixin`:

```python
    def _run_ocr(self, filename: str, content_bytes: bytes, mime_type: str) -> str | None:
        """Extract text from an image attachment using LLM vision OCR.

        Returns None if chat_model is absent, MIME is not an image, or OCR fails.
        """
        if not self.chat_model:
            return None
        if mime_type not in IMAGE_MIME_TYPES:
            return None
        try:
            processor = ImageProcessor(chat_model=self.chat_model)
            return processor.extract_text_from_image_bytes(content_bytes)
        except Exception as e:
            logger.warning("OCR failed for %s: %s", filename, e)
            return None
```

- [ ] **Step 13: Run OCR tests to verify they pass**

```bash
poetry run pytest tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py::test_run_ocr_with_chat_model tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py::test_run_ocr_without_chat_model tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py::test_run_ocr_non_image_mime tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py::test_run_ocr_processor_fails -v
```
Expected: all 4 `PASSED`

### Step group D: `_append_ocr_comment`

- [ ] **Step 14: Write failing tests for _append_ocr_comment**

Append to `test_attachment_mixin.py`:

```python
# ── _append_ocr_comment ───────────────────────────────────────────────────────

def test_append_ocr_comment_with_results(mixin_tool):
    ocr_results = [
        {"filename": "screen.png", "ocr_text": "NullPointerException at line 42"},
        {"filename": "log.jpg",    "ocr_text": "ERROR: connection refused"},
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
```

- [ ] **Step 15: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py::test_append_ocr_comment_with_results -v
```
Expected: `AttributeError: '_append_ocr_comment'`

- [ ] **Step 16: Implement _append_ocr_comment in attachment_mixin.py**

Append to `JiraAttachmentMixin`:

```python
    def _append_ocr_comment(self, issue_key: str, ocr_results: list[dict]) -> None:
        """Post a single comment to issue_key with all OCR extracts labeled by filename."""
        if not ocr_results:
            return
        lines = ["## Extracted image content\n"]
        for item in ocr_results:
            lines.append(f"**{item['filename']}**\n{item['ocr_text']}\n")
        comment_body = "\n".join(lines)
        try:
            self.jira.request(
                method="POST",
                path=f"/rest/api/2/issue/{issue_key}/comment",
                data={"body": comment_body},
                advanced_mode=True,
            )
            logger.info("Posted OCR comment to %s with %d image extract(s)", issue_key, len(ocr_results))
        except Exception as e:
            logger.warning("Failed to post OCR comment to %s: %s", issue_key, e)
```

- [ ] **Step 17: Run comment tests to verify they pass**

```bash
poetry run pytest tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py::test_append_ocr_comment_with_results tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py::test_append_ocr_comment_no_results -v
```
Expected: both `PASSED`

### Step group E: `copy_attachments_from_issue` (public orchestrator)

- [ ] **Step 18: Write failing tests for copy_attachments_from_issue**

Append to `test_attachment_mixin.py`:

```python
# ── copy_attachments_from_issue ───────────────────────────────────────────────

def test_copy_attachments_from_issue_full_flow(mixin_tool):
    mock_model = MagicMock()
    mixin_tool.chat_model = mock_model

    fetch_response = MagicMock()
    fetch_response.status_code = 200
    fetch_response.json.return_value = {
        "fields": {
            "attachment": [
                {"filename": "screen.png", "content": "https://jira.example.com/f/1", "mimeType": "image/png", "size": 1024},
                {"filename": "notes.txt",  "content": "https://jira.example.com/f/2", "mimeType": "text/plain",  "size": 256},
            ]
        }
    }
    dl_response = MagicMock()
    dl_response.status_code = 200
    dl_response.content = b"file-bytes"

    # First call = fetch attachments list; subsequent calls = downloads
    mixin_tool.jira.request.side_effect = [fetch_response, dl_response, dl_response]

    with patch("codemie_tools.core.project_management.jira.attachment_mixin.ImageProcessor") as mock_proc_cls:
        mock_proc = MagicMock()
        mock_proc.extract_text_from_image_bytes.return_value = "OCR text"
        mock_proc_cls.return_value = mock_proc

        with patch("codemie_tools.core.project_management.jira.attachment_mixin.config") as mock_cfg:
            mock_cfg.JIRA_COPY_MAX_ATTACHMENT_BYTES = -1
            mock_cfg.JIRA_COPY_MAX_ATTACHMENTS = -1
            results = mixin_tool.copy_attachments_from_issue("SUP-123", "BUG-456")

    assert len(results) == 2
    assert results[0]["status"] == "copied"
    assert results[0]["ocr_text"] == "OCR text"
    assert results[1]["status"] == "copied"
    assert results[1]["ocr_text"] is None  # not an image


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
                {"filename": f"file{i}.txt", "content": f"https://jira.example.com/f/{i}", "mimeType": "text/plain", "size": 100}
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
```

- [ ] **Step 19: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py::test_copy_attachments_from_issue_full_flow -v
```
Expected: `AttributeError: 'copy_attachments_from_issue'`

- [ ] **Step 20: Implement copy_attachments_from_issue in attachment_mixin.py**

Append to `JiraAttachmentMixin`:

```python
    def copy_attachments_from_issue(self, source_key: str, target_key: str) -> list[dict]:
        """Copy all attachments from source_key to target_key, OCR images if chat_model is set.

        Returns list of result dicts:
            {"filename": str, "size": int, "status": "copied"|"failed"|"skipped", "ocr_text": str|None}
        """
        from codemie.configs import config

        attachments = self._fetch_all_attachments(source_key)
        if not attachments:
            return []

        max_count = config.JIRA_COPY_MAX_ATTACHMENTS
        results: list[dict] = []
        ocr_results: list[dict] = []

        for i, att in enumerate(attachments):
            if max_count != -1 and i >= max_count:
                logger.warning(
                    "Attachment cap reached (%d). Skipping %d remaining.",
                    max_count, len(attachments) - i,
                )
                break

            content_bytes, status = self._copy_single_attachment(att, target_key)
            result: dict = {
                "filename": att["filename"],
                "size": att["size"],
                "status": status,
                "ocr_text": None,
            }

            if status == "copied" and content_bytes is not None:
                ocr_text = self._run_ocr(att["filename"], content_bytes, att["mime_type"])
                if ocr_text:
                    result["ocr_text"] = ocr_text
                    ocr_results.append({"filename": att["filename"], "ocr_text": ocr_text})

            results.append(result)

        self._append_ocr_comment(target_key, ocr_results)
        return results
```

- [ ] **Step 21: Run all mixin tests to verify they pass**

```bash
poetry run pytest tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py -v
```
Expected: all 16 `PASSED`

- [ ] **Step 22: Commit**

```bash
git add src/codemie_tools/core/project_management/jira/attachment_mixin.py \
        src/codemie_tools/core/project_management/jira/tools.py \
        tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py
git commit -m "EPMCDME-11264: Add JiraAttachmentMixin with copy and OCR logic"
```

---

## Task 3: Wire mixin into execute() + add chat_model field

**Files:**
- Modify: `src/codemie_tools/core/project_management/jira/tools.py`
- Modify: `tests/codemie_tools/core/project_management/jira/test_tools.py`

**Interfaces:**
- Consumes: `JiraAttachmentMixin.copy_attachments_from_issue` (from Task 2).
- Produces: `GenericJiraIssueTool.chat_model: Optional[BaseChatModel]`; `execute()` extended to strip `source_issue_key` and trigger copy post-create.

### Step group A: chat_model field + _is_issue_create_request + _extract_created_issue_key

- [ ] **Step 1: Write failing tests**

Append to `tests/codemie_tools/core/project_management/jira/test_tools.py`:

```python
# ── chat_model field ─────────────────────────────────────────────────────────

def test_tool_chat_model_defaults_none(jira_config, mock_jira):
    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool
        tool = GenericJiraIssueTool(config=jira_config)
    assert tool.chat_model is None


def test_tool_chat_model_accepts_value(jira_config, mock_jira):
    from langchain_core.language_models import BaseChatModel
    mock_model = MagicMock(spec=BaseChatModel)
    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool
        tool = GenericJiraIssueTool(config=jira_config)
        tool.chat_model = mock_model
    assert tool.chat_model is mock_model


# ── _is_issue_create_request ──────────────────────────────────────────────────

def test_is_issue_create_request_matches(jira_config, mock_jira):
    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool
        tool = GenericJiraIssueTool(config=jira_config)
    assert tool._is_issue_create_request("/rest/api/2/issue") is True
    assert tool._is_issue_create_request("/rest/api/3/issue") is True
    assert tool._is_issue_create_request("/rest/api/2/issue/PROJ-123") is False
    assert tool._is_issue_create_request("/rest/api/2/search") is False


# ── _extract_created_issue_key ────────────────────────────────────────────────

def test_extract_created_issue_key_success(jira_config, mock_jira):
    import json
    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool
        tool = GenericJiraIssueTool(config=jira_config)
    response_text = json.dumps({"id": "10001", "key": "BUG-456", "self": "https://jira.example.com/..."})
    assert tool._extract_created_issue_key(response_text) == "BUG-456"


def test_extract_created_issue_key_invalid_json(jira_config, mock_jira):
    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool
        tool = GenericJiraIssueTool(config=jira_config)
    assert tool._extract_created_issue_key("not-json") is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie_tools/core/project_management/jira/test_tools.py::test_tool_chat_model_defaults_none tests/codemie_tools/core/project_management/jira/test_tools.py::test_is_issue_create_request_matches tests/codemie_tools/core/project_management/jira/test_tools.py::test_extract_created_issue_key_success -v
```
Expected: `AttributeError` for each

- [ ] **Step 3: Add chat_model field and helper methods to tools.py**

In `src/codemie_tools/core/project_management/jira/tools.py`, add import at top:

```python
from typing import Type, Optional, Any, Dict, Union
from langchain_core.language_models import BaseChatModel
```

Add `chat_model` field to `GenericJiraIssueTool` (after `jira`):

```python
class GenericJiraIssueTool(CodeMieTool, FileToolMixin, JiraAttachmentMixin):
    config: JiraConfig
    jira: Optional[Jira] = None
    chat_model: Optional[BaseChatModel] = None
    name: str = GENERIC_JIRA_TOOL.name
    # ...
```

Add helper methods to `GenericJiraIssueTool`:

```python
    def _is_issue_create_request(self, relative_url: str) -> bool:
        """Check whether the URL targets issue creation (not a sub-resource or search)."""
        return bool(re.match(r"/rest/api/\d+/issue$", relative_url))

    def _extract_created_issue_key(self, response_text: str) -> str | None:
        """Parse the new issue key from a successful issue-create response body."""
        try:
            data = json.loads(response_text)
            return data.get("key")
        except (json.JSONDecodeError, TypeError):
            return None
```

- [ ] **Step 4: Run helper tests to verify they pass**

```bash
poetry run pytest tests/codemie_tools/core/project_management/jira/test_tools.py::test_tool_chat_model_defaults_none tests/codemie_tools/core/project_management/jira/test_tools.py::test_tool_chat_model_accepts_value tests/codemie_tools/core/project_management/jira/test_tools.py::test_is_issue_create_request_matches tests/codemie_tools/core/project_management/jira/test_tools.py::test_extract_created_issue_key_success tests/codemie_tools/core/project_management/jira/test_tools.py::test_extract_created_issue_key_invalid_json -v
```
Expected: all 5 `PASSED`

### Step group B: extend execute() dispatch + upload path tests

- [ ] **Step 5: Write failing tests for execute() dispatch**

Append to `tests/codemie_tools/core/project_management/jira/test_tools.py`:

```python
# ── execute() attachment dispatch ─────────────────────────────────────────────

def test_execute_post_issue_with_source_key_triggers_copy(jira_config, mock_jira):
    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool
        tool = GenericJiraIssueTool(config=jira_config)
        tool.jira = mock_jira

    response = MagicMock()
    response.status_code = 201
    response.reason = "Created"
    response.text = '{"id": "10001", "key": "BUG-456"}'
    mock_jira.request.return_value = response

    with patch.object(tool, "copy_attachments_from_issue", return_value=[
        {"filename": "f.png", "size": 100, "status": "copied", "ocr_text": None}
    ]) as mock_copy:
        result = tool.execute("POST", "/rest/api/2/issue", {"fields": {"summary": "Bug"}, "source_issue_key": "SUP-123"})

    mock_copy.assert_called_once_with("SUP-123", "BUG-456")
    assert "Attachment transfer: 1/1 copied" in result


def test_execute_post_issue_without_source_key_no_copy(jira_config, mock_jira):
    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool
        tool = GenericJiraIssueTool(config=jira_config)
        tool.jira = mock_jira

    response = MagicMock()
    response.status_code = 201
    response.reason = "Created"
    response.text = '{"id": "10001", "key": "BUG-456"}'
    mock_jira.request.return_value = response

    with patch.object(tool, "copy_attachments_from_issue") as mock_copy:
        tool.execute("POST", "/rest/api/2/issue", {"fields": {"summary": "Bug"}})

    mock_copy.assert_not_called()


# ── _handle_file_attachments upload path ─────────────────────────────────────

def test_handle_file_attachments_single_file(jira_config, mock_jira):
    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool
        tool = GenericJiraIssueTool(config=jira_config)
        tool.jira = mock_jira

    files = {"report.pdf": (b"pdf-bytes", "application/pdf")}
    result = tool._handle_file_attachments("/rest/api/2/issue/PROJ-123/attachments", None, files)

    mock_jira.add_attachment_object.assert_called_once()
    assert "report.pdf" in result
    assert "PROJ-123" in result


def test_handle_file_attachments_multiple_files(jira_config, mock_jira):
    with patch("codemie_tools.core.project_management.jira.tools.validate_jira_creds"):
        from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool
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
        from codemie_tools.core.project_management.jira.tools import GenericJiraIssueTool
        tool = GenericJiraIssueTool(config=jira_config)
        tool.jira = mock_jira

    mock_jira.add_attachment_object.side_effect = Exception("403 Forbidden")
    files = {"screen.png": (b"bytes", "image/png")}

    with pytest.raises(ToolException, match="Failed to attach files"):
        tool._handle_file_attachments("/rest/api/2/issue/PROJ-789/attachments", None, files)
```

- [ ] **Step 6: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie_tools/core/project_management/jira/test_tools.py::test_execute_post_issue_with_source_key_triggers_copy -v
```
Expected: copy not called / `source_issue_key` lands in Jira payload

- [ ] **Step 7: Extend execute() in tools.py**

Replace the `execute()` method body in `src/codemie_tools/core/project_management/jira/tools.py`:

```python
    def execute(self, method: str, relative_url: str, params: Optional[str] = "", *args):
        if self._is_attachment_operation(relative_url):
            all_files = self._resolve_files()
            if all_files:
                payload_params = parse_payload_params(params)
                requested_files = self._filter_requested_files(all_files, payload_params)
                if requested_files:
                    return self._handle_file_attachments(relative_url, params, requested_files)

        payload_params = parse_payload_params(params)

        # Strip source_issue_key before sending to Jira — it is not a Jira API field
        source_issue_key = payload_params.pop("source_issue_key", None) if isinstance(payload_params, dict) else None

        if method == "GET":
            payload_params = self._normalize_fields_param(payload_params)
            response_text, response = self._handle_get_request(relative_url, payload_params)
        else:
            response_text, response = self._handle_non_get_request(method, relative_url, payload_params)

        response_string = f"HTTP: {method} {relative_url} -> {response.status_code} {response.reason} {response_text}"
        logger.debug(response_string)

        if method == "POST" and source_issue_key and self._is_issue_create_request(relative_url):
            new_issue_key = self._extract_created_issue_key(response_text)
            if new_issue_key:
                copy_results = self.copy_attachments_from_issue(source_issue_key, new_issue_key)
                copied = sum(1 for r in copy_results if r["status"] == "copied")
                ocr_count = sum(1 for r in copy_results if r.get("ocr_text"))
                response_string += f"\nAttachment transfer: {copied}/{len(copy_results)} copied. {ocr_count} image(s) OCR'd."

        if method == "GET" and self._is_single_issue_request(relative_url):
            image_attachments = self._extract_image_attachments(response)
            if image_attachments:
                return JiraMultimodalResponse(text=response_string, image_attachments=image_attachments)

        return response_string
```

- [ ] **Step 8: Run all new tests to verify they pass**

```bash
poetry run pytest tests/codemie_tools/core/project_management/jira/test_tools.py::test_execute_post_issue_with_source_key_triggers_copy tests/codemie_tools/core/project_management/jira/test_tools.py::test_execute_post_issue_without_source_key_no_copy tests/codemie_tools/core/project_management/jira/test_tools.py::test_handle_file_attachments_single_file tests/codemie_tools/core/project_management/jira/test_tools.py::test_handle_file_attachments_multiple_files tests/codemie_tools/core/project_management/jira/test_tools.py::test_handle_file_attachments_upload_raises -v
```
Expected: all 5 `PASSED`

- [ ] **Step 9: Run the full Jira test suite to verify no regressions**

```bash
poetry run pytest tests/codemie_tools/core/project_management/jira/ -v
```
Expected: all existing + new tests `PASSED`

- [ ] **Step 10: Commit**

```bash
git add src/codemie_tools/core/project_management/jira/tools.py \
        tests/codemie_tools/core/project_management/jira/test_tools.py
git commit -m "EPMCDME-11264: Wire JiraAttachmentMixin into execute() and add chat_model field"
```

---

## Task 4: Update agent-facing tool description

**Files:**
- Modify: `src/codemie_tools/core/project_management/jira/tools_vars.py`

**Interfaces:**
- No new interfaces — documentation update only.

- [ ] **Step 1: Update get_jira_tool_description() in tools_vars.py**

In the `Key behaviors:` block inside `get_jira_tool_description()` (both v2 and v3 paths share the same appended string), add a new bullet after the `Image attachments` bullet:

```python
    description += """
    Key behaviors:
    - Get minimum required fields for search/read operations unless user requests more
    - Query API for missing required info, ask user if not found
    - For status updates: get available statuses first, compare with user input
    - File attachments: To attach files to an issue, use POST method with '/rest/api/{version}/issue/{issueIdOrKey}/attachments'
      and include the file name in params as {"file": "filename.ext"} or {"files": ["file1.ext", "file2.ext"]} for multiple files
    - Image attachments: When fetching a single issue, always include "attachment" in the requested fields
      (e.g., params={"fields": "key,summary,status,assignee,issuetype,attachment"}).
      Image attachments (PNG, JPEG, GIF, WebP) are automatically downloaded and passed to the AI model for visual analysis.
    - Attachment transfer: Whenever you create a new issue that is derived from, based on, or references
      an existing ticket (bug from support request, task from epic, subtask from story, escalation,
      follow-up, duplicate, or any similar relationship), include "source_issue_key" in params alongside
      the issue fields:
        params={"fields": {...issue fields...}, "source_issue_key": "ORIG-123"}
      where ORIG-123 is the key of the source ticket. This triggers automatic attachment copy: all files
      attached to ORIG-123 are copied to the new issue. If image analysis is configured, a comment with
      extracted image content is posted automatically. If the source ticket has no attachments the new
      issue is created normally with no side effects.
      source_issue_key is a hidden transfer parameter — it is never added to the issue content and does
      not constitute a reference to the source ticket. Whether to mention the source ticket in the
      description or fields is a separate decision. Always include source_issue_key in params for
      attachment transfer regardless of whether the user wants the source ticket referenced in the issue.
      Omitting source_issue_key when a source ticket is known silently skips attachment transfer.
    ...
    """
```

- [ ] **Step 2: Run the full Jira test suite to confirm no regressions**

```bash
poetry run pytest tests/codemie_tools/core/project_management/jira/ -v
```
Expected: all `PASSED`

- [ ] **Step 3: Commit**

```bash
git add src/codemie_tools/core/project_management/jira/tools_vars.py
git commit -m "EPMCDME-11264: Document source_issue_key attachment-transfer behavior in Jira tool description"
```

---

## Self-Review

**Spec coverage:**
- ✅ All attachments copied (`_fetch_all_attachments` no MIME filter, `_copy_single_attachment` for each)
- ✅ No fidelity loss (default `-1` caps)
- ✅ OCR → comment (`_run_ocr` + `_append_ocr_comment`)
- ✅ Backward compatible (`source_issue_key` absent → no copy)
- ✅ Configurable caps (Task 1)
- ✅ Structured logging throughout
- ✅ `chat_model=None` → copy still runs, OCR skipped
- ✅ Per-attachment failure tolerance in `copy_attachments_from_issue`
- ✅ Tool description updated (agent can find the feature)

**Placeholder scan:** None found.

**Type consistency:**
- `copy_attachments_from_issue` returns `list[dict]` with keys `filename`, `size`, `status`, `ocr_text` — consistent across Task 2 implementation and Task 3 consumer.
- `_copy_single_attachment` returns `tuple[bytes | None, str]` — consistent with Task 2 tests and Task 2 implementation.
- `source_issue_key` string key used consistently in execute() dispatch and tests.
