# EPMCDME-14305 — Publish the OAuth issuer in the managed MCP catalog — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a managed MCP catalog entry publish its IdP issuer so Claude Desktop uses issuer discovery instead of fabricating an issuer from `tokenUrl`'s origin.

**Architecture:** One additive optional field on `ManagedMcpOAuthConfig`. FastAPI serializes `response_model` by alias, so the pydantic model *is* the wire contract and the passthrough router `src/codemie/rest_api/routers/mcp_managed.py` is untouched.

**Tech Stack:** Python, pydantic v2, FastAPI, pytest + pytest-asyncio, `httpx.AsyncClient(ASGITransport(app))`.

**Spec:** `docs/superpowers/tasks/2026-08-20-epmcdme-14305-managed-mcp-oauth-issuer/spec.md`

## Global Constraints

- `authorization_server: Optional[List[str]] = Field(default=None, alias="authorizationServer")` — list, not scalar; never required.
- `authorization_url` / `token_url` stay **required**. Do not derive the issuer from `tokenUrl` by any string surgery.
- Do not set `populate_by_name`; camelCase stays the only accepted input spelling.
- Do not change `extra="ignore"` behaviour or add unknown-key detection — only reword its comment.
- Do not touch `mcp_managed.py`, the deprecated `auth` scalar, the loader's exception tuples, or unrelated tests. No client secret, feature flag, or ConfigMap edit from this repo.
- Line positions below were re-verified against the worktree at `6e211bcb6`.
- Commit per task using the repository's existing convention.

---

### Task 1: Serve `authorizationServer` from the model

**Files:**
- Modify: `src/codemie/configs/managed_mcp_config.py:76`, `:78-80`
- Test: `tests/codemie/rest_api/routers/test_mcp_managed.py`, `tests/codemie/configs/test_managed_mcp_config.py`

**Interfaces:**
- Produces: `ManagedMcpOAuthConfig.authorization_server: Optional[List[str]]`, alias `authorizationServer`; serialized key `"authorizationServer"` inside each entry's `oauth` object (`null` when unset).

**Test-first: yes** — the round-trip test below fails before the field exists: `extra="ignore"` drops the key at validation, so `response.json()[0]["oauth"]` has no `authorizationServer` (KeyError). This is the reported defect reproduced in-suite.

- [ ] **Step 1: Write the failing round-trip test** in `tests/codemie/rest_api/routers/test_mcp_managed.py`. It must **not** patch `load_managed_mcp_servers` — that patch is what hid the defect. Add `from codemie.configs import managed_mcp_config as mcp_config` to the imports.

```python
@pytest.mark.asyncio
async def test_authorization_server_survives_yaml_to_http(tmp_path, monkeypatch):
    # Closes the YAML -> HTTP seam: the real loader runs, reading
    # config.CUSTOMER_CONFIG_DIR at call time (managed_mcp_config.py:121).
    (tmp_path / "managed-mcp-servers.yaml").write_text(
        """
servers:
  - name: onehub_core
    transport: http
    url: https://mcp.example.com/mcp/onehub_core
    oauth:
      clientId: codemie-mcp-proxy
      scope: openid profile email
      callbackPort: 3118
      authorizationServer: ["https://auth.example.com/realms/codemie"]
      authorizationUrl: https://auth.example.com/realms/codemie/protocol/openid-connect/auth
      tokenUrl: https://auth.example.com/realms/codemie/protocol/openid-connect/token
"""
    )
    monkeypatch.setattr(mcp_config.config, "CUSTOMER_CONFIG_DIR", tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as ac:
        response = await ac.get(
            "/v1/mcp/managed-servers?client=claude-desktop", headers={"Authorization": "Bearer t"}
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()[0]["oauth"]["authorizationServer"] == ["https://auth.example.com/realms/codemie"]
```

- [ ] **Step 2: Write the two failing loader tests** in `tests/codemie/configs/test_managed_mcp_config.py`, using the module's `_write(tmp_path, ...)` helper (`:57-59`):
  - `test_loads_oauth_authorization_server_list` — an entry whose `oauth` carries `authorizationServer: ["https://auth.example.com/realms/codemie"]` loads and `servers[0].oauth.authorization_server == ["https://auth.example.com/realms/codemie"]`.
  - `test_skips_entry_with_scalar_authorization_server` — same entry but `authorizationServer: https://auth.example.com/realms/codemie` (scalar). Mirror `test_snake_case_oauth_key_is_rejected` (`:249-275`) exactly: a sibling `good` entry, `patch("codemie.configs.managed_mcp_config.logger")`, assert `[s.name for s in servers] == ["good"]` and `mock_logger.warning.assert_called_once()`. This pins acceptance criterion 4 and needs no production code.

