# Plan — EPMCDME-13541 backend (configurable skill content limit)

**Run:** 20260723-1200-EPMCDME-13541 · **Branch:** EPMCDME-13541_configurable-skill-content-limit
**Approved decisions:** DynamicConfig storage · runtime field_validators · expose effective max on a non-admin skills endpoint.
**Test runner:** `poetry run pytest tests/` · lint `poetry run ruff check`.

Config key: `SKILL_MAX_CONTENT_LENGTH` (INT), default 30000 via `DynamicConfigService.get_typed_value_safe`.

---

## Task 1 — Effective-limit accessor
Add `get_skill_max_content_length() -> int` to `src/codemie/rest_api/models/skill.py` (or a small skills-config helper) returning `DynamicConfigService.get_typed_value_safe("SKILL_MAX_CONTENT_LENGTH", int, default=MAX_CONTENT_LENGTH)`. Keep `MAX_CONTENT_LENGTH = 30000` as the named default.
**Test-first: yes** — `tests/codemie/rest_api/models/test_skill_content_limit.py`: accessor returns 30000 when unset (monkeypatch DynamicConfigService.get_typed_value_safe), returns configured int when set, falls back to 30000 on lookup error.

## Task 2 — Runtime validators on the four model fields
Replace static `max_length=MAX_CONTENT_LENGTH` with `field_validator`s that read the accessor and raise `ValueError(f"Skill content must not exceed {limit} characters (was {len(v)}).")`:
- `SkillCreateRequest.content` (L178) · `SkillUpdateRequest.content` (L245) · `SkillInstructionsGenerateRequest.existing_instructions` (L360) · `SkillInstructionsGenerateResponse.instructions` (L389).
Keep `min_length=100` behavior intact.
**Test-first: yes** — over-limit create/update raise ValidationError whose message states the effective max; within-limit pass; increased config (e.g. 50000) lets 40000-char content pass; message includes the number.

## Task 3 — Read / omitted-content-update safety
Verify no read/response model (`SkillDetailResponse`, `SkillListResponse`) caps `content` length; adjust if any does. `SkillUpdateRequest` validator must only fire when `content` is provided (partial update omitting content always passes).
**Test-first: yes** — `SkillUpdateRequest()` with no content passes even when effective limit is low; `SkillDetailResponse` accepts 40000-char content when effective limit is 30000 (existing oversized skill remains readable); over-limit content on update is rejected.

## Task 4 — Expose effective limit to the UI
Add non-admin `GET /v1/skills/config` (declared before any `/{skill_id}` catch-all) returning `SkillConfigResponse{ max_content_length: int, min_content_length: int }` using the accessor. `Depends(authenticate)`.
**Test-first: yes** — router test: endpoint returns `max_content_length` reflecting the configured value (monkeypatched) and default 30000 otherwise.

## Sequencing
T1 → T2 → T3 → T4. Commit per task with `EPMCDME-13541:` prefix (repo squash-merges).
