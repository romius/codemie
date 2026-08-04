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
from typing import Dict, Optional, List, BinaryIO, Any

import openpyxl
import pandas as pd
from markitdown.converters import HtmlConverter

from codemie.configs import config
from codemie.datasource.loader.file_processor_pool import file_process_pool, maybe_pool_submit
from codemie_tools.file_analysis.workers.xlsx_workers import load_xlsx

logger = logging.getLogger(__name__)


class XlsxProcessor:
    """
    Processes XLSX files by loading them into pandas DataFrames and converting to various formats.
    Provides functionality to filter out empty rows and columns, handle sheet visibility,
    filter rows based on column values, and convert to markdown format.
    """

    def __init__(
        self,
        sheet_names: Optional[List[str]] = None,
        visible_only: bool = True,
        filter_values: Optional[List[str]] = None,
        filter_mode: str = "exact",
    ):
        """
        Initialize the XLSX processor.

        Args:
            sheet_names: Optional list of specific sheet names to process
            visible_only: If True, only visible sheets will be processed
            filter_values: List of values to search for. Row must contain ALL values (AND logic).
            filter_mode: Filter matching mode ('exact' - exact match, 'contains' - substring match)
        """
        self.sheet_names = sheet_names if sheet_names else None
        self.visible_only = visible_only
        self.filter_values = filter_values
        self.filter_mode = filter_mode
        self._html_converter = HtmlConverter()
        logger.info("Initialized XlsxProcessor")

    def load(
        self, file_content: bytes | BinaryIO, clean_data: bool = True, file_ext: str = ".xlsx"
    ) -> Dict[str, pd.DataFrame]:
        """Load an Excel file and return a dictionary of DataFrames for each sheet

        Args:
            file_content: The Excel file content as bytes or file-like object
            clean_data: If True, clean the data by removing empty rows and columns
            file_ext: File extension ('.xlsx' or '.xlsb') controls the read engine

        Returns:
            Dictionary of DataFrames for each sheet
        """
        try:
            # Convert to bytes if needed
            if isinstance(file_content, bytes):
                file_bytes = file_content
            else:
                file_bytes = file_content.read()
                file_content.seek(0)

            return maybe_pool_submit(
                load_xlsx,
                file_bytes,
                self.sheet_names,
                self.visible_only,
                clean_data,
                self.filter_values,
                self.filter_mode,
                file_ext,
            )
        except Exception as e:
            logger.error(f"Failed to load Excel file: {str(e)}")
            raise e

    def convert(self, sheets: Dict[str, pd.DataFrame], **kwargs: Any) -> str:
        """
        Convert Excel sheets to markdown format.

        Args:
            sheets: Dictionary of sheet names to DataFrames
            **kwargs: Additional options to pass to the HTML converter

        Returns:
            Markdown string representation of the Excel sheets
        """
        logger.debug(f"Converting {len(sheets)} sheets to markdown: {sheets.keys()}")

        md_content = ""
        for sheet_name, df in sheets.items():
            md_content += f"## {sheet_name}\n"
            html_content = df.to_html(index=False)
            md_content += self._html_converter.convert_string(html_content, **kwargs).markdown.strip() + "\n\n"

        return md_content.strip()
