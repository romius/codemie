# Per-User OAuth Tokens for Shared Integrations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each user their own OAuth tokens on a shared integration, so app credentials stay shared (admin-managed) while every member authorizes and acts under their own provider account.

**Architecture:** App credentials (`client_id`, `client_secret`, `callback_base_url`, and GitLab `instance_url`) stay on the shared `Settings` row. Per-user OAuth tokens for GitLab, Jira, and Confluence live in the existing enterprise Token Management System (TMS), keyed by `(user_id, auth_config_id)`, where tool OAuth writes the raw `setting_id` into `auth_config_id`. TMS already provides Postgres persistence, AEAD envelope encryption, KMS-backed key handling, a Redis refresh lock, refresh-token rotation, and audit events. The tool layer adapts to it through `ToolOAuthTokenPort`, then provider token managers resolve tokens by `(setting_id, acting_user_id)`.

**Tech Stack:** Python, FastAPI, SQLModel/SQLAlchemy, Alembic enum migrations, Redis for in-flight OAuth state, enterprise TMS for tokens, httpx, pytest.

## Global Constraints

- Tokens live only in enterprise TMS; there is one token vault for MCP auth and tool OAuth.
- The tool `setting_id` is stored raw in the TMS `auth_config_id` column. MCP auth config ids and tool setting ids are server-generated UUIDs from disjoint tables, so no prefix is required.
- `Settings.credential_values` stores shared app credentials only. It never stores provider access tokens, refresh tokens, token expiry, scopes, or provider identity for GitLab, Jira, or Confluence.
- `OAUTH_STATE_SIGNING_SECRET` must be configured to at least 32 bytes whenever a tool OAuth provider is enabled. Signed state is HMAC-SHA256 and stdlib-only.
- `GITLAB_OAUTH_ENABLED`, `JIRA_OAUTH_ENABLED`, `CONFLUENCE_OAUTH_ENABLED`, and `MCP_AUTH_ENABLED` default to `False`.
- There are no provider-level `*_OAUTH_CLIENT_ID` or `*_OAUTH_CLIENT_SECRET` config fallbacks. Per-integration credentials are required and missing credentials raise actionable errors.
- Redis stores only in-flight authorization state/results. Refresh locking belongs to TMS.
- The connection-status endpoint returns only the caller's own durable connection state and never reveals whether another user connected.
- `ToolOAuthTokenPort.has_connection` must resolve through TMS `retrieve` (`get_oauth2_token_or_none`), not a valid-token predicate, so an expired-but-refreshable credential still counts as connected.
- TMS failures are sanitized through `map_tms_error_to_http`; raw persistence, crypto, audit, or refresh errors are not exposed to clients.
- The only Alembic work is adding `GITLAB_OAUTH`, `JIRA_OAUTH`, and `CONFLUENCE_OAUTH` to the existing `credentialtypes` enum.

---

## File Structure

- `src/codemie/service/oauth/token_port.py` — `TokenPort` Protocol, `ToolOAuthTokenPort`, TMS readiness, TMS save/retrieve/delete/invalidate adapter, connection check, and HTTP error mapping.
- `src/codemie/service/oauth/flow_engine.py` — provider-agnostic Authorization Code + PKCE engine, signed-state verification, callback dispatch, status lookup, and server-side token persistence hook.
- `src/codemie/service/oauth/provider_adapters.py` — GitLab, Jira, and Confluence provider adapters for authorize URLs, code exchange, user-info fetch, callback payload construction, and provider-specific persistence dispatch.
- `src/codemie/service/oauth/state_store.py` — shared encrypted Redis state/result store with `STATE_TTL = 600` and `RESULT_TTL = 300`.
- `src/codemie/service/oauth_state_signing.py` — HMAC-SHA256 signed OAuth state over `provider`, `user_id`, `integration_id`, `redirect_uri`, `nonce`, and `ts`.
- `src/codemie/service/oauth_security.py` — fail-closed guards for confidential token storage, state signing secret, and enterprise TMS availability (`MIN_ENTERPRISE_VERSION = "2.3.38"`).
- `src/codemie/enterprise/mcp_auth/dependencies.py` — public TMS access through `get_token_management_system()` and `tms_audit_context()`.
- `src/codemie/service/gitlab_oauth/{constants.py,flow_service.py,settings_service.py,state_store.py,token_manager.py}` — GitLab-specific wiring over the shared OAuth layer.
- `src/codemie/service/atlassian_oauth/state_store.py` — shared Atlassian Redis namespace for Jira and Confluence.
- `src/codemie/service/jira_oauth/{constants.py,flow_service.py,settings_service.py,token_manager.py}` — Jira-specific wiring over the shared OAuth layer.
- `src/codemie/service/confluence_oauth/{constants.py,flow_service.py,settings_service.py,token_manager.py}` — Confluence-specific wiring over the shared OAuth layer.
- `src/codemie/rest_api/routers/{gitlab,jira,confluence}_oauth.py` — initiate, callback/status, connect, connection, and disconnect endpoints.
- `src/codemie/service/settings/settings.py` — app-credential persistence, creator token persistence after save, config injection, lookup fallback, and integration-delete invalidation.
- `src/codemie/service/tools/oauth_connect_gate.py` — pre-stream aggregate connect gate for OAuth-backed tools.
- `src/codemie_tools/core/vcs/gitlab/{models.py,tools.py}` — GitLab config `acting_user_id` and OAuth token resolution.
- `src/codemie_tools/core/project_management/jira/{models.py,tools.py}` — Jira config `acting_user_id`, `cloud_id`, and OAuth token resolution.
- `src/codemie_tools/core/project_management/confluence/{models.py,tools.py}` — Confluence config `acting_user_id`, `cloud_id`, and OAuth token resolution.
- `src/codemie/core/exceptions.py` — provider not-connected errors and aggregate OAuth connect-required error.
- `src/codemie/configs/config.py` and `src/codemie/rest_api/main.py` — provider flags, state-signing config, and startup guards.
- `src/external/alembic/versions/g1l2a3b4o5a6_add_gitlab_oauth_credential_type.py` — adds GitLab OAuth credential type.
- `src/external/alembic/versions/j1r2a3o4a5u6_add_jira_oauth_credential_type.py` — adds Jira OAuth credential type.
- `src/external/alembic/versions/c1o2n3f4l5u6_add_confluence_oauth_credential_type.py` — adds Confluence OAuth credential type.

