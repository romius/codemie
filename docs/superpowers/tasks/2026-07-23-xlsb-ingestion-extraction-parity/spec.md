# Spec: EPMCDME-11738 — xlsb Ingestion/Extraction Parity with xlsx

**Ticket**: EPMCDME-11738 (prefix verified: all repo commits use EPMCDME-; branch `EPMCDME-11738_xlsb-ingestion-extraction-parity` is already checked out)  
**Scope**: Backend only  
**Date**: 2026-07-23

---

## 1. Goal

Add native `.xlsb` (Excel Binary Workbook) support across every backend surface that currently accepts `.xlsx`, at full functional parity. The frontend file-picker and upload-dialog ACs are **out of scope** for this run — they are handled separately in codemie-ui and are listed as dependencies at the end of §2.

---

## 2. Acceptance Criteria

| # | Criterion |
|---|---|
| AC1 | `.xlsb` files can be uploaded to a File Data Source and indexed like `.xlsx` — cell values, sheet names, and table structure are extracted and searchable. |
| AC2 | In assistant chat, an attached `.xlsb` file is accepted by the Excel tool; sheet listing, cell preview, row filtering, and full-content extraction all work identically to `.xlsx`. |
| AC3 | Azure DevOps work items and wiki pages with `.xlsb` attachments are loaded without error; attachment text content is extracted. Note: corrupt xlsb ADO attachments yield silently empty content (same as the existing xlsx error-handling path there) — AC6 does not apply to that surface. |
| AC4 | Formula cells: stored/cached values are indexed; no recalculation is performed (matches existing xlsx `data_only` behavior — decided with product owner). |
| AC5 | Macro-containing xlsb files: cell data is parsed; macros are silently ignored. No user-facing message is emitted. |
| AC6 | Corrupt or unreadable `.xlsb` files produce a clear, non-empty error message at the File Data Source and chat surfaces. No crash or silent data loss. |
| AC7 | All existing `.xlsx`, `.xls`, and `.csv` ingestion and extraction flows continue working unchanged. |

### Out-of-scope dependencies (codemie-ui)
- File upload dialog lists `.xlsb` as a supported format.
- OS file picker surfaces `.xlsb` files.
- Chat-attachment upload messaging reflects xlsb support.

---

## 3. New Dependency

Add to `pyproject.toml` `[tool.poetry.dependencies]`:
```toml
python-calamine = ">=0.2.0"
```

Also update `poetry.lock` (run `poetry lock --no-update` after adding the dependency).

`python-calamine` is the recommended pandas engine for `.xlsb` files in pandas ≥ 2.2. It is a read-only Rust-backed parser that:
- Returns dates as typed Python `datetime` objects (pyxlsb returns raw serial numbers)
- Exposes sheet-visibility metadata via `CalamineWorkbook.sheets_metadata` with `SheetVisibleEnum` (pyxlsb has no visibility API)
- Fails cleanly on password-protected files (same generic `CalamineError` via the pandas engine — no distinct PasswordError surfaces through `pd.read_excel`)
- Returns formula error cells as empty values (pyxlsb returns internal hex codes)
- Never executes VBA macros and is safe inside a multiprocessing pool (the existing pattern passes `bytes`, not file handles, across fork boundaries)

---

## 4. Architecture

### 4.1 Approach

**Single `file_ext` dispatch parameter (Approach A).** An optional `file_ext: str = '.xlsx'` parameter is added to the core read path functions. When `file_ext == '.xlsb'`, the function dispatches to calamine helpers; otherwise the existing openpyxl path runs unchanged. All existing xlsx call sites default to `.xlsx` and require no modification.

### 4.2 Core read path

**`src/codemie_tools/file_analysis/workers/markdown_workers.py`** — Chat preconversion:

`convert_file_to_markdown(file_bytes, file_name, ...)` runs before any file tool executes; it uses a bare `MarkItDown()` that has no xlsb converter and would raise before `XlsxTool` ever runs (the originally reported failure mode). Add a short-circuit before the `MarkItDown()` call:

```python
import os
from codemie_tools.file_analysis.workers.xlsx_workers import process_xlsx_to_markdown

ext = os.path.splitext(file_name or '')[1].lower()
if ext == '.xlsb':
    return process_xlsx_to_markdown(file_bytes, sheet_names=None, visible_only=False, file_ext='.xlsb')
```

**Sheet scope for preconversion: all sheets including hidden/veryHidden** (`visible_only=False`). The bare-MarkItDown xlsx preconversion reads all sheets today; the xlsb short-circuit must match that exactly.

