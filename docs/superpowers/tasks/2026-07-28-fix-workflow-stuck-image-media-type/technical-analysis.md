# Technical Research

**Task**: workflow image media-type error-handling langgraph node-execution
**Generated**: 2026-07-28T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Workflow execution becomes stuck in "in progress" state if model receives mismatched image media type (e.g., GIF sent as PNG); requires manual cancellation.

EPMCDME-11918 - Major priority bug.

Description:
- User has a workflow with a step (e.g., verify_bug) that sends images to an LLM model (e.g., Claude Haiku via Bedrock)
- The image is actually a GIF file but referenced as image/png in metadata or message payload
- Workflow enters "in progress" state at the relevant step and does not complete, requiring manual cancellation
- Step output error is: `Graph node execution failed. Error code: 400 - {'error': {'message': 'litellm.BadRequestError: BedrockException - {"message":"The model returned the following errors: messages.58.content.2.image.source.base64: The image was specified using the image/png media type, but the image appears to be a image/gif image"}...`

Expected behavior:
- Workflow should detect and handle image media type mismatches gracefully
- The step should fail quickly and mark the workflow as failed (not stuck "in progress" indefinitely)
- Clear error messaging should propagate to users about the cause (media type mismatch)
- No manual cancellation should be required when model returns media-type related 400 errors

Acceptance Criteria:
- When an image is sent to an LLM/model with a mismatched or invalid media type (e.g., actual GIF marked as PNG), the workflow step fails with a clear error (not stuck "in progress")
- The workflow as a whole transitions to 'Failed' state and user is notified of explicit error
- Model error responses due to payload specification issues (e.g., BedrockException for image types) are detected and surfaced without indefinite hang
- No scenario where workflow remains in "in progress" for unhandled model payload errors

---

## 2. Codebase Findings

### Existing Implementations

**Root cause — mime_type is trusted without validation at every layer:**

- `src/codemie_tools/base/file_object.py` — `FileObject.from_encoded_url()` deserializes `[mime_type, owner, name]` from a base64-encoded URL. The `mime_type` field is whatever was declared at upload time; no byte-sniffing or format detection is performed. `MimeType.is_image` only checks whether the declared string starts with `image/`.
- `src/codemie/service/file_service/image_service.py` — `ImageService.filter_base64_images()` takes `file_obj.mime_type` verbatim and returns `{'content': base64_content, 'mime_type': file_obj.mime_type}`. No validation against actual binary content.
- `src/codemie/datasource/loader/binary/image_loader.py` — PIL-based actual mime type detection (`img.get_format_mimetype()`) exists here, but only in the **document indexing pipeline**, not in the workflow execution path.

**Image injection into LLM messages (two distinct paths):**

- `src/codemie/agents/langgraph_agent.py` — `LangGraphAgent._get_inputs()` builds `{"type": "image", "mime_type": image_info['mime_type'], ...}` from `ImageService.filter_base64_images()`. The declared mime_type is passed to the model without verification.
- `src/codemie/agents/image_artifact_hook.py` — `image_artifact_pre_model_hook` builds `ImageContentBlock(mime_type=item["mime_type"])` from tool-returned image artifacts. Same pattern, same gap.
- `src/codemie/agents/supervisor/pre_model_hooks.py` — duplicate of the artifact hook pattern for the supervisor execution path.

**Error propagation chain (in order):**

- `src/codemie/agents/langgraph_agent.py` — `LangGraphAgent.invoke_task()` catches all exceptions (except `MCPAuthenticationRequiredException`) and returns `TaskResult.failed_result(user_error_message, original_exc=bad_request_error)`. `original_exc` is preserved.
- `src/codemie/workflows/nodes/agent_node.py` — `AgentNode.post_process_output()` detects `output.success == False` and raises `TaskException("Graph node execution failed. ...", original_exc=output.original_exc)`.
- `src/codemie/workflows/nodes/base_node.py` — `BaseNode.__call__()` generic `except Exception` handler (lines ~263-273) calls `finish_state(execution_state_id, output=str(e), status=WorkflowExecutionStatusEnum.FAILED)` then **re-raises `e`**. This marks the individual node state as FAILED but does **not** call `workflow_execution_service.fail()`.
- `src/codemie/workflows/workflow.py` — `WorkflowExecutor._execute_workflow_stream()` has an outer `except Exception` block (lines ~877-879) that calls `self._handle_task_exception(e, chunks_collector)`. `_handle_task_exception()` (lines ~1067-1097) is the canonical place that calls `workflow_execution_service.fail(error_class, error_message)`, which transitions the overall workflow to `FAILED`.

