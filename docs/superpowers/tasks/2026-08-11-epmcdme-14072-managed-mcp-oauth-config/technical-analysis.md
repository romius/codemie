# Technical Research

**Task**: mcp managed catalog oauth pydantic config
**Generated**: 2026-08-11
**Research path**: codegraph

---

## 1. Original Context

EPMCDME-14072 — Extend the managed MCP catalog to expose per-server OAuth client configuration.

Context: CodeMie backend serves a client-neutral managed MCP catalog to agent clients (Claude Desktop, Codex, etc.) via `GET /v1/mcp/managed-servers` (src/codemie/rest_api/routers/mcp_managed.py). Entries are loaded from `managed-mcp-servers.yaml`, supplied per deployment as a key in the `codemie-customer-config` ConfigMap and mounted at CUSTOMER_CONFIG_DIR (src/codemie/configs/managed_mcp_config.py). Today an entry only declares WHETHER a server needs OAuth (`auth: oauth | none`) and carries no OAuth parameters, so agent clients must hardcode Keycloak realm URLs, client id, scope and loopback callback. That breaks per-environment deployments and forces a client release for every endpoint change.

Goal: extend the catalog entry model and the endpoint response so each managed server can carry a complete, self-describing OAuth client configuration.

Target response shape:
[
  {
    "name": "onehub_core",
    "url": "https://codemie.lab.epam.com/mcp/mcp-proxy/onehub_core",
    "transport": "http",
    "oauth": {
      "clientId": "codemie-mcp-proxy",
      "scope": "openid profile email",
      "callbackHost": "localhost",
      "callbackPort": 3118,
      "authorizationUrl": "https://auth.codemie.lab.epam.com/realms/codemie-prod/protocol/openid-connect/auth?kc_idp_hint=epam-oidc&prompt=login",
      "tokenUrl": "https://auth.codemie.lab.epam.com/realms/codemie-prod/protocol/openid-connect/token"
    }
  }
]

New nested `oauth` object fields: clientId (string, required), scope (string, required), callbackHost (string, optional, default "localhost"), callbackPort (integer, required, 1-65535), authorizationUrl (string, required, full authorize URL including query params), tokenUrl (string, required). Wire format is camelCase.

Scope of change:
1. Add a nested OAuth config model and an optional `oauth` field on ManagedMcpServer (src/codemie/configs/managed_mcp_config.py).
2. Serialize camelCase in the API response and accept camelCase in the YAML catalog, so the catalog file and the wire format stay identical.
3. Preserve the loader resilience contract: an entry with a malformed `oauth` block is skipped and logged, the rest of the catalog still loads; a missing/corrupt file still yields [].
4. Update config/customer/managed-mcp-servers.example.yaml with the new schema and its field documentation.
5. Extend tests/codemie/configs/test_managed_mcp_config.py.

Backward compatibility (additive only): entries without `oauth` keep loading and serializing as before; the existing scalar `auth: oauth | none` field is retained; `description` and `clients` targeting plus the `?client=` filter are unchanged.

