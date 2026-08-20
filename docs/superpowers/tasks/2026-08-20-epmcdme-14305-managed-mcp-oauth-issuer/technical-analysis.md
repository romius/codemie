# Technical Research

**Task**: mcp oauth catalog pydantic
**Generated**: 2026-08-20
**Research path**: codegraph

---

## 1. Original Context

EPMCDME-14305 — Managed MCP catalog cannot express an OAuth issuer, so every Claude Desktop connector fails RFC 9207 validation. Type: Bug. Priority: Blocker.

PROBLEM. `GET /v1/mcp/managed-servers?client=claude-desktop` serves a per-server `oauth` block containing `authorizationUrl` and `tokenUrl` but no issuer. Claude Desktop reads that key combination as "explicit" OAuth mode, performs no discovery, and fabricates the authorization-server metadata by deriving `issuer` from the ORIGIN of `tokenUrl` (path discarded). Keycloak's issuer is the realm URL (`https://auth.codemie.lab.epam.com/realms/codemie-prod`), not the host origin, so the fabricated issuer never matches the `iss` Keycloak returns on the callback, and Desktop aborts the flow with `Issuer mismatch in authorization response (RFC 9207)`. Keycloak advertises `authorization_response_iss_parameter_supported: true`, so `iss` is always present and the check is never skipped. 35 of 42 catalog entries are affected; no EPAM MCP server can be connected from Claude Desktop. The failure happens inside Claude Desktop before the authorization code is redeemed, so the proxy logs show nothing.

ROOT CAUSE. In `src/codemie/configs/managed_mcp_config.py`: `ManagedMcpOAuthConfig` (line 54) declares exactly six fields — `client_id`/`clientId` (69), `scope`, `callback_host`/`callbackHost`, `callback_port`/`callbackPort` (74), `authorization_url`/`authorizationUrl` (75), `token_url`/`tokenUrl` (76) — with `model_config = ConfigDict(extra="ignore")` (81). There is no field by which a catalog entry can publish the IdP issuer, so every entry the backend serves necessarily drives Claude Desktop into explicit mode. Because of `extra="ignore"`, a ConfigMap-only workaround is silently discarded at validation: the entry still validates, no warning is logged, and the key never reaches the wire. This was verified by running the verbatim models through a FastAPI TestClient (pydantic 2.12.5, fastapi 0.133.1) with `authorizationServer` present — `model_extra` was `None` and the key was absent from the response.

