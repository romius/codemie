# Spec: Fix MCP Tool Listing — update_dashboard Silently Dropped

**Ticket**: EPMCDME-11241  
**Branch**: EPMCDME-11241_update-dashboard-tool  
**Date**: 2026-07-16

---

## Problem

`update_dashboard`, a tool from the **mcp-grafana** MCP server, does not appear in the tool list on the Create Assistant / MCP Server page. The tool is correctly served by the mcp-grafana server over the MCP-Connect bridge; the backend drops it during schema conversion.

Root cause: `MCPToolkit._create_tools()` (`src/codemie/service/mcp/toolkit.py:556–595`) silently discards any tool whose `json_schema_to_model()` call raises. `json_schema_to_model()` raises a `TypeError` for schemas that use `anyOf` or `oneOf` at the top level without `type: "object"`, and a `NotImplementedError` for schemas that use `patternProperties` or `if/then/else`. The mcp-grafana `update_dashboard` tool's inputSchema triggers one of these paths.

---

## Scope

Two minimal changes in the generic MCP tool-creation path. No changes to routers, services, or any native toolkit.

---

## Change 1 — Fallback schema in `_create_tools()`

**File**: `src/codemie/service/mcp/toolkit.py`  
**Method**: `MCPToolkit._create_tools()`

Replace the silent-drop exception handler with a fallback that creates a minimal schema and still appends the tool:

```python
except Exception as e:
    logger.warning(
        f"Failed to create schema for MCP tool '{tool_def.name}', using fallback schema: {str(e)}"
    )
    try:
        sanitized_name = sanitize_tool_name(tool_def.name)
        fallback_schema = create_model(f"{sanitized_name.capitalize()}ArgsSchema")
        tool = MCPTool(
            name=sanitized_name,
            description=tool_def.description,
            mcp_client=self.mcp_client,
            mcp_server_config=self.mcp_server_config,
            args_schema=fallback_schema,
            mcp_tool_name=tool_def.name,
        )
        tools.append(tool)
    except Exception as inner_e:
        logger.error(
            f"Failed to create MCP tool '{tool_def.name}' even with fallback schema: {str(inner_e)}",
            exc_info=True,
        )
```

The fallback schema is identical to what `_create_args_schema()` already produces for tools with no `inputSchema` (the `else` branch). The tool appears in the listing with its name and description intact; invocation with complex parameters still works through the underlying MCP protocol.

---

## Change 2 — Relax `_is_object_schema()` guard

**File**: `src/codemie/core/json_schema_utils.py`  
**Function**: `_is_object_schema()`

Add `anyOf`/`oneOf` to the accepted top-level schema patterns so that tools with union-type top-level schemas pass the guard and get their fields processed rather than immediately failing:

```python
def _is_object_schema(schema: JsonSchema) -> bool:
    """Check if a schema fragment represents a JSON object."""
    return (
        schema.get("type") == "object"
        or "properties" in schema
        or "allOf" in schema
        or "anyOf" in schema
        or "oneOf" in schema
    )
```

This change is safe: inside `_schema_to_type_annotation`, `anyOf`/`oneOf` are already dispatched by `_get_allof_oneof_anyof_dispatch` *before* `_is_object_schema` is checked, so no nested-schema behavior changes. The only difference is that top-level `anyOf`/`oneOf` schemas no longer fail the initial guard in `json_schema_to_model()`.

---

## Acceptance Criteria

1. `POST /v1/assistants/mcp_tools` with a mcp-grafana server config returns `update_dashboard` in the tools list.
2. Tools that currently work (schemas that parse correctly) continue to work unchanged.
3. A tool whose schema cannot be converted no longer causes a silent omission — it appears with a minimal fallback schema and a `WARNING`-level log instead of an `ERROR`.
4. No changes to API contracts, response models, or any native toolkit.
