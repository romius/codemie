# Per-User OAuth Tokens for Shared Integrations — Design

**Date:** 2026-07-31
**Status:** Approved — per-user tokens are stored in the enterprise Token Management System (TMS).
**Scope:** Backend. GitLab, Jira, and Confluence, over one provider-agnostic OAuth layer.

## Problem

An OAuth integration stores two very different kinds of data in one place today:

- **Application credentials** — `client_id`, `client_secret`, `callback_base_url`, `instance_url`. These belong to the OAuth *app* and are the same for everyone who uses the integration.
- **User tokens** — `access_token`, `refresh_token`, `expires_at`. These belong to the *person* who authorized and identify a specific account at the provider.

Both currently live in the integration's `Settings.credential_values` row. A PROJECT-scoped integration therefore has exactly one token set — whoever signed in first (in practice the admin who created it). Every project member who uses the integration then acts under that one account. That is wrong: actions are misattributed, and a member cannot use their own permissions.

## Goal

Separate the two concerns:

- **App credentials** stay on the shared `Settings` row. A project admin fills them once when creating the integration, and performs a first sign-in to verify the configuration works.
- **User tokens** become per-user. Each member authorizes under their own provider account; each member's calls use their own token. No member ever borrows another member's token.

USER-scoped (personal) integrations use the same mechanism with a single credential (owner only), so USER and PROJECT integrations are handled uniformly.

## Non-Goals

- Admin visibility into which members have connected. Each user sees only their own connection status.
- Changing the OAuth protocol flow (Authorization Code + PKCE), encryption, or refresh-rotation behavior.
- Migrating historical single-token integrations automatically (the first sign-in per user re-establishes tokens).

## Existing Building Blocks We Reuse

- **The enterprise Token Management System (TMS)** is the single token vault. It already provides Postgres persistence, AEAD envelope encryption (KMS-backed), a Redis refresh lock, refresh-token rotation, and an audit trail, keyed by `(user_id, auth_config_id)` — exactly the shape per-user tool tokens need. Tool integrations reuse it rather than introducing a second store.
- **`assert_secure_token_storage`** — the fail-closed guard that blocks OAuth initiation when the encryption backend is not confidential.
- **Encryption** via the existing `EncryptionService` (`EncryptionFactory`) for app credentials on the `Settings` row and for in-flight OAuth state in Redis.

## Data Model

**No new tables.** Per-user tokens live in the existing TMS credential table. The generic integration key is the TMS `auth_config_id` column, which carries:

| caller | value stored in `auth_config_id` |
|---|---|
| MCP servers | the MCP `auth_config.id` |
| Tool integrations (GitLab / Jira / Confluence) | the tool `setting_id` |

**Namespace decision:** the raw `setting_id` is used, without a `tool:` prefix. Both values are server-generated UUIDs drawn from disjoint tables, so they cannot collide; a prefix would only add an encoding to strip on every read.

Provider identity travels on the stored token itself via three additive fields on the enterprise `OAuth2TokenData` model — `provider_username`, `provider_user_id`, and `provider_metadata` (which carries the Atlassian `cloud_id`). Refresh is self-contained: an `OAuth2RefreshMetadata` snapshot (token endpoint, client id/secret, scopes) is stored alongside the token, so TMS can refresh without reading back `Settings` or `MCPConfig`. No extra `auth_config` snapshot column is required.

`Settings.credential_values` keeps app credentials only: `client_id`, `client_secret` (encrypted), `callback_base_url`, and `instance_url` (GitLab only).

### Enterprise dependency

Tool OAuth therefore **requires** the `codemie-enterprise` package (minimum `2.3.38`, the first release carrying the provider identity fields) and a TMS backend — `MCP_AUTH_TMS_ENABLED=true`, or `MCP_AUTH_TMS_ALLOW_MOCK=true` for local development. This is a deliberate trade: one audited, KMS-backed vault for every OAuth token instead of two stores with divergent security properties. A build without the enterprise package cannot enable GitLab / Jira / Confluence OAuth; `assert_token_vault_available()` fails at startup with an actionable message rather than letting a member hit an obscure error mid sign-in.

