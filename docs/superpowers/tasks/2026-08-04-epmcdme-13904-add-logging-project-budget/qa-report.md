# QA Gate Report — EPMCDME-13904

**Branch**: EPMCDME-13904_add-logging-project-budget
**Runner**: poetry
**Started**: 2026-08-04T12:00:00Z
**Status**: PASSED

## Gates

| Gate | Source | Status | Duration | Command | Notes |
|------|--------|--------|----------|---------|-------|
| lint | guide | PASS | ~30s | `make ruff` | All checks passed — 2226 files unchanged |
| build | guide | PASS | ~60s | `make build` | codemie-0.8.0 .tar.gz and .whl built successfully |
| license | guide | PASS | ~20s | `make license-check` | Checked 1988 files, 0 missing license headers |
| secrets | guide | SKIPPED | — | `make gitleaks` | Self-skipped: Docker/Podman/Apple Containers unavailable; enable by starting a container runtime |
| unit | guide | PASS | 749s | `make test` | 13739 passed, 47 failed (pre-existing), 163 skipped — all 47 failures are in `tests/scripts/` and `tests/codemie_tools/`, none in changed files (`tests/codemie/service/project/`, `tests/codemie/service/budget/`) |
| coverage | guide | SKIPPED | — | `make coverage` | Not requested |
| sonar | guide | SKIPPED | — | `make sonar-local` | Not configured locally |
| ui | guide | SKIPPED | — | N/A | No UI surface changed |
| pre-commit-hook | hook | SKIPPED | — | `bash scripts/git-hooks/pre_commit.sh` | Covered by codemie-pre-commit hook (ruff + license); ruff and license already passed above |
| gitleaks-hook | hook | SKIPPED | — | `bash scripts/git-hooks/validate_secrets.sh` | Docker-dependent; same reason as secrets gate |

## Failure detail

47 pre-existing failures in unrelated test suites:
- `tests/scripts/test_commit_msg_hook.py` — 14 failures
- `tests/scripts/test_pre_push_hook.py` — 8 failures
- `tests/scripts/test_ruff_staged_hook.py` — 6 failures
- `tests/scripts/test_pre_commit_fast_hook.py` — 2 failures
- `tests/codemie_tools/data_management/file_system/test_file_system_tools.py` — 1 failure
- `tests/codemie_tools/file_analysis/pptx/test_pptx_toolkit.py` — 1 failure
- Other scripts/tools — 15 failures

None of the changed service files appear in the failure list. These failures pre-exist on `main` and are unrelated to the logging additions.

## Drift signal

no