**Stuck-in-progress mechanism:**
The bug is caused by `WorkflowRetryPolicy.custom_retry_on` not suppressing retries for `BadRequestError`. Because a media-type mismatch (HTTP 400) is not on the suppression list (which only skips retries for 401/403/404 and specific credential errors), LangGraph retries the node. The retry loop may exhaust without ever propagating the exception cleanly out of `workflow.stream()`, leaving the overall workflow status never transitioned to FAILED by `_handle_task_exception`. There is also a secondary race: if LangGraph internally swallows the re-raised exception from `BaseNode.__call__()` (stopping the graph branch without re-raising to the `stream()` caller), `_handle_task_exception` is never entered at all.

**Existing pattern for graceful terminal state (reference for fix):**
`MCPAuthenticationRequiredException` handling in `BaseNode.__call__()` — this exception does NOT re-raise; instead it calls the relevant service method and returns `{NEXT_KEY: [END_NODE]}`, guaranteeing the workflow terminates cleanly regardless of whether the exception escapes `stream()`.

**Workflow execution service:**
- `src/codemie/service/workflow_execution/workflow_execution_service.py` — `fail(error_class, error_message)` is the single method that sets `overall_status = WorkflowExecutionStatusEnum.FAILED`. It is not called in `BaseNode.__call__()`'s generic exception block.

**Error classification:**
- `src/codemie/core/error_constants.py` — `ErrorCode.LITE_LLM_BAD_REQUEST_ERROR` exists. `LITE_LLM_EXC_TYPE_TO_ERROR_CODE` maps `"BadRequestError"` to it. However, `LITELLM_ERROR_KEYWORDS` for this code does not include `"BedrockException"`, `"image"`, or `"media_type"` — the keyword list is `["bad request", "invalid request", "unsupported params", "schema", "response_format", "json_schema"]`.
- `src/codemie/core/errors.py` — `LiteLLMErrorClassifier` is only active when `LLM_PROXY_MODE == "lite_llm"`. For direct `litellm.BadRequestError` exceptions (non-proxy path), the error falls through to `InternalErrorClassifier` and is classified as `PLATFORM_ERROR`.
- `src/codemie/core/workflow_models/workflow_models.py` — `WorkflowRetryPolicy.custom_retry_on` suppresses retries only for `InvalidCredentialsError`, `TruncatedOutputError`, and HTTP 401/403/404 responses.

### Architecture and Layers Affected

| Layer | Components |
|---|---|
| Workflow Orchestration | `WorkflowExecutor` (`workflow.py`), `WorkflowRetryPolicy` (`workflow_models.py`), `WorkflowExecutionService` |
| Workflow Node Execution | `BaseNode.__call__()` (`base_node.py`), `AgentNode.post_process_output()` (`agent_node.py`) |
| Agent / LLM Invocation | `LangGraphAgent.invoke_task()`, `LangGraphAgent._get_inputs()` (`langgraph_agent.py`) |
| Pre-model Hooks | `image_artifact_pre_model_hook` (`image_artifact_hook.py`), supervisor `_image_artifact_pre_model_hook` (`supervisor/pre_model_hooks.py`) |
| File / Image Service | `ImageService.filter_base64_images()` (`image_service.py`) |
| File Object Model | `FileObject.from_encoded_url()`, `MimeType.is_image` (`file_object.py`) |
| Error Classification | `ExceptionClassificationPipeline`, `LiteLLMErrorClassifier`, `AgentErrorClassifier` (`errors.py`), `error_constants.py` |

### Integration Points

- **litellm** (v1.84) — primary LLM router; raises `litellm.exceptions.BadRequestError` (subclass of `openai.BadRequestError`) for Bedrock 400 responses; carries `.status_code`, `.llm_provider`, `.model` attributes.
- **langchain-aws** (v1.4.3) — provides `ChatBedrock`; used by `LangGraphAgent`.
- **langchain-core** (v1.3.3) — provides `ImageContentBlock` for inline base64 image messages.
- **langgraph** (v1.1.6) — graph orchestration; node callables re-raise exceptions back to the `stream()` iterator. LangGraph's internal node dispatch may or may not re-raise node exceptions to the `stream()` caller depending on version and graph configuration.
- **Pillow** (v12.1.1/12.3.0) — image format detection (`get_format_mimetype()`) is available and used in the indexing pipeline; NOT currently used in the workflow execution path.
- **AWS Bedrock** — rejects message when declared `image/png` mime type does not match actual GIF binary content; returns HTTP 400.

