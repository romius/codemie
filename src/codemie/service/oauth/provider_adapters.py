# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Provider adapters for the shared OAuth flow engine."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from codemie.configs import config, logger
from codemie.service.confluence_oauth.constants import (
    ACCESSIBLE_RESOURCES_URL,
    API_AUDIENCE,
    AUTHORIZE_URL,
    CONFLUENCE_OAUTH_ERROR_DESCRIPTIONS,
    ME_URL,
    TOKEN_URL,
    build_redirect_uri as build_confluence_redirect_uri,
)
from codemie.service.gitlab_oauth.constants import (
    GITLAB_OAUTH_ERROR_DESCRIPTIONS,
    authorize_url,
    build_redirect_uri as build_gitlab_redirect_uri,
    ensure_instance_allowed,
    token_url,
    user_url,
)
from codemie.service.jira_oauth.constants import (
    JIRA_OAUTH_ERROR_DESCRIPTIONS,
    build_redirect_uri as build_jira_redirect_uri,
)
from codemie.service.oauth.flow_engine import CallbackContext, OAuthCallbackError, OAuthTokenPayload

_JIRA_STATE_PROVIDER = "jira"
_CONFLUENCE_STATE_PROVIDER = "confluence"


def _bearer_json_headers(access_token: str) -> dict[str, str]:
    """Headers for the provider profile/resource lookups made during the OAuth callback."""
    return {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}


def _exchange_authorization_code(
    token_endpoint: str,
    *,
    code: str,
    code_verifier: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    provider_label: str,
    default_expires_in: int,
    scopes: list[str],
) -> "OAuthTokenPayload":
    """Authorization Code + PKCE token exchange via the enterprise generic exchange helper.

    Reuses ``codemie_enterprise.mcp_auth.exchange_authorization_code`` so the tool OAuth flows share
    the same audited code-for-token exchange as MCP OAuth, without constructing any MCP config
    objects. ``client_secret_post`` (the default) matches GitLab/Atlassian confidential clients; no
    RFC-8707 ``resource`` indicator is sent. The returned ``OAuth2TokenData.expires_at`` is mapped
    back to ``expires_in``; the tool-specific refresh metadata is still built by the settings services.
    """
    from codemie_enterprise.mcp_auth import MCPAuthTokenExchangeError, exchange_authorization_code

    try:
        with httpx.Client(timeout=15) as http_client:
            token_data = exchange_authorization_code(
                token_url=token_endpoint,
                client_id=client_id,
                client_secret=client_secret,
                code=code,
                code_verifier=code_verifier,
                redirect_uri=redirect_uri,
                scopes=scopes,
                http_client=http_client,
            )
    except MCPAuthTokenExchangeError as exc:
        raise ValueError(f"{provider_label} token exchange failed: {exc}") from exc

    if not token_data.access_token:
        raise ValueError(f"{provider_label} token response missing access_token")

    expires_in = default_expires_in
    if token_data.expires_at is not None:
        expires_in = max(int((token_data.expires_at - datetime.now(tz=timezone.utc)).total_seconds()), 0)

    return OAuthTokenPayload(
        access_token=token_data.access_token,
        refresh_token=token_data.refresh_token or "",
        expires_in=expires_in,
        scopes=token_data.scope or "",
    )


