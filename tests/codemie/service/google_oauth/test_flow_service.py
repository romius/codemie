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

"""Unit tests for GoogleOAuthFlowService."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from codemie.service.google_oauth.constants import GOOGLE_OAUTH_SCOPES
from codemie.service.google_oauth.flow_service import GoogleOAuthFlowService


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    return MagicMock()


@pytest.fixture
def mock_encryption():
    """Mock encryption service."""
    mock_enc = MagicMock()
    mock_enc.encrypt.side_effect = lambda x: f"encrypted_{x}".encode() if isinstance(x, str) else b"encrypted_" + x
    mock_enc.decrypt.side_effect = lambda x: (
        x.decode().replace("encrypted_", "") if isinstance(x, bytes) else str(x).replace("encrypted_", "")
    )
    return mock_enc


@pytest.fixture
def mock_state_store(mock_redis, mock_encryption):
    """Mock GoogleOAuthStateStore."""
    from codemie.service.google_oauth.state_store import GoogleOAuthStateStore

    return GoogleOAuthStateStore(redis_client=mock_redis, encryption_service=mock_encryption)


@pytest.fixture
def service(mock_state_store, mock_encryption):
    """GoogleOAuthFlowService instance with mocked dependencies."""
    return GoogleOAuthFlowService(state_store=mock_state_store, encryption_service=mock_encryption)


class TestScopeValidation:
    """Test scope validation logic."""

    def test_validate_granted_scopes_returns_none_when_all_scopes_granted(self, service):
        """Should return None when all required scopes are granted."""
        granted_scope = " ".join(GOOGLE_OAUTH_SCOPES)
        error = service._validate_granted_scopes(granted_scope)
        assert error is None

    def test_validate_granted_scopes_returns_error_when_scope_missing(self, service):
        """Should return error message when required scope is missing."""
        # Only grant email and openid, but not documents.readonly
        granted_scope = "openid https://www.googleapis.com/auth/userinfo.email"
        error = service._validate_granted_scopes(granted_scope)

        assert error is not None
        assert "all permissions" in error.lower()
        assert "google docs" in error.lower()

    def test_validate_granted_scopes_returns_none_when_scope_param_missing(self, service):
        """Should return None when scope parameter is not provided (legacy behavior)."""
        error = service._validate_granted_scopes(None)
        assert error is None

    def test_validate_granted_scopes_handles_extra_scopes(self, service):
        """Should succeed when user grants more scopes than required."""
        granted_scope = " ".join(GOOGLE_OAUTH_SCOPES + ["https://www.googleapis.com/auth/drive"])
        error = service._validate_granted_scopes(granted_scope)
        assert error is None


class TestHandleCallback:
    """Test OAuth callback handling."""

    def test_handle_callback_returns_error_when_state_missing(self, service):
        """Should return 400 error when state parameter missing."""
        result = service.handle_callback("code123", None, None)
        assert not result.success
        assert result.status_code == 400

    def test_handle_callback_handles_oauth_error_without_storing_result(self, service, mock_redis, mock_encryption):
        """Should return error without storing result when OAuth provider returns error."""
        state_data = {"code_verifier": "verifier", "client_id": "client", "user_id": "user123"}
        encrypted = mock_encryption.encrypt(json.dumps(state_data))
        mock_redis.getdel.return_value = encrypted

        result = service.handle_callback(None, "test_state", "access_denied")

        assert not result.success
        assert "permissions" in result.message.lower()
        assert result.status_code == 200
        # Should NOT store error result (only consume state)
        assert mock_redis.set.call_count == 0

    def test_handle_callback_returns_error_on_invalid_state(self, service, mock_redis):
        """Should return 400 error when state invalid/expired."""
        mock_redis.getdel.return_value = None

        result = service.handle_callback("code123", "invalid_state", None)

        assert not result.success
        assert result.status_code == 400
        assert "invalid" in result.message.lower() or "expired" in result.message.lower()

    def test_handle_callback_validates_scopes_before_token_exchange(self, service, mock_redis, mock_encryption):
        """Should validate scopes and reject if missing required scopes."""
        state_data = {"code_verifier": "verifier", "client_id": "client", "user_id": "user123"}
        encrypted_state = mock_encryption.encrypt(json.dumps(state_data))
        mock_redis.getdel.return_value = encrypted_state

        # Grant only partial scopes
        granted_scope = "openid https://www.googleapis.com/auth/userinfo.email"

        result = service.handle_callback("auth_code", "test_state", None, granted_scope)

        assert not result.success
        assert "all permissions" in result.message.lower()
        assert result.status_code == 200
        # Should NOT store error result or attempt token exchange
        assert mock_redis.set.call_count == 0

    @patch("codemie.service.google_oauth.flow_service.config")
    @patch("codemie.service.google_oauth.flow_service.Flow")
    def test_handle_callback_proceeds_when_all_scopes_granted(
        self, mock_flow_class, mock_config, service, mock_redis, mock_encryption
    ):
        """Should proceed to token exchange when all required scopes are granted."""
        mock_config.google_oauth_redirect_uri = "http://redirect"

        # Setup state
        state_data = {"code_verifier": "verifier", "client_id": "client", "user_id": "user123"}
        encrypted_state = mock_encryption.encrypt(json.dumps(state_data))
        mock_redis.getdel.return_value = encrypted_state

        # Setup Flow
        mock_flow = MagicMock()
        mock_credentials = MagicMock()
        mock_credentials.token = "access_token"
        mock_credentials.refresh_token = "refresh_token"
        mock_credentials.expiry = datetime.now(UTC) + timedelta(hours=1)
        mock_flow.credentials = mock_credentials
        mock_flow_class.from_client_config.return_value = mock_flow

        # Grant all required scopes
        granted_scope = " ".join(GOOGLE_OAUTH_SCOPES)

        service.handle_callback("auth_code", "test_state", None, granted_scope)

        # Should have called fetch_token (scope validation passed)
        mock_flow.fetch_token.assert_called_once()

    @patch("codemie.service.google_oauth.flow_service.config")
    @patch("codemie.service.google_oauth.flow_service.Flow")
    def test_handle_callback_handles_token_exchange_failure_without_storing(
        self, mock_flow_class, mock_config, service, mock_redis, mock_encryption
    ):
        """Should not store result when token exchange fails."""
        mock_config.google_oauth_redirect_uri = "http://redirect"

        state_data = {"code_verifier": "verifier", "client_id": "client", "user_id": "user123"}
        encrypted_state = mock_encryption.encrypt(json.dumps(state_data))
        mock_redis.getdel.return_value = encrypted_state

        # Setup Flow to fail
        mock_flow = MagicMock()
        mock_flow.fetch_token.side_effect = Exception("Token exchange failed")
        mock_flow_class.from_client_config.return_value = mock_flow

        granted_scope = " ".join(GOOGLE_OAUTH_SCOPES)
        result = service.handle_callback("auth_code", "test_state", None, granted_scope)

        assert not result.success
        assert "failed" in result.message.lower()
        # Should NOT store error result
        assert mock_redis.set.call_count == 0

    @patch("codemie.service.google_oauth.flow_service.config")
    @patch("codemie.service.google_oauth.flow_service.Flow")
    @patch("codemie.service.google_oauth.flow_service.build")
    def test_handle_callback_stores_success_result(
        self, mock_build, mock_flow_class, mock_config, service, mock_redis, mock_encryption
    ):
        """Should store success result with tokens in Redis."""
        mock_config.google_oauth_redirect_uri = "http://redirect"

        # Setup state
        state_data = {"code_verifier": "verifier", "client_id": "client", "user_id": "user123"}
        encrypted_state = mock_encryption.encrypt(json.dumps(state_data))
        mock_redis.getdel.return_value = encrypted_state

        # Setup Flow
        mock_flow = MagicMock()
        mock_credentials = MagicMock()
        mock_credentials.token = "access_token"
        mock_credentials.refresh_token = "refresh_token"
        mock_credentials.expiry = datetime.now(UTC) + timedelta(hours=1)
        mock_flow.credentials = mock_credentials
        mock_flow_class.from_client_config.return_value = mock_flow

        # Mock userinfo fetch
        mock_service = MagicMock()
        mock_service.userinfo().get().execute.return_value = {"email": "test@example.com"}
        mock_build.return_value = mock_service

        granted_scope = " ".join(GOOGLE_OAUTH_SCOPES)
        result = service.handle_callback("auth_code", "test_state", None, granted_scope)

        assert result.success
        assert result.status_code == 200
        # Should have stored result in Redis
        assert mock_redis.set.call_count >= 1
