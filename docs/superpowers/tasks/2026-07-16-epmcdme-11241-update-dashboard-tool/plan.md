# Plan: EPMCDME-11241 — Fix MCP Tool Silent Drop

**Ticket**: EPMCDME-11241  
**Branch**: main (dirty)  
**Date**: 2026-07-16

---

## Tasks

### Task 1 — Fallback schema in `_create_tools()`
**File**: `src/codemie/service/mcp/toolkit.py`  
**Test-first**: No unit-level failing test needed — the bug manifests at integration with a live MCP server. The change is defensive infrastructure (exception handler replacement) verified by reading the updated handler.  
**Status**: DONE

Replace the bare `logger.error` + silent drop with:
1. `logger.warning` log (so the degraded path is visible but not alarming)
2. Inner try/except that constructs a minimal fallback schema with `create_model` and appends the tool
3. Inner `logger.error` only if even the fallback construction fails

### Task 2 — Relax `_is_object_schema()` guard
**File**: `src/codemie/core/json_schema_utils.py`  
**Test-first**: No. One-line guard change; covered by existing `json_schema_utils` tests for object detection.  
**Status**: DONE

Add `or "anyOf" in schema or "oneOf" in schema` to the return expression so top-level union schemas pass the initial guard in `json_schema_to_model()`.

---

## Verification

- `make ruff` — lint/format clean
- No test changes required: the silent-drop path has no existing unit test; the `_is_object_schema` change is covered by existing schema-utils tests