---

## Task 1: Shared TMS token port

**Files:**
- Modify: `src/codemie/service/oauth/token_port.py`
- Modify: `src/codemie/enterprise/mcp_auth/dependencies.py`
- Test: `tests/codemie/service/oauth/test_token_port.py`
- Test: `tests/codemie/service/oauth/test_token_port_connection.py`
- Test helper: `tests/codemie/service/oauth/tms_stub.py`

**Interfaces:**
- Produces: `TokenPort` Protocol keyed by `(user_id, integration_id)`.
- Produces: `ToolOAuthTokenPort.save_oauth2_token`, `get_oauth2_token_or_none`, `has_connection`, `delete`, and `invalidate_by_integration`.
- Produces: `map_tms_error_to_http(provider_label, exc) -> ExtendedHTTPException`.
- Consumes: enterprise TMS `store`, `retrieve`, `delete`, and `invalidate_by_config` through `get_token_management_system()`.

- [ ] **Step 1: Write the tests first**

Cover these cases in `tests/codemie/service/oauth/test_token_port.py` and `tests/codemie/service/oauth/test_token_port_connection.py`:

- `TMSUnavailable` maps to 503.
- persistence, audit, crypto, and refresh failures map to 502.
- unknown exceptions map to a generic 500 without leaking the raw exception text.
- `TokenNotFound` and `ReAuthenticationRequired` mean not connected.
- `has_connection` calls `retrieve` and returns true for an expired access token that TMS can refresh.
- vault failures propagate to the caller so the API can return a service error instead of prompting the user to sign in again.

Run:

```bash
poetry run pytest tests/codemie/service/oauth/test_token_port.py tests/codemie/service/oauth/test_token_port_connection.py -v
```

Expected before implementation: FAIL because the port and mapping do not exist.

- [ ] **Step 2: Implement the port**

In `token_port.py`:

- Define the `TokenPort` Protocol with per-user integration methods.
- Implement `ToolOAuthTokenPort._get_tms()` so all tool OAuth token operations go through `codemie.enterprise.mcp_auth.dependencies.get_token_management_system()`.
- Wrap TMS calls in `tms_audit_context(source, correlation_id=integration_id)` with operation-specific sources.
- Store `OAuth2TokenData` with `provider_username`, `provider_user_id`, `provider_metadata`, and `OAuth2RefreshMetadata`.
- Use the raw `setting_id` as `integration_id`; TMS persists it in `auth_config_id`.
- Implement `has_connection` through `get_oauth2_token_or_none` so refreshable expired tokens count as connected.
- Implement `invalidate_by_integration` via TMS `invalidate_by_config` for integration delete/update cleanup.
- Sanitize TMS errors through `map_tms_error_to_http`.

In `dependencies.py`:

- Expose `get_token_management_system()` as the single public TMS accessor.
- Expose `tms_audit_context(source, correlation_id=None)` for non-MCP callers.
- Build a standalone TMS instance only when MCP auth did not initialize one but tool OAuth needs the vault.

- [ ] **Step 3: Run tests**

```bash
poetry run pytest tests/codemie/service/oauth/test_token_port.py tests/codemie/service/oauth/test_token_port_connection.py -v
```

Expected: PASS.

---

## Task 2: Signed state and encrypted Redis state store

**Files:**
- Create: `src/codemie/service/oauth_state_signing.py`
- Create: `src/codemie/service/oauth/state_store.py`
- Modify: `src/codemie/service/gitlab_oauth/state_store.py`
- Create/Modify: `src/codemie/service/atlassian_oauth/state_store.py`
- Test: `tests/codemie/service/oauth/test_signed_state.py`
- Test: `tests/codemie/rest_api/routers/test_oauth_redis_lazy_init.py`

**Interfaces:**
- Produces: `create_signed_oauth_state(...) -> str`.
- Produces: `decode_and_verify_oauth_state(...) -> OAuthStatePayload`.
- Produces: `OAuthStateStore.store_state`, `consume_state`, `store_result`, `get_result`, and `get_pending_state`.
- Produces: `GitLabOAuthStateStore` and `AtlassianOAuthStateStore` subclasses that only set `namespace` and `label`.

- [ ] **Step 1: Write the tests first**

Cover:

- signed state round-trips all bound fields;
- tampered signature and tampered payload are rejected;
- wrong secret, expired token, unsupported provider, and malformed tokens are rejected;
- provider allowlist accepts the shared Atlassian callback for both Jira and Confluence;
- Redis state and result payloads are encrypted, namespaced, TTL-bound, and lazily initialized.

Run:

```bash
poetry run pytest tests/codemie/service/oauth/test_signed_state.py tests/codemie/rest_api/routers/test_oauth_redis_lazy_init.py -v
```

Expected before implementation: FAIL because signed state and the shared state store do not exist.

- [ ] **Step 2: Implement signed state**

In `oauth_state_signing.py`:

- Keep the module stdlib-only.
- Use HMAC-SHA256 with base64url payload/signature parts.
- Bind `provider`, `user_id`, `integration_id`, `redirect_uri`, `nonce`, `ts`, and version `v=1`.
- Verify signatures with `hmac.compare_digest`.
- Enforce max age and provider allowlists.
- Raise `OAuthStateValidationError` on every validation failure.

- [ ] **Step 3: Implement shared state storage**

In `service/oauth/state_store.py`:

