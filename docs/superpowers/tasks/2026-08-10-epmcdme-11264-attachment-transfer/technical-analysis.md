# Technical Research

**Task**: jira integration attachments image-processing bug-creation support-request
**Generated**: 2026-08-10T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Automate Transfer and Processing of Attachments When Creating Bugs from Support Requests. When creating a bug from a support request Jira ticket, the system should automatically: (1) Copy all file/image attachments from the source support request ticket to the newly created bug ticket, (2) For image attachments, extract key information (e.g. text via OCR or relevant data) and append it into the bug description or as a comment. Acceptance criteria: all attachments are copied with no fidelity loss, image-based info is visible in the bug report, if no attachments exist bug is created normally, no manual file downloads/uploads needed, activity is logged/auditable.

---

## 2. Codebase Findings

### Existing Implementations

**Core Jira tool (primary change target):**
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie_tools/core/project_management/jira/tools.py` — `GenericJiraIssueTool(CodeMieTool, FileToolMixin)`. Handles all Jira REST API calls. Contains partially complete primitives for this feature:
  - `_extract_image_attachments(response)` — parses `fields.attachment[]` from a GET-issue response; filters to image MIME types only (`image/png`, `image/jpeg`, `image/jpg`, `image/gif`, `image/webp`); caps at 5 MB per image and 5 images total.
  - `_download_attachment_as_base64(content_url)` — authenticated binary download via `jira.request(method="GET", path=url, advanced_mode=True, absolute=True)`; returns base64-encoded string.
  - `_download_image_artifacts(image_attachments)` — batch wrapper that builds `{filename, data, mime_type}` artifact dicts used by the multimodal pipeline.
  - `_handle_file_attachments(relative_url, params, files_content)` — uploads files to a target Jira issue via `jira.add_attachment_object(issue_key, io.BytesIO(content))`. Currently only reads from `config.input_files` (user-provided files in the current agent session), not from another Jira ticket.
  - `execute(method, relative_url, params)` — main dispatch entry point. Routes to `_handle_file_attachments` when the URL contains `/attachments` and `_resolve_files()` returns files.

- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie_tools/core/project_management/jira/models.py` — `JiraConfig(CodeMieToolConfig, FileConfigMixin)`: fields `url`, `username`, `token`, `cloud`. Inherits `input_files: Optional[List[FileObject]]` from `FileConfigMixin`.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie_tools/core/project_management/jira/utils.py` — `parse_payload_params`, `validate_jira_creds`, `process_search_response`, `get_issue_field`, `process_issue`.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie_tools/core/project_management/jira/tools_vars.py` — `GENERIC_JIRA_TOOL` metadata; `get_jira_tool_description()` — the agent-facing docstring that must be updated to describe attachment-copy behavior.

**Image processing (directly reusable for OCR):**
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie_tools/utils/image_processor.py` — `ImageProcessor` class. `extract_text_from_image_bytes(image_bytes: bytes, custom_prompt: Optional[str]) -> str`. Uses OpenCV (`cv2`) to decode bytes and sends the image as a base64 `image_url` content block to a `BaseChatModel` (LLM vision). Default prompt preserves paragraph and bullet structure. This is the correct OCR pathway — Tesseract is not installed in the container (see Section 5).

**Reference pattern — Azure DevOps attachment handling:**
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie_tools/azure_devops/attachment_content_mixin.py` — `AttachmentContentMixin._process_content(filename, bytes)`. Multi-format dispatcher: image → `ImageProcessor`, PDF → `PdfProcessor`, DOCX → `DocxProcessor`, PPTX → `PptxProcessor`, XLSX → `XlsxProcessor`, plain text passthrough. This is the canonical pattern to mirror in the Jira implementation.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie_tools/azure_devops/attachment_mixin.py` — `AzureDevOpsAttachmentMixin._upload_attachment(filename, bytes)` and `_download_attachment(url, filename)`. Cross-ticket binary transfer reference using `httpx`.

**LangGraph agent multimodal hook:**
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/agents/image_artifact_hook.py` — `image_artifact_pre_model_hook(state)`. Injects image artifacts from `ToolMessage.artifact` into the agent's LLM context as `HumanMessage` `ImageContentBlock` objects. This hook is already active for `GenericJiraIssueTool` via `response_format = "content_and_artifact"`.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/agents/supervisor/pre_model_hooks.py` — registers the hook into the agent graph.

