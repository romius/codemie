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

"""persist_user_token stores GitLab user tokens via ToolOAuthTokenPort (TMS).

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

from codemie.service.gitlab_oauth.settings_service import GitLabOAuthSettingsService  # noqa: E402


def _setting():
    return SimpleNamespace(
        credential_values=[
            SimpleNamespace(key="client_id", value="cid"),
            SimpleNamespace(key="client_secret", value="enc-csecret"),
            SimpleNamespace(key="instance_url", value="https://gitlab.com"),
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
        "instance_url": "https://gitlab.com",
        "scopes": "api read_user",
        "username": "groot",
        "user_id": "42",
    }


def test_persist_user_token_saves_to_tms(enc, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "codemie.rest_api.models.settings.Settings.find_by_id",
        staticmethod(lambda sid: _setting()),
    )
    monkeypatch.setattr(
        "codemie.service.gitlab_oauth.settings_service.ToolOAuthTokenPort.save_oauth2_token",
        staticmethod(lambda **kwargs: calls.append(kwargs)),
    )

    svc = GitLabOAuthSettingsService(encryption_service=enc)
    svc.persist_user_token(token_data=_token_data(), user_id="u1", setting_id="s1")

    assert len(calls) == 1
    kwargs = calls[0]
    assert kwargs["integration_id"] == "s1"
    assert kwargs["user_id"] == "u1"
    assert kwargs["access_token"] == "A"
    assert kwargs["refresh_token"] == "R"
    assert kwargs["scopes"] == ["api", "read_user"]
    assert kwargs["scope"] == "api read_user"
    assert kwargs["provider_username"] == "groot"
    assert kwargs["provider_user_id"] == "42"
    assert kwargs["provider_metadata"]["instance_url"] == "https://gitlab.com"
    assert kwargs["refresh_metadata"].client_id == "cid"
    assert kwargs["refresh_metadata"].client_secret == "csecret"
    assert kwargs["refresh_metadata"].client_auth_method == "client_secret_post"
    assert kwargs["refresh_metadata"].token_endpoint.endswith("/oauth/token")


def test_persist_user_token_raises_when_access_token_missing(enc, monkeypatch):
    monkeypatch.setattr(
        "codemie.service.gitlab_oauth.settings_service.ToolOAuthTokenPort.save_oauth2_token",
        staticmethod(lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not be called"))),
    )
    svc = GitLabOAuthSettingsService(encryption_service=enc)
    with pytest.raises(OAuthTokenDataError):
        svc.persist_user_token(token_data={"access_token": ""}, user_id="u1", setting_id="s1")


def _token_data_without_refresh_token():
    data = _token_data()
    data["refresh_token"] = ""
    return data


def _stored_token(username="groot", instance_url="https://gitlab.com", refresh_token="OLD-R"):
    return SimpleNamespace(
        refresh_token=refresh_token,
        provider_username=username,
        provider_metadata={"instance_url": instance_url},
    )


def _persist_with_stored_token(enc, monkeypatch, stored):
    """Run a re-consent where GitLab omitted refresh_token, against `stored` in the vault."""
    calls = []
    monkeypatch.setattr(
        "codemie.rest_api.models.settings.Settings.find_by_id",
        staticmethod(lambda sid: _setting()),
    )
    monkeypatch.setattr(
        "codemie.service.gitlab_oauth.settings_service.ToolOAuthTokenPort.get_oauth2_token_or_none",
        staticmethod(lambda *, user_id, integration_id: stored),
    )
    monkeypatch.setattr(
        "codemie.service.gitlab_oauth.settings_service.ToolOAuthTokenPort.save_oauth2_token",
        staticmethod(lambda **kwargs: calls.append(kwargs)),
    )
    svc = GitLabOAuthSettingsService(encryption_service=enc)
    svc.persist_user_token(token_data=_token_data_without_refresh_token(), user_id="u1", setting_id="s1")
    return calls


def test_refresh_token_is_recovered_from_the_vault_on_re_consent(enc, monkeypatch):
    """Tokens live in the vault, not on the setting, so that is where recovery has to look."""
    calls = _persist_with_stored_token(enc, monkeypatch, _stored_token())

    assert len(calls) == 1
    assert calls[0]["refresh_token"] == "OLD-R"


@pytest.mark.parametrize(
    "stored",
    [
        _stored_token(username="other-account"),
        _stored_token(instance_url="https://gitlab.example.com"),
        _stored_token(refresh_token=""),
        None,
    ],
    ids=["different_account", "different_instance", "no_stored_refresh_token", "nothing_stored"],
)
def test_refresh_token_is_not_recovered_across_identities(enc, monkeypatch, stored):
    """Reusing another identity's refresh token would silently refresh the wrong session."""
    with pytest.raises(OAuthTokenDataError):
        _persist_with_stored_token(enc, monkeypatch, stored)


def test_vault_outage_during_recovery_does_not_mask_the_missing_refresh_token(enc, monkeypatch):
    monkeypatch.setattr(
        "codemie.rest_api.models.settings.Settings.find_by_id",
        staticmethod(lambda sid: _setting()),
    )

    def _boom(*, user_id, integration_id):
        raise RuntimeError("vault unavailable")

    monkeypatch.setattr(
        "codemie.service.gitlab_oauth.settings_service.ToolOAuthTokenPort.get_oauth2_token_or_none",
        staticmethod(_boom),
    )
    monkeypatch.setattr(
        "codemie.service.gitlab_oauth.settings_service.ToolOAuthTokenPort.save_oauth2_token",
        staticmethod(lambda **kwargs: pytest.fail("a token without refresh_token must not be stored")),
    )
    svc = GitLabOAuthSettingsService(encryption_service=enc)

    with pytest.raises(OAuthTokenDataError):
        svc.persist_user_token(token_data=_token_data_without_refresh_token(), user_id="u1", setting_id="s1")
