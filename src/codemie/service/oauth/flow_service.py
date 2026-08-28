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

"""Shared tool OAuth flow service.

Every provider flow service is the same thin orchestration over :class:`OAuthFlowEngine`; the only
differences are the adapter, the Redis namespaces, and the default state ``provider``. This base
captures that shape so each provider module is reduced to a small declaration.
"""

from __future__ import annotations

from typing import Optional

from codemie.service.oauth.flow_engine import CallbackResult, OAuthFlowEngine
from codemie.service.oauth.stores import ToolCredsSnapshot, build_tool_pkce_store


class ToolOAuthFlowService:
    """Orchestrates a provider's OAuth through the shared :class:`OAuthFlowEngine`."""

    def __init__(
        self,
        *,
        adapter,
        pkce_namespace: str,
        creds_namespace: str,
        default_provider: str,
    ):
        self._default_provider = default_provider
        self.engine = OAuthFlowEngine(
            adapter=adapter,
            pkce_store=build_tool_pkce_store(pkce_namespace),
            creds_snapshot=ToolCredsSnapshot(creds_namespace),
        )

    def initiate_flow(
        self,
        user_id: str,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        callback_base_url: Optional[str] = None,
        integration_id: Optional[str] = None,
        provider: Optional[str] = None,
        persist_token: bool = False,
        **context,
    ) -> dict:
        """Start the flow. ``provider`` defaults to this service's provider; extra keyword arguments
        (e.g. GitLab's ``instance_url``) become the adapter's non-secret flow ``context``."""
        return self.engine.initiate_flow(
            user_id=user_id,
            persist_token=persist_token,
            client_id=client_id,
            client_secret=client_secret,
            callback_base_url=callback_base_url,
            integration_id=integration_id,
            provider=provider or self._default_provider,
            context=context or None,
        )

    def handle_callback(self, code: Optional[str], state: Optional[str], error: Optional[str]) -> CallbackResult:
        return self.engine.handle_callback(code=code, state=state, error=error)

    def revoke_connection(self, user_id: str, integration_id: str) -> None:
        """Revoke the caller's grant at the provider and drop the stored token."""
        self.engine.revoke_connection(user_id=user_id, integration_id=integration_id)
