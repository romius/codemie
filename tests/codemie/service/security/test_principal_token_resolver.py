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

from codemie.rest_api.security.user import User
from codemie.rest_api.security.user_context import (
    clear_current_auth_token,
    clear_current_client_access_token,
    set_current_auth_token,
    set_current_client_access_token,
)
from codemie.service.security.principal_token_resolver import (
    PrincipalType,
    resolve_current_bearer_token,
    resolve_current_bearer_token_value,
)


@pytest.fixture(autouse=True)
def clean_context():
    clear_current_auth_token()
    clear_current_client_access_token()
    yield
    clear_current_auth_token()
    clear_current_client_access_token()


def make_user(auth_token=None, client_access_token=None) -> User:
    return User(
        id="user-1",
        username="tester",
        auth_token=auth_token,
        client_access_token=client_access_token,
    )


def test_no_tokens_returns_none():
    assert resolve_current_bearer_token() == (None, None)


def test_user_token_from_context():
    set_current_auth_token("user-token")

    assert resolve_current_bearer_token() == ("user-token", PrincipalType.USER)


def test_client_token_from_context():
    set_current_client_access_token("client-token")

    assert resolve_current_bearer_token() == ("client-token", PrincipalType.CLIENT)


def test_user_token_preferred_over_client_token():
    set_current_auth_token("user-token")
    set_current_client_access_token("client-token")

    assert resolve_current_bearer_token() == ("user-token", PrincipalType.USER)


def test_explicit_user_auth_token_takes_precedence_over_context():
    set_current_auth_token("context-user-token")
    user = make_user(auth_token="explicit-user-token")

    assert resolve_current_bearer_token(user) == ("explicit-user-token", PrincipalType.USER)


def test_explicit_user_client_token_takes_precedence_over_context():
    set_current_client_access_token("context-client-token")
    user = make_user(client_access_token="explicit-client-token")

    assert resolve_current_bearer_token(user) == ("explicit-client-token", PrincipalType.CLIENT)


def test_explicit_user_without_tokens_falls_back_to_context():
    set_current_auth_token("context-user-token")
    user = make_user()

    assert resolve_current_bearer_token(user) == ("context-user-token", PrincipalType.USER)


def test_explicit_user_auth_token_wins_over_own_client_token():
    user = make_user(auth_token="user-token", client_access_token="client-token")

    assert resolve_current_bearer_token(user) == ("user-token", PrincipalType.USER)


def test_client_token_never_promoted_to_user_principal():
    """A client token must always be reported as CLIENT principal."""
    user = make_user(client_access_token="client-token")

    token, principal_type = resolve_current_bearer_token(user)

    assert token == "client-token"
    assert principal_type is PrincipalType.CLIENT
    assert principal_type is not PrincipalType.USER


def test_value_helper_returns_token_only():
    set_current_client_access_token("client-token")

    assert resolve_current_bearer_token_value() == "client-token"


def test_value_helper_returns_none_without_tokens():
    assert resolve_current_bearer_token_value() is None


def test_principal_type_values():
    assert PrincipalType.USER.value == "user"
    assert PrincipalType.CLIENT.value == "client"
