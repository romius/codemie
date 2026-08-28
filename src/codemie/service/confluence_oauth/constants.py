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

"""Confluence (Atlassian Cloud) OAuth 2.0 (Authorization Code + PKCE) constants."""

from codemie.service.oauth.constants import build_oauth_error_descriptions


AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"
ACCESSIBLE_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
ME_URL = "https://api.atlassian.com/me"
API_AUDIENCE = "api.atlassian.com"

# Backend path Atlassian redirects to after consent. Shared with Jira: Atlassian 3LO is one
# platform app, so both products use the single /v1/atlassian-oauth/callback (only one Callback URL
# needs to be registered on the Atlassian app). The full redirect URI is this path appended to the
# integration's callback base URL (or CALLBACK_API_BASE_URL as a fallback).
CALLBACK_PATH = "/v1/atlassian-oauth/callback"


def build_redirect_uri(callback_base_url: str) -> str:
    """Build the Confluence OAuth redirect URI from a callback base URL + the API root path."""
    from codemie.core.utils import get_api_root_path

    return f"{callback_base_url.rstrip('/')}{get_api_root_path()}{CALLBACK_PATH}"


def confluence_api_base_url(cloud_id: str) -> str:
    """Base URL for Confluence Cloud REST calls made with an OAuth bearer token.

    The Atlassian OAuth 2.0 gateway serves the Confluence v1 REST API under a
    ``/wiki`` prefix (``.../ex/confluence/{cloudId}/wiki/rest/api/...``), unlike
    Jira which is served directly at ``.../ex/jira/{cloudId}/rest/api/...``.
    Omitting ``/wiki`` makes every content request resolve to a path the gateway
    rejects as unauthorized.
    """
    return f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki"


CONFLUENCE_OAUTH_ERROR_DESCRIPTIONS = build_oauth_error_descriptions("Atlassian")
