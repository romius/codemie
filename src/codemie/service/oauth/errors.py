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

"""Domain exceptions for the tool OAuth settings / token-persistence layer.

These carry no HTTP status code. The settings services raise them to stay transport-agnostic;
translating a failure into an HTTP response (or an OAuth callback result) is the caller's job.
"""


class ToolOAuthSettingsError(Exception):
    """Base for tool OAuth token-persistence domain failures."""


class OAuthTokenDataError(ToolOAuthSettingsError):
    """The completed OAuth flow did not yield the tokens needed to persist a connection."""


class OAuthIntegrationNotFoundError(ToolOAuthSettingsError):
    """No Settings row exists for the target integration id."""


class OAuthIntegrationConfigError(ToolOAuthSettingsError):
    """The integration's stored app credentials are missing or cannot be decrypted."""


class OAuthTokenPersistenceError(ToolOAuthSettingsError):
    """Writing the user's tokens to the enterprise token vault failed."""
