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

"""Tool OAuth must refuse to boot against a token vault that cannot hold provider identity.

Per-user tokens live in the enterprise vault, so an enterprise package older than the release
that added the provider identity fields would fail deep inside a member's sign-in. The startup
guard turns that into an actionable boot error instead.
"""

import sys

import pytest

import codemie.service.oauth_security as oauth_security
from codemie.service.oauth.token_port import ToolOAuthTokenPort
from tests.codemie.service.oauth.tms_stub import TMS_MODULE_PATH, build_tms_stub_module


def _enable_providers(monkeypatch, enabled: bool) -> None:
    for flag in ("GITLAB_OAUTH_ENABLED", "JIRA_OAUTH_ENABLED", "CONFLUENCE_OAUTH_ENABLED"):
        monkeypatch.setattr(oauth_security.config, flag, enabled, raising=False)


def _install_vault(monkeypatch, token_fields: dict) -> None:
    module = build_tms_stub_module()
    module.OAuth2TokenData.model_fields = token_fields
    monkeypatch.setitem(sys.modules, TMS_MODULE_PATH, module)


def _install_no_vault(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, TMS_MODULE_PATH, None)


def _enable_tms(monkeypatch, enabled: bool) -> None:
    from codemie.service.oauth import token_port

    monkeypatch.setattr(token_port.config, "MCP_AUTH_TMS_ENABLED", enabled, raising=False)
    monkeypatch.setattr(token_port.config, "MCP_AUTH_TMS_ALLOW_MOCK", False, raising=False)


_CURRENT_FIELDS = {
    "access_token": object(),
    "provider_username": object(),
    "provider_user_id": object(),
    "provider_metadata": object(),
}
_OUTDATED_FIELDS = {"access_token": object(), "refresh_token": object()}


def test_no_check_when_every_provider_is_disabled(monkeypatch):
    _enable_providers(monkeypatch, False)
    _install_vault(monkeypatch, _OUTDATED_FIELDS)

    oauth_security.assert_token_vault_available()  # must not raise


def test_outdated_enterprise_package_fails_fast(monkeypatch):
    _enable_providers(monkeypatch, True)
    _enable_tms(monkeypatch, True)
    _install_vault(monkeypatch, _OUTDATED_FIELDS)

    with pytest.raises(RuntimeError) as exc:
        oauth_security.assert_token_vault_available()

    message = str(exc.value)
    assert oauth_security.MIN_ENTERPRISE_VERSION in message
    assert "provider_metadata" in message


def test_missing_enterprise_package_fails_fast(monkeypatch):
    _enable_providers(monkeypatch, True)
    _enable_tms(monkeypatch, True)
    _install_no_vault(monkeypatch)

    with pytest.raises(RuntimeError) as exc:
        oauth_security.assert_token_vault_available()

    assert "codemie-enterprise" in str(exc.value)


def test_current_enterprise_package_passes(monkeypatch):
    _enable_providers(monkeypatch, True)
    _enable_tms(monkeypatch, True)
    _install_vault(monkeypatch, _CURRENT_FIELDS)

    oauth_security.assert_token_vault_available()  # must not raise


def test_disabled_tms_fails_fast(monkeypatch):
    """A compatible package is not enough — the vault it talks to has to be switched on."""
    _enable_providers(monkeypatch, True)
    _enable_tms(monkeypatch, False)
    _install_vault(monkeypatch, _CURRENT_FIELDS)

    with pytest.raises(RuntimeError) as exc:
        oauth_security.assert_token_vault_available()

    assert "MCP_AUTH_TMS_ENABLED" in str(exc.value)


def test_assert_configured_is_a_pure_configuration_check(monkeypatch):
    """The startup check must not try to reach the injected token store / TMS connection."""
    _enable_providers(monkeypatch, True)
    _enable_tms(monkeypatch, True)
    monkeypatch.setattr(
        ToolOAuthTokenPort,
        "_require_tms",
        classmethod(lambda cls: pytest.fail("startup check must not connect to TMS")),
        raising=True,
    )

    ToolOAuthTokenPort.assert_configured()  # must not raise


# --- EPMCDME-13527: tool OAuth state signing now gates on MCP_AUTH_HMAC_SECRET ---


def test_state_signing_guard_noop_when_providers_disabled(monkeypatch):
    _enable_providers(monkeypatch, False)
    monkeypatch.setattr(oauth_security.config, "MCP_AUTH_HMAC_SECRET", "", raising=False)
    oauth_security.assert_oauth_state_signing_secret_configured()  # must not raise


def test_state_signing_guard_requires_mcp_hmac_secret(monkeypatch):
    _enable_providers(monkeypatch, True)
    monkeypatch.setattr(oauth_security.config, "MCP_AUTH_HMAC_SECRET", "", raising=False)
    with pytest.raises(RuntimeError):
        oauth_security.assert_oauth_state_signing_secret_configured()


def test_state_signing_guard_rejects_short_secret(monkeypatch):
    _enable_providers(monkeypatch, True)
    monkeypatch.setattr(oauth_security.config, "MCP_AUTH_HMAC_SECRET", "too-short", raising=False)
    with pytest.raises(RuntimeError):
        oauth_security.assert_oauth_state_signing_secret_configured()


def test_state_signing_guard_passes_with_strong_secret(monkeypatch):
    _enable_providers(monkeypatch, True)
    monkeypatch.setattr(oauth_security.config, "MCP_AUTH_HMAC_SECRET", "x" * 32, raising=False)
    oauth_security.assert_oauth_state_signing_secret_configured()  # must not raise
