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

from unittest.mock import MagicMock, patch

import pandas as pd

from codemie_tools.file_analysis.workers.xlsx_workers import (
    _get_visible_sheets_xlsb,
    _load_xlsb_sheets,
    load_xlsx,
    process_xlsx_to_markdown,
)


XLSB_BYTES = b"fake xlsb content"


class TestGetVisibleSheetsXlsb:
    def test_returns_visible_sheet_names(self):
        from python_calamine import SheetVisibleEnum

        # MagicMock(name=...) sets the mock's repr name, NOT the .name attribute.
        # Assign .name explicitly after construction.
        m1 = MagicMock()
        m1.name = "Sheet1"
        m1.visible = SheetVisibleEnum.Visible
        m2 = MagicMock()
        m2.name = "Hidden"
        m2.visible = SheetVisibleEnum.Hidden
        mock_meta = [m1, m2]
        mock_wb = MagicMock()
        mock_wb.sheets_metadata = mock_meta

        with patch("python_calamine.CalamineWorkbook.from_filelike", return_value=mock_wb):
            result = _get_visible_sheets_xlsb(XLSB_BYTES)

        assert result == ["Sheet1"]

    def test_returns_none_on_exception(self):
        with patch("python_calamine.CalamineWorkbook.from_filelike", side_effect=Exception("err")):
            result = _get_visible_sheets_xlsb(XLSB_BYTES)
        assert result is None


class TestLoadXlsbSheets:
    def test_loads_all_sheets_when_none(self):
        expected = {"Sheet1": pd.DataFrame({"A": [1]})}
        with patch("pandas.read_excel", return_value=expected) as mock_read:
            _load_xlsb_sheets(XLSB_BYTES, None)
        mock_read.assert_called_once()
        call_kwargs = mock_read.call_args.kwargs
        assert call_kwargs["engine"] == "calamine"
        assert call_kwargs["sheet_name"] is None

    def test_loads_specific_sheets(self):
        expected = {"Sheet1": pd.DataFrame()}
        with patch("pandas.read_excel", return_value=expected) as mock_read:
            _load_xlsb_sheets(XLSB_BYTES, ["Sheet1"])
        assert mock_read.call_args.kwargs["sheet_name"] == ["Sheet1"]


class TestLoadXlsxWithFileExt:
    def test_xlsb_dispatches_to_calamine(self):
        df = pd.DataFrame({"A": ["val"]})
        with patch(
            "codemie_tools.file_analysis.workers.xlsx_workers._load_xlsb_sheets",
            return_value={"Sheet1": df},
        ) as mock_xlsb:
            result = load_xlsx(XLSB_BYTES, None, False, False, None, "exact", file_ext=".xlsb")
        mock_xlsb.assert_called_once()
        assert "Sheet1" in result

    def test_xlsx_does_not_dispatch_to_calamine(self):
        df = pd.DataFrame({"A": ["val"]})
        with patch("pandas.read_excel", return_value={"Sheet1": df}):
            with patch("codemie_tools.file_analysis.workers.xlsx_workers._load_xlsb_sheets") as mock_xlsb:
                load_xlsx(b"fake", None, False, False, None, "exact", file_ext=".xlsx")
        mock_xlsb.assert_not_called()

    def test_xlsb_visible_only_filters_sheets(self):
        df = pd.DataFrame({"A": ["val"]})
        with patch(
            "codemie_tools.file_analysis.workers.xlsx_workers._get_visible_sheets_xlsb",
            return_value=["Sheet1"],
        ):
            with patch(
                "codemie_tools.file_analysis.workers.xlsx_workers._load_xlsb_sheets",
                return_value={"Sheet1": df},
            ) as mock_load:
                load_xlsx(XLSB_BYTES, None, True, False, None, "exact", file_ext=".xlsb")
        assert mock_load.call_args[0][1] == ["Sheet1"]


class TestProcessXlsxToMarkdownWithFileExt:
    def test_xlsb_produces_markdown(self):
        df = pd.DataFrame({"A": ["hello"]})
        with patch(
            "codemie_tools.file_analysis.workers.xlsx_workers.load_xlsx",
            return_value={"Sheet1": df},
        ):
            result = process_xlsx_to_markdown(XLSB_BYTES, None, False, file_ext=".xlsb")
        assert "hello" in result
        assert "Sheet1" in result

    def test_formula_cached_values_returned(self):
        df = pd.DataFrame({"Formula": [42.0], "Text": ["result"]})
        with patch(
            "codemie_tools.file_analysis.workers.xlsx_workers.load_xlsx",
            return_value={"Formulas": df},
        ):
            result = process_xlsx_to_markdown(XLSB_BYTES, None, False, file_ext=".xlsb")
        assert "42" in result
        assert "result" in result

    def test_macro_file_data_ingests_normally(self):
        df = pd.DataFrame({"Data": ["macro-file-cell-value"]})
        with patch(
            "codemie_tools.file_analysis.workers.xlsx_workers.load_xlsx",
            return_value={"Sheet1": df},
        ):
            result = process_xlsx_to_markdown(XLSB_BYTES, None, False, file_ext=".xlsb")
        assert "macro-file-cell-value" in result