- Store pending authorization state under `codemie:{namespace}:state:{state}` for 600 seconds.
- Store callback results under `codemie:{namespace}:result:{state}` for 300 seconds.
- Encrypt JSON payloads with `EncryptionFactory().get_current_encryption_service()`.
- Use Redis `getdel` when consuming state so a state is single-use.
- Treat decrypt/JSON corruption as invalid state, but surface Redis outages as temporary service errors.
- Do not add refresh-lock primitives.

In provider state stores:

- `GitLabOAuthStateStore.namespace = "gitlab_oauth"`, `label = "GitLab"`.
- `AtlassianOAuthStateStore.namespace = "atlassian_oauth"`, `label = "Atlassian"`.

- [ ] **Step 4: Run tests**

```bash
poetry run pytest tests/codemie/service/oauth/test_signed_state.py tests/codemie/rest_api/routers/test_oauth_redis_lazy_init.py -v
```

Expected: PASS.

---

## Task 3: Provider-agnostic OAuth flow engine and provider adapters

**Files:**
- Create: `src/codemie/service/oauth/flow_engine.py`
- Create: `src/codemie/service/oauth/provider_adapters.py`
- Create/Modify: `src/codemie/service/oauth/__init__.py`
- Test: `tests/codemie/service/oauth/test_flow_engine.py`

**Interfaces:**
- Produces: `OAuthFlowEngine.initiate_flow`, `handle_callback`, and `get_status`.
- Produces: `CallbackResult` and `OAuthTokenPayload` dataclasses.
- Produces: provider adapters for GitLab, Jira, and Confluence.
- Consumes: signed state, encrypted state store, provider constants, and provider settings services.

- [ ] **Step 1: Write the tests first**

Cover:

- PKCE verifier/challenge generation and state storage on initiate;
- missing app credentials raises the provider's configured message;
- callback verifies signed state before token exchange;
- callback cross-checks signed state against encrypted Redis state for `user_id`, `integration_id`, `redirect_uri`, and `provider`;
- provider errors short-circuit token exchange;
- successful callback stores encrypted result and invokes provider persistence when `integration_id` is present;
- persistence failure stores an error result and returns a user-facing failure;
- status lookup returns pending, success, error, or not-found only to the owning user.

Run:

```bash
poetry run pytest tests/codemie/service/oauth/test_flow_engine.py -v
```

Expected before implementation: FAIL because the engine does not exist.

- [ ] **Step 2: Implement `OAuthFlowEngine`**

- Generate PKCE verifier with `secrets.token_urlsafe(96)`.
- Generate challenge with SHA-256 + base64url without padding.
- Require provider credentials from the integration request; do not read global client id/secret config.
- Call `assert_secure_token_storage(provider_label)` before starting a flow.
- Create signed state with the provider, user id, integration id, and redirect URI.
- Store encrypted state with client id, client secret, redirect URI, code verifier, user id, provider, integration id, and adapter context.
- In callback, verify signed state, consume Redis state, cross-check all bound fields, exchange code, finalize payload, store encrypted result, then persist the per-user token when an integration id is bound.
- In status lookup, never expose another user's state/result.

- [ ] **Step 3: Implement provider adapters**

GitLab adapter:

- Builds redirect URI with GitLab constants.
- Enforces allowed `instance_url` before constructing authorize/token/user URLs.
- Requests configured GitLab scopes.
- Exchanges code at the selected instance token endpoint.
- Fetches `/api/v4/user` for username, id, and email.
- Persists through `GitLabOAuthSettingsService().persist_user_token(...)`.

Atlassian adapters:

- Use the shared Atlassian authorize/token endpoints and PKCE.
- Jira adapter accepts both `jira` and `confluence` state providers so the shared Atlassian callback can process both products.
- Confluence adapter uses the Confluence redirect URI builder but otherwise shares the Atlassian exchange logic.
- Fetch accessible resources for `cloud_id`, site URL, and site name.
- Fetch `/me` for username/account id/email.
- Persist through the matching Jira or Confluence settings service.

- [ ] **Step 4: Run tests**

```bash
poetry run pytest tests/codemie/service/oauth/test_flow_engine.py -v
```

Expected: PASS.

---

## Task 4: Startup guards and configuration

**Files:**
- Modify: `src/codemie/service/oauth_security.py`
- Modify: `src/codemie/configs/config.py`
- Modify: `src/codemie/rest_api/main.py`
- Test: `tests/codemie/service/oauth/test_signed_state.py`
- Test: `tests/codemie/service/oauth/test_token_port.py`

**Interfaces:**
- Produces: `assert_secure_token_storage(provider_label)`.
- Produces: `assert_oauth_state_signing_secret_configured()`.
- Produces: `assert_token_vault_available()`.
- Produces: `warn_insecure_oauth_storage_for_enabled_providers()`.
- Consumes: enterprise `OAuth2TokenData` fields `provider_username`, `provider_user_id`, and `provider_metadata`.

- [ ] **Step 1: Write the tests first**

Cover:

- OAuth flow initiation fails closed when token storage is non-confidential and the local escape hatch is off.
- startup fails when a provider is enabled without a 32-byte state signing secret.
- startup fails when enterprise TMS is unavailable or too old.
- startup passes when OAuth providers are disabled.
- provider flags default to disabled.

Run the focused OAuth tests after adding the cases:

```bash
poetry run pytest tests/codemie/service/oauth -v
```

Expected before implementation: FAIL for missing guards or missing config fields.

- [ ] **Step 2: Add config**

In `config.py`:

- Add `GITLAB_OAUTH_ENABLED = False`, `JIRA_OAUTH_ENABLED = False`, and `CONFLUENCE_OAUTH_ENABLED = False`.
- Add provider scopes and GitLab instance allowlist/default URL.
- Add `OAUTH_ALLOW_INSECURE_TOKEN_STORAGE = False` for local development only.
- Add `OAUTH_STATE_SIGNING_SECRET = ""` and `OAUTH_STATE_SIGNING_MAX_AGE_SECONDS = 600`.
- Keep `MCP_AUTH_ENABLED = False`.
- Do not add provider-level client id/secret fallback fields.

