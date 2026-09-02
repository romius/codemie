# QA report — EPMCDME-10913 backend

**Branch**: EPMCDME-10913_deprecate-zephyrsquad
**Base**: origin/main (4f9b6cd79)
**Ran**: 2026-07-31

## Gates

| Gate | Command | Result |
|---|---|---|
| Ruff format | `poetry run ruff format --check <changed files>` | PASS — 8 files already formatted |
| Ruff lint | `poetry run ruff check <changed files>` | PASS — All checks passed |
| License headers | `poetry run python scripts/license_headers/check_license_headers.py --check tests/codemie_tools/base/test_models_deprecated_flag.py` | PASS — 0 missing |
| Pre-commit hook | Ran during `git commit` — Codemie pre-commit (ruff fast fix + tests + sonar) | PASS |
| Targeted pytest | `poetry run pytest tests/codemie_tools/base/ tests/codemie_tools/qa/ tests/codemie/rest_api/routers/test_user_settings.py tests/codemie/rest_api/routers/test_project_settings.py` | PASS — 184 tests passed, 8 warnings |
| Gitleaks | Skipped — no docker in this shell; pre-commit sonar covers secret patterns | SKIPPED |
| Feature verification | Skipped — no UI touched in this run (backend only) | SKIPPED |

## Summary

Backend deprecation changes ship cleanly. All directly-affected test suites green (models, QA toolkit, user_settings router, project_settings router). No new lint or format issues introduced. New test file has proper license header. Pre-commit gate ran the same checks and passed at commit time.

## Notes

- The `HTTP_422_UNPROCESSABLE_ENTITY` deprecation warning is a pre-existing Starlette advisory unrelated to this change (it appears in the `test_test_setting_exception` test which was not modified).
- The `InsecureKeyLengthWarning` from ZephyrSquad api-wrapper tests is unrelated to this change.
