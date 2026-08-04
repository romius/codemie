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

import pandas as pd
import pytest

from codemie.datasource.exceptions import UnreadableWorkbookError
from codemie.datasource.loader.xlsb_loader import XlsbLoader
from codemie.datasource.loader.file_extraction_utils import LOADERS, is_binary_extractable
from codemie.rest_api.models.index import IndexKnowledgeBaseFileTypes
from codemie_tools.base.file_object import MimeType


XLSB_MD = "## Sheet1\n| A | B |\n| 1 | 2 |"


class TestMimeTypeXlsb:
    def test_xlsb_type_constant_is_correct_mime(self):
        assert MimeType.XLSB_TYPE == "application/vnd.ms-excel.sheet.binary.macroenabled.12"

    def test_is_excel_returns_true_for_xlsb(self):
        assert MimeType(MimeType.XLSB_TYPE).is_excel is True

    def test_is_excel_returns_true_for_xlsx(self):
        assert MimeType(MimeType.XLSX_TYPE).is_excel is True

    def test_is_excel_returns_false_for_pdf(self):
        assert MimeType(MimeType.PDF_TYPE).is_excel is False


class TestIndexKnowledgeBaseFileTypesXlsb:
    def test_xlsb_member_exists(self):
        assert IndexKnowledgeBaseFileTypes.XLSB.value == "xlsb"

    def test_xlsb_in_values(self):
        assert "xlsb" in IndexKnowledgeBaseFileTypes.values()


class TestXlsbInLoaders:
    def test_xlsb_registered_in_loaders(self):
        assert IndexKnowledgeBaseFileTypes.XLSB.value in LOADERS

    def test_xlsb_loader_class_is_xlsb_loader(self):
        assert LOADERS[IndexKnowledgeBaseFileTypes.XLSB.value] is XlsbLoader

    def test_is_binary_extractable_xlsb(self):
        assert is_binary_extractable("report.xlsb") is True


class TestXlsbLoader:
    """XlsbLoader delegates parsing to XlsxProcessor (calamine engine).

    These tests mock XlsxProcessor rather than any module-level helper, mirroring
    the pattern in test_azure_devops_work_item_loader.py.
    """

    def _make_temp_xlsb(self, tmp_path):
        p = tmp_path / "report.xlsb"
        p.write_bytes(b"fake xlsb bytes")
        return str(p)

    def test_load_returns_single_document_when_not_split(self, tmp_path):
        path = self._make_temp_xlsb(tmp_path)
        df = pd.DataFrame({"A": [1], "B": [2]})
        with patch("codemie.datasource.loader.xlsb_loader.XlsxProcessor") as mock_proc:
            instance = mock_proc.return_value
            instance.load.return_value = {"Sheet1": df}
            instance.convert.return_value = XLSB_MD
            docs = XlsbLoader(path, split_by_page=False).load()
        assert len(docs) == 1
        assert "Sheet1" in docs[0].page_content
        # Whole-workbook convert is called once with all sheets.
        instance.convert.assert_called_once_with({"Sheet1": df})

    def test_load_returns_per_sheet_documents_when_split(self, tmp_path):
        path = self._make_temp_xlsb(tmp_path)
        df_a = pd.DataFrame({"X": [1]})
        df_b = pd.DataFrame({"Y": [2]})
        with patch("codemie.datasource.loader.xlsb_loader.XlsxProcessor") as mock_proc:
            instance = mock_proc.return_value
            instance.load.return_value = {"Alpha": df_a, "Beta": df_b}
            instance.convert.side_effect = lambda sheets: f"## {next(iter(sheets))}"
            docs = XlsbLoader(path, split_by_page=True).load()
        assert len(docs) == 2
        page_numbers = {d.metadata["page_number"] for d in docs}
        assert page_numbers == {"Alpha", "Beta"}

    def test_load_passes_file_ext_and_visible_only_to_processor(self, tmp_path):
        path = self._make_temp_xlsb(tmp_path)
        df = pd.DataFrame({"A": [1]})
        with patch("codemie.datasource.loader.xlsb_loader.XlsxProcessor") as mock_proc:
            instance = mock_proc.return_value
            instance.load.return_value = {"Sheet1": df}
            instance.convert.return_value = XLSB_MD
            XlsbLoader(path).load()
        # Hidden/very-hidden sheets must be included for a full-workbook load.
        mock_proc.assert_called_once_with(visible_only=False)
        # calamine engine is selected via the .xlsb extension.
        assert instance.load.call_args.kwargs.get("file_ext") == ".xlsb"

    def test_corrupt_file_raises_unreadable_workbook_error_with_filename(self, tmp_path):
        path = self._make_temp_xlsb(tmp_path)
        with patch("codemie.datasource.loader.xlsb_loader.XlsxProcessor") as mock_proc:
            mock_proc.return_value.load.side_effect = Exception("Cannot detect file format")
            with pytest.raises(UnreadableWorkbookError) as exc_info:
                XlsbLoader(path).load()
        # Actionable, non-empty message that names the file (AC6).
        assert "report.xlsb" in str(exc_info.value)

    def test_metadata_contains_source(self, tmp_path):
        path = self._make_temp_xlsb(tmp_path)
        df = pd.DataFrame({"A": [1]})
        with patch("codemie.datasource.loader.xlsb_loader.XlsxProcessor") as mock_proc:
            instance = mock_proc.return_value
            instance.load.return_value = {"Sheet1": df}
            instance.convert.return_value = XLSB_MD
            docs = XlsbLoader(path).load()
        assert docs[0].metadata["source"] == path
