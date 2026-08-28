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

"""OAuthFlowEngine.handle_callback over the enterprise PKCE store + creds snapshot."""

import pytest

from tests.codemie.service.oauth.tms_stub import install_tms_stub_for_import

install_tms_stub_for_import()

from codemie_enterprise.mcp_auth import PKCEStateData  # noqa: E402

from codemie.service.oauth.flow_engine import OAuthFlowEngine, OAuthTokenPayload  # noqa: E402
from codemie.service.oauth.state_signing import sign_tool_state  # noqa: E402
from codemie.service.oauth.stores import signing_key  # noqa: E402

REDIRECT = "http://localhost:8080/v1/atlassian-oauth/callback"


@pytest.fixture(autouse=True)
def _configure_secret(monkeypatch):
    from codemie.service.oauth import stores
    from codemie.service import oauth_security

    monkeypatch.setattr(stores.config, "MCP_AUTH_HMAC_SECRET", "unit-test-secret-at-least-32-bytes!!", raising=False)
    # initiate_flow calls assert_secure_token_storage; keep it deterministic under any test order.
    monkeypatch.setattr(oauth_security.config, "OAUTH_ALLOW_INSECURE_TOKEN_STORAGE", True, raising=False)


class _FakeAdapter:
    provider_label = "Fake"
    default_provider = "jira"
    expected_state_providers = {"jira"}
    missing_credentials_message = "missing"
    unsupported_provider_message = "unsupported"
    auth_failed_message = "auth failed"
    success_status = "success"
    pending_status = "pending"
    error_status = "error"
    not_found_status = "not_found"

    def __init__(self):
        self.persisted = []

    def get_error_message(self, error, provider):
        return f"err:{error}"

    def build_redirect_uri(self, callback_base_url):
        return REDIRECT

    def build_state_data(
        self, *, user_id, integration_id, provider, redirect_uri, client_id, client_secret, code_verifier, context
    ):
        return {"provider": provider, "instance_url": context.get("instance_url", "")}

    def build_authorize_url(self, *, state_data, state, code_challenge, provider):
        return f"https://auth/{provider}?state={state}"

    def exchange_code_for_tokens(self, **_):
        return OAuthTokenPayload(access_token="AT", refresh_token="RT", expires_in=3600, scopes="read")

    def finalize_callback_payload(self, context):
        return {"access_token": context.token_payload.access_token, "cloud_id": "cid"}, {"cloud_id": "cid"}

    def persist_connected_token(self, *, token_data, user_id, integration_id, provider):
        self.persisted.append((user_id, integration_id, provider))


class _FakePkce:
    def __init__(self):
        self._store = {}
        self.last_stored = None

    def store(self, state, data):
        self._store[state] = data
        self.last_stored = data

    def consume(self, state):
        return self._store.pop(state, None)

    def peek(self, state):
        return self._store.get(state)


class _FakeCreds:
    def __init__(self):
        self._store = {}
        self.last = None

    def store(self, state, creds):
        self._store[state] = creds
        self.last = creds
        return True

    def consume(self, state):
        return self._store.pop(state, None)


def _engine():
    adapter = _FakeAdapter()
    pkce, creds = _FakePkce(), _FakeCreds()
    engine = OAuthFlowEngine(adapter=adapter, pkce_store=pkce, creds_snapshot=creds)
    return engine, adapter, pkce, creds


def _seed(
    pkce,
    creds,
    *,
    sign_integration="s1",
    pkce_integration=None,
    sign_user="u1",
    pkce_user=None,
    provider="jira",
    persist_token=True,
):
    """Sign a state and populate the PKCE + creds stores as initiate_flow would."""
    state = sign_tool_state(
        provider=provider,
        integration_id=sign_integration,
        user_id=sign_user,
        redirect_uri=REDIRECT,
        signing_key=signing_key(),
    )
    pkce.store(
        state,
        PKCEStateData(
            code_verifier="verifier",
            user_id=pkce_user or sign_user,
            auth_config_id=pkce_integration or sign_integration,
            session_binding_hash=None,
            context={"provider": provider, "instance_url": "", "persist_token": "true" if persist_token else "false"},
        ),
    )
    creds.store(state, {"client_id": "cid", "client_secret": "sec"})
    return state


def test_happy_path_persists_and_succeeds():
    engine, adapter, pkce, creds = _engine()
    state = _seed(pkce, creds, sign_integration="s1")
    outcome = engine.handle_callback(code="c", state=state, error=None)
    assert outcome.success is True
    assert outcome.status_code == 200
    assert adapter.persisted == [("u1", "s1", "jira")]


def test_cr001_integration_id_mismatch_is_rejected_and_never_persists():
    engine, adapter, pkce, creds = _engine()
    # signed state binds s1; the stored PKCE record claims s2 (attacker-swapped setting)
    state = _seed(pkce, creds, sign_integration="s1", pkce_integration="s2")
    outcome = engine.handle_callback(code="c", state=state, error=None)
    assert outcome.success is False
    assert outcome.status_code == 400
    assert adapter.persisted == []


