# Technical Research

**Task**: datasource file upload limit batch
**Generated**: 2026-08-03T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

EPMCDME-13212 - Allow more than 10 files to be uploaded to File based Datasource. Datasource File Type allows only 10 files, without any reason for this. Both creating and editing are limited to 10 files. It is not possible to add more unless the user removes some. Datasource should allow unlimited files. PO clarification: The default 10-file limit must remain in configuration, but it should be configurable so clients can set higher values when needed. File upload/processing must send files in batches so performance is not impacted if a user selects a large number of files, for example hundreds of files. Implied requirements: (1) default file datasource upload limit remains 10 files in configuration, (2) the limit is configurable so clients can raise it, (3) users can create and edit File based Datasources with more than 10 files when configuration allows it, (4) file upload/processing sends files in batches, (5) uploading large volumes (hundreds of files) does not cause unacceptable performance impact.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/models/index.py:1718` — `IndexKnowledgeBaseFileRequest`: create-path request model; `MAX_FILE_COUNT: ClassVar[int] = 10` hardcoded, enforced by `@field_validator("files") validate_files_count`
- `src/codemie/rest_api/models/index.py:1527` — `UpdateKnowledgeBaseFileRequest`: update-path request model; same `MAX_FILE_COUNT: ClassVar[int] = 10` hardcoded with identical validator; no shared base class between the two models
- `src/codemie/service/datasource/file_datasource_service.py` — `FileDatasourceService`: `upload_and_prepare_files` iterates files sequentially with `_process_upload_file`; no batched upload concurrency
- `src/codemie/service/datasource/zip_utils.py:29` — `_ZIP_MAX_FILE_COUNT = 1000`: separate hardcoded ceiling for files inside a ZIP archive (unaffected by this task)
- `src/codemie/use_cases/datasource/update_file_datasource_use_case.py` — `UpdateFileDatasourceUseCase`: facade orchestrating the full update path end-to-end
- `src/codemie/datasource/file/file_datasource_processor.py` — `FileDatasourceProcessor`: inherits `DEFAULT_PROCESSING_BATCH_SIZE = 50` from base but does not override `_processing_batch_size` via YAML; no `loader_batch_size` configured
- `src/codemie/datasource/file/file_datasource_update_processor.py` — `FileDatasourceUpdateProcessor`: overrides `process()` — replicates much of the base pipeline logic verbatim; batching changes in the base must be verified here too
- `src/codemie/datasource/base_datasource_processor.py:69` — `DEFAULT_PROCESSING_BATCH_SIZE = 50`: base default for embedding/ES write loop; all other datasource loaders override via YAML `loader_batch_size`; `FileDatasourceProcessor` is the only loader that does not
- `src/codemie/datasource/datasources_config.py:79` — `FileConfig`: fields are `chunk_size`, `chunk_overlap`, `enable_multiprocessing`, `max_subprocesses`, `processing_timeout`; no `loader_batch_size` field
- `config/datasources/datasources-config.yaml` — `file_loader` section: `chunk_size: 1500`, `chunk_overlap: 100`; no `loader_batch_size` entry unlike every other loader
- `src/codemie/configs/config.py` — app-level Pydantic settings with `FILES_STORAGE_MAX_UPLOAD_SIZE`, `FILE_DATASOURCE_MULTIPROCESSING_MAX_WORKERS`, `DATASOURCE_CONCURRENCY_LIMIT_ENABLED`, `MAX_CONCURRENT_DATASOURCE_INDEXING`

### Architecture and Layers Affected

- **REST API — Models**: `IndexKnowledgeBaseFileRequest` and `UpdateKnowledgeBaseFileRequest` in `rest_api/models/index.py` — the 10-file `ClassVar` limit lives here, enforced at Pydantic validation time before the router even sees the payload
- **REST API — Router**: `rest_api/routers/index.py` — wires models to use cases; likely carries the FastAPI `BackgroundTasks` injection and the concurrency gate call
- **Use Case layer**: `UpdateFileDatasourceUseCase` (update path); a corresponding create use case presumably exists; both must accept a now-configurable limit
- **Service layer**: `FileDatasourceService` — sequential `upload_and_prepare_files` loop; this is where upload batching would be introduced
- **Processor layer**: `FileDatasourceProcessor` and `FileDatasourceUpdateProcessor` — ES-write batch size comes from here; adding `loader_batch_size` to `FileConfig` and overriding `_processing_batch_size` targets this layer
- **Config layer**: `src/codemie/configs/config.py` (env-var config) and `config/datasources/datasources-config.yaml` + `datasources_config.py` (YAML config); both need new fields

### Integration Points

- **Elasticsearch**: `FileDatasourceProcessor` and `FileDatasourceUpdateProcessor` write document chunks in batches to ES; batch size flows from `_processing_batch_size()`
- **File storage**: `FileDatasourceService._process_upload_file` uploads each file to the backing object store; currently sequential — `FILES_STORAGE_MAX_UPLOAD_SIZE` (env-configurable) caps per-file size but nothing batches the upload loop
- **`DatasourceConcurrencyManager`**: semaphore-based gate controlling concurrent datasource indexing jobs; unaffected by this task but constrains how many large uploads run in parallel
- **`ThreadPoolExecutor` precedent in code executor**: `FileUploadService` (code executor domain) already uses `ThreadPoolExecutor(max_workers=3)` for parallel uploads — the natural model to follow when introducing concurrent upload batching in `FileDatasourceService`

### Patterns and Conventions

- **Pydantic `ClassVar` for model-level constants**: both `MAX_FILE_COUNT` instances use this pattern; the change should introduce a new app-config env var (following `FILES_STORAGE_MAX_UPLOAD_SIZE`) and inject it into the validators
- **Template Method in `BaseDatasourceProcessor`**: `process()` defines the pipeline; `_processing_batch_size()`, `_init_loader()`, `_get_splitter()` are the extension points; `FileDatasourceProcessor` must override `_processing_batch_size()` to read from `FileConfig.loader_batch_size`
- **YAML-loaded per-loader config**: every other loader (`JiraConfig`, `ConfluenceConfig`, etc.) declares `loader_batch_size` in `datasources-config.yaml` and wires it through `_processing_batch_size()`; `FileConfig` must follow the same pattern
- **Facade Use Case**: use cases accept request models and delegate to services/processors; the injected config value (new `FILE_DATASOURCE_MAX_UPLOAD_COUNT` env var) should be passed through or read directly from `config` rather than hardcoded in the use case
- **Background tasks + concurrency gate**: large uploads are kicked off as background tasks; the concurrency gate is transparent to this change

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/api/rest-api-patterns.md` — FastAPI router patterns; relevant for adding/modifying validators and response models in the affected router
- `.ai-run/guides/development/configuration-patterns.md` — env-var and YAML config conventions; directly applicable when adding `FILE_DATASOURCE_MAX_UPLOAD_COUNT` to `config.py` and `loader_batch_size` to `FileConfig`
- `.ai-run/guides/data/database-patterns.md` — SQLModel and session patterns; less directly relevant but covers persistence layer conventions
- `.ai-run/guides/architecture/layered-architecture.md` — confirms the REST API → Use Case → Service → Processor → Loader → ES layer taxonomy used above
- `.ai-run/guides/development/performance-patterns.md` — async and batching patterns; directly relevant for the upload batching requirement

