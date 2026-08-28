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

"""Shared constants for the tool OAuth providers (GitLab, Jira, Confluence).

The OAuth error-description map was byte-identical across providers apart from the provider brand
woven into a few strings, so it lives here once and each provider derives its own map from the helper.
"""

from __future__ import annotations

from urllib.parse import urlparse

from codemie.configs import config


def normalize_callback_base_url(url: str) -> str:
    """Normalize a callback base URL: strip trailing slash, require an http(s) scheme + host.

    Raises ``ValueError`` on obviously malformed input so a broken/hostile value fails early
    instead of being woven into an OAuth redirect URI.
    """
    if not url:
        raise ValueError("Callback base URL is required.")
    trimmed = url.strip().rstrip("/")
    parsed = urlparse(trimmed)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Invalid callback base URL: {url!r}. Expected e.g. https://app.example.com.")
    if parsed.username or parsed.password:
        raise ValueError("Callback base URL must not contain embedded credentials (user:pass@host).")
    return trimmed


def allowed_callback_base_urls() -> set[str]:
    """Normalized set of callback base URLs the OAuth flow may redirect back to.

    The deployment's own ``CALLBACK_API_BASE_URL`` is always allowed; the comma-separated
    ``OAUTH_CALLBACK_ALLOWED_BASE_URLS`` env var adds more (e.g. alternate ingress hosts).
    """
    urls = {normalize_callback_base_url(config.CALLBACK_API_BASE_URL)}
    for raw in (config.OAUTH_CALLBACK_ALLOWED_BASE_URLS or "").split(","):
        raw = raw.strip()
        if raw:
            urls.add(normalize_callback_base_url(raw))
    return urls


def ensure_callback_base_url_allowed(url: str | None) -> str:
    """Resolve the effective callback base URL, rejecting anything outside the allowlist.

    Falls back to ``CALLBACK_API_BASE_URL`` when ``url`` is empty. A provided value must normalize
    and be present in :func:`allowed_callback_base_urls`, otherwise ``ValueError`` is raised.
    """
    if not url or not url.strip():
        return normalize_callback_base_url(config.CALLBACK_API_BASE_URL)
    normalized = normalize_callback_base_url(url)
    if normalized not in allowed_callback_base_urls():
        raise ValueError(f"Callback base URL {normalized!r} is not in the allowed list.")
    return normalized


def build_oauth_error_descriptions(brand: str) -> dict[str, str]:
    """OAuth 2.0 error-code → human message map, with the provider ``brand`` woven in.

    ``brand`` is the name shown to the member in redirect/availability messages ("GitLab" for
    GitLab, "Atlassian" for the shared Jira/Confluence 3LO app). All other messages are
    provider-independent.
    """
    return {
        "access_denied": (
            "Authorization was declined or required permissions were not granted. "
            "Please try again and grant access to all requested scopes."
        ),
        "invalid_grant": "The authorization code is invalid or expired.",
        "invalid_client": "The application is not configured correctly. Contact your administrator.",
        "redirect_uri_mismatch": (
            f"Redirect URI does not match the one registered on {brand}. Contact your administrator."
        ),
        "invalid_request": "The authorization request is malformed. Please try again.",
        "unauthorized_client": "The client is not authorized for this grant type.",
        "unsupported_response_type": "The response type is not supported.",
        "invalid_scope": "One or more requested scopes are invalid or not consented.",
        "server_error": f"{brand} is currently unable to process the request. Please try again shortly.",
        "temporarily_unavailable": f"{brand} is temporarily unavailable. Please try again shortly.",
    }
