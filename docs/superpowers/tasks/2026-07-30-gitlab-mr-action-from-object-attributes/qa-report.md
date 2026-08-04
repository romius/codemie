# QA Report — EPMCDME-8384 GitLab MR action from `object_attributes`

Branch: `EPMCDME-8384_gitlab-mr-action-from-object-attributes` · Commit: `117d1bc6c`

| Gate | Command | Result |
|---|---|---|
| Lint / format | `make ruff` | **PASS** — 1 file reformatted, `ruff check --fix` and `ruff check` clean |
| License headers | `make license-check` | **PASS** — 1982 files checked, 0 missing |
| Unit tests (targeted) | `poetry run pytest tests/codemie/triggers/bindings/ -q` | **PASS** — 118 passed (111 before + 7 new) |
| Unit tests (full) | `make test` | **PASS for this change** — 46 failed / 13705 passed / 177 skipped. The same 46 failures were present on this branch before any edit (baseline run: 46 failed / 13698 passed), all in unrelated enterprise/MCP suites. Passed count rose by exactly the 7 new tests. |
| Secret scan | `make gitleaks` | **SKIPPED (pre-existing baseline)** — 1 leak: `.env:generic-api-key:1`, the local DIAL key in the tracked `.env`. `git diff main...HEAD --name-only` contains no `.env`, so this branch did not introduce it. |
| Test harness (sanity-api) | `make test-harness` | **PASS** — 168 passed, 4 skipped, 2 rerun, 0 failed (382.96s) |
| Pre-commit hook | on `git commit` | **PASS** — ruff fast fix + tests + sonar |

## TDD evidence

- RED (fixtures switched to the real GitLab shape, implementation untouched):
  `19 failed, 99 passed` — 12 previously green tests flipped, proving the defect
  and the fixture blind spot.
- GREEN (after reading the action from `object_attributes`):
  `118 passed`.

## Functional verification against a live GitLab

Not a synthetic payload — a webhook was registered in
`oleg_sotnichenko/codemie-public-skills` (hook 146999) pointing at a
webhook.site collector, and real GitLab 17.11.7 deliveries were replayed into
the local stack with `gitlab_event_filter = open,merge`:

| Real delivery | Before fix | After fix |
|---|---|---|
| `action=open` (MR created) | 200 `event filtered out`, resource not invoked — **wrong** | 200 `Webhook invoked successfully`, assistant invoked |
| `action=update` (MR description edited) | 200 `event filtered out` | 200 `event filtered out` (still correctly filtered, `filtered_action=update`) |
| `action=approved` (MR approved) | n/a | action recognized (`approved` ∈ `MR_ACTIONS`) |

Edge cases probed directly against the fixed extractor: top-level-only action
(fallback), both levels present (`object_attributes` wins), `object_attributes`
not a dict, empty-string action, missing action, unknown action, non-MR event,
invalid UTF-8 bytes, empty bytes — all behave as documented.

## Notes / follow-ups

- `codemie-sdk/test-harness/codemie_test_harness/tests/utils/webhook_utils.py`
  still builds the unrealistic top-level-`action` payload. Harmless thanks to the
  fallback, but it means SDK e2e cannot catch this class of defect. Separate MR.
- GitLab documents `approval` / `unapproval` actions for individual approvals in
  addition to `approved` / `unapproved`; those are absent from `MR_ACTIONS`. Not
  reproducible on this instance (approving sent `approved`), so left out of scope
  and flagged for a follow-up.
- Local compose default profile has no redis, so the webhook rate limiter logs
  `Error 111 connecting to localhost:6379` per delivery and degrades open.
  Unrelated to this change.
