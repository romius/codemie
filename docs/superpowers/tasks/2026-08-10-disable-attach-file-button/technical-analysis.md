# Technical Research

**Task**: chat file attachment customer config feature flag
**Generated**: 2026-08-10T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-7070: Ability to disable the Attach File button in the chat-bot. Add a backend configuration option that allows administrators to enable or disable file attachment functionality in the chat-bot. When disabled, users cannot attach files through the chat-bot. The user wants backend-only changes (no frontend/UI changes). This is about adding a config flag to customer/project configuration that controls whether file uploads are permitted in the chat-bot.

---

## 2. Codebase Findings

### Existing Implementations

**Customer config model and feature flag infrastructure:**
- `src/codemie/configs/customer_config.py` — `CustomerConfig(BaseModel)`: loads `config/customer/customer-config.yaml` at startup; exposes `is_feature_enabled(feature_key: str) -> bool` which checks for a `features:<feature_key>` component. Returns `False` when the key is absent (despite the docstring stating "defaults to True" — the actual code at line 267-268 delegates to `is_component_enabled` which defaults to `False` at line 253). Singleton `customer_config` is imported directly in all consumers.
- `config/customer/customer-config.yaml` — Canonical YAML list of all `features:*` boolean toggles. Currently contains 18 feature entries. No `features:chatFileAttachment` or equivalent entry exists today.

**File upload endpoints:**
- `src/codemie/rest_api/routers/files.py` — `POST /v1/files/` (`write_file`) and `POST /v1/files/bulk` (`write_files_bulk`): the only two endpoints that accept binary file data. Both require authentication via `Depends(authenticate)`, check `config.FILES_STORAGE_MAX_UPLOAD_SIZE`, and return `file_url` values for later reference in chat requests. No feature flag gate currently exists on either endpoint.

**Chat request model:**
- `src/codemie/core/models.py` — `AssistantChatRequest`: contains `file_names: Optional[list[str]]` (line 564), which holds the URLs of previously uploaded files. `MAX_FILE_COUNT = 20` enforced via `FileNamesCountValidatorMixin`.

**Chat assistant request handlers:**
- `src/codemie/rest_api/routers/assistant.py` — `_validate_assistant_supports_files_and_raise` (line 2349): the only existing per-assistant file validation helper. Currently gates only Bedrock assistants from receiving files. Called from `_ask_virtual_assistant` (line 2196) and `_ask_assistant` (line 2270). This is the primary extension point for a customer-level gate.
- `src/codemie/rest_api/handlers/assistant_handlers.py` — `_sync_uploaded_files_to_workspace` (line 219): syncs `request.file_names` to `AgentWorkspaceService` if non-empty.

**Downstream file consumers:**
- `src/codemie/agents/assistant_agent.py` — passes `request.file_names` to every agent invocation path.
- `src/codemie/agents/langgraph_agent.py` — reads `request.file_names`, base64-decodes images via `ImageService.filter_base64_images`.
- `src/codemie/service/conversation_service.py` — persists `file_names` in conversation history.

**Existing feature flag enforcement pattern (canonical reference):**
- `src/codemie/rest_api/routers/assistant_project_mapping.py` (lines 38-49): `_require_teams_bot_feature()` guard function that calls `customer_config.is_feature_enabled("teamsBotIntegration")` and raises `ExtendedHTTPException(code=HTTP_403_FORBIDDEN)` when disabled. This is the exact pattern to replicate.
- `src/codemie/service/tools/toolkit_service.py` (line 242): two-condition gate — `request.enable_web_search is True and customer_config.is_feature_enabled("webSearch")` — shows combining a per-request flag with a customer config flag.

**Config served to frontend:**
- `src/codemie/rest_api/routers/customer_config.py` — `GET /v1/config` returns `customer_config.get_enabled_components()`. Only components with `settings.enabled: true` are included. A disabled `features:chatFileAttachment` component will be absent from the response, which is the signal the frontend uses to hide UI controls.

### Architecture and Layers Affected

| Layer | Component | Change Required |
|---|---|---|
| Configuration (YAML) | `config/customer/customer-config.yaml` | Add `features:chatFileAttachment` entry, `enabled: true` |
| Config Model | `src/codemie/configs/customer_config.py` | No change — existing `is_feature_enabled` is sufficient |
| API / Router | `src/codemie/rest_api/routers/files.py` | Add feature flag guard to `write_file` and `write_files_bulk` |
| API / Router | `src/codemie/rest_api/routers/assistant.py` | Extend `_validate_assistant_supports_files_and_raise` OR add a parallel customer-level check at the same call sites |