**File object abstractions:**
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie_tools/base/file_object.py` — `FileObject`: `bytes_content() -> Optional[bytes]`, `to_image_base64() -> str`, `base64_content() -> str`, `mime_type`, `name`.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie_tools/base/file_tool_mixin.py` — `FileToolMixin._resolve_files()` returns `Dict[str, Tuple[bytes, str]]` (filename → `(bytes, mime_type)`).
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie_tools/base/codemie_tool.py` — `CodeMieTool(BaseTool)`: `_run()`, `_limit_output_content()`, `_post_process_output_content()`.

**Skill wrapper:**
- `/Users/yevhen_slyva/codemie-dev/codemie/.claude/skills/codemie-jira-assistant/SKILL.md` — CLI skill wrapper for the prebuilt Jira assistant (wraps assistant UUID `289d2751-afd9-4c77-a272-90df7cd71702`). Confirms the Jira assistant exists as a deployed entity but does not contain attachment-copy logic.

**Prompt templates:**
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/templates/agents/business_analyst.py` — `BA_SYSTEM_PROMPT` step 7 defines bug format: Description, Steps To Reproduce, Actual Result, Expected Result. The appended OCR text must be compatible with this structure.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/templates/agents/core_prompts.py` — `CODEMIE_JIRA_PROMPT` includes Jira API context for task/bug creation.

### Architecture and Layers Affected

| Layer | Component | Change Required |
|---|---|---|
| **Agent-Tool** | `GenericJiraIssueTool` in `codemie_tools/core/project_management/jira/tools.py` | Add `_download_all_attachments()`, `_copy_attachments_to_issue()`, and `_extract_and_append_image_text()` methods. Extend `execute()` dispatch or expose as a dedicated helper called by the agent orchestration. |
| **Agent-Tool config** | `JiraConfig` in `models.py` | No structural change needed; `input_files` field already supports file passing. |
| **Utility** | `ImageProcessor` in `codemie_tools/utils/image_processor.py` | Reuse as-is. Requires `chat_model` to be wired into `GenericJiraIssueTool`. |
| **Tool metadata / prompt** | `tools_vars.py` → `get_jira_tool_description()` | Update agent-facing docstring to describe the new attachment-copy and OCR flow. |
| **Agent graph hook** | `image_artifact_hook.py` | No change needed — existing hook already injects downloaded images into LLM context. |
| **Config** | `config.py` + `tools.py` module constants | Optionally promote `MAX_IMAGE_SIZE_BYTES` and `MAX_IMAGES_PER_RESPONSE` to config env vars for operator tuning. |

### Integration Points

**Internal:**
- `codemie_tools/core/project_management/toolkit.py` — registers `GenericJiraIssueTool` into `ProjectManagementToolkit`. No change needed here for the tool to be available.
- `codemie/rest_api/models/prebuilt_assistants.py` — references `GENERIC_JIRA_TOOL` in at least 8 prebuilt assistant definitions. No changes required; the new capability is additive.
- `codemie/service/settings/settings.py` — `SettingsService.JIRA_FIELDS` maps credential fields for storage/retrieval. No change needed.
- `codemie/repository/skill_event_repository.py` — `SkillEventRepository` persists lifecycle events to Postgres with fields: `id`, `command`, `status`, `skill_id`, `user_id`, `agent`, `source`, `created_at`. This is the project's established pattern for durable audit records. A new `SkillEvent` record per attachment-transfer operation would satisfy the "logged/auditable" acceptance criterion at a durable level.

**External:**
- `atlassian-python-api` (`atlassian.Jira`) — authenticated download via `jira.request(method="GET", path=content_url, advanced_mode=True, absolute=True)`; authenticated upload via `jira.add_attachment_object(issue_key, io.BytesIO(bytes_content))`. Both API surfaces are already established in the codebase.
- Jira REST API v2 — `GET /rest/api/2/issue/{key}?fields=attachment` (fetch source attachments); `POST /rest/api/2/issue/{key}/attachments` (upload to bug); `POST /rest/api/2/issue/{key}/comment` (append OCR text as comment — simpler than editing description post-creation).

### Patterns and Conventions

1. **Tool class extension**: All tools extend `CodeMieTool(BaseTool)`. File-handling tools also inherit `FileToolMixin`. The `execute()` method is the single abstract entry point. New attachment-copy logic must live inside `GenericJiraIssueTool` or a new dedicated tool following the same pattern.

2. **Atlassian client usage**: All HTTP to Jira routes through `self.jira` (an `atlassian.Jira` instance). Use `jira.request(advanced_mode=True)` for authenticated binary operations. Do not introduce raw `httpx` or `requests` calls for Jira traffic.

3. **Multimodal artifact pipeline**: Tools return `(text_content, List[{filename, data, mime_type}])` via `response_format = "content_and_artifact"`. The pre-model hook promotes artifact dicts into `HumanMessage` image content blocks. This pipeline already handles the "show image in agent context" requirement; OCR text-extraction-to-description is an additional write-back step.

4. **`AttachmentContentMixin` reference pattern**: The Azure DevOps `AttachmentContentMixin._process_content(filename, bytes)` is the established multi-format dispatcher. The Jira feature should follow this pattern: a new `JiraAttachmentContentMixin` or a helper function using the same `ImageProcessor`, `PdfProcessor`, etc. routing logic.

5. **LLM vision OCR (not Tesseract)**: `ImageProcessor.extract_text_from_image_bytes(bytes)` uses OpenCV + LLM `image_url` content block. Use this exclusively — Tesseract is not available in the container.

6. **Error handling**: `ToolException` (from `langchain_core.tools`) is the standard error type. Per-attachment failures should be logged with `logger.warning(...)` and skipped rather than aborting the full transfer. Whole-operation failures raise `ToolException`.

7. **Logging**: Use `logger.info(...)` for success (filename + size). Use `logger.warning(...)` for per-item failures. Use structured positional args (`"Downloaded: %s (%d bytes)", filename, size`) matching the pattern at `tools.py:374`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `/Users/yevhen_slyva/codemie-dev/codemie/.ai-run/guides/integration/jira-integration.md` — Sets the adapter boundary: "Jira APIs must go through datasource processors or `codemie_tools` project-management adapters, never directly from routers." Does not document attachment workflows, cross-ticket operations, or OCR.
- `/Users/yevhen_slyva/codemie-dev/codemie/.ai-run/guides/development/logging-patterns.md` — Three rules: (a) include operation, IDs, status, and sanitized details in messages; (b) give each failure class a distinct label; (c) match severity to operational impact.
- `/Users/yevhen_slyva/codemie-dev/codemie/.ai-run/guides/architecture/layered-architecture.md` — Governs layer boundaries: HTTP in `rest_api/routers/`, orchestration in `service/`, persistence in `repository/`. Tool adapters (`codemie_tools/`) sit outside and are invoked by agents.
- `/Users/yevhen_slyva/codemie-dev/codemie/.ai-run/guides/integration/xray-integration.md` — Only covers QA adapter boundary; no attachment guidance.

No guide covers cross-ticket attachment copy, image OCR appending, or the bug-from-support-request pattern.

### Architectural Decisions

- **Adapter boundary** (jira-integration.md): all Jira API calls must be inside `codemie_tools` project-management adapters. The new feature belongs in `GenericJiraIssueTool` or a closely composed companion inside the same package.
- **LLM vision over Tesseract**: implicit in the codebase — `ImageProcessor` uses OpenCV + LLM; `pytesseract` is declared in `pyproject.toml` but Tesseract system binary is not installed in the Dockerfile. The LLM vision path is the only available OCR mechanism.
- **Artifact pipeline for image visibility**: existing `response_format = "content_and_artifact"` + `image_artifact_pre_model_hook` makes images visible in the agent's conversation without appending OCR text to the ticket. Appending OCR text to the bug description or as a comment is an additive write-back that does not conflict with this pipeline.

### Derived Conventions

- Files are represented as `FileObject` instances with `bytes_content()` and `mime_type`. New attachment-copy code should build `FileObject` instances (or work directly with `bytes` + MIME type pairs) rather than inventing new file abstractions.
- Module-level logger: tool package uses `logging.getLogger(__name__)`; datasource uses `from codemie.configs import logger`. New code inside `codemie_tools/core/project_management/jira/` should use `logging.getLogger(__name__)`.
- The tool docstring in `tools_vars.py` is the agent's primary instruction source. Any new callable behavior must be described in `get_jira_tool_description()` to be usable by agents.

---

## 4. Testing Landscape

### Existing Coverage

| Test File | What It Covers |
|---|---|
| `tests/codemie_tools/core/project_management/jira/test_image_attachments.py` | `_is_single_issue_request`, `_extract_image_attachments` (MIME filter, size cap, images cap, no-attachment, JSON error), `_download_attachment_as_base64` (success, HTTP 403, exception), `_download_image_artifacts` (all-success, partial failure, all-fail), `_post_process_output_content`, `_limit_output_content` |
| `tests/codemie_tools/core/project_management/jira/test_tools.py` | `execute()` GET, POST, healthcheck, search response (353 lines) |
| `tests/codemie_tools/core/project_management/jira/test_tools_additional.py` | Config validation, API error, execute with invalid method (154 lines) |
| `tests/codemie_tools/core/project_management/jira/test_utils.py` | `parse_payload_params`, `validate_jira_creds`, `get_issue_field`, `process_issue`, `process_search_response` (452+ lines) |
| `tests/codemie_tools/core/project_management/jira/test_jira_advanced_jql.py` | JQL query construction edge cases |
| `tests/codemie_tools/utils/test_image_processor.py` | `ImageProcessor.encode_image_base64`, `extract_text_from_image_bytes` (success, decode failure, no chat model, custom prompt) |
| `tests/codemie/datasource/jira/test_jira_datasource_processor.py` | `JiraDatasourceProcessor` indexing pipeline (not tool-layer) |

### Testing Framework and Patterns

- **Framework**: pytest 8.3.x with `pytest-asyncio`, `pytest-mock`, `pytest-env`, `pytest-httpx`
- **Global fixture**: `mock_database_engine` (session-scoped, auto-applied) in `tests/conftest.py` — blocks real DB connections in all tests
- **Jira fixture pattern** (`tests/codemie_tools/core/project_management/jira/conftest.py`):
  1. Create `JiraConfig(url=..., token=..., username=..., cloud=...)`
  2. Patch `codemie_tools.core.project_management.jira.tools.Jira` with `MagicMock`
  3. Patch `validate_jira_creds`
  4. Inject `tool.jira = MagicMock()`
  5. Assert on `tool.jira.request.call_args` or `tool.jira.add_attachment_object.call_args`
- Available fixtures: `jira_config`, `jira_cloud_config`, `mock_jira`, `sample_issue`, `sample_search_response`
- **ImageProcessor fixture** (`tests/codemie_tools/utils/conftest.py`): `mock_chat_model`, `image_processor`, `pdf_processor`
- **Mocking pattern**: `unittest.mock.MagicMock`, `patch`, `Mock` — no custom fakes or factories

### Coverage Gaps

All of the following are zero-coverage areas that the feature will introduce or require:

1. **`_handle_file_attachments` (upload path)** — the existing upload method (`jira.add_attachment_object`) has no test. Needs: single file success, multiple files success, `add_attachment_object` raises exception → `ToolException`.

2. **`_is_attachment_operation` and `_extract_issue_key` helpers** — zero coverage. Needed: URL-based key extraction, params-based key extraction, missing key raises `ToolException`.

3. **`execute()` attachment dispatch branch** — the branch in `execute()` that routes to `_handle_file_attachments` when URL contains `/attachments` is untested.

4. **New: generic attachment metadata fetch (all MIME types)** — `_extract_image_attachments` only surfaces image types. A new `_extract_all_attachments()` method is needed and will require tests: includes PDFs, ZIPs, text files; handles empty list; handles malformed JSON.

5. **New: cross-ticket attachment copy** — no source or test for: download all attachments from source ticket, re-upload to bug ticket, partial failure handling (some copy, some fail), empty attachment list is a no-op.

6. **New: image OCR → description/comment append** — no source or test for: `ImageProcessor` wired into `GenericJiraIssueTool`, OCR text injected into create-issue payload or as a `POST /comment`, custom prompt for bug-relevant information.

7. **New: end-to-end bug-from-support-request flow** — no source or test for the full orchestration: GET source ticket → POST new bug → copy attachments → OCR images → append text → optional issue link.

8. **`FileToolMixin._resolve_files` / `_filter_requested_files` in Jira context** — zero coverage. These helpers gate the upload path but are never exercised via Jira tool tests.

---

## 5. Configuration and Environment

### Environment Variables

There are no `JIRA_*` environment variables. Jira credentials are stored per-user/project in the database as `Settings` ORM records with `credential_type = CredentialTypes.JIRA`. Fields: `url`, `token`, `username`, `cloud` (boolean). Accessed via `SettingsService`.

The only config.py flag adjacent to the feature area:

| Flag | Default | Purpose |
|---|---|---|
| `AMNA_AIRN_PRECREATE_WORKFLOWS` | `False` | When `True`, auto-seeds placeholder Jira credentials for the AMNA demo project at startup |
| `IMAGE_INDEXING_MAX_SIZE_BYTES` | `10 MB` | Max image size for datasource indexing (not the Jira tool attachment flow) |

### Configuration Files

- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie/configs/config.py` — `Config(BaseSettings)` central settings. No Jira-specific fields beyond the flags above.
- `/Users/yevhen_slyva/codemie-dev/codemie/src/codemie_tools/core/project_management/jira/models.py` — `JiraConfig` credential model (per-user, DB-persisted).
- `/Users/yevhen_slyva/codemie-dev/codemie/.env.example` — No Jira entries. Covers Azure OpenAI, GCP/VertexAI, Azure Speech, GitLab, and auth flags only.
- `pyproject.toml` — Key installed packages: `atlassian-python-api ^4.0.6`, `Pillow ^12.3.0`, `opencv-python-headless ^4.11.0.86`, `pytesseract ^0.3.13` (declared but Tesseract binary absent from container — see below), `pdfplumber ^0.11.9`, `httpx ^0.28.1`, `aiohttp ~3.14.3`.

