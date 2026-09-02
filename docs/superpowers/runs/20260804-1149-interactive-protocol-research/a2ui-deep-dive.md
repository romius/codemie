# A2UI — deep dive (as of 2026-08-04)

Source: research agent (a2ui.org spec, repo google/A2UI → a2ui-project/a2ui, npm/PyPI).
Versions: v0.9.1 — production-stable; v1.0 — Release Candidate (since 2026-06-08), stable — Q4 2026.

## Message model (v1.0; differences from v0.9 noted)

JSONL framing: each envelope is `{version, <one type key>}`; delivery order is mandatory
(stateful). MIME `application/a2ui+json`.

Agent→Renderer:
- `createSurface {surfaceId, catalogId, sendDataModel, components?, dataModel?}` — in v1.0
  may carry components and data right away (in v0.9 — initialization only; `theme` removed in v1.0).
- `updateComponents {surfaceId, components: [...]}` — flat adjacency list, tree via IDs.
- `updateDataModel {surfaceId, path (JSON Pointer, default /), value}` — value=null deletes the key.
- `deleteSurface {surfaceId}`.
- v1.0 RPC: `callFunction {call, args}` (+functionCallId, wantResponse), `actionResponse`.

Renderer→Agent:
- `action {name, surfaceId, sourceComponentId, timestamp, context, wantResponse?, actionId?}` —
  the context is declared by the agent on the button, paths are resolved by the renderer into values.
- v1.0: `functionResponse`; `error {code: VALIDATION_FAILED|INVALID_FUNCTION_CALL, path,
  message}` — a standard feedback format for LLM self-correction.

Form lifecycle: createSurface → the user fills it in (two-way binding, locally) →
Button click → `action` with context (+ with sendDataModel:true — the FULL surface data model in
transport metadata) → the agent replies with updateDataModel/updateComponents/actionResponse.

## Component model

Component = `{id, component: "<Type>", ...props}`. Basic Catalog (18): Text, Image, Icon,
Video, AudioPlayer, Row, Column, List, Card, Tabs, Modal, Divider, Button, TextField,
CheckBox, ChoicePicker, Slider, DateTimeInput.

Our controls: Button {child, variant, action(event{name,context}|functionCall), checks};
TextField {label, value(two-way), placeholder, variant: shortText|longText|number|obscured};
CheckBox {label, value}; DateTimeInput {value ISO, enableDate/enableTime, min/max};
ChoicePicker {label, options[{label,value}], value: string[], variant: mutuallyExclusive
(radio/select)|multipleSelection, displayStyle: checkbox|chips, filterable}.

Validation: the `checks` array = calls to catalog functions (required, regex, length, numeric,
email) with a message; **enforced by the renderer** (client): field error + Button auto-disable.
The protocol has no server-side enforcement.

Complex forms: there is NO conditional visibility (a confirmed gap). Patterns: turn-based
rebuild via updateComponents (= our current model), Tabs/Modal as pseudo-steps,
custom catalog functions. Multi-step flows are not addressed by the spec.

## Data model / binding

The data model lives on the renderer per surface. JSON Pointer RFC 6901; Dynamic types: literal |
{path} | {functionCall}. formatString interpolation `${...}`. List templates
(children: {componentId, path} + @index). An input writes locally immediately; the agent receives data
only on action (context selection or the entire data model — there is no partial sending).

## Streaming

WHOLE envelopes are streamed (the renderer does not parse incomplete JSON). Progressive rendering at
the tree level: rendering starts once the root appears, components/paths that have not arrived yet are tolerated gracefully.
Partial LLM output is the Python SDK's concern (streaming parsers + automatic JSON healing,
payload_fixer.py: incremental slicing of model output into valid envelopes).

## Python Agent SDK

PyPI `a2ui-agent-sdk` 0.5.0 (2026-07-31), Python>=3.10, Apache 2.0. Packages a2ui_agent +
a2ui_core. Core: A2uiSchemaManager (catalogs, generate_system_prompt — the "prompt-first"
protocol), A2uiValidator (JSON Schema + protocol rules), CatalogConfig,
parse_response + streaming parsers, BasicCatalog. Version-aware (version constants,
catalog protocolVersion, capabilities schemas); there is no formal handshake — exchange of
supportedCatalogIds via transport metadata. **Dynamic catalogs** — a declared 0.5.x
feature: a trimmed catalog per assistant → its own catalogId + trimmed prompt + trimmed
validation (= our feature gating). Transport: helpers for A2A and ADK
(SendA2uiToClientToolset); our own NDJSON is an officially supported path (we write the envelopes
ourselves). **There is no LangChain integration** (LangGraph is on the roadmap; the existing path is CopilotKit
middleware via AG-UI).

## @a2ui/react

