# QA Report — EPMCDME-9984

## Branch

`EPMCDME-9984_add-marketplace-sorting`

## Merge base

`main` (d78515ef3)

## Gates

| Gate | Status | Notes |
|---|---|---|
| Ruff lint | PASS | `ruff check` exits 0 on all changed files |
| Unit tests (sort) | PASS | 20/20 pass: 9 `_build_sort_column` tests, 8 `_apply_sort_to_query` tests, 3 enum/router tests |
| Router tests | SKIP | Full FastAPI import chain requires packages unavailable in Python 3.13 dev env (pre-existing env issue, not introduced by this branch) |
| Build (`make build`) | SKIP | Skipped — build requires resolving pre-existing poetry.lock drift (out of scope for this ticket) |
| Gitleaks | SKIP | Docker unavailable in this environment |
| Sonar | SKIP | Credentials/network not configured locally |

## Risk notes

- `tests/.env.test` contains `AUTH_PASSWORD=Password1234!` in committed state. Not introduced by this branch (pre-existing value), but noted for rotation before merge.
- Pre-existing dirty files (`.env`, `pyproject.toml`, `poetry.lock`, `tests/.env.test`, `config/customer/customer-config.yaml`) are correctly excluded from this branch's single commit.
