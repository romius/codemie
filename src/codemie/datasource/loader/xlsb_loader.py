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

"""LangChain loader for XLSB (Excel Binary Workbook) files via python-calamine."""

from typing import List, Dict, Any

from langchain_core.documents import Document
from langchain_markitdown.base_loader import BaseMarkitdownLoader

from codemie.datasource.exceptions import UnreadableWorkbookError
from codemie_tools.file_analysis.xlsx.processor import XlsxProcessor


class XlsbLoader(BaseMarkitdownLoader):
    """Loader for XLSB files using python-calamine instead of openpyxl/MarkItDown."""

    def __init__(self, file_path: str, split_by_page: bool = False):
        super().__init__(file_path)
        self.split_by_page = split_by_page

    def load(self) -> List[Document]:
        metadata: Dict[str, Any] = {
            "source": self.file_path,
            "file_name": self._get_file_name(self.file_path),
            "file_size": self._get_file_size(self.file_path),
            "conversion_success": True,
        }
        try:
            with open(self.file_path, "rb") as fh:
                file_bytes = fh.read()
            processor = XlsxProcessor(visible_only=False)
            sheets = processor.load(file_bytes, file_ext=".xlsb")
        except Exception as e:
            raise UnreadableWorkbookError(self._get_file_name(self.file_path), str(e)) from e

        if self.split_by_page:
            documents = []
            for sheet_name, df in sheets.items():
                table_content = processor.convert({sheet_name: df})
                page_metadata = metadata.copy()
                page_metadata["page_number"] = sheet_name
                documents.append(Document(page_content=table_content, metadata=page_metadata))
            return documents

        return [Document(page_content=processor.convert(sheets), metadata=metadata)]
