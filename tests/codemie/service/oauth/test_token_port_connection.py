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

"""A connected member must not be asked to sign in again every access-token lifetime.

TMS `has_valid_token` reports False for a merely expired access token even when its refresh
token can renew it, so `has_connection` must resolve through `retrieve` (which refreshes)
instead. Otherwise the connect gate would fire hourly and disagree with `/connection`.

Tool OAuth talks to the enterprise TMS vault directly (via `tms_vault_ops`) with no local fallback
cache, so a transient vault outage propagates rather than being masked with a possibly-stale token.
The TMS handle is injected at MCP-auth init via `set_tms`; these tests inject a fake TMS the same way.
"""

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from codemie.service.oauth.token_port import ToolOAuthTokenPort
from tests.codemie.service.oauth.tms_stub import install_tms_stub


class _FakeAuditProvider:
    def context(self, *, source, correlation_id=None):
        return nullcontext()


@pytest.fixture
def tms_module(monkeypatch):
    module = install_tms_stub(monkeypatch)
    yield module
    ToolOAuthTokenPort.clear_tms()


def _use_tms(tms):
    ToolOAuthTokenPort.set_tms(tms, _FakeAuditProvider())


def test_expired_but_refreshable_token_still_counts_as_connected(tms_module):
    calls = []

    class _RefreshingTMS:
        """Stands in for TMS: the access token is expired, but retrieve() refreshes it."""

        def has_valid_token(self, user_id, integration_id):
            return False  # access token expired — must NOT drive the gate

        def retrieve(self, user_id, integration_id):
            calls.append((user_id, integration_id))
            return SimpleNamespace(access_token="refreshed-access-token")

    _use_tms(_RefreshingTMS())

    assert ToolOAuthTokenPort.has_connection(user_id="u-1", integration_id="s-1") is True
    assert calls == [("u-1", "s-1")]


def test_missing_credential_is_not_connected(tms_module):
    class _NotConnectedTMS:
        def retrieve(self, user_id, integration_id):
            raise tms_module.TokenNotFound("no credential")

    _use_tms(_NotConnectedTMS())

    assert ToolOAuthTokenPort.has_connection(user_id="u-1", integration_id="s-1") is False
    assert ToolOAuthTokenPort.get_oauth2_token_or_none(user_id="u-1", integration_id="s-1") is None


def test_reauthentication_required_is_not_connected(tms_module):
    class _ReauthTMS:
        def retrieve(self, user_id, integration_id):
            raise tms_module.ReAuthenticationRequired("refresh_invalid_grant")

    _use_tms(_ReauthTMS())

    assert ToolOAuthTokenPort.has_connection(user_id="u-1", integration_id="s-1") is False


def test_vault_failure_propagates(tms_module):
    """A vault outage must NOT mislabel a member as disconnected — it propagates so the API surfaces
    a service error instead of prompting an unnecessary re-sign-in."""

    class _BrokenTMS:
        def retrieve(self, user_id, integration_id):
            raise tms_module.TMSUnavailable("vault down")

    _use_tms(_BrokenTMS())

    with pytest.raises(tms_module.TMSUnavailable):
        ToolOAuthTokenPort.has_connection(user_id="u-1", integration_id="s-1")


def test_transient_vault_outage_propagates_even_after_a_successful_read(tms_module):
    """No local cache: a later outage must not be masked by a previously-read token (which could be
    stale or revoked) — it propagates so the API surfaces a service error."""
    token = SimpleNamespace(access_token="access-token")

    class _FlakyTMS:
        def __init__(self):
            self.calls = 0

        def retrieve(self, user_id, integration_id):
            self.calls += 1
            if self.calls == 1:
                return token
            raise tms_module.TMSUnavailable("transient blip")

    _use_tms(_FlakyTMS())

    assert ToolOAuthTokenPort.get_oauth2_token_or_none(user_id="u-1", integration_id="s-1") is token
    with pytest.raises(tms_module.TMSUnavailable):
        ToolOAuthTokenPort.get_oauth2_token_or_none(user_id="u-1", integration_id="s-1")


def test_vault_is_resolved_lazily_when_mcp_auth_init_did_not_inject_one(tms_module, monkeypatch):
    """Tool OAuth can run with MCP auth disabled, so no vault is injected at startup. The port must
    resolve one on first use from the process-wide vault instead of failing."""
    ToolOAuthTokenPort.clear_tms()  # simulate 'MCP auth init never ran'

    class _StandaloneTMS:
        def retrieve(self, user_id, integration_id):
            return SimpleNamespace(access_token="lazy-token")

    standalone = _StandaloneTMS()

    def _fake_build(cls):
        cls._tms = standalone
        cls._audit_ctx = _FakeAuditProvider()

    monkeypatch.setattr(ToolOAuthTokenPort, "_build_lazy_tms", classmethod(_fake_build))

    assert ToolOAuthTokenPort.has_connection(user_id="u-1", integration_id="s-1") is True
    assert ToolOAuthTokenPort._tms is standalone  # cached after first lazy resolve


def test_delete_removes_the_token_and_a_later_outage_propagates(tms_module):
    """After a disconnect, a subsequent vault outage must not resurrect the token — with no cache it
    simply propagates."""
    deleted = []

    class _DeletableTMS:
        def __init__(self):
            self.retrieves = 0

        def retrieve(self, user_id, integration_id):
            self.retrieves += 1
            if self.retrieves == 1:
                return SimpleNamespace(access_token="access-token")
            raise tms_module.TMSUnavailable("transient blip")

        def delete(self, user_id, integration_id):
            deleted.append((user_id, integration_id))

    _use_tms(_DeletableTMS())

    assert ToolOAuthTokenPort.has_connection(user_id="u-1", integration_id="s-1") is True
    ToolOAuthTokenPort.delete(user_id="u-1", integration_id="s-1")
    assert deleted == [("u-1", "s-1")]

    with pytest.raises(tms_module.TMSUnavailable):
        ToolOAuthTokenPort.get_oauth2_token_or_none(user_id="u-1", integration_id="s-1")
