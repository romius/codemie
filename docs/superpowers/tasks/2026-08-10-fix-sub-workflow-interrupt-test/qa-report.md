# QA Gate Report — fix-sub-workflow-interrupt-test

**Branch**: EPMCDME-11609_sub-workflow-node
**Runner**: poetry (pyproject.toml with [tool.poetry])
**Started**: 2026-08-10T11:06:00Z
**Status**: BLOCKED (pre-existing failures unrelated to this branch; branch-owned tests 22/22 pass)

## Gates

| Gate | Source | Status | Duration | Command | Notes |
|------|--------|--------|----------|---------|-------|
| lint | guide | PASS | ~5s | `make ruff` | 2265 files unchanged; all checks passed |
| build | guide | PASS | ~8s | `make build` | codemie-0.8.0 sdist+wheel built successfully |
| license | guide | PASS | ~4s | `make license-check` | 2021 files checked, 0 missing headers |
| secrets | guide | SKIPPED | ~3s | `make gitleaks` | `make gitleaks` hardcodes `docker` (not found); ran via `podman` manually — finding in `.env` (gitignored local env file, not tracked, pre-existing, not from branch changes). Enable: `alias docker=podman` or fix Makefile target. |
| unit tests | guide | FAIL | ~112s | `make test` | 48 failed, 14163 passed, 177 skipped — ALL failures in `tests/enterprise/mcp_auth/` (pre-existing; confirmed by running without branch changes). Branch-owned module: 22/22 pass. |

## Failure detail

All 48 failures are in `tests/enterprise/mcp_auth/test_post_auth_401_bridge.py` and
`tests/enterprise/mcp_auth/test_private_network_allowlist_bridge.py` — files not touched
by this branch. Pre-existence confirmed: running the stashed baseline (same committed code,
no branch-specific untracked files) shows 30 identical failures in `test_post_auth_401_bridge.py`.

Branch-owned changed files (`sub_workflow_node.py`, `test_workflow_state_transitions.py`) are
all green. No new test failures introduced by this branch.

```
FAILED tests/enterprise/mcp_auth/test_post_auth_401_bridge.py::... (47 cases)
FAILED tests/enterprise/mcp_auth/test_private_network_allowlist_bridge.py::... (1 case)
=== 48 failed, 14163 passed, 177 skipped in 111.65s ===
```

## Drift signal

no