## Components and Data Flow

### 0. Shared OAuth layer

Three provider-agnostic pieces under `service/oauth/` carry the behavior that used to be copied per provider:

- **`token_port.py`** — the `TokenPort` contract plus `ToolOAuthTokenPort`, the TMS-backed adapter (`save_oauth2_token` / `get_oauth2_token_or_none` / `has_connection` / `delete` / `invalidate_by_integration`), and `map_tms_error_to_http`, which sanitizes TMS failures into 502/503 without leaking internals. It reaches the vault only through the public `get_token_management_system()` accessor, so MCP auth and tool OAuth share one construction, encryption, refresh-lock, and audit configuration.
- **`flow_engine.py` + `provider_adapters.py`** — the Authorization Code + PKCE engine and one adapter per provider. Only `build_authorize_url` / `exchange_code_for_tokens` / `fetch user info` / `revoke` differ per provider.
- **`state_store.py`** — one namespaced, encrypted Redis store for in-flight authorization state, used by the GitLab and Atlassian namespaces. It has **no** lock primitives: refresh locking belongs to TMS.

### 1. Storage split

- `Settings.credential_values` keeps app credentials exactly as today.
- Tokens are written to TMS under `(user_id, integration_id = setting_id)` and never touch the `Settings` row.

### 2. Token manager (thin)

`get_valid_access_token(setting_id, user_id)`:

- Validates that the setting exists and is of the expected OAuth credential type.
- Asks `ToolOAuthTokenPort` for the user's token. TMS transparently refreshes an expired access token under its own per-`(user, integration)` Redis lock and persists the rotated refresh token.
- If no usable credential exists, raises a typed "not connected" error (see §5).

### 3. Callback writes a per-user token

The state parameter is a **signed** token (HMAC-SHA256 over `provider`, `user_id`, `integration_id`, `redirect_uri`, `nonce`, `ts`, with a max age), so the callback can trust which integration and which user a code belongs to. Its claims are cross-checked against the encrypted Redis state before the code exchange. Because the signed state already carries the integration and the user, the callback is self-contained: no client-supplied `setting_id` is ever accepted after authorization, so a captured `state` cannot be replayed against a different integration.

### 4. Connect an existing integration

Signing in is no longer tied to integration creation. A member connects their account to an already-existing shared integration:

- `POST /v1/{provider}-oauth/connect` accepts a `setting_id`, loads the shared app credentials from that `Settings` row, and initiates the PKCE flow with `user_id` = the caller. On callback, the caller's token is written.
- `GET /v1/{provider}-oauth/status/{state}` reports the progress of one in-flight authorization (pending / success / error), while `GET /v1/{provider}-oauth/connection?setting_id=...` reports the caller's durable connection state — `connected` (with `provider_username`) or `not_connected`. **Route-naming decision:** these are kept as two endpoints because they answer two different questions; collapsing them onto a single `/status` would overload one route with a flow-scoped and an integration-scoped meaning.
- App credentials are always read from the integration. There are no environment-variable `client_id` / `client_secret` fallbacks — a misconfigured integration fails with a 400 naming the missing keys rather than silently authorizing against a global app.
- Every endpoint takes a caller-supplied `setting_id`, so authentication alone is not sufficient: each one resolves the integration through the same `Ability` model the settings API uses and answers **403** to a caller who is not a member of the owning project (or, for a USER-scoped integration, is not its owner). The access check runs *before* the credential-type check, so a wrong-type 400 cannot confirm the existence of an integration the caller may not see. Without it, anyone who learned a setting id could start a flow against it and read that integration's `client_id` out of the returned authorize URL.
- A callback that cannot be turned into a usable connection is reported as a failure rather than stored as a success — for Atlassian that includes a token whose account has no accessible site, since the resulting `cloud_id` is what the tool runtime needs to reach the gateway.
- Every terminal callback outcome, success or failure, is published to the flow's result key. The browser tab that started the flow polls `/status/{state}`; an unpublished failure would leave it polling a state that no longer exists. Only a flow still pending when the callback arrives is published, so a replayed callback cannot overwrite an outcome.

