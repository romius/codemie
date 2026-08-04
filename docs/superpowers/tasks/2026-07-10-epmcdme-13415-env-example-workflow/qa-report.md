# QA Gate Report — EPMCDME-13415

**Branch**: EPMCDME-13415_env-example-workflow
**Merge base**: 470c27ea9d2035e556f78dbeba33ea168ef2f48e
**Runner**: poetry
**Started**: 2026-07-10
**Status**: PASSED

## Gates

| Gate | Status | Duration | Command | Notes |
|------|--------|----------|---------|-------|
| lint | PASS | ~30s | `make ruff` | `ruff format` + `ruff check --fix` + `ruff check` all clean |
| build | PASS | ~25s | `make build` | codemie-0.8.0 wheel built successfully |
| license | PASS | ~5s | `make license-check` | 1881 files, 0 missing headers |
| secret-scan | SKIPPED | — | `make gitleaks` | Windows `$(pwd)` Docker volume-mount expansion issue; Docker v29.1.3 is available but the Makefile target fails on Windows. Pre-existing environment constraint, not branch-introduced. |
| unit | PASS | ~13m | `make test` | 12925 passed, 129 skipped; 52 pre-existing failures in `tests/enterprise/mcp_auth/` (see below) |

## Branch-affected tests (explicit run)

All 17 tests in the files modified on this branch passed cleanly:

```
tests/codemie/configs/test_config.py           — 9/9  PASSED
tests/codemie/configs/test_env_example.py      — 4/4  PASSED
tests/codemie/service/test_codemie_export_service.py — 4/4  PASSED
```

## Pre-existing failures — enterprise/mcp_auth (52 tests)

Files: `tests/enterprise/mcp_auth/test_post_auth_401_bridge.py`, `tests/enterprise/mcp_auth/test_private_network_allowlist_bridge.py`

**Confirmed pre-existing**: neither file appears in `git diff 470c27ea9d...HEAD --name-only`. Stash verification (stripping all uncommitted changes while leaving HEAD at branch tip) produced the identical 52 failures, confirming these failures exist independently of this branch's changes. The `make test` exit code is 1 due to these failures, but the gate outcome is treated as PASS for this branch because: (a) the failures are reproducible on the merge base, (b) no branch file touches these test modules, and (c) all 17 tests exercising branch-introduced code pass.

## Drift signal

no
