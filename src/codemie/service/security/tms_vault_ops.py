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

"""Low-level mechanics shared by the two TMS-backed token stores.

``TMSTokenStore`` (token exchange, with a TTLCache fallback) and ``ToolOAuthTokenPort`` (tool OAuth,
which propagates failures so the API layer can map them to sanitized HTTP errors) deliberately keep
different error-handling and return contracts. What they genuinely share is the *plumbing*: deferring
the enterprise import to build an ``OAuth2TokenData``, and running a store/retrieve/delete inside a
caller-supplied audit context. That plumbing lives here so it is written once; each caller layers its
own error policy and fallback around these calls.

The enterprise import is deferred (``codemie_enterprise`` is an optional dependency), and the audit
context manager is passed in by the caller — this module never decides the audit source or how the
context is obtained, so both stores keep their existing audit semantics.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any


def build_oauth2_token_data(**fields: Any):
    """Construct an enterprise ``OAuth2TokenData`` from raw values.

    Only the fields the caller passes are set; everything else takes the enterprise model's own
    defaults, so callers with different field sets each get exactly the object they built before.
    """
    from codemie_enterprise.mcp_auth import OAuth2TokenData

    return OAuth2TokenData(**fields)


def store_token(tms: Any, audit_cm: AbstractContextManager, user_id: str, config_id: str, token_data: Any) -> None:
    """Persist ``token_data`` for ``(user_id, config_id)`` inside the audit context."""
    with audit_cm:
        tms.store(user_id, config_id, token_data)


def retrieve_token(tms: Any, audit_cm: AbstractContextManager, user_id: str, config_id: str) -> Any:
    """Return the stored token for ``(user_id, config_id)`` inside the audit context."""
    with audit_cm:
        return tms.retrieve(user_id, config_id)


def delete_token(tms: Any, audit_cm: AbstractContextManager, user_id: str, config_id: str) -> None:
    """Delete the token for ``(user_id, config_id)`` inside the audit context."""
    with audit_cm:
        tms.delete(user_id, config_id)


def delete_all_for_user(tms: Any, audit_cm: AbstractContextManager, user_id: str) -> None:
    """Delete every token held for ``user_id`` inside the audit context."""
    with audit_cm:
        tms.delete_all_for_user(user_id)


def invalidate_by_config(tms: Any, audit_cm: AbstractContextManager, config_id: str) -> None:
    """Invalidate every user's token under ``config_id`` inside the audit context."""
    with audit_cm:
        tms.invalidate_by_config(config_id)
