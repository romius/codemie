# Plan: EPMCDME-13967 — ADO Work Item Reparenting

**Ticket**: EPMCDME-13967
**Branch**: main
**Date**: 2026-08-18

---

## Tasks

### Task 1 — Add input models to `models.py`
**File**: `src/codemie_tools/azure_devops/work_item/models.py`
**Status**: DONE

Add `RemoveWorkItemRelationInput` (work_item_id + relation_index) and `MoveWorkItemInput` (work_item_id + new_parent_id) before `GetWorkItemAttachmentContentInput`.

### Task 2 — Add ToolMetadata constants to `tools_vars.py`
**File**: `src/codemie_tools/azure_devops/work_item/tools_vars.py`
**Status**: DONE

Add `REMOVE_WORK_ITEM_RELATION_TOOL` and `MOVE_WORK_ITEM_TOOL`. The move tool description documents the four-step reparenting flow and TF201036 prevention.

### Task 3 — Implement tool classes in `tools.py`
**File**: `src/codemie_tools/azure_devops/work_item/tools.py`
**Status**: DONE

- Move `_HIERARCHY_REVERSE` constant to module top.
- Add `RemoveWorkItemRelationTool`: single `{"op": "remove", "path": f"/relations/{relation_index}"}` patch.
- Add `MoveWorkItemTool`: fetch → find existing parent index → build atomic patch (remove + add) → apply → verify with `endswith(f"/{new_parent_id}")`.

### Task 4 — Register tools in `toolkit.py`
**File**: `src/codemie_tools/azure_devops/work_item/toolkit.py`
**Status**: DONE

Import and register both tools. Tool count: 9 → 11.

### Task 5 — Unit tests
**Files**: `tests/codemie_tools/azure_devops/work_item/test_tools.py`, `test_toolkit.py`
**Status**: DONE

- 3 tests for `RemoveWorkItemRelationTool`
- 9 tests for `MoveWorkItemTool`
- Updated toolkit count assertions (9 → 11) and added new tool name assertions.

---

## Verification

- `make ruff` — lint/format clean
- `pytest tests/codemie_tools/azure_devops/` — all tests pass
- `uvx codemie-test-harness run sanity-api -n 1` — 186 passed, 4 skipped, 0 failed
