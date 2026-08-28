# Fix Gemini Image Generation Plan Logic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the Gemini image generation path so it selects from all 14 supported aspect ratios, scales `target_width`/`target_height` within the canvas, always crops the output, and uses actual canvas dimensions in the orientation hint.

**Architecture:** Four coordinated fixes in `generate_image_tool_v2.py`: (1) restore all 14 aspect ratios in the search space, (2) compute target dimensions by scaling the request within the Gemini output canvas (same logic as gpt-image, but bounded by the Gemini canvas), (3) remove `preserve_generated_dimensions` since cropping is always applied, (4) pass actual canvas dimensions to `get_orientation_hint` instead of the hardcoded `_MIN_DIMENSION`.

**Tech Stack:** Python, pytest (unittest style)

## Global Constraints

- Files changed: `src/codemie_tools/data_management/workspace/generate_image_tool_v2.py` and `tests/codemie_tools/data_management/workspace/test_generate_image_tool_v2.py`
- No changes to wiring, API layer, or other modules.

---

### Task 1: Correct Gemini image generation plan logic

**Files:**
- Modify: `src/codemie_tools/data_management/workspace/generate_image_tool_v2.py`
- Test: `tests/codemie_tools/data_management/workspace/test_generate_image_tool_v2.py`

**Interfaces:**
- `find_closest_gemini_aspect_ratio(width, height) -> str` — now searches all 14 entries from `_GEMINI_IMAGE_DIMENSIONS`
- `build_image_generation_plan` Gemini path — computes `target_width, target_height` by scaling request dimensions within `(canvas_width, canvas_height)`
- `get_orientation_hint(orientation, target_width, target_height, canvas_width, canvas_height)` — uses actual canvas height/width for percentage calculation
- `build_image_prompt(description, background, target_width, target_height, canvas_width, canvas_height)` — forwards canvas dims to hint
- `ImageGenerationPlan` — `preserve_generated_dimensions` field removed; cropping is always applied

- [x] **Step 1: Write failing tests** — updated existing tests to expect: `find_closest_gemini_aspect_ratio(2400, 1000) == "21:9"`, `target=(1365, 768)` for 1920×1080 Gemini plan, `target=(573, 1376)` for 5:12 ratio plan, `result["width"]==1365` for execute test
- [x] **Step 2: Verify RED** — 4 tests failed for expected reasons
- [x] **Step 3: Implement** — all 4 fixes applied; ruff passed
- [x] **Step 4: Verify GREEN** — 19/19 tests pass
- [x] **Step 5: Commit** — `fix-gemini-flash-aspect-ratio: correct Gemini image plan logic`
