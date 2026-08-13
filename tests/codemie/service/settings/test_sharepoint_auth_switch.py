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

"""Switching a SharePoint integration between delegated and app authentication.

Saving as app auth must not leave a usable sign-in behind: the tool decides which
identity to act as from `auth_type`, so a leftover delegated token would keep it acting
as the previously signed-in user instead of as the configured application.
"""

from types import SimpleNamespace

from codemie.rest_api.models.settings import CredentialValues
from codemie.service.settings.settings import SettingsService


def _request(**values):
    return SimpleNamespace(credential_values=[CredentialValues(key=key, value=value) for key, value in values.items()])


def _keys(credential_values):
    return {cred.key for cred in credential_values}


def test_switching_to_app_auth_drops_the_delegated_sign_in():
    request = _request(
        auth_type="app",
        url="https://contoso.sharepoint.com",
        tenant_id="tenant-id",
        client_id="client-id",
        client_secret="client-secret",
        access_token="stale-token",
        refresh_token="stale-refresh",
        expires_at="1700000000",
        username="someone@contoso.com",
    )

    result = SettingsService._drop_sharepoint_delegated_credentials(request)

    assert _keys(result) == {"auth_type", "url", "tenant_id", "client_id", "client_secret"}


def test_saving_a_delegated_integration_keeps_its_tokens():
    """Editing an alias must not sign the user out."""
    request = _request(
        auth_type="oauth",
        url="https://contoso.sharepoint.com",
        access_token="live-token",
        refresh_token="live-refresh",
    )

    result = SettingsService._drop_sharepoint_delegated_credentials(request)

    assert _keys(result) == {"auth_type", "url", "access_token", "refresh_token"}


def test_missing_auth_type_is_treated_as_app_auth():
    """Integrations saved before delegated auth existed carry no auth_type."""
    request = _request(url="https://contoso.sharepoint.com", tenant_id="tenant-id", access_token="stale")

    result = SettingsService._drop_sharepoint_delegated_credentials(request)

    assert _keys(result) == {"url", "tenant_id"}


def test_no_credential_values_is_handled():
    assert SettingsService._drop_sharepoint_delegated_credentials(SimpleNamespace(credential_values=None)) == []
