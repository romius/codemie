# Technical Research

**Task**: markdown cache service toolkit file conversion error handling logging
**Generated**: 2026-07-31T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Fix EPMCDME-13779: UnsupportedFormatException on chat file attachments. Root cause: unguarded markdown conversion in MarkdownCacheService.get_or_convert() (line 71) propagates exceptions through get_preconverted() → add_file_tools() → request abort. Files from conversation history are re-converted on every message; one bad file permanently breaks the conversation. markitdown 0.1.2 has no converter for CFB/OLE2 (.doc/.xls), password-protected OOXML, ODF, WEBP, zero-byte files. Fix requires: guard conversion with try/except, report per-file failures to LLM, ensure history files cannot break later messages, log errors with filename/owner/size/type.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/service/file_service/markdown_cache_service.py` — `MarkdownCacheService`: wraps markitdown conversion with cache read/write; line 71 contains the unguarded `convert_file_to_markdown()` call that is the root cause. Cache read errors (lines 65–68) and cache write errors (lines 81–84) are already silently swallowed with `logger.warning` — the conversion call at line 71 is the sole unguarded point. Contains `_SKIP_PRECONVERT_MIME_TYPES` and `_SKIP_PRECONVERT_EXTENSIONS` skip-lists used as a pre-conversion opt-out mechanism.
- `src/codemie_tools/file_analysis/workers/markdown_workers.py` — `convert_file_to_markdown()`: calls `markitdown.MarkItDown.convert()`, logs the error, then explicitly re-raises any exception — callers are responsible for catching.
- `src/codemie/service/tools/toolkit_service.py` — `ToolkitService.add_file_tools()` at line 1174: instantiates `MarkdownCacheService`, calls `get_preconverted()`, passes the result dict as `preconverted_content` to `FileAnalysisToolkit`; no exception guard around `get_preconverted()`.
- `src/codemie/core/utils.py` — `build_unique_file_objects_list()` / `_collect_files_from_conversation()`: collects `FileObject` instances from the current request AND all prior history messages up to `history_index`; this is the mechanism by which a single bad file from history re-triggers conversion failure on every subsequent message.
- `src/codemie_tools/base/file_object.py` — `FileObject` model: fields `name`, `mime_type`, `owner`, `path`, `content`; `bytes_content()` method returns `Optional[bytes]`; no dedicated `size` field but `len(fo.bytes_content() or b"")` yields byte size.
- `src/codemie_tools/file_analysis/toolkit.py` — `FileAnalysisToolkit.get_toolkit()`: receives `preconverted_content` dict; currently receives nothing if conversion raises, leaving the toolkit with no file content.
- `src/codemie_tools/file_analysis/file_analysis_tool.py` (lines 154–168) — `FileAnalysisTool._process_single_file()`: already implements the catch-and-fallback pattern (`except Exception as e: return self._fallback_decode_text_file(...)`) — the direct model for the guard to be added in `get_or_convert()`.
- `src/codemie_tools/file_analysis/docx/exceptions.py` — project-local docx exception `UnsupportedFormatError`; distinct from `markitdown.UnsupportedFormatException`.
- `src/codemie/configs/logger.py` — structured JSON logger with contextvars for `uuid`, `user_id`, `conversation_id` automatically injected; all relevant modules import `from codemie.configs.logger import logger`.

### Architecture and Layers Affected

1. **HTTP/API Layer** — `AssistantChatRequest` carries `file_names`, `conversation_id`, and `history_index` into the service layer.
2. **Service/Orchestration Layer** — `ToolkitService.add_file_tools()` orchestrates tool assembly; calls `build_unique_file_objects_list()` then `MarkdownCacheService.get_preconverted()`.
3. **File-Collection Utility** — `build_unique_file_objects_list()` / `_collect_files_from_conversation()` in `src/codemie/core/utils.py`; merges current and history files before passing to the cache service.
4. **Cache/Conversion Service Layer** — `MarkdownCacheService.get_preconverted()` → `get_or_convert()` — cache lookup and conversion dispatch; the primary fix target.
5. **Worker/Integration Layer** — `convert_file_to_markdown()` in `markdown_workers.py` → `markitdown.MarkItDown.convert()`; this layer logs and re-raises.
6. **Agent-Tool Layer** — `FileAnalysisToolkit.get_toolkit(preconverted_content=...)` — downstream consumer; currently receives no content when conversion raises.

### Integration Points

- `MarkdownCacheService` → `FileRepositoryFactory` (cache storage I/O — read and write already individually guarded)
- `MarkdownCacheService` → `convert_file_to_markdown()` → `markitdown.MarkItDown` (raises `markitdown.UnsupportedFormatException`, `markitdown.FileConversionException`, `markitdown.MissingDependencyException` — all subclasses of `markitdown.MarkItDownException`)
- `ToolkitService.add_file_tools()` → `MarkdownCacheService` → `FileAnalysisToolkit`
- `build_unique_file_objects_list()` → `Conversation.find_by_id()` (history fetch)
- Cache storage backends: `FileSystemRepository` (default), AWS S3, Azure Blob, GCS — governed by `FILES_STORAGE_TYPE`

### Patterns and Conventions

- **Skip-list opt-out**: `_SKIP_PRECONVERT_MIME_TYPES` and `_SKIP_PRECONVERT_EXTENSIONS` in `MarkdownCacheService` provide a declarative per-type bypass. `.doc`, `.xls`, WEBP could be added here as a belt-and-suspenders measure, but the try/except guard is still necessary for unknown/future broken formats.
- **Cache write failure already swallowed**: lines 73–82 in `markdown_cache_service.py` use `logger.warning` and return the converted content regardless — the conversion failure guard must follow the same severity and return-instead-of-raise pattern.
- **Catch-and-fallback**: `FileAnalysisTool._process_single_file()` (lines 154–168) is the established precedent — catch `Exception`, return a safe fallback value, continue processing.
- **ZipConverter precedent** (documented in `docs/superpowers/tasks/2026-07-13-epmcdme-5383-zip-upload-datasource/technical-analysis.md` line 79): `UnicodeDecodeError` → `ValueError` caught at the boundary, returning `[]`; same pattern applies here.
- **Structured logging**: all log calls use `%s` positional substitution with the module-level `logger`; contextual fields (`uuid`, `user_id`, `conversation_id`) are auto-injected via contextvars.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/development/error-handling.md` — directly relevant: "Sanitized Failures" rule mandates logging internal detail and returning a safe fallback, never propagating raw exceptions to the client; "Accurate Error Details" rule requires one distinct log message per failure mode with identifying context (owner, filename, size, type); "Typed Exceptions" rule applies to domain-level exception hierarchy.
- `.ai-run/guides/development/logging-patterns.md` — directly relevant: recoverable per-file failures are `logger.warning`-level events, not `logger.error`; log must include operation context and sanitized identifying details, not raw file bytes or tokens.
- `.ai-run/guides/agents/agent-tools.md` — indirectly relevant: tool outputs must be structured and serializable; a fallback error string (e.g., `"[Conversion failed for {name}: UnsupportedFormatException]"`) satisfies this; a re-raised exception does not.
- `.ai-run/guides/agents/custom-tool-creation.md` — indirectly relevant: any behavioral change to `get_or_convert()` requires corresponding new tests in the existing test file.
- `.ai-run/guides/development/performance-patterns.md` — low relevance; async patterns do not apply to the synchronous cache service fix.