class GitLabOAuthProviderAdapter:
    provider_label = "GitLab"
    default_provider = "gitlab"
    expected_state_providers = {"gitlab"}
    missing_credentials_message = "GitLab OAuth is not configured. Provide the OAuth Application ID and Secret."
    unsupported_provider_message = "Unsupported GitLab OAuth provider."
    auth_failed_message = "Failed to complete authentication. Please try again."

    @staticmethod
    def get_error_message(error: str, provider: str) -> str:
        return GITLAB_OAUTH_ERROR_DESCRIPTIONS.get(error, f"Authentication failed: {error}")

    @staticmethod
    def build_redirect_uri(callback_base_url: str) -> str:
        return build_gitlab_redirect_uri(callback_base_url)

    @staticmethod
    def build_state_data(
        *,
        user_id: str,
        integration_id: str,
        provider: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str,
        code_verifier: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        instance_url = ensure_instance_allowed(
            str(context.get("instance_url") or config.GITLAB_OAUTH_DEFAULT_INSTANCE_URL)
        )
        # Only non-secret flow context. Credentials go to the encrypted creds snapshot; the
        # verifier / user_id / redirect_uri / integration_id are owned by the flow engine.
        return {"provider": provider, "instance_url": instance_url}

    @staticmethod
    def build_authorize_url(*, state_data: dict[str, Any], state: str, code_challenge: str, provider: str) -> str:
        params = {
            "client_id": state_data["client_id"],
            "redirect_uri": state_data["redirect_uri"],
            "response_type": "code",
            "state": state,
            "scope": config.GITLAB_OAUTH_SCOPES,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{authorize_url(state_data['instance_url'])}?{urlencode(params)}"

    @staticmethod
    def exchange_code_for_tokens(
        *,
        code: str,
        code_verifier: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        state_data: dict[str, Any],
    ) -> OAuthTokenPayload:
        instance_url = ensure_instance_allowed(str(state_data.get("instance_url") or ""))
        return _exchange_authorization_code(
            token_url(instance_url),
            code=code,
            code_verifier=code_verifier,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            provider_label="GitLab",
            default_expires_in=7200,
            scopes=config.GITLAB_OAUTH_SCOPES.split(),
        )

    @staticmethod
    def _fetch_user_info(instance_url: str, access_token: str) -> tuple[str, str, str]:
        headers = _bearer_json_headers(access_token)
        try:
            with httpx.Client(timeout=10) as client:
                response = client.get(user_url(instance_url), headers=headers)
                response.raise_for_status()
                data = response.json() or {}
            return (
                data.get("username", "") or "",
                str(data.get("id", "") or ""),
                data.get("email", "") or data.get("public_email", "") or "",
            )
        except Exception as exc:
            logger.warning(f"GitLab OAuth: failed to fetch user info: {exc}")
            return "", "", ""

    def finalize_callback_payload(self, context: CallbackContext) -> tuple[dict[str, Any], dict[str, Any]]:
        token_payload = context.token_payload
        instance_url = str(context.state_data.get("instance_url") or "")
        username, gitlab_user_id, email = self._fetch_user_info(instance_url, token_payload.access_token)
        token_data = {
            "access_token": token_payload.access_token,
            "refresh_token": token_payload.refresh_token,
            "expires_in": token_payload.expires_in,
            "scopes": token_payload.scopes,
            "instance_url": instance_url,
            "username": username,
            "user_id": gitlab_user_id,
        }
        return token_data, {"instance_url": instance_url, "username": username, "email": email}

    @staticmethod
    def revoke_token(*, user_id: str, integration_id: str) -> None:
        """Ask GitLab to invalidate the grant before the token is dropped from the vault."""
        from codemie.service.gitlab_oauth.token_manager import GitLabOAuthTokenManager

        GitLabOAuthTokenManager().revoke_token(integration_id, user_id)

    @staticmethod
    def persist_connected_token(*, token_data: dict, user_id: str, integration_id: str, provider: str) -> None:
        from codemie.service.gitlab_oauth.settings_service import GitLabOAuthSettingsService

        GitLabOAuthSettingsService().persist_user_token(
            token_data=token_data, user_id=user_id, setting_id=integration_id
        )


_NO_SITE_MESSAGE = (
    "Authentication succeeded, but no Atlassian site is accessible to this account. "
    "Grant the app access to a site and try again."
)

_MULTIPLE_SITES_MESSAGE = (
    "Authentication succeeded, but this account can access more than one Atlassian site for this "
    "product. Limit the app's access to a single site (at id.atlassian.com) and try again."
)

# accessible-resources entries expose per-site scopes; the product keyword tells Jira and
# Confluence sites apart so a token is never bound to a site that lacks the intended product.
_PRODUCT_SCOPE_HINT = {"jira": "jira", "confluence": "confluence"}


class _AtlassianOAuthProviderAdapterBase:
    auth_failed_message = "Failed to complete authentication. Please try again."
    unsupported_provider_message = "Unsupported Atlassian OAuth provider."

    @staticmethod
    def build_redirect_uri(callback_base_url: str) -> str:
        return build_jira_redirect_uri(callback_base_url)

    @staticmethod
    def build_state_data(
        *,
        user_id: str,
        integration_id: str,
        provider: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str,
        code_verifier: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        # Only non-secret flow context (Atlassian resolves cloud_id at callback time).
        return {"provider": provider}

    def build_authorize_url(self, *, state_data: dict[str, Any], state: str, code_challenge: str, provider: str) -> str:
        params = {
            "audience": API_AUDIENCE,
            "client_id": state_data["client_id"],
            "scope": self._scope_for_provider(provider),
            "redirect_uri": state_data["redirect_uri"],
            "state": state,
            "response_type": "code",
            "prompt": "consent",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    @staticmethod
    def exchange_code_for_tokens(
        *,
        code: str,
        code_verifier: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        state_data: dict[str, Any],
    ) -> OAuthTokenPayload:
        provider = str(state_data.get("provider") or _JIRA_STATE_PROVIDER)
        return _exchange_authorization_code(
            TOKEN_URL,
            code=code,
            code_verifier=code_verifier,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            provider_label="Atlassian",
            default_expires_in=3600,
            scopes=_AtlassianOAuthProviderAdapterBase._scope_for_provider(provider).split(),
        )

    @staticmethod
    def _select_site(resources: Any, provider: str) -> dict[str, Any]:
        """Pick the one Atlassian site for this product, or fail rather than guess.

        Sites are filtered by their granted scopes so a token is never bound to a site that lacks
        the intended product (Jira vs Confluence). If more than one site still qualifies the
        selection is ambiguous, so we raise instead of arbitrarily taking the first one.
        """
        if not isinstance(resources, list) or not resources:
            logger.warning("Atlassian OAuth: accessible-resources returned no sites for this account")
            raise OAuthCallbackError(_NO_SITE_MESSAGE)
        sites = [r for r in resources if isinstance(r, dict) and r.get("id")]
        if not sites:
            logger.warning("Atlassian OAuth: accessible-resources returned no usable site entries")
            raise OAuthCallbackError(_NO_SITE_MESSAGE)

        hint = _PRODUCT_SCOPE_HINT.get(provider, "")
        if not hint:
            # No product-scope hint configured for this provider: we cannot confirm a site grants
            # the intended product, so fail closed rather than binding to an unverified site.
            logger.warning(f"Atlassian OAuth: no product-scope hint configured for provider '{provider}'")
            raise OAuthCallbackError(_NO_SITE_MESSAGE)
        # Require positive scope evidence for every candidate. A site without a matching `scopes`
        # entry is never selected — missing scope info fails closed instead of falling back to all
        # sites, so a token is never bound to a site whose product we could not verify.
        candidates = [s for s in sites if any(hint in str(scope).lower() for scope in (s.get("scopes") or []))]
        if not candidates:
            logger.warning(f"Atlassian OAuth: no accessible site grants the {provider} product")
            raise OAuthCallbackError(_NO_SITE_MESSAGE)

        if len(candidates) > 1:
            logger.warning(
                f"Atlassian OAuth: {len(candidates)} accessible {provider} sites; refusing to bind arbitrarily"
            )
            raise OAuthCallbackError(_MULTIPLE_SITES_MESSAGE)
        return candidates[0]

    @classmethod
    def _fetch_cloud(cls, access_token: str, provider: str) -> tuple[str, str, str]:
        headers = _bearer_json_headers(access_token)
        try:
            with httpx.Client(timeout=10) as client:
                response = client.get(ACCESSIBLE_RESOURCES_URL, headers=headers)
                response.raise_for_status()
                resources = response.json() or []
        except Exception as exc:
            logger.warning(f"Atlassian OAuth: failed to fetch accessible-resources: {exc}")
            raise OAuthCallbackError(_NO_SITE_MESSAGE)
        site = cls._select_site(resources, provider)
        cloud_id = str(site.get("id", "") or "")
        if not cloud_id:
            logger.warning("Atlassian OAuth: accessible-resources entry has no cloud id")
            raise OAuthCallbackError(_NO_SITE_MESSAGE)
        return cloud_id, str(site.get("url") or ""), str(site.get("name") or "")

    @staticmethod
    def _fetch_user_info(access_token: str) -> tuple[str, str, str]:
        headers = _bearer_json_headers(access_token)
        try:
            with httpx.Client(timeout=10) as client:
                response = client.get(ME_URL, headers=headers)
                response.raise_for_status()
                data = response.json() or {}
            return (
                data.get("name", "") or data.get("nickname", "") or "",
                str(data.get("account_id", "") or ""),
                data.get("email", "") or "",
            )
        except Exception as exc:
            logger.warning(f"Atlassian OAuth: failed to fetch user info: {exc}")
            return "", "", ""

    def finalize_callback_payload(self, context: CallbackContext) -> tuple[dict[str, Any], dict[str, Any]]:
        token_payload = context.token_payload
        cloud_id, site_url, site_name = self._fetch_cloud(token_payload.access_token, context.provider)
        username, account_id, email = self._fetch_user_info(token_payload.access_token)
        token_data = {
            "access_token": token_payload.access_token,
            "refresh_token": token_payload.refresh_token,
            "expires_in": token_payload.expires_in,
            "scopes": token_payload.scopes,
            "cloud_id": cloud_id,
            "site_url": site_url,
            "site_name": site_name,
            "username": username,
            "account_id": account_id,
        }
        return token_data, {
            "username": username,
            "email": email,
            "cloud_id": cloud_id,
            "site_name": site_name,
            "provider": context.provider,
        }

    @staticmethod
    def _scope_for_provider(provider: str) -> str:
        if provider == _CONFLUENCE_STATE_PROVIDER:
            return config.CONFLUENCE_OAUTH_SCOPES
        return config.JIRA_OAUTH_SCOPES

    @classmethod
    def revoke_token(cls, *, user_id: str, integration_id: str) -> None:
        """Record the disconnect: Atlassian's 3LO API exposes no token revocation endpoint.

        Users revoke access from their Atlassian account settings, so dropping the vault record
        is all this service can do. The grant stays live at Atlassian until the user removes it
        there, which makes this worth logging when auditing an account's connections.
        """
        logger.info(
            f"{cls.provider_label} OAuth: no provider-side revocation available; dropping vault record only "
            f"for user={user_id} integration={integration_id}"
        )


class JiraOAuthProviderAdapter(_AtlassianOAuthProviderAdapterBase):
    provider_label = "Jira"
    default_provider = _JIRA_STATE_PROVIDER
    expected_state_providers = {_JIRA_STATE_PROVIDER, _CONFLUENCE_STATE_PROVIDER}
    missing_credentials_message = "Jira OAuth is not configured. Provide the OAuth Application (client) ID and Secret."

    @staticmethod
    def get_error_message(error: str, provider: str) -> str:
        if provider == _CONFLUENCE_STATE_PROVIDER:
            return CONFLUENCE_OAUTH_ERROR_DESCRIPTIONS.get(error, f"Authentication failed: {error}")
        return JIRA_OAUTH_ERROR_DESCRIPTIONS.get(error, f"Authentication failed: {error}")

    @staticmethod
    def persist_connected_token(*, token_data: dict, user_id: str, integration_id: str, provider: str) -> None:
        if provider == _CONFLUENCE_STATE_PROVIDER:
            from codemie.service.confluence_oauth.settings_service import ConfluenceOAuthSettingsService

            ConfluenceOAuthSettingsService().persist_user_token(
                token_data=token_data, user_id=user_id, setting_id=integration_id
            )
            return
        from codemie.service.jira_oauth.settings_service import JiraOAuthSettingsService

        JiraOAuthSettingsService().persist_user_token(token_data=token_data, user_id=user_id, setting_id=integration_id)


class ConfluenceOAuthProviderAdapter(_AtlassianOAuthProviderAdapterBase):
    provider_label = "Confluence"
    default_provider = _CONFLUENCE_STATE_PROVIDER
    expected_state_providers = {_CONFLUENCE_STATE_PROVIDER}
    missing_credentials_message = (
        "Confluence OAuth is not configured. Provide the OAuth Application (client) ID and Secret."
    )

    @staticmethod
    def get_error_message(error: str, provider: str) -> str:
        return CONFLUENCE_OAUTH_ERROR_DESCRIPTIONS.get(error, f"Authentication failed: {error}")

    @staticmethod
    def build_redirect_uri(callback_base_url: str) -> str:
        return build_confluence_redirect_uri(callback_base_url)

    @staticmethod
    def persist_connected_token(*, token_data: dict, user_id: str, integration_id: str, provider: str) -> None:
        from codemie.service.confluence_oauth.settings_service import ConfluenceOAuthSettingsService

        ConfluenceOAuthSettingsService().persist_user_token(
            token_data=token_data, user_id=user_id, setting_id=integration_id
        )