### Patterns and Conventions

- **`MCPAuthenticationRequiredException` handling pattern**: When a specific terminal-condition exception is caught in `BaseNode.__call__()`, the correct pattern is to call the appropriate service method (e.g., `workflow_execution_service.fail()`), return `{NEXT_KEY: [END_NODE]}`, and NOT re-raise. This guarantees the workflow transitions to a terminal state regardless of whether LangGraph re-raises exceptions from `stream()`. This is the pattern that should be applied to `litellm.BadRequestError` for image media-type mismatches.
- **`TaskException.original_exc`**: The `BadRequestError` is preserved through the full chain (`invoke_task` → `TaskResult.original_exc` → `AgentNode.post_process_output` → `TaskException.original_exc`), so it is inspectable in `BaseNode.__call__()`.
- **`WorkflowRetryPolicy.custom_retry_on`**: Must suppress retries for `BadRequestError` (or for the subset of 400s that are unrecoverable payload errors) to avoid wasteful retry cycles before the terminal state is set.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/workflows/langgraph-workflows.md` — All workflow execution must go through `WorkflowExecutor`; node behavior belongs in `codemie.workflows.nodes`.
- `.ai-run/guides/development/error-handling.md` — Use typed exceptions; never bare `raise Exception()`; log internal detail and return a sanitized stable message to the client; one message per failure mode.
- `.ai-run/guides/architecture/service-layer-patterns.md` — Orchestration belongs in service methods; `workflow_service.py` is the named example.
- `.ai-run/guides/integration/llm-providers.md` — Provider-specific error handling must be gated (`is_litellm_enabled`) and pluggable.

### Architectural Decisions

- The canonical path for workflow FAILED state transition is `WorkflowExecutor._handle_task_exception()` → `workflow_execution_service.fail()`. This is the implicit design.
- `BaseNode.__call__()` is responsible for node-level state (`finish_state(FAILED)`) but not for workflow-level state. The separation between node-state and workflow-state is intentional.
- The `MCPAuthenticationRequiredException` handler in `BaseNode.__call__()` establishes that the pattern for guaranteeing workflow-level state transitions from within a node is to NOT re-raise and to return `{NEXT_KEY: [END_NODE]}`.

### Derived Conventions

- Image mime type is always sourced from the encoded URL metadata set at upload time. There is no established convention for re-detecting actual format at invocation time in the workflow path.
- Test fixtures for error-handling tests use `unittest.mock.patch` on `invoke_task` or `get_llm_by_credentials`; no real litellm/Bedrock calls occur in tests.
- `WorkflowRetryPolicy` fields are all `Optional` and default to `None`; absence of policy means LangGraph uses its default retry behavior.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/agents/test_image_artifact_hook.py` — covers `image_artifact_pre_model_hook` for `image/png` and `image/jpeg` artifacts; no test for mismatched or invalid mime types.
- `tests/codemie/workflows/test_agent_node_context.py` — TC_ANC_008 tests that `TaskResult(success=False)` causes `post_process_output` to raise `TaskException`.
- `tests/codemie/workflows/test_base_node_lifecycle.py` — tests `BaseNode.__call__()` exception path: `finish_state(FAILED)` is called, `on_node_fail` callbacks fire, exception propagates. Does NOT verify that `workflow_execution_service.fail()` is called.
- `tests/codemie/agents/test_assistant_agent/test_agent_error_flow.py` — covers `AIToolsAgent.stream()` error path, not `invoke_task`.
- `tests/codemie/agents/test_assistant_agent/test_invoke_method_error_handling.py` — covers `AIToolsAgent.invoke()`, not `invoke_task`.
- `tests/codemie/agents/test_assistant_agent/test_task_result.py` — unit tests for `TaskResult.failed_result()` in isolation.
- `tests/codemie/service/workflow_execution/test_workflow_execution_service.py` — covers `WorkflowExecutionService` but does NOT test the `fail()` method.

### Testing Framework and Patterns

- **Framework**: pytest with `pytest.ini` (`testpaths=tests`, `pythonpath=src`, `addopts=--import-mode=importlib`).
- **Mocking**: `unittest.mock` (`Mock`, `MagicMock`, `patch`, `patch.object`). LLM calls are mocked at `get_llm_by_credentials` or `invoke_task` — no real provider calls in tests.
- **Fixtures**: Inline per-module `@pytest.fixture` functions for `mock_workflow_execution_service`, `mock_thought_queue`, `mock_callbacks`, `mock_assistant`. Global `conftest.py` patches `PostgresClient.get_engine` (autouse, session-scoped).
- **Style**: AAA (Arrange-Act-Assert) with docstrings; TC-prefixed test IDs (`TC_ANC_XXX`) in workflow node tests.

