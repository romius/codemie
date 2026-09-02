# Plan — EPMCDME-13912 Phase 6 (subagent-driven, full scope)

Spec: `docs/superpowers/tasks/2026-08-04-epmcdme-13910-a2ui-migration/spec.md`.
Branches: codemie `EPMCDME-13912` (from main), codemie-ui `EPMCDME-13912` (from EPMCDME-13911).
Evidence: `evidence/<task>.json` in this run dir. No intermediate manual testing —
full scope lands, then review → gates → single end-to-end manual pass.

Completed inline before this plan (kept, count as done):
- T-BE1 — a2ui module (catalog+adapter), tool envelope emission, capability gate,
  prompt section. Commits 3af66d4b4, 707ae0569, c8e3c03eb. Evidence: suite green.

## Wave 1 (parallel, different repos) — DONE

### T-BE3 [DONE 7505edb07] — History: persistence + materialization (codemie)
Test-first: yes — failing tests for envelope persistence in GeneratedMessage and
materialization of createSurface/action into chat-history text.
- `GeneratedMessage.a2ui_envelopes: Optional[list[dict]]` (assistant turn) and
  `a2ui_action: Optional[dict]` + `a2ui_data_model: Optional[dict]` (user turn).
- serve_data (assistant_handlers) intercepts `{"a2ui": ...}` chunks and accumulates
  envelopes for turn persistence (mirror of interactive_request path).
- `Conversation.to_chat_history()` materializes assistant a2ui envelopes and user
  action+data model into LLM history text (new helpers in a2ui module or intake).
- ConversationResponse round-trips the new fields (camelCase out).

### T-FE1 [DONE 20303dde9] — Renderer core: factory + registry, all 18 components (codemie-ui)
Test-first: yes — vitest specs for the factory, registry completeness (18), binding
behavior, URL sanitization, unknown-component fallback.
- `src/a2ui/`: catalog config (CATALOG_ID const = v0_9 basic catalog URL), generic
  factory over `createComponentImplementation` (@a2ui/react v0_9), declarative
  registry binding all 18 Basic Catalog components to existing DS controls
  (PrimeReact wrappers; media/layout new on DS primitives), centralized
  agent-authored URL sanitization (allowlist http/https, no javascript:/data:),
  centralized unknown-component fallback, build-time manifest export
  (SUPPORTED_COMPONENTS derived from registry).

## Wave 2 — DONE

### T-BE2 [DONE 62e6df263] — Intake: action envelope + validation (codemie)
Test-first: yes.
- `AssistantChatRequest.a2ui_action: Optional[dict]` (wire envelope
  `{version, action:{name,surfaceId,sourceComponentId,context}}`) and
  `a2ui_data_model: Optional[dict]`.
- New `service/conversation/a2ui_intake.py`: find surface by surfaceId in history
  (422 unknown), double-answer protection with strict history-index re-answer
  path (port from interactive_intake), catalog resolved from the stored surface
  (never client input), semantic checks (payload size cap, values sane), then
  handler wiring in `_validate_interactive_response` path (ownership 403 first).
- Legacy kinds intake stays until X-2 removal; new path is additive.

### T-FE2 [DONE 9495b14fc] — Chat integration (codemie-ui)
Test-first: yes.
- `{"a2ui": ...}` chunk in chatGeneration `_handleChunk` → accumulate envelopes on
  the assistant message (`ChatMessage.a2uiEnvelopes`); MessageProcessor replay on
  history load; A2uiSurface render inside ChatAiInteractiveBlock (replacing
  InteractiveSurface for a2ui messages).
- Every chat request carries `a2uiSupportedCatalogs: [CATALOG_ID]`.
- Submit: build `action` envelope (+ data model) → `a2uiAction`/`a2uiDataModel`
  fields on the chat request; optimistic chip + rollback preserved.

## Wave 3 (sequential in codemie; FE-3 parallel)

### T-BE4 [DONE 531d416d7] — Assistant flag to boolean (codemie)
Test-first: yes.
- `interactive_features` → plain boolean field `interactive_enabled` on Assistant
  (JSONB column replaced; alembic migration: null→false, any-true→true; downgrade
  restores tri-group all-true/null).
- Gates read the boolean; `InteractiveFeaturesConfig` removed from gating call
  sites (model kept until X-2 removal if still referenced by intake/legacy).

### T-BE5 [DROPPED — see Phase 13] — Reversible history converter (codemie)
Built, then removed: the requirement went away, old chats are treated as invalid.
Agent attempts died twice at API-prototyping stage (SSL drop, then session limit); tree clean.
down_revision = e7f8a9b0c1d2 (head after T-BE4, verify via ScriptDirectory.get_heads()).
Test-first: yes.
- Deterministic converter old `interactive_request`/`interactive_response` →
  a2ui envelopes/action (9 legacy element types → catalog components; answers →
  data model). Reversible (round-trip test). Idempotent; counts report.
- Alembic data migration invoking the converter over conversations JSONB history.

### T-FE3 [DONE] — UX states over surfaces (codemie-ui)
Test-first: yes.
- active/submitted/stale/busy derived by surfaceId; prior answers prefilled via
  data model; re-answer via Edit (turn replacement); free-text fallback untouched;
  answer display text for the user chip.

## Wave 4 — cross-cutting

### T-X1 — BE↔FE contract test + assistant switch UI (both repos)
Test-first: yes.
- FE build emits `a2ui-manifest.json` (catalogId + SUPPORTED_COMPONENTS + hash);
  BE test asserts the advertised catalog ⊆ manifest components and catalogId
  equality (manifest snapshot committed for the BE test until the e2e harness wires
  live). Shipped without an enabled-subset knob: the backend advertises the whole
  Basic Catalog, so the comparison reads the SDK's catalog directly rather than a
  configured list.
