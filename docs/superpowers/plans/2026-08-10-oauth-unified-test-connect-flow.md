# Plan: Tool OAuth unified test/connect flow

Design: `docs/superpowers/specs/2026-08-10-oauth-unified-test-connect-flow-design.md`
Scope: backend only (GitLab / Jira / Confluence). Branch `EPMCDME-13527_oauth-integration-atlassian-gitlab`, MR 3907.
Rhythm: inline, checkpoint after each task; hold all commits until manual test; both ruff gates + oauth suite before any commit.

---

## Task 1 — Engine: explicit `persist_token`

- `initiate_flow` gains `persist_token: bool` (no default at engine level — callers pass it).
- Remove `integration = integration_id or ""`; use `integration_id or ""` only for storage keys.
- Reject `persist_token=True and not integration_id` → `ExtendedHTTPException(400, ...)`.
- Store `persist_token` in the PKCE `context` (as `"persist_token": "true"/"false"`).
- Read it back in `handle_callback` / `_reassemble_state_data`; thread into `_publish_success`.

**Verify:** engine unit tests for both modes + the rejected combo (added in Task 5).

## Task 2 — Engine: token out of the result store

- Split `_publish_success` by `persist_token`:
  - connect: persist `token_data` to TMS first, then `store_result(status, **result_kwargs)` — no `token_data`.
  - test: no persist; `store_result(status, **result_kwargs)`.
- Change the persistence call to pass `token_data` directly (see Task 3), not via `state`/result store.
- `_fail` unchanged. `get_status` already returns the record as-is; confirm no `token_data` key remains.

**Verify:** result record has no `token_data` in either mode.

## Task 3 — Services: `persist_user_token(token_data=...)`

- All three settings services: new signature
  `persist_user_token(*, token_data: dict, user_id, setting_id, existing_credentials=None)`.
- Extract the credential-shaping half of `populate_credentials_from_flow` into a helper that takes a
  `token_data` dict; drop the `get_status`/result-store read for the Tool OAuth services.
- Preserve GitLab refresh-token recovery (existing-setting + vault) operating on the passed dict.
- Adapters' `persist_connected_token` → pass `token_data` through (engine hands it the dict).

**Verify:** service test persists to TMS from a dict; recovery still fires (Task 5).

## Task 4 — Settings: decouple creation

- Remove `_persist_oauth_creator_token` calls + atomic rollback from `create_setting` and
  `update_settings` (the Tool OAuth branches only).
- Delete `_persist_oauth_creator_token` and any now-dead `persist_user_token(oauth_state,...)` overloads.
- Leave Google's `oauth_state` path (lines ~386, ~493) and `SettingRequest.oauth_state` intact.

**Verify:** creating/updating a Tool OAuth Setting touches no TMS and no rollback; Google unchanged.

## Task 5 — Routers: `/initiate` = test, `/connect` gains `test`

- `/initiate` router handlers call the flow service with `persist_token=False` (test), no integration_id.
- `Connect*OAuthRequest` gains `test: bool = False`; `/connect` calls with
  `persist_token = not request.test`, `integration_id = setting_id`.
- Thread `persist_token`/`test` through each `flow_service.initiate_flow` signature.
- Drop the now-unneeded `result.pop("token_data")` in `/status` handlers.

**Verify:** router tests for both endpoints/modes.

## Task 6 — Tests + gates

- Update/extend: `test_flow_engine.py`, `test_stores.py` (if result shape asserted), the three
  settings-service tests, `settings` create/update tests, router tests.
- Run the oauth suite in-container; run `ruff check` + `ruff format --check` over the full changed set.
- Update `review.md` checklist: CR-U02 / U06 / U08 → done; note U09 (frontend) still open.

**Verify:** green suite + both ruff gates. Then STOP for manual test before commit.
