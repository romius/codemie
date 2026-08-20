# EPMCDME-14305 — Publish the OAuth issuer in the managed MCP catalog

**Type**: Bug (Blocker) · **Size**: S (13/36) · **Date**: 2026-08-20

## Problem

`GET /v1/mcp/managed-servers` serves a per-server `oauth` block with `authorizationUrl` and
`tokenUrl` but no issuer. Claude Desktop reads that combination as *explicit* OAuth mode: it skips
discovery and fabricates the authorization-server metadata, deriving `issuer` from the **origin** of
`tokenUrl` with the path discarded. Keycloak's issuer is the realm URL, not the host origin, so the
fabricated value never matches the `iss` returned on the callback and Desktop aborts with
`Issuer mismatch in authorization response (RFC 9207)`. Keycloak advertises
`authorization_response_iss_parameter_supported: true`, so the check is never skipped. 35 of 42
entries are affected; no EPAM MCP server can be connected from Claude Desktop, and the failure
happens inside Desktop before the code is redeemed, so proxy logs show nothing.

`ManagedMcpOAuthConfig` (`src/codemie/configs/managed_mcp_config.py:54-81`) declares exactly six
fields and sets `extra="ignore"` (`:81`). There is no field for the issuer, and a ConfigMap-only
workaround is discarded at validation without a warning — verified through a FastAPI TestClient:
`model_extra` was `None`, the key absent from the response.

## Approach

Declare one additional optional field on `ManagedMcpOAuthConfig`, aliased `authorizationServer`,
typed as a list of strings, defaulting to `None`. Supplying it outranks `authorizationUrl`/`tokenUrl`
in Desktop's mode selection and switches it to issuer mode, where it discovers real metadata from
Keycloak's `/.well-known/openid-configuration` — confirmed end-to-end by hand-patching the local
Desktop config.

Nothing else in the request path changes. FastAPI serializes `response_model` by alias, so the
32-line passthrough router (`src/codemie/rest_api/routers/mcp_managed.py:27-32`) is untouched: the
pydantic model *is* the wire contract.

Three decisions worth recording:

1. **`Optional[List[str]]`, not `Optional[str]`.** Desktop's mode selection tests
   `e.authorizationServer && e.authorizationServer.length > 0`, which a bare string would also
   satisfy — but the list is the shape that was measured working, so match it. Consequence: a scalar
   YAML value fails validation and the module's fail-soft contract (`:144-147`) skips that entry with
   a warning while the rest of the catalog loads. That is acceptance criterion 4, and it needs no new
   code — only a test pinning it.
2. **`extra="ignore"` behaviour is left alone; only its comment (`:78-80`) is reworded.** It reads as
   though an unknown ConfigMap key propagates to clients; it does not, it only means the entry
   survives. Adding unknown-key detection would fire on exactly the ConfigMap-ahead-of-rollout case
   the setting exists to protect, and would change the module's contract beyond this bug.
3. **`authorizationUrl` and `tokenUrl` stay required.** The consuming CLI's `isValidOAuthConfig`
   hard-requires both; an entry missing either is dropped whole, and if every entry drops the CLI
   reads the catalog as a fetch failure and serves no org MCPs. Relaxing this is a later ticket.

Entries without the key serialize `"authorizationServer": null`, matching how `description`,
`clients` and `oauth` already serialize. Null is falsy in Desktop's check and ignored by the CLI, so
existing clients behave exactly as today.

## Scope

- **`src/codemie/configs/managed_mcp_config.py`** — add the field following the sibling
  `Optional[...] = Field(default=None, alias=...)` pattern at `:99-101`; reword the `extra="ignore"`
  comment. Re-verify line numbers before editing; a prior codegraph run reported stale ones.
- **`config/customer/managed-mcp-servers.example.yaml`** — document `authorizationServer` in the
  schema comment block (`:19-25`) and the example entry (`:33-39`), placed before `authorizationUrl`
  to mirror the deployment snippet. Correct `:23-24`, which advertise `authorizationUrl` as carrying
  `kc_idp_hint`/`prompt`: a client using issuer discovery ignores them, because Keycloak's discovered
  `authorization_endpoint` carries no query string. That loss is an accepted side effect of the fix.
