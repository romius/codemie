# Spec: Automate Attachment Transfer and OCR When Creating Bugs from Support Requests

**Ticket**: EPMCDME-11264
**Branch**: feature/EPMCDME-11264-attachment-transfer
**Date**: 2026-08-10

---

## Problem

When an agent creates a new issue based on an existing ticket (bug from support request, task from epic, subtask from story, or any similar derived relationship), attachments (files and images) stay on the source ticket. Diagnostics and evidence are not carried over, requiring manual downloads and re-uploads. Image content is never extracted into the new issue.

---

## Solution Overview

Extend `GenericJiraIssueTool` with a `JiraAttachmentMixin` that, when the create-issue payload explicitly opts in, can:
1. Copy all attachments from a source ticket to the new issue — opt-in via `copy_attachments: true` (default `false`). A `source_issue_key` identifying the source ticket must also be present; on its own it triggers nothing.
2. Additionally run LLM-vision OCR on image attachments and post the extracted text as a single comment on the new issue — opt-in via `ocr_images: true` (default `false`). OCR requires `chat_model` to be set on `GenericJiraIssueTool`; if absent, the OCR request is a no-op and files are still copied when `copy_attachments=true`.

Both flags and `source_issue_key` are hidden transfer parameters — they are stripped from the request before it reaches Jira, never added to the new issue's content, and do not constitute a reference to the source ticket. Whether to mention the source ticket in the description or fields is a separate decision controlled by the agent. The tool never copies attachments or runs vision-model calls on its own; the caller (agent prompt) must ask for each explicitly.

The feature is fully backward-compatible: create-issue calls without `copy_attachments=true` behave exactly as before.

---

## Architecture

### New file: `codemie_tools/core/project_management/jira/attachment_mixin.py`

Follows the `AttachmentContentMixin` pattern from `codemie_tools/azure_devops/attachment_content_mixin.py`. Owns all cross-ticket copy and OCR logic. `GenericJiraIssueTool` inherits this mixin.

**Public method:**
```python
def copy_attachments_from_issue(
    self, source_key: str, target_key: str, run_ocr: bool = False
) -> list[dict]:
    """
    Returns: [{"filename": str, "size": int, "status": "copied"|"failed"|"skipped", "ocr_text": str|None}]
    OCR and the extracted-content comment run only when run_ocr=True.
    """
```

**Internal methods:**
- `_fetch_all_attachments(issue_key: str) -> list[dict]` — GET `/rest/api/2/issue/{key}?fields=attachment`; returns all MIME types (no filtering).
- `_copy_single_attachment(attachment_meta: dict, target_key: str) -> tuple[bytes | None, str]` — authenticated binary download via `jira.request(method="GET", path=content_url, advanced_mode=True, absolute=True)`; upload via `jira.add_attachment_object(target_key, io.BytesIO(content))`. Respects config caps. Validates the attachment URL's origin against `self.jira.url` by comparing parsed `(scheme, netloc)` — a plain `startswith()` check is unsafe because a base URL without a trailing slash (e.g. `https://jira.example.com`) also matches a lookalike host (`https://jira.example.com.attacker.com/...`), which would let a hostile plugin or future cross-instance source ticket steer the authenticated download at any host.
- `_run_ocr(filename: str, content_bytes: bytes, mime_type: str) -> str | None` — delegates to `ImageProcessor.extract_text_from_image_bytes()` when `self.chat_model` is set and MIME type is an image. Returns `None` otherwise.
- `_append_ocr_comment(issue_key: str, ocr_results: list[dict]) -> None` — posts a single formatted comment listing all OCR extracts by filename. Only called when at least one OCR result is present. Both filenames and OCR text are run through `_JIRA_MARKUP_ESCAPE` (escapes `*`, `{`, `[`, `|`, `~`) before being embedded in the `{noformat}` wrapper: Jira's `{noformat}` block is not recursive, so a literal `{noformat}` inside the extracted text would prematurely close the block and let the rest render as live wiki markup — including `[~user]` mentions that fire notifications on the target ticket.

### Changed files

