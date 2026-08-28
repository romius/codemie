# Spec: Azure DevOps Work Item Reparenting

**Ticket**: EPMCDME-13967
**Branch**: main
**Date**: 2026-08-18

---

## Problem

CodeMie Assistant cannot move an Azure DevOps work item from one parent to another. The existing `link_work_items` tool only adds relations; when a work item already has a parent and a second `System.LinkTypes.Hierarchy-Reverse` relation is added, Azure DevOps rejects the request with error **TF201036** ("Work item already has a parent"). There was also no way to remove an existing relation without the primitive not being exposed.

---

## Scope

Two new tools added to the ADO Work Item toolkit:

1. **`remove_work_item_relation`** — low-level primitive: removes a relation from a work item by its 0-based index in the relations array.
2. **`move_work_item`** — high-level orchestrated operation: reparents a work item atomically (removes old parent if present, adds new parent, verifies result).

No changes to existing tools. No API or router changes.

---

## Change 1 — Input models (`models.py`)

**File**: `src/codemie_tools/azure_devops/work_item/models.py`

Added two Pydantic input models:

```python
class RemoveWorkItemRelationInput(BaseModel):
    work_item_id: int = Field(description="ID of the work item to remove a relation from")
    relation_index: int = Field(
        description="0-based index of the relation to remove. "
        "Use get_work_item with expand=Relations to discover the correct index."
    )

class MoveWorkItemInput(BaseModel):
    work_item_id: int = Field(description="ID of the child work item to move to a new parent")
    new_parent_id: int = Field(description="ID of the new parent work item")
```

---

## Change 2 — Tool metadata (`tools_vars.py`)

**File**: `src/codemie_tools/azure_devops/work_item/tools_vars.py`

Added `REMOVE_WORK_ITEM_RELATION_TOOL` and `MOVE_WORK_ITEM_TOOL` `ToolMetadata` constants before the existing `GET_WORK_ITEM_ATTACHMENT_CONTENT_TOOL`. The `MOVE_WORK_ITEM_TOOL` description documents the full reparenting flow and explicitly calls out TF201036 prevention.

---

## Change 3 — Tool implementations (`tools.py`)

**File**: `src/codemie_tools/azure_devops/work_item/tools.py`

- Moved `_HIERARCHY_REVERSE = "System.LinkTypes.Hierarchy-Reverse"` to top of file (line 60) — it is a module-level constant, not a class attribute.
- Added `RemoveWorkItemRelationTool`: sends a single JSON Patch `remove` operation to `update_work_item`.
- Added `MoveWorkItemTool`: fetches current relations, builds an atomic patch document (optional `remove` + mandatory `add`), applies in one round-trip, then verifies the new parent URL using `endswith(f"/{new_parent_id}")` to avoid substring false-positives (e.g. ID 100 matching URL `.../workItems/1009`).

---

## Change 4 — Toolkit registration (`toolkit.py`)

**File**: `src/codemie_tools/azure_devops/work_item/toolkit.py`

Imported and registered both new tools. Tool count increased from 9 to 11.

---

## Change 5 — Tests

**Files**: `tests/codemie_tools/azure_devops/work_item/test_tools.py`, `test_toolkit.py`

- Added `TestRemoveWorkItemRelationTool` (3 tests: success, index-zero, API error).
- Added `TestMoveWorkItemTool` (9 tests: existing parent removed+added, no parent only-add, `None` relations, parent at non-zero index, substring URL false-positive guard, verification failure, API error on fetch, API error on update, correct URL format).
- Updated toolkit count assertions from 9 → 11 and added new tool name presence assertions.

---

## Acceptance Criteria

1. `move_work_item(work_item_id, new_parent_id)` succeeds for a work item that already has a parent — no TF201036 error.
2. `move_work_item` on a work item with no parent only adds the new parent relation.
3. `remove_work_item_relation(work_item_id, relation_index)` removes the relation at the specified index.
4. Reparenting is atomic — single `update_work_item` call combining `remove` + `add` operations.
5. All 11 existing tool names are present in the toolkit definition.
6. All new unit tests pass; no regression in existing tests.
