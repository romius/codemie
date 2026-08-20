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

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NoReturn, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import status

from codemie.configs.logger import logger
from codemie.core.exceptions import ExtendedHTTPException, MCPAuthenticationRequiredException

# \x00-\x1f already covers CR (\x0d) and LF (\x0a); \x7f is DEL.
_LOG_UNSAFE_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _sanitize_log_value(value: str | None) -> str | None:
    """Neutralize CR/LF/control chars in client-supplied values before logging (CWE-117).

    The diagnostics endpoint is unauthenticated, so its string fields are
    attacker-controllable; sanitizing at the source keeps the log line safe
    regardless of the logger's own escaping or the runtime environment.
    """
    if value is None:
        return None
    return _LOG_UNSAFE_CHARS.sub(" ", value)


def _sanitize_log_field(value: object) -> str:
    """Stringify any log field and neutralize CR/LF/control chars (CWE-117).

    Values reaching the mcp_auth log lines come from user-named configs, client-supplied flow
    ids and remote authorization-server documents, so none of them are trusted.
    """
    return _LOG_UNSAFE_CHARS.sub(" ", str(value))


class CallbackPageError(Exception):
    def __init__(
        self,
        message: str,
        *,
        server_name: str | None = None,
        title: str = "Authentication could not be completed",
        error_code: str | None = None,
        auth_config_id: str | None = None,
        bridge_error_code: str | None = None,
        error_description: str | None = None,
        error_uri: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.server_name = server_name
        self.title = title
        self.error_code = error_code
        self.auth_config_id = auth_config_id
        self.bridge_error_code = bridge_error_code
        self.error_description = error_description
        self.error_uri = error_uri


class MCPAuthEnterpriseUnavailableError(Exception):
    """Raised when the optional enterprise MCP auth package is unavailable."""


@dataclass(frozen=True)
class MCPPostAuth401Result:
    retry_auth_headers: dict[str, str] | None = None
    auth_exception: MCPAuthenticationRequiredException | None = None


class _CleanupEnqueuer(Protocol):
    def enqueue_cleanup(self, user_id: str) -> None: ...


def _is_missing_required_value(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _raise_client_error(message: str, details: str, *, code: int = status.HTTP_400_BAD_REQUEST) -> NoReturn:
    logger.warning(f"MCP auth client error: {message} details={_sanitize_log_value(details)}")
    raise ExtendedHTTPException(code=code, message=message, details=details, help="Review the MCP auth configuration.")


def _candidate_string(candidate: Mapping[str, Any], field_name: str) -> str | None:
    value = candidate.get(field_name)
    return value if isinstance(value, str) and value.strip() else None


def _get_discovery_result_field(result: Any, field_name: str) -> Any:
    if isinstance(result, Mapping):
        return result.get(field_name)
    return getattr(result, field_name, None)


def _get_discovery_candidate_field(candidate: Mapping[str, Any] | Any, field_name: str) -> Any:
    if isinstance(candidate, Mapping):
        return candidate.get(field_name)
    return getattr(candidate, field_name, None)


def _as_hostname_from_error_context(error_context: Mapping[str, Any]) -> str | None:
    value = error_context.get("issuer") or error_context.get("selected_authorization_server")
    return urlsplit(value).hostname if isinstance(value, str) else None


def _execution_context_attr(execution_context: Any | None, attr_name: str) -> Any:
    return getattr(execution_context, attr_name, None) if execution_context is not None else None


def _is_discovered_auth_config_id(auth_config_id: str | None) -> bool:
    return isinstance(auth_config_id, str) and auth_config_id.startswith("discovered:")


def _build_discovered_initiate_url(discovered_flow_id: str) -> str:
    return f"/v1/mcp-auth/oauth2/initiate?{urlencode({'discovered_flow_id': discovered_flow_id})}"


def _build_recovery_initiate_url(recovery_flow_id: str) -> str:
    return f"/v1/mcp-auth/oauth2/initiate?{urlencode({'recovery_flow_id': recovery_flow_id})}"


def _build_discovered_config_error_payload(
    candidate: Mapping[str, Any],
    error_context: Mapping[str, Any],
    *,
    as_hostname: str | None = None,
) -> dict[str, Any]:
    mcp_config_name = _candidate_string(candidate, "mcp_config_name") or _candidate_string(candidate, "server_name")
    return {
        "mcp_config_id": _candidate_string(candidate, "mcp_config_id"),
        "mcp_config_name": mcp_config_name,
        "mcp_server_name": _candidate_string(candidate, "mcp_server_name") or mcp_config_name,
        "auth_type": "oauth2",
        "as_hostname": as_hostname or _as_hostname_from_error_context(error_context),
        "status": "config_error",
        "error_context": dict(error_context),
    }


def _log_state_prefix(state: str | None) -> str | None:
    """First 12 chars of an OAuth2 state — enough to join initiate<->callback, never reversible.

    Sanitized because the callback receives ``state`` as an unauthenticated query parameter and
    logs it before verification. The sanitizer substitutes one character per character, so the
    prefix still matches the one initiate emitted for the same flow.
    """
    return _sanitize_log_value(state[:12]) if isinstance(state, str) else None


_MAX_LOGGED_FIELD_CHARS = 256


def _log_field(value: str | None) -> str | None:
    """Sanitize and bound one authorize-request field.

    These values come off a URL a remote authorization server chose, so their length is not ours
    to trust — an unbounded one could pad a single log line arbitrarily.
    """
    sanitized = _sanitize_log_value(value)
    if sanitized is None:
        return None
    return sanitized[:_MAX_LOGGED_FIELD_CHARS] or None


def _describe_authorize_request(auth_url: str) -> dict[str, str | None]:
    """Loggable, non-secret view of an authorize URL, including the state prefix that ties an
    initiate log line to its callback. Never raises (AC8) — malformed input degrades to all-None
    fields instead, so observing a flow can never break it."""
    fields: dict[str, str | None] = {
        "as_host": None,
        "client_id": None,
        "redirect_uri": None,
        "resource": None,
        "scope": None,
        "state_prefix": None,
    }
    try:
        parts = urlsplit(auth_url)
        query = dict(parse_qsl(parts.query))
        # hostname, not netloc: netloc would carry any user:password@ prefix into the log line.
        host = parts.hostname
        fields["as_host"] = _log_field(f"{host}:{parts.port}" if host and parts.port else host)
        for key in ("client_id", "redirect_uri", "resource", "scope"):
            fields[key] = _log_field(query.get(key))
        # Truncated here so no caller can accidentally log a full state.
        # SAML carries the same correlation value under RelayState.
        fields["state_prefix"] = _log_state_prefix(query.get("state") or query.get("RelayState"))
    except Exception:  # noqa: BLE001 - the contract is "never raises", not "never raises ValueError"
        pass
    return fields
