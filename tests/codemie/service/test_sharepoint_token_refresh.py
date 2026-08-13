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

"""Unit tests for the platform-side SharePoint delegated token refresh."""

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from codemie.service import sharepoint_pkce_service
from codemie.service.sharepoint_pkce_service import get_valid_access_token

_EXPIRED = int(time.time()) - 60
_FRESH = int(time.time()) + 3600


class FakeRedis:
    """Enough Redis to exercise the refresh lock, with real cross-thread atomicity."""

    def __init__(self):
        self._values = {}
        self._guard = threading.Lock()

    def set(self, key, value, nx=False, ex=None):
        with self._guard:
            if nx and key in self._values:
                return None
            self._values[key] = value
            return True

    def get(self, key):
        with self._guard:
            return self._values.get(key)

    def eval(self, _script, _numkeys, key, value):
        """Compare-and-delete, matching _RELEASE_LOCK_SCRIPT."""
        with self._guard:
            if self._values.get(key) == value:
                del self._values[key]
                return 1
            return 0

    def held(self, setting_id="setting-1"):
        return f"codemie:sp_pkce:refresh_lock:{setting_id}" in self._values


def _setting(**overrides):
    """A stored SharePoint setting as `_build_config` sees it: decrypted key/value pairs."""
    values = {
        "url": "https://contoso.sharepoint.com",
        "auth_type": "oauth",
        "access_token": "old-token",
        "refresh_token": "old-refresh",
        "expires_at": str(_EXPIRED),
    }
    values.update(overrides)
    setting = MagicMock()
    setting.id = "setting-1"
    setting.normalize_values.return_value = values
    return setting


def _token_response(status_code=200, **body):
    response = MagicMock()
    response.status_code = status_code
    payload = {"access_token": "new-token", "refresh_token": "new-refresh", "expires_in": 3600}
    payload.update(body)
    response.json.return_value = payload
    return response


@pytest.fixture(autouse=True)
def _app_registration(monkeypatch):
    """Pin the platform app registration so tests do not depend on a developer's .env."""
    monkeypatch.setattr(sharepoint_pkce_service.config, "SHAREPOINT_OAUTH_CLIENT_ID", "platform-client-id")
    monkeypatch.setattr(sharepoint_pkce_service.config, "SHAREPOINT_OAUTH_TENANT_ID", "tenant-guid")


@pytest.fixture(autouse=True)
def redis(monkeypatch):
    """Keep the refresh lock off a real Redis without silently degrading to no lock."""
    client = FakeRedis()
    monkeypatch.setattr(sharepoint_pkce_service, "create_redis_client", lambda: client)
    return client


@pytest.fixture(autouse=True)
def _no_reload(monkeypatch):
    """Default: the re-read under the lock finds nothing newer than the caller's copy."""
    monkeypatch.setattr(sharepoint_pkce_service, "_reload_credentials", lambda _setting_id: None)


@pytest.fixture
def store():
    with patch("codemie.service.settings.settings.SettingsService.update_sharepoint_oauth_tokens") as mock:
        yield mock


class TestWhenRefreshHappens:
    @patch.object(sharepoint_pkce_service, "requests")
    def test_expired_token_is_refreshed(self, mock_requests, store):
        mock_requests.post.return_value = _token_response()

        assert get_valid_access_token(_setting()) == "new-token"

    @patch.object(sharepoint_pkce_service, "requests")
    def test_token_near_expiry_is_refreshed_before_it_dies(self, mock_requests, store):
        """Refreshing inside the buffer stops a run starting on a token that expires mid-flight."""
        mock_requests.post.return_value = _token_response()

        assert get_valid_access_token(_setting(expires_at=str(int(time.time()) + 60))) == "new-token"

    @patch.object(sharepoint_pkce_service, "requests")
    def test_missing_access_token_is_refreshed(self, mock_requests, store):
        mock_requests.post.return_value = _token_response()

        assert get_valid_access_token(_setting(access_token="", expires_at="0")) == "new-token"

    @patch.object(sharepoint_pkce_service, "requests")
    def test_valid_token_is_returned_untouched(self, mock_requests, store):
        assert get_valid_access_token(_setting(expires_at=str(_FRESH))) == "old-token"
        mock_requests.post.assert_not_called()
        store.assert_not_called()

    @patch.object(sharepoint_pkce_service, "requests")
    def test_unknown_expiry_is_left_to_graph(self, mock_requests, store):
        """With no expiry recorded the token is used until Graph rejects it."""
        assert get_valid_access_token(_setting(expires_at="")) == "old-token"
        mock_requests.post.assert_not_called()

    @patch.object(sharepoint_pkce_service, "requests")
    def test_app_auth_setting_is_ignored(self, mock_requests, store):
        """App auth mints its own token per call and must never reach the refresh path."""
        assert get_valid_access_token(_setting(auth_type="app")) is None
        mock_requests.post.assert_not_called()
        store.assert_not_called()

    @patch.object(sharepoint_pkce_service, "requests")
    def test_setting_without_a_refresh_token_is_not_refreshed(self, mock_requests, store):
        assert get_valid_access_token(_setting(refresh_token="")) == "old-token"
        mock_requests.post.assert_not_called()

    @pytest.mark.parametrize("auth_type", ["oauth", "oauth_codemie", "oauth_custom"])
    @patch.object(sharepoint_pkce_service, "requests")
    def test_every_delegated_auth_type_is_refreshed(self, mock_requests, store, auth_type):
        mock_requests.post.return_value = _token_response()

        assert get_valid_access_token(_setting(auth_type=auth_type)) == "new-token"


