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

from pathlib import Path
from unittest.mock import patch

from codemie.configs.managed_mcp_config import (
    ManagedMcpOAuthConfig,
    ManagedMcpServer,
    load_managed_mcp_servers,
)

SAMPLE_YAML = """
servers:
  - name: sample
    transport: http
    url: https://mcp.example.com/mcp/sample
    auth: oauth
    clients: [claude-desktop, codex]
  - name: globalmcp
    transport: http
    url: https://mcp.example.com/mcp/global
    auth: oauth
"""

OAUTH_YAML = """
servers:
  - name: with_oauth
    transport: http
    url: https://mcp.example.com/mcp/with_oauth
    auth: oauth
    clients: [claude-desktop]
    oauth:
      clientId: codemie-mcp-proxy
      scope: openid profile email
      callbackHost: 127.0.0.1
      callbackPort: 3118
      authorizationUrl: https://auth.example.com/realms/codemie/protocol/openid-connect/auth?kc_idp_hint=epam-oidc
      tokenUrl: https://auth.example.com/realms/codemie/protocol/openid-connect/token
  - name: without_oauth
    transport: http
    url: https://mcp.example.com/mcp/without_oauth
"""


def _write(dir_path: Path, text: str) -> Path:
    (dir_path / "managed-mcp-servers.yaml").write_text(text)
    return dir_path


def test_missing_file_returns_empty(tmp_path: Path):
    assert load_managed_mcp_servers(base_dir=tmp_path) == []


def test_loads_and_parses_entries(tmp_path: Path):
    _write(tmp_path, SAMPLE_YAML)
    servers = load_managed_mcp_servers(base_dir=tmp_path)
    assert [s.name for s in servers] == ["sample", "globalmcp"]
    assert servers[0].url == "https://mcp.example.com/mcp/sample"
    assert servers[0].auth == "oauth"


def test_skips_malformed_entries(tmp_path: Path):
    _write(
        tmp_path,
        "servers:\n  - {name: ok, transport: http, url: https://a}\n  - {name: bad, transport: ftp, url: https://b}\n",
    )
    servers = load_managed_mcp_servers(base_dir=tmp_path)
    assert [s.name for s in servers] == ["ok"]


def test_filters_by_client(tmp_path: Path):
    _write(tmp_path, SAMPLE_YAML)
    codex = load_managed_mcp_servers(client="codex", base_dir=tmp_path)
    assert {s.name for s in codex} == {"sample", "globalmcp"}
    _write(tmp_path, "servers:\n  - {name: only_cd, transport: http, url: https://x, clients: [claude-desktop]}\n")
    assert load_managed_mcp_servers(client="codex", base_dir=tmp_path) == []
    assert [s.name for s in load_managed_mcp_servers(client="claude-desktop", base_dir=tmp_path)] == ["only_cd"]


def test_corrupt_yaml_returns_empty(tmp_path: Path):
    _write(tmp_path, "servers: [unclosed")
    assert load_managed_mcp_servers(base_dir=tmp_path) == []


def test_returns_typed_models(tmp_path: Path):
    _write(tmp_path, SAMPLE_YAML)
    servers = load_managed_mcp_servers(base_dir=tmp_path)
    assert all(isinstance(s, ManagedMcpServer) for s in servers)


def test_path_is_directory_returns_empty(tmp_path: Path):
    # A directory at the catalog path raises IsADirectoryError (an OSError);
    # the loader must still return [] rather than propagate.
    (tmp_path / "managed-mcp-servers.yaml").mkdir()
    assert load_managed_mcp_servers(base_dir=tmp_path) == []


def test_non_dict_root_returns_empty(tmp_path: Path):
    _write(tmp_path, "just a string")
    assert load_managed_mcp_servers(base_dir=tmp_path) == []


def test_non_list_servers_returns_empty(tmp_path: Path):
    _write(tmp_path, "servers: not-a-list")
    assert load_managed_mcp_servers(base_dir=tmp_path) == []


def test_non_dict_entry_is_skipped(tmp_path: Path):
    _write(tmp_path, "servers:\n  - just-a-string\n  - {name: ok, transport: http, url: https://a}\n")
    servers = load_managed_mcp_servers(base_dir=tmp_path)
    assert [s.name for s in servers] == ["ok"]


def test_loads_oauth_block(tmp_path: Path):
    _write(tmp_path, OAUTH_YAML)
    servers = load_managed_mcp_servers(base_dir=tmp_path)
    assert [s.name for s in servers] == ["with_oauth", "without_oauth"]

    oauth = servers[0].oauth
    assert isinstance(oauth, ManagedMcpOAuthConfig)
    assert oauth.client_id == "codemie-mcp-proxy"
    assert oauth.scope == "openid profile email"
    assert oauth.callback_host == "127.0.0.1"
    assert oauth.callback_port == 3118
    assert (
        oauth.authorization_url
        == "https://auth.example.com/realms/codemie/protocol/openid-connect/auth?kc_idp_hint=epam-oidc"
    )
    assert oauth.token_url == "https://auth.example.com/realms/codemie/protocol/openid-connect/token"


def test_entry_without_oauth_block_still_loads(tmp_path: Path):
    _write(tmp_path, OAUTH_YAML)
    servers = load_managed_mcp_servers(base_dir=tmp_path)
    assert servers[1].name == "without_oauth"
    assert servers[1].oauth is None


