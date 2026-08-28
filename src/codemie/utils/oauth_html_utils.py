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

"""Shared OAuth HTML utilities for callback pages."""

import html
from urllib.parse import urlsplit

from codemie.configs import config

# postMessage type the frontend listens for (per-user tool OAuth completion). Distinct from the
# MCP flow's ``mcp_auth_callback`` because tool OAuth correlates by signed ``state``, not auth_config_id.
TOOL_OAUTH_CALLBACK_EVENT_TYPE = "tool_oauth_callback"


def tool_oauth_callback_target_origin() -> str:
    """Origin the callback page may postMessage the result to (the frontend/opener window).

    Derived from ``FRONTEND_URL`` (same source the MCP callback uses). Returns ``""`` when it is not
    a valid absolute URL, in which case the page renders without notifying the opener.
    """
    parsed = urlsplit(config.FRONTEND_URL)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def html_success_page(message: str) -> str:
    """Generate OAuth success callback HTML page.

    Args:
        message: Success message to display.

    Returns:
        Complete HTML page string.
    """
    escaped = html.escape(message)
    body = f"<h2>Authentication Complete</h2><p>{escaped}</p><p>You can close this window.</p>"
    return f"<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body>{body}</body></html>"


def html_error_page(message: str) -> str:
    """Generate OAuth error callback HTML page.

    Args:
        message: Error message to display.

    Returns:
        Complete HTML page string.
    """
    escaped = html.escape(message)
    body = (
        f"<h2>Authentication Failed</h2><p>{escaped}</p><p>You can close this window and return to the application.</p>"
    )
    return f"<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body>{body}</body></html>"


def build_tool_oauth_callback_script() -> str:
    """External JS served at the tool OAuth ``callback-page.js`` endpoint and referenced by the
    callback page. Mirrors the enterprise MCP callback-page script.

    Reads the flow result from ``data-*`` attributes (no value is interpolated into JS, so escaped
    attribute values cannot inject script), postMessages it to the opener window — correlated by the
    signed ``state`` and restricted to ``target_origin`` — then closes the popup. Served under the
    strict ``script-src 'self'`` CSP, so it must be an external file (no inline script).
    """
    return f"""(function () {{
  var el = document.querySelector('main[data-callback-result]');
  if (!el) return;
  var targetOrigin = el.dataset.targetOrigin;
  if (window.opener && targetOrigin) {{
    try {{
      window.opener.postMessage({{
        type: '{TOOL_OAUTH_CALLBACK_EVENT_TYPE}',
        status: el.dataset.callbackResult,
        state: el.dataset.state || null,
        username: el.dataset.username || undefined,
        error: el.dataset.error || undefined
      }}, targetOrigin);
    }} catch (e) {{ /* opener gone or origin mismatch — nothing to do */ }}
  }}
  try {{ window.close(); }} catch (e) {{ /* some browsers refuse to close */ }}
}})();""".strip()


def html_callback_page(
    *,
    success: bool,
    message: str,
    state: str,
    target_origin: str,
    script_src: str,
    username: str = "",
    error: str = "",
) -> str:
    """Callback page that hands ``{type, status, state, error?}`` to the opener, then closes.

    ``state`` correlates the message with the popup the frontend opened; ``target_origin`` restricts
    delivery to the frontend window. The postMessage logic lives in the external script at
    ``script_src`` (:func:`build_tool_oauth_callback_script`), served same-origin so the strict
    ``script-src 'self'`` CSP allows it.
    """
    outcome = "success" if success else "error"
    title = "Authentication Complete" if success else "Authentication Failed"
    attrs = " ".join(
        [
            f'data-callback-result="{html.escape(outcome, quote=True)}"',
            f'data-state="{html.escape(state or "", quote=True)}"',
            f'data-target-origin="{html.escape(target_origin or "", quote=True)}"',
            f'data-username="{html.escape(username or "", quote=True)}"',
            f'data-error="{html.escape(error or "", quote=True)}"',
        ]
    )
    body = (
        f"<main {attrs}>"
        f"<h2>{html.escape(title)}</h2>"
        f"<p>{html.escape(message)}</p>"
        f"<p>You can close this window.</p>"
        f'<script src="{html.escape(script_src, quote=True)}"></script>'
        f"</main>"
    )
    return f"<!DOCTYPE html><html><head><meta charset='UTF-8'></head><body>{body}</body></html>"