npm 0.10.2; deps: @a2ui/web_core ^0.10.5, zod ^3.25; **peer React ^19.2.7** (in 0.8.0 it was
^18||^19); ~1.0 MB unpacked. **Supports only v0.8 and v0.9** (v0_8/v0_9 imports;
there is NO v1_0 renderer). API: MessageProcessor([catalogs]) from web_core (processMessages,
surfacesMap, onSurfaceCreated/Deleted), <A2uiSurface surface/>. Mapping onto your own DS is
a first-class scenario: createComponentImplementation(Api, ({props, buildChild}) => <...>)
with a zod schema; the Generic Binder resolves Dynamic props, two-way binding (props.setValue),
actions as callbacks, injects isValid/validationErrors from checks; your own new Catalog(...)
instead of basicCatalog (the entire Basic Catalog can be reimplemented on PrimeReact while keeping the
catalogId). Maturity: the Basic Catalog is ~65% implemented in React (no Modal, DateTimeInput,
Image, Video, AudioPlayer, Icon), theming is not wired up (#977). Behavior on an unknown
component is not documented (the validator rejects it; the fallback is ours).

## Security

Declarative JSON, not code; the renderer draws only components from its own catalog.
callableFrom: rendererOnly → MUST reject the agent's callFunction (INVALID_FUNCTION_CALL).
Weak spots: no normative content sanitization, no rate/size limits, server-side
validation of the action payload = "validate against the schema"; semantic/anti-tampering
validation of responses (our intake) remains entirely ours. Client-side checks are UX, not
a security boundary.

## Interop / transports

Transport requirements: ordering, framing, bidirectional metadata. A2A 1.0 — an official
extension (envelopes in DataPart content.a2uiMessages, data model in metadata
a2uiRendererDataModel). AG-UI — envelopes as event payloads (CopilotKit >=1.61.2, the
generate_a2ui tool). MCP — envelopes in tool_result / resource subscription. Plain NDJSON/SSE/WS —
legitimate: line = envelope; capabilities placement is at our discretion.

## Versioning / stability

The envelope carries a version → the renderer can handle v0.9 and v1.0 simultaneously.
v0.8→v0.9 broke EVERYTHING (message/property renames, component flattening,
structured-output→prompt-first). v0.9→v1.0 is gentler (an RPC add-on, inline createSurface),
but there are no compatibility guarantees. Roadmap: stable v1.0 Q4 2026 (stability guarantees,
migration path, test suite, certification program). Officially: "expect changes until stable".

## Real-world experience

~16k stars; official React/Lit/Angular/Flutter renderers + ~10 community ones. The largest
production deployment — OpenClaw (via @a2ui/lit; lessons: do not fork the patterns, got stuck on v0.8,
vendoring ~1MB). Criticism: security/impersonation of agent-authored UI, "identical UIs",
incompleteness of the React catalog, fast-moving peer deps.

## Mapping onto our product (key decisions)

| Today | With A2UI | Remains ours |
|---|---|---|
| request_user_input tool, return_direct | The tool emits A2UI envelopes (or prompt-first via SchemaManager + streaming parser) | When to show UI; ending the turn |
| NDJSON chunk interactive_request | New chunk type with an A2UI envelope (NDJSON is a legitimate transport) | Multiplexing with text chunks, capabilities |
| Our surface format (9 types) | Basic Catalog: TextField/CheckBox/ChoicePicker(mutuallyExclusive=radio, multipleSelection)/DateTimeInput/Button; layout Column/Row/Card; checks instead of our rules | Conditional visibility/multi-step — turn-based rebuild (as today) |
| interactive_response intake | action envelope + sendDataModel; the server validates via A2uiValidator against the catalog schema | Semantic/anti-tampering validation — entirely ours |
| Turn-based materialization into history | Not in conflict; we materialize createSurface + action/dataModel; bonus — the standard error VALIDATION_FAILED for LLM self-correction | The whole materialization scheme |
| JSONB history | Store raw envelopes (self-contained, versioned); a converter for the old format | Storage schema, replay into MessageProcessor |
| InteractiveFeaturesConfig gating | Dynamic catalogs: per-assistant CatalogConfig → its own catalogId | Catalog registry, serving the catalog JSON |
| PrimeReact wrappers | createComponentImplementation on top of them + MessageProcessor/A2uiSurface; default components only as a reference | All component implementations, fallback, UX states |

## Main risks

1. @a2ui/react supports only v0.9 (v1.0 features unavailable on the frontend) → target
   v0.9.1 while being ready for the v1.0 renames.
2. **Peer React ^19.2.7 versus our React 18.3.1** (codemie-ui) — either upgrade React, or use the
   old @a2ui/react 0.8.x (protocol v0.8 is legacy, not an option), or use web_core directly / our own
   thin envelope renderer.
3. Stable v1.0 only in Q4 2026; v0.8→v0.9 demonstrated willingness to break everything.
4. The LangChain glue (tool → SDK parser → NDJSON) is written by us.
5. Security validation of responses is outside the protocol; our code survives almost entirely.