**`src/codemie_tools/file_analysis/workers/xlsx_workers.py`**:

- Add `_get_visible_sheets_xlsb(binary_content: bytes) -> Optional[List[str]]`  
  Uses `python_calamine.CalamineWorkbook.from_filelike(io.BytesIO(binary_content))` and the `sheets_metadata` property to filter for `SheetVisibleEnum.Visible` entries (the enum is `SheetVisibleEnum`, not `SheetVisible`). On failure: logs warning, returns `None` (falls back to processing all sheets — same behavior as the xlsx path).

- Add `_load_xlsb_sheets(binary_content: bytes, sheets_to_load) -> dict[str, pd.DataFrame]`  
  Calls `pd.read_excel(io.BytesIO(binary_content), engine="calamine", sheet_name=sheets_to_load, keep_default_na=True, na_filter=True)`.

- Extend `load_xlsx(file_bytes, sheet_names, visible_only, clean_data, filter_values, filter_mode, file_ext='.xlsx')`  
  When `file_ext == '.xlsb'`: calls `_get_visible_sheets_xlsb` + `_load_xlsb_sheets`.  
  When `file_ext != '.xlsb'`: existing openpyxl path unchanged.  
  Shared utilities (`_normalize_column_names`, `_replace_nan_with_empty`, `_clean_dataframe`, `_filter_dataframe`) run for both paths.

- Extend `process_xlsx_to_markdown(file_bytes, sheet_names, visible_only, file_ext='.xlsx')`  
  Adds `file_ext` as a keyword argument with default `.xlsx`. Passes it to `load_xlsx`. No macro warning is appended.

**`src/codemie_tools/file_analysis/xlsx/processor.py`**:

- Extend `XlsxProcessor.load(file_content, clean_data=True, file_ext='.xlsx')`  
  Passes `file_ext` through `maybe_pool_submit(load_xlsx, ..., file_ext)`.

**`src/codemie_tools/file_analysis/xlsx/markitdown_xlsx_converter.py`**:

- Override `accepts()` to include `.xlsb` (the inherited base-class method only accepts `.xlsx`):
  ```python
  def accepts(self, file_stream, stream_info, **kwargs):
      return stream_info.extension in ('.xlsx', '.xlsb') if stream_info else False
  ```
- Extend `convert()` to derive `file_ext` from `stream_info.extension` and pass to `processor.load()`:
  ```python
  file_ext = (stream_info.extension or '.xlsx').lower()
  sheets_clean = self.processor.load(file_stream, clean_data=True, file_ext=file_ext)
  ```
  This covers the FileAnalysisTool path that registers this converter.

### 4.3 Ingestion loader

`src/codemie/datasource/loader/xlsb_loader.py` — **new file**:

```
XlsbLoader(file_path: str, split_by_page: bool = False)
    .load() -> List[Document]
```

- Reads file bytes from `file_path`.
- **Sheet scope**: `visible_only` is a constructor parameter on `XlsxProcessor` (not `load()`). Instantiate with `XlsxProcessor(visible_only=False)` — the existing xlsx Data Source path indexes all sheets including hidden/veryHidden; xlsb ingestion must match.
- Calls `XlsxProcessor(visible_only=False).load(file_bytes, file_ext='.xlsb')` → `dict[str, pd.DataFrame]`.
- **Per-sheet Documents** (no `"## "` string splitting): iterates the sheets dict directly. For each `(sheet_name, df)` pair, converts via `XlsxProcessor.convert({sheet_name: df})` → per-sheet markdown, wraps in a `Document`. Avoids false splits on sheet names or cell text containing `## `.
- `_sheets_to_markdown` prepends `## {sheet_name}` to each sheet's markdown. Confirm during implementation whether the existing `langchain_markitdown.XlsxLoader`'s split documents include or exclude that heading, and whether metadata uses `page_number=sheet_name` — the xlsb `Document`s must match whatever xlsx produces, heading and metadata both.
- When `split_by_page=True`, one `Document` per sheet. When `False`, one joined `Document`.
- No `openpyxl.load_workbook()` call.

### 4.4 Registration points

**`src/codemie/rest_api/models/index.py`**  
Add to `IndexKnowledgeBaseFileTypes`:
```python
XLSB = 'xlsb'
```