Out of scope: server-side execution of the OAuth flow (token exchange stays client-side), storing client secrets, local/stdio MCP servers.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/configs/managed_mcp_config.py` (99 lines) — the entire feature. Contains:
  - `MANAGED_MCP_FILENAME = "managed-mcp-servers.yaml"` (line 36)
  - `ManagedMcpServer(BaseModel)` (line 39) — fields `name: str`, `transport: Literal["http","sse"]`, `url: str`, `auth: Literal["oauth","none"] = "none"`, `description: Optional[str]`, `clients: Optional[List[str]]`; `model_config = ConfigDict(extra="ignore")`.
  - `load_managed_mcp_servers(client=None, base_dir=None)` (line 52) — resolves `base_dir or config.CUSTOMER_CONFIG_DIR`, `path.exists()` short-circuit, `yaml.safe_load`, guarded `data.get("servers")` / `isinstance(raw, list)` checks, per-entry `ManagedMcpServer(**item)` inside `try/except (ValidationError, TypeError)` with `logger.warning`, then client filtering (`not s.clients or client in s.clients`).
- `src/codemie/rest_api/routers/mcp_managed.py` (32 lines) — `router = APIRouter(prefix="/v1/mcp", tags=["MCP"], dependencies=[Depends(authenticate)])`; `@router.get("/managed-servers", response_model=List[ManagedMcpServer])`; handler is a one-line delegation `return load_managed_mcp_servers(client=client)`.
- `config/customer/managed-mcp-servers.example.yaml` — documentation-only example with an inline schema comment block (name / transport / url / auth / description / clients) that must be extended with the `oauth` block.
- `config/customer/customer-config.yaml` — sibling file in the same customer ConfigMap directory (unrelated content, but shares the mount).

Note: this catalog is entirely separate from the DB-backed MCP catalog (`src/codemie/rest_api/models/mcp_config.py` `MCPConfig`, `src/codemie/service/mcp_config_service.py` `MCPConfigService`), which has its own `auth_config` with encrypted client secrets. No code path connects the two; the ticket touches only the YAML catalog.

### Architecture and Layers Affected

- **Config loader layer** (`src/codemie/configs/`) — `managed_mcp_config.py`. Primary change surface: new nested model + optional field + camelCase aliasing. Sibling loaders in the same package for convention reference: `llm_config.py` (`LLMConfig`/`LLMModel`/`CostConfig`), `budget_config.py` (`BudgetConfig`), `mcp_commands_config.py` (`MCPCommandsConfig`).
- **REST API layer** (`src/codemie/rest_api/routers/mcp_managed.py`) — no signature change required; `response_model=List[ManagedMcpServer]` picks up the new field automatically. FastAPI serializes response models with `by_alias=True` by default, so field aliases surface on the wire without router edits.
- **Deployment config layer** (`config/customer/`) — example file plus the out-of-repo `codemie-customer-config` ConfigMap key.
- **Test layer** — `tests/codemie/configs/test_managed_mcp_config.py` and `tests/codemie/rest_api/routers/test_mcp_managed.py`.
- No service layer, no repository layer, no database, no migration involved.

### Integration Points

- `codemie.configs.config.config.CUSTOMER_CONFIG_DIR` — the only internal config dependency; resolves the ConfigMap mount directory.
- `codemie.configs.logger.logger` — used for the two resilience warnings.
- `codemie.rest_api.security.authentication.authenticate` — router-level auth dependency; overridden in the router test via `app.dependency_overrides`.
- External consumers: agent clients (Claude Desktop, Codex) consume the JSON directly; the ConfigMap is authored outside this repository per environment.
- Keycloak realm URLs appear as *data values* only — no server-side HTTP call to the IdP is added by this ticket.

### Patterns and Conventions

- Pydantic v2 `BaseModel` + `ConfigDict(extra="ignore")`; `Literal[...]` for enumerated scalars; `Optional[X] = None` for optional fields.
- A single model serves both as YAML schema and as the FastAPI `response_model` — no separate request/response DTO under `src/codemie/rest_api/models/`. This deliberately diverges from `.ai-run/guides/api/endpoint-conventions.md` ("keep API schemas near the REST API layer"), and is what makes the "catalog file == wire format" requirement achievable.
- Resilience idiom: defensive `isinstance` checks at container level, per-item `try/except (ValidationError, TypeError)` + `logger.warning`, never raise out of the loader.
- Testability seam: `base_dir` parameter override rather than patching `config`.
- Bounded-integer precedent for `callbackPort`: `Field(default=10, ge=1)` in `src/codemie/datasource/datasources_config.py` (`StorageConfig.indexing_heartbeat_interval`).
- **No camelCase precedent found.** `managed_mcp_config.py`, `llm_config.py`, `budget_config.py`, `datasources_config.py` and `mcp_config.py` are all snake_case with no `alias_generator`, `to_camel`, `serialization_alias` or `populate_by_name` usage surfaced by codegraph. The camelCase requirement introduces a new convention to this package.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/mcp-integration.md` — 12 lines; only states that MCP config/auth behavior belongs behind existing MCP routers/services. Does **not** document the managed catalog at all.
- `.ai-run/guides/api/endpoint-conventions.md` — typed request/response models near the REST layer; routers validate and delegate, services orchestrate.
- `.ai-run/guides/development/configuration-patterns.md` — use `src/codemie/configs/` and existing YAML config trees for runtime settings; do not read env vars directly in feature code.
- `.ai-run/guides/testing/testing-patterns.md` — mirror `src/` under `tests/codemie/<package path>/`; run the narrowest relevant scope; "seam tests" required when a helper encapsulates a default-vs-custom decision (directly applicable to `callbackHost` defaulting to `"localhost"`).
- `.ai-run/guides/standards/code-quality.md`, `.ai-run/guides/quality-gates.md` — Ruff/validation commands (not read in depth; load before running gates).
- No ADR directory exists in the repository.