The task does not require changes to the Service, Repository, or Database-Persistence layers.

### Integration Points

**Internal:**
- `customer_config` singleton imported directly (not injected via FastAPI `Depends`) into `files.py` after the change.
- `ExtendedHTTPException` from `codemie.core.exceptions` used for the 403 response.
- `authenticate` dependency already on both upload endpoints — no auth changes needed.

**External:**
- No external service changes. Storage backend (S3, Azure Blob, GCP, filesystem) is not involved in flag enforcement.

### Patterns and Conventions

- **Feature flag check**: `customer_config.is_feature_enabled("chatFileAttachment")` — camelCase key, no `features:` prefix in the call.
- **YAML component ID**: `features:chatFileAttachment` — the full ID used in the YAML.
- **Default**: `is_feature_enabled` returns `False` when the component is absent (the implementation at line 267-268 delegates to `is_component_enabled` which defaults to `False`). Therefore, the YAML entry must be present with `enabled: true` for the feature to work in all existing deployments. Shipping without a YAML entry would break all existing deployments.
- **Guard function pattern**: Define a small private function (e.g., `_require_chat_file_attachment_feature()`) in `files.py` that raises `ExtendedHTTPException(code=HTTP_403_FORBIDDEN)`. Call it at the top of the two upload handler functions.
- **No DynamicConfigService needed**: This is a static admin config (restart required to change), not a runtime-mutable toggle.
- **No `CONFIG_IDS` registry entry needed**: `CONFIG_IDS` is only for runtime-computed flags derived from system state (enterprise edition, user management, env vars). YAML-backed flags do not appear in `CONFIG_IDS`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/development/configuration-patterns.md` — Directly relevant. Establishes the two-tier distinction: YAML-backed `features:*` for restart-required admin toggles vs. `DynamicConfigService` for runtime-mutable flags. Confirms that the `customer_config.is_feature_enabled` pattern is the canonical mechanism for this task.
- `.ai-run/guides/architecture/layered-architecture.md` — Confirms HTTP concerns belong in routers under `src/codemie/rest_api/routers/`. Enforcement of the feature gate at the router layer (before services) is the correct placement.
- `.ai-run/guides/api/endpoint-conventions.md` — Confirms typed Pydantic request models own validation; router handlers raise shared exceptions for feature gates.
- `.ai-run/guides/api/rest-api-patterns.md` — Confirms use of `ExtendedHTTPException` for gated feature errors.
- `.ai-run/guides/development/error-handling.md` — States that feature-gate errors use `ExtendedHTTPException` with a message naming the specific config component to enable.

### Architectural Decisions

- **Decision 1 — Feature flags use `features:<key>` in customer-config.yaml.** All 18 existing behavioral toggles follow this convention. Evidence: `customer_config.py:267`, `customer-config.yaml` throughout.
- **Decision 2 — Two enforcement points exist and both must be guarded.** File upload (`POST /v1/files/`) and chat request processing (`AssistantChatRequest.file_names`) are separate request paths. A client can upload a file then reference it by URL in a subsequent chat call. If only the upload endpoint is guarded, previously-uploaded or externally-hosted files could still be used. If only the chat endpoint is guarded, files can still be stored to the backend.
- **Decision 3 — `GET /v1/config` omits disabled components.** The frontend relies on `features:chatFileAttachment` being present and enabled in the config response to show the Attach button. Setting `enabled: false` removes it from the response automatically.
- **Decision 4 — No hot reload.** `customer_config` is a module-level singleton loaded at import time. Changing `enabled: true/false` in the YAML requires a pod restart to take effect.

### Derived Conventions

- All feature guard functions in routers follow the naming pattern `_require_<feature_name>_feature()`.
- The `ExtendedHTTPException` always uses `HTTP_403_FORBIDDEN` for feature-gate denials (as opposed to `HTTP_404` which is used for some analytics guards).
- The `features:chatFileAttachment` component will need a `description` field in the YAML (optional but consistent with other entries that have it).

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/routers/test_files.py` — Comprehensive tests for `POST /v1/files/` and `POST /v1/files/bulk`: success paths, file size limits, MIME handling, cache invalidation, authentication. Does not currently import or patch `customer_config`. No feature-flag guard tests exist.
- `tests/codemie/configs/test_customer_config.py` — Tests for `CustomerConfig`, `is_feature_enabled`, `is_component_enabled`, `get_enabled_components`, runtime features. Existing `test_is_feature_enabled` provides the pattern for new flag tests.
- `tests/codemie/rest_api/routers/test_customer_config_router.py` — Tests for `GET /v1/config` endpoint — verifies `get_enabled_components()` output serialization.
- `tests/unit/routers/test_analytics_enriched_user.py` — The most complete existing example of testing a feature-flag guard: uses `_patch_feature(enabled: bool)` helper that patches `customer_config` on the module under test and asserts HTTP 403.
- `tests/codemie/rest_api/routers/test_user_settings.py` — Tests `features:personalLiteLLMIntegrations` guard; another reference for the pattern.
- `tests/codemie/rest_api/routers/test_assistant.py` — Tests `_validate_assistant_supports_files_and_raise` for Bedrock assistants; relevant if the chat endpoint enforcement point is extended.