**`src/codemie/datasource/loader/file_extraction_utils.py`**  
```python
from codemie.datasource.loader.xlsb_loader import XlsbLoader

LOADERS[IndexKnowledgeBaseFileTypes.XLSB.value] = XlsbLoader
DEFAULT_LOADER_KWARGS[IndexKnowledgeBaseFileTypes.XLSB.value] = {"split_by_page": True}
```

**Side effect**: registering `.xlsb` in `LOADERS` automatically extends xlsb pickup to all crawlers that feed `extract_documents_from_bytes` — Git, SVN, SharePoint, and email-attachment crawling. Accepted per story clarification.

**`src/codemie_tools/base/file_object.py`**  
```python
XLSB_TYPE = 'application/vnd.ms-excel.sheet.binary.macroenabled.12'

@property
def is_excel(self) -> bool:
    return normalise_mime(self.mime_type) in [self.XLSX_TYPE, self.XLS_TYPE, self.XLSB_TYPE]
```

Use the existing `normalise_mime()` helper (defined at `file_object.py:26`) for all MIME comparisons — it lowercases and strips MIME parameters, handling the `macroEnabled`/`macroenabled` casing split.

**`src/codemie_tools/file_analysis/xlsx/tools.py`**  
```python
def _get_supported_mime_types(self):
    return [
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.ms-excel',
        'application/vnd.ms-excel.sheet.binary.macroenabled.12',
    ]

def _get_supported_extensions(self):
    return ['.xlsx', '.xls', '.xlsb']
```

`file_ext` must thread through both internal helpers:
- `_process_excel_file(file_object, ...)`: derives `file_ext = os.path.splitext(file_object.name)[1].lower()`, passes to `process_xlsx_to_markdown(..., file_ext=file_ext)`.
- `_load_excel_file(file_object, ..., file_ext='.xlsx')`: passes `file_ext` to `processor.load(file_object.bytes_content(), clean_data=clean_data, file_ext=file_ext)`. Called at `tools.py:157, 177, 265, 479, 522` — all callers must derive and pass `file_ext` from the `FileObject`.

**`src/codemie/datasource/loader/azure_devops_work_item_loader.py`**  
```python
XLSX_MIME_TYPES = frozenset({
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/vnd.ms-excel.sheet.binary.macroenabled.12",   # xlsb
})

if normalise_mime(content_type) in XLSX_MIME_TYPES or ext in {".xlsx", ".xls", ".xlsb"}:
```

`_extract_xlsx_text` derives `file_ext = os.path.splitext(filename)[1].lower()` and passes to `XlsxProcessor.load(bytes, file_ext=file_ext)`.

**`src/codemie/datasource/loader/azure_devops_wiki_loader.py`**  
Identical changes to `XLSX_MIME_TYPES`, the extension set, and the `normalise_mime()` comparison.

**`src/codemie_tools/azure_devops/attachment_content_mixin.py`**  
`filename` is already in scope. Derive `file_ext` and pass to `XlsxProcessor.load()`:
```python
if mime.is_excel:
    file_ext = os.path.splitext(filename)[1].lower() if filename else '.xlsx'
    processor = XlsxProcessor()
    sheets = processor.load(content_bytes, file_ext=file_ext)
    text = processor.convert(sheets)
```

**`src/codemie_tools/file_analysis/email/tools.py` (line 729)**  
One-line extension — add `.xlsb` to the existing dispatch:
```python
if ext in (".xlsx", ".xls", ".xlsb"):
    return str(XlsxTool(config=cfg).execute())
```

**`src/codemie_tools/core/project_management/xwiki/tools.py` (line 653)**  
`filename` is already in scope. Thread `file_ext` the same way as the ADO mixin:
```python
if mime.is_excel:
    file_ext = os.path.splitext(filename)[1].lower() if filename else '.xlsx'
    processor = XlsxProcessor()
    sheets = processor.load(content_bytes, file_ext=file_ext)
    text = processor.convert(sheets)
```

**`src/codemie/service/tools/tool_execution_service.py` (line ~503)**  
Add `XLSB` to the supported-formats string:
```python
"Tool requires uploaded file. Supported formats: PPTX, DOCX, XLSX, XLSB, PDF, CSV, JPEG, PNG, ..."
```

---

## 5. Data Flows

