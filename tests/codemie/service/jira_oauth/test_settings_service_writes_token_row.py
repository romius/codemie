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

"""persist_user_token stores Jira user tokens via ToolOAuthTokenPort (TMS).

The token_data dict is the adapter's finalized callback payload, handed to persist_user_token
directly — it never passes through the result store.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from codemie.service.oauth.errors import OAuthTokenDataError
from tests.codemie.service.oauth.tms_stub import install_tms_stub_for_import

# The service binds vault symbols at import time, so the stub has to be in place first.
install_tms_stub_for_import()

from codemie.service.jira_oauth.settings_service import JiraOAuthSettingsService  # noqa: E402


def _setting():
    return SimpleNamespace(
        credential_values=[
            SimpleNamespace(key="client_id", value="cid"),
            SimpleNamespace(key="client_secret", value="enc-csecret"),
        ]
    )


@pytest.fixture
def enc():
    e = MagicMock()
    e.decrypt.side_effect = lambda v: v.replace("enc-", "")
    return e


def _token_data():
    return {
        "access_token": "A",
        "refresh_token": "R",
        "expires_in": 3600,
        "scopes": "read:jira-work offline_access",
        "cloud_id": "CID-1",
        "site_url": "https://site.atlassian.net",
        "site_name": "Site",
        "username": "groot",
        "account_id": "acc-1",
    }


def test_persist_user_token_stores_tokens_and_cloud_metadata(enc, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "codemie.rest_api.models.settings.Settings.find_by_id",
        staticmethod(lambda sid: _setting()),
    )
    monkeypatch.setattr(
        "codemie.service.oauth.settings_base.ToolOAuthTokenPort.save_oauth2_token",
        staticmethod(lambda **kwargs: calls.append(kwargs)),
    )

    svc = JiraOAuthSettingsService(encryption_service=enc)
    svc.persist_user_token(token_data=_token_data(), user_id="u1", setting_id="s1")

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["integration_id"] == "s1"
    assert kwargs["user_id"] == "u1"
    assert kwargs["access_token"] == "A"
    assert kwargs["refresh_token"] == "R"
    assert kwargs["scope"] == "read:jira-work offline_access"
    assert kwargs["scopes"] == ["read:jira-work", "offline_access"]
    assert kwargs["provider_username"] == "groot"
    assert kwargs["provider_user_id"] == "acc-1"
    assert kwargs["provider_metadata"]["cloud_id"] == "CID-1"
    assert kwargs["provider_metadata"]["site_url"] == "https://site.atlassian.net"
    assert kwargs["refresh_metadata"].token_endpoint.endswith("/oauth/token")
    assert kwargs["refresh_metadata"].client_id == "cid"
    assert kwargs["refresh_metadata"].client_secret == "csecret"


def test_persist_raises_when_tokens_missing(enc, monkeypatch):
    monkeypatch.setattr(
        "codemie.rest_api.models.settings.Settings.find_by_id",
        staticmethod(lambda sid: _setting()),
    )
    monkeypatch.setattr(
        "codemie.service.oauth.settings_base.ToolOAuthTokenPort.save_oauth2_token",
        staticmethod(lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not be called"))),
    )
    svc = JiraOAuthSettingsService(encryption_service=enc)
    with pytest.raises(OAuthTokenDataError):
        svc.persist_user_token(token_data={"access_token": "A"}, user_id="u1", setting_id="s1")