| File | Change |
|---|---|
| `jira/tools.py` | Inherit `JiraAttachmentMixin`; extend `execute()` to strip `source_issue_key`, `copy_attachments`, and `ocr_images` from the payload before dispatch, and gate the post-create `copy_attachments_from_issue` call on `source_issue_key` **and** `copy_attachments=True`, forwarding `run_ocr=ocr_images`; add `chat_model: Optional[BaseChatModel] = None` field; wire `ImageProcessor` |
| `jira/tools_vars.py` | Update `get_jira_tool_description()` to document the opt-in attachment-transfer flags, generalized derived-issue relationships, hidden-param semantics, and the requirement that both `copy_attachments` and `ocr_images` be added only when the user explicitly asks |
| `configs/config.py` | Add `JIRA_COPY_MAX_ATTACHMENT_BYTES: int = -1` and `JIRA_COPY_MAX_ATTACHMENTS: int = -1` |

---

## Data Flow

```
Agent: POST /rest/api/2/issue
  body: {fields: {...issue fields...},
         source_issue_key: "SUP-123",     # optional, hidden
         copy_attachments: true,          # optional, hidden, default false
         ocr_images: true}                # optional, hidden, default false
  ↓
execute() strips source_issue_key, copy_attachments, ocr_images from the payload
execute() creates bug → response contains new_issue_key (e.g. "BUG-456")
  ↓
source_issue_key present AND copy_attachments == true?
  NO  → return tool response as-is (no change to existing flow)
  YES →
    _fetch_all_attachments("SUP-123")
      ↓
      for each attachment:
        check JIRA_COPY_MAX_ATTACHMENTS cap (-1 = unlimited)
        check JIRA_COPY_MAX_ATTACHMENT_BYTES cap (-1 = unlimited)
        reject when urlparse(content).(scheme, netloc) != urlparse(self.jira.url).(scheme, netloc)
        download binary → upload to BUG-456
        if ocr_images AND image MIME AND chat_model → run OCR → collect text
        logger.info("Copied: %s (%d bytes) to %s", filename, size, target_key)
      ↓
      if ocr_images AND any OCR text → POST single comment to BUG-456
        (filenames and ocr_text run through _JIRA_MARKUP_ESCAPE first):
        "## Extracted image content\n\n**{safe_filename}**\n{noformat}\n{safe_ocr_text}\n{noformat}\n..."
      ↓
      append copy summary to tool response:
        "Attachment transfer: {N}/{total} copied."
        followed by " {M} image(s) OCR'd." only when ocr_images was true.
```

### Config caps (-1 = unlimited, per project convention matching NATS_MAX_RECONNECT_ATTEMPTS)

| Variable | Default | Behavior when positive |
|---|---|---|
| `JIRA_COPY_MAX_ATTACHMENT_BYTES` | `-1` | Skip files exceeding N bytes; log warning |
| `JIRA_COPY_MAX_ATTACHMENTS` | `-1` | Stop after N files; log warning for skipped |

---

## OCR

- **Engine**: `ImageProcessor.extract_text_from_image_bytes(bytes, custom_prompt)` — LLM vision via OpenCV + multimodal LLM. This is the only supported OCR path. `pytesseract` is not usable (Tesseract binary absent from Docker image).
- **Activation**: Two conditions must both hold — the caller passes `ocr_images: true` in the create-issue payload, and `chat_model` is set on `GenericJiraIssueTool` (`Optional[BaseChatModel] = None`). When either condition is missing, OCR is silently skipped; files are still copied when `copy_attachments=true`. In production, `toolkit_service.py` auto-injects `chat_model` at tool construction time (lines 768–785) when the tool class declares the field and an LLM model is configured; no extra setup is required for OCR to be available when the caller opts in.
- **Target**: A single Jira comment on the new issue, posted after all attachments are processed, only when `ocr_images=true`. One comment with all image extracts labeled by filename. If `ocr_images=false` or there are zero OCR results, no comment is posted.
- **Custom prompt**: Uses `ImageProcessor` default ("extract key text and information").

---

## Error Handling

Per-attachment failures are tolerated (warn + skip). Whole-operation failures do not abort bug creation.

| Failure | Behavior |
|---|---|
| Source ticket GET fails | `logger.warning(...)` → copy step skipped; bug creation result returned normally |
| Download fails (HTTP error) | `logger.warning("Failed to download %s: %s", filename, error)` → skip file, continue |
| Upload fails | `logger.warning("Failed to upload %s to %s: %s", filename, target_key, error)` → skip file, continue |
| OCR fails | `logger.warning("OCR failed for %s: %s", filename, error)` → file still copied; OCR text omitted |
| `chat_model` is `None` | OCR silently skipped; copy still runs |
| Source has no attachments | No-op; no copy step, no comment |
| All attachments fail | Summary "0/N copied" appended to response; no `ToolException` (bug was created) |
| Size cap exceeded | `logger.warning("Skipping %s: %d bytes exceeds JIRA_COPY_MAX_ATTACHMENT_BYTES=%d", ...)` |
| Count cap reached | `logger.warning("Attachment cap reached (%d). Skipping %d remaining.", ...)` |

