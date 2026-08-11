# EPMCDME-14072 — Per-Server OAuth Configuration in the Managed MCP Catalog: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each entry in the managed MCP catalog carry a complete, self-describing OAuth client configuration, so an agent client (Claude Desktop, Codex) can run an authorization-code flow with zero hardcoded values.

**Architecture:** One new nested pydantic model, `ManagedMcpOAuthConfig`, plus one optional `oauth` field on the existing `ManagedMcpServer` in `src/codemie/configs/managed_mcp_config.py`. That single class is dual-role — it is both the YAML catalog schema and the FastAPI `response_model` — so camelCase `Field(alias=...)` on the nested model makes the catalog file and the wire format byte-identical. The router needs no edit at all: `response_model=List[ManagedMcpServer]` inherits the new field and FastAPI's `response_model_by_alias` defaults to `True`.

**Tech Stack:** Python 3, pydantic v2, FastAPI, PyYAML, pytest + pytest-asyncio + httpx `ASGITransport`, Ruff, Poetry.

## Global Constraints

Copied verbatim in substance from the approved spec. These four design decisions are settled — **do not relitigate them during implementation.**

- **Aliasing is scoped to the nested OAuth model only.** `ManagedMcpServer` itself is left untouched — its fields are all single words, so casing is moot. Do NOT add a package-wide `alias_generator=to_camel`, and do NOT name Python fields `clientId` / `callbackPort` (violates PEP 8 and the repo's snake_case standard).
- **camelCase is the single canonical form — `populate_by_name` is deliberately NOT set.** The alias is the only accepted input spelling. A snake_case `client_id` in the catalog is not a second accepted form: it fails validation, and the entry is skipped and logged like any other malformed entry.
- **The nested block keeps `extra="ignore"`.** Do NOT use `extra="forbid"` — graceful degradation is this module's ethos, and a ConfigMap must be able to gain a new OAuth field ahead of a backend rollout.
- **The scalar `auth` field is retained and marked deprecated.** It keeps working unchanged. Do NOT derive `auth` from the presence of `oauth` — that would silently override what an operator wrote.
- **`src/codemie/rest_api/routers/mcp_managed.py` gets NO edit.** If you find yourself editing it, stop — something else is wrong.
- **The catalog stays secret-free.** Only public-client parameters (client id, scope, loopback callback, IdP authorize/token URLs). No client secret is stored, transmitted, or logged. Token exchange stays client-side.
- **The caveat comment at `src/codemie/configs/managed_mcp_config.py:91-92`** — *"v1 entries carry no secrets/tokens, so logging the raw entry is safe. Revisit if a credential-bearing field is ever added."* — **must stay in place, unchanged.** It was consciously re-read for this ticket and still holds.
- **The loader must never raise.** Missing / unreadable / corrupt file → `[]`. A malformed entry → skipped and logged, every sibling entry still loads.
- **`callbackPort` bounds are `1..65535`** (`Field(ge=1, le=65535)`). **`callbackHost` defaults to `"localhost"`.**
- **Ruff:** line-length 120, `quote-style = "preserve"`. `tests/*` has `E501` ignored. Run `make ruff` before every commit.
- **Commit message format:** `EPMCDME-14072: Capital description`. Never conventional-commit prefixes (`feat:`, `fix:`).
- **Branch:** `EPMCDME-14072_managed-mcp-oauth`.
- **No new files are created** — therefore `make license-check` is unaffected. No new dependency, no migration, no service or repository layer.

---

## File Structure

Four files change. No file is created.

| File | Responsibility | Change |
|---|---|---|
| `src/codemie/configs/managed_mcp_config.py` (99 lines) | Holds both the pydantic catalog model and the resilient YAML loader. This module is the entire feature. | Add `ManagedMcpOAuthConfig`; add `oauth: Optional[ManagedMcpOAuthConfig] = None` to `ManagedMcpServer`; mark `auth` deprecated. `load_managed_mcp_servers` is **not** touched — the nested block rides the existing per-entry `try/except (ValidationError, TypeError)`. |
| `src/codemie/rest_api/routers/mcp_managed.py` (32 lines) | One-line delegation endpoint. | **None.** |
| `config/customer/managed-mcp-servers.example.yaml` (18 lines) | The only in-repo expression of the catalog schema. Documentation only — the loader reads `managed-mcp-servers.yaml`, never `.example.yaml`. | Extend the schema comment header with the `oauth` block; add a sample `oauth` block to the example entry. |
| `tests/codemie/configs/test_managed_mcp_config.py` (125 lines, 11 tests) | Loader contract: resilience, per-entry skip, client filtering, example-file validity. | Add `OAUTH_YAML` fixture constant and 7 loader cases; strengthen `test_example_file_is_valid`. |
| `tests/codemie/rest_api/routers/test_mcp_managed.py` (77 lines, 2 tests) | Endpoint wire contract. | **Repair required** at lines 55-64 (exact-dict assertion breaks the moment an `oauth` key appears) + one new wire-casing test. |

**Why the model file is not split:** at ~125 lines after this change it stays comfortably readable, and the repo's established convention (documented in the technical analysis) is that a config model lives in the same module as its loader, not under `src/codemie/rest_api/models/`.

## Task Ordering Rationale

Adding the `oauth` field necessarily breaks `tests/codemie/rest_api/routers/test_mcp_managed.py:55-64`, because the serialized payload gains an `"oauth": null` key. The repair therefore **must** ship in the same task as the model change — Task 1 — or the suite is left red at a commit boundary. Task 2 then adds the wire-casing pin, and Task 3 the operator-facing documentation.

---

### Task 1: Nested OAuth config model and the `oauth` field

**Files:**
- Modify: `src/codemie/configs/managed_mcp_config.py:31` (import), `:39-49` (`ManagedMcpServer`)
- Test: `tests/codemie/configs/test_managed_mcp_config.py` (add `OAUTH_YAML` after line 33; add 7 tests)
- Test: `tests/codemie/rest_api/routers/test_mcp_managed.py:55-64` (mandatory repair)

**Test-first: yes — 7 new loader tests in `tests/codemie/configs/test_managed_mcp_config.py` fail at import time with `ImportError: cannot import name 'ManagedMcpOAuthConfig' from 'codemie.configs.managed_mcp_config'`, and the repaired exact-dict assertion in `test_list_managed_servers_returns_loaded_entries` fails because the response payload has no `"oauth"` key.**

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `ManagedMcpOAuthConfig(BaseModel)` — importable from `codemie.configs.managed_mcp_config`. Python attribute names (snake_case): `client_id: str`, `scope: str`, `callback_host: str`, `callback_port: int`, `authorization_url: str`, `token_url: str`. Input/output keys (camelCase, the only accepted spelling): `clientId`, `scope`, `callbackHost`, `callbackPort`, `authorizationUrl`, `tokenUrl`.
  - `ManagedMcpServer.oauth: Optional[ManagedMcpOAuthConfig] = None`.
  - Construction note for later tasks: because `populate_by_name` is unset, you cannot write `ManagedMcpOAuthConfig(client_id=...)`. Pass a camelCase dict — `ManagedMcpServer(name=..., transport=..., url=..., oauth={"clientId": ..., ...})` — which is also how YAML data actually arrives.

- [ ] **Step 1: Write the failing loader tests**

Add the `unittest.mock.patch` import at the top of `tests/codemie/configs/test_managed_mcp_config.py` (below `from pathlib import Path`):

```python
from unittest.mock import patch
```

Extend the existing model import (currently lines 17-20) to:

```python
from codemie.configs.managed_mcp_config import (
    ManagedMcpOAuthConfig,
    ManagedMcpServer,
    load_managed_mcp_servers,
)
```

Add this constant immediately after `SAMPLE_YAML` (after line 33):

```python
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
```

Append these 7 tests to the end of the file (after `test_non_dict_entry_is_skipped`, before `test_example_file_is_valid`):

```python
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
    assert oauth.authorization_url == "https://auth.example.com/realms/codemie/protocol/openid-connect/auth?kc_idp_hint=epam-oidc"
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
```

Note on `patch("codemie.configs.managed_mcp_config.logger")`: do **not** reach for pytest's `caplog` here. The `codemie` logger is configured with `propagate: False` (`src/codemie/configs/logger.py:78`), so `caplog` would not capture these warnings and the assertion would silently never fire.

- [ ] **Step 2: Repair the endpoint test that the new field will break**

In `tests/codemie/rest_api/routers/test_mcp_managed.py`, add one line to the exact-dict assertion at lines 55-64 so the whole assertion reads:

```python
        assert response.json() == [
            {
                "name": "sample",
                "transport": "http",
                "url": "https://mcp.example.com/mcp/sample",
                "auth": "oauth",
                "description": None,
                "clients": None,
                "oauth": None,
            }
        ]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run:
```bash
poetry run pytest tests/codemie/configs/test_managed_mcp_config.py tests/codemie/rest_api/routers/test_mcp_managed.py -v
```

Expected: collection of `tests/codemie/configs/test_managed_mcp_config.py` errors with `ImportError: cannot import name 'ManagedMcpOAuthConfig' from 'codemie.configs.managed_mcp_config'`, and `test_list_managed_servers_returns_loaded_entries` FAILS on the dict comparison — the actual payload has no `"oauth"` key.

- [ ] **Step 4: Write the implementation**

In `src/codemie/configs/managed_mcp_config.py`, change the pydantic import on line 31 to include `Field`:

```python
from pydantic import BaseModel, ConfigDict, Field, ValidationError
```

Then replace the whole `ManagedMcpServer` class block (lines 39-49) with:

```python
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
    callback_host: str = Field(default="localhost", alias="callbackHost")
    callback_port: int = Field(alias="callbackPort", ge=1, le=65535)
    authorization_url: str = Field(alias="authorizationUrl")
    token_url: str = Field(alias="tokenUrl")

    # Inherits this module's graceful-degradation contract rather than a
    # stricter one: an unknown key is ignored, so a ConfigMap may gain a new
    # OAuth field ahead of a backend rollout without breaking the entry.
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
```

Do **not** touch `load_managed_mcp_servers`. The nested block validates inside the existing per-entry `try/except (ValidationError, TypeError)` at lines 88-93, which is exactly what makes skip-and-log work for free.

- [ ] **Step 5: Run the tests to verify they pass**

Run:
```bash
poetry run pytest tests/codemie/configs/test_managed_mcp_config.py tests/codemie/rest_api/routers/test_mcp_managed.py -v
```
Expected: PASS — 18 passed in the config file, 2 passed in the router file.

- [ ] **Step 6: Confirm the security caveat comment survived**

Run:
```bash
sed -n '88,96p' src/codemie/configs/managed_mcp_config.py
```
Expected: the comment *"v1 entries carry no secrets/tokens, so logging the raw entry is safe. Revisit if a credential-bearing field is ever added."* is still present and unmodified. It was consciously re-evaluated for this ticket — the OAuth block is public-client config with no secret, so the premise holds and the comment stays.

- [ ] **Step 7: Lint and commit**

```bash
make ruff
git add src/codemie/configs/managed_mcp_config.py tests/codemie/configs/test_managed_mcp_config.py tests/codemie/rest_api/routers/test_mcp_managed.py
git commit -m "EPMCDME-14072: Add nested OAuth config to managed MCP catalog model"
```

---

### Task 2: Pin the camelCase wire format at the endpoint boundary

The codebase has **zero** existing coverage of wire-format casing — every field today is a single lowercase word, so alias serialization is entirely unproven here. This task adds the assertion that pins the "camelCase is the single canonical form" decision, and doubles as the seam test that `.ai-run/guides/testing/testing-patterns.md` requires for the `callbackHost` default (observed at the outer boundary, not just on the model).

**Files:**
- Test: `tests/codemie/rest_api/routers/test_mcp_managed.py` (append one test)
- Modify: none — `src/codemie/rest_api/routers/mcp_managed.py` needs no edit; FastAPI's `response_model_by_alias` defaults to `True`.

**Test-first: yes — `test_list_managed_servers_serializes_oauth_in_camel_case` asserts an `"oauth"` object keyed `clientId` / `callbackHost` / `callbackPort` / `authorizationUrl` / `tokenUrl` in the HTTP payload; against the pre-Task-1 model the key is absent entirely, and Step 3 below re-proves it can fail by mutating the alias.**

**Interfaces:**
- Consumes: `ManagedMcpServer` and its `oauth` field from Task 1. Constructed with a camelCase dict, since `populate_by_name` is unset.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the test**

Append to `tests/codemie/rest_api/routers/test_mcp_managed.py`:

```python
@pytest.mark.asyncio
async def test_list_managed_servers_serializes_oauth_in_camel_case():
    # The catalog YAML and the HTTP response use identical keys, so this pins
    # both the wire format and the accepted input spelling. `callbackHost` is
    # omitted on purpose -- the seam test for its "localhost" default.
    entries = [
        ManagedMcpServer(
            name="onehub_core",
            transport="http",
            url="https://codemie.lab.epam.com/mcp/mcp-proxy/onehub_core",
            auth="oauth",
            oauth={
                "clientId": "codemie-mcp-proxy",
                "scope": "openid profile email",
                "callbackPort": 3118,
                "authorizationUrl": "https://auth.example.com/realms/codemie/protocol/openid-connect/auth?kc_idp_hint=epam-oidc&prompt=login",
                "tokenUrl": "https://auth.example.com/realms/codemie/protocol/openid-connect/token",
            },
        )
    ]
    with patch("codemie.rest_api.routers.mcp_managed.load_managed_mcp_servers", return_value=entries):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.get("/v1/mcp/managed-servers", headers={"Authorization": "Bearer testtoken"})

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == [
        {
            "name": "onehub_core",
            "transport": "http",
            "url": "https://codemie.lab.epam.com/mcp/mcp-proxy/onehub_core",
            "auth": "oauth",
            "description": None,
            "clients": None,
            "oauth": {
                "clientId": "codemie-mcp-proxy",
                "scope": "openid profile email",
                "callbackHost": "localhost",
                "callbackPort": 3118,
                "authorizationUrl": "https://auth.example.com/realms/codemie/protocol/openid-connect/auth?kc_idp_hint=epam-oidc&prompt=login",
                "tokenUrl": "https://auth.example.com/realms/codemie/protocol/openid-connect/token",
            },
        }
    ]
```

- [ ] **Step 2: Run the test**

Run:
```bash
poetry run pytest tests/codemie/rest_api/routers/test_mcp_managed.py -v
```
Expected: PASS (3 passed). Task 1 already made the behavior correct — Step 3 proves the assertion is real rather than vacuous.

- [ ] **Step 3: Mutation check — prove the test actually pins the alias**

In `src/codemie/configs/managed_mcp_config.py`, temporarily change one line:

```python
    client_id: str = Field(alias="client_id")
```

Run:
```bash
poetry run pytest tests/codemie/rest_api/routers/test_mcp_managed.py::test_list_managed_servers_serializes_oauth_in_camel_case -v
```
Expected: FAIL — the payload carries `"client_id"` instead of `"clientId"`.

Then revert the line to `Field(alias="clientId")` and re-run:
```bash
poetry run pytest tests/codemie/rest_api/routers/test_mcp_managed.py -v
```
Expected: PASS (3 passed). Confirm with `git diff src/codemie/configs/managed_mcp_config.py` that the mutation is fully reverted — the file must show **no** changes.

- [ ] **Step 4: Lint and commit**

```bash
make ruff
git add tests/codemie/rest_api/routers/test_mcp_managed.py
git commit -m "EPMCDME-14072: Pin camelCase OAuth wire format on managed MCP endpoint"
```

---

### Task 3: Document the `oauth` block in the example catalog

`config/customer/managed-mcp-servers.example.yaml` is the only in-repo expression of the schema — the authoritative catalog lives out of repo, in the `codemie-customer-config` ConfigMap. `test_example_file_is_valid` hard-binds this file to the model, so the example and the model must land in the same branch.

**Files:**
- Modify: `config/customer/managed-mcp-servers.example.yaml:5-18`
- Test: `tests/codemie/configs/test_managed_mcp_config.py:105-124` (strengthen `test_example_file_is_valid`)

**Test-first: yes — the strengthened `test_example_file_is_valid` fails with `AssertionError: example file must document an oauth block` because the current example entry parses to `oauth is None`.**

**Interfaces:**
- Consumes: `ManagedMcpOAuthConfig` attribute names (`client_id`, `scope`, `callback_port`, `authorization_url`, `token_url`) from Task 1.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the failing assertion**

Replace the body of `test_example_file_is_valid` (lines 105-124) with:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
poetry run pytest tests/codemie/configs/test_managed_mcp_config.py::test_example_file_is_valid -v
```
Expected: FAIL with `AssertionError: example file must document an oauth block`.

- [ ] **Step 3: Update the example catalog**

Replace the entire contents of `config/customer/managed-mcp-servers.example.yaml` with:

```yaml
# Example managed MCP catalog. Copy this content into the
# `managed-mcp-servers.yaml` key of the `codemie-customer-config` ConfigMap.
# This .example.yaml file is documentation only and is never read at runtime.
#
# Schema (remote-only in v1):
#   name:        unique server name [a-zA-Z0-9_-]
#   transport:   http | sse
#   url:         server URL
#   auth:        oauth | none        (default: none) -- DEPRECATED, use `oauth`
#   description: optional human label
#   clients:     optional list; omit = all clients (e.g. claude-desktop, codex)
#   oauth:       optional public OAuth client configuration. Keys are camelCase
#                and camelCase is the ONLY accepted spelling -- a snake_case key
#                such as `client_id` makes the whole entry invalid, and the
#                entry is skipped with a warning while the rest of the catalog
#                still loads. NEVER put a client secret here: this catalog is
#                served to agent clients as-is and the token exchange runs
#                entirely client-side.
#     clientId:         required -- public OAuth client registered in the IdP
#     scope:            required -- space-delimited scopes
#     callbackHost:     optional -- loopback redirect host (default: localhost)
#     callbackPort:     required -- loopback redirect port, 1..65535
#     authorizationUrl: required -- full authorize URL, including query params
#                       such as kc_idp_hint and prompt
#     tokenUrl:         required -- token endpoint
servers:
  - name: example
    transport: http
    url: https://mcp.example.com/mcp/example
    auth: oauth
    description: Example MCP server
    clients: [claude-desktop]
    oauth:
      clientId: codemie-mcp-proxy
      scope: openid profile email
      callbackHost: localhost
      callbackPort: 3118
      authorizationUrl: https://auth.example.com/realms/codemie/protocol/openid-connect/auth?kc_idp_hint=epam-oidc&prompt=login
      tokenUrl: https://auth.example.com/realms/codemie/protocol/openid-connect/token
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
poetry run pytest tests/codemie/configs/test_managed_mcp_config.py -v
```
Expected: PASS (18 passed).

- [ ] **Step 5: Run the full narrow scope for this change**

Run:
```bash
poetry run pytest tests/codemie/configs/test_managed_mcp_config.py tests/codemie/rest_api/routers/test_mcp_managed.py -v
```
Expected: PASS (21 passed).

- [ ] **Step 6: Lint and commit**

```bash
make ruff
git add config/customer/managed-mcp-servers.example.yaml tests/codemie/configs/test_managed_mcp_config.py
git commit -m "EPMCDME-14072: Document OAuth block in managed MCP example catalog"
```

---

## Final Verification

- [ ] Run the full gate: `make verify` (Ruff, license headers, gitleaks, tests). Gitleaks needs Docker; if Docker is unavailable, report the skip explicitly rather than claiming the gate passed.
- [ ] Confirm `git diff --stat main` touches exactly four files: `src/codemie/configs/managed_mcp_config.py`, `config/customer/managed-mcp-servers.example.yaml`, `tests/codemie/configs/test_managed_mcp_config.py`, `tests/codemie/rest_api/routers/test_mcp_managed.py`. `src/codemie/rest_api/routers/mcp_managed.py` must be untouched.

## Spec Coverage Map

| Spec testing requirement | Covered by |
|---|---|
| 1. A valid `oauth` block round-trips through the loader | Task 1 — `test_loads_oauth_block` |
| 2. Wire casing: `clientId`, `scope`, `callbackHost`, `callbackPort`, `authorizationUrl`, `tokenUrl` | Task 2 — `test_list_managed_servers_serializes_oauth_in_camel_case` (+ mutation check) |
| 3. Entry with no `oauth` key still loads; `oauth` null in the response | Task 1 — `test_entry_without_oauth_block_still_loads` and the repaired `test_list_managed_servers_returns_loaded_entries` |
| 4. Malformed `oauth` skipped and logged, siblings still load — missing required key, and out-of-range `callbackPort` | Task 1 — `test_skips_entry_with_missing_required_oauth_key`, `test_skips_entry_with_out_of_range_callback_port` |
| 5. `callbackHost` defaults to `localhost` | Task 1 — `test_callback_host_defaults_to_localhost` (model), Task 2 — response-boundary seam test |
| 6. snake_case `client_id` fails validation | Task 1 — `test_snake_case_oauth_key_is_rejected` |
| 7. `?client=` filtering unaffected by `oauth` | Task 1 — `test_filters_by_client_is_unaffected_by_oauth` |
| 8. Updated example YAML still validates against the model | Task 3 — strengthened `test_example_file_is_valid` |

## Known Residual Risk (carried from the spec, not actionable in this plan)

The authoritative catalog lives outside this repository, in the `codemie-customer-config` ConfigMap. No test can detect drift between a deployed catalog and this model; the example YAML is the only in-repo expression of the schema, and it is documentation, never read at runtime. Rollout is ConfigMap-driven — the code change is additive and inert until a deployment adds an `oauth` block, and agent clients must tolerate a missing/null `oauth` key during the window where code is deployed but ConfigMaps are not.