Hard-coded constants in `tools.py` (candidates for promotion to config env vars for the new feature):

| Constant | Value | Relevance |
|---|---|---|
| `MAX_IMAGE_SIZE_BYTES` | `5 MB` | Per-image filter; fidelity-preserving copy may need to bypass or increase this |
| `MAX_IMAGES_PER_RESPONSE` | `5` | Cap on images in a single response; may need a separate cap for copy operations |
| `IMAGE_MIME_TYPES` | `{png, jpeg, jpg, gif, webp}` | Image filter for download; copy pipeline must handle all MIME types |

### Feature Flags and Deployment Concerns

- No feature flags gate Jira attachment or OCR behavior.
- **Critical Dockerfile gap**: `tesseract-ocr` is NOT installed in the production Docker image at `/Users/yevhen_slyva/codemie-dev/codemie/Dockerfile`. `pytesseract` in `pyproject.toml` is a non-functional dependency for container-deployed code. All image text extraction must use `ImageProcessor` (LLM vision path via `cv2` + multimodal LLM). No Dockerfile changes are required for this feature.
- Installed system packages relevant to the feature: `libimage-exiftool-perl` (EXIF metadata, builder stage), `libpango1.0-dev` (SVG rendering), `pandoc` + `texlive` (PDF export). OpenCV headless is installed via Python wheel — no system package needed.

