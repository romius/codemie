# Survey of public technologies for interactive chat UI (as of August 2026)

Source: research agent (WebSearch/WebFetch), 2026-08-04. Scenario: an LLM assistant
(Python/FastAPI + LangChain) asks the user to fill in controls in a React chat and
receives a structured response.

## 1. A2UI (Google) — top candidate

- Open generative UI protocol: the agent declares UI in JSON, the client renders it
  **with native components of its own design system** (not an iframe). Announced 15.12.2025, Apache 2.0,
  github.com/google/A2UI ~16k stars.
- Status: **v0.9.1 production-stable; v1.0 — Release Candidate** (spec updated
  2026-06-08), stable v1.0 targeted for Q4 2026. v0.8→v0.9 was a breaking redesign.
  The project itself: "early stage public preview, expect changes".
- Model: `createSurface`, `updateComponents`, `updateDataModel`, `deleteSurface` messages;
  bidirectional RPC (`callFunction`/`actionResponse` ↔ `action`/`functionResponse`/`error`).
  Data binding — JSON Pointer (RFC 6901), two-way input binding; the response goes to the agent
  on `action` (click), optionally with the full data model. Transport-agnostic (SSE, WS, REST,
  MCP, A2A 1.0, AG-UI). Streaming: progressive rendering, partial-JSON.
- Catalog (Basic Catalog): Text, Image, Icon, Video, AudioPlayer, Row, Column, List, Card,
  Tabs, Divider, Modal, **Button, CheckBox, TextField, DateTimeInput, ChoicePicker
  (select/radio), Slider** + validation (required, regex, email, length, numeric).
  Covers all of our controls out of the box.
- Integration: official Python Agent SDK (version negotiation, dynamic catalogs,
  resilient streaming; ADK/LangChain examples); React — official `@a2ui/react`
  (0.10.1, built on `@a2ui/web_core`; young, appeared in v0.9), + `@a2ui/lit`, `@a2ui/angular`,
  community renderers.
- Risks: pre-1.0, recent breaking redesign, young React renderer.
- https://github.com/google/A2UI · https://a2ui.org/specification/v1.0-a2ui/

## 2. MCP Apps (SEP-1865) / mcp-ui

- Official standardization of UI in MCP; mcp-ui (Ido Salomon) became the basis of SEP-1865
  "MCP Apps", co-authored by Anthropic and OpenAI; **status Final since 2026-01-26** (`ext-apps`).
- Model: the MCP server declares a `ui://` UI resource (`text/html;profile=mcp-app`), the host
  renders the HTML in a **sandboxed iframe**; communication via postMessage + JSON-RPC 2.0 (`ui/*`); the UI can
  invoke tools/call. The UI is **pre-authored by the developer** (a mini-app), not generated
  declaratively by the LLM.
- Largest adoption: Claude, ChatGPT, VS Code Copilot, Goose, Postman, LibreChat…
  `@mcp-ui/server|client` (TS/React), `mcp-ui-server` (PyPI). 5.1k stars.
- For "the LLM asks for a form in the chat feed on the fly" — heavyweight and a foreign look & feel; appropriate
  if the product strategically becomes an MCP host.
- https://modelcontextprotocol.io/seps/1865-mcp-apps-interactive-user-interfaces-for-mcp

## 3. AG-UI (CopilotKit)

- Agent↔frontend event protocol, MIT, 14.7k stars; integrations: LangGraph (first-party),
  Pydantic AI, CrewAI, LlamaIndex, ADK, AWS Strands, MS Agent Framework.
- ~16 event types in a single stream (SSE/HTTP): lifecycle, TEXT_MESSAGE_*, TOOL_CALL_*,
  STATE_SNAPSHOT/DELTA, interrupts, CUSTOM. Generative UI: **no component catalog** —
  the frontend maps tool calls to its own components; HITL — interrupts
  (`renderAndWaitForResponse`). Python: `ag-ui-protocol` (PyPI), FastAPI-friendly, thin.
  React: CopilotKit (thick) or your own client on `@ag-ui/client`.
- Solves the transport/semantics of the whole chat protocol, but the UI vocabulary stays home-grown.
  Compatible with A2UI as a payload.
- https://github.com/ag-ui-protocol/ag-ui

## 4. OpenAI Apps SDK

- Converged with MCP Apps (ChatGPT is compatible with the spec; OpenAI recommends building on MCP Apps).
  Not needed as a separate technology — a confirmation that the standard = MCP Apps.

## 5. Adaptive Cards (Microsoft)

- JSON card schema (~2017), MIT, schema 1.6 stable for years. Input.Text/Date/Time/
  Number/Toggle/ChoiceSet + Action.Submit — exactly our scenario, invented before LLMs.
  LLM-friendly (massively present in training data).
- But: maintenance mode, the official React renderer is abandoned (wrappers over the imperative JS
  SDK), customization via HostConfig is limited, the ecosystem outside the Microsoft stack is stagnating.

## 6. Vercel AI SDK Generative UI / AI Elements

- TS/Next-centric; generative UI = mapping tool calls to React components; streaming
  is first-class. Python — only via the Pydantic AI adapter (`pydantic_ai.ui.vercel_ai`);
  for LangChain+FastAPI — a semi-home-grown bridge. Weakly addresses the goal of "moving away from home-grown".

## 7. Others

- **RJSF / JSON Forms**: the LLM generates JSON Schema + uiSchema → form is rendered → submit
  is validated with the same schema in Python. Maximally LLM-friendly, all controls, mature libraries
  (RJSF v6). But it is a forms library, not a chat protocol — the envelope stays home-grown (thin).
- **Slack Block Kit** — a design precedent, not portable.
- **LangGraph `interrupt()`** — the server-side half of the scenario (graph pause → schema → 
  `Command(resume=...)`); not a competitor but a possible server-side foundation.

## Comparative summary (top 3 for our scenario)

1. **A2UI** — the only one that fully covers the scenario by design: LLM-generated
   declarative JSON, a full input catalog, native rendering in our DS, official
   Python SDK and @a2ui/react, partial-JSON streaming, transport agnosticism (will fit our
   NDJSON). Risk: pre-1.0 + young React renderer; mitigation — isolation behind an adapter,
   the v1.0 RC with a migration guide is already published.
2. **AG-UI (+ LangGraph interrupt)** — if we change the whole chat protocol, not just the forms.
   It provides no control catalog (combine with A2UI/JSON Schema as the payload).
3. **RJSF over a thin envelope** — a low-risk fallback: JSON Schema instead of our own
   DSL, the request/response envelope stays ours (tiny).

Honorable mention: MCP Apps — the only Final standard with the largest adoption, but the model
(pre-built iframe apps) does not match our scenario. Adaptive Cards — mature,
but the React story and stagnation make it worse than A2UI along every axis except maturity.