def test_user_id_mismatch_is_rejected():
    engine, adapter, pkce, creds = _engine()
    state = _seed(pkce, creds, sign_user="u1", pkce_user="attacker")
    outcome = engine.handle_callback(code="c", state=state, error=None)
    assert outcome.success is False
    assert outcome.status_code == 400
    assert adapter.persisted == []


def test_tampered_state_is_rejected():
    engine, adapter, pkce, creds = _engine()
    state = _seed(pkce, creds)
    body, sig = state.split(".")
    outcome = engine.handle_callback(code="c", state=f"{body}.{sig[:-2]}xy", error=None)
    assert outcome.success is False
    assert outcome.status_code == 400
    assert adapter.persisted == []


def test_missing_state_is_rejected():
    engine, *_ = _engine()
    outcome = engine.handle_callback(code="c", state=None, error=None)
    assert outcome.success is False
    assert outcome.status_code == 400


def test_provider_error_publishes_terminal_result_for_waiting_tab():
    engine, adapter, pkce, creds = _engine()
    state = _seed(pkce, creds)
    outcome = engine.handle_callback(code=None, state=state, error="access_denied")
    assert outcome.success is False
    assert outcome.status_code == 200
    assert outcome.message == "err:access_denied"
    assert adapter.persisted == []


def test_token_exchange_failure_publishes_terminal_result():
    engine, adapter, pkce, creds = _engine()

    def _boom(**_):
        raise ValueError("bad grant")

    adapter.exchange_code_for_tokens = _boom
    state = _seed(pkce, creds)
    outcome = engine.handle_callback(code="c", state=state, error=None)
    assert outcome.success is False
    assert outcome.message == "auth failed"


def test_finalize_failure_is_reported_and_no_token_persisted():
    from codemie.service.oauth.flow_engine import OAuthCallbackError

    engine, adapter, pkce, creds = _engine()

    def _boom(_context):
        raise OAuthCallbackError("no site is accessible")

    adapter.finalize_callback_payload = _boom
    state = _seed(pkce, creds)
    outcome = engine.handle_callback(code="c", state=state, error=None)
    assert outcome.success is False
    assert outcome.message == "no site is accessible"
    assert adapter.persisted == []


def test_credentials_are_not_stored_in_the_pkce_record():
    engine, adapter, pkce, creds = _engine()
    engine.initiate_flow(
        user_id="u1",
        persist_token=True,
        client_id="cid",
        client_secret="sec",
        integration_id="s1",
        provider="jira",
        context={"instance_url": ""},
    )
    stored = pkce.last_stored
    assert "client_secret" not in stored.context
    assert "client_secret" not in vars(stored)
    # persist_token rides in the (server-side) PKCE context so the callback can read it back.
    assert stored.context["persist_token"] == "true"
    assert creds.last == {"client_id": "cid", "client_secret": "sec"}


def test_persist_failure_publishes_error_not_success():
    """CR-004: a persist failure must not leave a 'success' result the browser could read."""
    engine, adapter, pkce, creds = _engine()

    def _boom(**_):
        raise RuntimeError("tms down")

    adapter.persist_connected_token = _boom
    state = _seed(pkce, creds, sign_integration="s1")
    outcome = engine.handle_callback(code="c", state=state, error=None)
    assert outcome.success is False
    assert adapter.persisted == []


def test_test_mode_validates_without_persisting():
    """persist_token=False runs the full exchange but discards the token (a "Test")."""
    engine, adapter, pkce, creds = _engine()
    state = _seed(pkce, creds, sign_integration="s1", persist_token=False)
    outcome = engine.handle_callback(code="c", state=state, error=None)
    assert outcome.success is True
    assert adapter.persisted == []  # nothing saved to TMS


def test_initiate_rejects_persist_without_integration():
    """persist_token=True with no integration to save under is the one invalid combination."""
    from codemie.core.exceptions import ExtendedHTTPException

    engine, *_ = _engine()
    with pytest.raises(ExtendedHTTPException) as exc:
        engine.initiate_flow(
            user_id="u1",
            persist_token=True,
            client_id="cid",
            client_secret="sec",
            integration_id=None,
            provider="jira",
            context={"instance_url": ""},
        )
    assert exc.value.code == 400


def test_initiate_test_mode_allows_no_integration():
    """A test from the add form has no Setting yet, so persist_token=False must not require one."""
    engine, adapter, pkce, creds = _engine()
    out = engine.initiate_flow(
        user_id="u1",
        persist_token=False,
        client_id="cid",
        client_secret="sec",
        integration_id=None,
        provider="jira",
        context={"instance_url": ""},
    )
    assert "auth_url" in out and "state" in out
    assert pkce.last_stored.context["persist_token"] == "false"


def test_callback_redis_error_on_consume_returns_503():
    """A Redis outage / decrypt failure while reading callback state is a sanitized 503, not a 500."""
    engine, adapter, pkce, creds = _engine()
    state = _seed(pkce, creds)

    def _boom(_state):
        raise RuntimeError("redis down")

    pkce.consume = _boom
    outcome = engine.handle_callback(code="c", state=state, error=None)
    assert outcome.success is False
    assert outcome.status_code == 503
    assert adapter.persisted == []
