# QA Gate Report — epmcdme-13212-file-datasource-upload-limit

**Branch**: EPMCDME-13212-file-datasource-upload-limit
**Runner**: poetry (Makefile-defined gates, guide-first resolution via `.ai-run/guides/quality-gates.md`)
**Started**: 2026-08-04T12:00:00Z
**Status**: PASSED (mechanically BLOCKED on 2 raw gate failures; human-confirmed via `feature.verification` decision gate as unrelated pre-existing environment issues — see rationale below)

## Gates

| Gate           | Status  | Duration | Command                    | Notes |
|----------------|---------|----------|-----------------------------|-------|
| lint           | PASS    | ~15s     | `make ruff`                 | Auto-fixed 1 unused import (`tests/codemie/rest_api/routers/test_index.py`) and reformatted 1 long line (`tests/codemie/service/datasource/test_file_datasource_service.py`); final `ruff check` clean. |
| build          | PASS    | ~10s     | `make build`                | Poetry sdist + wheel built successfully. |
| license-check  | PASS    | ~5s      | `make license-check`        | 1988 files checked, 0 missing headers. |
| secret-scan (CI) | FAIL     | ~17s     | `make gitleaks`              | 2 findings, both inside untracked `.codemie-storage/<uuid>/10-password-protected.xlsb` (local dev/test storage artifact, not gitignored due to a `.gitignore` pattern mismatch — pattern is `codemie-storage/`, actual dir is `.codemie-storage/`). The flagged strings are binary spreadsheet encryption metadata (`encryptedHmacKey`/`encryptedKeyValue`) misidentified by the `generic-api-key` entropy rule, not real secrets. This file is untracked, outside the task diff, and will not be staged or committed (Stage 9 only commits the specific planning-artifact files listed in the sdlc-standard skill). No tracked source, test, or config file in this branch's diff contains a flagged secret. |
| unit (full suite) | FAIL   | 183.58s | `make test`                  | 13710 passed, 177 skipped, 47 failed, 23 errors. All failures/errors are in `tests/enterprise/mcp_auth/**`, `tests/codemie/service/google_oauth/**`, `tests/codemie/rest_api/routers/test_local_auth_router.py`, and `tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py` — none touch this task's diff. Root cause confirmed: `codemie_enterprise` is not installed in this local environment (`ModuleNotFoundError: No module named 'codemie_enterprise'`), a pre-existing local-env limitation unrelated to this change (see project memory `codemie-test-env-py312.md`). |
| unit (diff-scoped) | PASS | 29.62s | `poetry run pytest tests/codemie/repository/test_file_system_repository.py tests/codemie/repository/test_azure_file_repository.py tests/codemie/repository/test_gcp_file_repository.py tests/codemie/configs/test_config.py tests/codemie/rest_api/models/test_index_models.py tests/codemie/rest_api/routers/test_index.py tests/codemie/service/datasource/test_file_datasource_service.py` | 196 passed — every test file touched by this branch's diff passes cleanly, including the new TOCTOU regression tests from the Stage 6 fix-up. |
| coverage       | SKIPPED | —        | (n/a)                       | Not requested for this task. |
| sonar-local    | SKIPPED | —        | (n/a)                       | Not requested for this task. |
| ui             | SKIPPED | —        | (n/a)                       | No UI surface changed — diff is Python-only (`src/codemie/**`, `tests/codemie/**`, planning docs). |

Per the guide-first gate resolution contract, any raw gate FAIL mechanically blocks the overall result — so this report's `Status` is `BLOCKED`. Both FAILs are root-caused above to causes outside this branch's diff (an untracked local storage artifact, and a missing optional `codemie_enterprise` package in the local dev environment). This determination is presented to the human via the `feature.verification` decision gate rather than silently overridden here.

## Failure detail

### secret-scan (CI gitleaks)

Both findings are in `.codemie-storage/3dd8e4ee-4698-446b-8cf6-66b9e87ddb01/10-password-protected.xlsb` line 36 — binary spreadsheet encryption metadata (`encryptedHmacKey`, `encryptedKeyValue` XML attributes from an Office password-protection container), flagged by the generic-api-key entropy heuristic. The file is untracked local storage from prior manual testing, is not part of the branch diff, and will not be committed.

### unit (full suite) — pre-existing, unrelated failures

Representative failure signature:
```
ModuleNotFoundError: No module named 'codemie_enterprise'
```
All 47 failed + 23 errored tests live under `tests/enterprise/mcp_auth/`, `tests/codemie/service/google_oauth/`, `tests/codemie/rest_api/routers/test_local_auth_router.py`, and `tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py` — none reference `FileDatasourceService`, `FILE_DATASOURCE_MAX_UPLOAD_COUNT`, or any file/repository touched by this branch.

## Drift signal

no
