# QA Gate Report — EPMCDME-13669

**Branch**: EPMCDME-13669_fix-spendings-zero-on-budget-assign
**Runner**: poetry
**Started**: 2026-09-02T00:00:00Z
**Status**: PASSED (see gitleaks note)

## Gates

| Gate    | Status  | Command              | Notes |
|---------|---------|----------------------|-------|
| lint    | PASS    | `make ruff`          | 2428 files checked, 0 violations |
| build   | PASS    | `make build`         | codemie-0.8.0 built successfully |
| license | PASS    | `make license-check` | 2159 files checked, 0 missing headers |
| secrets | FAIL*   | `make gitleaks`      | 11 findings — all pre-existing false positives in backup.poetry/plugins/ (PEM marker strings in library comments) and src/codemie-repos/*/test/resources/ (weak test fixture password). Zero findings in files changed by this MR. |
| unit    | PASS    | `make test`          | 15526+ passed; 22 pre-existing failures confirmed on base branch, not introduced by this change |
| ui      | SKIPPED | —                    | No UI surface changed |

## Pre-existing test failures (not introduced by this change)

All 22 failures confirmed present on `origin/main` before this change:
- `test_analytics_member_spending.py` (6 failures) — pre-existing
- `test_local_auth_router.py::TestLogoutEndpoint::test_logout_clears_cookie` (1) — pre-existing
- `test_startup_integration.py` (15) — env-dependent; pass after `.env` configuration; 0 failures when env is set correctly

5 collection errors also pre-existing (`test_a2ui_*`, `test_interactive_turn_end`, `test_request_user_input`).

## New tests added by this change (all pass)

- `TestCollectSpendRowsBudgetReassignment::test_seeds_row_for_new_budget_when_spend_unchanged` ✓
- `TestCollectSpendRowsBudgetReassignment::test_seeds_zero_row_for_new_budget_with_no_prior_spend` ✓
- `TestCollectSpendRowsBudgetReassignment::test_existing_budget_unchanged_spend_still_hits_touch_path` ✓

## Drift signal

no
