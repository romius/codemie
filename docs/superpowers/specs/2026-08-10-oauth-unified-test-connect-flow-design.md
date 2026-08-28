# Tool OAuth: unified test/connect flow

## Problem

The per-user Tool OAuth layer (GitLab / Jira / Confluence) currently entangles three concerns that
should be independent:

1. **Test vs. connect is inferred from an empty `integration_id`.** `OAuthFlowEngine.initiate_flow`
   sets `integration = integration_id or ""`, and `_publish_success` persists only when
   `validated.integration_id` is truthy. A frontend that forgets to send `integration_id` silently
   turns a real connect into a throwaway, and the UI cannot explain why nothing was saved.

2. **The Redis result store doubles as a token transport.** On success the engine encrypts the full
   token blob and writes it into the result store; `get_status` returns that record to the polling
   browser. Tokens should live only in enterprise TMS, not in a short-lived Redis record that the
   status endpoint hands back to the client.

3. **Setting creation is coupled to a completed OAuth flow.** `create_setting` / `update_settings`
   call `_persist_oauth_creator_token(request.oauth_state, ...)`, which re-reads the parked token
   out of the result store and writes it to TMS under the just-created `Setting.id`, wrapped in an
   atomic rollback of the Setting row. Creating a Setting therefore depends on having already run a
   flow, and the Settings write path has to understand `oauth_state`, result records, and
   credential-type branches.

These are one knot: the empty-`integration_id` "creator" flow exists *because* the Setting does not
exist yet at flow time, which is *why* the token is parked in the result store, which is *what*
`create_setting` later drains. Untying any one strand requires untying all three.

## Goals

- Replace the implicit empty-`integration_id` test detection with an explicit `persist_token: bool`.
- Stop transporting tokens through the result store; the status endpoint returns only status +
  non-secret metadata.
- Let a user create a Setting with app credentials alone and connect (or test) afterward; remove the
  create-time token persist and its rollback for the Tool OAuth types.

## Non-goals

- Frontend work (the Test button UX). Tracked separately; this design fixes the backend contract the
  frontend will call.
- Google OAuth. Its `oauth_state` → `populate_credentials_from_flow` → credentials-on-the-row path is
  a different mechanism and is left untouched. `oauth_state` stays on `SettingRequest` for Google.
- Changing the signed-state / PKCE-store / creds-snapshot primitives introduced in the previous round.

## Design

### The two modes

`persist_token` becomes an explicit flow input carried in the **server-side PKCE `context`** dict
(alongside `provider`, `instance_url`). It is set at `initiate_flow` and read back in the callback.
Because it lives in the Redis-side PKCE record — never in a client-controlled parameter — it cannot
be tampered with between initiate and callback; there is no need to add it to the signed state.

- **Connect (`persist_token=True`)** — requires `integration_id`. The callback exchanges the code,
  the adapter finalizes it, and the resulting `token_data` is written **directly to TMS** under
  `integration_id`. The result store records only `status = success` + public metadata.
- **Test (`persist_token=False`)** — `integration_id` optional. The callback runs the *full*
  exchange and `finalize_callback_payload` (so Atlassian site-selection and the username lookup
  actually execute — that is what makes it a real test), then **discards** the token. The result
  store records only `status = success` + public metadata.

`initiate_flow` rejects `persist_token=True` with no `integration_id` (400) — the one invalid
combination. The old `integration = integration_id or ""` line is removed.

### Token no longer flows through the result store

`_publish_success` is split by mode:

- Connect: persist first (unchanged ordering guarantee — a persist failure must never leave a false
  success published), then store a result of `status + result_kwargs` (public metadata) with **no**
  `token_data` field.
- Test: skip persistence entirely; store a result of `status + result_kwargs`.

`persist_connected_token` / `persist_user_token` stop reading the result store. Instead the engine
passes the finalized `token_data` dict straight into persistence. The GitLab refresh-token recovery
logic (recover from an existing setting or the vault when GitLab omits `refresh_token` on re-consent)
is preserved — it just operates on the passed-in `token_data` rather than one re-read via
`get_status`.

New persistence contract (all three services):

```
persist_user_token(*, token_data: dict, user_id: str, setting_id: str,
                   existing_credentials=None) -> None
```

`populate_credentials_from_flow`'s result-store read (`get_status` → `token_data`) is removed; the
credential-shaping half (extract access/refresh/expiry/metadata from a `token_data` dict) is kept and
fed the dict directly. The Google service keeps its own `populate_credentials_from_flow` untouched.

`get_status` returns `{status, <public metadata>}` in both modes; routers no longer need to
`result.pop("token_data")` because it is never stored.

### Setting creation decoupled

- Remove the `_persist_oauth_creator_token` call and its surrounding atomic-rollback from both
  `create_setting` and `update_settings`. `_persist_oauth_creator_token` fires only for the Tool
  OAuth types today (Google is not in `_oauth_app_keys`), so removing it affects only GitLab / Jira /
  Confluence. Google's credentials are written into `request.credential_values` before the row is
  saved, so its create/update stays atomic without the rollback.
- A Tool OAuth Setting is created with app credentials only. The creator connects their own account
  afterward via `POST /connect` — which now always has a real `Setting.id`, so the empty-
  `integration_id` path disappears from real usage entirely.
- `_persist_oauth_creator_token` and the now-dead `persist_user_token`-via-`oauth_state` overloads
  are removed.

### Endpoint surface

| Endpoint | Body | Mode | `integration_id` |
|---|---|---|---|
| `POST /initiate` | raw app creds (`client_id`, `client_secret`, `callback_base_url`, …) | test only | none |
| `POST /connect` | `setting_id`, `test: bool = false` | `test=false` → connect (persist); `test=true` → test | `setting_id` |

- `/initiate` has no Setting to persist under, so it is inherently test mode → engine called with
  `persist_token=False, integration_id=None`. Backs the **Add-form** Test button.
- `/connect` loads app creds from the authorized Setting and calls the engine with
  `persist_token = not request.test`, `integration_id = setting_id`. `test=true` backs the
  **Edit-form** Test button; `test=false` is the real connect/Save.

`/status/{state}`, `/connection`, and `DELETE /connection` are unchanged in shape.

## Risks

- **Behavior change for existing frontend.** The current UI persists the creator token via the
  create-Setting form (`oauth_state`). After this change the creator must connect via `/connect`
  after the Setting exists. The frontend (CR-U09) must land in the same release or the creator's
  first token will not be saved. Called out for coordination; the backend contract here is what the
  frontend will target.
- **`populate_credentials_from_flow` shared with Google?** No — each provider has its own service
  class; only the Tool OAuth three change. Verified GitLab/Jira/Confluence services are independent
  of `GoogleOAuthSettingsService`.
- **Test mode still performs a real provider round-trip** (token exchange + finalize). This is
  intentional — a test that skips the exchange would not validate the app credentials or scopes.

## Test strategy

- `flow_engine`: connect persists + result carries no `token_data`; test does not persist and still
  returns metadata; `persist_token=True` without `integration_id` is rejected at initiate;
  persist-failure-before-success ordering still holds.
- Settings service: `persist_user_token(token_data=...)` writes to TMS and preserves refresh-token
  recovery; no result-store dependency.
- `settings.py`: `create_setting` / `update_settings` for Tool OAuth types create the row with app
  creds only and do not touch TMS or roll back; Google path unchanged.
- Routers: `/initiate` → test; `/connect` `test` flag maps to `persist_token`; `/status` returns no
  token blob.
- Full oauth suite + both ruff gates over the whole changed set (src + tests).