### Coverage Gaps

1. **No test for `LangGraphAgent.invoke_task()` when `litellm.BadRequestError` is raised** — the specific media-type 400 scenario is untested.
2. **No test that `WorkflowExecutionService.fail()` is called when a node raises due to `BadRequestError`** — the full chain (invoke_task → TaskResult → post_process_output → TaskException → BaseNode → fail()) is not integration-tested.
3. **No test for `WorkflowRetryPolicy.custom_retry_on` with `BadRequestError`** — whether it suppresses retries or allows them is untested.
4. **No test for mime-type mismatch in `image_artifact_hook.py`** — GIF artifact declared as `image/png` is not tested.
5. **`WorkflowExecutionService.fail()` is untested in isolation**.
6. **Supervisor pre-model hook (`supervisor/pre_model_hooks.py`) has no dedicated test file** — it is a duplicate of the agent hook with the same gap.

---

## 5. Configuration and Environment

### Environment Variables

- `AWS_BEDROCK_MAX_RETRIES` (default: 5) — number of Bedrock call retries.
- `AWS_BEDROCK_READ_TIMEOUT` (default: 60000 ms) — 60-second read timeout per call.
- `AWS_BEDROCK_REGION` (default: empty; falls back to `AWS_DEFAULT_REGION`).
- `LLM_PROXY_MODE` (default: `"internal"`) — when `"lite_llm"`, routes through LiteLLM proxy with proxy-specific error classification; when `"internal"`, direct `litellm.BadRequestError` is thrown.
- `LLM_PROXY_ENABLED` (default: `False`) — master switch for LiteLLM proxy mode.
- `LITELLM_MSG_INVALID_REQUEST` — user-facing message string shown for `BadRequestError`/`InvalidRequestError`.
- `LITELLM_MSG_UNKNOWN_ERROR` — fallback user-facing message for unclassified LLM exceptions.
- `WORKFLOW_MAX_CONCURRENCY` (default: 5), `WORKFLOW_DEFAULT_CONCURRENCY` (default: 2).
- `AI_AGENT_RECURSION_LIMIT` (default: 150) — LangGraph recursion limit for the agent graph.
- `HIDE_AGENT_STREAMING_EXCEPTIONS` (default: `False`) — when `True`, suppresses streaming exceptions from surfacing.
- `IMAGE_INDEXING_MAX_SIZE_BYTES` (default: 10 MB) — max image size for indexing; governs indexing pipeline only.

### Configuration Files

- `src/codemie/configs/config.py` — central `Config` (pydantic-settings `BaseSettings`); governs all runtime behavior. The `LITELLM_MSG_INVALID_REQUEST` field controls the user-facing message for 400 errors.
- `config/llms/llm-aws-config.yaml` — Bedrock model catalog; all Claude models marked `multimodal: true`.
- `deploy-templates/values.yaml` — Helm chart values; AWS credentials injected via `aws-secrets` Kubernetes Secret; `AWS_DEFAULT_REGION=us-west-2` hardcoded.
- `.env` / `tests/.env.test` — local and test environment overrides.

### Feature Flags and Deployment Concerns

- `ENABLE_LANGGRAPH_AITOOLS_AGENT` (default: `True`) — controls whether the `AgentNode` LangGraph execution path is active; the affected code path requires this to be `True`.
- `LLM_PROXY_MODE` — affects which error classification path is active (`LiteLLMErrorClassifier` only active when proxy mode is `"lite_llm"`).
- No feature flag specifically controls workflow error handling for image payload errors.
- **No watchdog job exists for stuck-in-progress workflow executions** — the only existing watchdog is `STALE_INDEXING_WATCHDOG_ENABLED` (for datasource indexing). A workflow stuck `IN_PROGRESS` has no automated recovery path.
- AWS credentials in production come from Kubernetes Secrets (`aws-secrets`), not from `config.py`. The `AWS_DEFAULT_REGION=us-west-2` is hardcoded in the Helm values; `AWS_BEDROCK_REGION` is not set in the deploy template and defaults to empty.

---

## 6. Risk Indicators

