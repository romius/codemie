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


def test_sharepoint_config_is_importable():
    from codemie_tools.data_management.sharepoint.models import SharePointConfig

    assert SharePointConfig is not None


def test_sharepoint_url_placeholder_in_schema():
    from codemie_tools.data_management.sharepoint.models import SharePointConfig

    schema = SharePointConfig.model_json_schema()
    assert schema["properties"]["url"].get("placeholder") == "SharePoint URL, e.g. https://yourtenant.sharepoint.com"
    assert schema["properties"]["url"].get("required_at_runtime") is True


def test_sharepoint_credential_type():
    from codemie_tools.data_management.sharepoint.models import SharePointConfig
    from codemie_tools.base.models import CredentialTypes

    config = SharePointConfig(url="https://contoso.sharepoint.com")
    assert config.credential_type == CredentialTypes.SHAREPOINT


def test_sharepoint_url_is_required_at_runtime():
    from codemie_tools.data_management.sharepoint.models import SharePointConfig

    # default is empty when no tool_defaults configured; app enforces required_at_runtime at use time
    config = SharePointConfig()
    assert config.url == ""


def test_app_auth_fields_are_required_at_runtime():
    from codemie_tools.data_management.sharepoint.models import SharePointConfig

    schema = SharePointConfig.model_json_schema()
    for field in ("tenant_id", "client_id", "client_secret"):
        assert schema["properties"][field].get("required_at_runtime") is True, field


def test_client_secret_is_marked_sensitive():
    from codemie_tools.data_management.sharepoint.models import SharePointConfig

    schema = SharePointConfig.model_json_schema()
    assert schema["properties"]["client_secret"].get("sensitive") is True


def test_auth_type_defaults_to_app():
    from codemie_tools.data_management.sharepoint.models import SharePointConfig

    assert SharePointConfig().auth_type == "app"


def test_access_token_is_hidden_and_sensitive():
    """It comes from the sign-in flow; it must never render as an editable form input."""
    from codemie_tools.data_management.sharepoint.models import SharePointConfig

    access_token = SharePointConfig.model_json_schema()["properties"]["access_token"]
    assert access_token.get("hidden") is True
    assert access_token.get("sensitive") is True


def test_the_refresh_token_never_reaches_a_tool():
    """The platform renews the access token, so the long-lived credential stays on the
    setting. A tool that cannot hold it cannot leak it."""
    from codemie_tools.data_management.sharepoint.models import SharePointConfig

    assert "refresh_token" not in SharePointConfig.model_fields
    assert "expires_at" not in SharePointConfig.model_fields


def test_config_is_a_file_config_so_chat_attachments_are_injected():
    """toolkit_service._init_tool gates attachment injection on isinstance(config, FileConfigMixin).

    Dropping the mixin makes uploads fail silently: no files arrive and the tool reports
    "no files are attached" for a conversation that has them.
    """
    from codemie_tools.base.models import FileConfigMixin
    from codemie_tools.data_management.sharepoint.models import SharePointConfig

    assert isinstance(SharePointConfig(url="https://contoso.sharepoint.com"), FileConfigMixin)


def test_input_files_are_not_persisted_to_settings():
    from codemie_tools.data_management.sharepoint.models import SharePointConfig

    config = SharePointConfig(url="https://contoso.sharepoint.com", input_files=["anything"])

    assert "input_files" not in config.model_dump()


def test_delegated_credential_keys_are_loaded():
    """A delegated integration is built from stored values; the refresh token is not one of them."""
    from codemie_tools.data_management.sharepoint.models import SharePointConfig

    config = SharePointConfig(
        url="https://contoso.sharepoint.com",
        auth_type="oauth",
        access_token="delegated-token",
        refresh_token="delegated-refresh",
        expires_at=1700000000,
        username="someone@contoso.com",
    )

    assert config.auth_type == "oauth"
    assert config.access_token == "delegated-token"
    assert not hasattr(config, "refresh_token")


def test_unknown_stored_keys_are_still_ignored():
    """SettingsTester builds the config from every stored credential key."""
    from codemie_tools.data_management.sharepoint.models import SharePointConfig

    config = SharePointConfig(
        url="https://contoso.sharepoint.com",
        tenant_id="tenant-id",
        client_id="client-id",
        client_secret="client-secret",
        some_legacy_key="whatever",
    )

    assert config.tenant_id == "tenant-id"
    assert not hasattr(config, "some_legacy_key")
