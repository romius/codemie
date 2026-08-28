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

"""Provider-agnostic OAuth Authorization Code + PKCE flow engine."""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any, Optional, Protocol, Union

import httpx

from codemie.configs import logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.oauth.constants import ensure_callback_base_url_allowed
from codemie.service.oauth.state_signing import (
    ToolOAuthState,
    ToolStateError,
    sign_tool_state,
    verify_tool_state,
)
from codemie.service.oauth.stores import signing_key
from codemie.service.oauth_security import assert_secure_token_storage

# Sanitized message for backend-dependency failures (Redis / crypto / signing) during flow
# initiation. Kept generic so no infra detail leaks to the caller.
_FLOW_INIT_UNAVAILABLE_MESSAGE = "OAuth service is temporarily unavailable. Please try again."


@dataclass
class CallbackResult:
    success: bool
    message: str
    status_code: int
    # Provider account (username/email) that authenticated — surfaced to the opener so a "Test"
    # can report "authenticated as …" without a persisted connection to query.
    username: str = ""


@dataclass(frozen=True)
class OAuthTokenPayload:
    access_token: str
    refresh_token: str
    expires_in: int
    scopes: str


@dataclass(frozen=True)
class CallbackContext:
    """Everything an adapter may need to turn a successful token exchange into a connection.

    Passed as one object because adapters need different subsets: GitLab reads the instance URL
    from ``state_data``, while Atlassian resolves its site remotely and only needs ``provider``
    to tell Jira and Confluence apart.
    """

    token_payload: OAuthTokenPayload
    state_data: dict[str, Any]
    provider: str


class OAuthCallbackError(Exception):
    """An adapter could not build a usable connection from an otherwise successful token exchange.

    The message is shown to the user, so it must stay free of provider internals and secrets.
    """


INVALID_STATE_MESSAGE = "Invalid or expired authentication state."

_ExchangeOutcome = Union[CallbackResult, tuple[dict[str, Any], dict[str, Any]]]


@dataclass(frozen=True)
class _ValidatedCallbackState:
    """The server-side state record after it has been cross-checked against the signed state."""

    user_id: str
    redirect_uri: str
    integration_id: str
    client_id: str
    client_secret: str
    code_verifier: str


class OAuthProviderAdapter(Protocol):
    """What the engine needs from a provider to drive one Authorization Code + PKCE flow.

    Some parameters are only meaningful to a subset of adapters: ``provider`` distinguishes Jira
    from Confluence behind one Atlassian app, while ``state_data`` carries the self-hosted GitLab
    instance URL. Implementations accept the full contract and use the parts that apply to them.
    """

    provider_label: str
    default_provider: str
    expected_state_providers: set[str]
    missing_credentials_message: str
    unsupported_provider_message: str
    auth_failed_message: str

    def get_error_message(self, error: str, provider: str) -> str: ...

    def build_redirect_uri(self, callback_base_url: str) -> str: ...

    def build_state_data(
        self,
        *,
        user_id: str,
        integration_id: str,
        provider: str,
        redirect_uri: str,
        client_id: str,
        client_secret: str,
        code_verifier: str,
        context: dict[str, Any],
    ) -> dict[str, Any]: ...

    def build_authorize_url(
        self,
        *,
        state_data: dict[str, Any],
        state: str,
        code_challenge: str,
        provider: str,
    ) -> str: ...

    def exchange_code_for_tokens(
        self,
        *,
        code: str,
        code_verifier: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        state_data: dict[str, Any],
    ) -> OAuthTokenPayload: ...

    def finalize_callback_payload(self, context: CallbackContext) -> tuple[dict[str, Any], dict[str, Any]]: ...

    def persist_connected_token(
        self, *, token_data: dict[str, Any], user_id: str, integration_id: str, provider: str
    ) -> None: ...

    def revoke_token(self, *, user_id: str, integration_id: str) -> None: ...