- [ ] **Step 3: Add guards**

In `oauth_security.py`:

- Treat plain/base64 encryption as non-confidential.
- Raise 503 during flow initiation when a provider is enabled with non-confidential encryption and the local escape hatch is off.
- Require `OAUTH_STATE_SIGNING_SECRET` length >= 32 bytes when any provider is enabled.
- Require `codemie-enterprise >= 2.3.38` by checking `OAuth2TokenData` for `provider_username`, `provider_user_id`, and `provider_metadata`.
- Require the token vault to be available before serving OAuth-enabled traffic.

In `main.py` lifespan:

- Call `assert_oauth_state_signing_secret_configured()`.
- Call `assert_token_vault_available()`.
- Call `warn_insecure_oauth_storage_for_enabled_providers()`.

- [ ] **Step 4: Run tests**

```bash
poetry run pytest tests/codemie/service/oauth -v
```

Expected: PASS.

---

## Task 5: Credential enum migrations

**Files:**
- Modify: `src/codemie_tools/base/models.py`
- Create: `src/external/alembic/versions/g1l2a3b4o5a6_add_gitlab_oauth_credential_type.py`
- Create: `src/external/alembic/versions/j1r2a3o4a5u6_add_jira_oauth_credential_type.py`
- Create: `src/external/alembic/versions/c1o2n3f4l5u6_add_confluence_oauth_credential_type.py`
- Test: existing settings and router tests that instantiate the credential types.

**Interfaces:**
- Produces: `CredentialTypes.GITLAB_OAUTH`.
- Produces: `CredentialTypes.JIRA_OAUTH`.
- Produces: `CredentialTypes.CONFLUENCE_OAUTH`.
- Produces: Alembic enum migrations only.

- [ ] **Step 1: Write/extend tests first**

Add focused assertions where provider settings are constructed so each new credential type can be serialized and used by settings lookup.

Run:

```bash
poetry run pytest tests/codemie/service/settings/test_gitlab_oauth_get_config_gate.py tests/codemie/service/settings/test_jira_oauth_get_config_gate.py tests/codemie/service/settings/test_confluence_oauth_get_config_gate.py -v
```

Expected before implementation: FAIL because the enum values do not exist.

- [ ] **Step 2: Add enum values**

In `codemie_tools/base/models.py`, add GitLab, Jira, and Confluence OAuth credential types using the naming style of existing credential types.

- [ ] **Step 3: Add Alembic migrations**

Create one migration per provider, following the existing enum migration helper pattern:

- upgrade adds the provider enum value to `credentialtypes`;
- downgrade removes settings rows of that provider type, then recreates the enum without the value;
- no token-storage tables or token columns are created.

- [ ] **Step 4: Run tests**

```bash
poetry run pytest tests/codemie/service/settings/test_gitlab_oauth_get_config_gate.py tests/codemie/service/settings/test_jira_oauth_get_config_gate.py tests/codemie/service/settings/test_confluence_oauth_get_config_gate.py -v
```

Expected: PASS.

---

## Task 6: GitLab OAuth service wiring

**Files:**
- Modify: `src/codemie/service/gitlab_oauth/constants.py`
- Modify: `src/codemie/service/gitlab_oauth/flow_service.py`
- Modify: `src/codemie/service/gitlab_oauth/settings_service.py`
- Modify: `src/codemie/service/gitlab_oauth/token_manager.py`
- Modify: `src/codemie/service/gitlab_oauth/state_store.py`
- Test: `tests/codemie/service/gitlab_oauth/test_settings_service_writes_token_row.py`
- Test: `tests/codemie/service/gitlab_oauth/test_token_manager_per_user.py`

**Interfaces:**
- Produces: `GitLabOAuthFlowService` wrapper over `OAuthFlowEngine`.
- Produces: `GitLabOAuthSettingsService.populate_credentials_from_flow`, `persist_user_token`, and `get_preserved_credential_keys`.
- Produces: `GitLabOAuthTokenManager.get_valid_access_token(setting_id, user_id)` and `revoke_token(setting_id, user_id)`.

- [ ] **Step 1: Write the tests first**

Cover:

- `persist_user_token` reads the completed flow result, decrypts token payload, validates access/refresh token presence, reads app credentials from `Settings`, builds `OAuth2RefreshMetadata`, and calls `ToolOAuthTokenPort.save_oauth2_token` with `integration_id=setting_id` and `user_id`.
- GitLab provider metadata includes normalized `instance_url`.
- `get_valid_access_token` validates the setting type and returns the TMS access token for the acting user.
- missing TMS token raises `GitLabOAuthNotConnected`.
- `revoke_token` best-effort posts the refresh token to the GitLab revoke endpoint and does not delete the TMS record itself.

Run:

```bash
poetry run pytest tests/codemie/service/gitlab_oauth/test_settings_service_writes_token_row.py tests/codemie/service/gitlab_oauth/test_token_manager_per_user.py -v
```

Expected before implementation: FAIL for missing TMS-backed wiring.

- [ ] **Step 2: Wire the flow service**

- Wrap `OAuthFlowEngine` with `GitLabOAuthProviderAdapter` and `GitLabOAuthStateStore`.
- Pass `instance_url` in adapter context.
- Return `auth_url` and `state` from initiate.
- Delegate callback and status to the shared engine.

- [ ] **Step 3: Implement settings service persistence**

- Keep app-credential preservation to `instance_url`, `client_id`, `client_secret`, and `callback_base_url`.
- Keep `populate_credentials_from_flow` for create/update flows that already read the encrypted result.
- In `persist_user_token`, reload the saved setting, decrypt `client_secret`, validate `client_id` and `instance_url`, calculate remaining expiry seconds, build GitLab refresh metadata, and store the token through TMS.
- Store provider identity as `provider_username` and `provider_user_id`; store `instance_url` in `provider_metadata`.
- Map TMS failures through `map_tms_error_to_http("GitLab", exc)`.

