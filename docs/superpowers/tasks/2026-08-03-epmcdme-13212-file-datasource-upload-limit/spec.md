# Spec: Configurable File Datasource upload limit + batched file saving

**Jira**: EPMCDME-13212 — Allow more than 10 files to be uploaded to File based Datasource
**Complexity**: Medium (20/36) — see `complexity-assessment.json`
**Date**: 2026-08-03

## Problem

File-type Datasource create (`POST /index/knowledge_base/file`) and update (`PUT /index/knowledge_base/file`) both hardcode a 10-file limit with no way to raise it. The product requirement is: keep 10 as the default, but make it configurable per deployment. Raising the limit to hundreds of files must not create a performance problem, so file saving needs to happen in batches rather than one file at a time. The frontend upload dropzone independently hardcodes its own 10-file cap, so raising the backend limit alone does not let users upload more files through the UI — the frontend needs a way to discover the configured limit.

## Architecture

One new global config setting replaces two hardcoded `ClassVar[int] = 10` constants, and a new shared helper on `FileDatasourceService` replaces two duplicated sequential file-write loops (create path and update path) with a small bounded thread pool. No database schema changes. No per-client/per-datasource override — this is a single deployment-wide setting, consistent with other tunables in `Config`. ZIP archive extraction limits (`_ZIP_MAX_FILE_COUNT`, `_ZIP_MAX_UNCOMPRESSED_BYTES` in `zip_utils.py`) are out of scope and remain untouched. The existing indexing/embedding pipeline (`base_datasource_processor.py`, already batching at 50 docs/batch) is unaffected — the performance concern this spec addresses is specifically the file **upload/save** step that runs synchronously inside the HTTP request handler, before any background task is scheduled. The configured limit is also advertised read-only via the existing `GET /v1/info` endpoint so the frontend can size its own upload guard against the real limit instead of a hardcoded constant.

## Components

1. **`src/codemie/configs/config.py`** — add `FILE_DATASOURCE_MAX_UPLOAD_COUNT: int = 10`, alongside `FILES_STORAGE_MAX_UPLOAD_SIZE`. Env-var overridable like every other setting on `Config`. No enforced ceiling in code — a plain configurable int.
2. **`src/codemie/rest_api/models/index.py`** — in `IndexKnowledgeBaseFileRequest.validate_files_count` and `UpdateKnowledgeBaseFileRequest.validate_files_count`, replace the `cls.MAX_FILE_COUNT` reference with `config.FILE_DATASOURCE_MAX_UPLOAD_COUNT` (mirroring the existing `validate_files_sizes` pattern, which already reads `config.FILES_STORAGE_MAX_UPLOAD_SIZE` at validation time). Drop both now-unused `MAX_FILE_COUNT: ClassVar[int] = 10` declarations. `IndexKnowledgeBaseFileRequest.MIN_FILE_COUNT` is untouched.
3. **`src/codemie/service/datasource/file_datasource_service.py`** — new `FileDatasourceService.process_files_batch(files, user_id, file_repo)` static method. Runs the existing `_process_upload_file` over all files concurrently via `ThreadPoolExecutor(max_workers=3)` (matching the precedent in `codemie_tools/data_management/code_executor/file_upload_service.py`), then merges the per-file `(paths, filenames)` results back in input order. This is a dedicated, ephemeral pool per call — it does not reuse the shared pools in `rest_api/routers/utils.py`, to avoid contention with unrelated background work.
4. **`src/codemie/rest_api/routers/index.py`** (`index_knowledge_base_files`) and **`file_datasource_service.py`** (`upload_and_prepare_files`) — both replace their inline `for file in files: FileDatasourceService._process_upload_file(...)` loop with a single call to `FileDatasourceService.process_files_batch(...)`. The router keeps its existing `except ZipExtractionError` → HTTP 422 conversion wrapped around the call.
5. **`src/codemie/core/models.py`** (`InfoResponse`) and **`src/codemie/rest_api/routers/common.py`** (`app_info`) — add `file_datasource_max_upload_count: int` to `InfoResponse` and populate it from `config.FILE_DATASOURCE_MAX_UPLOAD_COUNT`. Read-only advertisement on the existing `GET /v1/info` endpoint; no new endpoint. Consuming this field in the upload dropzone is a change to the separate frontend repository, out of scope here.

## Data flow

Request → Pydantic validator checks `len(files)` against `config.FILE_DATASOURCE_MAX_UPLOAD_COUNT` (default 10, raisable via env var) → accepted files are written to storage concurrently by `process_files_batch` (3 workers) → paths/filenames returned in original order → downstream is unchanged: `FileDatasourceProcessor` / the update use case build from those paths and index using their existing internal batching. Separately, `GET /v1/info` returns `file_datasource_max_upload_count` (serialized as `fileDatasourceMaxUploadCount`) alongside `version`/`description`, so a client can discover the configured limit without a dedicated request.

## Error handling

Semantics are unchanged from the caller's perspective: a bad file (missing filename, corrupt/oversized ZIP) still raises `ZipExtractionError`, still surfaces as HTTP 422 at the router boundary — it will propagate out of `process_files_batch` when its results are consumed. One nuance introduced by parallelizing: today, a failing file stops the loop immediately, so files *after* it in the list are never written; with a thread pool, several files around the failure point may already be in flight or written by the time the error surfaces. There is no rollback of plain (non-ZIP) files across the whole batch today, so this does not regress an existing guarantee — it only changes which specific files end up partially written on failure, which was never a guarantee to begin with.

## Testing

- Regression: existing `test_validate_files_count_high` (create model, `tests/codemie/rest_api/models/test_index_models.py`) must still pass unchanged (default stays 10).
- New: `UpdateKnowledgeBaseFileRequest.validate_files_count` tests — currently no test class exists for this model. Add boundary test at the default of 10, and a monkeypatched-config test allowing more than 10.
- New: `IndexKnowledgeBaseFileRequest.validate_files_count` test with a monkeypatched higher `config.FILE_DATASOURCE_MAX_UPLOAD_COUNT` (e.g. 50), proving the limit is genuinely configurable.
- New: unit tests for `FileDatasourceService.process_files_batch` — result order is preserved under concurrent writes, and a `ZipExtractionError` raised by one file's processing propagates out of the call.
- New: `tests/codemie/rest_api/routers/test_common.py` — `GET /v1/info` returns the default `file_datasource_max_upload_count` (10), and reflects a monkeypatched higher `config.FILE_DATASOURCE_MAX_UPLOAD_COUNT`.

## Out of scope

- Per-client / per-datasource override of the upload limit (global config setting only, per product decision).
- Any change to ZIP extraction limits (`_ZIP_MAX_FILE_COUNT`, `_ZIP_MAX_UNCOMPRESSED_BYTES`).
- Any change to the indexing/embedding batch sizes (`DEFAULT_PROCESSING_BATCH_SIZE`, `embeddings_max_docs_count`) — already handled at that layer.
- Enforcing an upper ceiling on the new config value in code.
- Frontend consumption of the advertised limit (replacing the dropzone's own hardcoded max) — lives in the separate frontend repository.