---

## 6. Risk Indicators

- **No cross-ticket attachment copy exists anywhere in the codebase.** `_handle_file_attachments` uploads from `config.input_files` (session files); `_download_attachment_as_base64` downloads for LLM vision context only. The new feature's core flow (download from source ticket → upload to new bug ticket) is entirely new code with no existing scaffolding beyond the primitives.

- **`_handle_file_attachments` has zero test coverage.** The upload method is untested. Adding tests for this method is a prerequisite before the new cross-ticket copy can be built on it reliably.

- **`pytesseract` is declared in `pyproject.toml` but Tesseract is not installed in the Dockerfile.** Any code path that calls `pytesseract` directly will fail at runtime in the container. OCR must use `ImageProcessor.extract_text_from_image_bytes()` (LLM vision). This is the only safe path.

- **Image attachment limits (`MAX_IMAGE_SIZE_BYTES = 5 MB`, `MAX_IMAGES_PER_RESPONSE = 5`) are hard-coded module constants.** The "no fidelity loss" acceptance criterion conflicts with these caps for copy operations. A separate set of limits (or no cap) for the copy pipeline will need to be decided and exposed as config values.

- **`_extract_image_attachments` filters to image MIME types only.** The "copy ALL attachments" requirement means a new `_extract_all_attachments()` method is required that does not apply MIME type filtering.

