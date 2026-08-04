# Design — Make AI Assistant Skill content length limit configurable (backend)

**Run:** 20260723-1200-EPMCDME-13541 · **Ticket:** EPMCDME-13541 · **Scope:** codemie backend (codemie-ui in a separate run)
**Status:** DRAFT — provisional decisions pending user approval at the `spec.approved` gate (user was away during drafting).

## Problem

`MAX_CONTENT_LENGTH = 30000` (`src/codemie/rest_api/models/skill.py:138`) is a hardcoded constant used as a compile-time `Field(max_length=...)` on four skill models. It cannot be tuned per deployment, which blocks larger skills (e.g. Philips).

## Provisional decisions (confirm at gate)

1. **Storage = DynamicConfig (runtime, DB).** Read via `DynamicConfigService.get_typed_value_safe("SKILL_MAX_CONTENT_LENGTH", int, default=30000)`. Admin-tunable at runtime via `/v1/dynamic-config`, no redeploy, safe fallback to 30000 on unset/DB error.
   - _Alternative:_ `Config(BaseSettings)` env var (deploy-time only, simpler, no DB read). Rejected as primary because runtime tunability best serves the driving use case; can be reconsidered.
2. **UI exposure = add effective max to a non-admin skills endpoint.** `/v1/dynamic-config` is admin-only, so the skill editor (regular users) needs another read path. Add the effective limit to an existing authenticated, non-admin skills-config/metadata response (exact endpoint finalized in Phase 5). The codemie-ui run consumes it.
3. **Validation approach = runtime field validators.** Replace the static `Field(max_length=MAX_CONTENT_LENGTH)` on the `content`/instructions fields with Pydantic `field_validator`s that read the configured limit at validation time and raise a message stating the effective max — mirroring the existing pattern at `skill.py:377-383` (`"Prompt must not exceed 10000 characters"`).

## Design

### 1. Effective-limit accessor
Add a single helper (e.g. `get_skill_max_content_length() -> int` in the skills models/service module) returning `DynamicConfigService.get_typed_value_safe("SKILL_MAX_CONTENT_LENGTH", int, default=30000)`. Centralizes the lookup; keeps `MAX_CONTENT_LENGTH = 30000` as the named default constant passed to the accessor.

### 2. Model validation (src/codemie/rest_api/models/skill.py)
For each of the four sites, drop the static `max_length=MAX_CONTENT_LENGTH` and enforce via `field_validator` that calls the accessor:
- `SkillCreateRequest.content` (L178-182) — create.
- `SkillUpdateRequest.content` (L245) — update. Field stays `Optional`; validator only runs when `content` is provided, so a partial update that omits `content` is never blocked by the limit (satisfies "existing oversized skills usable / non-content edits allowed").
- `SkillInstructionsGenerateRequest.existing_instructions` (L363) — AI-generate input.
- `SkillInstructionsGenerateResponse.instructions` (L392) — AI-generated output (validated against same effective max).

Error message: `f"Skill content must not exceed {limit} characters (was {len(v)})."` — states the effective maximum, per AC.

### 3. Reads must not break
The limit is enforced ONLY on create/update request models and generation. Response/read models for stored skills (list/get) carry no max_length on content, so pre-existing skills above a lowered limit remain fully readable/serializable. Confirm no read/response model imposes `max_length` on skill content.

### 4. Expose effective limit
Return `max_content_length` (int) from the chosen non-admin skills endpoint so the UI reads the live value instead of hardcoding.

## Acceptance criteria mapping

| AC | How satisfied |
|---|---|
| Configurable per deployment/environment | DynamicConfig key `SKILL_MAX_CONTENT_LENGTH` |
| Default backward-compatible (30000) | `default=30000` in `get_typed_value_safe` |
| Backend validation uses configured value | field_validators read accessor |
| Errors state effective maximum | message interpolates `{limit}` |
| Decreasing limit doesn't truncate/corrupt existing | limit only on create/update content; storage untouched |
| Existing oversized skills readable/usable | no max_length on read/response models; omitted-content updates allowed |
| Editing oversized skill blocked until compliant (backend) | update validator rejects provided content over limit with clear message (UI warning UX = UI run) |
| Tests: default/increased/exceeded/decreased | pytest matrix monkeypatching the accessor / DynamicConfig |

## Test plan (Phase 6, TDD)
- Default: no config set → limit 30000; content of 30000 passes, 30001 fails.
- Increased: config=50000 → 40000-char content passes.
- Exceeded: content over effective limit → 422/ValueError with message stating the effective max.
- Decreased-for-existing: stored skill with 40000 chars; config lowered to 30000 → GET/list returns it intact; PATCH omitting content succeeds; PATCH/PUT with 40000-char content is rejected.
- Accessor fallback: DB error → falls back to 30000 (no validation crash).

## Open questions to confirm at gate
- Config field name `SKILL_MAX_CONTENT_LENGTH` OK?
- Which non-admin endpoint carries the effective max (existing skills-config response vs new small GET)?
- Should the configured max be guarded against being set below `MIN_CONTENT_LENGTH = 100`?
- Should the AI-generate response cap (`instructions`, L392) also follow the configured limit (assumed yes)?