### Testing Framework and Patterns

- **Framework**: pytest (configured in `pytest.ini`; `testpaths = tests`, `pythonpath = src`, `addopts = --import-mode=importlib`).
- **Async tests**: `anyio`/`asyncio` marks on async test functions.
- **Auth override**: `app.dependency_overrides[authenticate]` pattern in all router tests.
- **Customer config patching** (from `test_analytics_enriched_user.py`):
  ```python
  def _patch_feature(enabled: bool):
      mock_cfg = MagicMock()
      mock_cfg.is_feature_enabled.return_value = enabled
      return patch("codemie.rest_api.routers.files.customer_config", mock_cfg)
  ```
- **Config patching in files.py** (from `test_files.py`): `mocker.patch("codemie.rest_api.routers.files.config", mock_config)` — same module target pattern applies for `customer_config`.

### Coverage Gaps

1. **`test_files.py` — no feature-flag guard tests**: New tests must verify `POST /v1/files/` and `POST /v1/files/bulk` return HTTP 403 when `customer_config.is_feature_enabled("chatFileAttachment")` returns `False`, and succeed when it returns `True`.
2. **Chat endpoint (`test_assistant.py`) — no customer-level file gate test**: If enforcement is also added to `_validate_assistant_supports_files_and_raise`, tests must cover the scenario where `file_names` is non-empty and the feature flag is off.
3. **`test_customer_config.py` — no test for new flag**: A test case asserting `is_feature_enabled("chatFileAttachment")` returns `True` when the YAML entry is `enabled: true` and `False` when `enabled: false` should be added.
4. **`test_get_enabled_components` count assertion**: Existing tests assert `len(get_enabled_components()) == N`. Adding a new YAML-backed `features:chatFileAttachment` entry with `enabled: true` will increase the count — the assertion in `test_customer_config.py` will need updating.

---

## 5. Configuration and Environment

### Environment Variables

No new environment variable is needed. This task uses the YAML-backed feature flag mechanism, not `Config(BaseSettings)`. Adjacent env vars (for storage backend only — not for the flag):

| Variable | Default | Role |
|---|---|---|
| `FILES_STORAGE_TYPE` | `"filesystem"` | Storage backend selector |
| `FILES_STORAGE_MAX_UPLOAD_SIZE` | 100 MB | Per-file size cap |
| `CUSTOMER_CONFIG_DIR` | `"./config/customer"` | Path to `customer-config.yaml` |

### Configuration Files

- `config/customer/customer-config.yaml` — The only file that needs a new entry. The new entry:
  ```yaml
  - id: "features:chatFileAttachment"
    settings:
      enabled: true
      name: "Chat File Attachment"
      description: "Allow users to attach files through the chat-bot"
  ```
  Default must be `enabled: true` to preserve backward-compatible behavior for all existing deployments. When administrators want to disable file attachment, they change `enabled: false` and restart the pod.

### Feature Flags and Deployment Concerns

