# Requirements — 20260804-1031-EPMCDME-13900

**Source**: ticket:EPMCDME-13900
**Work Item**: docs/superpowers/work-items/epmcdme-13900.md
**Original input**: |
  EPMCDME-13900

## Goal

Fix the CodeMie backend so that MCP servers launched via `npx` can successfully load tools into assistants, and ensure actionable diagnostics are surfaced when the MCP subprocess exits, closes the connection, or fails during handshake.

## Acceptance Criteria

- The assistant can successfully load tools from an MCP server launched through `npx` when the same server works locally via MCP inspector
- MCP client initialization does not fail with `Connection closed (ValueError)` for valid and locally working MCP server configurations
- The system captures and exposes actionable diagnostics when the MCP process exits, closes the connection, or fails during handshake
- Error details include enough context to distinguish between: process startup failure, missing environment variables, dependency installation issues, MCP protocol handshake failure, and timeout
- Regression testing covers `npx`-based MCP server startup and tool loading from assistant configuration
- Existing MCP integrations and other transport/configuration modes are not regressed

## Context

- **Error**: `MCPToolLoadException: Failed to load MCP tools from 'MCP-server' - Failed to create MCP client: McpError: Connection closed (ValueError)`
- **Timing**: failure occurs almost immediately after clicking "Test connection" — suggests subprocess crash rather than timeout
- **Constraint**: The MCP-Connect bridge service (external to this codebase) spawns the npx subprocess. It uses `extra="forbid"` on its request body model, so new fields cannot be added to `MCPToolInvocationRequest` without bridge-team coordination
- **Current timeout state**: Python-side `MCP_SERVER_INIT_TIMEOUT` was raised to 300s in EPMCDME-10045; bridge-side `MCP_CONNECT_INIT_TIMEOUT` remains at 30s default
- **Error path**: bridge returns HTTP error with body `"Failed to create MCP client: McpError: Connection closed"` → `client.py` wraps as `ValueError` → `toolkit_service.py` wraps as `MCPToolLoadException` → `mcp_tester.py` returns generic "Please, check the configuration."
- **Key files**: `src/codemie/service/mcp/client.py`, `src/codemie/service/mcp/models.py`, `src/codemie/service/mcp/toolkit_service.py`, `src/codemie/service/mcp/mcp_tester.py`

## Open questions

(none)
