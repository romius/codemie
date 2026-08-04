# Technical Analysis — EPMCDME-11738: xlsb ingestion/extraction parity with xlsx

**Generated**: 2026-07-23
**Research path**: direct grep/read (no codegraph index)

---

## 1. Original Context

EPMCDME-11738 — Support for .xlsb File Format. Backend scope only.

Goal: Add native .xlsb support at parity with .xlsx across every backend surface where .xlsx is accepted today:
1. File Data Source ingestion — xlsb files should be ingested and indexed the same way xlsx files are.
2. Assistant chat file attachments — users can attach xlsb files and the assistant can extract cell values, list sheets, filter rows, preview content.
3. Azure DevOps integrations — work items with xlsb attachments and wiki pages referencing xlsb files load without error.

Key constraints from ticket clarifications:
- Formula cells: index stored/cached results, no recalculation (matching current xlsx `data_only` behavior).
- Macros: parse cell data, ignore macros, emit user-facing warning `"Macros were ignored"`.
- File limits/size: same as xlsx — no separate ceilings.
- Corrupt/unreadable xlsb: clear user-facing error message.
- Workbook scope: sheet identity, table structure, cell values/types only — same as xlsx.
- All existing xlsx/xls/csv flows must continue to work unchanged.

Frontend ACs (NOT in scope for this backend run — handled separately in codemie-ui):
- File upload dialogs list xlsb as supported format.
- OS file picker surfaces xlsb files.
- Chat-attachment upload messaging reflects xlsb support.

---

## 2. Codebase Findings

### 2.1 File Data Source ingestion pipeline

**Entry point**: `src/codemie/datasource/loader/file_extraction_utils.py`

`extract_documents_from_bytes` drives all File Data Source ingestion. It resolves a loader by matching the file extension against the `LOADERS` dict, keyed on `IndexKnowledgeBaseFileTypes` enum values:

```python
LOADERS = {
    IndexKnowledgeBaseFileTypes.XLSX.value: XlsxLoader,   # 'xlsx' → langchain_markitdown.XlsxLoader
    ...
}
loader_class = LOADERS.get(file_ext, PlainTextLoader)
```

`IndexKnowledgeBaseFileTypes` (`src/codemie/rest_api/models/index.py:1533`) has `XLSX = 'xlsx'` but **no `XLSB` entry**. An `.xlsb` file falls through to `PlainTextLoader`, producing binary garbage.

**Two changes required**:
- Add `XLSB = 'xlsb'` to `IndexKnowledgeBaseFileTypes`.
- Add `IndexKnowledgeBaseFileTypes.XLSB.value: XlsxLoader` to `LOADERS` (reusing `XlsxLoader` is safe if the underlying converter handles xlsb — see §2.3).

### 2.2 Core spreadsheet read path: xlsx_workers / XlsxProcessor

**Files**:
- `src/codemie_tools/file_analysis/workers/xlsx_workers.py` — `load_xlsx`, `_get_visible_sheets`, `process_xlsx_to_markdown`
- `src/codemie_tools/file_analysis/xlsx/processor.py` — `XlsxProcessor.load()`

`load_xlsx` does two things that are openpyxl-only and will fail on xlsb:

```python
# Visibility detection — openpyxl only
wb = openpyxl.load_workbook(binary_content, read_only=True)
visible_sheet_names = [sheet.title for sheet in wb.worksheets if sheet.sheet_state == 'visible']

# Loading — openpyxl engine
sheets = pd.read_excel(binary_content, engine="openpyxl", sheet_name=sheets_to_load, ...)
```

`openpyxl` **cannot read xlsb** — it will raise `zipfile.BadZipFile`. The fix is to detect xlsb and dispatch to the `pyxlsb` engine:

- `pd.read_excel(file, engine="pyxlsb", sheet_name=None)` reads all xlsb sheets.
- Sheet visibility in xlsb is accessible via `pyxlsb.open_workbook(file).sheets`, each of which has a `visibility` attribute. This mirrors the openpyxl path exactly.
- `pyxlsb` is a read-only binary parser — it never executes VBA macros.

