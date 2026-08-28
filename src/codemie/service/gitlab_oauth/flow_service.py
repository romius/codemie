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

"""GitLab OAuth 2.0 flow service backed by the shared provider-agnostic flow engine."""

from codemie.service.oauth.flow_service import ToolOAuthFlowService
from codemie.service.oauth.provider_adapters import GitLabOAuthProviderAdapter


class GitLabOAuthFlowService(ToolOAuthFlowService):
    """Orchestrates GitLab OAuth through the shared OAuthFlowEngine.

    ``instance_url`` (gitlab.com or a self-hosted host) is passed through ``initiate_flow`` as flow
    context and consumed by the GitLab adapter.
    """

    def __init__(self):
        super().__init__(
            adapter=GitLabOAuthProviderAdapter(),
            pkce_namespace="tool_oauth_gitlab",
            creds_namespace="tool_oauth_gitlab",
            default_provider="gitlab",
        )