- **Tests** — `tests/codemie/configs/test_managed_mcp_config.py` and
  `tests/codemie/rest_api/routers/test_mcp_managed.py`.

### Test coverage

The defect lived in an untested seam: nothing drives YAML → HTTP. Router tests patch
`load_managed_mcp_servers` and inject constructed models; loader tests stop at the model. Closing
that seam is the point of the new coverage, so the round-trip test must **not** patch the loader — it
points `config.CUSTOMER_CONFIG_DIR` (read at call time, `:121`) at a `tmp_path` holding a real
`managed-mcp-servers.yaml` and asserts `authorizationServer` reaches the JSON body. One such test is
enough; 21 loader tests already cover validation.

Beyond that: a loader test for the list value; a loader test for the scalar-where-list-expected skip,
asserting the warning as `test_snake_case_oauth_key_is_rejected` does; and the equality assertions.
`test_list_managed_servers_serializes_oauth_in_camel_case` (`test_mcp_managed.py:69-111`) pins all
six current `oauth` keys and must gain the seventh; `test_list_managed_servers_returns_loaded_entries`
(`:41-65`) asserts a body whose entry has `oauth: null` and must be re-checked.
`test_example_file_is_valid` (`test_managed_mcp_config.py:289-320`) makes the example-file edit
test-visible and should assert the new key parses as a list.

## Acceptance criteria

1. An entry carrying `authorizationServer` in the ConfigMap is returned by
   `GET /v1/mcp/managed-servers?client=claude-desktop` with `authorizationServer` present inside the
   `oauth` object of the JSON response.
2. An entry without `authorizationServer` still loads and serves exactly as before — backward
   compatible with every existing ConfigMap.
3. `authorizationUrl` and `tokenUrl` remain required; an entry omitting either is still skipped and
   logged, and the rest of the catalog still loads.
4. A malformed `authorizationServer` (scalar where a list is expected) skips that entry with a
   warning; the catalog still loads.
5. No client secret is introduced; the catalog remains secret-free.
6. End-to-end after the ConfigMap update: no `Issuer mismatch in authorization response (RFC 9207)`
   and a `saved OAuth tokens` line in Claude Desktop's `main.log`. Out-of-repo, post-deploy — not
   provable by CI.
7. `make ruff`, `make license-check` and `make test` pass.

## Non-goals

- Do **not** derive the issuer from `tokenUrl` by truncating at `/protocol/openid-connect/` or by any
  other string surgery — that hardcodes a Keycloak URL shape into a vendor-neutral catalog. One
  explicit field per entry.
- Do **not** make `authorizationServer` required, and do not relax `authorizationUrl` or `tokenUrl`.
- Do **not** change `extra="ignore"`, add unknown-key detection, or otherwise alter the module's
  graceful-degradation contract.
- Do **not** change `mcp_managed.py`. No service, repository, database, migration or enterprise
  module is in scope.
- Do **not** set `populate_by_name`; camelCase stays the only accepted input spelling.
- Do **not** introduce a client secret, a feature flag, or a CLI release.
- Do **not** update the live `codemie-customer-config` ConfigMap from this repo — separate deployment
  step, after merge and deploy.
- Do **not** touch the deprecated `auth` scalar (`:98`), restore `kc_idp_hint`/`prompt` under issuer
  discovery, or address the unrelated `-31004 server-fault` failure that surfaces afterwards.
- Do **not** refactor the loader, its exception tuples, or unrelated tests.

## Risks

- An operator's natural spelling is the scalar `authorizationServer: https://…`, which removes that
  whole server from the catalog under the fail-soft contract. Mitigated only by documentation, which
  makes the example-YAML edit load-bearing rather than cosmetic.
- Criterion 6 is the real pass/fail signal and lives in a Claude Desktop log after a ConfigMap change
  no CI job can perform. In-repo tests prove the key is served, not that the flow completes.
- The out-of-repo couplings (CLI `isValidOAuthConfig`, the ConfigMap) are asserted by the ticket and
  were not independently verified from this repository.
- Rollout order is load-bearing: backend merge → deploy → ConfigMap update → verify.