- Assistant form switch keeps single-toggle UX bound to the boolean field.

### T-X2 — Old protocol removal + docs (both repos)
Test-first: n/a (removal; suites must stay green).
- Remove `core/interactive.py` protocol parts, legacy intake branches,
  `InteractiveElements/*`, `types/entity/interactive.ts`, old tests superseded by
  new ones; update `.ai-run/guides` interactive references.

## After Phase 6
Phase 7: code-review-orchestrator two-round on the full diff (both repos).
Phase 8: qa-gates (ruff/pytest; typecheck/lint/vitest/builds) + feature-verification.
Phase 9: actual complexity. Phase 10: handoff (branch ready; MR on user command).

## Phase 11 — manual testing, and what it cost [DONE]
The user drove the feature by hand in the local stack after Phases 6-8 had signed off.
Sixteen defects, in two classes: our own, and model-authoring failures the feature
handled badly. Evidence: `evidence/PHASE-manual-testing.json`. The product rule settled
here, after two reversals: **the form appears, minus behaviour it cannot render**, even
when the assistant's own instructions asked for that behaviour. Refusing the whole form
is not an acceptable outcome.

## Phase 12 — second review round [DONE, 11 of 13 closed]
`sdlc-factory:code-review` over the diff accumulated since the first verdict. Verdict:
`docs/superpowers/reviews/2026-08-12-a2ui-post-manual-testing/code-review-final.json`;
work recorded in `evidence/PHASE-review-fixes.json`.

Two findings were corrected by re-testing before the verdict was acted on — one
downgraded from critical after its causal claim was disproved, one restated after its
threat model turned out not to be a privilege boundary. Both corrections are recorded
alongside the findings rather than quietly dropped.

Structural changes worth carrying forward:
- `core/a2ui/envelopes.py` — the wire vocabulary, below adapter and catalog so neither
  import creates a cycle. A test fails on any hand-written message-kind access elsewhere.
- Every catalog property the backend advertises now has an observable effect in the
  renderer. The spec no longer claims the manifest gate proves that; it says plainly that
  the gate detects package drift and behavioural tests hold the rest.
- Emit-side and intake-side agree on what a valid data model is, from one shared constant.

Open, awaiting a product decision (both are consequences of deliberate choices, not
oversights): **CR-008** gated options after pruning, **CR-010** the hand-authored example
in the prompt section.

## Phase 13 — history migration dropped [DONE]
The requirement to migrate stored conversation history was withdrawn: elements recorded
under the old protocol are not converted, and those conversations are considered invalid.
Removed `core/a2ui/legacy_history_migration.py`, the `f1a2b3c4d5e6` revision (chain
re-closed `e7f8a9b0c1d2 → d1c2b3a4e5f6`), the legacy `GeneratedMessage` fields and their
tests — 1608 lines.

What "invalid" was taken to mean, and pinned by tests: the element is gone, the
conversation is not. An old record still loads with its text intact, and the history list
is re-serialized whole on every turn, so a conversation that gets a new message clears its
own legacy payload. Rollback simplifies to a release rollback: nothing was converted, so
nothing has to be undone.

## Phase 14 — the catalog draws itself [DONE]
Replaced our hand-written renderer with the SDK's own components. `registry.tsx` went from
951 lines to 88: it now records only which six components are ours and why. The rest of the
module was reorganized along the same seam — envelope helpers in one place, our renderers
in another, two pairs of near-identical modules merged, and the dead layout machinery that
survived the move deleted.

Three things had to move DOWN a layer rather than be re-implemented, because the SDK's
components and the catalog disagree: the default `variant`, a scalar selection seed and the
empty text value are normalized in the envelopes, where the server already enforces the
same rules. Two catalog properties the SDK ignores (`accessibility`, `validationRegexp`)
are supplied from outside its implementations.

Styling is by CSS variables plus structural rules, because the published `@a2ui/react`
build ships CSS modules with empty class maps — its own stylesheet matches nothing. Its
selectors are bare words (`.button`, `.label`), so importing it would restyle the app.

## Phase 15 — configuration and naming [DONE]
Two follow-ups from reading the manifest mechanism aloud:

- The vendored manifest was renamed `frontend_manifest.json` → `a2ui-manifest.json`, the
  same name the frontend generates. The copy is made by hand, and an identical name is what
  makes "is this the same file?" answerable by a plain diff. The copy step is now written
  down in spec §3 as a numbered procedure, and the generator prints the reminder after
  writing the file — documentation is read in advance, that line appears at the moment it
  matters.
- Backend configuration is gathered into `core/a2ui/config.py`, the mirror of the
  frontend's `config.ts`: versions, catalog id, message-kind names, the action envelope's
  key set and all six limits, plus the SDK loading the id is derived from. It also holds
  `client_supports_catalog`, replacing two separate `CATALOG_ID in declared` checks that
  had to agree but were not forced to.

## Handed to the team [OPEN]
`poetry.lock` still has to be regenerated: the branch adds `a2ui-agent-sdk` to
`pyproject.toml` and the lock was never updated, which is why every CI run fails in `build`.
It cannot be done from a machine without GCP Artifact Registry credentials (`codemie-enterprise`
is pinned to that source), and hand-editing it would mean resolving `a2a-sdk`, `a2ui-core`,
`antlr4-python3-runtime`, `google-adk` and `google-genai` against the existing Google tree by
hand — the lock file's job. Recorded in the MR description.

## Not part of this change
- Surfacing `tool_errors` in chat: a platform-wide change, awaiting a product decision.
