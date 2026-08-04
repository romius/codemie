# Technical Research

**Task**: export tool xlsx csv file toolkit file_export_service codemie_tool (EPMCDME-12313)
**Generated**: 2026-07-24
**Research path**: filesystem (codegraph MCP not available — tool-not-found)

---

## 1. Original Context

EPMCDME-12313 https://jiraeu.epam.com/browse/EPMCDME-12313: Add a deterministic export tool for the assistant.

GOAL
Give the assistant a tool that converts structured table data from its output into a real, downloadable binary file — not a markdown table.

SCOPE v1 (this ticket)
- .xlsx (multi-sheet) and .csv only. Defer .docx/.pptx to a follow-up ticket.
- .xlsx: one sheet per table, header row styled (bold), frozen header pane, auto-ish column width. Handle hundreds of rows/sheet, a few thousand total.
- Tool input = structured tables (list of {sheet_name, columns, rows}), NOT free-form markdown the LLM has to re-parse.

REUSE (do not build from scratch)
- FileExportService at src/codemie_tools/data_management/code_executor/file_export_service.py — for storing the generated file + MIME detection.
- Download endpoint already exists: src/codemie/rest_api/routers/files.py (same path touched in EPMCDME-12266). The chat download-link rendering already works for assistant files — verify it surfaces the tool output.
- Follow the base tool pattern: base/codemie_tool.py, base_toolkit.py, file_tool_mixin.py, file_object.py. Look at an existing simple tool as a template for registration/toolkit wiring.

DEPENDENCY
- openpyxl is only a transitive/optional dep and is NOT importable now. Add it as an explicit direct dependency in pyproject.toml (needs approval).
- python-docx / python-pptx / pandas are already direct deps (for the follow-up).

ACCEPTANCE
- Assistant can invoke the tool to produce a real .xlsx / .csv from structured tables.
- Multi-tab workbook with basic styling (header + frozen panes).
- One-click download in the chat UI (reuse existing file-download flow).
- TDD: tests first.

CONSTRAINTS
- Backend repo only for v1. Ticket-prefixed commits "EPMCDME-12313: ...". Branch EPMCDME-12313_export-tool.

---

## 2. Codebase Findings

### Existing Implementations

**Base tool contract** — `src/codemie_tools/base/codemie_tool.py`
- `class CodeMieTool(BaseTool)` (LangChain `BaseTool` subclass). Concrete tools implement one method:
  - `def execute(self, *args, **kwargs) -> Any` (abstract). This is where the tool logic lives.
  - `_run` wraps `execute`, validates config, applies token-size truncation (`tokens_size_limit`), and JSON-serializes non-string returns via `_post_process_output_content`. **Return a plain `str` (the sandbox URL) to avoid JSON wrapping.**
  - Fields available: `name`, `description`, `args_schema`, `output_format: ToolOutputFormat` (TEXT/MARKDOWN).
  - `_validate_config()` auto-checks any config field flagged `required_at_runtime` (see `RequiredField` in models.py).

**Closest template — `GenerateImageTool`** — `src/codemie_tools/data_management/file_system/generate_image_tool.py` (66 lines, the cleanest in-memory-bytes→file→sandbox-URL example):
- Declares `args_schema` from a local `BaseModel` input class.
- Holds `file_repository: Optional[Any]` and `user_id: str` as excluded pydantic fields.
- `execute(...)` generates bytes then calls:
  ```python
  stored_file = self.file_repository.write_file(
      name=filename, mime_type="image/png", content=<bytes>, owner=self.user_id,
  )
  return f"sandbox:/v1/files/{stored_file.to_encoded_url()}"
  ```
- **This return-string-is-the-download-link pattern is exactly what the export tool needs.** No sandbox session or code-execution machinery required for in-memory generation.

**Tool metadata registry** — `src/codemie_tools/data_management/file_system/tools_vars.py`
- Each tool has a `ToolMetadata(name=..., description=..., label=..., user_description=...)` constant (e.g. `GENERATE_IMAGE_TOOL`). The new tool needs an entry here (or a new `tools_vars.py`) e.g. `EXPORT_TABLES_TOOL`.

**Toolkit wiring** — `src/codemie_tools/data_management/file_system/toolkit.py`
- `FileSystemToolkitUI(ToolKit)` lists tools via `Tool.from_metadata(<META>)` — this is the UI-facing catalog.
- `FileSystemToolkit(BaseToolkit)` implements:
  - `get_tools() -> list` — instantiates tool objects, injecting `file_repository`, `user_id`, `input_files`.
  - `get_toolkit(cls, configs, file_repository, chat_model, image_generator, input_files)` — factory that reads `configs` dict and constructs the toolkit.
- **The export tool fits naturally into this existing toolkit** (it already receives `file_repository` + `user_id`), or into `DataManagementToolkit`.

