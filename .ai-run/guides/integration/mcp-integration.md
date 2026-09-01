# MCP Integration

## MCP Configuration

Keep MCP configuration and authentication behavior behind existing service and router modules.

| Avoid | Prefer |
|---|---|
| Adding MCP auth logic to unrelated routers | Use MCP config/auth routers and services |
| Treating MCP tools as static app code | Load/configure through existing MCP service boundaries |

Evidence: MCP config router is registered at `src/codemie/rest_api/main.py:695`; MCP auth router is included at `src/codemie/rest_api/main.py:713`.

## Slow-Start Troubleshooting

If an MCP server reports 500 errors only on first connection (e.g. during `npx`/`uvx` dependency download), the initialization timeout is the most likely cause.

| Variable | Service | Default | Unit | Purpose |
|---|---|---|---|---|
| `MCP_SERVER_INIT_TIMEOUT` | CodeMie | `300.0` | seconds | Backend guard — `create_toolkit` times out if server start exceeds this |
| `MCP_CONNECT_INIT_TIMEOUT` | MCP-Connect | `30000` | **milliseconds** | Bridge-side guard around `session.initialize()` |
| `MCP_CLIENT_TIMEOUT` | CodeMie | `300.0` | seconds | Per-invocation timeout for `tools/call` requests |

The effective initialization budget is `min(MCP_SERVER_INIT_TIMEOUT, MCP_CONNECT_INIT_TIMEOUT / 1000)` — the smaller value wins. Raise **both** variables together. Example for a 120-second budget:

```
# CodeMie deployment
MCP_SERVER_INIT_TIMEOUT=120

# MCP-Connect deployment
MCP_CONNECT_INIT_TIMEOUT=120000
```

The timeout applies to all transports (stdio, SSE, streamable-http) and both cached and single-use modes.
