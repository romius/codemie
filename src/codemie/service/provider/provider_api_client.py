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

from codemie.clients.provider import client as provider_client
from codemie.rest_api.security.user import User
from codemie.rest_api.models.provider import ProviderConfiguration
from codemie.service.security.principal_token_resolver import (
    PrincipalType,
    resolve_current_bearer_token,
)
from codemie.configs import logger, config

PRINCIPAL_TYPE_HEADER = "X-Auth-Principal-Type"


class ProviderAPIClient:
    """Client for interacting with the provider's API endpoints."""

    LOCAL_MOCK_BEARER = "local"
    NO_AUTH_TOKEN_MSG = (
        "No authentication credential is available for this request. "
        "CodeMie requires either a user bearer token (Authorization header) or, "
        "in BFF deployments, a client access token forwarded via the "
        "'x-auth-request-access-token' header. The 'x-userinfo' header carries "
        "identity claims only and cannot be used as a token. "
        "Please review your gateway/BFF configuration or contact your administrator."
    )

    def __init__(
        self,
        user: User,
        url: str,
        provider_security_config: ProviderConfiguration,
        log_prefix: str = "",
    ):
        self.user = user
        self.url = url
        self.provider_security_config = provider_security_config  # Fixed variable name to match usage in methods
        self.log_prefix = log_prefix

    def build(self) -> provider_client.ToolInvocationManagementApi:
        """Build a provider client for tool invocation management."""
        host = self.url.rstrip("/")
        credentials, principal_type = self._get_auth_credentials()
        client_config = provider_client.Configuration(host=host, **credentials)
        client_config.verify_ssl = False

        with provider_client.ApiClient(client_config) as api_client:
            if principal_type is not None:
                api_client.set_default_header(PRINCIPAL_TYPE_HEADER, principal_type.value)
            return provider_client.ToolInvocationManagementApi(api_client)

    def _get_auth_credentials(self) -> tuple[dict, PrincipalType | None]:
        """Retrieve authentication credentials and the principal type they represent.

        Security: never logs token values — only principal type and operation status.
        """
        security_config = self.provider_security_config

        if config.is_local:
            logger.info(f"{self.log_prefix} Using local mock bearer token")
            return {"access_token": self.LOCAL_MOCK_BEARER}, PrincipalType.USER

        if security_config.auth_type == ProviderConfiguration.AuthType.BEARER.value:
            token, principal_type = resolve_current_bearer_token(self.user)

            if not token:
                logger.error(f"{self.log_prefix} No bearer credential available (neither user nor client principal)")
                raise ValueError(self.NO_AUTH_TOKEN_MSG)

            logger.info(f"{self.log_prefix} Using {principal_type.value}-principal bearer token")
            return {"access_token": token}, principal_type

        logger.error(f"{self.log_prefix} Unknown auth type: {security_config.auth_type}")
        raise ValueError(f"Unknown auth type: {security_config.auth_type}")
