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

from unittest.mock import patch, MagicMock

import pytest

from codemie_tools.file_analysis.workers.markdown_workers import convert_file_to_markdown


XLSB_BYTES = b"fake xlsb content"


class TestXlsbPreconversion:
    def test_xlsb_short_circuits_before_markitdown(self):
        with patch(
            "codemie_tools.file_analysis.workers.markdown_workers.process_xlsx_to_markdown",
            return_value="## Sheet1\n| A |\n| val |",
        ) as mock_conv:
            result = convert_file_to_markdown(XLSB_BYTES, "report.xlsb")
        mock_conv.assert_called_once_with(XLSB_BYTES, sheet_names=None, visible_only=False, file_ext=".xlsb")
        assert "Sheet1" in result

    def test_xlsb_short_circuit_does_not_call_markitdown(self):
        with patch(
            "codemie_tools.file_analysis.workers.markdown_workers.process_xlsx_to_markdown",
            return_value="md",
        ):
            # Patch the name in the module where it's used (from markitdown import MarkItDown),
            # NOT the source module — otherwise the real MarkItDown still runs.
            with patch("codemie_tools.file_analysis.workers.markdown_workers.MarkItDown") as mock_md:
                convert_file_to_markdown(XLSB_BYTES, "report.xlsb")
        mock_md.assert_not_called()

    def test_corrupt_xlsb_preconversion_raises(self):
        with patch(
            "codemie_tools.file_analysis.workers.markdown_workers.process_xlsx_to_markdown",
            side_effect=Exception("Cannot detect file format"),
        ):
            with pytest.raises(Exception, match="Cannot detect file format"):
                convert_file_to_markdown(XLSB_BYTES, "broken.xlsb")

    def test_xlsx_file_not_affected_by_xlsb_short_circuit(self):
        mock_result = MagicMock()
        mock_result.text_content = "xlsx content"
        with patch("codemie_tools.file_analysis.workers.markdown_workers.MarkItDown") as mock_md_cls:
            mock_md = mock_md_cls.return_value
            mock_md.convert.return_value = mock_result
            with patch("codemie_tools.file_analysis.workers.markdown_workers.process_xlsx_to_markdown") as mock_xlsb:
                convert_file_to_markdown(b"xlsx bytes", "report.xlsx")
        mock_xlsb.assert_not_called()