**`pyxlsb` is NOT in `pyproject.toml`** — must be added as a dependency (`pandas` 2.x already supports it as an optional engine).

**Recommended pattern**: add `_get_visible_sheets_xlsb` and `load_xlsb` helpers in `xlsx_workers.py`. Make `load_xlsx` / `process_xlsx_to_markdown` accept a `file_name` parameter (or inspect bytes magic) to dispatch per format. `XlsxProcessor` stays format-agnostic.

**Macro warning**: xlsb files may contain macro streams. Since pyxlsb doesn't execute them, emit `"Macros were ignored"` unconditionally for xlsb inputs (the ticket allows this — all xlsb files are macro-capable by format spec).

### 2.3 XlsxConverter / XlsxTool — chat attachment surface

**Files**:
- `src/codemie_tools/file_analysis/xlsx/markitdown_xlsx_converter.py` — `XlsxConverter` subclasses `markitdown.converters.XlsxConverter` and delegates to `XlsxProcessor`.
- `src/codemie_tools/file_analysis/xlsx/tools.py` — `XlsxTool` is the agent-facing chat-attachment tool.

`XlsxTool._get_supported_mime_types()` currently returns only xlsx/xls MIME types; `_get_supported_extensions()` returns `['.xlsx', '.xls']`. The `FileToolMixin` uses these to filter `input_files` before the tool processes them — xlsb files are silently excluded.

Python stdlib `mimetypes.guess_type('file.xlsb')` returns `application/vnd.ms-excel.sheet.binary.macroenabled.12` (verified in local env).

**Changes required**:
- Add xlsb MIME (`application/vnd.ms-excel.sheet.binary.macroenabled.12`) and `.xlsb` extension to both lists in `XlsxTool`.
- After the `load_xlsx` fix in §2.2, `XlsxProcessor.load()` handles xlsb transparently. `XlsxConverter` needs no change.

For the ingestion path where `XlsxLoader` (langchain_markitdown) wraps `XlsxConverter`: verify that `XlsxLoader` registers itself with the xlsb MIME in MarkItDown's converter registry. If it only registers the xlsx MIME, a separate `XlsbLoader` (thin wrapper pointing at the same `XlsxConverter`) may be needed for the `LOADERS` dict entry.

### 2.4 MimeType class

**File**: `src/codemie_tools/base/file_object.py:91`

```python
XLSX_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
XLS_TYPE  = 'application/vnd.ms-excel'

@property
def is_excel(self) -> bool:
    return self.mime_type in [self.XLSX_TYPE, self.XLS_TYPE]
```

`AttachmentContentMixin._process_content()` routes to `XlsxProcessor` only when `mime.is_excel`. Without xlsb MIME here, xlsb attachments fall through to the `_build_base64_response` fallback.

**Change required**: Add `XLSB_TYPE = 'application/vnd.ms-excel.sheet.binary.macroenabled.12'` constant and include it in `is_excel`.

### 2.5 Azure DevOps Work Item Loader

**File**: `src/codemie/datasource/loader/azure_devops_work_item_loader.py:77`

```python
XLSX_MIME_TYPES = frozenset({
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
})

def _extract_attachment_text(self, content_bytes, content_type, filename):
    ext = os.path.splitext(filename)[1].lower()
    ...
    if content_type in XLSX_MIME_TYPES or ext in {".xlsx", ".xls"}:
        return self._extract_xlsx_text(content_bytes, filename)
```

`_extract_xlsx_text` (~line 502) uses `XlsxProcessor` directly. After the §2.2 fix, xlsb will be handled transparently.

**Change required**: Add xlsb MIME to `XLSX_MIME_TYPES` frozenset and `.xlsb` to the `ext in {…}` set.

### 2.6 Azure DevOps Wiki Loader

**File**: `src/codemie/datasource/loader/azure_devops_wiki_loader.py:68`

Identical pattern — `XLSX_MIME_TYPES` at line 68, extension check at line 624. Same gap, same fix.

