# EPMCDME-13541 — Make AI Assistant Skill content length limit configurable

- **Type:** Story
- **Priority:** Major
- **Status:** In Progress (external) / Running (SDLC)
- **Assignee:** Evgenii Kurdakov
- **External ticket:** https://jiraeu.epam.com/browse/EPMCDME-13541
- **Repo scope (this run):** codemie (backend) — codemie-ui handled in a separate run
- **Branch:** _pending (Phase 2)_

## Summary

CodeMie hardcodes a 30,000-character limit for AI Assistant Skill content. Make it configurable per deployment/environment, defaulting to 30000 for backward compatibility. Backend validation must use the configured value and error messages must state the effective maximum.

## Linked Artifacts

- docs/superpowers/runs/20260723-1200-EPMCDME-13541/meta.json

## History

- 2026-07-23 — work item created (placeholder from raw_input); SDLC run 20260723-1200-EPMCDME-13541 started.