- [ ] **Step 4: Implement token manager**

- Validate setting existence and `CredentialTypes.GITLAB_OAUTH`.
- Retrieve the acting user's token through `ToolOAuthTokenPort.get_oauth2_token_or_none(user_id=user_id, integration_id=setting_id)`.
- Return `token_data.access_token`.
- Raise `GitLabOAuthNotConnected(setting_id)` when no usable token exists.
- Revoke refresh tokens best-effort using app credentials from the setting; leave deletion to the caller.

- [ ] **Step 5: Run tests**

```bash
poetry run pytest tests/codemie/service/gitlab_oauth -v
```

Expected: PASS.

---

## Task 7: Jira and Confluence OAuth service wiring

**Files:**
- Modify: `src/codemie/service/atlassian_oauth/state_store.py`
- Modify: `src/codemie/service/jira_oauth/{constants.py,flow_service.py,settings_service.py,token_manager.py}`
- Modify: `src/codemie/service/confluence_oauth/{constants.py,flow_service.py,settings_service.py,token_manager.py}`
- Test: `tests/codemie/service/jira_oauth/test_settings_service_writes_token_row.py`
- Test: `tests/codemie/service/jira_oauth/test_token_manager_per_user.py`
- Test: `tests/codemie/service/confluence_oauth/test_settings_service_writes_token_row.py`
- Test: `tests/codemie/service/confluence_oauth/test_token_manager_per_user.py`

**Interfaces:**
- Produces: `JiraOAuthFlowService` and `ConfluenceOAuthFlowService` wrappers over `OAuthFlowEngine`.
- Produces: `JiraOAuthSettingsService.persist_user_token` and `ConfluenceOAuthSettingsService.persist_user_token`.
- Produces: `JiraOAuthTokenManager` and `ConfluenceOAuthTokenManager` with `get_valid_access_token`, `get_cloud_id`, and `revoke_token`.

- [ ] **Step 1: Write the tests first**

Cover for each provider:

- completed flow result persists tokens to `ToolOAuthTokenPort.save_oauth2_token` with `integration_id=setting_id` and `user_id`;
- missing access/refresh token raises an `ExtendedHTTPException` before TMS is called;
- app credentials are loaded from `Settings` and `client_secret` is decrypted;
- `OAuth2RefreshMetadata` uses the Atlassian token endpoint and `client_secret_post`;
- `provider_metadata` contains `cloud_id`, `site_url`, and `site_name`;
- token manager returns the acting user's TMS access token;
- missing token raises the provider's typed auth-required exception;
- `get_cloud_id` reads `provider_metadata.cloud_id`.

Run:

```bash
poetry run pytest tests/codemie/service/jira_oauth tests/codemie/service/confluence_oauth -v
```

Expected before implementation: FAIL for missing TMS-backed wiring.

- [ ] **Step 2: Wire the shared Atlassian state store and flow services**

- Use one `AtlassianOAuthStateStore` namespace for both products.
- Jira uses `provider="jira"`; Confluence uses `provider="confluence"`.
- The Jira adapter accepts both state providers because the Atlassian callback is shared.
- Delegate callback/status handling to the shared engine.

- [ ] **Step 3: Implement settings services**

For Jira and Confluence:

- Preserve only `client_id`, `client_secret`, and `callback_base_url` on the setting row.
- Read the encrypted callback result, require both access and refresh token, parse scopes, and build `OAuth2RefreshMetadata`.
- Store provider username/account id and Atlassian cloud metadata in TMS.
- Map TMS failures through the provider label.

- [ ] **Step 4: Implement token managers**

For Jira and Confluence:

- Validate setting existence and provider credential type.
- Retrieve the acting user's access token through `ToolOAuthTokenPort.get_oauth2_token_or_none`.
- Raise the provider auth-required exception when no usable token exists.
- Implement `get_cloud_id` from `token_data.provider_metadata`.
- Keep `revoke_token` as a debug no-op because Atlassian 3LO does not provide a simple server-side revoke path here; deletion is handled by TMS.

- [ ] **Step 5: Run tests**

```bash
poetry run pytest tests/codemie/service/jira_oauth tests/codemie/service/confluence_oauth -v
```

Expected: PASS.

---

## Task 8: OAuth routers and connection endpoints

**Files:**
- Modify: `src/codemie/rest_api/routers/gitlab_oauth.py`
- Modify: `src/codemie/rest_api/routers/jira_oauth.py`
- Modify: `src/codemie/rest_api/routers/confluence_oauth.py`
- Test: `tests/codemie/rest_api/routers/test_gitlab_oauth_connect.py`
- Test: `tests/codemie/rest_api/routers/test_jira_oauth_shared_callback.py`

**Interfaces:**
- Produces: `POST /v1/gitlab-oauth/initiate`, `GET /v1/gitlab-oauth/callback`, `GET /v1/gitlab-oauth/status/{state}`, `POST /v1/gitlab-oauth/connect`, `GET /v1/gitlab-oauth/connection?setting_id=...`, `DELETE /v1/gitlab-oauth/connection?setting_id=...`.
- Produces: `POST /v1/atlassian-oauth/initiate`, `GET /v1/atlassian-oauth/callback`, `GET /v1/atlassian-oauth/status/{state}`, `POST /v1/atlassian-oauth/connect`, `GET /v1/atlassian-oauth/connection?setting_id=...`, `DELETE /v1/atlassian-oauth/connection?setting_id=...` for Jira.
- Produces: `POST /v1/confluence-oauth/initiate`, `GET /v1/confluence-oauth/status/{state}`, `POST /v1/confluence-oauth/connect`, `GET /v1/confluence-oauth/connection?setting_id=...`, `DELETE /v1/confluence-oauth/connection?setting_id=...`.
- Consumes: per-provider flow services and `ToolOAuthTokenPort`.

