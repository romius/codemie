# Export Tool (EPMCDME-12313) Implementation Plan

> **For agentic workers:** This plan is executed inline via TDD (sdlc-standard Stage 5). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the assistant an `export_tables_tool` that turns structured tables into a real, downloadable `.xlsx` (multi-sheet, styled) or `.csv` file and returns a chat download link.

**Architecture:** New `ExportTablesTool(CodeMieTool)` under `data_management/file_system/`. `execute` builds bytes (openpyxl for xlsx, stdlib `csv` for csv), stores them via `FileExportService.store_exported_bytes` (storage + MIME + unique name), and returns the resulting decorated `sandbox:/v1/files/...` string that the existing chat flow renders as a download. Registered always-on in `FileSystemToolkit` alongside `GenerateImageTool`.

**Tech Stack:** Python, pydantic, openpyxl, stdlib csv, LangChain `CodeMieTool` base, pytest + unittest.mock.

## Global Constraints

- Every new source file starts with the EPAM Apache-2.0 license header (copy verbatim from an existing `src/codemie_tools/...` file).
- Ticket-prefixed commits: `EPMCDME-12313: <desc>`.
- `openpyxl = "^3.1.5"` added as a direct dependency (approval-gated — confirm with user before editing `pyproject.toml` / running `poetry lock`).
- Tool `execute` returns a short string only (the store result); never the table data (avoids `CodeMieTool` token truncation).
- Do not modify `files.py` / frontend — the download flow is verified, not changed.

---

### Task 1: openpyxl dependency (deferred to maintainer)

**Files:** none committed (see outcome).

**Outcome:** The intended direct-dependency promotion was **reverted**.
`poetry lock` cannot run in this environment (auth failure to the private
`codemie-enterprise` GCP registry; developer has no GCP access). `openpyxl
3.1.5` is already resolved in `poetry.lock` as a main-group transitive dependency
(via `markitdown[all]` / `pandas`) and is importable at runtime, so the tool
works. To keep `pyproject.toml` and `poetry.lock` consistent (avoid a CI lock
hash mismatch), the direct-dep promotion is left for a maintainer with registry
access as a **follow-up**, noted in the MR description. `openpyxl` was installed
into the local venv for testing.

---

### Task 2: Tool metadata + input schema + CSV export

**Files:**
- Create: `src/codemie_tools/data_management/file_system/export_tables_tool.py`
- Modify: `src/codemie_tools/data_management/file_system/tools_vars.py` (add `EXPORT_TABLES_TOOL`)
- Test: `tests/codemie_tools/data_management/file_system/test_export_tables_tool.py`

**Interfaces:**
- Consumes: `FileExportService.store_exported_bytes(filename, content) -> Optional[str]`; `CodeMieTool` base.
- Produces:
  - `TableSpec(sheet_name: str, columns: list[str], rows: list[list[Any]])`
  - `ExportTablesInput(tables: list[TableSpec], filename: str, format: Literal["xlsx","csv"])`
  - `ExportTablesTool(CodeMieTool)` with excluded fields `file_repository`, `user_id`; `execute(**kwargs) -> str`
  - Internal `_build_csv(table: TableSpec) -> bytes`, `_normalize_filename(name, fmt) -> str`
  - `EXPORT_TABLES_TOOL: ToolMetadata` (name `export_tables_tool`)

**Test-first: yes** — CSV round-trip + escaping, multi-table CSV rejection, empty-tables error, no-repo error, storage-call contract.

- [ ] **Step 1: Write failing tests** in the test module:

```python
import csv
import io
from unittest import TestCase
from unittest.mock import MagicMock

from codemie_tools.data_management.file_system.export_tables_tool import (
    ExportTablesTool, TableSpec, ExportTablesInput,
)


def _stored():
    sf = MagicMock()
    sf.to_encoded_url.return_value = "encoded-id"
    return sf


def _tool():
    repo = MagicMock()
    repo.write_file.return_value = _stored()
    return ExportTablesTool(file_repository=repo, user_id="u1"), repo


class ExportCsvTests(TestCase):
    def test_csv_single_table_round_trips_with_escaping(self):
        tool, repo = _tool()
        table = TableSpec(
            sheet_name="Data",
            columns=["a", "b"],
            rows=[["x,y", 'she said "hi"'], ["line1\nline2", None]],
        )
        result = tool.execute(tables=[table], filename="out", format="csv")
        # stored once, csv MIME resolved from the normalized filename
        kwargs = repo.write_file.call_args.kwargs
        self.assertEqual(kwargs["mime_type"], "text/csv")
        self.assertTrue(kwargs["name"].endswith("out.csv"))
        parsed = list(csv.reader(io.StringIO(kwargs["content"].decode("utf-8"))))
        self.assertEqual(parsed[0], ["a", "b"])
        self.assertEqual(parsed[1], ["x,y", 'she said "hi"'])
        self.assertEqual(parsed[2], ["line1\nline2", ""])
        self.assertIn("sandbox:/v1/files/encoded-id", result)

    def test_csv_rejects_multiple_tables(self):
        tool, _ = _tool()
        t = TableSpec(sheet_name="s", columns=["a"], rows=[["1"]])
        with self.assertRaises(ValueError):
            tool.execute(tables=[t, t], filename="out", format="csv")

    def test_empty_tables_raises(self):
        tool, _ = _tool()
        with self.assertRaises(ValueError):
            tool.execute(tables=[], filename="out", format="csv")

    def test_missing_repository_raises(self):
        tool = ExportTablesTool(file_repository=None, user_id="u1")
        t = TableSpec(sheet_name="s", columns=["a"], rows=[["1"]])
        with self.assertRaises(ValueError):
            tool.execute(tables=[t], filename="out", format="csv")
```

