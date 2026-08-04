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

import logging
from typing import BinaryIO, Any

import markitdown
from markitdown import StreamInfo, DocumentConverterResult

from codemie_tools.base.file_object import MimeType, normalise_mime
from codemie_tools.file_analysis.xlsx.processor import XlsxProcessor

logger = logging.getLogger(__name__)


class XlsxConverter(markitdown.converters.XlsxConverter):
    """
    Converts XLSX files to Markdown, with each sheet presented as a separate Markdown table.
    Customized to filter out empty rows and columns (NaN values) to reduce markdown size and improve readability.
    Can filter sheets based on visibility.
    """

    def __init__(self, sheet_names: list[str] = None, visible_only: bool = True) -> None:
        super().__init__()
        self.processor = XlsxProcessor(sheet_names=sheet_names, visible_only=visible_only)
        logger.info("Enable custom XLSX converter")

    def accepts(self, file_stream: BinaryIO, stream_info: StreamInfo, **kwargs: Any) -> bool:
        if not stream_info:
            return False
        # Preserve the parent's matching (accepts .xlsx by extension AND by MIME type),
        # then additionally accept XLSB by extension or by (normalised) MIME type.
        if super().accepts(file_stream, stream_info, **kwargs):
            return True
        extension = (stream_info.extension or "").lower()
        if extension == ".xlsb":
            return True
        return normalise_mime(stream_info.mimetype or "") == MimeType.XLSB_TYPE

    def convert(
        self,
        file_stream: BinaryIO,
        stream_info: StreamInfo,
        **kwargs: Any,  # Options to pass to the converter
    ) -> DocumentConverterResult:
        """
        Converts Excel files to markdown tables, filtering out empty rows and columns to reduce output size.
        If visible_only is True, only visible sheets will be processed.
        """
        file_ext = self._resolve_file_ext(stream_info)

        # Use the processor to load and clean Excel data
        sheets_clean = self.processor.load(file_stream, clean_data=True, file_ext=file_ext)

        # Use the processor to convert the sheets to markdown
        md_content = self.processor.convert(sheets_clean, **kwargs)

        return DocumentConverterResult(markdown=md_content)

    @staticmethod
    def _resolve_file_ext(stream_info: StreamInfo) -> str:
        """Resolve the read engine extension from the stream.

        Prefer the explicit file extension; when it is absent (a MIME-only stream),
        map the MIME type instead so an XLSB MIME is routed to the calamine engine
        rather than silently defaulting to the openpyxl (.xlsx) path.
        """
        extension = (stream_info.extension or "").lower()
        if extension:
            return extension
        if normalise_mime(stream_info.mimetype or "") == MimeType.XLSB_TYPE:
            return ".xlsb"
        return ".xlsx"