- **No database migration required.** The flag is YAML-only.
- **Restart required for flag changes.** `CustomerConfig` is a module-level singleton instantiated at import time.
- **The YAML is typically a Kubernetes ConfigMap.** Changing the ConfigMap requires a rolling pod restart.
- **Two enforcement points must be consistent.** Guarding only the upload endpoints leaves the chat endpoint open to referencing previously-uploaded or externally-hosted files by URL. Guarding only the chat endpoint still permits file storage to occur. Both points should be enforced.
- **`is_feature_enabled` returns `False` when absent.** If the YAML entry is omitted entirely, `is_feature_enabled("chatFileAttachment")` returns `False`, silently blocking all file uploads. The YAML entry with `enabled: true` is mandatory for current-behavior preservation.

---

## 6. Risk Indicators

- **Docstring / behavior mismatch in `customer_config.py:256-268`**: The docstring of `is_feature_enabled` states "defaults to True (enabled)" but the actual implementation delegates to `is_component_enabled` which returns `False` when the component is absent. This means omitting the YAML entry will break existing file upload functionality — the YAML entry with `enabled: true` is non-optional.
- **Two enforcement points required for complete enforcement**: Guarding only `POST /v1/files/` allows bypass via chat requests referencing previously-uploaded or externally-hosted file URLs. Guarding only the chat endpoint allows orphan file storage. The task description must clarify whether both points need guarding or only the upload endpoint.
- **`test_customer_config.py` `len()` assertion fragility**: `test_get_enabled_components` asserts exact `len()` counts for `get_enabled_components()` output. Adding the new `features:chatFileAttachment` YAML entry with `enabled: true` will increment this count and break the test unless the assertion is updated simultaneously.
- **`_validate_assistant_supports_files_and_raise` currently Bedrock-only**: Extending this function to also check the customer-level flag would generalize file validation but risks unintended scope expansion. A narrower alternative is to call `customer_config.is_feature_enabled` directly at the `_ask_assistant` / `_ask_virtual_assistant` call sites.
- **No existing tests for `features:webSearch` or `features:teamsBotIntegration` guards in toolkit/mapping routers**: The project has inconsistent feature-flag test coverage. The `chatFileAttachment` guard should be tested, consistent with the more complete examples (`userEnrichmentEnabled`, `personalLiteLLMIntegrations`).
- **`customer_config` singleton is not a FastAPI dependency**: It is imported directly at the module level. Test patching must target the module-level binding (e.g., `patch("codemie.rest_api.routers.files.customer_config", ...)`) not the source module.
- **No existing `features:chatFileAttachment` component in the YAML**: Adding a net-new flag is a configuration deployment concern — the YAML change must be included in the same release as the code guard, or the guard must handle the "absent = disabled" default with an awareness that the default behavior would change upon deployment.

---

## 7. Summary for Complexity Assessment

This task adds a single boolean feature flag (`features:chatFileAttachment`) to the existing `CustomerConfig` YAML-backed feature toggle system, which is well-established and has a canonical pattern followed by at least six prior implementations. The architectural layers touched are: Configuration YAML (one entry), API/Router (`files.py` for both upload endpoints, and optionally `assistant.py` for chat request validation). No Service, Repository, or Database-Persistence layer changes are needed. Estimated file change surface: 2-4 files (`customer-config.yaml`, `files.py`, optionally `assistant.py`, and the corresponding test files).

The task follows an established pattern with no technical novelty. The pattern is: add a YAML entry, add a guard function in the router that calls `customer_config.is_feature_enabled("chatFileAttachment")`, raise `ExtendedHTTPException(HTTP_403_FORBIDDEN)` when disabled. The canonical reference implementation is `_require_teams_bot_feature()` in `assistant_project_mapping.py`. One non-obvious detail is that `is_feature_enabled` returns `False` when absent (despite a misleading docstring), making the YAML entry with `enabled: true` non-optional for backward compatibility.

Test coverage posture for the affected area is mixed: `test_files.py` has strong coverage of the upload endpoints' happy and error paths but no feature-flag guard tests, while `test_customer_config.py` has thorough coverage of the config model's `is_feature_enabled` method. The key risk factors are: (1) the two-enforcement-point question (upload vs. chat request) that the task description leaves ambiguous; (2) the `len()` assertion in existing config tests that will break if not updated; and (3) the docstring/behavior mismatch in `is_feature_enabled` that makes the `enabled: true` YAML default mandatory rather than optional.
