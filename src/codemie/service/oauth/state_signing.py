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

"""Sign and validate the tool OAuth ``state`` parameter using the enterprise signer.

The enterprise HMAC layer only proves integrity of an opaque payload; the tool-specific
semantics (provider allowlist, integration binding, freshness window) are validated here.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

_STATE_VERSION = 1


class ToolStateError(ValueError):
    """Raised when a tool OAuth state fails signature or semantic validation."""


@dataclass(frozen=True)
class ToolOAuthState:
    provider: str
    integration_id: str
    user_id: str
    redirect_uri: str
    nonce: str
    ts: int
    v: int = _STATE_VERSION


def sign_tool_state(
    *,
    provider: str,
    integration_id: str,
    user_id: str,
    redirect_uri: str,
    signing_key: bytes,
) -> str:
    """Return a signed, opaque ``state`` string binding the tool OAuth flow."""
    from codemie_enterprise.mcp_auth import encode_signed_state

    payload = {
        "v": _STATE_VERSION,
        "provider": provider,
        "integration_id": integration_id,
        "user_id": user_id,
        "redirect_uri": redirect_uri,
        "nonce": secrets.token_urlsafe(16),
        "ts": int(time.time()),
    }
    return encode_signed_state(payload, signing_key)


def verify_tool_state(
    state: str,
    *,
    signing_key: bytes,
    expected_providers: set[str],
    max_age_seconds: int = 600,
    clock_skew_seconds: int = 5,
) -> ToolOAuthState:
    """Verify the signature, then the tool semantics; raise ToolStateError on any failure."""
    from codemie_enterprise.mcp_auth import decode_and_verify_signed_state

    try:
        payload = decode_and_verify_signed_state(state, signing_key)
    except Exception as exc:  # signature / format failures
        raise ToolStateError("Invalid or expired authentication state.") from exc

    try:
        parsed = ToolOAuthState(
            v=int(payload.get("v", 0)),
            provider=str(payload["provider"]),
            integration_id=str(payload.get("integration_id", "") or ""),
            user_id=str(payload["user_id"]),
            redirect_uri=str(payload["redirect_uri"]),
            nonce=str(payload["nonce"]),
            ts=int(payload["ts"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolStateError("OAuth state payload is malformed.") from exc

    if parsed.v != _STATE_VERSION:
        raise ToolStateError("Unsupported OAuth state version.")
    if parsed.provider not in expected_providers:
        raise ToolStateError("OAuth state provider is not allowed for this callback.")
    if not parsed.user_id or not parsed.redirect_uri or not parsed.nonce:
        raise ToolStateError("OAuth state payload fields are invalid.")

    now = int(time.time())
    if parsed.ts <= 0 or now - parsed.ts > max_age_seconds:
        raise ToolStateError("OAuth state has expired.")
    if parsed.ts > now + clock_skew_seconds:
        raise ToolStateError("OAuth state timestamp is in the future.")
    return parsed
