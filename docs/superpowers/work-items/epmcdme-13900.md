# Work Item: EPMCDME-13900

**Type**: Bug
**Priority**: Major
**Status**: In Progress
**External Ticket**: https://jira.epam.com/jira/browse/EPMCDME-13900
**Assignee**: TBD
**Epic**: MCP Ecosystem & Tool Connectivity

## Summary

MCP server launched via npx fails to load tools in assistant with `Connection closed (ValueError)` despite working locally.

## Description

An MCP server configured for an assistant stopped working without any code or configuration changes for approximately two months. The same MCP server starts and works locally via npx, and functionality was confirmed using the MCP inspector. When the assistant attempts to load MCP tools, tool loading fails during MCP client creation with a connection closed error.

## Acceptance Criteria

- [ ] The assistant can successfully load tools from an MCP server launched through npx when the same server works locally via MCP inspector
- [ ] MCP client initialization does not fail with `Connection closed (ValueError)` for valid and locally working MCP server configurations
- [ ] The system captures and exposes actionable diagnostics when the MCP process exits, closes the connection, or fails during handshake
- [ ] Error details include enough context to distinguish between: process startup failure, missing environment variables, dependency installation issues, MCP protocol handshake failure, and timeout
- [ ] Regression testing covers npx-based MCP server startup and tool loading from assistant configuration
- [ ] Existing MCP integrations and other transport/configuration modes are not regressed

## Context

- Error observed: `MCPToolLoadException: Failed to load MCP tools from 'MCP-server' - Failed to create MCP client: McpError: Connection closed (ValueError)`
- Failure occurs almost immediately after clicking "Test connection" — suggests a process crash rather than a timeout
- Server works locally via `npx` and MCP inspector
- Environment variables are configured and verified

## Linked Artifacts

_None yet_

## History

| Timestamp | Event | Notes |
|---|---|---|
| 2026-08-04T10:32:00Z | work_item.created | Run 20260804-1031-EPMCDME-13900 |