### Architectural Decisions

Recorded as inline comments in `src/codemie/configs/managed_mcp_config.py` rather than in guides:

- Module docstring (lines 15-23): the catalog file is intentionally **not** committed; it is a ConfigMap key mounted at `CUSTOMER_CONFIG_DIR`; the loader degrades to "no managed MCPs" instead of raising.
- Lines 74-79: the `(yaml.YAMLError, OSError, UnicodeDecodeError)` tuple is deliberate — `OSError` covers Permission/IsADirectory/TOCTOU, `UnicodeDecodeError` covers binary content and is not an `OSError` subclass.
- Lines 91-92: **"v1 entries carry no secrets/tokens, so logging the raw entry is safe. Revisit if a credential-bearing field is ever added."** This ticket adds an OAuth block; it is public-client config with no secret, so the premise holds — but the comment's own trigger condition is being approached and should be re-read during implementation.
- Lines 96-97: `not s.clients` intentionally treats both `None` and `[]` as "applies to all clients".
- `config/customer/managed-mcp-servers.example.yaml` lines 1-11: the `.example.yaml` suffix guarantees the loader never picks it up; the schema comment block is the user-facing documentation.

### Derived Conventions

- Field documentation for this catalog lives in the YAML example's comment header, not in Pydantic `Field(description=...)` — the existing model uses no `description=` at all. Extending the example header is the documented path (ticket item 4 matches this).
- Loader-level validation is preferred over router-level validation; the router stays a one-liner.
- New config models go in the same module as their loader, not in `rest_api/models/`.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/configs/test_managed_mcp_config.py` (125 lines, 11 tests) — module-level `SAMPLE_YAML` constant and a `_write(dir_path, text)` helper writing `managed-mcp-servers.yaml` into `tmp_path`:
  - `test_missing_file_returns_empty`, `test_corrupt_yaml_returns_empty`, `test_path_is_directory_returns_empty`, `test_non_dict_root_returns_empty`, `test_non_list_servers_returns_empty` — resilience contract.
  - `test_loads_and_parses_entries`, `test_returns_typed_models` — happy path and typing.
  - `test_skips_malformed_entries` (bad `transport: ftp`), `test_non_dict_entry_is_skipped` — per-entry skip contract.
  - `test_filters_by_client` — `?client=` targeting semantics.
  - `test_example_file_is_valid` (line 105) — resolves the repo root from `codemie.__file__`, `yaml.safe_load`s `config/customer/managed-mcp-servers.example.yaml`, constructs `ManagedMcpServer(**item)` for every entry, and asserts `load_managed_mcp_servers(base_dir=example.parent) == []`. **This binds the example file to the model: any schema change must update both or this test fails.**
- `tests/codemie/rest_api/routers/test_mcp_managed.py` (77 lines, 2 tests) — `user` fixture, autouse `override_dependency` fixture patching `mcp_managed_router.authenticate`, `patch("codemie.rest_api.routers.mcp_managed.load_managed_mcp_servers")`.
  - `test_list_managed_servers_returns_loaded_entries` asserts **exact JSON dict equality** at lines 55-64, including `"description": None, "clients": None`.
  - `test_list_managed_servers_without_client_param` asserts `mock_load.assert_called_once_with(client=None)` and `[]`.

### Testing Framework and Patterns

- pytest with function-style tests and `tmp_path` for the loader; `pytest.mark.asyncio` + `httpx.AsyncClient(transport=ASGITransport(app=app))` for the router.
- Auth bypass via `app.dependency_overrides[...] = lambda: user` in an autouse fixture that resets `app.dependency_overrides = {}` on teardown.
- Loader tests use the real filesystem (`tmp_path`) rather than mocks; router tests mock the loader boundary with `unittest.mock.patch`.
- Assertions favor exact equality on whole response payloads over field-by-field checks.

### Coverage Gaps

- **No test asserts the wire *casing* of any field.** The router test is the only wire-format assertion, and every current field name is a single lowercase word, so camelCase serialization is entirely unverified today. A new test asserting `oauth.clientId` (not `client_id`) in the HTTP response is required.
- No test covers "entry with a malformed `oauth` block is skipped while the rest of the catalog loads" — the closest is `test_skips_malformed_entries`, which uses a bad top-level `transport`. A nested-failure case is new ground.
- No test covers `callbackHost` defaulting, and per `.ai-run/guides/testing/testing-patterns.md` this default deserves a seam test at the response boundary, not just at the model.
- No boundary test for `callbackPort` (0 / 65536 / non-integer).
- No test asserts that a snake_case `oauth` block in YAML is rejected/ignored, which matters because `extra="ignore"` silently drops unknown keys.
- `src/codemie/rest_api/routers/mcp_managed.py` has no negative-path test (unauthenticated request).

---

## 5. Configuration and Environment

### Environment Variables

- `CUSTOMER_CONFIG_DIR` — the sole env-backed setting in this path; read via `config.CUSTOMER_CONFIG_DIR` in `src/codemie/configs/managed_mcp_config.py:67`, defined on the central `config` object in `src/codemie/configs/config.py`. Points at the mounted `codemie-customer-config` ConfigMap directory.
- No new environment variable is required by this ticket — all OAuth values are data inside the YAML catalog, per deployment.

### Configuration Files

- `managed-mcp-servers.yaml` — **not in this repository**; a key of the `codemie-customer-config` ConfigMap, mounted at `CUSTOMER_CONFIG_DIR`. Its root shape is `{servers: [ ... ]}`.
- `config/customer/managed-mcp-servers.example.yaml` — in-repo documentation copy with the schema comment header; validated by `test_example_file_is_valid`; deliberately named so the loader never reads it.
- `config/customer/customer-config.yaml` — sibling file under the same mount.

### Feature Flags and Deployment Concerns

- No feature flag gates this endpoint; the router is registered unconditionally.
- Rollout is ConfigMap-driven: the code change is additive and inert until a deployment adds an `oauth` block to its ConfigMap. Old ConfigMaps keep working (entries without `oauth`).
- No secrets are stored — the OAuth block is public-client configuration (client id, scope, loopback callback, IdP URLs). Client secrets remain explicitly out of scope, keeping this catalog free of the encryption machinery used by `MCPConfigService` for the DB-backed catalog.
- Client-side coupling: agent clients must tolerate a missing/null `oauth` key during the window where code is deployed but ConfigMaps are not yet updated.

---

## 6. Risk Indicators

- `tests/codemie/rest_api/routers/test_mcp_managed.py:55-64` asserts the response JSON by exact dict equality; adding an `oauth` key (serialized as `null` for entries without it) **breaks this test**. The ticket's scope list names only `tests/codemie/configs/test_managed_mcp_config.py`, so this file is a scope omission.
- camelCase aliasing is a **novel pattern** for `src/codemie/configs/` — no `alias_generator` / `to_camel` / `populate_by_name` usage exists in `managed_mcp_config.py`, `llm_config.py`, `budget_config.py`, `datasources_config.py` or `mcp_config.py`. A design decision is needed: alias only the nested OAuth model (existing top-level fields are single-word and unaffected) versus the whole `ManagedMcpServer`.
- `ConfigDict(extra="ignore")` means a mistyped OAuth sub-key (`clientid`, `client_id`) is silently dropped instead of surfacing. Missing *required* fields still raise `ValidationError` and hit the skip-and-log path, but the failure mode for near-miss keys is a silently absent OAuth block rather than a logged skip — directly at odds with the ticket's resilience wording.
- `src/codemie/configs/managed_mcp_config.py:91-92` logs the raw entry with the explicit caveat "Revisit if a credential-bearing field is ever added". The OAuth block adds client id, scope and IdP URLs to warning logs. No secret is involved, but the comment's trigger condition should be consciously re-evaluated and the comment updated.
- `test_example_file_is_valid` (`tests/codemie/configs/test_managed_mcp_config.py:105`) hard-binds `config/customer/managed-mcp-servers.example.yaml` to the model — the example and the model must change in the same commit or the suite fails.
- Zero existing coverage of wire-format casing: all current fields are single lowercase words, so `by_alias` serialization behavior is unproven in this codebase. FastAPI's default `response_model_by_alias=True` is being relied on implicitly in `src/codemie/rest_api/routers/mcp_managed.py:27`.
- Dual-role model: `ManagedMcpServer` is simultaneously the YAML schema and the HTTP response model. Alias configuration chosen for the wire also changes what the YAML parser accepts — the two cannot be tuned independently, and `populate_by_name` is the only lever that keeps both spellings loadable.
- `callbackPort` bounds (1-65535) require `Field(ge=1, le=65535)`; the only precedent in the repo is `Field(default=10, ge=1)` in `src/codemie/datasource/datasources_config.py`, and none in this module.
- Deployment split-brain: the authoritative catalog lives in the out-of-repo `codemie-customer-config` ConfigMap per environment, so the example file can drift from every real deployment with no test able to detect it.
- The router bypasses the service layer documented in `.ai-run/guides/api/endpoint-conventions.md`; acceptable at current size but worth an explicit note if the model grows further.
- `.ai-run/guides/integration/mcp-integration.md` (12 lines) does not mention the managed catalog at all — the domain is effectively undocumented outside inline comments in one 99-line module.

---

## 7. Summary for Complexity Assessment

**Layers and change surface.** The task touches four layers, but only one of them meaningfully: the config-loader layer in `src/codemie/configs/managed_mcp_config.py`, a self-contained 99-line module holding both the Pydantic model and the YAML loader. The REST layer (`src/codemie/rest_api/routers/mcp_managed.py`, 32 lines) needs no edit at all — its `response_model=List[ManagedMcpServer]` inherits the new field, and FastAPI's default alias-aware serialization emits camelCase automatically. The deployment layer needs `config/customer/managed-mcp-servers.example.yaml` updated (schema comment header plus a sample `oauth` block). The test layer needs two files touched, not one: `tests/codemie/configs/test_managed_mcp_config.py` as the ticket states, and — unstated in the ticket — `tests/codemie/rest_api/routers/test_mcp_managed.py`, whose exact-dict assertion at lines 55-64 will fail the moment an `oauth` key appears in the payload. Expected surface: 4 files, roughly 40-70 net new lines, no database, no migration, no service layer, no new dependency.

**Technical novelty.** The change is additive and structurally simple, but it introduces one convention this package has never used: camelCase wire aliasing. codegraph found no `alias_generator`, `to_camel`, `serialization_alias` or `populate_by_name` anywhere in `src/codemie/configs/` or `src/codemie/rest_api/models/mcp_config.py` — every config model in the repo is snake_case. Compounding this, `ManagedMcpServer` is a dual-role model: the same class parses the YAML catalog and serializes the HTTP response, so whatever alias configuration is chosen governs both sides simultaneously. That is precisely what makes the "catalog file == wire format" requirement achievable in one model, but it removes the ability to tune input and output independently, and it interacts with the existing `ConfigDict(extra="ignore")` setting, under which a near-miss key such as `client_id` or `clientid` is silently discarded rather than skipped-and-logged. The safest shape — aliasing only the nested OAuth model with `populate_by_name` enabled — is a small decision with outsized behavioral consequences for the stated resilience contract.

**Coverage posture and risk.** Test coverage of the loader is genuinely strong: 11 tests cover missing file, corrupt YAML, directory-at-path, non-dict root, non-list servers, per-entry skip, client filtering, and example-file validity. The gaps sit exactly where this ticket lands — nothing asserts wire-format casing (all existing fields are single lowercase words, so aliasing is unproven), nothing covers a nested-block validation failure being skipped while siblings load, nothing exercises the `callbackHost` default at the response boundary (which `.ai-run/guides/testing/testing-patterns.md` explicitly calls for as a seam test), and nothing bounds-checks an integer field. Two coupling risks warrant attention during implementation: `test_example_file_is_valid` hard-binds the example YAML to the model so both must move together, and the inline comment at `managed_mcp_config.py:91-92` ("Revisit if a credential-bearing field is ever added") is being brushed by an OAuth block that will now appear in warning logs — public client config only, no secret, but a deliberate re-read is warranted. Overall this reads as a low-to-moderate task: small blast radius, strong existing test scaffolding to extend, one genuinely novel serialization decision, and one test file the ticket forgot to list.
