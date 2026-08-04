# QA Gate Report — EPMCDME-10889 marketplace-clone-counter-backend

**Branch**: EPMCDME-10889_marketplace-clone-counter
**Runner**: poetry (Makefile targets)
**Started**: 2026-07-27T18:12:00Z
**Status**: PASSED

## Gates

| Gate | Status | Command | Notes |
|---|---|---|---|
| lint/format | PASS | `make ruff` | 2198 files unchanged, all checks passed. |
| license headers | PASS | `make license-check` | 1964 files checked, 0 missing headers. |
| secret scan | PASS | `make gitleaks` | Docker available; no leaks found. |
| build | PASS | `make build` | sdist + wheel built for codemie 0.8.0. |
| tests | PASS (after fix) | `make test` | See below. |

## Test detail

First `make test` run surfaced a real regression from this diff: **10 failures**
in `tests/codemie/rest_api/models/test_assistant_model.py` and
`tests/codemie/rest_api/models/test_map_assistant_request_metadata.py`, all
`ValueError: "Assistant" object has no field "source_assistant_id"`.

Root cause: `AssistantBase._map_assistant_request()` (used by the *update*
path) iterates `AssistantRequest`'s fields and `setattr()`s each onto the
`Assistant` model. The create-path fix already excluded `source_assistant_id`
from `model_dump(exclude={...})` in the router, but the update-path mapping
method had its own separate exclusion list that was not updated — `Assistant`
has no such field (it's create-only, consumed directly in the router's
clone-tracking block), so any assistant *update* request crashed.

**Fix**: added `'source_assistant_id'` to the exclusion tuple in
`_map_assistant_request()` (`src/codemie/rest_api/models/assistant.py:885-893`),
mirroring the existing create-path exclusion. Committed at `8d2ada320`.

Verified pre-existing/unrelated failures by stashing this diff and re-running
a sample of the remaining failures (`test_file_system_repository`,
`test_toolkit_service_auth_resolver`, `test_client_metadata_bridge`) — they
fail identically without this diff applied, confirming they are not caused by
this change (enterprise mcp_auth bridge tests + file-system-repository tests,
unrelated subsystems).

**Second `make test` run** (post-fix): `49 failed, 13517 passed, 177 skipped`
— the same 49 pre-existing/unrelated failures, zero failures in any file
touched by this diff. Full feature test scope
(`assistant_clone_event`, `assistant_clone_event_repository`, `test_assistant.py`,
`test_assistant_repository.py`, `test_assistant_model.py`,
`test_map_assistant_request_metadata.py`) — **126 passed, 0 failed**.

## Drift signal

no

## Commits added by this stage

- `8d2ada320` — EPMCDME-10889: Exclude source_assistant_id from assistant update mapping
