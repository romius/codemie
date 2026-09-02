# QA Gate Report — epmcdme-8827-backend-hardening

**Branch**: EPMCDME-8827_workflow-versioning-clean
**Runner**: poetry
**Started**: 2026-08-13T06:48:55Z
**Status**: BLOCKED

## Gates

| Gate | Status | Duration | Command | Notes |
|------|--------|----------|---------|-------|
| lint | PASS | ~3s | `make ruff` | format unchanged; ruff check passed |
| build | PASS | ~6s | `make build` | wheel + sdist built |
| license | PASS | ~1s | `make license-check` | 2015 files, 0 missing headers |
| gitleaks | FAIL | ~24s | `make gitleaks` | 2 findings outside this change: gitignored `.env` Azure key; unrelated historical `code-review-check.diff` |
| unit | FAIL | ~167s | `make test` | 14038 passed, 177 skipped; 86 failed + 30 errors in unrelated packages (enterprise mcp_auth, git wrappers, scripts). Hardening files all passed: `test_workflow_config_repository.py`, `test_workflow_service.py`, `test_bedrock_flow_service.py` |
| affected | SKIPPED | — | (n/a) | no changed-file-aware test command |
| ui | SKIPPED | — | (n/a) | no UI surface changed |
| coverage | SKIPPED | — | `make coverage` | not requested |
| sonar | SKIPPED | — | `make sonar-local` | credentials/network not assumed |
| verify | SKIPPED | — | `make verify` | component gates already run |
| test-harness | SKIPPED | — | `make test-harness` | not opening an MR in this stage |

## Failure detail

### gitleaks

- `/workspace/.env` `AZURE_OPENAI_API_KEY` (gitignored local env)
- `docs/superpowers/tasks/2026-07-31-epmcdme-8827-be-1-workflow-version-persistence/code-review-check.diff` (unrelated historical artifact)

Neither path is in this run's implementation diff.

### unit (`make test`)

Last summary:

```
= 86 failed, 14038 passed, 177 skipped, 192 warnings, 30 errors in 166.98s (0:02:46) =
```

Failed/errored files are not in the hardening change set (examples: `tests/enterprise/mcp_auth/*`, `tests/codemie_tools/git/test_custom_git_api_wrapper.py`, `tests/scripts/test_commit_msg_hook.py`). Scoped hardening tests passed in the same run.

## Drift signal

no