- [ ] **Step 3: Run the three tests and confirm they fail**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_mcp_managed.py::test_authorization_server_survives_yaml_to_http tests/codemie/configs/test_managed_mcp_config.py -k "authorization_server" -v`
Expected: FAIL — key absent from the JSON body; `authorization_server` attribute missing; the scalar entry is *not* skipped (the key is ignored, so the entry loads).

- [ ] **Step 4: Add the field** to `ManagedMcpOAuthConfig`, immediately after `token_url` (`managed_mcp_config.py:76`), following the sibling `Optional[...] = Field(default=None, alias=...)` pattern at `:99-101`:

```python
    # Optional: the IdP issuer(s). Published so a client uses issuer discovery
    # instead of fabricating metadata from tokenUrl's origin (RFC 9207). A list,
    # matching the shape the consuming clients expect; a scalar is invalid.
    authorization_server: Optional[List[str]] = Field(default=None, alias="authorizationServer")
```

- [ ] **Step 5: Reword the `extra="ignore"` comment** at `:78-80`. It currently reads as though an unknown ConfigMap key propagates to clients. Replacement (behaviour unchanged):

```python
    # Inherits this module's graceful-degradation contract rather than a
    # stricter one: an unknown key is dropped at validation -- silently, and
    # without reaching the response. The entry still loads, so a ConfigMap may
    # run ahead of a backend rollout, but a key is served only once it is a
    # declared field here.
```

- [ ] **Step 6: Add the seventh key** to the pinned body in `test_list_managed_servers_serializes_oauth_in_camel_case` (`test_mcp_managed.py:102-109`): `"authorizationServer": None` in the expected `oauth` dict. `test_list_managed_servers_returns_loaded_entries` (`:41-65`) needs **no** change — its entry has `oauth: None`, so no nested key appears; confirm this by running it.

- [ ] **Step 7: Run the touched test files and confirm they pass**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_mcp_managed.py tests/codemie/configs/test_managed_mcp_config.py -v`
Expected: PASS (the pre-existing `test_example_file_is_valid` still passes — the example YAML has no `authorizationServer` yet and the field is optional).

- [ ] **Step 8: Commit** the model and test changes.

---

### Task 2: Document `authorizationServer` in the example catalog

The example YAML is the only in-repo expression of the schema and the sole mitigation for the scalar-form footgun (a scalar drops the whole server under the fail-soft contract), so this edit is load-bearing.

**Files:**
- Modify: `config/customer/managed-mcp-servers.example.yaml:19-25` (schema comment block), `:33-39` (example `oauth` block)
- Test: `tests/codemie/configs/test_managed_mcp_config.py:289-320`

**Test-first: yes** — `test_example_file_is_valid` gains an assertion that the documented `oauth` block carries `authorizationServer` as a non-empty list of `https://` strings; it fails while the example file lacks the key (`oauth.authorization_server is None`).

- [ ] **Step 1: Extend `test_example_file_is_valid`** — add to the assertion group at `:310-315`:

```python
    assert isinstance(oauth.authorization_server, list) and oauth.authorization_server
    assert all(u.startswith("https://") for u in oauth.authorization_server)
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `poetry run pytest tests/codemie/configs/test_managed_mcp_config.py::test_example_file_is_valid -v`
Expected: FAIL — `authorization_server` is `None`.

- [ ] **Step 3: Document the key in the schema comment block**, inserting after the `callbackPort` line (`:22`) so it precedes `authorizationUrl`, mirroring the deployment snippet:

```yaml
#     authorizationServer: optional -- YAML LIST of IdP issuer URLs (for Keycloak,
#                       the realm URL, e.g. https://auth.example.com/realms/codemie).
#                       Publish it whenever the issuer differs from the origin of
#                       tokenUrl. A bare scalar value is INVALID and skips the whole
#                       entry with a warning.
```

- [ ] **Step 4: Correct the `authorizationUrl` description** at `:23-24`, which advertises it as carrying `kc_idp_hint`/`prompt`. Replace with: required, the full authorize URL, plus a note that a client using issuer discovery (see `authorizationServer`) obtains `authorization_endpoint` from the IdP's `/.well-known/openid-configuration` and therefore ignores query params such as `kc_idp_hint` and `prompt` — an accepted side effect. Leave the query string on the example entry's `authorizationUrl` (`:38`) unchanged; it still applies to clients in explicit mode.

- [ ] **Step 5: Add the key to the example entry's `oauth` block**, before `authorizationUrl` (`:38`):

```yaml
      authorizationServer: ["https://auth.example.com/realms/codemie"]
```

- [ ] **Step 6: Run the loader test file and confirm it passes**

Run: `poetry run pytest tests/codemie/configs/test_managed_mcp_config.py -v`
Expected: PASS — including the unchanged assertion at `:319-320` that the `.example.yaml` is still not picked up by the loader.

- [ ] **Step 7: Commit** the example YAML and its test.
