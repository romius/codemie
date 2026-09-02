# Migrating the custom interactive chat elements protocol to A2UI — analysis report

**Run:** 20260804-1149-interactive-protocol-research · 2026-08-04
**Repositories:** codemie (backend), codemie-ui (frontend)
**Status:** draft for sign-off (research-only run, implementation out of scope)

Supporting materials: [backend-map.md](backend-map.md), [frontend-map.md](frontend-map.md),
[tech-landscape.md](tech-landscape.md), [a2ui-deep-dive.md](a2ui-deep-dive.md),
[complexity.json](complexity.json).

---

## 1. Summary and recommendation

**Recommendation: migrate to A2UI (Google), targeting protocol v0.9.1** (the only
version actually supported by `@a2ui/react`), isolated behind an adapter layer and kept
ready for the v1.0 renames (stable expected in Q4 2026). The replacement is complete
(the custom protocol is removed), and conversation history is migrated into A2UI
envelopes via a converter.

A2UI is the target technology: a public protocol that covers our scenario end to end
("the LLM declaratively asks for controls in the chat feed, rendered with our own
design-system components") and provides a component catalog, validation, streaming, and
official Python/React SDKs. (The alternatives reviewed during research are covered in
[tech-landscape.md](tech-landscape.md).)

**React fork in the road (resolved with the user):** `@a2ui/react` 0.10.x requires peer
**React ^19.2.7**, while codemie-ui is on **React 18.3.1**. Decision: **the React 18→19
upgrade is a prerequisite sub-task (EPMCDME-13911)**; the main migration follows it
using the official `@a2ui/react` (`createComponentImplementation` on top of our
PrimeReact wrappers — the Generic Binder provides Dynamic-prop resolution, two-way
binding, and isValid/validationErrors for free). The alternative — a thin custom
renderer on top of `@a2ui/web_core` without upgrading React — was considered and
rejected in favor of the official renderer.

**Honest benefit assessment.** The current implementation is compact (~2,250 lines of
product code across both repos) and already "A2UI-like" in spirit. The replacement does
not reduce maintenance to zero:
- **Becomes standard** (we stop maintaining it ourselves): the element vocabulary and
  its schema, client-side validation, value binding, request/response format, protocol
  documentation (public spec + community + examples), LLM prompt generation,
  partial-JSON parsing.
- **Stays ours**: multiplexing envelopes into the NDJSON stream, turn-based turn
  semantics, server-side semantic/anti-tampering validation of answers (the protocol
  does not provide it), UX states (active/submitted/stale, re-answer, optimistic chip),
  glue with the agent runtimes (LangChain AgentExecutor and LangGraph — see §3),
  the catalog definition and feature gating, history materialization for the LLM.
- **Gets added**: tracking the evolution of a pre-1.0 protocol (v0.9→v1.0 migration in
  Q4 2026).

The benefit is greatest precisely for the stated plans: **complex forms** (Slider,
number/obscured TextField, filterable ChoicePicker, Tabs/Card/Modal, list templates —
come "for free" from the catalog) and **interop** (the backend already has an A2A
handler; A2UI has an official A2A extension — interactivity becomes available to
external A2A clients almost without extra work).

## 2. Current state (summary)

Feature EPMCDME-13259; both sides are built around an element registry as the single
source of truth:

- **Backend** (~1,000 lines + ~1,430 test lines): `core/interactive.py` (695 lines —
  models for 9 element types, validation, catalog, prompt), the `request_user_input`
  tool (return_direct=True, emits an `{"interactive_request"}` chunk into NDJSON),
  intake in `interactive_intake.py` (double-answer protection, server-side
  re-validation), turn-based materialization of request+answer into history text, JSONB
  persistence, gating: per-assistant `interactive_features` + the platform flag
  `interactiveElements`.
- **Frontend** (~1,250 lines + ~1,436 test lines): mirrored types, registry +
  elementHandlers + InteractiveSurface on top of PrimeReact wrappers,
  active/submitted/stale states, re-answer via Edit, optimistic chip with rollback,
  derived "answered" state by request_id. No schema validation of the incoming protocol
  (guards + ErrorBoundary).

Weak points of the home-grown approach: extending the catalog means manual synchronized
BE+FE work (an unknown type crashes the block into the ErrorBoundary), no public
spec/examples, custom validation rules and binding, legacy response kinds are dead
weight.

## 3. Target architecture

### Backend (codemie)

1. **Emission**: the `request_user_input` tool remains the birthplace of UI (the agent
   decides when to show it, as today), but its args schema and output move to A2UI: the
   tool builds catalog components and emits `createSurface` + `updateComponents` +
   `updateDataModel` envelopes (v0.9 semantics) as a new NDJSON chunk type
   (`{"a2ui": <envelope>}`); `return_direct` and turn-based turn completion are
   preserved. Important: chat runs on **two agent runtimes** — LangGraph by default
   (`LangGraphAgent` → `langgraph.prebuilt.create_react_agent`; selection in
   `assistant_service.py:575`: `ENABLE_LANGGRAPH_AITOOLS_AGENT` (default true) and
   `not is_react`) and the classic LangChain `AgentExecutor` (`AIToolsAgent`) for
   react LLMs or with the flag off. The tool is a LangChain `BaseTool` and works in both
   runtimes; the contract "return_direct ends the turn in langgraph create_react_agent"
   is pinned by `test_interactive_turn_end.py` and carries over to the new tool as is.
   The SDK's prompt-first mode (SchemaManager + streaming parser with JSON healing) is
   a phase-two option, not required for the migration.
2. **Catalog and gating**: **one static catalog with all 18 Basic Catalog
   components**, defined in code — the official Basic Catalog `catalogId` (taken from
   SDK constants, nothing self-hosted), one prompt section, one validation schema
   (both generated by the SDK). Media components (Image/Video/AudioPlayer/Icon) ship
   with sanitization of agent-authored URLs on our side (the spec does not provide
   it). Gating: the two existing binary gates unchanged — the per-assistant switch
   (whether the tool is registered and the prompt injected — same UI as today) and
   the platform `interactiveElements` flag — plus a third, new one: **catalog
   capability declaration**. The chat request carries an optional
   `a2ui_supported_catalogs` field (derived from the frontend registry); the tool is
   registered only when the active catalogId is declared. Requests without it —
   stale open tabs during the atomic release, IDE and other non-A2UI clients — get
   no emission, and the agent degrades gracefully to plain-text questions. This is a
   binary gate only (no dynamic catalog assembly) and lays the wire format for full
   negotiation later. The three-group
   `InteractiveFeaturesConfig` granularity (already collapsed to a single switch in
   the UI) is removed together with the old protocol; the assistant column migrates
   to a plain boolean (null → off, any group enabled → on). Per-assistant catalog
   subsets via the SDK's `CatalogConfig` (dynamic catalogs) remain a documented
   extension reserve — supported out of the box, not built in S-1.
3. **Intake**: `interactive_response` → the `action` envelope (+ the full data model
   via `sendDataModel: true`). Server-side validation: `A2uiValidator` (catalog schema)
   + our semantic/anti-tampering checks (values within options, size limits,
   double-answer protection, ownership) — carried over almost as is.
   **Legacy kinds (`action`/`choice`/`form`) are removed** — the frontend always sends
   a single answer shape (today `submit`, becomes the `action` envelope), free text
   remains the fallback; dead validation branches are not carried over. Intake dispatch
   is designed to be extensible for future answer types/consumers.
4. **History**: materialization of `createSurface` envelopes and `action`+data model
   into text for the LLM (replacing the current materialize_* helpers). Storage — raw
   envelopes in JSONB (self-contained, versioned). The standard `error
   VALIDATION_FAILED` becomes LLM feedback for self-correcting invalid UI.
5. **Data migration**: a converter from the old format to envelopes (deterministic
   mapping of the 9 types onto the catalog; answers → data model), a one-off migration
   of the JSONB conversation history.

### Frontend (codemie-ui)

0. **Prerequisite (separate story)**: React 18.3.1 → 19.x upgrade (required by peer
   `@a2ui/react` ^19.2.7) with a full UI regression pass. Compatibility verification of
   key dependencies (primereact 10.9.x, react-datepicker, valtio, etc.) is part of that
   story.
1. **Core**: the official `@a2ui/react` (v0_9 import path) + `@a2ui/web_core` own
   all protocol machinery (MessageProcessor, surfacesMap, data binding, checks);
   **visual controls come from the existing product component library** — the
   PrimeReact-based inputs, buttons and date picker already used in chat
   (RadioButton/Checkbox/Select/DatePicker/Input/Button). Our code is thin
   declarative bindings via `createComponentImplementation` connecting the renderer
   to that library across all 18 Basic Catalog components; the few visuals the
   library lacks (media, Slider, Tabs/Modal shells) are added on the same
   design-system primitives. The renderer's bundled demo implementations are not
   used (the set is ~65% complete and not our DS). ChoicePicker: mutuallyExclusive →
   RadioButton/Select, multipleSelection → Checkbox group.
   **Generic wrapper (design decision):** instead of 18 hand-written implementations —
   a thin factory + a declarative mapping registry (matches the project's data-driven
   registry pattern). Three group wrappers: *inputs* (TextField, CheckBox,
   DateTimeInput, Slider — uniform label/value/setValue/validation-error handling; per
   component only "which PrimeReact control + prop translation", ~5-15 config lines),
   *layout* (Row, Column, Card, Divider — container around `buildChild`), *media*
   (Image, Video, AudioPlayer, Icon — with **URL sanitization centralized in one
   place**). ~4-5 components stay bespoke (ChoicePicker variant branching, Modal,
   Tabs, List templates, Button action semantics). The factory also centralizes the
   unknown-component fallback and the test strategy (test the factory hard once +
   a light snapshot per registry row). Future catalog additions and v0.9→v1.0 prop
   renames become registry/factory edits instead of 18 file edits — a direct pre-1.0
   risk mitigation. Constraint: the factory must stay a thin mapping table, not grow
   into a meta-framework on top of A2UI.
   **Bindings (decision): all 18 catalog components are bound to the existing
   component library via the factory** (no stock visuals at runtime); the renderer's
   stock `basicCatalog` serves as a reference only. Fully relying on
   stock implementations is not viable anyway (the React catalog is ~65% complete —
   DateTimeInput, Modal, and all media components are missing; theming is not wired
   up, #977). A hybrid (reusing stock implementations for DS-neutral layout/Text) was
   considered and rejected: it saves only ~100-150 trivial lines while adding an
   upstream-drift surface (stock implementations change with pre-1.0 releases), a
   second source of visual truth, and a dependency on undocumented export
   granularity. Own implementations also keep the entire catalog visually native to
   the design system.
2. **Chat integration**: new chunk type → MessageProcessor; the supported catalog
   id (`a2ui_supported_catalogs`, derived from the registry) is declared in every
   chat request; `ChatAiInteractiveBlock`
   stays as the product wrapper (disabled/submitted/stale, re-answer, optimistic chip)
   around an A2UI surface instead of the custom InteractiveSurface. History replay =
   replaying the persisted envelopes.
3. **Fallback**: unknown component/catalog → a text placeholder (behavior is not
   defined by the spec — we keep our ErrorBoundary pattern).
4. Removal of `src/components/InteractiveElements/*` and `types/entity/interactive.ts`
   after the switch.

### What the "whole mechanism" depth buys us

The envelope format, component model, binding, validation, RPC semantics (v1.0), and
error format become standard. Transport remains our NDJSON (a legitimate A2UI
transport; the spec only requires ordering/framing/metadata). A full transport
replacement with AG-UI is deliberately NOT recommended now: it would be a second
migrating protocol in the same project with its own risks, while A2UI over our NDJSON
already provides standard agent↔UI semantics. If we later want AG-UI/A2A — A2UI
officially rides on top of both.

**BE/FE synchronization (in scope):** invariant `ENABLED_COMPONENTS (BE) ⊆
registry (FE)`, same catalogId, pinned SDK versions on both sides. Configs are code:
a `CatalogConfig` constant module on the backend, the implementation registry on the
frontend (`SUPPORTED_COMPONENTS` and the declared catalog id are derived from it).
The frontend build emits `a2ui-manifest.json`; a CI contract test in the e2e harness
compares it with the backend-advertised catalog (subset + id equality) and checks
schema-hash equality of the pinned SDK packages. Rollout order for catalog changes:
frontend first (superset is valid), backend enables second. Published catalogs are
immutable (a changed composition or version = a new catalogId) so persisted history
stays resolvable forever.

**Extensibility (design for it, do not include in S-1):** A2A exposure of
interactivity — envelope assembly/parsing is isolated from the transport (a dedicated
adapter module on each side), so that a future "A2UI over A2A" story (official
extension: envelopes in DataPart, capabilities in the agent card) becomes an add-on
without refactoring the core. Likewise, intake dispatch stays open for new answer
types/consumers (IDE, external hosts). Further reserves: full runtime capabilities
negotiation (composition fingerprint in the request → effective catalog as the
intersection, via SDK dynamic catalogs) building on the shipped minimal declaration;
graceful degradation for unknown components (layout → render children flat; input →
optional generic control by bound value type; media → never auto-render).

## 4. Scope and complexity assessment

Initiative complexity: **30/36 (XL)** — see complexity.json.

**Packaging (final, in Jira): one story with two sub-tasks — EPMCDME-13910
"Migrate interactive chat elements from custom protocol to A2UI".** The protocol
cannot be delivered piecemeal: the switch is atomic (BE and FE in sync, two MRs under
one ticket).

* **Sub-task 1 — EPMCDME-13911**: React 18→19 upgrade in codemie-ui (prerequisite,
  starts with a dependency-compatibility spike, ~1-3 weeks).
* **Sub-task 2 — EPMCDME-13912**: the entire A2UI migration (backend + frontend +
  data migration + old protocol removal), ~7-10 weeks for a BE+FE pair. The work
  breakdown below lives in its description as a checklist, not as separate tickets.

Internal work plan of EPMCDME-13912 (sizes S≈1-2 days, M≈3-5 days, L≈1-2 weeks):

| # | Work package | Repo | Size |
|---|---|---|---|
| BE-1 | A2UI SDK: integrate a2ui-agent-sdk, catalog(s), envelope emission from the tool, new NDJSON chunk type | codemie | **L** |
| BE-2 | Intake: action envelope + sendDataModel, A2uiValidator validation + porting semantic/anti-tampering checks | codemie | **M** |
| BE-3 | History: envelope materialization into LLM text, envelope persistence, error VALIDATION_FAILED feedback | codemie | **M** |
| BE-4 | Static full-catalog definition; migrate the assistant flag to a plain boolean (drop InteractiveFeaturesConfig granularity), keep the platform flag | codemie | **S** |
| BE-5 | Data migration: old-format → envelope converter, JSONB conversation history migration | codemie | **M** |
| FE-1 | Core: @a2ui/react (v0_9) + own Catalog: all 18 Basic Catalog components via a generic factory + mapping registry (~4-5 bespoke), inputs on PrimeReact wrappers | codemie-ui | **L** |
| FE-2 | Chat integration: new chunk → MessageProcessor, history replay, unknown-component fallback | codemie-ui | **M** |
| FE-3 | UX states on top of A2UI: active/submitted/stale, re-answer, optimistic chip, answer display text | codemie-ui | **M** |
| X-1 | End-to-end acceptance: e2e (SDK test harness), atomic joint BE+FE release, rollback plan (reverse history converter) | both | **M** |
| X-2 | Removal of the old protocol (BE+FE) + guides/docs updates | both | **S-M** |

Overlapping sequence: EPMCDME-13911 → (BE-1→BE-2/3/4 ∥ FE-1→FE-2/3) → BE-5 → X-1 → X-2.

Migration test debt: ~2,900 lines of existing protocol tests are rewritten against
A2UI semantics (accounted for in the package sizes).

**Code volume forecast.** Total owned code stays roughly flat to modestly lower
(~-10…-20%), but its nature changes. Backend shrinks noticeably (~1,000 → ~600-750
own lines): element models, surface schema validation, dynamic args schema, prompt
generation, and partial-JSON parsing move to the SDK; what stays ours is semantic
intake validation (~150-200), tool/emission glue (~100-150), history materialization
(~100), catalog infrastructure (~100-200 new), media URL sanitization (~50-100 new),
plus the one-off history converter (~150-300). Frontend stays about flat (~1,250 →
~1,100-1,300): ~800 lines of protocol machinery are replaced by the renderer, while
the catalog doubles (9 → 18 components); the generic factory + registry keeps the
implementations at ~450-600 lines instead of ~500-900 hand-written. Test volume stays
comparable. The real win is the marginal cost per change: a new element becomes one
registry row instead of a synchronized BE model + validation + prompt + FE type +
handler + tests on both sides.

## 5. Risks and mitigations

| Risk | Likelihood/impact | Mitigation |
|---|---|---|
| Breaking changes before stable v1.0 (precedent: v0.8→v0.9 broke everything) | Medium / high | Target v0.9.1 (what the renderer supports); adapter layer: envelopes are built/parsed in a single module on each side; envelopes in the DB carry their version — history does not break |
| Peer React ^19.2 in @a2ui/react | Materialized / medium | Decision: React 18→19 upgrade as a separate prerequisite story (S-0); the main migration starts after it |
| Incomplete React implementation of the Basic Catalog (~65%) | High / low | We do not use the default components — all implementations are ours on top of PrimeReact |
| No LangChain/LangGraph integration in the SDK | Materialized / medium | We write the "LangChain BaseTool → SDK validator → NDJSON" glue ourselves (thin); a single tool covers both runtimes (LangGraph create_react_agent consumes LangChain tools); the return_direct contract is already pinned by a test; prompt-first — later |
| Security: the spec provides no server-side validation/sanitization | Constant / high | Port our intake checks as is; client-side checks are UX only; for media components (Image/Video/AudioPlayer) — sanitization/allowlist of agent-authored URLs |
| Atomic release without a transition flag | Medium / high | Rollback = release rollback + reverse history converter (the converter is designed to be reversible); thorough X-1 acceptance on staging before the production data migration |
| Pre-1.0 SDK version drift (Python 0.5.0 / web_core 0.10.x) | Medium / medium | Pin versions, contract tests for envelopes between BE and FE |

## 6. Decisions made during the research (with the user)

1. Complete replacement, no prolonged parallel support.
2. A2UI is the priority candidate; the pre-1.0 risk is accepted: adopt now.
3. Depth: the whole agent↔UI interaction mechanism (NDJSON transport remains as the
   carrier channel — see §3).
4. Outlook: complex forms (multi-step — via turn-based rebuilds; dependent fields —
   custom catalog functions; more input types — from the catalog).
5. History: data migration (converting the old format into envelopes).
6. Packaging (final): one story **EPMCDME-13910** with two sub-tasks —
   **EPMCDME-13911** (React 18→19 upgrade, prerequisite) and **EPMCDME-13912** (the
   entire A2UI migration; the work breakdown is a checklist inside it). Finer-grained
   sub-tasks initially created (13913…13921) were closed as Won't Fix and consolidated
   into 13912 per the user's request. The feature cannot be delivered piecemeal — the
   protocol switch is atomic (two MRs: codemie + codemie-ui).
7. React path: upgrade to React 19 as a prerequisite; the main migration uses the
   official `@a2ui/react` (the web_core-without-upgrade option was rejected).
8. Jira: created on 2026-08-04 — story EPMCDME-13910 with sub-tasks EPMCDME-13911
   and EPMCDME-13912 (supersedes the earlier "no tickets now" decision).
9. First-wave catalog: **the full Basic Catalog (18 components)** in S-1, including
   media/layout (with agent-authored URL sanitization).
10. Rollout: **atomic release** without a protocol-selection transition flag; the
    existing `interactiveElements` flag remains the gate for the feature as a whole;
    rollback — release rollback + reverse history converter.
11. A2A exposure and other external consumers: **design for it (envelope isolation
    from transport, extensible intake), do not include in S-1** — a separate story in
    the future.
12. Legacy intake kinds (`action`/`choice`/`form`): **removed** during the migration.
13. Frontend rendering: the official `@a2ui/react` renderer wired to the
    **existing product component library** — protocol machinery is the library's,
    visuals are reused design-system controls, our code is thin bindings (generic
    factory + mapping registry) covering all 18 catalog components; stock
    `basicCatalog` visuals are a reference only. The earlier hybrid option (stock
    implementations for DS-neutral components) was revisited and rejected as
    complexity without payoff (upstream drift, two sources of visual truth,
    undocumented export granularity — vs ~100-150 trivial lines saved).
14. Catalog model: **one static catalog with all 18 components**; gating stays as
    today — the per-assistant boolean switch (UI unchanged) + the platform
    `interactiveElements` flag; the three-group `InteractiveFeaturesConfig`
    granularity is removed (the column migrates to a boolean). Per-assistant catalog
    subsets (`CatalogConfig` dynamic catalogs) are a documented future reserve, not
    part of S-1.
15. Catalog capability declaration (minimal handshake): the chat request declares
    supported catalog id(s); the backend emits interactive UI only to clients that
    declared the active catalog — protects the atomic-release window (stale tabs)
    and non-A2UI clients degrade to plain text. Full negotiation (composition
    intersection) stays a future extension.
16. catalogId: use the official Basic Catalog identifier from SDK constants
    (nothing self-hosted); a custom id is minted only if we ever trim the
    composition or add custom components.

## 7. Open questions (for the story spec)

1. Exact scope of the React upgrade (EPMCDME-13911) — requires a
   dependency-compatibility spike of codemie-ui against React 19; the spike is the
   first step of that sub-task.
