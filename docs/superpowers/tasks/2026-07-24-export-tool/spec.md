# EPMCDME-12313 — Deterministic Export Tool for the Assistant

**Ticket**: https://jiraeu.epam.com/browse/EPMCDME-12313
**Branch**: `EPMCDME-12313_export-tool`

## Problem

The assistant can only present tabular results as markdown tables in chat. Users
need a real, downloadable binary file (`.xlsx` / `.csv`) instead. The conversion
must be **deterministic** — the tool receives already-structured tables, not
free-form markdown it has to re-parse.

## Goal (v1)

A new agent tool that turns structured tables into a stored `.xlsx` or `.csv`
file and returns a chat download link. `.docx` / `.pptx` are explicitly deferred
to a follow-up ticket.

## Scope

### In scope
- `.xlsx` (multi-sheet) and `.csv` output.
- `.xlsx`: one sheet per table, bold header row, frozen header pane, auto-ish
  column width. Handles hundreds of rows/sheet, a few thousand total.
- Structured input: a list of `{sheet_name, columns, rows}` tables.
- Registration into `FileSystemToolkit`, always-on (like `GenerateImageTool`).
- New direct dependency `openpyxl` in `pyproject.toml`.

### Out of scope
- `.docx` / `.pptx` (follow-up ticket).
- Frontend changes — the existing chat file-download flow is reused and only
  **verified**, not modified (touch frontend only if manual e2e proves it does
  not render the download for tool-generated files).
- Reading tables back from markdown.

## Design

### Input schema (pydantic, `args_schema`)

```python
class TableSpec(BaseModel):
    sheet_name: str          # sheet title (xlsx) / ignored for csv content
    columns: list[str]       # header row
    rows: list[list[Any]]    # data rows; cell values any JSON scalar

class ExportTablesInput(BaseModel):
    tables: list[TableSpec]
    filename: str            # base name, e.g. "sales_report" or "sales.xlsx"
    format: Literal["xlsx", "csv"]
```

### Tool contract

- New `ExportTablesTool(CodeMieTool)` in
  `src/codemie_tools/data_management/file_system/export_tables_tool.py`.
- Excluded pydantic fields `file_repository` and `user_id` (same as
  `GenerateImageTool`).
- `execute(...)`:
  1. Validate input (see Validation / edge cases).
  2. Build bytes: `_build_xlsx(tables)` or `_build_csv(tables)`.
  3. Normalize `filename` to carry the correct extension for `format`
     (append `.xlsx` / `.csv` if missing or mismatched) — required so
     `FileExportService.store_exported_bytes` resolves the right MIME from the
     extension.
  4. Store via `FileExportService(file_repository, user_id).store_exported_bytes(filename, content)`.
  5. Return that string verbatim (decorated
     `" File '<name>', URL \`sandbox:/v1/files/<encoded>\`"`). The chat
     `SANDBOX_FILE_RE` extracts the URL and renders a one-click download.
- If `store_exported_bytes` returns `None` (no `file_repository` configured),
  raise `ValueError("File export is not configured.")` — mirrors
  `GenerateImageTool`'s no-repo behavior.
- `output_format` stays `TEXT`; return value is the short link string only, never
  the table data (avoids `CodeMieTool` token-size truncation).

### XLSX generation (openpyxl)