- **No `AttachmentContentMixin` equivalent exists for Jira.** The Azure DevOps domain has a fully developed multi-format attachment processor. The Jira domain only has the image download/display pipeline. A Jira-specific mixin (or reuse of `AttachmentContentMixin` directly) must be introduced.

- **`ImageProcessor` requires a `chat_model` instance.** `GenericJiraIssueTool` currently does not hold a `chat_model` field. Wiring `ImageProcessor` into the Jira tool requires either adding a `chat_model: BaseChatModel` to `JiraConfig` or injecting it at tool-construction time — a structural change to the tool's config model.

- **No audit/durable logging for inter-ticket operations.** The "logged/auditable" acceptance criterion is not met by transient `logger.info` calls. The project's established pattern for durable audit records is `SkillEventRepository` (Postgres). No `SkillEvent` model or repository method covers Jira attachment operations today. Implementing durable audit logging adds a repository and service layer touch beyond the tool layer.

- **No formal ADR or guide documents the attachment-copy or OCR-to-description pattern.** Convention must be derived from the Azure DevOps `AttachmentContentMixin` reference. This introduces implementation risk if the Azure DevOps pattern is not fully portable (e.g., `httpx` vs `atlassian` client differences).

- **The `execute()` attachment dispatch branch is untested.** The routing logic that decides when to invoke `_handle_file_attachments` is untested, meaning the entire upload path is fragile.

