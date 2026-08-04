# QA Gate Report — EPMCDME-6577

**Branch**: EPMCDME-6577_ghe-github-base-url
**Runner**: poetry (guide-first via Makefile)
**Started**: 2026-07-22T22:35:00+02:00
**Status**: BLOCKED (env pre-existing) — task-scope PASSED

## Gates

| Gate  | Source | Status | Duration | Command | Notes |
|-------|--------|--------|----------|---------|-------|
| lint (ruff) | guide | PASS | ~1.5s | `make ruff` | All checks passed; 2184 files unchanged. |
| license headers | guide | PASS | ~0.7s | `make license-check` | 1950 files checked, 0 missing headers. |
| build (poetry) | guide | PASS | ~2.0s | `make build` | codemie-0.8.0 sdist + wheel built. |
| gitleaks | guide | FAIL (pre-existing, out of scope) | ~11.2s | `make gitleaks` | 1 leak reported on `.env` (`dial-ns336wly...` = local DIAL API key). `.env` is tracked in the repo but is NOT in this branch's diff — leak is from a pre-existing local uncommitted edit, documented in user memory (codemie-env-secrets-not-committed.md). No file touched by EPMCDME-6577 triggers gitleaks. |
| tests (pytest) | guide | FAIL (pre-existing, out of scope) | ~85s | `make test` | 47 failures / 13390 passed / 129 skipped. All 47 failures are in `tests/enterprise/mcp_auth/` and `tests/enterprise/*private_network*` — files this branch does NOT touch. `git diff --name-only merge_base...HEAD \| grep -E "mcp_auth\|enterprise"` returns empty. Scope run: 155/155 passing across all six test files this branch modified. |
| coverage | guide | SKIPPED | — | (n/a) | Guide: "Skip if the user did not request coverage." |
| sonar-local | guide | SKIPPED | — | (n/a) | Guide: "Skip if Sonar configuration/network/credentials are unavailable." |

## Failure detail (out-of-scope, informational only)

**gitleaks**: `Secret dial-ns336wly2mjwfjhaxauwqj4q797 found in .env:1 (generic-api-key)`. This is a local uncommitted `.env` modification (DIAL API key), not part of the EPMCDME-6577 branch diff, and gitleaks scans the working tree. Fix belongs to the environment, not this task.

**test suite**: 47 failures all in `tests/enterprise/mcp_auth/test_post_auth_401_bridge.py` and `tests/enterprise/mcp_auth/test_private_network_allowlist_bridge.py`. My branch does not touch `src/codemie/enterprise/**` or these test files. Task-scope regression suite:

```
tests/codemie_tools/git/test_custom_git_api_wrapper.py
tests/codemie_tools/git/test_github_app_auth.py
tests/codemie_tools/core/vcs/github/test_github_app_auth.py
tests/codemie/datasource/loader/test_git_auth_utils.py
tests/codemie/datasource/loader/test_git_loader.py
tests/codemie/service/git_api/test_git_api_service.py
→ 155 passed
```

## Drift signal

no