### 2.7 AttachmentContentMixin

**File**: `src/codemie_tools/azure_devops/attachment_content_mixin.py:273`

```python
if mime.is_excel:
    processor = XlsxProcessor()
    sheets = processor.load(content_bytes)
    text = processor.convert(sheets)
```

After the `MimeType.is_excel` fix (§2.4) and `XlsxProcessor.load()` fix (§2.2), this route will handle xlsb correctly. **No change required here** beyond the upstreams.

---

## 3. Dependency Gap

| Package | Status | Action |
|---|---|---|
| `openpyxl` 3.1.5 | Installed (indirect) | No change |
| `pyxlsb` | **NOT installed** | Add `pyxlsb = ">=1.0.10"` to `pyproject.toml` |
| `langchain-markitdown` 0.1.8 | Installed | No version bump needed |

`pyxlsb` is the canonical pandas engine for `.xlsb` files and is process-pool safe.

---

## 4. Testing Landscape

Existing xlsx test suites (all in `tests/`):
- `codemie_tools/file_analysis/xlsx/test_xlsx_processor.py`
- `codemie_tools/file_analysis/xlsx/test_xlsx_converter.py`
- `codemie_tools/file_analysis/xlsx/test_nan_handling.py`
- `codemie_tools/file_analysis/test_excel_tool.py`
- `codemie/datasource/loader/test_file_extraction_utils.py`
- `codemie/datasource/loader/test_azure_devops_work_item_loader.py`
- `codemie/datasource/loader/test_azure_devops_wiki_loader.py`

New xlsb tests should mirror the xlsx tests in each suite. A minimal xlsb fixture file can be generated by `pyxlsb`-compatible tooling or converted from a known xlsx fixture.

---

## 5. Risk Indicators

1. **`pyxlsb` in multiprocessing pool**: `load_xlsx` runs inside `file_process_pool` (multiprocessing). Verify `pyxlsb` file handles are not shared across fork boundaries — the existing pattern passes `bytes`, which is safe.
2. **MarkItDown MIME registration for xlsb**: `langchain_markitdown.XlsxLoader` may only register xlsx MIME with MarkItDown's internal converter registry. If so, a thin `XlsbLoader` is needed for the `LOADERS` dict rather than reusing `XlsxLoader` directly.
3. **`mimetypes` platform parity**: `mimetypes.guess_type('file.xlsb')` returns the xlsb MIME on macOS (verified). Confirm same on the production Linux image; if not registered, extension-based routing must be the primary path in ADO loaders.
4. **xlsb visibility API**: `pyxlsb` exposes `wb.sheets` with `visibility` attribute but its semantics differ slightly from openpyxl's `sheet_state`. Defensive fallback to "process all sheets" on detection failure mirrors the existing openpyxl path.

---

## 6. Predicted File Change Surface

| File | Change |
|---|---|
| `pyproject.toml` | Add `pyxlsb` dependency |
| `src/codemie_tools/base/file_object.py` | Add `XLSB_TYPE`; extend `is_excel` |
| `src/codemie/rest_api/models/index.py` | Add `XLSB = 'xlsb'` to `IndexKnowledgeBaseFileTypes` |
| `src/codemie/datasource/loader/file_extraction_utils.py` | Add xlsb to `LOADERS`; optional `DEFAULT_LOADER_KWARGS` entry |
| `src/codemie_tools/file_analysis/workers/xlsx_workers.py` | Add `_get_visible_sheets_xlsb`, `load_xlsb`; dispatch in `load_xlsx` / `process_xlsx_to_markdown` |
| `src/codemie_tools/file_analysis/xlsx/tools.py` | Add xlsb MIME + extension to supported lists |
| `src/codemie/datasource/loader/azure_devops_work_item_loader.py` | Add xlsb MIME to `XLSX_MIME_TYPES`; `.xlsb` to extension set |
| `src/codemie/datasource/loader/azure_devops_wiki_loader.py` | Same |
| New/updated tests | Mirror xlsx tests for each surface |
