# QA gates — EPMCDME-13738

Branch `feature/EPMCDME-13738-per-workflow-integration-scope` in both repos. Gates were run
directly (the change spans two repositories, so each repo's own runner was used).

| Gate | Repo | Command | Result |
|---|---|---|---|
| Lint | codemie | `poetry run ruff check src tests` | **pass** — all checks passed |
| License headers | codemie | `make license-check` | **pass** — 1978 files checked, 0 missing |
| Unit/integration tests | codemie | `make test` | **pass for this change** — 6962 passed in the affected suites; see the pre-existing failures below |
| Migration chain | codemie | `poetry run alembic heads` | **pass** — single head `w1o2r3k4f5l6` |
| Types | codemie-ui | `npx tsc --noEmit` | **pass** — no errors |
| Unit tests | codemie-ui | `npm run test:unit` | **pass** — 334 files, 3987 tests |
| Integration tests | codemie-ui | `npm run test:integration` | **pass** — 30 files, 451 tests, 1 skipped |
| Build | codemie-ui | `npm run build` | **pass** — built in 14.11s |
| Lint | codemie-ui | `npm run lint` | **skipped (environment)** — see below |

## Pre-existing failures, not caused by this change

`make test` reports 47 failures on this branch. All of them come from missing local packages and
reproduce independently of this change:

- `tests/enterprise/mcp_auth/**` (44) — `ModuleNotFoundError: No module named 'codemie_enterprise'`
- `tests/codemie/service/mcp/test_toolkit_service_auth_resolver.py` (2) and
  `tests/codemie/rest_api/routers/test_local_auth_router.py` (1) —
  `ValueError: the greenlet library is required to use this function. No module named 'greenlet'`

None of them touch the assistant user mapping, the settings handler chain, or the workflow
plumbing changed here.

## Frontend lint

`npm run lint` fails with 8413 errors of the form `Unable to resolve path to module '@/…'` — the
repo-wide ESLint alias resolver is broken in this environment and fails the same way on an
untouched checkout, so the local run carries no signal about this change. Lint for the frontend is
enforced by CI on the merge request.

## Migration — manual verification (done)

The alembic migration cannot be exercised by the test suite: `tests/conftest.py:34` mocks the
database engine globally and the repo has no DDL tests. It was therefore verified by hand against
the local Postgres (schema `codemie`), with the backend serving on `:8080` throughout:

| Check | Result |
|---|---|
| Upgrade with existing rows present | **pass** — 7 pre-existing rows kept, all carrying `workflow_id = ''`, so nobody has to re-select an integration |
| Column shape | **pass** — `workflow_id character varying NOT NULL DEFAULT ''` |
| Constraint swap | **pass** — `uix_assistant_user_mapping_scope` over (assistant_id, user_id, workflow_id); the old two-column `uix_assistant_user_mapping` is gone |
| Index | **pass** — `ix_assistant_user_mapping_workflow_id` present |
| Downgrade with a workflow-scoped row present | **pass** — the row was archived into `assistant_user_mapping_workflow_scope_backup` and removed from the table; the 7 assistant-scoped rows were untouched; the two-column constraint was restored and the revision fell back to `i1n2t3e4r5a6` |
| Re-upgrade | **pass** — back to `w1o2r3k4f5l6` with the three-column constraint and the index restored |
| Backend health after the cycle | **pass** — `GET :8080/docs` → 200 |

The test row and the archive table created during the check were removed afterwards; the database
is back to its pre-check state (7 assistant-scoped rows, revision `w1o2r3k4f5l6`).

## End-to-end walkthrough in the browser (done)

Driven through Playwright against the local stack (frontend `:5173`, backend `:8080`), on two
throw-away assistants and a throw-away workflow created and deleted for the run.

| Scenario | Result |
|---|---|
| Automatic lookup on, an integration of the type exists → the panel pre-selects it (`fake_jira_integration (user)`); a type with no integration offers "Add Integration" instead | **pass** |
| The user picks "No integration" → stored as an empty id and still shown after a reload, i.e. automatic lookup no longer overrides the explicit choice | **pass** |
| The author turns automatic lookup off in the assistant form → the "Select integration" dropdown appears, the flag survives the save, and the panel shows "No integration" with no pre-selection (the user can still pick one) | **pass** |
| Saving in the workflow panel with the checkbox off → a workflow-scoped row is written and the assistant-scoped row is untouched; the toast names the workflow scope | **pass** |
| Ticking "Apply to the whole assistant" → the selection is promoted to the assistant row and the workflow row is removed in the same save | **pass** (after the fix below) |
| Saving a workflow whose assistant has an unresolved slot → HTTP 200 with `warnings`, not a 400 | **pass** |
| Using a tool whose slot has no integration → the assistant answers that the integration is not configured | **pass** (after the fix below) |

### Defects found and fixed during the walkthrough

1. **The scope checkbox did not make the panel dirty.** Ticking "Apply to the whole assistant"
   without also touching a slot left the Save button hidden, so an existing selection could not be
   promoted to the assistant at all. Fixed in `UserMapping.tsx`; covered by
   `offers the save once the checkbox alone is ticked`.
2. **The explicit "no integration" choice broke agent start-up.** `ToolConfig` rejected an empty
   integration id, so a slot the user had deliberately left empty raised a validation error while
   the agent was being built and the whole chat failed with a 500 instead of the tool reporting the
   missing integration. Fixed in `codemie/core/models.py`; covered by
   `test_tool_config_accepts_the_explicit_no_integration_choice`. The existing
   `test_missing_integration_tool.py` had missed this because it built its fixture with
   `model_construct`, bypassing validation — it now uses the real constructor.
