# Code review — 2026-08-27-skip-private-project-creation (2026-08-27)

**approve** · confidence: high · 2 resolved · 0 unresolved · 0 superseded
Coverage: targeted verifier ✓ (2/2 blocking findings graded)

## Finding status

- CR-001 `src/codemie/service/user/authentication_service.py:583` — resolved — new parametrized test (external/service_account) proves the DB user's excluded user_type, not the IDP's "human", reaches `reconcile_personal_project_on_email_change`
- CR-002 `src/codemie/service/user/user_profile_service.py:202` — resolved — new parametrized test (external/service_account) proves the captured excluded user_type reaches `reconcile_personal_project_on_email_change`

Both tests mirror the pre-existing "regular" test at their respective call sites, changing only the user_type under test and the expected assertion value.

## Checked and clean

commit-format ✓ · code-quality ✓ · security ✓ (carried forward from the final round; standards audit is a final-round-only actor)

Out of scope, no findings raised: `.claude/settings.json` and `.ai-run/sdlc-factory/doctor.json` (pre-existing unrelated working-tree changes swept into the diff). One pre-existing unrelated failure, `TestLoadUserForAuth::test_load_user_for_auth_found`, confirmed failing identically before this fix — not a finding.