class TestRefreshRequest:
    @patch.object(sharepoint_pkce_service, "requests")
    def test_uses_the_platform_app_registration_and_sends_no_secret(self, mock_requests, store):
        """The delegated flow is a public client: a client secret must never be sent."""
        mock_requests.post.return_value = _token_response()

        get_valid_access_token(_setting())

        url = mock_requests.post.call_args.args[0]
        sent = mock_requests.post.call_args.kwargs["data"]
        assert url == "https://login.microsoftonline.com/tenant-guid/oauth2/v2.0/token"
        assert sent == {
            "client_id": "platform-client-id",
            "grant_type": "refresh_token",
            "refresh_token": "old-refresh",
        }
        assert "client_secret" not in sent

    @patch.object(sharepoint_pkce_service, "requests")
    def test_request_is_time_bounded(self, mock_requests, store):
        mock_requests.post.return_value = _token_response()

        get_valid_access_token(_setting())

        assert mock_requests.post.call_args.kwargs["timeout"] == 30

    @patch.object(sharepoint_pkce_service, "requests")
    def test_rotated_tokens_are_stored(self, mock_requests, store):
        mock_requests.post.return_value = _token_response()

        get_valid_access_token(_setting())

        stored = store.call_args
        assert stored.args == ("setting-1",)
        assert stored.kwargs["access_token"] == "new-token"
        assert stored.kwargs["refresh_token"] == "new-refresh"
        assert stored.kwargs["expires_at"] > time.time()

    @patch.object(sharepoint_pkce_service, "requests")
    def test_current_refresh_token_is_kept_when_entra_returns_none(self, mock_requests, store):
        mock_requests.post.return_value = _token_response(refresh_token=None)

        get_valid_access_token(_setting())

        assert store.call_args.kwargs["refresh_token"] == "old-refresh"


class TestFailuresAreNonFatal:
    """A failed refresh must return the stored token; the tool reports the rejection."""

    @patch.object(sharepoint_pkce_service, "requests")
    def test_rejected_refresh_keeps_the_stored_token(self, mock_requests, store):
        mock_requests.post.return_value = _token_response(status_code=400)

        assert get_valid_access_token(_setting()) == "old-token"
        store.assert_not_called()

    @patch.object(sharepoint_pkce_service, "requests")
    def test_network_error_does_not_propagate(self, mock_requests, store):
        mock_requests.post.side_effect = Exception("connection reset")

        assert get_valid_access_token(_setting()) == "old-token"
        store.assert_not_called()

    @patch.object(sharepoint_pkce_service, "requests")
    def test_non_json_body_does_not_propagate(self, mock_requests, store):
        response = _token_response()
        response.json.side_effect = ValueError("not json")
        mock_requests.post.return_value = response

        assert get_valid_access_token(_setting()) == "old-token"

    @patch.object(sharepoint_pkce_service, "requests")
    def test_body_without_access_token_does_not_propagate(self, mock_requests, store):
        mock_requests.post.return_value = _token_response(access_token=None)

        assert get_valid_access_token(_setting()) == "old-token"

    @patch.object(sharepoint_pkce_service, "requests")
    def test_malformed_expires_in_falls_back_to_an_hour(self, mock_requests, store):
        mock_requests.post.return_value = _token_response(expires_in="soon")

        assert get_valid_access_token(_setting()) == "new-token"
        assert store.call_args.kwargs["expires_at"] > time.time()

    @patch.object(sharepoint_pkce_service, "requests")
    def test_malformed_expires_at_is_treated_as_unknown(self, mock_requests, store):
        assert get_valid_access_token(_setting(expires_at="not-a-number")) == "old-token"
        mock_requests.post.assert_not_called()

    @patch.object(sharepoint_pkce_service, "requests")
    def test_failed_store_still_returns_a_usable_token(self, mock_requests, store):
        mock_requests.post.return_value = _token_response()
        store.side_effect = Exception("database down")

        assert get_valid_access_token(_setting()) == "new-token"


