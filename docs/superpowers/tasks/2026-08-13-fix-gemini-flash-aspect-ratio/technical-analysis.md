# Technical Research

**Task**: generate_image aspect_ratio gemini flash image_tool
**Generated**: 2026-08-13T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

src/codemie_tools/data_management/workspace/generate_image_tool_v2.py - final aspect ratio for Gemini Flash is incorrect (for models like gpt-image it's fine)

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie_tools/data_management/workspace/generate_image_tool_v2.py` — primary tool; parses requested size, builds an `ImageGenerationPlan`, dispatches to the image generator, crops/converts the result; contains the bug
- `src/codemie_tools/data_management/file_system/image_generator.py` — defines the `ImageGenerator` protocol, `LiteLLMImageGenerator` (AzureOpenAI/LiteLLM proxy client), `ChatModelImageGenerator` (Vertex AI direct), and the `is_gemini_image_model()` detector
- `src/codemie_tools/data_management/file_system/generate_image_tool.py` — V1 tool (filesystem-scoped); no aspect ratio logic and no Gemini branch; serves as a comparison point
- `src/codemie_tools/data_management/workspace/toolkit.py` — wires `GenerateWorkspaceImageToolV2` into the workspace toolkit
- `src/codemie/service/tools/toolkit_settings_service.py` — resolves `image_generation_model` from request → assistant config → global default; constructs `LiteLLMImageGenerator` or `ChatModelImageGenerator` and instantiates the v2 tool
- `src/codemie/configs/config.py:67` — sets `IMAGE_GENERATION_MODEL = "gemini-3.1-flash-image"` as the application-wide default

### Architecture and Layers Affected

- **Tool layer** (`codemie_tools/data_management/workspace/`) — `GenerateWorkspaceImageToolV2` is the LangChain tool wrapper; contains the plan-builder functions, the `_GEMINI_IMAGE_DIMENSIONS` lookup table, `find_closest_gemini_aspect_ratio`, and `select_gemini_image_size`
- **Image generator layer** (`codemie_tools/data_management/file_system/`) — `LiteLLMImageGenerator` constructs the `extra_body` payload and sends it to the LiteLLM proxy; `is_gemini_image_model()` is the branch predicate used both here and in the tool
- **Toolkit/settings service layer** (`codemie/service/tools/`) — dependency injection wiring; selects the concrete generator implementation and builds the tool instance
- **Config layer** (`codemie/configs/config.py`) — env-overridable model ID that activates Gemini-specific code paths

### Integration Points

- `generate_image_tool_v2` → `image_generator.py`: imports `is_gemini_image_model`; calls `LiteLLMImageGenerator.generate()` with `extra_body={"response_format": {"image": {"aspect_ratio": ..., "image_size": ...}}}`
- `toolkit_settings_service` → `image_generator.py`: calls `is_gemini_image_model()` to decide which concrete generator to build; always selects `LiteLLMImageGenerator` for the workspace v2 tool
- `toolkit.py` → `generate_image_tool_v2`: imports and instantiates `GenerateWorkspaceImageToolV2`
- External: LiteLLM proxy → `vertex_ai/gemini-3.1-flash-image` (registered in `litellm_config.yaml:186` with `vertex_location: "global"`)
- The `ChatModelImageGenerator` (direct Vertex AI) path discards `extra_body` entirely (`del size, output_format, extra_body`); it has no aspect-ratio support and is not used by the workspace v2 tool

### Patterns and Conventions

- **Provider dispatch**: `is_gemini_image_model(model_id)` — checks that both `"gemini"` and `"image"` are present in the lowercased model ID string; used at plan-build time to select which parameter schema to construct
- **`ImageGenerationPlan` dataclass**: separates API parameter construction from canvas-math and crop post-processing; keeps Gemini-specific fields (`aspect_ratio`, `image_size`, `preserve_generated_dimensions`) isolated from the gpt-image fields (`size`, `output_format`)
- **`extra_body` passthrough**: Gemini parameters are delivered via the OpenAI `extra_body` kwarg rather than standard API fields, allowing the LiteLLM proxy to forward them to Vertex AI
- **`preserve_generated_dimensions=True`**: for Gemini, actual output width/height are read back from the returned image bytes rather than assumed from the plan — so any mismatch between the planned aspect ratio and what Gemini actually honours is silently absorbed into the reported dimensions
- **`_BOUNDING_BOXES` constraint (gpt-image path)**: restricts non-Gemini models to exactly 3 valid sizes (1536×1024 landscape, 1024×1024 square, 1024×1536 portrait), which is why that path is unaffected by the bug

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/agents/agent-tools.md` — confirms reusable tool integrations belong in `codemie_tools`; schema compatibility with LangChain must be maintained
- `.ai-run/guides/agents/custom-tool-creation.md` — confirms provider toolkits live under `src/codemie_tools/data_management/`; tests must be updated when schema or provider behavior changes
- `.ai-run/guides/integration/cloud-integrations.md` — covers GCP provider packages; does not document Gemini image generation or aspect ratio constraints
- `.ai-run/guides/integration/llm-providers.md` — documents provider config under `config/llms/`; does not document Gemini image aspect ratio restrictions

### Architectural Decisions

- No ADRs, TODO, HACK, or DECISION markers exist anywhere in `src/codemie_tools/data_management/workspace/`
- No CHANGELOG entries for image generation aspect ratios or Gemini Flash image support
- The only recorded Gemini image architectural choice is the default in `src/codemie/configs/config.py:67` (`IMAGE_GENERATION_MODEL = "gemini-3.1-flash-image"`) and the LiteLLM routing entry in `litellm_config.yaml`

### Derived Conventions

- Gemini-specific API parameters are delivered exclusively via `extra_body`; standard `size=` and `output_format=` are only used for non-Gemini models
- The `_GEMINI_IMAGE_DIMENSIONS` dict serves as both the aspect-ratio lookup table and the source of `_SUPPORTED_GEMINI_ASPECT_RATIOS`; this dual use is the structural cause of the bug
- For non-Gemini models, valid output sizes are hard-coded via `_BOUNDING_BOXES`; for Gemini, they are inferred from the selected `image_size` tier ("512", "1K", "2K", "4K") in `_GEMINI_IMAGE_DIMENSIONS`

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie_tools/data_management/workspace/test_generate_image_tool_v2.py` — covers helper functions, Gemini plan building (2 cases: `1920x1080 → 16:9` and `5:12 ratio → 9:16`), and the full `execute` flow (5 tests); both existing Gemini plan cases happen to land on API-supported ratios, so the bug is not caught
- `tests/codemie_tools/data_management/file_system/test_generate_image_tool.py` — covers V1 tool, `LiteLLMImageGenerator`, `ChatModelImageGenerator`; one test verifies `edit` call passes `extra_body` containing `aspect_ratio`

### Testing Framework and Patterns

- pytest with `unittest.TestCase` subclasses; configured in `pytest.ini` (`testpaths = tests`, `pythonpath = src`, `--import-mode=importlib`)
- `MagicMock` / `patch` for all external dependencies (`AzureOpenAI`, `AgentWorkspaceService`, `requests.get`)
- `object.__new__(AgentWorkspaceService)` pattern to bypass `__init__` in service setup
- `_make_png_bytes()` inline factory using Pillow `Image.new()` to produce synthetic PNG content
- Assertions on `call_args.kwargs` to verify exact keyword arguments passed to generators

### Coverage Gaps

- `find_closest_gemini_aspect_ratio` — only one test case (`1920x1080 → 16:9`); no portrait, square, near-boundary, or inputs that would resolve to an unsupported ratio (e.g. `2400x1000` currently selects `21:9`)
- `select_gemini_image_size` — zero coverage; controls which Gemini `image_size` tier is selected and the resulting pixel dimensions in the plan
- `get_gemini_dimension_distance` — zero coverage; underlying scoring function for `select_gemini_image_size`
- `build_image_generation_plan` for non-Gemini models — zero coverage; the `_BOUNDING_BOXES`-constrained path is entirely untested
- `validate_background`, `classify_orientation`, `get_orientation_hint`, `build_image_prompt` — none tested
- `get_canvas_dimensions`, `resize_image_to_png`, `convert_image_to_png`, `crop_image_to_dimensions` — none tested

---

## 5. Configuration and Environment

### Environment Variables

- `IMAGE_GENERATION_MODEL` — global fallback model ID; defaults to `"gemini-3.1-flash-image"`; also controls whether Gemini-specific aspect-ratio logic activates via `is_gemini_image_model()`
- `LLM_PROXY_ENABLED` — bool; when `True` + `LITE_LLM_URL` set, routes image requests through the LiteLLM proxy
- `LITE_LLM_URL` — LiteLLM proxy base URL; used as `api_base` for `LiteLLMImageGenerator`
- `LITE_LLM_APP_KEY` — API key for the LiteLLM proxy
- `AZURE_OPENAI_URL` / `AZURE_OPENAI_API_KEY` — fallback Azure OpenAI endpoint when LiteLLM proxy is disabled
- `OPENAI_API_VERSION` — API version for the image endpoint (default `2025-04-01-preview`)
- `GOOGLE_VERTEXAI_REGION` / `GOOGLE_PROJECT_ID` — GCP credentials for the direct `ChatModelImageGenerator` path
- `VERTEX_PROJECT` — consumed in `litellm_config.yaml` for Vertex AI model entries

### Configuration Files

- `litellm_config.yaml:186` — registers `gemini-3.1-flash-image` as `vertex_ai/gemini-3.1-flash-image` with `vertex_location: "global"` and `supports_image_generation: true`; no per-model aspect-ratio restrictions are set here
- `config/llms/llm-gcp-config.yaml` — GCP LLM registry; lists `gemini-3.1-flash-image` with cost metadata (`output_cost_per_image_token`); no aspect ratio config
- `config/llms/llm-dial-config.yaml` — DIAL provider registry; mirrors the same model deployment name; no aspect ratio config
- `src/codemie/configs/config.py:67` — application config class; `IMAGE_GENERATION_MODEL` default

### Feature Flags and Deployment Concerns

- No feature flags found for image generation
- The fix is entirely in source code — `litellm_config.yaml` passes the `aspect_ratio` field through to Vertex AI as-is; no config override can correct a wrong ratio value at the proxy level
- The workspace v2 tool always uses `LiteLLMImageGenerator` (no `ChatModelImageGenerator` fallback in `_build_workspace_image_generator`), so the `extra_body` aspect-ratio path is always exercised for Gemini Flash in production

---

## 6. Risk Indicators

- **Root bug — `_SUPPORTED_GEMINI_ASPECT_RATIOS` includes 14 ratios but Gemini Flash only accepts 5**: `_SUPPORTED_GEMINI_ASPECT_RATIOS = tuple(_GEMINI_IMAGE_DIMENSIONS.keys())` (line 63 of `generate_image_tool_v2.py`) derives the search candidate set from all keys of `_GEMINI_IMAGE_DIMENSIONS`, which includes 9 unsupported ratios (`1:4`, `1:8`, `2:3`, `3:2`, `4:1`, `4:5`, `5:4`, `8:1`, `21:9`). When `find_closest_gemini_aspect_ratio` selects one of these, it is sent verbatim to the Gemini API in `extra_body`, which silently uses a different default and returns an image with the wrong shape.
- **`select_gemini_image_size` and `get_gemini_dimension_distance` are entirely untested** — these functions determine which `image_size` tier and therefore the actual pixel dimensions in the plan; defects here would also produce wrong output dimensions
- **`find_closest_gemini_aspect_ratio` is undertested** — only the `16:9` happy path is covered; no test exercises a near-boundary case or an input that resolves to an unsupported ratio
- **`preserve_generated_dimensions=True` masks dimension mismatches** — when Gemini ignores an unsupported ratio and returns a different size, the plan's `target_width`/`target_height` are overwritten with the actual returned dimensions; the mismatch is absorbed silently rather than raising an error
- **`ChatModelImageGenerator` discards `extra_body`** — the direct Vertex AI path has no aspect-ratio support; if a deployment switches to direct Vertex AI for Gemini Flash, images will always use the model's default aspect ratio
- **Short task description (< 50 words)** — the bug report contains no acceptance criteria or expected behavior specification for edge cases; requirements clarity risk for the fix scope

---

## 7. Summary for Complexity Assessment

The bug is well-isolated and the fix is surgical. `find_closest_gemini_aspect_ratio` in `generate_image_tool_v2.py` searches across all 14 keys of `_GEMINI_IMAGE_DIMENSIONS` to find the closest aspect ratio to the user's requested dimensions. However, Gemini Flash (Imagen via Vertex AI) only accepts 5 valid aspect ratios: `1:1`, `3:4`, `4:3`, `9:16`, `16:9`. The other 9 entries in the dictionary (`1:4`, `1:8`, `2:3`, `3:2`, `4:1`, `4:5`, `5:4`, `8:1`, `21:9`) are only useful for dimension lookups once a valid API ratio is chosen; they are not valid API values. The fix requires introducing a separate constant — e.g. `_GEMINI_FLASH_SUPPORTED_ASPECT_RATIOS` — restricted to those 5 values, and substituting it for `_SUPPORTED_GEMINI_ASPECT_RATIOS` in `find_closest_gemini_aspect_ratio`. The `_GEMINI_IMAGE_DIMENSIONS` table itself can remain unchanged. Only one source file changes meaningfully: `generate_image_tool_v2.py`. `image_generator.py` and all wiring layers are unaffected.

The affected area follows an established pattern (provider dispatch via `is_gemini_image_model()`, `extra_body` passthrough, `ImageGenerationPlan` dataclass) with no novel architectural moves required. The gpt-image path is protected by `_BOUNDING_BOXES` and is unaffected. The `ChatModelImageGenerator` (direct Vertex AI) path ignores `extra_body` entirely, so it is also out of scope. The task does not touch any API endpoints, database models, or service-layer orchestration.

Test coverage for the affected functions is thin: only one test case for `find_closest_gemini_aspect_ratio` (a `16:9` happy path), and zero coverage for `select_gemini_image_size` and `get_gemini_dimension_distance`. The fix should be accompanied by new unit tests that verify inputs mapping to unsupported ratios are correctly snapped to the nearest valid Gemini API ratio. Key risk: the silent `preserve_generated_dimensions=True` behaviour means the bug manifests as a shape mismatch in the returned image rather than an API error, which makes it easy to miss without regression tests covering the full 14-ratio input space.
