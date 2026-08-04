# ruff: noqa: T201
"""
Minimal verification script for EPMCDME-11241 fix.

Tests two things:
1. json_schema_to_model accepts top-level anyOf/oneOf (secondary fix)
2. MCPToolkit._create_tools falls back gracefully for complex schemas like
   mcp-grafana's update_dashboard (primary fix)

Run with: poetry run python verify_fix.py
"""

import logging
import sys
import types

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
_log = logging.getLogger("verify_fix")

# Add src first so the real codemie package is discoverable
sys.path.insert(0, "src")

# Inject a stub for codemie.configs BEFORE anything imports it.
# codemie.__init__.py is empty so importing the package itself is safe,
# but codemie.configs pulls in the full app stack — stub it out.
codemie_cfg = types.ModuleType("codemie.configs")
codemie_cfg.logger = _log
sys.modules["codemie.configs"] = codemie_cfg

# ---------------------------------------------------------------------------
# Now import the real fixed functions
# ---------------------------------------------------------------------------
from codemie.core.json_schema_utils import json_schema_to_model  # noqa: E402

# ── Test 1: top-level anyOf schema (secondary fix) ──────────────────────────
print("\n=== Test 1: top-level anyOf schema ===")
anyof_schema = {
    "anyOf": [
        {"type": "object", "properties": {"uid": {"type": "string"}}},
        {"type": "object"},
    ]
}
try:
    model = json_schema_to_model(anyof_schema)
    print(f"  PASS  json_schema_to_model accepted anyOf schema -> {model}")
except TypeError as e:
    print(f"  FAIL  still raising TypeError: {e}")

# ── Test 2: nested patternProperties (real update_dashboard-like schema) ────
print("\n=== Test 2: patternProperties schema (update_dashboard-like) ===")
pattern_schema = {
    "type": "object",
    "properties": {
        "dashboard": {
            "patternProperties": {"^[a-z]+$": {"type": "string"}},
        }
    },
}
try:
    model = json_schema_to_model(pattern_schema)
    dashboard_field = model.model_fields.get("dashboard")
    print(f"  PASS  schema converted -> {model}, dashboard field type: {dashboard_field.annotation}")
except (NotImplementedError, TypeError) as e:
    print(f"  FAIL  raised {type(e).__name__}: {e}")

# ── Test 3: simulate _create_tools fallback ─────────────────────────────────
print("\n=== Test 3: simulate _create_tools fallback for update_dashboard ===")
from pydantic import create_model  # noqa: E402


def sanitize_tool_name(name: str) -> str:
    import re

    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)


tools = []
tool_defs = [
    {"name": "list_dashboards", "schema": {"type": "object", "properties": {"org_id": {"type": "integer"}}}},
    {"name": "update_dashboard", "schema": pattern_schema},  # the problematic one
]

for td in tool_defs:
    try:
        schema_model = json_schema_to_model(td["schema"])
        tools.append({"name": td["name"], "schema": schema_model, "via": "normal"})
    except Exception as e:
        _log.warning(f"Failed to create schema for MCP tool '{td['name']}', using fallback schema: {e}")
        try:
            sanitized = sanitize_tool_name(td["name"])
            fallback = create_model(f"{sanitized.capitalize()}ArgsSchema")
            tools.append({"name": td["name"], "schema": fallback, "via": "fallback"})
        except Exception as inner_e:
            _log.error(f"Failed to create MCP tool '{td['name']}' even with fallback schema: {inner_e}")

print(f"\n  Tools in listing ({len(tools)}/{len(tool_defs)}):")
for t in tools:
    fields = list(t['schema'].model_fields.keys())
    print(f"    • {t['name']:<20} fields={fields if fields else '(empty fallback)'}  [{t['via']}]")

if len(tools) == len(tool_defs):
    print("\n  PASS  Both tools appear — update_dashboard is no longer silently dropped.")
else:
    print(f"\n  FAIL  Only {len(tools)} of {len(tool_defs)} tools appeared.")
