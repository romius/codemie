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

"""GitLab OAuth (Authorization Code + PKCE) constants."""

from urllib.parse import urlparse

from codemie.configs import config
from codemie.service.oauth.constants import build_oauth_error_descriptions


AUTHORIZE_PATH = "/oauth/authorize"
TOKEN_PATH = "/oauth/token"
USER_PATH = "/api/v4/user"

# Backend path GitLab redirects to after consent. The full redirect URI is this path appended to
# the integration's callback base URL (or CALLBACK_API_BASE_URL as a fallback).
CALLBACK_PATH = "/v1/gitlab-oauth/callback"


def build_redirect_uri(callback_base_url: str) -> str:
    """Build the GitLab OAuth redirect URI from a callback base URL + the API root path."""
    from codemie.core.utils import get_api_root_path

    return f"{callback_base_url.rstrip('/')}{get_api_root_path()}{CALLBACK_PATH}"


def normalize_instance_url(url: str) -> str:
    """Normalise a GitLab instance URL: strip trailing slash, require scheme + host.

    Raises ValueError on obviously malformed input so we fail early instead of
    building broken OAuth URLs.
    """
    if not url:
        raise ValueError("GitLab instance URL is required.")
    trimmed = url.strip().rstrip("/")
    parsed = urlparse(trimmed)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Invalid GitLab instance URL: {url!r}. Expected e.g. https://gitlab.com.")
    if parsed.username or parsed.password:
        raise ValueError("GitLab instance URL must not contain embedded credentials (user:pass@host).")
    return trimmed


def allowed_instance_urls() -> set[str]:
    """Normalized set of GitLab instances the client_secret may be sent to.

    The default instance is always allowed; the comma-separated env var adds more.
    """
    urls = {normalize_instance_url(config.GITLAB_OAUTH_DEFAULT_INSTANCE_URL)}
    for raw in (config.GITLAB_OAUTH_ALLOWED_INSTANCE_URLS or "").split(","):
        raw = raw.strip()
        if raw:
            urls.add(normalize_instance_url(raw))
    return urls


def ensure_instance_allowed(url: str) -> str:
    """Normalize url; raise ValueError if it is not in the allowlist. Returns the normalized url."""
    normalized = normalize_instance_url(url)
    if normalized not in allowed_instance_urls():
        raise ValueError(f"GitLab instance {normalized!r} is not in the allowed list.")
    return normalized


def authorize_url(instance_url: str) -> str:
    return f"{normalize_instance_url(instance_url)}{AUTHORIZE_PATH}"


def token_url(instance_url: str) -> str:
    return f"{normalize_instance_url(instance_url)}{TOKEN_PATH}"


def user_url(instance_url: str) -> str:
    return f"{normalize_instance_url(instance_url)}{USER_PATH}"


GITLAB_OAUTH_ERROR_DESCRIPTIONS = build_oauth_error_descriptions("GitLab")