- [ ] **Step 2: Run to verify fail** — `poetry run pytest tests/codemie_tools/data_management/file_system/test_export_tables_tool.py -v` → FAIL (module/class missing).
- [ ] **Step 3: Implement** metadata in `tools_vars.py`:

```python
EXPORT_TABLES_TOOL = ToolMetadata(
    name="export_tables_tool",
    description=(
        "Export structured tables to a downloadable .xlsx (multi-sheet) or .csv file. "
        "Input is a list of tables ({sheet_name, columns, rows}), NOT markdown. "
        "Use when the user wants a real spreadsheet/CSV file to download."
    ),
    label="Export tables",
    user_description="Lets the AI assistant export structured tabular data as a downloadable Excel or CSV file.",
)
```

  and the tool file (license header + full implementation): `TableSpec`, `ExportTablesInput`, `ExportTablesTool` with `execute` dispatching on `format`, `_build_csv` using `csv.writer` (None → ""), `_normalize_filename` appending `.<fmt>` when missing/mismatched, storage via `FileExportService(self.file_repository, self.user_id).store_exported_bytes(...)`, raising `ValueError("File export is not configured.")` when it returns `None`, and raising `ValueError` for empty tables / multi-table CSV.

- [ ] **Step 4: Run to verify pass** — same pytest command → PASS.
- [ ] **Step 5: Commit** — `git add ... && git commit -m "EPMCDME-12313: Add export_tables_tool with CSV export"`

---

### Task 3: XLSX export (multi-sheet + styling)

**Files:**
- Modify: `src/codemie_tools/data_management/file_system/export_tables_tool.py` (add `_build_xlsx`)
- Test: `tests/codemie_tools/data_management/file_system/test_export_tables_tool.py` (add xlsx cases)

**Interfaces:**
- Consumes: `openpyxl.Workbook`, `openpyxl.load_workbook` (tests).
- Produces: `_build_xlsx(tables: list[TableSpec]) -> bytes`; `_sheet_title(raw, used: set) -> str`.

**Test-first: yes** — openable multi-sheet workbook, bold header + `freeze_panes == "A2"`, sheet-name sanitization/de-dup, header-only edge, large-table smoke.

- [ ] **Step 1: Write failing tests** (append to the module):

