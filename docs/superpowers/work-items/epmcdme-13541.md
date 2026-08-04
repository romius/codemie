# EPMCDME-13541 — Make AI Assistant Skill content length limit configurable

- **Type:** Story
- **Priority:** Major
- **External Ticket:** https://jiraeu.epam.com/browse/EPMCDME-13541
- **Assignee:** Evgenii Kurdakov
- **Reporter:** Filip Stastny (Contractor)
- **Labels:** AI-Generated, AI/Run, codemie_feedback
- **External sync:** resolved (atlassian MCP adapter)
- **Repo scope:** Cross-repo. Backend (codemie) tracked by run 20260723-1200-EPMCDME-13541; frontend (codemie-ui) in a separate run.

## Summary

CodeMie hardcodes a 30,000-character limit for AI Assistant Skill content. Make it configurable per deployment/environment, default 30000 (backward compatible). Validation must use the configured value and error messages must state the effective maximum. Decreasing the limit must not corrupt or truncate existing skills.

## Acceptance Criteria (from ticket)

- Skill content length limit is configurable per deployment/environment.
- Default limit remains backward compatible if no custom value is configured.
- UI and backend validation use the configured value, not hardcoded 30000.
- Validation errors clearly state the effective maximum length.
- Decreasing the configured limit does not truncate, crop, or corrupt existing skills.
- Existing skills above the new limit remain readable/usable as-is.
- Editing an existing oversized skill shows a warning and prevents saving until it complies with the current limit.
- Tests cover default limit, increased limit, exceeded-limit validation, and decreased-limit behavior for existing skills.

## Linked Artifacts

- docs/superpowers/runs/20260723-1200-EPMCDME-13541/requirements.md
- docs/superpowers/runs/20260723-1200-EPMCDME-13541/meta.json

## History

- 2026-07-23 — work item created from external ticket EPMCDME-13541 (atlassian adapter, resolved). Backend SDLC run 20260723-1200-EPMCDME-13541 started. External sync: resolved.