- [ ] **Step 1: Write the tests first**

Cover:

- connection status returns `{status: "not_connected", username: ""}` for a caller without a token;
- connection status returns `{status: "connected", username}` from `provider_username` for the caller's token;
- connection status queries TMS with `(user.id, setting_id)` and never by another user's id;
- disconnect revokes provider-side where supported and deletes only the caller's TMS credential;
- connect loads app credentials from the selected setting, decrypts `client_secret`, rejects wrong credential types, and passes `integration_id=setting_id` into the flow;
- GitLab callback returns secured HTML;
- Atlassian callback remains available when either Jira or Confluence OAuth is enabled.

Run:

```bash
poetry run pytest tests/codemie/rest_api/routers/test_gitlab_oauth_connect.py tests/codemie/rest_api/routers/test_jira_oauth_shared_callback.py -v
```

Expected before implementation: FAIL for missing endpoints or shared-callback gating.

- [ ] **Step 2: Implement GitLab router**

- Keep `/initiate` for integration creation and verification flows with app credentials supplied in the body.
- Add `/connect` for existing integrations; load app credentials from `Settings`, decrypt `client_secret`, validate `client_id`, `client_secret`, `callback_base_url`, and `instance_url`, then call `initiate_flow(user.id, integration_id=setting_id, ...)`.
- Keep `/status/{state}` flow-scoped; strip encrypted `token_data` before returning to the client.
- Add `/connection` for durable per-user state using TMS lookup.
- Add `DELETE /connection` to best-effort revoke and delete the caller's token through TMS.

- [ ] **Step 3: Implement Jira router**

- Use prefix `/v1/atlassian-oauth`.
- Keep `/callback` shared for Jira and Confluence; it is enabled when either product is enabled.
- Implement `/initiate`, `/status/{state}`, `/connect`, `/connection`, and `DELETE /connection` with Jira credential type validation.
- Strip encrypted `token_data` from status responses.

- [ ] **Step 4: Implement Confluence router**

- Use prefix `/v1/confluence-oauth`.
- Do not add a Confluence-specific callback; Atlassian redirects to `/v1/atlassian-oauth/callback`.
- Implement `/initiate`, `/status/{state}`, `/connect`, `/connection`, and `DELETE /connection` with Confluence credential type validation.

- [ ] **Step 5: Run tests**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_gitlab_oauth_connect.py tests/codemie/rest_api/routers/test_jira_oauth_shared_callback.py -v
```

Expected: PASS.

---

## Task 9: Settings service integration lifecycle

**Files:**
- Modify: `src/codemie/service/settings/settings.py`
- Test: `tests/codemie/service/settings/test_gitlab_oauth_get_config_gate.py`
- Test: `tests/codemie/service/settings/test_jira_oauth_get_config_gate.py`
- Test: `tests/codemie/service/settings/test_confluence_oauth_get_config_gate.py`
- Test: `tests/codemie/service/settings/test_gitlab_oauth_delete_cleans_tokens.py`

**Interfaces:**
- Produces: `_keep_only_oauth_app_credentials(request)`.
- Produces: `_persist_oauth_creator_token(request, user_id, setting_id)`.
- Produces: `_inject_oauth_config_values(setting, user_id=None) -> dict`.
- Produces: `_cleanup_{gitlab,jira,confluence}_oauth_tokens(setting_id)`.
- Consumes: provider settings services and `ToolOAuthTokenPort.invalidate_by_integration`.

- [ ] **Step 1: Write the tests first**

Cover:

- create/update for GitLab, Jira, and Confluence persists only app credential keys on `Settings`;
- when `oauth_state` is present, the creator's token is persisted after the setting row exists;
- create rolls back the setting if token persistence fails;
- update restores the prior setting state if token persistence fails;
- alias-only updates preserve existing app credentials;
- `get_config` injects `auth_type="oauth"`, `integration_id=setting.id`, and `acting_user_id=user_id`;
- GitLab config mirrors `instance_url` onto `url`;
- Jira and Confluence configs set `cloud=True`;
- deleting an integration invalidates all TMS credentials for that `setting_id`.

Run:

```bash
poetry run pytest tests/codemie/service/settings/test_gitlab_oauth_get_config_gate.py tests/codemie/service/settings/test_jira_oauth_get_config_gate.py tests/codemie/service/settings/test_confluence_oauth_get_config_gate.py tests/codemie/service/settings/test_gitlab_oauth_delete_cleans_tokens.py -v
```

Expected before implementation: FAIL for token-in-setting behavior, missing config injection, or missing invalidation.

- [ ] **Step 2: Store app credentials only**

- Define app-credential key lists:
  - GitLab: `client_id`, `client_secret`, `callback_base_url`, `instance_url`.
  - Jira: `client_id`, `client_secret`, `callback_base_url`.
  - Confluence: `client_id`, `client_secret`, `callback_base_url`.
- Before preparing/encrypting credentials on create or update, filter provider requests to those app keys.
- Preserve missing app keys during updates using each provider settings service's `get_preserved_credential_keys`.

- [ ] **Step 3: Persist creator tokens atomically**

- After a setting is saved and only when `oauth_state` is present, call the provider settings service `persist_user_token(oauth_state, user_id, setting_id)`.
- If create token persistence fails, delete the just-created setting row.
- If update token persistence fails, restore the original credentials, alias, scope, hash, and update date before re-raising.

- [ ] **Step 4: Inject OAuth config values**

- For provider OAuth settings, normalize values and inject:
  - `auth_type="oauth"`;
  - `integration_id=setting.id`;
  - `acting_user_id=user_id or ""`.
- For GitLab, copy `instance_url` to `url` when `url` is absent.
- For Jira and Confluence, set `cloud=True`.
- Prefer OAuth-backed settings for GitLab/Jira/Confluence tools before falling back to PAT/basic settings.
- For GitLab clone credentials, call `GitLabOAuthTokenManager().get_valid_access_token(setting.id, user_id)` and return username `oauth2`.

- [ ] **Step 5: Invalidate on integration delete**

- In provider delete branches, call `ToolOAuthTokenPort.invalidate_by_integration(setting_id)`.
- Map provider-specific TMS failures through `map_tms_error_to_http`.
- Delete the setting only after provider cleanup succeeds.

- [ ] **Step 6: Run tests**

```bash
poetry run pytest tests/codemie/service/settings/test_gitlab_oauth_get_config_gate.py tests/codemie/service/settings/test_jira_oauth_get_config_gate.py tests/codemie/service/settings/test_confluence_oauth_get_config_gate.py tests/codemie/service/settings/test_gitlab_oauth_delete_cleans_tokens.py -v
```

Expected: PASS.

---

## Task 10: Tool model and runtime token resolution

**Files:**
- Modify: `src/codemie_tools/core/vcs/gitlab/models.py`
- Modify: `src/codemie_tools/core/vcs/gitlab/tools.py`
- Modify: `src/codemie_tools/core/project_management/jira/models.py`
- Modify: `src/codemie_tools/core/project_management/jira/tools.py`
- Modify: `src/codemie_tools/core/project_management/confluence/models.py`
- Modify: `src/codemie_tools/core/project_management/confluence/tools.py`
- Test: `tests/codemie_tools/core/vcs/gitlab/test_tools_resolve_per_user.py`
- Test: `tests/codemie_tools/core/project_management/jira/test_tools_lazy_oauth.py`

**Interfaces:**
- Produces: `acting_user_id` on GitLab, Jira, and Confluence config models.
- Produces: `cloud_id` on Jira and Confluence config models.
- Consumes: provider token managers.

- [ ] **Step 1: Write the tests first**

Cover:

- GitLab OAuth access-token resolution calls `GitLabOAuthTokenManager().get_valid_access_token(integration_id, acting_user_id)`;
- Jira OAuth resolution calls `JiraOAuthTokenManager` with `(integration_id, acting_user_id)` and obtains `cloud_id` when not already present;
- Confluence OAuth resolution mirrors the Jira pattern;
- PAT/basic auth paths still work without an acting user id;
- provider auth-required exceptions surface as tool auth requirements rather than generic failures.

Run:

```bash
poetry run pytest tests/codemie_tools/core/vcs/gitlab/test_tools_resolve_per_user.py tests/codemie_tools/core/project_management/jira/test_tools_lazy_oauth.py -v
```

Expected before implementation: FAIL because configs do not carry the acting user or tools call old token-manager signatures.

- [ ] **Step 2: Add config fields**

- Add `acting_user_id: str = Field(default="", exclude=True, ...)` to GitLab, Jira, and Confluence configs.
- Add `cloud_id` to Jira and Confluence configs so TMS metadata can drive `https://api.atlassian.com/ex/{product}/{cloud_id}` base URLs.

