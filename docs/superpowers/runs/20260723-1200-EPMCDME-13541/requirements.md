# Requirements — 20260723-1200-EPMCDME-13541

**Source**: ticket:https://jiraeu.epam.com/browse/EPMCDME-13541
**Work Item**: docs/superpowers/work-items/epmcdme-13541.md
**Original input**: |
  https://jiraeu.epam.com/browse/EPMCDME-13541 — "Make AI Assistant Skill content length limit configurable" (Story, Major, In Progress)

## Goal

Make the AI Assistant Skill content-length limit (currently the hardcoded 30,000-character `MAX_CONTENT_LENGTH`) configurable per deployment/environment on the CodeMie backend, defaulting to 30000 for backward compatibility.

## Scope (this run = BACKEND / codemie only)

In scope:
- Introduce a configurable maximum skill-content length in the backend config (`codemie/src/codemie/configs/config.py`), default 30000.
- Skill create/update validation (`codemie/src/codemie/rest_api/models/skill.py`) uses the configured value instead of a hardcoded constant.
- Validation error messages state the effective maximum length.
- Reads/serialization of existing skills whose content exceeds the current limit must NOT fail (limit applies to create/update of content, not to reading stored data).
- Expose the effective limit to clients (so the UI can consume it rather than hardcoding its own).
- Backend tests covering: default limit, increased limit, exceeded-limit rejection, and decreased-limit behaviour for pre-existing oversized skills (read OK, non-compliant update blocked).

Deferred to the separate codemie-ui run:
- UI validation using the configured value, UI validation-message wording.
- UI "editing an existing oversized skill shows a warning and prevents saving" UX.

## Acceptance Criteria

From the ticket (backend-relevant subset marked ✅ in-scope, 🔶 UI-run):
- ✅ Skill content length limit is configurable per deployment/environment.
- ✅ Default limit remains backward compatible (30000) if no custom value is configured.
- ✅ Backend validation uses the configured value, not hardcoded 30000. 🔶 (UI validation — UI run.)
- ✅ Validation errors clearly state the effective maximum length.
- ✅ Decreasing the configured limit does not truncate, crop, or corrupt existing skills.
- ✅ Existing skills above the new limit remain readable/usable as-is (backend must not reject reads/list/get of oversized stored skills).
- ✅ Backend: editing an existing oversized skill is blocked when the submitted content still exceeds the current limit (returns a clear error stating the effective max). 🔶 (UI warning UX — UI run.)
- ✅ Tests cover default limit, increased limit, exceeded-limit validation, and decreased-limit behavior for existing skills.

## Context

- Constant lives at `codemie/src/codemie/rest_api/models/skill.py:138` (`MAX_CONTENT_LENGTH = 30000`), consumed by Pydantic `Field(max_length=...)` on the skill `content` field at L181, L245, L363, L392 (multiple request models — create/update/partial variants).
- Backend config/settings module: `codemie/src/codemie/configs/config.py` (per repo Configuration guide: `.ai-run/guides/development/configuration-patterns.md`).
- Business driver: Philips is blocked by the fixed 30000 limit for larger structured skills.
- "per deployment/environment" → a single global config value (env var / settings field), not per-project or per-user.
- Backend uses FastAPI + Pydantic/SQLModel. A Pydantic `Field(max_length=...)` uses a class-definition-time constant, so making it configurable at runtime likely requires moving from a static `Field(max_length=...)` to a runtime validator that reads the configured value — this is a design decision for Phase 4.

## Open questions

1. **Config field name & mechanism.** What env var / settings field name should hold the limit (e.g. `SKILL_MAX_CONTENT_LENGTH`)? Should it live on the existing settings object in `config.py` and be overridable via environment variable following the repo's configuration-patterns guide?
2. **Exposing the limit to the UI.** Is there an existing config/settings endpoint the UI already reads (so we add the limit there), or should a new field be added to an existing skills-related endpoint response? (Needed so the UI run can consume the effective value.)
3. **Validation placement.** The current limit is enforced via Pydantic `Field(max_length=MAX_CONTENT_LENGTH)` at class-definition time. Confirm the preferred approach for a runtime-configurable limit: a Pydantic field/model validator that reads the settings value at validation time (vs. keeping a static Field). This affects all four request models at L181/245/363/392.
4. **Update semantics for oversized existing skills.** For a PATCH/partial update where `content` is omitted, the update must be allowed even if stored content exceeds the current limit. When `content` IS provided, it must satisfy the current limit. Confirm this is the intended rule.
5. **Lower bound / MIN_CONTENT_LENGTH.** `content` also has `min_length=100`. Should the configurable max be validated to stay above the min (guard against misconfiguration), and what happens if the configured max is set below 100?