### Architectural Decisions

- `_ZIP_MAX_FILE_COUNT = 1000` in `zip_utils.py` is a separate and independently maintained limit for ZIP contents; must not be conflated with the new configurable upload count limit
- `DEFAULT_PROCESSING_BATCH_SIZE = 50` in the base processor is a processing (ES-write) batch size, not an upload batch size; the task introduces both a new configurable file count ceiling and a new upload-phase batch size — these are distinct concerns at different layers

### Derived Conventions

- New env vars for file datasource follow the `FILE_DATASOURCE_*` prefix already used by `FILE_DATASOURCE_MULTIPROCESSING_MAX_WORKERS`; the new limit should be `FILE_DATASOURCE_MAX_UPLOAD_COUNT` (or similar) with default `10`
- New YAML fields in `FileConfig` follow the snake_case pattern of existing fields; `loader_batch_size: <int>` matches every other loader
- Validators in Pydantic request models reference `settings` (injected or imported) rather than hardcoding values; see the `FILES_STORAGE_MAX_UPLOAD_SIZE` validator as the precedent for reading from app config

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/models/test_index_models.py` — validates `IndexKnowledgeBaseFileRequest.MAX_FILE_COUNT`; contains an 11-file rejection test at line 82; does **not** cover `UpdateKnowledgeBaseFileRequest` count validator
- `tests/codemie/service/datasource/test_file_datasource_service.py` — service-level logic: `parse_uploaded_files`, `upload_and_prepare_files`, ZIP edge cases
- `tests/codemie/use_cases/datasource/test_update_file_datasource_use_case.py` — `UpdateFileDatasourceUseCase` orchestration
- `tests/codemie/datasource/file/test_file_datasource_update_processor.py` — `FileDatasourceUpdateProcessor`: selective ES deletion, progress tracking
- `tests/codemie/datasource/file/test_file_datasource_processor.py` — `FileDatasourceProcessor`: init and `started_message` (narrow coverage)

### Testing Framework and Patterns

- **pytest** (Pydantic v2 validators tested via model instantiation / `pytest.raises(ValidationError)`)
- Fixtures and factories used across service and use-case tests
- Model tests instantiate request objects directly to trigger validators — the pattern is clear and easy to extend

### Coverage Gaps

- `UpdateKnowledgeBaseFileRequest.validate_files_count` has **no unit test** — any change to the update-path limit is undetected by the current test suite
- Upload batching logic in `FileDatasourceService.upload_and_prepare_files` has no concurrency or batch-boundary tests
- `FileDatasourceProcessor._processing_batch_size()` override (once added) has no tests asserting it reads from `FileConfig`
- Configurable `FILE_DATASOURCE_MAX_UPLOAD_COUNT` env-var wiring into both request model validators has no tests
- `FileDatasourceUpdateProcessor.process()` override behavior under large file sets has no dedicated test

---

## 5. Configuration and Environment

### Environment Variables

- `FILES_STORAGE_MAX_UPLOAD_SIZE` — per-file upload size cap (default 100 MB); env-configurable via `config.py`; **not** the file count limit
- `FILE_DATASOURCE_MULTIPROCESSING_MAX_WORKERS` — multiprocessing worker count for file processing; env-configurable
- `DATASOURCE_CONCURRENCY_LIMIT_ENABLED` — toggles the semaphore-based concurrency gate
- `MAX_CONCURRENT_DATASOURCE_INDEXING` — max simultaneous datasource indexing jobs
- **Missing**: no env var currently controls the 10-file count limit; `FILE_DATASOURCE_MAX_UPLOAD_COUNT` (default `10`) needs to be added to `config.py`

### Configuration Files

- `config/datasources/datasources-config.yaml` (`file_loader` section) — governs chunk size, overlap, multiprocessing; **missing `loader_batch_size`**; all other loaders carry this field
- `src/codemie/datasource/datasources_config.py` (`FileConfig` class) — the Pydantic model for the YAML `file_loader` block; must add `loader_batch_size: int` field with a sensible default
- `src/codemie/configs/config.py` — app-level Pydantic settings; must add `FILE_DATASOURCE_MAX_UPLOAD_COUNT: int = 10`

### Feature Flags and Deployment Concerns

- No existing feature flags for file datasource upload behavior
- Clients who need more than 10 files must set `FILE_DATASOURCE_MAX_UPLOAD_COUNT` via environment variable — this is a new operational knob that must be documented in deployment guides
- Batch size for upload (`loader_batch_size` in YAML) is a per-deployment tuning parameter; changing it affects memory pressure and storage throughput — default should be conservative (e.g. 10–20 files per batch)

---

## 6. Risk Indicators

- **Dual hardcoded `ClassVar[int] = 10` in unrelated model classes** — `IndexKnowledgeBaseFileRequest` (line 1718) and `UpdateKnowledgeBaseFileRequest` (line 1527) have no shared base; if only one is updated the create/edit limits diverge silently
- **No env var backs `MAX_FILE_COUNT`** — the constant is not wired to any config; the validator must be refactored to read from `settings` (following `FILES_STORAGE_MAX_UPLOAD_SIZE` precedent) before the limit becomes configurable
- **`FileConfig` missing `loader_batch_size`** — it is the only loader config class without this field; adding it is safe but requires YAML, Pydantic model, and processor changes in coordination
- **`upload_and_prepare_files` is sequential** — at hundreds of files, uploading one-by-one to object storage will be the primary latency bottleneck; no async/batch upload pattern exists yet in this module (though the code executor provides a `ThreadPoolExecutor` precedent)
- **`FileDatasourceUpdateProcessor` overrides `process()` verbatim** — any batch-handling logic added to `BaseDatasourceProcessor._process()` must be explicitly verified (and likely replicated) in the update processor override
- **Test gap on `UpdateKnowledgeBaseFileRequest`** — the update model's count validator is completely untested; this is also a risk for the current codebase independent of this task
- **Test gap on processor batch size** — `test_file_datasource_processor.py` currently covers only init and `started_message`; adding `loader_batch_size` without tests leaves the new behavior unverified
- **Upload batch size as a new operational knob** — clients can misconfigure `loader_batch_size` (too large → memory pressure on ingestion pod; too small → excessive round-trips); needs a documented safe range

---

## 7. Summary for Complexity Assessment

This task touches five distinct layers: REST API request model validation (two separate `ClassVar` constants in `index.py`), app-level and YAML-level configuration (a new env var in `config.py` and a new `loader_batch_size` field in `FileConfig` + `datasources-config.yaml`), the service layer (`FileDatasourceService.upload_and_prepare_files` sequential loop), and the processor layer (`FileDatasourceProcessor._processing_batch_size` override). The change surface is moderate — approximately 6–9 files across these layers — but the layers are well-separated and the existing patterns (YAML loader config, `ClassVar` validators reading from `settings`, `ThreadPoolExecutor` batch upload) are already established for analogous concerns elsewhere in the codebase. No new third-party dependencies are required.

The task follows established patterns rather than introducing novel ones, which reduces design risk. The two highest-risk areas are: (1) the dual `MAX_FILE_COUNT` constants with no shared base — one easy to miss, and (2) `FileDatasourceUpdateProcessor` overriding `process()` verbatim, meaning batching changes in `BaseDatasourceProcessor` will not automatically apply to the update path. Both are straightforward to handle once identified, but require deliberate attention. The `ThreadPoolExecutor` pattern in the code executor provides a clear precedent for concurrent batch uploads in `FileDatasourceService`.

Test coverage posture is mixed: the create-path validator has a test, the update-path validator does not; the service and use case have reasonable coverage; the processor has thin coverage. The task will require new tests for the update-path validator, configurable limit wiring, upload batching boundaries, and the processor batch size override. These gaps are workable but must be addressed to meet the project's TDD policy — the coverage delta for this task is non-trivial.