### Architectural Decisions

- No `ADR:` or `DECISION:` markers found in `markdown_cache_service.py`, `markdown_workers.py`, `file_analysis_tool.py`, or `toolkit_service.py`.
- No dedicated ADR documents for the markdown cache or markitdown integration exist in `docs/`.
- Prior analogous decision (ZipConverter `UnicodeDecodeError`): catch at the conversion boundary, return safe empty/fallback result, let caller decide what to report to the user — this is the de facto established pattern.

### Derived Conventions

- Exception catch must be at the `get_or_convert()` level (line 71), not inside `convert_file_to_markdown()`, because the worker layer explicitly re-raises after logging. The caller is responsible.
- Fallback value written into `preconverted_content` must be a human-readable string the LLM can consume (e.g., `"[Conversion failed for report.doc: UnsupportedFormatException]"`), not `None` or an empty string, to satisfy the "report per-file failures to LLM" requirement.
- Writing the fallback string to the cache (via the existing `write_file` path at lines 73–82) prevents re-conversion on every subsequent history message — this is the key mechanism for "ensure history files cannot break later messages".
- Log call shape: `logger.warning("MarkdownCache: conversion failed for %s/%s (%s, %d bytes): %s", fo.owner, fo.name, fo.mime_type, len(fo.bytes_content() or b""), e)` — consistent with existing warning-level cache error log style.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/file_service/test_markdown_cache_service.py` — 10 tests covering `MarkdownCacheService`: cache hit, cache miss (writes result), empty cache treated as miss, cache write failure non-fatal, `invalidate()`, `get_preconverted()` happy path, CSV and email MIME-type skipping, CSV extension fallback skipping. All 10 tests mock `convert_file_to_markdown` as a successful no-exception call.
- `tests/codemie/service/tools/test_toolkit_service_file_tools.py` — one test verifying `add_file_tools` delegates to `MarkdownCacheService.get_preconverted` and forwards `preconverted_content` to `FileAnalysisToolkit.get_toolkit`; happy path only, `MarkdownCacheService` fully mocked.
- `tests/codemie/service/test_assistant_service_conversation_file_tools.py` — five tests covering `add_file_tools` with history variations (empty history, no file references, duplicates, missing conversation ID); `MarkdownCacheService` patched out wholesale, conversion behavior never exercised.
- `tests/codemie_tools/file_analysis/test_file_analysis.py` — covers `FileAnalysisTool` internals including `MarkItDown().convert` raising a generic `Exception` inside `convert_file_to_markdown` at the worker level; isolated to the tool layer, not the cache service layer.

### Testing Framework and Patterns

- pytest `^8.3.1` (runtime: `8.4.2`)
- pytest-mock `^3.14.0` (`mocker` fixture used in toolkit service tests)
- pytest-asyncio `^0.23.7` (present but not used in the four directly relevant test files)
- `unittest.mock.patch` as context manager (`with (patch(...) as x, patch(...) as y):` multi-patch blocks) — dominant pattern in `test_markdown_cache_service.py`
- Late import of the class under test inside the `with patch(...)` block to force module re-import after patching
- `MagicMock(spec=...)` for typed mocks (`FileObject`, `Assistant`, `Conversation`, `User`)
- `side_effect = Exception(...)` to simulate storage read failures — same mechanism needed for the new conversion failure tests
- Session-scoped autouse fixture `mock_database_engine` in `tests/conftest.py` patches `PostgresClient.get_engine` globally
- Test patching target for new tests: `patch("codemie.service.file_service.markdown_cache_service.convert_file_to_markdown")`

### Coverage Gaps

- No test for `get_or_convert()` when `convert_file_to_markdown` raises `UnsupportedFormatException`, `FileConversionException`, or generic `Exception` — the exact root cause path has zero coverage.
- No test for `get_preconverted()` partial failure: one file in a list fails, others succeed — no verification that processing continues (skip-and-continue) vs. aborts.
- No test for specific unsupported format types (CFB/OLE2 `.doc`/`.xls`, WEBP, zero-byte content).
- No test verifying that a `FileObject` sourced from conversation history that fails conversion does not abort processing of new-message files in the same `add_file_tools` call.
- No test verifying that `get_preconverted()` or `get_or_convert()` returns a fallback error string (not raises) for failed files.
- No test asserting that logger calls include `filename`, `owner`, `size`, and `type` on conversion failure.
- No test checking whether a failed conversion result is written to cache (preventing re-attempt on every message) vs. left as a cache miss.

---

## 5. Configuration and Environment

### Environment Variables

- `FILES_STORAGE_TYPE` (default `filesystem`) — selects the storage backend for both original files and `-md-cache.md` cache files; values: `filesystem`, `aws`, `azure`, `gcp`.
- `FILES_STORAGE_DIR` (default `./codemie-storage`) — root path for `FileSystemRepository` cache writes.
- `FILES_STORAGE_MAX_UPLOAD_SIZE` (default `100 MB`) — hard upload gate; does not guard conversion, only upload size.
- `ENABLE_FILE_MULTIPROCESSING` (default `false`) — routes datasource-level conversion through a process pool; does NOT apply to the chat-path `MarkdownCacheService` / `add_file_tools` flow. Not a workaround for this bug.
- `LLM_REQUEST_ADD_MARKDOWN_PROMPT` (default `true`) — adds markdown-formatting instruction to LLM requests; affects how conversion output is consumed but unrelated to conversion errors.

### Configuration Files

- `pyproject.toml` — declares `markitdown = {extras = ["all"], version = "^0.1.2"}` (line 120) and `langchain-markitdown = "^0.1.8"` (line 75); `^` constraint allows `0.1.x` upgrades automatically; `[extras = "all"]` means all markitdown optional converters are installed.
- `src/codemie/configs/config.py` — governs `FILES_STORAGE_TYPE`, `FILES_STORAGE_DIR`, `FILES_STORAGE_MAX_UPLOAD_SIZE`, `ENABLE_FILE_MULTIPROCESSING`, Redis knobs, and `LLM_REQUEST_ADD_MARKDOWN_PROMPT`.
- `config/customer/customer-config.yaml` — feature-flag registry; no conversion-related toggle exists.
- `tests/.env.test` — sets `ENABLE_FILE_MULTIPROCESSING=false` for test runs.

### Feature Flags and Deployment Concerns

- No feature flags exist for markdown conversion or `MarkdownCacheService`; it is unconditionally exercised for every non-skipped file in `add_file_tools`.
- **No cache TTL**: the `-md-cache.md` file is persisted indefinitely across all storage backends. Writing the fallback error string to cache is essential to prevent repeated conversion attempts on history files; without it, every message in a conversation with a bad history file will re-invoke `convert_file_to_markdown` at cost.
- **Cache namespace collision**: cache key is `{fo.name}{_CACHE_SUFFIX}` scoped by `(owner, filename)`; the fix must not widen the collision surface (do not add a per-error variant key).
- **Multi-backend cache write**: after the fix ships, deployments using S3, Azure Blob, or GCS must verify that writing the fallback string atomically replaces any prior empty or stale cache entry for the same key.

---

## 6. Risk Indicators

- No existing test coverage for the conversion failure path in `MarkdownCacheService.get_or_convert()` — the exact root cause has zero tests; new tests must be added to `tests/codemie/service/file_service/test_markdown_cache_service.py`.
- `convert_file_to_markdown()` explicitly re-raises after logging — the fix must catch at the `get_or_convert()` call site (line 71), not inside the worker; failing to do so leaves the re-raise path active.
- `markitdown.UnsupportedFormatException` is a subclass of `markitdown.MarkItDownException`; catching only `UnsupportedFormatException` will miss `FileConversionException` and `MissingDependencyException` — the guard should catch `markitdown.MarkItDownException` (or broader `Exception`) to be future-proof.
- History file re-conversion on every message is architectural: `_collect_files_from_conversation()` in `src/codemie/core/utils.py` merges history files unconditionally before passing to `get_preconverted()`; the fix must write the fallback result to cache so the miss-path is not taken on subsequent messages.
- No cache TTL — if the fallback error string is not written to cache, every message in a conversation with a bad file will retry conversion at full cost on all storage backends.
- `get_preconverted()` currently uses a dict comprehension; if the comprehension is not restructured into a loop with per-item exception handling, catching inside `get_or_convert()` alone is sufficient — but the caller (`add_file_tools`) still has no visibility into which files failed; the LLM notification requirement demands a per-file failure map be surfaced to `FileAnalysisToolkit`.
- `FileObject` has no `size` field; log calls must compute `len(fo.bytes_content() or b"")` inline — this reads file bytes into memory, which is already done by the conversion path, so no additional I/O is introduced, but the pattern is non-obvious and should be documented in the fix.
- `markitdown = {extras = ["all"], version = "^0.1.2"}` — a future `0.1.x` release adding CFB/OLE2 support would be picked up automatically, but the guard is still needed for partial converter failures, missing system libraries, and zero-byte edge cases.
- No `TODO` or `FIXME` markers in `markdown_cache_service.py` or `markdown_workers.py` — the unguarded call was not flagged as technical debt before this ticket.

---

## 7. Summary for Complexity Assessment

The task touches three architectural layers: the Cache/Conversion Service layer (`MarkdownCacheService.get_or_convert()` and `get_preconverted()` in `src/codemie/service/file_service/markdown_cache_service.py`), the Service/Orchestration layer (`ToolkitService.add_file_tools()` in `src/codemie/service/tools/toolkit_service.py`, which must handle a richer return type if per-file failure maps are surfaced), and the Worker/Integration layer (`convert_file_to_markdown()` in `src/codemie_tools/file_analysis/workers/markdown_workers.py`, where the re-raise may be softened or left as-is depending on the chosen fix boundary). The file change surface is narrow: 2–3 source files plus the existing test file. The `build_unique_file_objects_list()` utility in `src/codemie/core/utils.py` does not need changes — the history re-conversion problem is solved by writing the fallback result to cache, not by changing collection logic.

The task follows established patterns with moderate novelty. The catch-and-fallback pattern already exists in `FileAnalysisTool._process_single_file()` and the cache write-failure guard in `MarkdownCacheService` itself, so the guard at line 71 is a direct application of precedent. The novel element is surfacing per-file failure information to the LLM via `preconverted_content`: this requires either (a) changing `get_or_convert()` to return a failure sentinel string that the LLM receives as file content, or (b) adding a parallel failure dict returned by `get_preconverted()` and consumed by `FileAnalysisToolkit`. Option (a) is simpler and aligns with the `FileAnalysisTool._process_single_file()` fallback pattern; option (b) changes the interface between `ToolkitService` and `FileAnalysisToolkit`.

Test coverage posture is weak for this specific path: all 10 existing `MarkdownCacheService` tests are happy-path only, and no test exercises `convert_file_to_markdown` raising. The fix requires at minimum 4–5 new test cases covering: (1) `UnsupportedFormatException` returns fallback string, (2) fallback string is written to cache, (3) multiple files where one fails and others succeed, (4) history file failure does not abort new-message processing, and (5) logger call includes owner/filename/mime_type/size. Key risk factors for complexity scoring: the unguarded call is a single line change, but the "report to LLM" and "cache the failure" requirements add non-trivial behavioral surface; test authoring from scratch for the failure path adds effort; the choice between returning a sentinel string vs. a parallel failure dict has downstream interface implications.
