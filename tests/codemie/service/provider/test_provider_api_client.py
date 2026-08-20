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

import pytest
from unittest.mock import patch

from codemie.rest_api.models.provider import ProviderConfiguration
from codemie.rest_api.security.user import User
from codemie.rest_api.security.user_context import (
    clear_current_auth_token,
    clear_current_client_access_token,
    set_current_client_access_token,
)
from codemie.service.provider.provider_api_client import (
    PRINCIPAL_TYPE_HEADER,
    ProviderAPIClient,
)
from codemie.service.security.principal_token_resolver import PrincipalType


@pytest.fixture(autouse=True)
def clean_context():
    clear_current_auth_token()
    clear_current_client_access_token()
    yield
    clear_current_auth_token()
    clear_current_client_access_token()


@pytest.fixture(autouse=True)
def non_local_env():
    with patch("codemie.service.provider.provider_api_client.config") as mock_config:
        mock_config.is_local = False
        yield mock_config


def make_client(user: User) -> ProviderAPIClient:
    return ProviderAPIClient(
        user=user,
        url="https://provider.example.com/",
        provider_security_config=ProviderConfiguration(),
        log_prefix="[test]",
    )


def make_user(auth_token=None, client_access_token=None) -> User:
    return User(id="user-1", username="tester", auth_token=auth_token, client_access_token=client_access_token)


def test_user_token_yields_user_principal():
    client = make_client(make_user(auth_token="user-token"))

    credentials, principal_type = client._get_auth_credentials()

    assert credentials == {"access_token": "user-token"}
    assert principal_type is PrincipalType.USER


def test_client_token_yields_client_principal():
    client = make_client(make_user(client_access_token="client-token"))

    credentials, principal_type = client._get_auth_credentials()

    assert credentials == {"access_token": "client-token"}
    assert principal_type is PrincipalType.CLIENT


def test_client_token_from_context_when_user_has_none():
    set_current_client_access_token("context-client-token")
    client = make_client(make_user())

    credentials, principal_type = client._get_auth_credentials()

    assert credentials == {"access_token": "context-client-token"}
    assert principal_type is PrincipalType.CLIENT


def test_missing_token_raises_actionable_error():
    client = make_client(make_user())

    with pytest.raises(ValueError) as exc:
        client._get_auth_credentials()

    message = str(exc.value)
    assert "x-auth-request-access-token" in message
    assert "x-userinfo" in message
    assert "identity claims only" in message


def test_missing_token_error_contains_no_token_value():
    client = make_client(make_user())

    with pytest.raises(ValueError) as exc:
        client._get_auth_credentials()

    assert "Bearer " not in str(exc.value)


def test_local_mode_uses_mock_bearer(non_local_env):
    non_local_env.is_local = True
    client = make_client(make_user())

    credentials, principal_type = client._get_auth_credentials()

    assert credentials == {"access_token": ProviderAPIClient.LOCAL_MOCK_BEARER}
    assert principal_type is PrincipalType.USER


def test_unknown_auth_type_raises():
    client = make_client(make_user(auth_token="user-token"))
    client.provider_security_config = ProviderConfiguration.model_construct(auth_type="basic")

    with pytest.raises(ValueError, match="Unknown auth type"):
        client._get_auth_credentials()


def test_build_sets_user_principal_header():
    client = make_client(make_user(auth_token="user-token"))

    api = client.build()

    headers = api.api_client.default_headers
    assert headers[PRINCIPAL_TYPE_HEADER] == "user"


def test_build_sets_client_principal_header():
    client = make_client(make_user(client_access_token="client-token"))

    api = client.build()

    headers = api.api_client.default_headers
    assert headers[PRINCIPAL_TYPE_HEADER] == "client"


def test_no_token_value_in_logs(caplog):
    client = make_client(make_user(auth_token="super-secret-token"))

    with caplog.at_level("DEBUG"):
        client._get_auth_credentials()

    assert "super-secret-token" not in caplog.text
