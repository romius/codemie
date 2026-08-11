# EPMCDME-14072 — Per-server OAuth configuration in the managed MCP catalog

**Status**: design approved pending gate
**Complexity**: S (14/36)
**Branch**: `EPMCDME-14072_managed-mcp-oauth`

## Problem

`GET /v1/mcp/managed-servers` serves a client-neutral catalog of managed MCP servers to agent
clients (Claude Desktop, Codex). An entry today declares only *whether* a server needs OAuth
(`auth: oauth | none`) — it carries no OAuth parameters.

Agent clients therefore cannot complete an authorization-code flow from the catalog alone. They must
hardcode the Keycloak realm URLs, client id, scope and loopback callback, which breaks per-environment
deployments (lab / prod / customer realms) and forces a client release for every endpoint change.

## Goal

Let each catalog entry carry a complete, self-describing OAuth client configuration, so an agent
client can run the flow with zero hardcoded values.

## Design

### Data model

A new nested model in `src/codemie/configs/managed_mcp_config.py`, and one optional field on the
existing `ManagedMcpServer`:

```python
class ManagedMcpOAuthConfig(BaseModel):
    """Public OAuth client parameters for a managed MCP server. Secret-free by design."""

    client_id: str = Field(alias="clientId")
    scope: str
    callback_host: str = Field(default="localhost", alias="callbackHost")
    callback_port: int = Field(alias="callbackPort", ge=1, le=65535)
    authorization_url: str = Field(alias="authorizationUrl")
    token_url: str = Field(alias="tokenUrl")

    model_config = ConfigDict(extra="ignore")


class ManagedMcpServer(BaseModel):
    name: str
    transport: Literal["http", "sse"]
    url: str
    auth: Literal["oauth", "none"] = "none"      # deprecated — see below
    description: Optional[str] = None
    clients: Optional[List[str]] = None
    oauth: Optional[ManagedMcpOAuthConfig] = None    # NEW

    model_config = ConfigDict(extra="ignore")
```

| Field | Type | Required | Notes |
|---|---|---|---|
| `clientId` | string | yes | Public OAuth client registered in the IdP |
| `scope` | string | yes | Space-delimited scopes |
| `callbackHost` | string | no — default `localhost` | Loopback redirect host |
| `callbackPort` | integer | yes | Loopback redirect port, bounded `1..65535` |
| `authorizationUrl` | string | yes | Full authorize URL including query params (`kc_idp_hint`, `prompt`) |
| `tokenUrl` | string | yes | Token endpoint |

### Decisions

Four design decisions were taken explicitly; each rejected alternative is recorded so a later reader
does not relitigate it.

**1. Aliasing is scoped to the nested OAuth model only.** camelCase has no precedent anywhere in
`src/codemie/configs/` — every model there is snake_case. `ManagedMcpServer`'s own fields are all
single words (`name`, `transport`, `url`, `auth`, `description`, `clients`), so casing is moot for
them and the model is left untouched.
*Rejected*: a package-wide `alias_generator=to_camel`, which would restyle the entire entry and
establish a new convention on the back of one ticket; and naming the Python fields `clientId` /
`callbackPort` directly, which violates PEP 8 and the repo's snake_case standard.

**2. camelCase is the single canonical form — `populate_by_name` is deliberately NOT set.**
In pydantic v2, `Field(alias=…)` governs validation *and* serialization, and FastAPI's
`response_model_by_alias` defaults to `True`. With `populate_by_name` unset, the alias is the only
accepted input form. One consequence is the property the ticket asks for: **the YAML catalog and the
HTTP response use identical keys.** A snake_case `client_id` in the catalog is not a second accepted
spelling — it fails validation, and the entry is skipped and logged like any other malformed entry.

**3. The nested block keeps `extra="ignore"`.** This inherits the module's existing contract rather
than introducing a stricter one. The exposure is narrower than it first appears: a typo in a
*required* key (`client_id` for `clientId`) already fails validation loudly and triggers
skip-and-log. Only a typo in the *optional* `callbackHost` degrades silently, falling back to
`localhost`.
*Rejected*: `extra="forbid"`, which would make every typo loud but would break entries outright when
a ConfigMap gains a new OAuth field ahead of a backend rollout. Graceful degradation is this
module's whole ethos.

