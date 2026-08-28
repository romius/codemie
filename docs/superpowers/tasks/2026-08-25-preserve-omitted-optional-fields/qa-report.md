# QA Gate Report — preserve-omitted-optional-fields

**Branch**: EPMCDME-14150_preserve-omitted-optional-fields
**Runner**: poetry (Makefile targets are the source of truth)
**Started**: 2026-08-25T08:30:00Z
**Status**: PASSED

## Gates

| Gate | Source | Status | Command | Notes |
|------|--------|--------|---------|-------|
| lint | guide | PASS | `make ruff` | `ruff format` reformatted 3 test files; `ruff check --fix` and final `ruff check` both clean. |
| build | guide | PASS | `make build` | Poetry built sdist + wheel for codemie 0.8.0. |
| license | guide | PASS | `make license-check` | 2050 files checked, 0 missing headers. |
| affected | guide | PASS | `poetry run pytest <changed suites>` | 109 passed: map-assistant-request helpers, version-compare service, version-service (critical + base), version-compare-metadata, router assistant, assistant_factory. |
| unit (full) | guide | SKIPPED | `make test` | Not run: this checkout has a pre-existing environment gap (`make test` fails ~58 tests from a missing `codemie_enterprise` package, `tests/enterprise/*`) unrelated to this change. Ran the affected suites instead. Full suite / `/sanity` regression owed at MR time. |
| secrets | hook/ci | SKIPPED | `make gitleaks` / `codemie-gitleaks` | Container-engine (Docker/Podman) gate; belongs to the commit/MR handoff. Enable locally by starting Podman/Docker; will run in the pre-commit hook at MR time. |
| sonar | ci | SKIPPED | `make sonar-local` | Requires Sonar config/token/network; not available locally. |
| test-harness | guide | SKIPPED | `make test-harness` | MR compliance gate (docker stack + fixtures). Owed at Stage 10 / MR creation, not this validation stage. |
| ui | guide | SKIPPED | (n/a) | No UI surface changed (backend-only diff). |

## Failure detail

None.

## Drift signal

no — implementation matches the (now scope-expanded) spec: `_should_update_field`, `_map_assistant_request`, `has_configuration_changes`, and `create_new_version` all present with the signatures referenced by the spec/plan.

## Owed at MR time (only CI/hooks can settle)

- `codemie-gitleaks` pre-commit secret scan (needs a container engine).
- `make test` full suite (blocked locally by the pre-existing `codemie_enterprise` gap) and the `/sanity` regression run.
- `make test-harness` + `## Test harness` MR section for the `auto_epm-cdme_vcs` bot.
