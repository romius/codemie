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

"""Tool OAuth token storage facade.

``ToolOAuthTokenPort`` is the stateless facade the app calls to persist and read tool OAuth tokens.
It calls the shared low-level :mod:`~codemie.service.security.tms_vault_ops` directly against the
enterprise TMS handle — deliberately not through ``TMSTokenStore``, whose TTLCache fallback (meant
for token exchange) is inappropriate here: that cache is not invalidated on reconnect and could
serve a stale/revoked token during a TMS outage. Tool OAuth instead propagates TMS failures so the
API layer maps them to sanitized HTTP errors. The TMS handle + audit provider are injected at
MCP-auth initialization (like ``TokenExchangeService``); tool OAuth can also run with MCP auth
disabled, so they are resolved lazily on first use from the process-wide standalone vault.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from codemie.configs import config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.security import tms_vault_ops


class _ModuleAuditContextProvider:
    """Audit-context provider backed by the mcp_auth module-level ``tms_audit_context()``.

    Used when the vault is resolved lazily (MCP auth init did not inject one): it routes to the same
    process-wide audit context provider that ``get_token_management_system()`` ensures exists, so a
    lazily-resolved vault audits identically to one injected at init.
    """

    def context(self, *, source: str, correlation_id: str | None = None):
        from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies

        return mcp_auth_dependencies.tms_audit_context(source, correlation_id=correlation_id)


class ToolOAuthTokenPort:
    """Stateless facade over the enterprise TMS vault, wired at MCP-auth initialization."""

    _tms: ClassVar[Any] = None
    _audit_ctx: ClassVar[Any] = None
    _init_lock: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def set_tms(cls, tms: Any, audit_context_provider: Any) -> None:
        """Inject the TMS handle + audit provider at MCP-auth init (mirrors TokenExchangeService)."""
        cls._tms = tms
        cls._audit_ctx = audit_context_provider

    @classmethod
    def clear_tms(cls) -> None:
        cls._tms = None
        cls._audit_ctx = None

    @classmethod
    def _require_enterprise_tms_ready(cls) -> None:
        oauth_enabled = config.GITLAB_OAUTH_ENABLED or config.JIRA_OAUTH_ENABLED or config.CONFLUENCE_OAUTH_ENABLED
        if not oauth_enabled:
            return
        if not config.MCP_AUTH_TMS_ENABLED and not config.MCP_AUTH_TMS_ALLOW_MOCK:
            raise RuntimeError(
                "Tool OAuth requires enterprise TMS. Set MCP_AUTH_TMS_ENABLED=true "
                "(or MCP_AUTH_TMS_ALLOW_MOCK=true for local development)."
            )

    @classmethod
    def assert_configured(cls) -> None:
        """Verify TMS is switched on, without opening a connection to it.

        Safe to call during startup: it inspects configuration only, so a boot check can fail
        fast on a misconfigured deployment rather than at a user's first connect attempt.
        """
        cls._require_enterprise_tms_ready()

    @classmethod
    def _require_tms(cls) -> tuple[Any, Any]:
        if cls._tms is not None:
            return cls._tms, cls._audit_ctx
        # MCP auth init is optional; resolve the vault on first use under a lock so concurrent
        # first-time callers resolve exactly once.
        with cls._init_lock:
            if cls._tms is None:
                cls._build_lazy_tms()
            return cls._tms, cls._audit_ctx

    @classmethod
    def _build_lazy_tms(cls) -> None:
        from codemie.enterprise.mcp_auth import dependencies as mcp_auth_dependencies

        cls._require_enterprise_tms_ready()
        cls._tms = mcp_auth_dependencies.get_token_management_system()
        cls._audit_ctx = _ModuleAuditContextProvider()

    @classmethod
    def save_oauth2_token(
        cls,
        *,
        user_id: str,
        integration_id: str,
        access_token: str,
        refresh_token: str,
        expires_in_seconds: int,
        scope: str | None,
        scopes: list[str],
        refresh_metadata,
        provider_username: str = "",
        provider_user_id: str = "",
        provider_metadata: dict[str, str] | None = None,
    ) -> None:
        tms, audit_ctx = cls._require_tms()
        expires_in = expires_in_seconds if expires_in_seconds > 0 else 3600
        token_data = tms_vault_ops.build_oauth2_token_data(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="Bearer",
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
            scope=scope,
            scopes=scopes,
            flow_source="admin",
            refresh_metadata=refresh_metadata,
            provider_username=provider_username or None,
            provider_user_id=provider_user_id or None,
            provider_metadata=provider_metadata or {},
        )
        tms_vault_ops.store_token(
            tms,
            audit_ctx.context(source="tool_oauth_store", correlation_id=integration_id),
            user_id,
            integration_id,
            token_data,
        )

    @classmethod
    def get_oauth2_token_or_none(cls, *, user_id: str, integration_id: str):
        """Return the full tool OAuth token object, or ``None`` when the user has no usable credential.

        ``TokenNotFound`` / ``ReAuthenticationRequired`` (a definitive "no credential") return
        ``None``; every other TMS failure propagates so the caller surfaces a service error rather
        than mislabelling a connected member as disconnected. There is no local cache, so a transient
        outage is never masked with a possibly-stale token.
        """
        from codemie_enterprise.mcp_auth import ReAuthenticationRequired, TokenNotFound

        tms, audit_ctx = cls._require_tms()
        try:
            token_data = tms_vault_ops.retrieve_token(
                tms,
                audit_ctx.context(source="tool_oauth_retrieve", correlation_id=integration_id),
                user_id,
                integration_id,
            )
        except (TokenNotFound, ReAuthenticationRequired):
            return None
        if not hasattr(token_data, "access_token"):
            return None
        return token_data

    @classmethod
    def has_connection(cls, *, user_id: str, integration_id: str) -> bool:
        """Return whether the user holds a usable credential for this integration.

        Deliberately goes through ``retrieve`` rather than TMS ``has_valid_token``: the latter
        reports False for a merely expired access token even when a refresh token can renew it,
        which would prompt an already-connected member to sign in again every token lifetime.
        """
        token_data = cls.get_oauth2_token_or_none(user_id=user_id, integration_id=integration_id)
        return bool(token_data is not None and token_data.access_token)

    @classmethod
    def delete(cls, *, user_id: str, integration_id: str) -> None:
        tms, audit_ctx = cls._require_tms()
        tms_vault_ops.delete_token(
            tms,
            audit_ctx.context(source="tool_oauth_delete", correlation_id=integration_id),
            user_id,
            integration_id,
        )

    @classmethod
    def invalidate_by_integration(cls, integration_id: str) -> None:
        tms, audit_ctx = cls._require_tms()
        tms_vault_ops.invalidate_by_config(
            tms,
            audit_ctx.context(source="tool_oauth_admin_config_change", correlation_id=integration_id),
            integration_id,
        )


def map_tms_error_to_http(provider_label: str, exc: Exception) -> ExtendedHTTPException:
    """Map enterprise TMS failures to sanitized API errors."""
    try:
        from codemie_enterprise.mcp_auth import (
            TMSAuditError,
            TMSCryptoError,
            TMSPersistenceError,
            TMSUnavailable,
            TokenRefreshError,
        )
    except ImportError:
        # ImportError covers a fully-absent package (ModuleNotFoundError) and a partial/broken
        # codemie-enterprise install; either way the TMS exception types are unavailable.
        return ExtendedHTTPException(503, f"{provider_label} OAuth token service is unavailable.")

    if isinstance(exc, TMSUnavailable):
        return ExtendedHTTPException(503, f"{provider_label} OAuth token service is temporarily unavailable.")
    if isinstance(exc, (TMSPersistenceError, TMSAuditError, TMSCryptoError, TokenRefreshError)):
        return ExtendedHTTPException(502, f"{provider_label} OAuth token service failed to process credentials.")
    return ExtendedHTTPException(500, f"{provider_label} OAuth token service error.")
