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

"""End-to-end XLSB tests against REAL fixtures — no mocking of pandas/calamine.

These tests parse an actual .xlsb workbook through python-calamine and therefore cover
what the mocked unit tests cannot: real formula cached-value reads, sheet visibility, and
mixed cell types (EPMCDME-11738 follow-up item 7, spec §7).

The XLSB format cannot be written by any Python library or by LibreOffice, so the fixture
must be authored by a human in Microsoft Excel and committed next to this file. See
``fixtures/xlsb/README.md`` for the exact layout. Until ``sample.xlsb`` exists, every test
here is skipped rather than failed.
"""

import os

import pytest

from codemie.datasource.exceptions import UnreadableWorkbookError
from codemie.datasource.loader.file_extraction_utils import extract_documents_from_bytes
from codemie.datasource.loader.xlsb_loader import XlsbLoader
from codemie_tools.base.file_object import FileObject, MimeType
from codemie_tools.file_analysis.models import FileAnalysisConfig
from codemie_tools.file_analysis.workers.xlsx_workers import load_xlsx
from codemie_tools.file_analysis.xlsx.tools import XlsxTool

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "xlsb")
SAMPLE_XLSB = os.path.join(FIXTURE_DIR, "sample.xlsb")
MACRO_XLSB = os.path.join(FIXTURE_DIR, "sample_macro.xlsb")
PROTECTED_XLSB = os.path.join(FIXTURE_DIR, "sample_protected.xlsb")

# Documented sheet layout (see fixtures/xlsb/README.md) — asserted verbatim.
VISIBLE_SHEETS = {"Data", "Summary"}
ALL_SHEETS = {"Data", "Summary", "HiddenSheet", "VeryHidden"}
# The Summary sheet holds a formula whose *cached* result must equal this value.
EXPECTED_FORMULA_CACHED_VALUE = "42"

pytestmark = pytest.mark.skipif(
    not os.path.exists(SAMPLE_XLSB),
    reason=(
        "Real XLSB fixture missing. Author fixtures/xlsb/sample.xlsb in Microsoft Excel "
        "per fixtures/xlsb/README.md (XLSB cannot be generated programmatically)."
    ),
)


def _sample_bytes() -> bytes:
    with open(SAMPLE_XLSB, "rb") as fh:
        return fh.read()


def _load_sheet_names(visible_only: bool) -> set[str]:
    sheets = load_xlsx(
        _sample_bytes(),
        None,  # sheet_names
        visible_only,
        True,  # clean_data
        None,  # filter_values
        "exact",  # filter_mode
        ".xlsb",  # file_ext -> calamine engine
    )
    return set(sheets.keys())


# --- load_xlsx: sheet visibility (calamine engine) ---------------------------


def test_load_xlsx_visible_only_returns_visible_sheets():
    assert _load_sheet_names(visible_only=True) == VISIBLE_SHEETS


def test_load_xlsx_all_sheets_includes_hidden_and_very_hidden():
    assert _load_sheet_names(visible_only=False) == ALL_SHEETS


# --- XlsbLoader / extract_documents_from_bytes -------------------------------


def test_xlsb_loader_reads_all_sheets():
    docs = XlsbLoader(SAMPLE_XLSB, split_by_page=True).load()
    page_numbers = {d.metadata["page_number"] for d in docs}
    assert page_numbers == ALL_SHEETS


def test_extract_documents_from_bytes_reads_xlsb():
    docs = extract_documents_from_bytes(_sample_bytes(), "sample.xlsb", datasource_id="")
    assert docs, "expected at least one document from the real xlsb"
    combined = "\n".join(d.page_content for d in docs)
    assert EXPECTED_FORMULA_CACHED_VALUE in combined


def test_formula_cached_value_present_in_output():
    """AC4: cached formula results are read (not recalculated)."""
    docs = XlsbLoader(SAMPLE_XLSB, split_by_page=True).load()
    summary = next(d for d in docs if d.metadata["page_number"] == "Summary")
    assert EXPECTED_FORMULA_CACHED_VALUE in summary.page_content


# --- Chat operations via XlsxTool --------------------------------------------


def _xlsx_tool(visible_only: bool = True) -> XlsxTool:
    file_obj = FileObject(
        name="sample.xlsb",
        mime_type=MimeType.XLSB_TYPE,
        owner="test",
        content=_sample_bytes(),
    )
    return XlsxTool(config=FileAnalysisConfig(input_files=[file_obj]))


def test_chat_full_read_contains_data_sheet():
    out = _xlsx_tool().execute()
    assert "Data" in out


def test_chat_get_sheet_names_visible_only():
    out = _xlsx_tool().execute(get_sheet_names=True)
    assert "Data" in out and "Summary" in out
    assert "HiddenSheet" not in out and "VeryHidden" not in out


def test_chat_get_sheet_names_all():
    out = _xlsx_tool().execute(get_sheet_names=True, visible_only=False)
    for name in ALL_SHEETS:
        assert name in out


def test_chat_sheet_by_index():
    out = _xlsx_tool().execute(sheet_index=0)
    assert out.strip()


def test_chat_filter_values():
    # A filter that matches nothing should yield an (empty) result without error.
    out = _xlsx_tool().execute(filter_values=["__no_such_value__"])
    assert isinstance(out, str)


# --- Corrupt fixture (generated in-test by truncating the real file) ---------


def test_truncated_xlsb_raises_unreadable_workbook_error(tmp_path):
    corrupt = tmp_path / "corrupt.xlsb"
    corrupt.write_bytes(_sample_bytes()[:50])
    with pytest.raises(UnreadableWorkbookError) as exc_info:
        XlsbLoader(str(corrupt)).load()
    assert "corrupt.xlsb" in str(exc_info.value)


# --- Macro-enabled variant: VBA part is ignored, parses identically ----------


@pytest.mark.skipif(not os.path.exists(MACRO_XLSB), reason="sample_macro.xlsb fixture not present")
def test_macro_variant_reads_all_sheets_and_cached_formula():
    """A macro-enabled xlsb (VBA project embedded) parses identically to the plain one —
    spreadsheet readers ignore the macro part. Same 4 sheets, same cached formula value."""
    docs = XlsbLoader(MACRO_XLSB, split_by_page=True).load()
    page_numbers = {d.metadata["page_number"] for d in docs}
    assert page_numbers == ALL_SHEETS
    summary = next(d for d in docs if d.metadata["page_number"] == "Summary")
    assert EXPECTED_FORMULA_CACHED_VALUE in summary.page_content


# --- Password-protected variant: surfaces as UnreadableWorkbookError ----------


@pytest.mark.skipif(not os.path.exists(PROTECTED_XLSB), reason="sample_protected.xlsb fixture not present")
def test_password_protected_xlsb_raises_unreadable_workbook_error():
    """calamine raises on an encrypted/password-protected workbook; the loader surfaces the
    actionable UnreadableWorkbookError (same combined message as the corrupt case, spec §3)."""
    with pytest.raises(UnreadableWorkbookError) as exc_info:
        XlsbLoader(PROTECTED_XLSB).load()
    assert "sample_protected.xlsb" in str(exc_info.value)