class TestRefreshIsSerialized:
    """Entra invalidates a refresh token when it is spent, so only one run may spend it."""

    @patch.object(sharepoint_pkce_service, "requests")
    def test_the_lock_is_released_after_a_refresh(self, mock_requests, store, redis):
        mock_requests.post.return_value = _token_response()

        get_valid_access_token(_setting())

        assert not redis.held()

    @patch.object(sharepoint_pkce_service, "requests")
    def test_the_lock_is_released_when_the_refresh_fails(self, mock_requests, store, redis):
        """A stuck lock would block every later run for the whole TTL."""
        mock_requests.post.side_effect = Exception("connection reset")

        get_valid_access_token(_setting())

        assert not redis.held()

    def test_release_leaves_a_lock_that_is_no_longer_ours(self, redis):
        """Once our TTL expires the key can belong to another worker; deleting it would
        hand that worker's refresh to a third."""
        key = "codemie:sp_pkce:refresh_lock:setting-1"

        with sharepoint_pkce_service._refresh_lock("setting-1") as locked:
            assert locked
            redis.set(key, b"another-workers-token")

        assert redis.get(key) == b"another-workers-token"

    @patch.object(sharepoint_pkce_service, "requests")
    def test_a_token_refreshed_while_we_waited_is_used_as_is(self, mock_requests, store, monkeypatch):
        monkeypatch.setattr(
            sharepoint_pkce_service,
            "_reload_credentials",
            lambda _id: {
                "access_token": "holder-token",
                "refresh_token": "holder-refresh",
                "expires_at": str(_FRESH),
            },
        )

        assert get_valid_access_token(_setting()) == "holder-token"
        mock_requests.post.assert_not_called()
        store.assert_not_called()

    @patch.object(sharepoint_pkce_service, "requests")
    def test_the_stored_refresh_token_wins_over_the_callers_stale_copy(self, mock_requests, store, monkeypatch):
        """The caller's setting was read before the lock; only the re-read is current."""
        monkeypatch.setattr(
            sharepoint_pkce_service,
            "_reload_credentials",
            lambda _id: {
                "access_token": "old-token",
                "refresh_token": "rotated-refresh",
                "expires_at": str(_EXPIRED),
            },
        )
        mock_requests.post.return_value = _token_response()

        get_valid_access_token(_setting())

        assert mock_requests.post.call_args.kwargs["data"]["refresh_token"] == "rotated-refresh"

    @patch.object(sharepoint_pkce_service, "requests")
    def test_a_redis_outage_does_not_block_the_run(self, mock_requests, store, monkeypatch):
        def _unavailable():
            raise Exception("redis down")

        monkeypatch.setattr(sharepoint_pkce_service, "create_redis_client", _unavailable)
        mock_requests.post.return_value = _token_response()

        assert get_valid_access_token(_setting()) == "new-token"

    @patch.object(sharepoint_pkce_service, "requests")
    def test_concurrent_refresh_exchanges_once_and_keeps_the_rotated_token(self, mock_requests, monkeypatch):
        """Two runs on one expired setting: the second must reuse what the first stored.

        Without the lock both spend "old-refresh", and the slower write buries the newer
        refresh token — the user is silently signed out at the next expiry.
        """
        stored = {"access_token": "old-token", "refresh_token": "old-refresh", "expires_at": str(_EXPIRED)}
        writes = []

        def _slow_exchange(*_args, **_kwargs):
            # Hold the lock long enough that the second thread has to queue behind it.
            time.sleep(0.05)
            return _token_response()

        def _write(_setting_id, access_token, refresh_token, expires_at):
            writes.append(refresh_token)
            stored.update(access_token=access_token, refresh_token=refresh_token, expires_at=str(expires_at))

        mock_requests.post.side_effect = _slow_exchange
        monkeypatch.setattr(sharepoint_pkce_service, "_reload_credentials", lambda _id: dict(stored))

        results = []
        with patch(
            "codemie.service.settings.settings.SettingsService.update_sharepoint_oauth_tokens",
            side_effect=_write,
        ):
            threads = [
                threading.Thread(target=lambda: results.append(get_valid_access_token(_setting()))) for _ in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

        assert mock_requests.post.call_count == 1
        assert writes == ["new-refresh"]
        assert results == ["new-token", "new-token"]
        assert stored["refresh_token"] == "new-refresh"