---

## 7. Summary for Complexity Assessment

This feature touches the **Agent-Tool layer** (`GenericJiraIssueTool` in `codemie_tools/core/project_management/jira/tools.py`) as its primary change site, with secondary touches to the **Utility layer** (`ImageProcessor` wiring), **Configuration layer** (promoting hard-coded constants and adding `chat_model` to tool config), and optionally the **Repository/Service layer** (durable audit via `SkillEventRepository`). The estimated file change surface is 3–5 files modified (`tools.py`, `models.py`, `tools_vars.py`, optionally `config.py`) plus 2–3 new test files. If durable audit is in scope, `codemie/repository/skill_event_repository.py` and a service wrapper are additional touches, expanding the surface to 6–8 files.

The feature does not introduce novel architectural patterns — all necessary primitives exist: download via `jira.request(absolute=True)`, upload via `jira.add_attachment_object`, OCR via `ImageProcessor`, and multi-format dispatch via `AttachmentContentMixin`. However, the feature introduces a **new data flow not present in the codebase**: downloading binary attachment content from one Jira ticket and re-uploading it to another within a single tool execution. This cross-ticket transfer pattern has no existing Jira-domain implementation to follow, making it a moderate novelty that requires careful composition of existing primitives and new error-handling logic for partial failures.

Test coverage posture is **mixed with critical gaps**: the image download/display pipeline is well tested (`test_image_attachments.py`), but the upload path (`_handle_file_attachments`) has zero coverage and the entire cross-ticket copy flow requires new tests from scratch. The "auditable" acceptance criterion adds further complexity if `SkillEventRepository` writes are required — that path has no existing Jira-domain tests. Key risk factors for scoring: (1) zero upload-path test coverage to build on, (2) `chat_model` injection required to activate OCR, (3) image size caps conflict with fidelity requirement, (4) durable audit logging scope is ambiguous and could double the change surface.