Logging uses `logging.getLogger(__name__)` inside `codemie_tools/` per package convention.

---

## Security

- **Attachment URL origin check.** `_copy_single_attachment` refuses to download from any host that does not match `self.jira.url`. Origin is compared as parsed `(scheme, netloc)` via `urllib.parse.urlparse`, not by `str.startswith()`. A naive `startswith()` check is unsafe when the configured base has no trailing slash: `"https://jira.example.com"` also matches `https://jira.example.com.attacker.com/...`, and a hostile self-hosted plugin (Automation for Jira, custom field processor) or a future cross-instance source ticket could steer the authenticated download at an attacker-controlled host.
- **OCR text escaping.** OCR extracts are attacker-influenced content (they come from an image the source ticket's reporter uploaded). Before the extracted text is embedded in the `{noformat}` block on the target ticket, both the filename and the text are run through `_JIRA_MARKUP_ESCAPE` (escapes `*`, `{`, `[`, `|`, `~`). Without this, an image containing the literal string `{noformat}` would close the wrapper early and let the following text render as live Jira wiki markup — a `[~admin] Urgent request` embedded in the OCR text would then fire a notification to the target ticket's admin account.
- **Explicit opt-in on side effects.** The tool never copies attachments or invokes vision-model calls on its own; both require an explicit `copy_attachments=true` / `ocr_images=true` flag in the caller's request. This keeps vision-model spend and target-ticket mutations under the caller's control and prevents the tool from performing hidden work.

---

## Testing

### New: `tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py`

Uses existing fixtures: `jira_config`, `mock_jira`, `patch validate_jira_creds`.

| Test | Coverage |
|---|---|
| `test_fetch_all_attachments_returns_all_mime_types` | image + PDF + ZIP all returned (no MIME filter) |
| `test_fetch_all_attachments_empty` | No attachments field → `[]` |
| `test_copy_single_attachment_success` | Download → upload → logged |
| `test_copy_single_attachment_download_fails` | `jira.request` raises → warning, status `"failed"` |
| `test_copy_single_attachment_upload_fails` | `add_attachment_object` raises → warning, status `"failed"` |
| `test_copy_single_attachment_size_cap_skips` | Exceeds `JIRA_COPY_MAX_ATTACHMENT_BYTES` → skipped with warning |
| `test_copy_single_attachment_rejects_lookalike_host` | `content_url` on a lookalike host (`https://jira.example.com.attacker.com/...`) → `"failed"`, no HTTP request issued |
| `test_copy_count_cap_stops_early` | `JIRA_COPY_MAX_ATTACHMENTS=2`, 4 attachments → 2 copied, warning |
| `test_run_ocr_with_chat_model` | Image MIME → `ImageProcessor` called → text returned |
| `test_run_ocr_without_chat_model` | `chat_model=None` → `None` returned, no OCR call |
| `test_run_ocr_non_image_mime` | PDF MIME → `None` returned |
| `test_run_ocr_processor_fails` | `ImageProcessor` raises → warning, `None` returned |
| `test_append_ocr_comment_with_results` | OCR text → `jira.add_comment` called with formatted body |
| `test_append_ocr_comment_no_results` | No OCR → no comment call |
| `test_append_ocr_comment_escapes_noformat_and_mentions_in_ocr_text` | OCR text containing `{noformat}` and `[~admin]` → escaped so neither closes the wrapper nor triggers a mention |
| `test_copy_attachments_from_issue_full_flow` | GET source → copy 2 files → OCR 1 image (with `run_ocr=True`) → comment posted |
| `test_copy_attachments_ocr_off_by_default` | GET source → copy 1 image with `run_ocr=False` → `ImageProcessor` not constructed, no comment POST |
| `test_copy_attachments_from_issue_no_attachments` | Source empty → `[]`, no upload, no comment |
| `test_copy_attachments_source_fetch_fails` | Source GET fails → warns, `[]` |

### Extensions to `tests/codemie_tools/core/project_management/jira/test_tools.py`

- `test_execute_post_issue_with_source_key_triggers_copy` — POST `/issue` with `source_issue_key` **and** `copy_attachments=true` → `copy_attachments_from_issue` called with `run_ocr=False`; response omits the OCR summary
- `test_execute_post_issue_with_ocr_flag_runs_ocr` — POST `/issue` with `source_issue_key`, `copy_attachments=true`, `ocr_images=true` → `copy_attachments_from_issue` called with `run_ocr=True`; response includes the OCR summary
- `test_execute_post_issue_source_key_without_copy_flag_no_copy` — POST `/issue` with `source_issue_key` but without `copy_attachments=true` → no copy call
- `test_execute_post_issue_without_source_key_no_copy` — POST `/issue` without `source_issue_key` → no copy call
- `test_handle_file_attachments_single_file` — upload path, currently zero coverage
- `test_handle_file_attachments_multiple_files` — multiple uploads
- `test_handle_file_attachments_upload_raises` — `add_attachment_object` raises → `ToolException`

---

## Acceptance Criteria Coverage

| Criterion | How met |
|---|---|
| All attachments copied to new bug when the agent asks | `_fetch_all_attachments` (no MIME filter) + `_copy_single_attachment` for each, gated on `source_issue_key` and `copy_attachments=true` |
| No fidelity loss | Default caps are `-1` (unlimited); configurable if needed |
| Image OCR text visible in bug when the agent asks | Single comment posted with extracted text labeled by filename when `ocr_images=true`; content escaped so no wiki markup or mentions can fire from image text |
| No side effects unless explicitly requested | Missing `copy_attachments=true` → no copy; missing `ocr_images=true` → no OCR and no comment |
| No attachments → issue created normally | Any of `source_issue_key` / `copy_attachments=true` absent, or source has no attachments → no-op |
| No manual download/upload needed | Fully automated within `execute()` once the caller opts in |
| Logged/auditable | `logger.info` per file (name, size, status) + summary in tool response |

---

## Verification

### Baseline (existing behaviour, before the change)

Ask the Jira BA agent to create a bug from a real support ticket that has at least one image attachment:

> "Create a bug from support ticket SUP-123"

Expected today: bug is created, **no attachments** appear on it, no OCR comment is posted. This confirms the baseline.

Run existing unit tests to confirm the upload path has no coverage:
```bash
poetry run pytest tests/codemie_tools/core/project_management/jira/ -v
```

### After implementation

**Automated tests:**
```bash
poetry run pytest tests/codemie_tools/core/project_management/jira/test_attachment_mixin.py -v
poetry run pytest tests/codemie_tools/core/project_management/jira/test_tools.py -v
```

**Manual end-to-end:**

1. Find or create a Jira support ticket with at least one image (PNG/JPG) and one non-image file (e.g. PDF).
2. Ask the BA agent explicitly: `"Create a bug from support ticket SUP-123 and copy its attachments; extract text from any images."` The agent must translate that intent into a create-issue call that carries both `copy_attachments=true` and `ocr_images=true` alongside `source_issue_key`.
3. Open the newly created bug in Jira and check:

| Check | Expected |
|---|---|
| Attachments tab | Same files as the source ticket, identical content |
| Comments | One comment starting with `## Extracted image content` listing OCR text per image (only if `chat_model` is configured **and** the request included `ocr_images=true`) |
| Agent response | Contains `"Attachment transfer: N/N copied"` and, when `ocr_images=true`, ` M image(s) OCR'd` |
| Source ticket unchanged | Original attachments still present on SUP-123 |

**Copy-only mode (no OCR):**

Repeat the flow with a request phrased so the agent sets `copy_attachments=true` but omits `ocr_images` (e.g. `"copy attachments only, do not extract text"`). Attachments must appear on the new bug, no OCR comment is posted, and the response summary omits the `image(s) OCR'd` clause.

**No-opt-in baseline:**

Ask the agent to create a bug from a support ticket in the plain form (`"Create a bug from support ticket SUP-123"`). The bug should be created normally with no copy step and no comment — identical to the pre-feature behaviour — because the agent must not add `copy_attachments` speculatively.

**No-attachment baseline:**

With `copy_attachments=true` on a source ticket that has zero attachments, the bug is created normally, no copy step runs and no comment is posted.

**`chat_model` absent (copy-only mode):**

If `chat_model` is `None`, even a request with `ocr_images=true` copies attachments but posts no OCR comment.

---

## Out of Scope

- Multi-format content extraction (PDF, DOCX, XLSX) — OCR is image-only in this story; `AttachmentContentMixin` multi-format dispatcher is not wired for non-image types
- Durable DB audit via `SkillEventRepository` — structured `logger.info` satisfies the audit requirement
- Linking source and bug tickets — not in acceptance criteria
- UI changes