### Chat preconversion (xlsb) — runs before any file tool
```
MarkdownCacheService.get_preconverted(file_bytes, "report.xlsb")
  → convert_file_to_markdown(file_bytes, "report.xlsb", ...)
      → ext = '.xlsb'  → short-circuit before MarkItDown()
      → process_xlsx_to_markdown(file_bytes, sheet_names=None, visible_only=False, file_ext='.xlsb')
          → load_xlsx(..., visible_only=False, file_ext='.xlsb')
              → _get_visible_sheets_xlsb returns None (visible_only=False skips filtering)
              → pd.read_excel(engine="calamine", sheet_name=None)  # all sheets
              → shared DataFrame utilities
          → _sheets_to_markdown()
```
Sheet scope: all sheets (matches bare-MarkItDown xlsx preconversion behavior).

### Chat tool (xlsb) — runs after preconversion
```
XlsxTool.execute()
  → _get_supported_files()          # now includes .xlsb
  → _process_excel_file(file_object)
      → file_ext = os.path.splitext(file_object.name)[1].lower()  # '.xlsb'
      → process_xlsx_to_markdown(bytes, sheet_names, visible_only=True, file_ext='.xlsb')
  → _load_excel_file(file_object, ..., file_ext=file_ext)  # sheet listing, filtering, stats, etc.
      → XlsxProcessor(visible_only=True).load(bytes, file_ext='.xlsb')
```
Sheet scope: visible-only (same as existing xlsx chat tool path).

### Ingestion (xlsb)
```
extract_documents_from_bytes(bytes, "report.xlsb", ...)
  → file_ext = 'xlsb'
  → LOADERS['xlsb'] = XlsbLoader
  → XlsbLoader(temp_path, split_by_page=True).load()
      → XlsxProcessor(visible_only=False).load(file_bytes, file_ext='.xlsb')
      → iterate sheets dict: for each (sheet_name, df):
            XlsxProcessor.convert({sheet_name: df})  → per-sheet markdown
            Document(page_content=sheet_markdown, metadata={...})
```
Sheet scope: all sheets (matches existing xlsx Data Source ingestion path).

### ADO work item / wiki (xlsb)
```
_extract_attachment_text(bytes, content_type, filename)
  → normalise_mime(content_type) in XLSX_MIME_TYPES  OR  ext == '.xlsb'
  → file_ext = os.path.splitext(filename)[1].lower()
  → _extract_xlsx_text(bytes, filename)
      → XlsxProcessor().load(bytes, file_ext=file_ext)
```

---

## 6. Error Handling

Per-surface error contract (verified against code):

| Surface | Scenario | Behavior |
|---|---|---|
| **File Data Source** | Corrupt / unreadable xlsb | Per-file failures are swallowed into skipped counters in `file_loader.py _load_docs_parallel`. User-facing error appears only if all files fail ("no chunks imported"). AC6 test: single corrupt xlsb datasource → Error status with non-empty message. |
| **Chat (preconversion)** | Corrupt / unreadable xlsb | `convert_file_to_markdown` re-raises the `CalamineError`; it propagates to the user as an error message. AC6 test at the `convert_file_to_markdown` boundary. |
| **ADO work item / wiki** | Corrupt xlsb attachment | `_extract_xlsx_text` catches all exceptions and returns `""` — silently empty content, warning logged, item still loads. Satisfies AC3 ("loaded without error"). AC6 does **not** apply to this surface (documented in AC3 note). |
| **All calamine surfaces** | Password-protected xlsb | Same generic `CalamineError` ("Cannot detect file format") as corrupt files — no distinct PasswordError surfaces through `pd.read_excel(engine="calamine")`. Tests assert a non-empty error message, not a specific exception class. |
| **All surfaces** | xlsb sheet visibility detection fails | `_get_visible_sheets_xlsb` catches exception, logs warning, returns `None`; `load_xlsx` falls back to loading all sheets. |
| **All surfaces** | `python-calamine` not installed | `ImportError` from `pd.read_excel`; surfaces as "Failed to process Excel file". |
| **Regression guard** | xlsb file routed to xlsx path | Prevented by `file_ext` dispatch — openpyxl path only taken when `file_ext != '.xlsb'`. |

---

## 7. Testing

New xlsb tests are added alongside existing xlsx tests. Existing xlsx tests are not modified — they serve as regression guards.

### Fixtures

Four fixtures will be provided (do not attempt to create `.xlsb` files — no library or LibreOffice can write the format):
- `tests/fixtures/plain.xlsb` — minimal sheet with text and numbers
- `tests/fixtures/formula.xlsb` — cells with known cached numeric and string formula results
- `tests/fixtures/macro.xlsb` — macro-enabled file, normal cell data
- `tests/fixtures/password.xlsb` — password-protected (password not provided; used to assert unreadability)

