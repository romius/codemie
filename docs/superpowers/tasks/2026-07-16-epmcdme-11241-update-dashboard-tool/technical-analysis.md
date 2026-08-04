# Technical Analysis: EPMCDME-11241 — update_dashboard Tool Missing from MCP Server UI

**Ticket**: EPMCDME-11241  
**Date**: 2026-07-16  
**Analyst**: sdlc-factory:tech-analyst (corrected)

---

## Summary

`update_dashboard` is a tool exposed by the **mcp-grafana** MCP server (configured via `npx` JSON on the Create Assistant UI). The tool does not appear in the tool list on the "Create Assistant / MCP Server" page. The fix belongs entirely in the backend's generic MCP tool-fetching path, not in any native toolkit.

---

## Root Cause

### Code path for `POST /v1/assistants/mcp_tools`

```
POST /v1/assistants/mcp_tools (assistant.py:574–635)
  └── MCPToolsInfoService.get_mcp_toolkit_info()  (mcp_tools_info_service.py)
        └── MCPToolkitService.get_mcp_server_tools()  (toolkit_service.py)
              └── MCPToolkitFactory.create_toolkit()
                    └── MCPToolkit._create_tools()  ← FAILURE POINT
                          └── MCPToolkit._create_args_schema(tool_def)
                                └── json_schema_to_model(tool_def.inputSchema)  ← RAISES
```

### Failure point 1: `MCPToolkit._create_tools()` silently drops tools

File: `src/codemie/service/mcp/toolkit.py:556–595`

```python
except Exception as e:
    logger.error(f"Failed to create tool {tool_def.name}: {str(e)}", exc_info=True)
    # Tool is dropped. No fallback. Nothing returned for this tool_def.
```

Any exception from `_create_args_schema()` causes the tool to be completely excluded from the returned list. The log line only shows in server logs, never surfaced to the UI caller.

### Failure point 2: `json_schema_to_model()` top-level guard is too strict

File: `src/codemie/core/json_schema_utils.py:81–112`

```python
def json_schema_to_model(schema):
    if not _is_object_schema(schema):
        raise TypeError("Top-level schema must represent an object...")
```

```python
def _is_object_schema(schema):
    return schema.get("type") == "object" or "properties" in schema or "allOf" in schema
```

This guard rejects schemas that use `anyOf` or `oneOf` at the top level without `type: "object"` or `properties`. Many MCP server tools — including mcp-grafana — legitimately use `anyOf`/`oneOf` for schemas with mixed-type parameters.

Additionally, `_check_for_unsupported_keywords()` raises `NotImplementedError` for `patternProperties`, `if/then/else`, and `not`, which are present in some complex Grafana dashboard schemas.

### How these combine

For `update_dashboard` from mcp-grafana, the inputSchema likely uses schema patterns (top-level `anyOf`, nested `patternProperties`, or similar) that trigger one or both failures above. The result: `json_schema_to_model()` raises, `_create_args_schema()` propagates it, and `_create_tools()` silently drops `update_dashboard`.

---

## Codebase Findings

| File | Role |
|---|---|
| `src/codemie/service/mcp/toolkit.py:556–595` | `_create_tools()` — PRIMARY fix target |
| `src/codemie/core/json_schema_utils.py:253–255` | `_is_object_schema()` — SECONDARY fix target |
| `src/codemie/service/tools/mcp_tools_info_service.py` | No change needed |
| `src/codemie/rest_api/routers/assistant.py:574–635` | No change needed |
| `src/codemie/service/mcp/toolkit_service.py` | No change needed |

---

## Risk Indicators

- **Low blast radius**: two small, isolated changes in generic MCP infrastructure
- **No API contract change**: tool listing response format unchanged
- **Fallback is safe**: creating an empty Pydantic model as fallback schema is how the codebase already handles tools with no inputSchema (the `else` branch in `_create_args_schema`)
- **`_is_object_schema` change is safe**: the function is used in two places; the `_schema_to_type_annotation` caller already dispatches `anyOf`/`oneOf` before reaching `_is_object_schema`, so no nested-schema behavior changes
