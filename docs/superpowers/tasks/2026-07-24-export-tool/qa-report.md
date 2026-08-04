# QA Gate Report — 2026-07-24-export-tool

**Branch**: EPMCDME-12313_export-tool
**Runner**: poetry (guide-first: `.ai-run/guides/quality-gates.md`)
**Started**: 2026-07-24
**Status**: PASSED (with environment caveat on the full unit suite — see notes)

## Gates

| Gate | Status | Command | Notes |
|------|--------|---------|-------|
| lint | PASS | `poetry run ruff check` + `ruff format --check` (changed files) | All checks passed; 5 files already formatted. |
| build | PASS | `poetry build` | Built `codemie-0.8.0` sdist + wheel; artifacts removed after. |
| license | PASS | `check_license_headers.py --check` (new files) | 0 missing headers. |
| unit (affected) | PASS | `pytest test_export_tables_tool.py test_file_system_toolkit.py` | 30 passed (15 export + 15 toolkit). |
| unit (full suite) | SKIPPED | `poetry run pytest tests/` | Cannot run in this local env — see notes. Must be validated in CI. |
| gitleaks | SKIPPED | `make gitleaks` | Docker/colima daemon not running (guide allows Skip-if Docker unavailable). |
| ui | SKIPPED | (n/a) | No UI surface changed (backend-only). Green. |

## Notes / environment caveats

The **full** unit suite could not be collected in this local checkout due to
**pre-existing, change-unrelated** environment gaps:

- `tree-sitter-languages` (pulled transitively by `openhands_aci` via the
  file-system tools) has **no wheel for Python 3.13**, and only 3.13 is available
  here — importing `tools.py` (and thus `test_file_system_toolkit.py`
  unstubbed) fails. Verified via a local-only stub for that unused transitive
  import; the toolkit tests pass with it.
- `pymssql` fails to build from source on this host (Cython compile error).
- The private `codemie-enterprise` GCP registry is not authenticatable here, so a
  full `poetry install` cannot complete.

The gates that **do** run against this change (lint, build, license, and the
affected test modules) all pass. The full suite must be run by CI (which uses a
Python version with the required wheels and registry access).

## Lint / commit / dependency notes

- 3 feature commits + 1 review-fix commit, all `EPMCDME-12313:`-prefixed.
- `openpyxl` direct-dependency promotion was reverted (poetry.lock cannot be
  regenerated without registry auth); it remains a resolved main-group transitive
  dependency and is importable at runtime. Promotion is a maintainer follow-up
  (to be noted in the MR).

## Drift signal

no
