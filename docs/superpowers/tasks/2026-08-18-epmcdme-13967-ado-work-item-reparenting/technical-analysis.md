# Technical Analysis: EPMCDME-13967 — ADO Work Item Reparenting

**Ticket**: EPMCDME-13967
**Date**: 2026-08-18

---

## Summary

CodeMie Assistant cannot reparent an Azure DevOps work item because the only available relation tool (`link_work_items`) unconditionally adds a relation. Azure DevOps enforces a single-parent rule; adding a second `System.LinkTypes.Hierarchy-Reverse` link fails with TF201036. The fix requires a low-level relation-removal primitive and a high-level orchestrated reparent that batches `remove` + `add` into a single atomic patch.

---

## Root Cause

### ADO single-parent enforcement

Azure DevOps allows at most one `System.LinkTypes.Hierarchy-Reverse` relation per work item (the "Parent" link). Attempting to add a second parent via `update_work_item` raises:

```
TF201036: You cannot add a second Parent link to work item {id}. A work item can have at most one parent.
```

### Existing `link_work_items` tool

`LinkWorkItemsTool` issues a single `{"op": "add", "path": "/relations/-", "value": {...}}` patch. It has no awareness of existing relations and no way to remove them first. Callers cannot work around TF201036 without a separate remove step.

### No remove primitive

The ADO JSON Patch API supports relation removal via `{"op": "remove", "path": "/relations/{index}"}` where `{index}` is the 0-based position in the work item's `relations` array. This operation was not exposed in any existing tool.

---

## Codebase Findings

| File | Role |
|---|---|
| `src/codemie_tools/azure_devops/work_item/tools.py` | Tool classes — PRIMARY change target |
| `src/codemie_tools/azure_devops/work_item/models.py` | Input models — new `RemoveWorkItemRelationInput`, `MoveWorkItemInput` |
| `src/codemie_tools/azure_devops/work_item/tools_vars.py` | ToolMetadata constants — two new entries |
| `src/codemie_tools/azure_devops/work_item/toolkit.py` | Toolkit registration — two new tools |
| `tests/codemie_tools/azure_devops/work_item/test_tools.py` | Unit tests — 12 new tests |
| `tests/codemie_tools/azure_devops/work_item/test_toolkit.py` | Toolkit tests — count + name assertions updated |

---

## Key Design Decisions

### Atomic patch vs. two separate calls

Both the `remove` and `add` operations are batched into a single `update_work_item(document=[op1, op2], ...)` call. The ADO SDK applies the operations sequentially in one round-trip. This avoids a race window where the work item would momentarily have no parent between two separate calls, and reduces network overhead.

### `endswith` vs. `in` for URL verification

After the patch, `MoveWorkItemTool` verifies the new parent by checking `rel.url.endswith(f"/{new_parent_id}")`. Using `str(new_parent_id) in rel.url` would produce a false positive: ID `100` would match a URL ending in `.../workItems/1009`. The `endswith` check is exact.

### Conditional remove step

`MoveWorkItemTool` only appends the `remove` op when a `Hierarchy-Reverse` relation is found. If the work item has no current parent the patch contains only the `add` op — the tool handles both cases in a single code path.

### `_HIERARCHY_REVERSE` placement

The constant `"System.LinkTypes.Hierarchy-Reverse"` is used by both `MoveWorkItemTool` and for verification. It was moved from its original position between class definitions to the module top (line 60) to reflect its role as a module-level constant.

---

## Risk Indicators

- **Low blast radius**: changes confined to the ADO Work Item toolkit; no shared infrastructure touched.
- **No API contract change**: no new routes, no changed response models.
- **Additive only**: existing tools (`link_work_items`, `get_work_item`, etc.) are unmodified.
- **ADO-specific errors propagate cleanly**: TF201036, permission errors, and invalid ID errors from the ADO SDK surface as readable strings via `ToolException`.