def test_callback_host_defaults_to_localhost(tmp_path: Path):
    _write(
        tmp_path,
        """
servers:
  - name: defaulted
    transport: http
    url: https://mcp.example.com/mcp/defaulted
    oauth:
      clientId: codemie-mcp-proxy
      scope: openid
      callbackPort: 3118
      authorizationUrl: https://auth.example.com/authorize
      tokenUrl: https://auth.example.com/token
""",
    )
    servers = load_managed_mcp_servers(base_dir=tmp_path)
    assert servers[0].oauth.callback_host == "localhost"


def test_blank_callback_host_falls_back_to_default(tmp_path: Path):
    # `callbackHost:` written with no value parses as None, not as an absent
    # key. The key is documented as optional with a `localhost` default, so a
    # blank value must fall back to it rather than dropping the whole entry.
    _write(
        tmp_path,
        """
servers:
  - name: blank_host
    transport: http
    url: https://mcp.example.com/mcp/blank
    oauth:
      clientId: codemie-mcp-proxy
      scope: openid
      callbackHost:
      callbackPort: 3118
      authorizationUrl: https://auth.example.com/authorize
      tokenUrl: https://auth.example.com/token
""",
    )
    servers = load_managed_mcp_servers(base_dir=tmp_path)
    assert [s.name for s in servers] == ["blank_host"]
    assert servers[0].oauth.callback_host == "localhost"


def test_skips_entry_with_missing_required_oauth_key(tmp_path: Path):
    # `callbackPort` is required; its absence must skip the whole entry and
    # leave every sibling entry loading normally.
    _write(
        tmp_path,
        """
servers:
  - name: bad_oauth
    transport: http
    url: https://mcp.example.com/mcp/bad
    oauth:
      clientId: codemie-mcp-proxy
      scope: openid
      authorizationUrl: https://auth.example.com/authorize
      tokenUrl: https://auth.example.com/token
  - name: good
    transport: http
    url: https://mcp.example.com/mcp/good
""",
    )
    with patch("codemie.configs.managed_mcp_config.logger") as mock_logger:
        servers = load_managed_mcp_servers(base_dir=tmp_path)

    assert [s.name for s in servers] == ["good"]
    mock_logger.warning.assert_called_once()


def test_skips_entry_with_out_of_range_callback_port(tmp_path: Path):
    _write(
        tmp_path,
        """
servers:
  - name: bad_port
    transport: http
    url: https://mcp.example.com/mcp/bad
    oauth:
      clientId: codemie-mcp-proxy
      scope: openid
      callbackPort: 70000
      authorizationUrl: https://auth.example.com/authorize
      tokenUrl: https://auth.example.com/token
  - name: good
    transport: http
    url: https://mcp.example.com/mcp/good
""",
    )
    with patch("codemie.configs.managed_mcp_config.logger") as mock_logger:
        servers = load_managed_mcp_servers(base_dir=tmp_path)

    assert [s.name for s in servers] == ["good"]
    mock_logger.warning.assert_called_once()


def test_snake_case_oauth_key_is_rejected(tmp_path: Path):
    # camelCase aliases are the ONLY accepted spelling (`populate_by_name` is
    # deliberately unset), so `client_id` is not a second form -- it is a
    # missing required key, and the entry is skipped and logged.
    _write(
        tmp_path,
        """
servers:
  - name: snake_cased
    transport: http
    url: https://mcp.example.com/mcp/snake
    oauth:
      client_id: codemie-mcp-proxy
      scope: openid
      callback_port: 3118
      authorizationUrl: https://auth.example.com/authorize
      tokenUrl: https://auth.example.com/token
  - name: good
    transport: http
    url: https://mcp.example.com/mcp/good
""",
    )
    with patch("codemie.configs.managed_mcp_config.logger") as mock_logger:
        servers = load_managed_mcp_servers(base_dir=tmp_path)

    assert [s.name for s in servers] == ["good"]
    mock_logger.warning.assert_called_once()


def test_filters_by_client_is_unaffected_by_oauth(tmp_path: Path):
    _write(tmp_path, OAUTH_YAML)

    codex = load_managed_mcp_servers(client="codex", base_dir=tmp_path)
    assert [s.name for s in codex] == ["without_oauth"]

    claude_desktop = load_managed_mcp_servers(client="claude-desktop", base_dir=tmp_path)
    assert [s.name for s in claude_desktop] == ["with_oauth", "without_oauth"]
    assert claude_desktop[0].oauth.client_id == "codemie-mcp-proxy"


def test_example_file_is_valid():
    from pathlib import Path

    import codemie

    repo_root = Path(codemie.__file__).resolve().parents[2]
    example = repo_root / "config" / "customer" / "managed-mcp-servers.example.yaml"
    assert example.exists(), f"example file missing at {example}"

    import yaml

    data = yaml.safe_load(example.read_text())
    assert isinstance(data, dict) and isinstance(data.get("servers"), list)
    servers = [ManagedMcpServer(**item) for item in data["servers"]]

    # The example is the only in-repo expression of the schema, so it must
    # document a complete `oauth` block -- in camelCase, the only spelling the
    # model accepts.
    documented = [s for s in servers if s.oauth is not None]
    assert documented, "example file must document an oauth block"
    oauth = documented[0].oauth
    assert oauth.client_id
    assert oauth.scope
    assert oauth.authorization_url.startswith("https://")
    assert oauth.token_url.startswith("https://")
    assert 1 <= oauth.callback_port <= 65535
    assert oauth.callback_host == "localhost"

    # The loader reads `managed-mcp-servers.yaml`, so the `.example.yaml` file
    # is documentation only and must NOT be picked up.
    servers = load_managed_mcp_servers(base_dir=example.parent)
    assert servers == []