**FileExportService** — `src/codemie_tools/data_management/code_executor/file_export_service.py`
- `class FileExportService.__init__(self, file_repository=None, user_id="")`.
- Relevant reusable methods:
  - `store_exported_bytes(self, filename: str, content: bytes) -> Optional[str]` — computes extension→MIME via `_determine_mime_type`, writes `uuid4()_<basename>` to repo, returns a **human-readable string** `" File '<name>', URL \`sandbox:/v1/files/<encoded>\`"` (note: NOT a bare sandbox URL — different shape than GenerateImageTool).
  - `@staticmethod _determine_mime_type(extension: str) -> str` — `mimetypes.guess_type` with `application/octet-stream` fallback. Reusable for `.xlsx`/`.csv` MIME resolution.
  - `collect_files_from_execution` / `export_files_from_execution` require a live `llm_sandbox.SandboxSession` — **not applicable** to pure in-memory generation.
- **Decision point for planning:** `store_exported_bytes` returns a decorated sentence, while the chat download flow keys off a bare `sandbox:/v1/files/...` URL (see §2 Integration Points). The GenerateImageTool pattern (direct `file_repository.write_file` + bare sandbox URL) is the more reliable match for one-click chat download. Reusing `FileExportService._determine_mime_type` for MIME while emitting a bare sandbox URL is a viable middle path.

### Architecture and Layers Affected

- **Agent-Tool layer** (primary): new `CodeMieTool` subclass + input schema + `ToolMetadata` + toolkit registration under `src/codemie_tools/data_management/...`.
- **Toolkit discovery layer**: `src/codemie_tools/base/toolkit_provider.py` auto-discovers `DiscoverableToolkit` subclasses; UI catalog surfaces via `get_tools()` / `get_available_toolkits_info()`.
- **API layer** (verify-only, no change expected): `src/codemie/rest_api/routers/files.py` `GET /v1/files/{file_name}` serves the stored bytes as a download.
- **Dependency/build layer**: `pyproject.toml` `[tool.poetry.dependencies]` — add `openpyxl`.

### Integration Points

