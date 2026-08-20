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

"""Central principal-aware bearer token resolution.

Outbound integrations (DSP/provider, MCP, token exchange) must not decide
manually between the user-scoped ``auth_token`` and the client-scoped
``client_access_token`` (BFF client-credentials). This module is the single
place that ranks the two and reports which principal the token represents.

Security:
    - Token values are never logged here or by callers of this module.
    - A client token is never promoted to a user principal.
"""

from __future__ import annotations

from enum import StrEnum

from codemie.rest_api.security.user import User
from codemie.rest_api.security.user_context import (
    get_current_auth_token,
    get_current_client_access_token,
)


class PrincipalType(StrEnum):
    """Type of principal represented by an outbound bearer token."""

    USER = "user"
    CLIENT = "client"


def resolve_current_bearer_token(user: User | None = None) -> tuple[str | None, PrincipalType | None]:
    """Resolve the bearer token to use for outbound calls and its principal type.

    Resolution order:
        1. User-scoped token — explicit ``user.auth_token``, then request context.
        2. Client-scoped token — explicit ``user.client_access_token``, then request context.

    Args:
        user: Optional explicit User whose tokens take precedence over context.

    Returns:
        ``(token, PrincipalType)`` or ``(None, None)`` when no credential is available.
    """
    user_token = (user.auth_token if user else None) or get_current_auth_token()
    if user_token:
        return user_token, PrincipalType.USER

    client_token = (user.client_access_token if user else None) or get_current_client_access_token()
    if client_token:
        return client_token, PrincipalType.CLIENT

    return None, None


def resolve_current_bearer_token_value(user: User | None = None) -> str | None:
    """Compatibility helper for callers that only need the token value."""
    token, _principal_type = resolve_current_bearer_token(user)
    return token
