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

"""Shared attachment content processing mixin for Azure DevOps tools."""

import base64
import mimetypes
import os
from typing import Any, Dict

from codemie_tools.base.codemie_tool import logger
from codemie_tools.base.file_object import MimeType
from codemie_tools.file_analysis.pdf.processor import PdfProcessor
from codemie_tools.file_analysis.pptx.processor import PptxProcessor
from codemie_tools.file_analysis.docx.processor import DocxProcessor
from codemie_tools.file_analysis.docx.models import QueryType as DocxQueryType
from codemie_tools.file_analysis.xlsx.processor import XlsxProcessor
from codemie_tools.utils.image_processor import ImageProcessor


class AttachmentContentMixin:
    """Mixin providing file content parsing for Azure DevOps attachment tools.

    Handles text, PDF, image, DOCX, PPTX, and XLSX file types.
    Set ``chat_model`` to enable AI-based image description and PDF OCR.
    """

    chat_model: Any = None

    _MAX_BASE64_BYTES: int = 50_000
    _TEXT_PREFIX: str = "text/"
    # Excel extensions used as a fallback when the OS MIME table cannot resolve the
    # type (e.g. a slim Linux container without /etc/mime.types has no .xlsb entry).
    _EXCEL_EXTENSIONS: frozenset = frozenset({".xlsx", ".xls", ".xlsb"})
    _TEXT_EXTENSIONS: frozenset = frozenset(
        {
            ".txt",
            ".md",
            ".markdown",
            ".json",
            ".xml",
            ".csv",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".cfg",
            ".conf",
            ".log",
            ".html",
            ".htm",
            ".rst",
            ".properties",
            ".env",
        }
    )

    def _build_base64_response(self, content_bytes: bytes, note: str) -> Dict[str, Any]:
        """Return base64 content if the file is small enough, otherwise metadata-only.

        Large binary blobs encoded as base64 exceed the tool output token limit
        and get truncated, producing a useless partial string that the LLM
        cannot decode.  For files larger than ``_MAX_BASE64_BYTES`` we return
        a ``metadata_only`` content type with an actionable note instead.
        """
        if len(content_bytes) <= self._MAX_BASE64_BYTES:
            return {
                "content_type": "base64",
                "content": base64.b64encode(content_bytes).decode("utf-8"),
                "note": note,
            }

        size_kb = len(content_bytes) / 1024
        return {
            "content_type": "metadata_only",
            "content": None,
            "note": (
                f"{note} "
                f"The file is too large ({size_kb:.1f} KB) to return as base64 without being "
                f"truncated. Only metadata is provided."
            ),
        }

    def _detect_mime_type(self, filename: str) -> str:
        """Detect MIME type from filename using the stdlib mimetypes module."""
        mime_type, _ = mimetypes.guess_type(filename)
        return mime_type or "application/octet-stream"

    def _is_text_based(self, mime_type: str, filename: str) -> bool:
        """Return True if the file should be decoded as plain text."""
        if mime_type.startswith(self._TEXT_PREFIX):
            return True
        ext = os.path.splitext(filename)[1].lower()
        return ext in self._TEXT_EXTENSIONS

    def _pdf_ocr_via_page_rendering(self, content_bytes: bytes) -> str:
        """Render each PDF page as an image and run OCR via the vision model.

        Handles image-based PDFs where pdfplumber finds no selectable text.
        """
        import io
        import pdfplumber

        image_proc = ImageProcessor(chat_model=self.chat_model)
        results = []

        with pdfplumber.open(io.BytesIO(content_bytes)) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                try:
                    page_image = page.to_image(resolution=150)
                    img_bytes_io = io.BytesIO()
                    page_image.original.save(img_bytes_io, format="PNG")
                    image_bytes = img_bytes_io.getvalue()

                    page_text = image_proc.extract_text_from_image_bytes(image_bytes)
                    if page_text.strip():
                        results.append(f"--- Page {page_num} ---\n{page_text}")
                except Exception as e:
                    logger.warning(f"Failed to OCR page {page_num}: {e}")

        return "\n\n".join(results)

    @staticmethod
    def _extract_pdf_metadata(content_bytes: bytes) -> str:
        """Extract structural metadata from a PDF when text extraction yields nothing."""
        import io
        import pdfplumber

        parts: list[str] = []
        try:
            with pdfplumber.open(io.BytesIO(content_bytes)) as pdf:
                total_pages = len(pdf.pages)
                parts.append(f"Total pages: {total_pages}")

                if pdf.metadata:
                    meta_lines = []
                    for key, value in pdf.metadata.items():
                        if value:
                            meta_lines.append(f"  {key}: {value}")
                    if meta_lines:
                        parts.append("Document metadata:")
                        parts.extend(meta_lines)

                page_summaries: list[str] = []
                for idx, page in enumerate(pdf.pages, start=1):
                    width = round(page.width, 1)
                    height = round(page.height, 1)
                    img_count = len(page.images) if page.images else 0
                    page_summaries.append(f"  Page {idx}: {width}x{height} pt, {img_count} embedded image(s)")
                if page_summaries:
                    parts.append("Page details:")
                    parts.extend(page_summaries)
        except Exception as e:
            logger.warning(f"Failed to extract PDF metadata: {e}")
            parts.append(f"(metadata extraction failed: {e})")

        return "\n".join(parts)

    def _process_pdf_content(self, filename: str, content_bytes: bytes) -> Dict[str, Any]:
        """Extract text from a PDF, falling back to OCR then structural metadata."""
        try:
            text = PdfProcessor.extract_text_as_markdown(content_bytes)
            if not text.strip() and self.chat_model:
                logger.info(f"No selectable text in '{filename}', falling back to per-page OCR")
                text = self._pdf_ocr_via_page_rendering(content_bytes)
            if text.strip():
                return {"content_type": "text", "content": text, "note": None}

            metadata_text = self._extract_pdf_metadata(content_bytes)
            no_ocr_note = (
                "PDF appears to contain only images with no selectable text. "
                "A chat model with vision capabilities is required for OCR. "
                "Below is the structural metadata that could be extracted."
            )
            return {
                "content_type": "text",
                "content": f"{no_ocr_note}\n\n{metadata_text}",
                "note": no_ocr_note,
            }
        except Exception as e:
            logger.warning(f"PDF text extraction failed for '{filename}': {e}")
            try:
                metadata_text = self._extract_pdf_metadata(content_bytes)
                return {
                    "content_type": "text",
                    "content": (f"PDF text extraction failed: {e}. Structural metadata:\n\n{metadata_text}"),
                    "note": f"PDF text extraction failed: {e}.",
                }
            except Exception:
                return self._build_base64_response(
                    content_bytes,
                    f"PDF text extraction failed: {e}.",
                )

    def _process_content(self, filename: str, content_bytes: bytes) -> Dict[str, Any]:
        """Parse attachment bytes according to their file type.

        Returns a dict with keys: content_type, content, note.
        """
        mime_type = self._detect_mime_type(filename)
        mime = MimeType(mime_type)

        if self._is_text_based(mime_type, filename):
            try:
                text = content_bytes.decode("utf-8", errors="replace")
                return {"content_type": "text", "content": text, "note": None}
            except Exception as e:
                logger.warning(f"Failed to decode text file '{filename}': {e}")
                return self._build_base64_response(
                    content_bytes,
                    f"Text decoding failed: {e}. Returned as base64.",
                )

        if mime.is_pdf:
            return self._process_pdf_content(filename, content_bytes)

        if mime.is_image:
            if self.chat_model:
                try:
                    processor = ImageProcessor(chat_model=self.chat_model)
                    description = processor.extract_text_from_image_bytes(content_bytes)
                    return {
                        "content_type": "image_description",
                        "content": description or "(No text detected in image)",
                        "note": None,
                    }
                except Exception as e:
                    logger.warning(f"Image description failed for '{filename}': {e}")

            return self._build_base64_response(
                content_bytes,
                "Image content cannot be described without a chat model. "
                "Provide a chat model via the tool's chat_model field to enable AI-based image description.",
            )

        if mime.is_docx:
            try:
                processor = DocxProcessor(ocr_enabled=False, chat_model=self.chat_model)
                doc_content = processor.read_document_from_bytes(
                    content=content_bytes,
                    file_name=filename,
                    query=DocxQueryType.TEXT,
                )
                return {"content_type": "text", "content": doc_content.text, "note": None}
            except Exception as e:
                logger.warning(f"DOCX text extraction failed for '{filename}': {e}")
                return self._build_base64_response(
                    content_bytes,
                    f"DOCX extraction failed: {e}.",
                )

        if mime.is_pptx:
            try:
                processor = PptxProcessor(chat_model=self.chat_model)
                pptx_document = PptxProcessor.open_pptx_document(content_bytes)
                text = processor.extract_text_as_markdown(pptx_document)
                return {"content_type": "text", "content": text, "note": None}
            except Exception as e:
                logger.warning(f"PPTX text extraction failed for '{filename}': {e}")
                return self._build_base64_response(
                    content_bytes,
                    f"PPTX extraction failed: {e}.",
                )

        if mime.is_excel or os.path.splitext(filename)[1].lower() in self._EXCEL_EXTENSIONS:
            try:
                file_ext = os.path.splitext(filename)[1].lower() or ".xlsx"
                processor = XlsxProcessor()
                sheets = processor.load(content_bytes, file_ext=file_ext)
                text = processor.convert(sheets)
                return {"content_type": "text", "content": text, "note": None}
            except Exception as e:
                logger.warning(f"Excel text extraction failed for '{filename}': {e}")
                return self._build_base64_response(
                    content_bytes,
                    f"Excel extraction failed: {e}.",
                )

        return self._build_base64_response(
            content_bytes,
            f"File type '{mime_type}' cannot be parsed to text.",
        )
