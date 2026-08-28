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

"""Jira (Atlassian Cloud) OAuth 2.0 (Authorization Code + PKCE) constants.

Atlassian Cloud always authorizes at auth.atlassian.com; there is no per-instance host to
allowlist (unlike self-hosted GitLab). After authorization, Jira REST APIs are reached via
https://api.atlassian.com/ex/jira/{cloudId}, where cloudId comes from the accessible-resources
endpoint and is stored per user alongside the tokens.
"""

from codemie.service.oauth.constants import build_oauth_error_descriptions


AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"
ACCESSIBLE_RESOURCES_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
ME_URL = "https://api.atlassian.com/me"
# audience the authorization + token requests are scoped to.
API_AUDIENCE = "api.atlassian.com"

# Backend path Atlassian redirects to after consent. The full redirect URI is this path appended to
# the integration's callback base URL (or CALLBACK_API_BASE_URL as a fallback).
CALLBACK_PATH = "/v1/atlassian-oauth/callback"


def build_redirect_uri(callback_base_url: str) -> str:
    """Build the Jira OAuth redirect URI from a callback base URL + the API root path."""
    from codemie.core.utils import get_api_root_path

    return f"{callback_base_url.rstrip('/')}{get_api_root_path()}{CALLBACK_PATH}"


def jira_api_base_url(cloud_id: str) -> str:
    """Base URL for Jira Cloud REST calls made with an OAuth bearer token."""
    return f"https://api.atlassian.com/ex/jira/{cloud_id}"


JIRA_OAUTH_ERROR_DESCRIPTIONS = build_oauth_error_descriptions("Atlassian")
