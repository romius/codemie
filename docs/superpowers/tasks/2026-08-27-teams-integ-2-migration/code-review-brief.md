# Code review check — 2026-08-27-teams-integ-2-migration (2026-08-27)

**approve** · confidence: high · 6/6 in-scope findings resolved · 1 out of scope (CR-001)
Coverage: targeted verifier ✓ (6/6 in-scope blocking findings graded)

## Finding status

- CR-002 `src/codemie/rest_api/models/settings.py:318` — resolved — direct unit tests now exercise `check_ms_teams_singleton` (no longer mocked away).
- CR-003 `src/codemie/rest_api/models/settings.py:318` — resolved — partial unique index (migration f1a2b3c4d5e6) + `IntegrityError` → 409 in both create/update routers.
- CR-004 `src/codemie/rest_api/models/settings.py:318` — resolved — `setting_id: str | None = None`.
- CR-005 `src/codemie/rest_api/routers/project_settings.py:93` — resolved — permission check now runs before `validate_ms_teams_request`.
- CR-006 `src/codemie/service/settings/settings_request_validator.py:285` — resolved — `project_name` required-checked.
- CR-007 `src/codemie/service/settings/settings_request_validator.py:308` — resolved — `assistant_ids` elements `isinstance(str)`-validated before dedup/join.

## Out of scope this round

- CR-001 (commit subject missing `EPMCDME-14111` prefix) — explicitly deferred to a manual squash before merge per the task; not re-checked, not resolved, excluded from this round's decision.
