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

"""Jira/Atlassian OAuth 2.0 flow service backed by the shared flow engine."""

from codemie.service.oauth.flow_engine import CallbackResult
from codemie.service.oauth.flow_service import ToolOAuthFlowService
from codemie.service.oauth.provider_adapters import JiraOAuthProviderAdapter

# Re-exported for callers that resolve the callback result type via this module.
__all__ = ["JiraOAuthFlowService", "CallbackResult"]


class JiraOAuthFlowService(ToolOAuthFlowService):
    """Orchestrates Jira OAuth through the shared OAuthFlowEngine and Atlassian adapter."""

    def __init__(self):
        super().__init__(
            adapter=JiraOAuthProviderAdapter(),
            pkce_namespace="tool_oauth_atlassian",
            creds_namespace="tool_oauth_atlassian",
            default_provider="jira",
        )
