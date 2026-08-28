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

"""Pre-stream, MCP-style aggregate connect gate for per-user OAuth tools.

Mirrors how MCP handles auth: at tool-assembly time (before the streaming response starts — the only
point a gate can surface to the UI), check every OAuth-backed tool the assistant uses and, if the
acting user has not connected one or more of them, raise a single exception listing them all. The
user connects the ones they need in one step and resends once, rather than being prompted for one
provider at a time.
"""

import logging

logger = logging.getLogger(__name__)


def _oauth_gate_targets(tools) -> list[tuple[str, str, str]]:
    """Return (setting_id, error_code, integration_name) for each OAuth-backed tool in the toolset."""
    from codemie_tools.core.project_management.confluence.models import ConfluenceConfig
    from codemie_tools.core.project_management.jira.models import JiraConfig
    from codemie_tools.core.vcs.gitlab.models import GitlabConfig

    meta_by_config = {
        GitlabConfig: ("gitlab_auth_required", "GitLab integration"),
        JiraConfig: ("jira_auth_required", "Jira integration"),
        ConfluenceConfig: ("confluence_auth_required", "Confluence integration"),
    }
    targets: list[tuple[str, str, str]] = []
    for tool in tools:
        cfg = getattr(tool, "config", None)
        if cfg is None or getattr(cfg, "auth_type", None) != "oauth":
            continue
        setting_id = getattr(cfg, "integration_id", "")
        meta = meta_by_config.get(type(cfg))
        if setting_id and meta:
            error_code, default_name = meta
            targets.append((setting_id, error_code, default_name))
    return targets


def enforce_oauth_connected(tools, user_id: str) -> None:
    """Raise a single aggregate connect gate for every OAuth-backed tool the acting user has not
    connected. No-op if there are no OAuth tools or everything is already connected.

    Must be called during agent build (before the streaming response begins).
    """
    if not user_id:
        return
    targets = _oauth_gate_targets(tools)
    if not targets:
        return

    from codemie.service.oauth.token_port import ToolOAuthTokenPort, map_tms_error_to_http

    providers: list[dict] = []
    seen: set[str] = set()
    for setting_id, error_code, integration_name in targets:
        if setting_id in seen:  # the same shared integration can back more than one tool
            continue
        seen.add(setting_id)
        try:
            connected = ToolOAuthTokenPort.has_connection(user_id=user_id, integration_id=setting_id)
        except Exception as exc:
            raise map_tms_error_to_http("Tool", exc)
        if not connected:
            providers.append({"error": error_code, "setting_id": setting_id, "integration_name": integration_name})

    if providers:
        from codemie.core.exceptions import OAuthConnectRequiredException

        logger.info(
            f"OAuth connect gate: user {user_id} must connect {[p['error'] for p in providers]} before running."
        )
        raise OAuthConnectRequiredException(providers)