**How the tool output becomes a one-click chat download (the full chain):**
1. Tool `execute()` returns the string `sandbox:/v1/files/<encoded_url>`.
2. `<encoded_url>` = `FileObject.to_encoded_url()` = base64 of `[mime_type, owner, name]` via `StringSerializer` (`src/codemie_tools/base/file_object.py:146`).
3. Agent output scanning: `src/codemie/agents/tools/agent.py:36` `SANDBOX_FILE_RE = re.compile(r"sandbox:/v1/files/[^\s)\]>\"']+")`, used at line 147 (`SANDBOX_FILE_RE.findall(value)`) to extract file references from tool/agent output — this is what surfaces the download link in chat.
4. Download served by `read_file` in `files.py:157` — decodes the file, and for the XLSX MIME (`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, unknown to the handler map) falls through to `get_attachment_response` → `Content-Disposition: attachment` (correct one-click download). CSV (`text/csv`) is also not in `READ_FILE_MIME_TYPE_HANDLERS` → also attachment. Good.
5. Other sandbox-URL consumers to be aware of: `src/codemie/service/agent_workspace_service.py:47` (`SANDBOX_FILE_PREFIX`), `src/codemie/service/conversation/export_utils.py:32`, `src/codemie/service/mcp/toolkit.py:398`.

**File repository storage contract** (`write_file`):
- `file_repository.write_file(name=<str>, mime_type=<str>, content=<bytes>, owner=<user_id>) -> stored_file`, where `stored_file.to_encoded_url()` yields the URL slug. Obtained in routers via `FileRepositoryFactory().get_current_repository()`.

**MIME constants already defined** — `src/codemie_tools/base/file_object.py`:
- `MimeType.XLSX_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'`
- `MimeType.CSV_TYPE = 'text/csv'`
- Use these directly rather than relying on `mimetypes.guess_type` (which does resolve `.xlsx`/`.csv` correctly, but the constants are explicit and already imported across the codebase).

### Patterns and Conventions

- Tool = `CodeMieTool` subclass implementing `execute`; input = a local pydantic `BaseModel` assigned to `args_schema`.
- The v1 input shape (`list[{sheet_name, columns, rows}]`) maps cleanly to a nested pydantic model, e.g. `TableSpec(sheet_name: str, columns: list[str], rows: list[list[Any]])` and `ExportTablesInput(tables: list[TableSpec], filename: str, format: Literal["xlsx","csv"])`.
- Registration is **auto-discovery** based: any `DiscoverableToolkit` subclass under `codemie_tools/` is found by `toolkit_provider._find_toolkits()` (recursive `pkgutil` scan, cached via `lru_cache`). Adding the tool to an existing discovered toolkit (`DataManagementToolkit` / `FileSystemToolkit`) requires only a `Tool.from_metadata(...)` entry in the UI class plus instantiation in `get_tools()`.
- Every source file carries the EPAM Apache-2.0 license header (copy verbatim into new files).
- Return `str` from `execute` (LLM-facing). `output_format` can stay `TEXT`.
- License/naming: tool `name` is snake_case; `Tool.set_label` auto-capitalizes words if no label given.

---

## 3. Documentation Findings

### Guides and Architecture Docs
Relevant guides under `.ai-run/guides/` (per AGENTS.md task classifier — "Agents & Tools" category):
- `.ai-run/guides/agents/custom-tool-creation.md` (P0 — the tool-creation workflow)
- `.ai-run/guides/agents/agent-tools.md`, `.ai-run/guides/agents/tool-overview.md`
- `.ai-run/guides/api/rest-api-patterns.md` (files router conventions)
- `.ai-run/guides/testing/testing-patterns.md` + `testing-service-patterns.md` (TDD requirement)
- `.ai-run/guides/development/configuration-patterns.md` (dependency/config additions)
- `.ai-run/guides/standards/git-workflow.md` (ticket-prefixed commits)
These should be read by the implementer before coding (AGENTS.md mandates loading the P0 guide first).

### Architectural Decisions
- Auto-discovery of toolkits is a deliberate architectural choice (`toolkit_provider._find_toolkits`, docstring on `get_hedgeable_toolkits` explicitly documents "auto-discovered with no extra registration").
- `files.py` deliberately routes unknown/binary MIME types (xlsx, csv) to `attachment` disposition with `X-Content-Type-Options: nosniff` (security-by-default for downloads). Inline handlers exist only for html/svg/js/xml (active-content sanitization) — xlsx/csv are safe by omission.

### Derived Conventions
- Tool → sandbox-URL → chat-download is an established, tested pattern (GenerateImageTool + its tests). Follow it rather than inventing a new response contract.

---

## 4. Testing Landscape

### Existing Coverage
- **Best template test**: `tests/codemie_tools/data_management/file_system/test_generate_image_tool.py` — `unittest.TestCase`, mocks `file_repository` with `MagicMock`, asserts `execute(...)` returns `"sandbox:/v1/files/encoded-file-id"` and that `write_file` was called with expected `mime_type` / `content` / `owner` kwargs. Directly reusable structure for the export tool.
- Related: `tests/codemie_tools/data_management/test_file_system_toolkit.py`, `tests/codemie_tools/data_management/file_system/test_file_system_tools.py`, `tests/codemie_tools/data_management/workspace/test_generate_image_tool_v2.py`, and the `code_executor/` test suite (export-path validation, models).

### Testing Framework and Patterns
- `unittest.TestCase` with `unittest.mock` (`MagicMock`, `patch`) is the dominant style in tool tests; pytest is the runner (per AGENTS.md testing guide). Fixtures are lightweight — mocks constructed inline in helper methods (e.g. `_tool_with_generator`).
- Assertions inspect `mock_repo.write_file.call_args.kwargs` — the export tool tests should assert the workbook/csv bytes were written with the correct MIME (`application/vnd.openxmlformats-...sheet` / `text/csv`) and the returned bare sandbox URL.

### Coverage Gaps
- No existing tests for XLSX/CSV generation from structured tables (greenfield). TDD: write `tests/codemie_tools/data_management/<location>/test_export_tables_tool.py` first — cover: single-sheet xlsx, multi-sheet xlsx, csv, header styling (bold + frozen pane via openpyxl `Worksheet.freeze_panes`/`Font(bold=True)`), empty/edge-case tables, correct MIME + sandbox URL, `file_repository` missing → error.
- No existing openpyxl usage in `src/` to model against — styling/column-width logic is net-new (openpyxl only appears transitively in poetry.lock via markitdown/pandas extras).

---

## 5. Configuration and Environment

### Environment Variables
- No new env var strictly required. Note existing gating pattern in `FileSystemToolkit`: `FILE_SYSTEM_TOOLS_ENABLED`, `CODE_EXECUTOR_ENABLED` (via `os.getenv(...) == "true"`). If the export tool should be feature-flagged, follow this convention; otherwise it can ship enabled like `GenerateImageTool` (always in `get_tools()`).

### Configuration Files
- `pyproject.toml` `[tool.poetry.dependencies]` (header at line 18). Current relevant deps:
  - `pandas = "^2.2.2"` (line 125) — already direct.
  - `python-docx = "^1.1.0"` (line 130), `python-pptx = "^1.0.2"` (line 135) — already direct (for follow-up docx/pptx).
  - **`openpyxl` is NOT listed** as a direct dependency. It exists only transitively/optionally in `poetry.lock` (openpyxl 3.1.5, pulled via `markitdown[all]` extra and `pandas`'s `excel`/`xlsx` extras — lines 5274/5293/6269 of poetry.lock). It is therefore NOT reliably importable. **Action (needs approval per ticket): add `openpyxl = "^3.1.5"` to `[tool.poetry.dependencies]`** (place alphabetically ~line 123-124, near `openapi-schema-pydantic`/`packaging`), then `poetry lock` + install.

### Feature Flags and Deployment Concerns
- Download endpoint requires no auth for `GET /v1/files/{file_name}` (public read by encoded URL); `POST` endpoints require `authenticate`. No deployment manifest change needed. Adding openpyxl slightly increases the image dependency set (pure-python, low risk).

---

## 6. Risk Indicators

- **Dependency approval gate**: openpyxl must be promoted from transitive/optional to a direct dep in `pyproject.toml` before the tool can import it; ticket says this needs approval. Until then, any import will fail at runtime in a clean environment. (poetry.lock lines 5274/5293/6269 confirm it is optional-only today.)
- **Response-shape mismatch**: `FileExportService.store_exported_bytes` returns a decorated sentence (`" File '...', URL \`sandbox:/v1/files/...\`"`), NOT a bare `sandbox:/v1/files/...` URL. The chat download regex (`SANDBOX_FILE_RE`) will still `findall` the URL inside it, but the GenerateImageTool bare-URL pattern is cleaner and better-tested — pick one deliberately and test the exact string.
- **No prior openpyxl usage in `src/`**: header styling, `freeze_panes`, and auto-ish column width are net-new logic with no in-repo precedent to copy — moderate implementation novelty; cover thoroughly with tests.
- **Scale**: ticket targets hundreds of rows/sheet, a few thousand total. openpyxl in-memory write is fine at this scale, but `CodeMieTool._limit_output_content` truncates by token count — ensure the tool returns only the short sandbox URL, not the table data, to avoid truncation.
- **MIME/security path**: xlsx/csv rely on falling through to `get_attachment_response` in `files.py` (no dedicated handler). Verify `write_file` stores the correct MIME so the encoded URL round-trips (`FileObject.from_encoded_url` reconstructs `[mime_type, owner, name]`); a wrong MIME would still download but mislabel the file.
- **codegraph not indexed**: research done via filesystem fallback only (codegraph MCP tool-not-found) — call-graph completeness is best-effort via grep, not semantic. Additional sandbox-URL consumers beyond those listed in §2 may exist.
- **Toolkit placement decision open**: tool can live in `FileSystemToolkit` (already injects `file_repository`+`user_id`) or `DataManagementToolkit` (matches "Data Management" domain but currently only defines UI/`get_definition`, no `get_tools` instance wiring). This is an architecture choice the plan must make.

---

## 7. Summary for Complexity Assessment

This is a **single-layer, additive feature** concentrated in the Agent-Tool layer under `src/codemie_tools/data_management/`. The change surface is small and well-bounded: one new `CodeMieTool` subclass (`execute` returning a bare `sandbox:/v1/files/<encoded>` string), one pydantic input schema for the structured-tables payload, one `ToolMetadata` constant, and a one-to-two-line registration into an existing auto-discovered toolkit (`FileSystemToolkit.get_tools()` + its UI list, or `DataManagementToolkit`). Plus a `pyproject.toml` dependency addition (openpyxl) and a new test module. Estimated ~4-6 files touched (2-3 new source files, 1 new test file, `pyproject.toml`/`poetry.lock`, and one toolkit edit). The download/chat-surfacing chain (`files.py` GET endpoint + `SANDBOX_FILE_RE` extraction) already exists and requires verification only, not modification.

**Technical novelty is low-to-moderate.** The tool-produces-file → returns-sandbox-URL → chat-renders-download pattern is fully established and unit-tested by `GenerateImageTool` and its test file, which serve as near drop-in templates. The genuinely new work is the openpyxl workbook construction (bold header font, `freeze_panes`, auto column width, multi-sheet) — there is no prior openpyxl usage in `src/` to copy, so that logic is written from scratch, but the library API is stable and the scale (a few thousand rows) is trivial for openpyxl. CSV generation is straightforward (stdlib `csv`).

**Test coverage posture is a clean greenfield with a strong template.** The affected area currently has zero coverage for table→file export, but the surrounding tool test conventions (unittest + MagicMock file_repository, asserting `write_file` kwargs and the returned sandbox URL) are clear and directly reusable, making TDD low-friction. **Key risk factors for scoring:** (1) the openpyxl direct-dependency promotion is an approval-gated external blocker (not a code risk, but a process one); (2) a deliberate decision is needed on response shape (bare URL vs `FileExportService.store_exported_bytes`'s decorated string) and on toolkit placement; (3) net-new openpyxl styling logic warrants thorough edge-case tests. None of these touch security, auth, persistence schemas, or cross-cutting infrastructure, keeping overall complexity modest.