Generate the corrupt fixture in-test by truncating `plain.xlsb` (e.g. first 3000 bytes) — no Excel required:
```python
@pytest.fixture
def corrupt_xlsb_bytes(plain_xlsb_path):
    return open(plain_xlsb_path, 'rb').read(3000)
```

### Test table

| Test file | New tests |
|---|---|
| `tests/codemie_tools/file_analysis/workers/test_markdown_workers.py` (new) | `test_xlsb_preconversion_produces_markdown` — verifies `convert_file_to_markdown` short-circuits correctly and returns non-empty markdown; `test_corrupt_xlsb_preconversion_raises` — verifies error propagates |
| `tests/codemie_tools/file_analysis/xlsx/test_xlsx_processor.py` | `test_load_xlsb_visible_sheets`, `test_load_xlsb_all_sheets`, `test_load_xlsb_with_filter`, `test_corrupt_xlsb_raises_clear_error`, `test_password_protected_xlsb_raises_clear_error`, `test_formula_cached_values_numeric`, `test_formula_cached_values_string`, `test_macro_file_ingests_normally` |
| `tests/codemie_tools/file_analysis/xlsx/test_xlsx_converter.py` | `test_xlsb_converter_produces_markdown` (via extended `XlsxConverter.accepts()` + `convert()`) |
| `tests/codemie_tools/file_analysis/test_excel_tool.py` | `test_xlsb_file_accepted_by_tool`, `test_xlsb_sheet_listing` (via `_load_excel_file`), `test_xlsb_filter_rows` |
| `tests/codemie/datasource/loader/test_file_extraction_utils.py` | `test_xlsb_routed_to_xlsb_loader`, `test_xlsb_produces_documents` |
| `tests/codemie/datasource/loader/test_azure_devops_work_item_loader.py` | `test_xlsb_attachment_text_extracted`, `test_corrupt_xlsb_attachment_loads_empty` |
| `tests/codemie/datasource/loader/test_azure_devops_wiki_loader.py` | `test_xlsb_attachment_text_extracted` |
| `tests/codemie_tools/azure_devops/test_attachment_content_mixin.py` (if exists) | `test_xlsb_attachment_processed_via_mixin` |
| `tests/codemie_tools/file_analysis/email/test_email_tool.py` (if exists) | `test_xlsb_email_attachment_extracted` |
| `tests/codemie_tools/core/project_management/xwiki/test_xwiki_tool.py` (if exists) | `test_xlsb_xwiki_attachment_extracted` |

AC6 error tests assert that corrupt and password-protected xlsb files cause a non-empty error message — not a specific exception class.

---

## 8. File Change Summary

| File | Change type |
|---|---|
| `pyproject.toml` | Dependency added (`python-calamine`) |
| `poetry.lock` | Updated |
| `src/codemie/datasource/loader/xlsb_loader.py` | New file |
| `src/codemie_tools/base/file_object.py` | Modified (`XLSB_TYPE`, `is_excel`, `normalise_mime` use) |
| `src/codemie/rest_api/models/index.py` | Modified |
| `src/codemie/datasource/loader/file_extraction_utils.py` | Modified |
| `src/codemie_tools/file_analysis/workers/markdown_workers.py` | Modified (xlsb preconversion short-circuit) |
| `src/codemie_tools/file_analysis/workers/xlsx_workers.py` | Modified |
| `src/codemie_tools/file_analysis/xlsx/processor.py` | Modified |
| `src/codemie_tools/file_analysis/xlsx/markitdown_xlsx_converter.py` | Modified (`accepts()` override, `file_ext` in `convert()`) |
| `src/codemie_tools/file_analysis/xlsx/tools.py` | Modified (`file_ext` in `_load_excel_file` + `_process_excel_file`) |
| `src/codemie/datasource/loader/azure_devops_work_item_loader.py` | Modified |
| `src/codemie/datasource/loader/azure_devops_wiki_loader.py` | Modified |
| `src/codemie_tools/azure_devops/attachment_content_mixin.py` | Modified (file_ext threading) |
| `src/codemie_tools/file_analysis/email/tools.py` | Modified (add `.xlsb` at line 729) |
| `src/codemie_tools/core/project_management/xwiki/tools.py` | Modified (file_ext threading at line 653) |
| `src/codemie/service/tools/tool_execution_service.py` | Modified (add XLSB to format list at line ~503) |
| Test files (7 suites + 3 conditional) | Tests added |