- [ ] **Step 3: Resolve tokens at runtime**

- GitLab: if `auth_type == "oauth"`, resolve through `GitLabOAuthTokenManager` with `(integration_id, acting_user_id)`.
- Jira: if `auth_type == "oauth"`, resolve access token and cloud id through `JiraOAuthTokenManager` and build the Atlassian API base URL.
- Confluence: same pattern using `ConfluenceOAuthTokenManager`.
- Leave non-OAuth auth types unchanged.

- [ ] **Step 4: Run tests**

```bash
poetry run pytest tests/codemie_tools/core/vcs/gitlab/test_tools_resolve_per_user.py tests/codemie_tools/core/project_management/jira/test_tools_lazy_oauth.py -v
```

Expected: PASS.

---

## Task 11: Aggregate OAuth connect gate

**Files:**
- Create: `src/codemie/service/tools/oauth_connect_gate.py`
- Modify: the agent/tool assembly path that runs before streaming starts
- Test: `tests/codemie/service/tools/test_oauth_connect_gate.py`

**Interfaces:**
- Produces: `enforce_oauth_connected(tools, user_id) -> None`.
- Produces: `OAuthConnectRequiredException` payloads with provider-specific error codes and setting ids.
- Consumes: `ToolOAuthTokenPort.has_connection`.

- [ ] **Step 1: Write the tests first**

Cover:

- no user id or no OAuth tools is a no-op;
- connected tools pass;
- one unconnected GitLab/Jira/Confluence tool raises an aggregate auth-required exception;
- the same shared integration appears only once in the payload;
- multiple providers aggregate into one payload;
- TMS service failures map to service errors and do not look like missing credentials.

Run:

```bash
poetry run pytest tests/codemie/service/tools/test_oauth_connect_gate.py -v
```

Expected before implementation: FAIL because the gate does not exist or is not called.

- [ ] **Step 2: Implement target discovery**

- Inspect tool configs and select only `auth_type == "oauth"` configs.
- Map config types to error codes:
  - GitLab: `gitlab_auth_required`.
  - Jira: `jira_auth_required`.
  - Confluence: `confluence_auth_required`.
- Record `(setting_id, error_code, integration_name)`.

- [ ] **Step 3: Implement the gate**

- Deduplicate by `setting_id`.
- Call `ToolOAuthTokenPort.has_connection(user_id=user_id, integration_id=setting_id)`.
- Raise `OAuthConnectRequiredException(providers)` if any provider is not connected.
- Map TMS failures through `map_tms_error_to_http("Tool", exc)`.
- Invoke the gate before a streaming response begins, while the UI can still render a structured sign-in prompt.

- [ ] **Step 4: Run tests**

```bash
poetry run pytest tests/codemie/service/tools/test_oauth_connect_gate.py -v
```

Expected: PASS.

---

## Task 12: Provider API behavior and error handling

