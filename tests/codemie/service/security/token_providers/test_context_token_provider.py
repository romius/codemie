# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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
from codemie.service.security.principal_token_resolver import PrincipalType
from codemie.service.security.token_providers.context_token_provider import ContextTokenProvider
from codemie.service.security.token_providers.base_provider import TokenProviderException


@pytest.fixture
def provider():
    return ContextTokenProvider()


@patch("codemie.service.security.token_providers.context_token_provider.resolve_current_bearer_token")
@patch("codemie.service.security.token_providers.context_token_provider.get_current_user")
def test_get_token_success(mock_get_user, mock_resolve, provider):
    mock_resolve.return_value = ("test-token", PrincipalType.USER)
    mock_get_user.return_value.id = "user-123"

    token = provider.get_token()

    assert token == "test-token"
    mock_resolve.assert_called_once()


@patch("codemie.service.security.token_providers.context_token_provider.resolve_current_bearer_token")
@patch("codemie.service.security.token_providers.context_token_provider.get_current_user")
def test_get_token_client_principal(mock_get_user, mock_resolve, provider):
    mock_resolve.return_value = ("client-token", PrincipalType.CLIENT)
    mock_get_user.return_value.id = "user-123"

    token = provider.get_token()

    assert token == "client-token"
    mock_resolve.assert_called_once()


@patch("codemie.service.security.token_providers.context_token_provider.resolve_current_bearer_token")
@patch("codemie.service.security.token_providers.context_token_provider.get_current_user")
def test_get_token_none(mock_get_user, mock_resolve, provider):
    mock_resolve.return_value = (None, None)
    mock_get_user.return_value.id = "user-123"

    token = provider.get_token()

    assert token is None
    mock_resolve.assert_called_once()


@patch("codemie.service.security.token_providers.context_token_provider.resolve_current_bearer_token")
def test_get_token_exception(mock_resolve, provider):
    mock_resolve.side_effect = Exception("Unexpected error")

    with pytest.raises(TokenProviderException) as exc_info:
        provider.get_token()

    assert "Failed to retrieve token from context" in str(exc_info.value)


def test_get_provider_name(provider):
    assert provider.get_provider_name() == "ContextTokenProvider"