**4. The scalar `auth` field is retained and marked deprecated.** It keeps working unchanged, so no
existing deployment breaks. The model docstring and the example YAML document `oauth` as the source
of truth and `auth` as deprecated; removal is a follow-up ticket once deployments have migrated.
*Rejected*: deriving `auth` from the presence of `oauth`, which would silently override what an
operator wrote — a behavior change on an existing field.

### Layers touched

| Layer | File | Change |
|---|---|---|
| Config loader | `src/codemie/configs/managed_mcp_config.py` | New nested model + optional `oauth` field + deprecation note |
| REST API | `src/codemie/rest_api/routers/mcp_managed.py` | **None.** `response_model=List[ManagedMcpServer]` inherits the field; FastAPI serializes by alias by default |
| Deployment config | `config/customer/managed-mcp-servers.example.yaml` | Schema comment header + sample `oauth` block |
| Tests | `tests/codemie/configs/test_managed_mcp_config.py` | New cases (below) |
| Tests | `tests/codemie/rest_api/routers/test_mcp_managed.py` | **Repair required** — the exact-dict assertion at lines 55-64 breaks once an `oauth` key appears |

### Behavior

Loading is unchanged in shape — `load_managed_mcp_servers` still reads
`<CUSTOMER_CONFIG_DIR>/managed-mcp-servers.yaml`, still validates entry by entry, still filters by
`?client=`. The nested block simply participates in the existing per-entry validation:

- Entry with a valid `oauth` block → loads; response carries the nested object in camelCase.
- Entry with **no** `oauth` key → loads exactly as today; `oauth` is `null` in the response.
- Entry with a malformed `oauth` block (missing required key, non-integer or out-of-range
  `callbackPort`, wrong-cased required key) → `ValidationError` → that entry is skipped and logged,
  **every other entry still loads**.
- Missing, unreadable or corrupt catalog file → `[]`, HTTP 200. Unchanged.

### Security

The catalog stays secret-free. Only public-client parameters are published: client id, scope,
loopback callback, and IdP authorize/token URLs. No client secret is stored, transmitted, or logged,
and token exchange remains entirely client-side.

This preserves the existing "logging the raw entry is safe" property at
`managed_mcp_config.py:91-92`. The caveat comment there — *revisit if a credential-bearing field is
ever added* — still holds and must stay in place, because a malformed entry's raw contents are
written to the warning log.

## Testing

Behavior to cover, all in the two test files named above:

1. A valid `oauth` block round-trips: the loader produces the nested model with the correct values.
2. **Wire casing** — the endpoint response contains exactly `clientId`, `scope`, `callbackHost`,
   `callbackPort`, `authorizationUrl`, `tokenUrl`. This is the assertion that pins decision 2; the
   codebase has no existing coverage of wire casing anywhere.
3. An entry with no `oauth` key still loads, and `oauth` is absent/null in the response.
4. A malformed `oauth` block is skipped and logged while sibling entries still load — one case for a
   missing required key, one for an out-of-range `callbackPort`.
5. `callbackHost` defaults to `localhost` when omitted.
6. A snake_case required key (`client_id`) fails validation, confirming decision 2 rather than
   silently half-loading.
7. `?client=` filtering is unaffected by the presence of `oauth`.
8. The updated example YAML still validates against the model — the existing
   `test_example_file_is_valid` enforces this, so the example and the model must move in one commit.

## Out of scope

- Server-side execution of the OAuth flow — token exchange stays client-side.
- Client secrets — the catalog remains secret-free.
- Local/stdio MCP servers — the catalog stays remote-only.
- Removing the deprecated `auth` field.

## Known residual risk

The authoritative catalog lives outside this repository, in the `codemie-customer-config` ConfigMap.
No test can detect drift between a deployed catalog and this model; the example YAML is the only
in-repo expression of the schema, and it is documentation, never read at runtime.