class OAuthFlowEngine:
    """Shared OAuth flow orchestration for provider adapters."""

    def __init__(self, *, adapter: OAuthProviderAdapter, pkce_store, creds_snapshot):
        self.adapter = adapter
        self.pkce_store = pkce_store
        self.creds_snapshot = creds_snapshot

    @staticmethod
    def _generate_code_verifier() -> str:
        return secrets.token_urlsafe(96)

    @staticmethod
    def _generate_code_challenge(verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    def initiate_flow(
        self,
        *,
        user_id: str,
        persist_token: bool,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        callback_base_url: Optional[str] = None,
        integration_id: Optional[str] = None,
        provider: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        effective_client_id = client_id or ""
        effective_client_secret = client_secret or ""
        if not effective_client_id or not effective_client_secret:
            # Missing/empty app credentials are a client/config error, not a backend outage → 400.
            raise ExtendedHTTPException(400, self.adapter.missing_credentials_message)

        # A connect must save the token under a concrete integration; a test never persists, so it
        # may run without one. Inferring the mode from an empty integration_id is exactly the
        # ambiguity this flag removes, so reject the one nonsensical combination explicitly.
        if persist_token and not integration_id:
            raise ExtendedHTTPException(400, "An integration is required to save this connection.")

        assert_secure_token_storage(self.adapter.provider_label)

        effective_provider = provider or self.adapter.default_provider
        if effective_provider not in self.adapter.expected_state_providers:
            raise ExtendedHTTPException(400, self.adapter.unsupported_provider_message)

        try:
            effective_callback_base_url = ensure_callback_base_url_allowed(callback_base_url)
        except ValueError as exc:
            raise ExtendedHTTPException(400, f"Invalid OAuth callback configuration: {exc}")
        redirect_uri = self.adapter.build_redirect_uri(effective_callback_base_url)
        code_verifier = self._generate_code_verifier()
        code_challenge = self._generate_code_challenge(code_verifier)
        integration = integration_id or ""
        try:
            state = sign_tool_state(
                provider=effective_provider,
                integration_id=integration,
                user_id=user_id,
                redirect_uri=redirect_uri,
                signing_key=signing_key(),
            )
        except Exception as exc:
            # signing_key() derives from the Redis-backed encryption service; a crypto/backend
            # failure here must not surface as an unhandled 500.
            logger.error(f"Tool OAuth: failed to derive signing key / sign state: {exc}")
            raise ExtendedHTTPException(503, _FLOW_INIT_UNAVAILABLE_MESSAGE)
        try:
            flow_context = self.adapter.build_state_data(
                user_id=user_id,
                integration_id=integration,
                provider=effective_provider,
                redirect_uri=redirect_uri,
                client_id=effective_client_id,
                client_secret=effective_client_secret,
                code_verifier=code_verifier,
                context=context or {},
            )
        except ValueError as exc:
            raise ExtendedHTTPException(400, str(exc))

        flow_context.setdefault("provider", effective_provider)
        self._persist_flow_records(
            state=state,
            flow_context=flow_context,
            code_verifier=code_verifier,
            user_id=user_id,
            integration=integration,
            persist_token=persist_token,
            client_id=effective_client_id,
            client_secret=effective_client_secret,
        )

        auth_url = self.adapter.build_authorize_url(
            state_data={**flow_context, "client_id": effective_client_id, "redirect_uri": redirect_uri},
            state=state,
            code_challenge=code_challenge,
            provider=effective_provider,
        )
        return {"auth_url": auth_url, "state": state}

    def _persist_flow_records(
        self,
        *,
        state: str,
        flow_context: dict[str, Any],
        code_verifier: str,
        user_id: str,
        integration: str,
        persist_token: bool,
        client_id: str,
        client_secret: str,
    ) -> None:
        """Persist the in-flight PKCE record and the encrypted credential snapshot for this flow.

        Verifier + non-secret context go to the PKCE store; app credentials go to a separate
        encrypted snapshot so secrets never live inside the PKCE/state record. Either write failing
        raises a sanitized 503 so the caller never hands back an auth_url whose callback can't
        complete the exchange.
        """
        from codemie_enterprise.mcp_auth import PKCEStateData

        # persist_token rides in the server-side PKCE context so it survives the redirect round-trip
        # and cannot be tampered with by the client (unlike a callback query param). It is read back
        # in the callback to decide connect (save) vs test (discard).
        pkce_context = {key: str(value) for key, value in flow_context.items()}
        pkce_context["persist_token"] = "true" if persist_token else "false"
        try:
            self.pkce_store.store(
                state,
                PKCEStateData(
                    code_verifier=code_verifier,
                    user_id=user_id,
                    auth_config_id=integration,
                    session_binding_hash=None,
                    context=pkce_context,
                ),
            )
        except Exception as exc:
            # Redis write / encryption failure — without the PKCE record the callback cannot verify
            # the flow, so fail now with a sanitized 503 rather than an unhandled 500.
            logger.error(f"Tool OAuth: failed to persist PKCE state: {exc}")
            raise ExtendedHTTPException(503, _FLOW_INIT_UNAVAILABLE_MESSAGE)

        # ToolCredsSnapshot.store swallows its own errors and returns False; a discarded failure
        # would hand back an auth_url whose callback can't read the app creds, dead-ending the flow.
        if not self.creds_snapshot.store(state, {"client_id": client_id, "client_secret": client_secret}):
            logger.error("Tool OAuth: failed to persist credential snapshot during flow initiation")
            raise ExtendedHTTPException(503, _FLOW_INIT_UNAVAILABLE_MESSAGE)

    def _fail(self, message: str, http_status: int) -> CallbackResult:
        """Terminal failure. The callback page hands this result to the opener via postMessage."""
        return CallbackResult(False, message, http_status)

    def _validate_state_data(
        self, signed_state: ToolOAuthState, state_data: dict[str, Any]
    ) -> Optional[_ValidatedCallbackState]:
        """Cross-check the server-side state record against the signed state.

        The signed state proves the callback parameters were not tampered with; this check proves
        they still match the record created at connect time. Returns ``None`` when the two
        disagree or the record is missing the credentials needed to complete the exchange.
        """
        user_id = str(state_data.get("user_id") or "")
        redirect_uri = str(state_data.get("redirect_uri") or "")
        integration_id = str(state_data.get("integration_id") or "")
        provider = str(state_data.get("provider") or "")
        if (
            not user_id
            or user_id != signed_state.user_id
            or integration_id != signed_state.integration_id
            or (provider and provider != signed_state.provider)
        ):
            return None

        client_id = str(state_data.get("client_id") or "")
        client_secret = str(state_data.get("client_secret") or "")
        if not client_id or not client_secret or not redirect_uri:
            return None

        return _ValidatedCallbackState(
            user_id=user_id,
            redirect_uri=redirect_uri,
            integration_id=integration_id,
            client_id=client_id,
            client_secret=client_secret,
            code_verifier=str(state_data.get("code_verifier") or ""),
        )

    def _exchange_and_finalize(
        self,
        *,
        code: str,
        signed_state: ToolOAuthState,
        state_data: dict[str, Any],
        validated: _ValidatedCallbackState,
    ) -> _ExchangeOutcome:
        """Trade the authorization code for tokens and let the adapter shape the connection.

        Returns the adapter's ``(token_data, result_kwargs)`` on success, or a terminal
        ``CallbackResult`` the caller should return as-is.
        """
        try:
            token_payload = self.adapter.exchange_code_for_tokens(
                code=code,
                code_verifier=validated.code_verifier,
                client_id=validated.client_id,
                client_secret=validated.client_secret,
                redirect_uri=validated.redirect_uri,
                state_data=state_data,
            )
        except httpx.HTTPStatusError as exc:
            logger.error(
                f"{self.adapter.provider_label} OAuth: token exchange failed HTTP "
                f"{exc.response.status_code} ({type(exc).__name__})"
            )
            return self._fail(self.adapter.auth_failed_message, 200)
        except ValueError as exc:
            logger.warning(f"{self.adapter.provider_label} OAuth: token exchange rejected: {exc}")
            return self._fail(self.adapter.auth_failed_message, 200)
        except Exception as exc:
            logger.error(f"{self.adapter.provider_label} OAuth: token exchange failed: {exc}")
            return self._fail(self.adapter.auth_failed_message, 200)

        try:
            return self.adapter.finalize_callback_payload(
                CallbackContext(
                    token_payload=token_payload,
                    state_data=state_data,
                    provider=signed_state.provider,
                )
            )
        except OAuthCallbackError as exc:
            logger.error(f"{self.adapter.provider_label} OAuth: could not finalize callback: {exc}")
            return self._fail(str(exc), 200)

    def _reassemble_state_data(self, signed_state: ToolOAuthState, pkce, creds: Optional[dict]) -> dict[str, Any]:
        """Rebuild the callback state_data the adapters expect from the PKCE record + creds snapshot.

        Cross-checked identity fields keep the PKCE record's *stored* values (auth_config_id,
        user_id) so ``_validate_state_data`` can still detect a record that does not match the
        signed state. redirect_uri lives only in the signed (HMAC-protected) state.
        """
        return {
            **(pkce.context or {}),
            "user_id": pkce.user_id,
            "integration_id": pkce.auth_config_id,
            "redirect_uri": signed_state.redirect_uri,
            "code_verifier": pkce.code_verifier,
            "client_id": (creds or {}).get("client_id", ""),
            "client_secret": (creds or {}).get("client_secret", ""),
        }

    def handle_callback(self, code: Optional[str], state: Optional[str], error: Optional[str]) -> CallbackResult:
        if not state:
            return CallbackResult(False, "Missing state parameter.", 400)

        try:
            signed_state = verify_tool_state(
                state,
                signing_key=signing_key(),
                expected_providers=self.adapter.expected_state_providers,
            )
        except ToolStateError:
            return CallbackResult(False, INVALID_STATE_MESSAGE, 400)

        # Reading the PKCE/creds records touches Redis and decrypts; surface an outage or a
        # decryption failure as a sanitized 503 rather than an unhandled 500.
        try:
            pkce = self.pkce_store.consume(state)
            creds = self.creds_snapshot.consume(state)
        except Exception as exc:
            logger.error(f"{self.adapter.provider_label} OAuth: failed to read callback state: {exc}")
            return CallbackResult(False, "Authentication service temporarily unavailable. Please try again.", 503)
        state_data = self._reassemble_state_data(signed_state, pkce, creds) if pkce is not None else None

        if error:
            message = self.adapter.get_error_message(error, signed_state.provider)
            return self._fail(message, 200)
        if state_data is None or creds is None:
            return CallbackResult(False, INVALID_STATE_MESSAGE, 400)
        if not code:
            return self._fail("Missing authorization code.", 400)

        validated = self._validate_state_data(signed_state, state_data)
        if validated is None:
            return CallbackResult(False, INVALID_STATE_MESSAGE, 400)

        outcome = self._exchange_and_finalize(
            code=code,
            signed_state=signed_state,
            state_data=state_data,
            validated=validated,
        )
        if isinstance(outcome, CallbackResult):
            return outcome
        token_data, public_metadata = outcome
        username = str(public_metadata.get("username") or public_metadata.get("email") or "")

        persist_token = str(state_data.get("persist_token", "")).lower() == "true"
        return self._publish_success(
            signed_state=signed_state,
            validated=validated,
            token_data=token_data,
            persist_token=persist_token,
            username=username,
        )

    def _publish_success(
        self,
        *,
        signed_state: ToolOAuthState,
        validated: _ValidatedCallbackState,
        token_data: dict[str, Any],
        persist_token: bool,
        username: str = "",
    ) -> CallbackResult:
        """Attach the token to the integration and report success to the callback page.

        In test mode (``persist_token`` false) the exchange is validated but the token is never
        saved. The token is handed to the adapter directly and written to enterprise TMS; it never
        passes through any intermediate store.
        """
        if persist_token:
            try:
                self.adapter.persist_connected_token(
                    token_data=token_data,
                    user_id=validated.user_id,
                    integration_id=validated.integration_id,
                    provider=signed_state.provider,
                )
            except Exception as exc:
                logger.error(
                    f"{self.adapter.provider_label} OAuth: failed to persist user token for "
                    f"provider={signed_state.provider} integration={validated.integration_id}: {exc}"
                )
                return CallbackResult(False, "Failed to connect this integration. Please try again.", 200)

        return CallbackResult(True, "Authentication successful.", 200, username=username)

    def revoke_connection(self, *, user_id: str, integration_id: str) -> None:
        """Revoke the user's grant at the provider, then drop the token from the vault.

        Provider revocation is best effort: dropping the local record is what the user asked
        for, so a provider-side failure is logged rather than left blocking the disconnect.
        """
        from codemie.service.oauth.token_port import ToolOAuthTokenPort

        try:
            self.adapter.revoke_token(user_id=user_id, integration_id=integration_id)
        except Exception as exc:
            logger.warning(
                f"{self.adapter.provider_label} OAuth: provider revocation failed for "
                f"integration={integration_id}: {exc}"
            )
        ToolOAuthTokenPort.delete(user_id=user_id, integration_id=integration_id)
