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

"""
Loader for the client-neutral managed MCP server catalog.

The catalog file (`managed-mcp-servers.yaml`) is NOT committed to this
repository — it is supplied per deployment as a key in the
`codemie-customer-config` ConfigMap, mounted at `CUSTOMER_CONFIG_DIR`. This
loader is intentionally resilient: a missing or malformed file yields an empty
list rather than raising, so the endpoint degrades to "no managed MCPs".
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, List, Literal, Optional

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError
from pydantic_core import PydanticUseDefault

from codemie.configs.config import config
from codemie.configs.logger import logger

MANAGED_MCP_FILENAME = "managed-mcp-servers.yaml"


def _use_default_if_none(value: Any) -> Any:
    """
    Let an explicitly blank YAML key fall back to the field's default.

    `callbackHost:` written with no value parses as None, not as an absent key,
    and a non-Optional field would reject it -- dropping the whole server entry
    over an omitted *optional* value. Raising PydanticUseDefault keeps the
    default defined in exactly one place: the field itself.
    """
    if value is None:
        raise PydanticUseDefault
    return value


class ManagedMcpOAuthConfig(BaseModel):
    """
    Public OAuth client parameters for one managed MCP server.

    Secret-free by design: only public-client values are published (client id,
    scope, loopback callback, IdP authorize/token URLs). No client secret is
    stored or transmitted, and the token exchange stays entirely client-side.

    Field aliases are camelCase and `populate_by_name` is deliberately NOT set,
    so the alias is the only accepted input spelling. That is what keeps the
    YAML catalog and the HTTP response identical: a snake_case key such as
    `client_id` is not a second accepted form -- it fails validation, and the
    whole entry is skipped and logged like any other malformed entry.
    """

    client_id: str = Field(alias="clientId")
    scope: str
    callback_host: Annotated[str, BeforeValidator(_use_default_if_none)] = Field(
        default="localhost", alias="callbackHost"
    )
    callback_port: int = Field(alias="callbackPort", ge=1, le=65535)
    authorization_url: str = Field(alias="authorizationUrl")
    token_url: str = Field(alias="tokenUrl")

    # Optional: the IdP issuer(s). Published so a client uses issuer discovery
    # instead of fabricating metadata from tokenUrl's origin (RFC 9207). A list,
    # matching the shape the consuming clients expect; a scalar is invalid.
    authorization_server: Optional[List[str]] = Field(default=None, alias="authorizationServer")

    # Inherits this module's graceful-degradation contract rather than a
    # stricter one: an unknown key is dropped at validation -- silently, and
    # without reaching the response. The entry still loads, so a ConfigMap may
    # run ahead of a backend rollout, but a key is served only once it is a
    # declared field here.
    model_config = ConfigDict(extra="ignore")


class ManagedMcpServer(BaseModel):
    """
    A client-neutral managed MCP server entry. Remote-only in v1.

    `oauth` is the source of truth for OAuth client configuration. The scalar
    `auth` field is DEPRECATED: it is retained unchanged so existing ConfigMaps
    keep working, and is never derived from `oauth` -- that would silently
    override what an operator wrote. Removal is a follow-up ticket, once
    deployments have migrated.
    """

    name: str
    transport: Literal["http", "sse"]
    url: str
    auth: Literal["oauth", "none"] = "none"  # deprecated -- prefer the `oauth` block
    description: Optional[str] = None
    clients: Optional[List[str]] = None
    oauth: Optional[ManagedMcpOAuthConfig] = None

    model_config = ConfigDict(extra="ignore")


def load_managed_mcp_servers(
    client: Optional[str] = None,
    base_dir: Optional[Path] = None,
) -> List[ManagedMcpServer]:
    """
    Load managed MCP servers from the customer ConfigMap directory.

    Args:
        client: optional client id; entries are kept when they have no
            `clients` targeting (apply to all) or include this client.
        base_dir: override the config directory (defaults to CUSTOMER_CONFIG_DIR).

    Returns:
        Validated entries; never raises (missing/corrupt file -> []).
    """
    directory = Path(base_dir) if base_dir is not None else Path(config.CUSTOMER_CONFIG_DIR)
    path = directory / MANAGED_MCP_FILENAME
    if not path.exists():
        return []

    try:
        data = yaml.safe_load(path.read_text())
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        # Resilience contract: a missing/unreadable/corrupt file degrades to
        # "no managed MCPs" rather than raising. OSError covers Permission/
        # IsADirectory/TOCTOU-FileNotFound; UnicodeDecodeError covers binary
        # content (it is a ValueError subclass, not an OSError subclass).
        logger.warning(f"Failed to read/parse {MANAGED_MCP_FILENAME}: {exc}")
        return []

    raw = data.get("servers", []) if isinstance(data, dict) else []
    if not isinstance(raw, list):
        return []

    servers: List[ManagedMcpServer] = []
    for item in raw:
        try:
            servers.append(ManagedMcpServer(**item))
        except (ValidationError, TypeError) as exc:
            # v1 entries carry no secrets/tokens, so logging the raw entry is
            # safe. Revisit if a credential-bearing field is ever added.
            logger.warning(f"Skipping invalid managed MCP entry {item!r}: {exc}")

    if client:
        # `not s.clients` covers both None (no targeting) and [] — both mean
        # "applies to all clients" by design.
        servers = [s for s in servers if not s.clients or client in s.clients]
    return servers
