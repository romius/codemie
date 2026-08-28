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

import io
import logging
from urllib.parse import urlparse

from codemie.configs import config
from codemie_tools.utils.image_processor import ImageProcessor

logger = logging.getLogger(__name__)

IMAGE_MIME_TYPES: set[str] = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
_JIRA_MARKUP_ESCAPE = str.maketrans({"*": r"\*", "{": r"\{", "[": r"\[", "|": r"\|", "~": r"\~"})


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

    def _copy_single_attachment(self, attachment_meta: dict, target_key: str) -> tuple[bytes | None, str]:
        """Download one attachment from Jira and upload it to target_key.

        Returns (content_bytes, status) where status is 'copied', 'failed', or 'skipped'.
        """
        filename = attachment_meta["filename"]
        size = attachment_meta["size"]
        content_url = attachment_meta["content_url"]

        max_bytes = config.JIRA_COPY_MAX_ATTACHMENT_BYTES
        if max_bytes != -1 and size > max_bytes:
            logger.warning(
                "Skipping %s: %d bytes exceeds JIRA_COPY_MAX_ATTACHMENT_BYTES=%d",
                filename,
                size,
                max_bytes,
            )
            return None, "skipped"

        # Validate URL origin to prevent SSRF via crafted attachment metadata.
        # startswith() is unsafe: "https://jira.example.com".startswith(same) also matches
        # https://jira.example.com.attacker.com/... Compare parsed (scheme, netloc) instead.
        try:
            content_origin = urlparse(content_url)
            jira_origin = urlparse(self.jira.url)
        except ValueError:
            logger.warning("Skipping %s: content URL is not parseable", filename)
            return None, "failed"
        if (content_origin.scheme, content_origin.netloc) != (jira_origin.scheme, jira_origin.netloc):
            logger.warning("Skipping %s: content URL does not match Jira base URL", filename)
            return None, "failed"

        try:
            response = self.jira.request(
                method="GET",
                path=content_url,
                advanced_mode=True,
                absolute=True,
            )
            if response.status_code != 200:
                logger.warning("Failed to download %s: HTTP %d", filename, response.status_code)
                return None, "failed"
            content_bytes = response.content
            if max_bytes != -1 and len(content_bytes) > max_bytes:
                logger.warning(
                    "Skipping %s: actual %d bytes exceeds JIRA_COPY_MAX_ATTACHMENT_BYTES=%d",
                    filename,
                    len(content_bytes),
                    max_bytes,
                )
                return None, "skipped"
        except Exception as e:
            logger.warning("Failed to download %s: %s", filename, e)
            return None, "failed"

        try:
            buf = io.BytesIO(content_bytes)
            buf.name = filename
            self.jira.add_attachment_object(target_key, buf)
            return content_bytes, "copied"
        except Exception as e:
            logger.warning("Failed to upload %s to %s: %s", filename, target_key, e)
            return None, "failed"

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

    def _append_ocr_comment(self, issue_key: str, ocr_results: list[dict]) -> None:
        """Post a single comment to issue_key with all OCR extracts labeled by filename."""
        if not ocr_results:
            return
        lines = ["## Extracted image content\n"]
        for item in ocr_results:
            safe_name = item["filename"].translate(_JIRA_MARKUP_ESCAPE)
            # Jira's {noformat} block is not recursive: the first literal {noformat} inside
            # closes the block early and everything after would render as live wiki markup
            # (including [~user] mentions). Escape the OCR text so no markup or macro fires
            # regardless of what the image contained.
            safe_ocr = item["ocr_text"].translate(_JIRA_MARKUP_ESCAPE)
            lines.append(f"**{safe_name}**\n{{noformat}}\n{safe_ocr}\n{{noformat}}\n")
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

    def copy_attachments_from_issue(self, source_key: str, target_key: str, run_ocr: bool = False) -> list[dict]:
        """Copy all attachments from source_key to target_key.

        OCR of image attachments and posting of the extracted-content comment are opt-in
        via `run_ocr=True`; both are skipped by default so callers explicitly consent to
        vision-model spend and target-ticket mutation.

        Returns list of result dicts:
            {"filename": str, "size": int, "status": "copied"|"failed"|"skipped", "ocr_text": str|None}
        """
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
                    max_count,
                    len(attachments) - i,
                )
                break

            content_bytes, status = self._copy_single_attachment(att, target_key)
            logger.info("Attachment %s (%d bytes) to %s: %s", att["filename"], att["size"], target_key, status)
            result: dict = {
                "filename": att["filename"],
                "size": att["size"],
                "status": status,
                "ocr_text": None,
            }

            if run_ocr and status == "copied" and content_bytes is not None:
                ocr_text = self._run_ocr(att["filename"], content_bytes, att["mime_type"])
                if ocr_text:
                    result["ocr_text"] = ocr_text
                    ocr_results.append({"filename": att["filename"], "ocr_text": ocr_text})

            results.append(result)

        if run_ocr:
            self._append_ocr_comment(target_key, ocr_results)
        return results
