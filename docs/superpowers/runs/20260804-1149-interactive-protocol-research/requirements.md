# Requirements — replacing the custom chat interactive elements protocol

**Run:** 20260804-1149-interactive-protocol-research (research-only)
**Source:** free-form user input, 2026-08-04
**Repos:** codemie (backend), codemie-ui (frontend)

## Goal

Replace the custom-built protocol for chat interactive elements (buttons, inputs, date pickers,
radio buttons, checkboxes) with a widely adopted public technology/protocol, in order to reduce
maintenance cost and simplify further development and feature expansion.

## Scope of THIS run (research, not implementation)

1. Map the current custom solution (backend + frontend, end-to-end).
2. Research public candidate technologies; **A2UI is the priority candidate**,
   the others (MCP-UI/MCP Apps, AG-UI, Adaptive Cards, etc.) — for an honest comparison.
3. Estimate the scope and complexity of the required changes — for two replacement levels:
   minimal (wire format + renderer) and full (format + transport + agent↔UI
   interaction model); the level choice — based on the assessment results.
4. Ask the user clarifying questions during the research (escalation, not stand-in).

## Constraints / decisions (resolved with user)

- **Full replacement**: the old protocol is ultimately removed; parallel support —
  only as a temporary migration technique, if the analysis shows it is necessary. The fate
  of historical interactive messages (migration vs static fallback) is to be assessed in the analysis.
- **A2UI is the priority**: the comparison is conducted against A2UI as the baseline hypothesis.
- **Replacement depth** — an open research question (assess both levels with their cost).

## Deliverables

- Analytical document (map of the current solution, technology comparison, scope/complexity
  assessment, recommendation) — in `docs/superpowers` inside the repo.
- Jira epic/story (EPMCDME) with decomposition into tasks — based on the analysis results.

## Out of scope (this run)

- Any implementation, code changes, feature branches with code.
- Creating MRs/PRs.

## Resolved questions

- Compatibility: full replacement (see Constraints).
- Technology: A2UI is the priority; no constraints (licenses/iframe/self-hosted) declared.
- Protocol scope: to be determined in the research — assess both minimal and full options.
- Outcome: analytical document + Jira epic/story.

## Open questions (deferred to research phase)

- The fate of historical interactive dialogs under a full replacement.
- Whether an expansion of the element set is needed in the foreseeable future (selects,
  multiselects, files, tables, cards) — affects the technology choice.
- Whether the replacement affects SDK/CLI/plugins (protocol consumers beyond codemie-ui).