FIX SCOPE (4 surfaces, 3 in this repo):
1. `src/codemie/configs/managed_mcp_config.py` — add `authorization_server: Optional[List[str]] = Field(default=None, alias="authorizationServer")` to `ManagedMcpOAuthConfig`. Keep `authorization_url` and `token_url` REQUIRED (the consuming CLI's `isValidOAuthConfig` hard-requires them; entries missing either are dropped whole, and if every entry drops the CLI treats the catalog as a fetch failure). Reword the `extra="ignore"` comment, which currently implies unknown ConfigMap keys reach clients — they do not. Do NOT auto-derive the issuer from `tokenUrl` by string-truncating at `/protocol/openid-connect/`: that hardcodes a Keycloak URL shape into a deliberately vendor-neutral catalog.
2. `config/customer/managed-mcp-servers.example.yaml` — document `authorizationServer` in the schema comment block and the example entry; correct the existing lines that advertise `authorizationUrl` as carrying `kc_idp_hint`/`prompt` query params, since a client using issuer discovery ignores them.
3. Tests — `tests/codemie/configs/test_managed_mcp_config.py` and `tests/codemie/rest_api/routers/test_mcp_managed.py`. No existing test asserts the SERVED field set, which is exactly why a dropped key is invisible today; coverage must include the HTTP round-trip, not only model validation.
4. (Out of repo scope) ConfigMap update to `managed-mcp-servers.yaml` in `codemie-customer-config`.

ACCEPTANCE CRITERIA: (1) an entry carrying `authorizationServer` in the ConfigMap is returned by `GET /v1/mcp/managed-servers?client=claude-desktop` with `authorizationServer` present inside the `oauth` object of the JSON response; (2) an entry without it still loads and serves exactly as before; (3) `authorizationUrl`/`tokenUrl` remain required — an entry omitting either is still skipped and logged while the rest of the catalog loads; (4) malformed `authorizationServer` follows the module's existing contract — entry skipped with a warning, catalog still loads; (5) no client secret introduced; (6) end-to-end no `Issuer mismatch` and a `saved OAuth tokens` line in Claude Desktop's log; (7) `make ruff`, `make license-check`, `make test` pass.

RELATED PRIOR WORK: EPMCDME-14072 (commit `224fd8deb`) introduced this oauth block; its own spec/plan/technical-analysis/code-review artifacts under `docs/superpowers/tasks/2026-08-11-epmcdme-14072-managed-mcp-oauth-config/` never considered the issuer/discovery dimension. Full ticket text: `/home/taras_spashchenko/EPAM/EPM-CDME-bug-managed-mcp-oauth-issuer.md`.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/configs/managed_mcp_config.py` (153 lines) — the entire feature. Contains:
  - `MANAGED_MCP_FILENAME = "managed-mcp-servers.yaml"` (line 37).
  - `_use_default_if_none` (line 40) — `BeforeValidator` raising `PydanticUseDefault` so a blank YAML key (`callbackHost:` → `None`) falls back to the field default instead of invalidating the entry.
  - `ManagedMcpOAuthConfig` (line 54) — six fields, all camelCase-aliased: `client_id`/`clientId` (69), `scope` (70), `callback_host`/`callbackHost` with `default="localhost"` (71-73), `callback_port`/`callbackPort` with `ge=1, le=65535` (74), `authorization_url`/`authorizationUrl` (75), `token_url`/`tokenUrl` (76). `model_config = ConfigDict(extra="ignore")` (81). `populate_by_name` is deliberately NOT set, so the camelCase alias is the only accepted input spelling.
  - `ManagedMcpServer` (line 84) — `name`, `transport: Literal["http","sse"]`, `url`, `auth: Literal["oauth","none"] = "none"` (documented DEPRECATED), `description`, `clients`, `oauth: Optional[ManagedMcpOAuthConfig] = None`; also `extra="ignore"` (103).
  - `load_managed_mcp_servers(client, base_dir)` (line 106) — reads `Path(config.CUSTOMER_CONFIG_DIR)/managed-mcp-servers.yaml`; returns `[]` if absent; catches `(yaml.YAMLError, OSError, UnicodeDecodeError)` for whole-file failures (128); per entry catches `(ValidationError, TypeError)` and logs `logger.warning(f"Skipping invalid managed MCP entry {item!r}: {exc}")` (144-147); filters by `client` last (149-152).
- `src/codemie/rest_api/routers/mcp_managed.py` (32 lines) — `router = APIRouter(prefix="/v1/mcp", tags=["MCP"], dependencies=[Depends(authenticate)])` (24); `@router.get("/managed-servers", response_model=List[ManagedMcpServer])` → `list_managed_mcp_servers(client)` returns `load_managed_mcp_servers(client=client)` (27-32). A pure passthrough — no service, repository, or DB layer.
- `config/customer/managed-mcp-servers.example.yaml` (39 lines) — documentation only, never read at runtime (the loader reads `managed-mcp-servers.yaml`). Lines 5-25 are the schema comment block; lines 19-25 document the six oauth keys; line 23-24 state `authorizationUrl: required -- full authorize URL, including query params such as kc_idp_hint and prompt`. Lines 26-39 hold one example entry with a full `oauth` block.

### Architecture and Layers Affected

- **Config/model layer** — `src/codemie/configs/managed_mcp_config.py` (pydantic v2 models + resilient YAML loader). Sibling loaders in the same package (`mcp_commands_config.py`, `authorized_apps_config.py`) are fail-closed (raise `ValueError` at startup); this one is deliberately fail-soft.
- **API layer** — `src/codemie/rest_api/routers/mcp_managed.py`. The wire contract is the pydantic model itself: FastAPI serializes `response_model` by alias, which is why camelCase reaches the client with no router code. The prior ticket's `actual-complexity.json` records this explicitly ("router unchanged, response_model_by_alias defaults to True").
- **Deployment config layer** — `config/customer/` (`customer-config.yaml`, `managed-mcp-servers.example.yaml`). The live catalog is a key in the `codemie-customer-config` ConfigMap mounted at `CUSTOMER_CONFIG_DIR`, not in this repo.
- No database, no migration, no repository, no service class, and no enterprise module participate in this endpoint.

### Integration Points

- `codemie.configs.config.config.CUSTOMER_CONFIG_DIR` — the only runtime input besides the `client` query parameter.
- `codemie.configs.logger.logger` — the single observability surface for skipped entries.
- `codemie.rest_api.security.authentication.authenticate` — router-level dependency; every test overrides it via `app.dependency_overrides`.
- Downstream consumers (outside this repo): `@codemieai/code` CLI (`managed-mcp-remote.ts`) and Claude Desktop, which read the served `oauth` object. Not verifiable from this repo; the ticket documents them.

### Patterns and Conventions

- Pydantic v2 `BaseModel` + `Field(alias=...)` camelCase-only input; `ConfigDict(extra="ignore")` on both models.
- `Annotated[T, BeforeValidator(...)]` + `PydanticUseDefault` to keep a default defined once, on the field.
- Graceful degradation contract, documented in the module docstring (lines 15-23): missing/corrupt file → `[]`; invalid entry → skip + `logger.warning`, rest of the catalog still loads.
- Heavy explanatory comments recording *why* a choice was made (alias-only spelling, deprecated `auth`, exception tuple composition, safety of logging raw entries).
- Apache 2.0 license header on every `.py` file (enforced by `scripts/license_headers/check_license_headers.py`).

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/` exists (40 guides). Relevant: `development/configuration-patterns.md` ("use `src/codemie/configs/` and existing YAML config trees for runtime settings"; prefer central config over reading env vars in feature code), `quality-gates.md` (gate order and commands), `testing/testing-api-patterns.md`, `integration/mcp-integration.md`, `standards/code-quality.md`.
- No guide covers the managed MCP catalog specifically; conventions for it come from the module's own comments and the prior task's artifacts.

### Architectural Decisions

- `docs/superpowers/tasks/2026-08-11-epmcdme-14072-managed-mcp-oauth-config/` holds the prior run's `spec.md`, `plan.md`, `technical-analysis.md`, `qa-report.md`, two code-review JSONs and `actual-complexity.json`. That run scored **total 14, size S, band 10-14, routing `writing-plans`**, with 4 modified files (the same four in scope now), 0 new files, 325 insertions / 7 deletions. Technical Risk was bumped S→M by an authorization red flag (the endpoint publishes OAuth client parameters to external clients).
- In-code decisions: `auth` scalar kept and never derived from `oauth`; catalog is secret-free by design; alias-only spelling keeps YAML and HTTP keys identical; `extra="ignore"` chosen so a ConfigMap may run ahead of a backend rollout.
- **External reference read**: `/home/taras_spashchenko/EPAM/EPM-CDME-bug-managed-mcp-oauth-issuer.md` (322 lines) — the full ticket. Beyond Section 1 it adds: measured environment (Claude Desktop 2.1.234, CLI 0.14.0, profile `preview`); the exact `main.log` error text; the three discovery candidates tried in issuer mode (only `…/realms/codemie-prod/.well-known/openid-configuration` returns 200); a hand-patched end-to-end confirmation showing `saved OAuth tokens`; the accepted side effect that `kc_idp_hint=epam-oidc` and `prompt=login` are lost under issuer discovery because Keycloak's discovered `authorization_endpoint` carries no query string; the Claude Desktop connector key whitelist; the rollout order (backend → deploy → ConfigMap → verify, no CLI release); and an out-of-scope note that a *second, independent* failure (`-31004 server-fault`, proxy mirroring MCP protocol version `2026-07-28`) will appear afterwards and must not be read as this fix failing.

### Derived Conventions

- New optional fields are additive, camelCase-aliased, `Optional[...] = Field(default=None, alias=...)`, and documented in both the example YAML schema comment block and the example entry.
- Any behaviour worth explaining is explained in an inline comment next to the code, not only in the commit message.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/configs/test_managed_mcp_config.py` (321 lines, 21 tests, pytest function style with `tmp_path`): missing file, parse, malformed-entry skip, client filtering, corrupt YAML, typed models, directory-at-path, non-dict root, non-list `servers`, non-dict entry; oauth-specific — `test_loads_oauth_block` (asserts all six fields), `test_entry_without_oauth_block_still_loads`, `test_callback_host_defaults_to_localhost`, `test_blank_callback_host_falls_back_to_default`, `test_skips_entry_with_missing_required_oauth_key`, `test_skips_entry_with_out_of_range_callback_port`, `test_snake_case_oauth_key_is_rejected` (the last three assert `mock_logger.warning.assert_called_once()`), `test_filters_by_client_is_unaffected_by_oauth`, and `test_example_file_is_valid` (289-320) which loads `config/customer/managed-mcp-servers.example.yaml`, validates every entry through `ManagedMcpServer`, asserts oauth fields are present/well-formed, and asserts the `.example.yaml` is NOT picked up by the loader.
- `tests/codemie/rest_api/routers/test_mcp_managed.py` (124 lines, 3 tests, `pytest.mark.asyncio` + `httpx.AsyncClient(ASGITransport(app=app))`): `test_list_managed_servers_returns_loaded_entries` (41-65) asserts the whole JSON list with `==`, including `"oauth": None`; `test_list_managed_servers_serializes_oauth_in_camel_case` (69-111) asserts the whole JSON list with `==`, including an exact six-key `oauth` object and the `callbackHost: "localhost"` default seam; `test_list_managed_servers_without_client_param` (115-123) asserts `mock_load.assert_called_once_with(client=None)`.

### Testing Framework and Patterns

- pytest `^8.3.1`, pytest-asyncio `^0.23.7`, pytest-mock, pytest-env, pytest-cov; `httpx ^0.28.1` for ASGI transport. Command: `make test` → `poetry run pytest tests/`.
- Patterns: `tmp_path` + a local `_write()` helper writing `managed-mcp-servers.yaml`; module-level YAML string constants (`SAMPLE_YAML`, `OAUTH_YAML`); `unittest.mock.patch("codemie.configs.managed_mcp_config.logger")` to assert warnings; router tests patch `codemie.rest_api.routers.mcp_managed.load_managed_mcp_servers` and override `authenticate` through an autouse `app.dependency_overrides` fixture.

### Coverage Gaps

- **No test drives YAML → HTTP.** Every router test injects already-constructed `ManagedMcpServer` objects with `load_managed_mcp_servers` patched out; every loader test stops at the model. Nothing exercises a ConfigMap key surviving validation and reaching the JSON body — which is the seam the reported defect lives in. (Note: the ticket's phrasing "no existing test asserts the served field set" is imprecise — `test_list_managed_servers_serializes_oauth_in_camel_case` asserts the full served `oauth` dict by equality; what is untested is the *origin* of those keys.)
- No test feeds an undeclared/extra key into `ManagedMcpOAuthConfig`, so the silent `extra="ignore"` drop is unobserved by the suite.
- No test covers list-typed oauth values or a scalar-where-list-expected malformation.

---

## 5. Configuration and Environment

### Environment Variables

- `CUSTOMER_CONFIG_DIR` — consumed as `config.CUSTOMER_CONFIG_DIR` at `managed_mcp_config.py:121`; the catalog directory. Declared on the `Config(BaseSettings)` class in `src/codemie/configs/config.py` (925+ lines); the exact declaration line and default literal were not surfaced by codegraph in this run.
- No other env var, feature flag, or dynamic-config key gates this endpoint. Authentication is the router-level `Depends(authenticate)`.

### Configuration Files

- `config/customer/managed-mcp-servers.example.yaml` — schema documentation, never read at runtime.
- `config/customer/customer-config.yaml` — the sibling customer config in-repo.
- `managed-mcp-servers.yaml` — the live catalog; a key in the `codemie-customer-config` ConfigMap, not committed here.
- `pyproject.toml` — pydantic `^2.9.2`, pydantic-settings `^2.5.2`, fastapi `^0.133.0`, starlette `~1.3.1`, httpx `^0.28.1`, ruff `^0.5.4`; ruff line-length 120, `select = [E,F,B,N,C4,G,T20,RSE,SIM,C,W,PERF,ISC]`, `N815` ignored (mixedCase class attributes), `tests/*` ignores `E501`.
- `Makefile` — `ruff` (format + check --fix + check), `license-check`, `test`, `gitleaks`, `verify` (= ruff license gitleaks test), `coverage`.

### Feature Flags and Deployment Concerns

- No feature flag. Rollout is code → deploy → ConfigMap edit; the served payload changes only for entries whose ConfigMap gains the key.
- Secrets: the catalog is served verbatim to agent clients, so the secret-free invariant is load-bearing; `make gitleaks` and the pre-commit `codemie-gitleaks` hook guard the repo side.
- Acceptance criterion 6 (Claude Desktop log evidence) is an out-of-repo, post-deploy check — not reachable by CI.

---

## 6. Risk Indicators

- Two router tests assert the entire JSON body by equality — `test_mcp_managed.py:41-65` and `:69-111` (the latter pins all six `oauth` keys). Any change to the served field set is visible there.
- No test anywhere drives YAML → HTTP: router tests patch `load_managed_mcp_servers` and inject model objects, so ConfigMap-key survival is unobserved.
- `extra="ignore"` on both models (`managed_mcp_config.py:81`, `:103`) drops undeclared keys with no warning; the loader has no detection path for them.
- Fail-closed per entry: `load_managed_mcp_servers` catches only `(ValidationError, TypeError)` (line 144), so one wrong-typed value removes a whole server from the catalog — with the raw entry logged.
- `test_example_file_is_valid` (`test_managed_mcp_config.py:289-320`) asserts oauth fields on the example YAML, so example-file edits are test-visible.
- The live catalog is not in this repo; acceptance criterion 6 cannot be verified in-repo or in CI.
- `config.CUSTOMER_CONFIG_DIR`'s declaration/default was not surfaced by codegraph — an unresolved dimension-5 detail.
- Codegraph blast-radius reported `ManagedMcpServer` at `managed_mcp_config.py:39` while the verbatim source shows line 84 — re-verify line numbers before editing.
- Out-of-repo couplings the ticket asserts (CLI `isValidOAuthConfig`, `codemie-customer-config` ConfigMap) were not independently verified; that repository was not queried.
- Speculative: adding a field to `ManagedMcpOAuthConfig` changes the serialized `oauth` object for every entry, so the two equality assertions above would need updating — test churn, not new components.
- Speculative: the ticket forbids deriving the issuer from `tokenUrl`; vendor-neutrality is a stated constraint, so a "derive it" shortcut is out of bounds by design, not by omission.

---

## 7. Summary for Complexity Assessment

The surface is unusually contained. The whole feature is one 153-line config module (`src/codemie/configs/managed_mcp_config.py`) plus a 32-line passthrough router (`mcp_managed.py`), one documentation-only example YAML, and two test files. There is no service, repository, database, migration, or enterprise module in the path, and the router needs no change because FastAPI serializes `response_model` by alias — the pydantic model *is* the wire contract. Layers touched are therefore Config/Model, the API response schema, and deployment-config documentation. The prior ticket that created this `oauth` block (EPMCDME-14072) changed exactly these four files and measured **S / total 14**.

Technical novelty is near zero: the module already demonstrates every pattern involved — camelCase alias-only fields, `Optional[...] = Field(default=None, alias=...)`, `BeforeValidator`/`PydanticUseDefault`, and a documented graceful-degradation contract. The stated constraints (keep `authorizationUrl`/`tokenUrl` required, do not derive the issuer, no client secret) all *preserve* existing behaviour rather than introduce new mechanics.

Test posture is good at the unit level and blind at exactly one seam. 21 loader tests and 3 router tests exist, including exact-equality assertions on the served JSON and a test validating the example YAML. What no test does is drive a ConfigMap YAML key through validation into the HTTP body — which is why a silently dropped key was invisible. Chief risk factors are that dropped-key blindness, the fail-closed per-entry validation (one bad value removes a server), the secret-free invariant on a payload served verbatim to external clients, and the fact that the definitive acceptance signal lives in a Claude Desktop log after a ConfigMap change no CI job can perform.
