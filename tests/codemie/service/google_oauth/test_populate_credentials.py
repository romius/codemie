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

"""Unit tests for GoogleOAuthSettingsService.populate_credentials_from_flow()."""

import json
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings import CredentialValues
from codemie.service.google_oauth.settings_service import GoogleOAuthSettingsService


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
def service(mock_encryption):
    """GoogleOAuthSettingsService instance with mocked encryption."""
    return GoogleOAuthSettingsService(encryption_service=mock_encryption)


class TestPopulateCredentialsFromFlow:
    """Test populate_credentials_from_flow method."""

    def test_raises_400_when_flow_not_completed(self, service):
        """Should raise 400 when OAuth flow status is not 'success'."""
        with patch.object(service.flow_service, 'get_status', return_value={"status": "pending"}):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                service.populate_credentials_from_flow("test_state", "user123")

            assert exc_info.value.code == 400
            assert "not completed" in exc_info.value.message.lower()

    def test_raises_400_when_flow_failed(self, service):
        """Should raise 400 when OAuth flow status is 'error'."""
        with patch.object(
            service.flow_service, 'get_status', return_value={"status": "error", "message": "Access denied"}
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                service.populate_credentials_from_flow("test_state", "user123")

            assert exc_info.value.code == 400
            assert "error" in exc_info.value.details.lower()

    def test_raises_502_when_token_data_missing(self, service):
        """Should raise 502 when token_data is missing from result."""
        with patch.object(service.flow_service, 'get_status', return_value={"status": "success"}):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                service.populate_credentials_from_flow("test_state", "user123")

            assert exc_info.value.code == 502
            assert "token data missing" in exc_info.value.message.lower()

    def test_raises_502_when_access_token_missing(self, service, mock_encryption):
        """Should raise 502 when access_token is missing from token_data."""
        token_data = {"refresh_token": "refresh123", "expires_in": 3600}
        encrypted_token_data = mock_encryption.encrypt(json.dumps(token_data))

        with patch.object(
            service.flow_service,
            'get_status',
            return_value={"status": "success", "token_data": encrypted_token_data, "email": "test@example.com"},
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                service.populate_credentials_from_flow("test_state", "user123")

            assert exc_info.value.code == 502
            assert "access token missing" in exc_info.value.message.lower()

    def test_raises_502_when_refresh_token_missing(self, service, mock_encryption):
        """Should raise 502 when refresh_token is missing from token_data."""
        token_data = {"access_token": "access123", "expires_in": 3600}
        encrypted_token_data = mock_encryption.encrypt(json.dumps(token_data))

        with patch.object(
            service.flow_service,
            'get_status',
            return_value={"status": "success", "token_data": encrypted_token_data, "email": "test@example.com"},
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                service.populate_credentials_from_flow("test_state", "user123")

            assert exc_info.value.code == 502
            assert "refresh token missing" in exc_info.value.message.lower()

    def test_returns_encrypted_credential_values(self, service, mock_encryption):
        """Should return list of CredentialValues with plaintext tokens (encryption deferred to SettingsService)."""
        token_data = {"access_token": "access123", "refresh_token": "refresh123", "expires_in": 3600}
        encrypted_token_data = mock_encryption.encrypt(json.dumps(token_data))

        with patch.object(
            service.flow_service,
            'get_status',
            return_value={"status": "success", "token_data": encrypted_token_data, "email": "test@example.com"},
        ):
            with patch('time.time', return_value=1000.0):
                creds = service.populate_credentials_from_flow("test_state", "user123")

        # Should return 4 CredentialValues
        assert len(creds) == 4
        assert all(isinstance(c, CredentialValues) for c in creds)

        # Check keys
        keys = {c.key for c in creds}
        assert keys == {"access_token", "refresh_token", "expires_at", "email"}

        # Check values (plaintext - encryption handled by SettingsService._encrypt_fields)
        cred_dict = {c.key: c.value for c in creds}
        assert cred_dict["access_token"] == "access123"
        assert cred_dict["refresh_token"] == "refresh123"
        assert cred_dict["expires_at"] == "4600"  # 1000 + 3600
        assert cred_dict["email"] == "test@example.com"

    def test_uses_default_expires_in_when_missing(self, service, mock_encryption):
        """Should default to 3600 seconds when expires_in is missing."""
        token_data = {
            "access_token": "access123",
            "refresh_token": "refresh123",
            # expires_in missing
        }
        encrypted_token_data = mock_encryption.encrypt(json.dumps(token_data))

        with patch.object(
            service.flow_service,
            'get_status',
            return_value={"status": "success", "token_data": encrypted_token_data, "email": "test@example.com"},
        ):
            with patch('time.time', return_value=1000.0):
                creds = service.populate_credentials_from_flow("test_state", "user123")

        cred_dict = {c.key: c.value for c in creds}
        assert cred_dict["expires_at"] == "4600"  # 1000 + 3600 (default)

    def test_uses_empty_string_when_email_missing(self, service, mock_encryption):
        """Should use empty string when email is missing from result."""
        token_data = {"access_token": "access123", "refresh_token": "refresh123", "expires_in": 3600}
        encrypted_token_data = mock_encryption.encrypt(json.dumps(token_data))

        with patch.object(
            service.flow_service,
            'get_status',
            return_value={
                "status": "success",
                "token_data": encrypted_token_data,
                # email missing
            },
        ):
            with patch('time.time', return_value=1000.0):
                creds = service.populate_credentials_from_flow("test_state", "user123")

        cred_dict = {c.key: c.value for c in creds}
        assert cred_dict["email"] == ""

    def test_decrypts_token_data_correctly(self, service, mock_encryption):
        """Should decrypt token_data using encryption service."""
        token_data = {"access_token": "access123", "refresh_token": "refresh123", "expires_in": 3600}
        encrypted_token_data = mock_encryption.encrypt(json.dumps(token_data))

        with patch.object(
            service.flow_service,
            'get_status',
            return_value={"status": "success", "token_data": encrypted_token_data, "email": "test@example.com"},
        ):
            with patch('time.time', return_value=1000.0):
                creds = service.populate_credentials_from_flow("test_state", "user123")

        # Mock encryption was called to decrypt token_data
        mock_encryption.decrypt.assert_called()

        # Tokens returned as plaintext strings (encryption deferred to SettingsService)
        cred_dict = {c.key: c.value for c in creds}
        assert isinstance(cred_dict["access_token"], str)
        assert isinstance(cred_dict["refresh_token"], str)

    def test_reuses_refresh_token_when_email_matches(self, service, mock_encryption):
        """Should reuse existing refresh_token when authenticated email matches existing integration."""
        # Existing integration credentials
        existing_creds = [
            CredentialValues(key="email", value="test@example.com"),
            CredentialValues(key="refresh_token", value=mock_encryption.encrypt("old_refresh123")),
            CredentialValues(key="access_token", value=mock_encryption.encrypt("old_access123")),
            CredentialValues(key="expires_at", value="12345"),
        ]

        # New token data WITHOUT refresh_token (Google didn't return it because account already authorized)
        token_data = {"access_token": "new_access123", "expires_in": 3600}
        encrypted_token_data = mock_encryption.encrypt(json.dumps(token_data))

        with patch.object(
            service.flow_service,
            'get_status',
            return_value={"status": "success", "token_data": encrypted_token_data, "email": "test@example.com"},
        ):
            with patch('time.time', return_value=1000.0):
                creds = service.populate_credentials_from_flow(
                    "test_state", "user123", existing_credentials=existing_creds
                )

        # Should return credentials with REUSED refresh_token
        cred_dict = {c.key: c.value for c in creds}
        assert cred_dict["access_token"] == "new_access123"
        assert cred_dict["refresh_token"] == "old_refresh123"  # Reused from existing
        assert cred_dict["email"] == "test@example.com"

    def test_raises_502_when_email_differs_and_no_refresh_token(self, service, mock_encryption):
        """Should raise 502 when authenticated email differs from existing integration and no refresh_token."""
        # Existing integration for different email
        existing_creds = [
            CredentialValues(key="email", value="old@example.com"),
            CredentialValues(key="refresh_token", value=mock_encryption.encrypt("old_refresh123")),
        ]

        # New token data WITHOUT refresh_token, different email
        token_data = {"access_token": "new_access123", "expires_in": 3600}
        encrypted_token_data = mock_encryption.encrypt(json.dumps(token_data))

        with patch.object(
            service.flow_service,
            'get_status',
            return_value={"status": "success", "token_data": encrypted_token_data, "email": "new@example.com"},
        ):
            with pytest.raises(ExtendedHTTPException) as exc_info:
                service.populate_credentials_from_flow("test_state", "user123", existing_credentials=existing_creds)

            assert exc_info.value.code == 502
            assert "refresh token missing" in exc_info.value.message.lower()
            assert "new@example.com" in exc_info.value.details

    def test_works_without_existing_credentials_when_refresh_token_present(self, service, mock_encryption):
        """Should work without existing_credentials when Google returns refresh_token."""
        token_data = {"access_token": "access123", "refresh_token": "refresh123", "expires_in": 3600}
        encrypted_token_data = mock_encryption.encrypt(json.dumps(token_data))

        with patch.object(
            service.flow_service,
            'get_status',
            return_value={"status": "success", "token_data": encrypted_token_data, "email": "test@example.com"},
        ):
            with patch('time.time', return_value=1000.0):
                # No existing_credentials provided (new integration)
                creds = service.populate_credentials_from_flow("test_state", "user123")

        cred_dict = {c.key: c.value for c in creds}
        assert cred_dict["refresh_token"] == "refresh123"


class TestRevokeOldCredentialsIfEmailChanged:
    """Test revoke_old_credentials_if_email_changed method."""

    def test_does_nothing_when_email_unchanged(self, service, mock_encryption):
        """Should not revoke when email stays the same."""
        existing_creds = [
            CredentialValues(key="email", value="test@example.com"),
            CredentialValues(key="refresh_token", value=mock_encryption.encrypt("refresh123")),
        ]
        new_creds = [
            CredentialValues(key="email", value="test@example.com"),
            CredentialValues(key="refresh_token", value="refresh456"),
        ]

        with patch.object(service.token_manager, '_has_other_integrations_with_same_token') as mock_check:
            service.revoke_old_credentials_if_email_changed("user123", "setting456", existing_creds, new_creds)

            # Should not check for other integrations since email didn't change
            mock_check.assert_not_called()

    def test_does_nothing_when_old_email_missing(self, service, mock_encryption):
        """Should not revoke when old credentials have no email."""
        existing_creds = [
            CredentialValues(key="refresh_token", value=mock_encryption.encrypt("refresh123")),
        ]
        new_creds = [
            CredentialValues(key="email", value="new@example.com"),
            CredentialValues(key="refresh_token", value="refresh456"),
        ]

        with patch.object(service.token_manager, '_has_other_integrations_with_same_token') as mock_check:
            service.revoke_old_credentials_if_email_changed("user123", "setting456", existing_creds, new_creds)

            mock_check.assert_not_called()

    def test_does_nothing_when_new_email_missing(self, service, mock_encryption):
        """Should not revoke when new credentials have no email."""
        existing_creds = [
            CredentialValues(key="email", value="old@example.com"),
            CredentialValues(key="refresh_token", value=mock_encryption.encrypt("refresh123")),
        ]
        new_creds = [
            CredentialValues(key="refresh_token", value="refresh456"),
        ]

        with patch.object(service.token_manager, '_has_other_integrations_with_same_token') as mock_check:
            service.revoke_old_credentials_if_email_changed("user123", "setting456", existing_creds, new_creds)

            mock_check.assert_not_called()

    def test_does_nothing_when_other_integrations_use_same_token(self, service, mock_encryption):
        """Should not revoke when other integrations still use the old credentials."""
        existing_creds = [
            CredentialValues(key="email", value="old@example.com"),
            CredentialValues(key="refresh_token", value=mock_encryption.encrypt("refresh123")),
        ]
        new_creds = [
            CredentialValues(key="email", value="new@example.com"),
            CredentialValues(key="refresh_token", value="refresh456"),
        ]

        with patch.object(service.token_manager, '_has_other_integrations_with_same_token', return_value=True):
            # Mock requests module imported inside the method
            import sys
            from unittest.mock import MagicMock

            mock_requests = MagicMock()
            with patch.dict(sys.modules, {'requests': mock_requests}):
                service.revoke_old_credentials_if_email_changed("user123", "setting456", existing_creds, new_creds)

                # Should not call Google's revoke endpoint
                mock_requests.post.assert_not_called()

    def test_revokes_when_email_changed_and_no_other_integrations(self, service, mock_encryption):
        """Should revoke old credentials when email changed and no other integrations use them."""
        existing_creds = [
            CredentialValues(key="email", value="old@example.com"),
            CredentialValues(key="refresh_token", value=mock_encryption.encrypt("refresh123")),
        ]
        new_creds = [
            CredentialValues(key="email", value="new@example.com"),
            CredentialValues(key="refresh_token", value="refresh456"),
        ]

        # Mock the token_manager.revoke_token_from_credentials method
        with patch.object(service.token_manager, 'revoke_token_from_credentials') as mock_revoke:
            service.revoke_old_credentials_if_email_changed("user123", "setting456", existing_creds, new_creds)

            # Should call revoke_token_from_credentials with correct arguments
            mock_revoke.assert_called_once_with(
                "user123",
                "setting456",
                "old@example.com",
                mock_encryption.encrypt("refresh123"),
            )

    def test_logs_warning_on_revocation_failure(self, service, mock_encryption):
        """Should log warning when revocation fails but not raise exception."""
        existing_creds = [
            CredentialValues(key="email", value="old@example.com"),
            CredentialValues(key="refresh_token", value=mock_encryption.encrypt("refresh123")),
        ]
        new_creds = [
            CredentialValues(key="email", value="new@example.com"),
            CredentialValues(key="refresh_token", value="refresh456"),
        ]

        # Mock the token_manager.revoke_token_from_credentials to raise exception
        with patch.object(service.token_manager, 'revoke_token_from_credentials', side_effect=Exception("Test error")):
            # Should not raise exception, just log warning
            service.revoke_old_credentials_if_email_changed("user123", "setting456", existing_creds, new_creds)

            # No assertion needed - we just verify it doesn't raise