**Files:**
- Modify: `src/codemie/core/exceptions.py`
- Modify: `src/codemie/rest_api/routers/{gitlab,jira,confluence}_oauth.py`
- Modify: `src/codemie/service/{gitlab,jira,confluence}_oauth/token_manager.py`
- Test: `tests/codemie/rest_api/routers/test_gitlab_oauth_connect.py`
- Test: `tests/codemie_tools/core/vcs/gitlab/test_tools_resolve_per_user.py`
- Test: `tests/codemie_tools/core/project_management/jira/test_tools_lazy_oauth.py`

**Interfaces:**
- Produces: provider-specific not-connected exceptions.
- Produces: durable connection responses with `status` and `username`.
- Produces: sanitized TMS errors for routers and token managers.

- [ ] **Step 1: Write the tests first**

Cover:

- a missing user token raises `GitLabOAuthNotConnected`, `JiraAuthRequiredException`, or `ConfluenceAuthRequiredException`;
- routers return not-connected instead of a 500 for missing user credentials;
- routers map TMS service faults to sanitized 502/503 responses;
- status polling never includes encrypted `token_data`;
- disabled provider flags return 503 at endpoints;
- wrong integration credential type returns 400.

Run:

```bash
poetry run pytest tests/codemie/rest_api/routers/test_gitlab_oauth_connect.py tests/codemie_tools/core/vcs/gitlab/test_tools_resolve_per_user.py tests/codemie_tools/core/project_management/jira/test_tools_lazy_oauth.py -v
```

Expected before implementation: FAIL for generic errors or leaked token data.

- [ ] **Step 2: Implement exceptions**

- Add a GitLab typed exception with a user-facing "connect your GitLab account" message.
- Reuse MCP-style auth-required exceptions for Jira and Confluence so clients can render actionable prompts.
- Add `OAuthConnectRequiredException` for the aggregate pre-stream gate.

- [ ] **Step 3: Normalize router behavior**

- All provider endpoints call `_require_enabled()`.
- `/status/{state}` returns 202 for pending, 200 for success, and 400 for error/not-found.
- `/status/{state}` removes encrypted `token_data` before responding.
- `/connection` reports only the caller's TMS token state and `provider_username`.
- `DELETE /connection` removes only the caller's token.

- [ ] **Step 4: Normalize token-manager behavior**

- Validate setting type before TMS lookup.
- Treat `None` token data and blank access token as not connected.
- Let TMS refresh happen inside `retrieve`; token managers do not implement their own refresh locks.
- Map TMS faults through `map_tms_error_to_http`.

- [ ] **Step 5: Run tests**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_gitlab_oauth_connect.py tests/codemie_tools/core/vcs/gitlab/test_tools_resolve_per_user.py tests/codemie_tools/core/project_management/jira/test_tools_lazy_oauth.py -v
```

Expected: PASS.

---

## Task 13: Full-suite gate + manual verification

- [ ] **Step 1: Run the affected suites**

```bash
poetry run pytest tests/codemie/service/oauth tests/codemie/service/gitlab_oauth tests/codemie/service/jira_oauth tests/codemie/service/confluence_oauth tests/codemie/rest_api/routers/test_gitlab_oauth_connect.py tests/codemie/rest_api/routers/test_jira_oauth_shared_callback.py tests/codemie/service/settings/test_gitlab_oauth_get_config_gate.py tests/codemie/service/settings/test_jira_oauth_get_config_gate.py tests/codemie/service/settings/test_confluence_oauth_get_config_gate.py tests/codemie/service/settings/test_gitlab_oauth_delete_cleans_tokens.py tests/codemie/service/tools/test_oauth_connect_gate.py tests/codemie_tools/core/vcs/gitlab/test_tools_resolve_per_user.py tests/codemie_tools/core/project_management/jira/test_tools_lazy_oauth.py -v
```

Expected: all PASS.

- [ ] **Step 2: Lint**

```bash
make ruff
```

Expected: clean.

- [ ] **Step 3: Full test suite**

```bash
make test
```

Expected: clean.

- [ ] **Step 4: License gate**

```bash
make license-check
```

Expected: clean.

- [ ] **Step 5: Manual end-to-end verification (two users, one shared PROJECT integration)**

1. Enable the provider, configure `OAUTH_STATE_SIGNING_SECRET`, and ensure enterprise TMS is available.
2. As admin, create a PROJECT GitLab OAuth integration, fill app credentials, and sign in with GitLab. Verify TMS stores a credential for `(admin_id, setting_id)` and the `Settings` row contains app credentials only.
3. As a second project member, call `POST /v1/gitlab-oauth/connect` for the same `setting_id`, complete the popup, and poll `GET /v1/gitlab-oauth/status/{state}` until success.
4. As that member, call `GET /v1/gitlab-oauth/connection?setting_id=...` and verify it reports `connected` with the member's provider username.
5. Run an assistant using the GitLab integration as the member and verify provider calls use the member's OAuth identity.
6. Repeat the connect/status/connection flow for Jira and Confluence; verify Atlassian `cloud_id` comes from TMS `provider_metadata`.
7. As a member with no token, run an assistant that needs the integration and verify the aggregate connect-required payload lists the missing provider and `setting_id` before streaming starts.
8. Disconnect one member with `DELETE /connection?setting_id=...`; verify only that member's TMS credential is removed.
9. Delete the integration as admin; verify all TMS credentials for that `setting_id` are invalidated.

---

## Author Notes

- **Spec coverage:** TMS token storage (Tasks 1, 6, 7, 9), signed state and state storage (Tasks 2-3), startup guardrails (Task 4), provider enum availability (Task 5), provider routes (Task 8), acting-user resolution (Tasks 9-10), aggregate connection gating (Task 11), and end-to-end quality gates (Task 13).
- **Provider-agnostic core:** GitLab, Jira, and Confluence share the same token port, flow engine, state store base class, signed-state format, and TMS error mapping.
- **Security posture:** Tokens are encrypted and rotated by TMS, state is signed and encrypted in Redis, refresh locking is centralized, and app credentials never fall back to process-wide provider secrets.