- **No call to `workflow_execution_service.fail()` in `BaseNode.__call__()`'s generic `except Exception` block** — the node marks itself FAILED but relies entirely on the exception escaping `workflow.stream()` to trigger the workflow-level FAILED transition. If LangGraph swallows the exception internally, the workflow stays `IN_PROGRESS` indefinitely. File: `src/codemie/workflows/nodes/base_node.py` lines ~263-273.
- **`WorkflowRetryPolicy.custom_retry_on` does not suppress retries for `BadRequestError` (HTTP 400)** — a media-type mismatch will be retried until `max_attempts` is exhausted (default behavior), amplifying latency and wasted LLM budget before any terminal state is reached. File: `src/codemie/core/workflow_models/workflow_models.py`.
- **`ImageService.filter_base64_images()` trusts declared mime_type without byte-sniffing** — PIL-based format detection exists in `image_loader.py` (indexing pipeline) but is not wired into the workflow execution path. File: `src/codemie/service/file_service/image_service.py`.
- **Duplicate image artifact hook in supervisor path has no tests** — `src/codemie/agents/supervisor/pre_model_hooks.py` is untested and contains the same mime-type gap.
- **`LiteLLMErrorClassifier` is inactive for direct Bedrock calls** — when `LLM_PROXY_MODE="internal"` (the default), a `litellm.BadRequestError` is classified as `PLATFORM_ERROR` by `InternalErrorClassifier`, not as `LITE_LLM_BAD_REQUEST_ERROR`. The user-facing message `LITELLM_MSG_INVALID_REQUEST` may not be shown. File: `src/codemie/core/errors.py`.
- **`LITELLM_ERROR_KEYWORDS` for `LITE_LLM_BAD_REQUEST_ERROR` does not include `"BedrockException"`, `"image"`, or `"media_type"`** — even on the proxy path, the keyword classifier would not match a Bedrock image error message. File: `src/codemie/core/error_constants.py` lines ~202-207.
- **No watchdog for stuck `IN_PROGRESS` workflows** — if the fix is incomplete or a new path is missed, there is no automated recovery.
- **`WorkflowExecutionService.fail()` method has no test coverage** — changes to its behavior cannot be detected by existing tests.
- **Five coverage gaps identified** — no test exercises the full path from `BadRequestError` to `workflow_execution_service.fail()`.

---

## 7. Summary for Complexity Assessment

The bug spans four architectural layers: File/Image Service (mime-type sourcing), Agent/LLM Invocation (image block construction), Workflow Node Execution (error propagation), and Workflow Orchestration (FAILED state transition). The root cause has two interacting components: (1) mime_type is taken from upload-time metadata without validation against actual binary content, so the wrong type reaches the model; and (2) when Bedrock rejects the message with HTTP 400, the `WorkflowRetryPolicy` does not suppress retries for this error class, and — depending on LangGraph's internal exception handling in `stream()` — the exception may never reach `WorkflowExecutor._handle_task_exception()`, which is the only place that calls `workflow_execution_service.fail()`. The estimated file change surface is 4-7 files: `base_node.py` (primary fix: add `workflow_execution_service.fail()` call or apply `MCPAuthenticationRequiredException`-style terminal handling), `workflow_models.py` (suppress BadRequestError retries), `image_service.py` (optional: add PIL-based format detection), `error_constants.py` and `errors.py` (add `BadRequestError` to non-proxy classifier), and optionally `image_artifact_hook.py` / `supervisor/pre_model_hooks.py` (proactive mime-type validation in pre-model hooks).

Technical novelty is low. The fix follows an already-established pattern: the `MCPAuthenticationRequiredException` handler in `BaseNode.__call__()` demonstrates exactly how to handle a specific terminal-condition exception without re-raising, calling `workflow_execution_service` methods directly and returning `{NEXT_KEY: [END_NODE]}`. The same pattern applied to `litellm.BadRequestError` (detectable via `TaskException.original_exc` type inspection) is sufficient to guarantee the workflow transitions to FAILED state regardless of LangGraph's internal exception propagation behavior. The optional PIL-based mime-type detection at `ImageService.filter_base64_images()` would prevent the error from reaching the model at all, which is the deeper fix; PIL is already in the dependency tree and used in the indexing pipeline.

Test coverage posture is mixed: `AgentNode.post_process_output()` and `BaseNode.__call__()`'s exception path are individually tested, but the integrated path from `BadRequestError` through to `workflow_execution_service.fail()` is not. `WorkflowExecutionService.fail()` has zero test coverage. Five concrete test gaps are identified (detailed in Section 4), all of which are straightforward to fill with existing mock patterns. The highest-risk gap is the absence of any test verifying that the overall workflow transitions to FAILED (not IN_PROGRESS) when a node encounters a `BadRequestError`.