- One worksheet per table, in input order.
- **Sheet title**: sanitized from `sheet_name` — strip Excel-illegal chars
  `[ ] : * ? / \`, truncate to 31 chars, fall back to `Sheet` when empty, and
  de-duplicate collisions with a numeric suffix (`Name`, `Name_2`, ...).
- **Header**: `columns` written to row 1, `Font(bold=True)`.
- **Frozen pane**: `ws.freeze_panes = "A2"` (header stays visible on scroll).
- **Auto-ish width**: per column, width = min(max cell string length + small
  padding, cap) so wide free text does not explode the layout.
- Data rows written as native values (openpyxl handles int/float/str/None).

### CSV generation (stdlib `csv`)

- Exactly one table. `csv.writer` handles quoting/escaping of commas, quotes,
  and newlines. Header row then data rows. `None` → empty cell.
- Written to a text buffer then encoded to UTF-8 bytes.

### Validation / edge cases

- Empty `tables` list → `ValueError` (nothing to export).
- `format == "csv"` with **more than one** table → `ValueError` telling the
  assistant CSV supports exactly one table and to use `xlsx` for multiple
  (deterministic, no silent data loss — per design decision).
- Empty-table edge: a table with `columns` but zero `rows` is **valid** (writes
  a header-only sheet/file).
- Ragged rows (row length ≠ column count) are written as-is; no padding/trim.

### Reuse map

| Concern | Reused component |
|---|---|
| Store bytes + MIME + unique name | `FileExportService.store_exported_bytes` |
| Download link surfacing | existing `SANDBOX_FILE_RE` + `files.py` GET (verify only) |
| Tool base contract | `CodeMieTool` (`execute` → `str`) |
| Toolkit wiring | `FileSystemToolkit.get_tools()` / `FileSystemToolkitUI` |
| License header | copied verbatim from existing source files |

### Registration

- Add `EXPORT_TABLES_TOOL = ToolMetadata(...)` to
  `data_management/file_system/tools_vars.py`.
- Add `Tool.from_metadata(EXPORT_TABLES_TOOL)` to `FileSystemToolkitUI.tools`
  and to the "safe tools" list in `get_tools_ui_info` (so it surfaces for
  non-admins like `GENERATE_IMAGE_TOOL`).
- Instantiate `ExportTablesTool(file_repository=..., user_id=...)`
  unconditionally in `FileSystemToolkit.get_tools()`.

### Dependency

- **Intended**: promote `openpyxl` to a direct dependency (`openpyxl = "^3.1.5"`
  in `[tool.poetry.dependencies]`) + `poetry lock`.
- **Actual (this MR)**: the direct-dep promotion was reverted. `poetry lock`
  cannot run in this environment — it fails to authenticate to the private
  `codemie-enterprise` GCP artifact registry, and the developer has no GCP
  access. `openpyxl 3.1.5` is already resolved in `poetry.lock` as a main-group
  (non-optional) transitive dependency via `markitdown[all]` / `pandas`, so it is
  importable at runtime and the tool works. Promoting it to a direct dependency
  is deferred to a **maintainer with registry access** (follow-up), keeping
  `pyproject.toml` and `poetry.lock` consistent so CI does not fail on a lock
  hash mismatch.

## Testing (TDD — tests first)

New module `tests/codemie_tools/data_management/file_system/test_export_tables_tool.py`,
`unittest.TestCase` + `MagicMock` file_repository (mirrors
`test_generate_image_tool.py`). Cases:

1. **Single-sheet xlsx**: valid, openable workbook (load bytes back via
   `openpyxl.load_workbook`), correct sheet title, header cells, data cells.
2. **Multi-sheet xlsx**: one sheet per table, correct titles and order.
3. **Header styling**: header cells bold; `ws.freeze_panes == "A2"`.
4. **CSV**: correct rows; escaping of comma / quote / newline values.
5. **CSV rejects multiple tables**: `ValueError`.
6. **Empty-table edge**: header-only table produces a valid file.
7. **Large-table**: a few thousand rows produce a valid workbook (smoke/scale).
8. **Sheet-name sanitization / de-dup**: illegal chars stripped, >31 truncated,
   duplicates suffixed.
9. **Storage contract**: `store_exported_bytes` (or `write_file`) called with
   the extension-normalized filename; returned string is passed through.
10. **No file_repository → `ValueError`**.

## Acceptance

- Assistant invokes the tool to produce a real `.xlsx` / `.csv` from structured
  tables.
- Multi-tab workbook with bold header + frozen panes.
- One-click download in chat via the existing file-download flow (verified).
- Tests above pass; `make` quality gates green.
- Commits ticket-prefixed `EPMCDME-12313: ...`.