```python
import openpyxl
from openpyxl.styles import Font  # noqa: F401


class ExportXlsxTests(TestCase):
    def _load(self, repo):
        content = repo.write_file.call_args.kwargs["content"]
        return openpyxl.load_workbook(io.BytesIO(content))

    def test_multi_sheet_headers_rows_and_styling(self):
        tool, repo = _tool()
        tables = [
            TableSpec(sheet_name="Sales", columns=["Region", "Total"],
                      rows=[["EU", 10], ["US", 20]]),
            TableSpec(sheet_name="Costs", columns=["Item", "Amount"],
                      rows=[["Rent", 5]]),
        ]
        tool.execute(tables=tables, filename="report", format="xlsx")
        kwargs = repo.write_file.call_args.kwargs
        self.assertEqual(
            kwargs["mime_type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertTrue(kwargs["name"].endswith("report.xlsx"))
        wb = self._load(repo)
        self.assertEqual(wb.sheetnames, ["Sales", "Costs"])
        ws = wb["Sales"]
        self.assertEqual([c.value for c in ws[1]], ["Region", "Total"])
        self.assertEqual(ws["A2"].value, "EU")
        self.assertTrue(ws["A1"].font.bold)
        self.assertEqual(ws.freeze_panes, "A2")

    def test_sheet_name_sanitized_and_deduped(self):
        tool, repo = _tool()
        bad = "a" * 40 + "[x]:*?/\\"
        tables = [
            TableSpec(sheet_name=bad, columns=["c"], rows=[["1"]]),
            TableSpec(sheet_name=bad, columns=["c"], rows=[["2"]]),
        ]
        tool.execute(tables=tables, filename="r", format="xlsx")
        wb = self._load(repo)
        for name in wb.sheetnames:
            self.assertLessEqual(len(name), 31)
            self.assertFalse(set(name) & set('[]:*?/\\'))
        self.assertEqual(len(set(wb.sheetnames)), 2)

    def test_header_only_table_is_valid(self):
        tool, repo = _tool()
        tables = [TableSpec(sheet_name="Empty", columns=["a", "b"], rows=[])]
        tool.execute(tables=tables, filename="r", format="xlsx")
        wb = self._load(repo)
        self.assertEqual([c.value for c in wb["Empty"][1]], ["a", "b"])

    def test_large_table_is_valid(self):
        tool, repo = _tool()
        rows = [[i, f"row{i}"] for i in range(3000)]
        tables = [TableSpec(sheet_name="Big", columns=["n", "label"], rows=rows)]
        tool.execute(tables=tables, filename="r", format="xlsx")
        wb = self._load(repo)
        self.assertEqual(wb["Big"].max_row, 3001)
```

- [ ] **Step 2: Run to verify fail** — pytest the new class → FAIL (`_build_xlsx` missing / format branch).
- [ ] **Step 3: Implement `_build_xlsx`**: `Workbook`, remove default sheet, per table create `ws = wb.create_sheet(_sheet_title(...))`, write header with `Font(bold=True)`, `ws.freeze_panes = "A2"`, append data rows, compute auto-ish width per column (`min(max_len + 2, 60)`), save to `io.BytesIO`. `_sheet_title` strips `[]:*?/\`, truncates to 31, falls back to `"Sheet"`, de-dups with `_2` suffix while keeping ≤31.
- [ ] **Step 4: Run to verify pass** — pytest module → PASS (all xlsx + csv cases).
- [ ] **Step 5: Commit** — `git add ... && git commit -m "EPMCDME-12313: Add multi-sheet XLSX export with styling"`

---

### Task 4: Register the tool in FileSystemToolkit

**Files:**
- Modify: `src/codemie_tools/data_management/file_system/toolkit.py`
- Test: `tests/codemie_tools/data_management/test_file_system_toolkit.py` (add case) or the tool test module.

**Interfaces:**
- Consumes: `ExportTablesTool`, `EXPORT_TABLES_TOOL`.
- Produces: `ExportTablesTool` present in `FileSystemToolkit.get_tools()` and in the UI catalog.

**Test-first: yes** — `get_tools()` of a toolkit built with a mock repo contains an `ExportTablesTool` instance.

- [ ] **Step 1: Write failing test**:

```python
def test_toolkit_exposes_export_tables_tool():
    from codemie_tools.data_management.file_system.toolkit import FileSystemToolkit
    from codemie_tools.data_management.file_system.export_tables_tool import ExportTablesTool
    toolkit = FileSystemToolkit.get_toolkit(configs={"user_id": "u1"}, file_repository=MagicMock())
    assert any(isinstance(t, ExportTablesTool) for t in toolkit.get_tools())
```

- [ ] **Step 2: Run to verify fail** — pytest → FAIL (tool absent).
- [ ] **Step 3: Implement** — import `ExportTablesTool` + `EXPORT_TABLES_TOOL`; append `Tool.from_metadata(EXPORT_TABLES_TOOL)` to `FileSystemToolkitUI.tools` and to the safe-tools list in `get_tools_ui_info`; instantiate `ExportTablesTool(file_repository=self.file_repository, user_id=self.user_id or "")` unconditionally in `get_tools()`.
- [ ] **Step 4: Run to verify pass** — pytest → PASS.
- [ ] **Step 5: Commit** — `git add ... && git commit -m "EPMCDME-12313: Register export_tables_tool in FileSystemToolkit"`

---

### Task 5: Manual e2e verification of chat download (verify-only)

**Files:** none (verification).

- [ ] **Step 1:** Confirm the returned string contains a bare `sandbox:/v1/files/...` URL that `SANDBOX_FILE_RE` matches (already asserted in Task 2/3 tests).
- [ ] **Step 2:** If a running instance is available, invoke the tool through the assistant and confirm the chat renders a one-click download for the generated `.xlsx` / `.csv`. If the download does not surface, escalate — only then consider a frontend change (per ticket constraint).
- [ ] **Step 3:** Record the outcome in the QA/handoff notes.