### 5. Resolution in the tools

`SettingsService.get_config` / `_inject_oauth_config_values` already thread the acting `user_id`. Resolution fetches the token via `(integration_id = setting_id, user_id = acting user)`. When no usable credential exists, resolution surfaces a typed `<Provider>OAuthNotConnected` error; before streaming starts, `enforce_oauth_connected` aggregates every unconnected provider into one connect gate.

The gate asks the port for `has_connection`, which resolves through `retrieve` (refreshing if needed) rather than the TMS `has_valid_token` predicate. `has_valid_token` reports false for a merely expired access token even when a refresh token could renew it, which would prompt an already-connected member to sign in again every token lifetime (1h on Atlassian, 2h on GitLab) and would disagree with what the `/connection` endpoint reports.

### 6. Lifecycle / deletion

- Deleting the integration (admin) invalidates every user's credential for that `setting_id` in one call (`invalidate_by_integration`).
- A user may disconnect only their own credential, which best-effort revokes at the provider and then deletes just that entry. Revocation is a per-provider adapter concern — GitLab has a revocation endpoint, Atlassian does not — and a provider-side failure never blocks the local delete, since removing the credential is what the user asked for.

## Client Behavior (for the UI layer)

- The integration card shows a **per-user** status — "connected as `<username>`" or "not connected" — plus a **"Sign in with GitLab"** button available to every member, driving `/connect` + `/status`.
- If a member runs an assistant that uses the integration before connecting, the run returns the actionable "connect your account" message (lazy path).

## Error Handling

- No usable credential → typed `<Provider>OAuthNotConnected` (not a generic 500); mapped to a clear connect-prompt at the run layer and a "not connected" state on the connection endpoint.
- TMS failures are mapped by `map_tms_error_to_http` to 503 (vault unavailable) or 502 (persistence / crypto / audit / refresh), never leaking the underlying error text. `TokenNotFound` and `ReAuthenticationRequired` are matched by type — they mean "this user must connect", not "the service is broken".
- Refresh failures (`invalid_grant`, revoked) are scoped to the single user's credential, so one member's revoked token never affects another's.

## Testing

- Signed state: tamper, expiry, wrong provider, and replay across integrations are all rejected.
- Flow engine: PKCE round-trip, callback state cross-check, persist failure path.
- Token port: TMS error mapping is sanitized; "not connected" errors are distinguished from service faults.
- Token manager: per-`(setting_id, user_id)` resolution; missing credential raises the typed not-connected error.
- Settings service: the acting user's token is written with the refresh snapshot and (Atlassian) `cloud_id`.
- Connect gate: aggregates every unconnected provider, dedupes a shared integration, no-ops when all are connected.
- Lifecycle: integration delete invalidates all users' credentials; user disconnect removes only their own.

## Rollout Notes

- **No Alembic migration for tokens** — TMS already owns the credential table, so the only schema change is the three new `credentialtypes` enum values.
- **Release ordering (blocking):** `codemie-enterprise` must publish `2.3.38` (the additive `provider_username` / `provider_user_id` / `provider_metadata` fields on `OAuth2TokenData`) and `codemie` must bump its pin and lock to it **before** this change ships. Until then `assert_token_vault_available()` refuses to boot with OAuth enabled, which is the intended fail-fast.
- **Required configuration when enabling a provider:** `OAUTH_STATE_SIGNING_SECRET` (≥32 bytes), a TMS backend (`MCP_AUTH_TMS_ENABLED`, or `MCP_AUTH_TMS_ALLOW_MOCK` locally), and a confidential encryption backend. All three `*_OAUTH_ENABLED` flags default to `False`.
- App-credential storage on `Settings` is unchanged, so existing integrations keep their app config; each user connects once to establish their own credential.
