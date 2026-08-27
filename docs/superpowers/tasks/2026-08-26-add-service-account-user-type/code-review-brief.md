# Code review — 2026-08-26-add-service-account-user-type check round (2026-08-27)

**approve** · confidence: high · 5/5 findings graded · 0 unresolved
Coverage: targeted verifier ✓ (5/5 blocking findings from the prior round graded)

## Finding status

- CR-001 — resolved (decision) — `src/codemie/rest_api/security/user.py` — user explicitly dismissed; service_account's inherited internal-user access via `is_external_user` left unchanged by deliberate choice, not oversight.
- CR-002 — resolved — `src/codemie/rest_api/security/user_type_validator.py`, `src/codemie/enterprise/idp/dependencies.py` — stale "regular'/'external'" 401 text now built from `sorted(VALID_USER_TYPES)`; exception type, status code, control flow unchanged; 6 pinned tests updated.
- CR-003 — resolved — `src/codemie/service/user/user_management_service.py` — admin API now checks `VALID_USER_TYPES` imported from the validator; the local-mode/admin-only editability gate and admin-status resolution are confirmed untouched by this diff; no circular import; new regression test added.
- CR-004 — resolved — `src/codemie_enterprise/idp/user_type.py` — `InvalidUserTypeError` detail/log text now derived from `sorted(VALID_USER_TYPES)`; error signature and help text unchanged.
- CR-005 — resolved — `tests/idp/test_user_type.py` (new) — direct unit tests added covering lowercase/uppercase/whitespace-trimmed `service_account` handling plus the multi-type detail-text assertion.

## Scope

No scope expansion: the base-repo diff touches only the 5 targeted source/test files (plus 2 pre-existing, excluded, unrelated files); the enterprise-repo diff touches only `user_type.py` and the new test file. Nothing beyond the four targeted patch findings and CR-001's decision.
