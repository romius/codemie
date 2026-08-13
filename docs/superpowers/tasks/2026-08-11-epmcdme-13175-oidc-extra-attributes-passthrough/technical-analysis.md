# Technical Research

**Task**: oidc idp user authentication UserContext extra_attributes
**Generated**: 2026-08-11T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-13175: Extra user attributes sent in the JWT (e.g. department, cost_center, team_id) are silently dropped after OIDC parsing and never reach MCP tools, general tools, or workflow context. The fix requires adding a pass-through extra_attributes field to IdpUser, User, and UserContext, then propagating it through each layer: _build_idp_user() in codemie-enterprise, the IdpUser→User adapter bridge in codemie/enterprise/idp/dependencies.py, and UserContext.from_user() in codemie/rest_api/security/user.py.

---

## 2. Codebase Findings

### Existing Implementations

**User model** (`src/codemie/rest_api/security/user.py`, lines 28-118):
- `id: str`
- `username: str = ""`
- `name: str = ""`
- `email: str = ""`
- `roles: list` (bare untyped list — inconsistency noted)
- `project_names: list[str]`
- `admin_project_names: list[str]`
- `picture: str = ""`
- `knowledge_bases: list`
- `user_type: str | None = 'regular'`
- `is_admin: bool`
- `is_maintainer: bool`
- `project_limit: int | None` (DB-backed, no counterpart in UserContext)
- `auth_token: str | None = Field(None, exclude=True)` — excluded from serialization
- `tenant_id: str | None = Field(None, exclude=True)` — excluded from serialization
- **No `extra_attributes` field exists.**

**UserContext** (`src/codemie/rest_api/security/user.py`, lines 121-156):
- Fields (all `| None`): `id`, `username`, `name`, `email`, `roles: list[str] | None`, `is_admin`, `is_maintainer`, `user_type`, `project_names`, `admin_project_names`, `knowledge_bases`, `picture`
- `from_user()` at line 142 maps exactly those 12 fields one-for-one from `User`. No pass-through for unknown attributes.
- **No `extra_attributes` field exists.**

**Adapter bridge** (`src/codemie/enterprise/idp/dependencies.py`, lines 54-134):
- `EnterpriseIdpWrapper.authenticate()` at lines 63-128 calls `self._provider.authenticate(headers)` to obtain an `IdpUser`, then explicitly constructs `User(...)` at lines 80-92 by mapping only the 11 known fields.
- Fix point in this repo: add `extra_attributes=idp_user.extra_attributes` to the `User(...)` constructor call at lines 80-92.

**IdpUser model** — defined in the **external `codemie_enterprise` package** (separate repository, not present here):
- Imported via `from codemie_enterprise.idp import IdpUser` in `src/codemie/enterprise/loader.py` lines 152-164 (try/except import block guarded by `HAS_IDP`)
- Fields inferred from the adapter bridge: `id`, `username`, `name`, `email`, `roles`, `project_names`, `admin_project_names`, `knowledge_bases`, `picture`, `user_type`, `auth_token`
- **No `extra_attributes` field exists** — this is Drop Point 1 where JWT claims are silently discarded.

**`_build_idp_user()`** — defined in the external enterprise package (`codemie_enterprise/idp/keycloak.py` or `codemie_enterprise/idp/oidc.py`). NOT present in this repository. This method reads JWT/OIDC claims and maps only known fields into `IdpUser`, discarding everything else. This is the root fix point — it must collect unmapped claims into `extra_attributes: dict[str, Any]` and store them on `IdpUser`.

**IDP providers in this repo:**
- `src/codemie/rest_api/security/idp/base.py` — `BaseIdp` abstract base with `authenticate(request) -> User` and `get_session_cookie()`
- `src/codemie/rest_api/security/idp/local.py` — `LocalIdp` (dev `user-id` header path, no actual IDP)
- `src/codemie/rest_api/security/idp/jwks_validating.py` — `JwksValidatingIdp` (validates Bearer token JWKS signature, injects `x-auth-request-access-token`, delegates to inner enterprise IDP, extracts `tenant_id` from validated claims)
- `src/codemie/rest_api/security/idp/factory.py` — `IdpFactory` (registry/factory pattern)
- `src/codemie/enterprise/idp/dependencies.py` — `EnterpriseIdpWrapper` bridge + `register_enterprise_idps()`

**from_user() call sites (both need extra_attributes to flow through):**
- `src/codemie/service/mcp/toolkit_service.py:242` — `user_context=UserContext.from_user(current_user) if current_user else None` inside `MCPExecutionContext(...)` constructor
- `src/codemie/service/provider/provider_tool_factory.py:175` — `user_context=UserContext.from_user(user).model_dump(exclude_none=True)` inside `ToolInvocationRequest(...)`

### Architecture and Layers Affected

Five distinct layers are involved, spanning two repositories:

1. **External enterprise package** (`codemie_enterprise` — separate repo): `_build_idp_user()` in Keycloak/OIDC providers + `IdpUser` model — root fix point; must add `extra_attributes: dict[str, Any]` field and populate it from unmapped JWT claims
2. **Enterprise adapter layer** (this repo): `src/codemie/enterprise/idp/dependencies.py` — `EnterpriseIdpWrapper.authenticate()` lines 80-92; must forward `extra_attributes` when constructing `User`
3. **Auth/Security model layer** (this repo): `src/codemie/rest_api/security/user.py` — `User` (line 28) must gain `extra_attributes: dict[str, Any] | None = None`; `UserContext` (line 121) must gain the same; `from_user()` (line 142) must map it
4. **MCP service layer** (this repo): `src/codemie/service/mcp/toolkit_service.py:242` — `MCPExecutionContext` will carry the field automatically once `UserContext` has it; no explicit change needed unless `to_request_fields()` must be extended
5. **General/DSP tool service layer** (this repo): `src/codemie/service/provider/provider_tool_factory.py:175` — `model_dump(exclude_none=True)` will include `extra_attributes` automatically once present in `UserContext`; no explicit change needed

**Workflow context** — stored via `set_current_user(user)` ContextVar; accessible via `get_current_user()`. The `User` object stored there is the full `User` — once `extra_attributes` is added to `User`, workflow code calling `get_current_user()` gains access automatically without additional propagation changes.

### Integration Points

**Internal module dependencies:**
- `codemie.enterprise.loader` → `codemie_enterprise.idp` (external package, guarded by `HAS_IDP`)
- `codemie.enterprise.idp.dependencies` → `codemie.rest_api.security.user` (User model)
- `codemie.service.mcp.toolkit_service` → `codemie.rest_api.security.user` (UserContext)
- `codemie.service.provider.provider_tool_factory` → `codemie.rest_api.security.user` (UserContext)
- `codemie.service.mcp.models` → `MCPExecutionContext` (contains `user_context: UserContext | None`)

**External service connections:**
- Enterprise OIDC/Keycloak IDP: `EnterpriseIdpWrapper` delegates authentication over an in-process provider interface (not HTTP) — the `IdpUser` object is returned in-memory
- Provider/DSP service: `ToolInvocationRequest.user_context: Optional[Dict[str, Any]]` is serialized and sent over HTTP — `extra_attributes` will flow to this service once `UserContext` includes it
- MCP-Connect servers: `MCPExecutionContext.to_request_fields()` **explicitly excludes** `user_context` from the wire protocol (per EPMCDME-13546) — `extra_attributes` will NOT reach remote MCP-Connect servers via this path

**x-userinfo header path:** The literal `x-userinfo` header is parsed by the enterprise OIDC/Keycloak providers inside `codemie_enterprise`, outside this repo. In this repo, `EnterpriseIdpWrapper.authenticate()` extracts `dict(request.headers)` (line 76) and passes the full header dict to `self._provider.authenticate(headers)`. The `JwksValidatingIdp` additionally injects `x-auth-request-access-token` before delegating. Both the base (x-userinfo) and JWT bearer paths share the same `_build_idp_user()` entry point in the enterprise package — both are fixed by adding `extra_attributes` to `IdpUser` there.

### Patterns and Conventions

- **Pydantic models** used throughout (`User`, `UserContext`, `ToolInvocationRequest`) — `extra_attributes` should be a Pydantic field with `dict[str, Any] | None = None`
- **`Field(None, exclude=True)`** pattern used for `auth_token` and `tenant_id` on `User` — `extra_attributes` should NOT use `exclude=True` since it must be visible and propagated
- **`model_dump(exclude_none=True)`** at the provider tool factory call site — using `None` as the default (rather than `{}`) is important so the field is absent from serialization when not populated
- **`HAS_IDP` guard** at `loader.py` lines 152-164 — any code touching `idp_user.extra_attributes` must be within an `if HAS_IDP:` block or within the `EnterpriseIdpWrapper` which is only instantiated when `HAS_IDP` is true
- **`try/except ImportError`** pattern for the enterprise package import — graceful degradation must remain intact

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/development/security-patterns.md` — covers authentication and secret handling at a high level; no OIDC-specific guidance, no JWT claim mapping documentation, no `extra_attributes` guidance

### Architectural Decisions

- **EPMCDME-13546** (referenced in `MCPExecutionContext.to_request_fields()`): deliberate decision to exclude `user_context` from the MCP wire protocol. This means `extra_attributes` will be available locally in `MCPExecutionContext` for in-process use but will not be transmitted to remote MCP-Connect servers. The task description states the fix should reach "MCP tools" — this limitation should be noted; if wire-protocol propagation is required, it is a separate, larger change.
- No ADRs found in `docs/` or inline `ADR:` comments for the auth/IDP domain.

### Derived Conventions

- All auth model changes follow the Pydantic field pattern with type annotations and defaults
- Sensitive fields (tokens) use `Field(exclude=True)` — `extra_attributes` should be visible, not excluded
- The enterprise package boundary is strict: this repo treats `codemie_enterprise` as an opaque installed dependency and does not vendor its source

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/security/test_user_context.py` — 5 unit tests for `UserContext` and `from_user()`; a test explicitly asserts "all 12 non-sensitive fields" are mapped — **must be updated** to 13 when `extra_attributes` is added
- `tests/codemie/service/mcp/test_execution_context_integration.py` — integration tests for `get_mcp_server_tools()` propagating `user_context` through `MCPExecutionContext`
- `tests/codemie/service/mcp/test_models.py` — tests for `user_context` exclusion from `to_request_fields()` in the MCP wire protocol
- `tests/codemie/service/provider/test_provider_tool_factory.py` — tests that `user_context` appears in `ToolInvocationRequest` and that `auth_token`/`tenant_id` are excluded
- `tests/codemie/service/user/test_authentication_service.py` — full suite for `AuthenticationService` (1551 lines)
- `tests/codemie/rest_api/security/idp/test_base_idp.py` — `BaseIdp` abstract class tests
- `tests/codemie/rest_api/security/idp/test_jwks_validating.py` — `JwksValidatingIdp` tests

### Testing Framework and Patterns

- pytest (inferred from test file structure and `tests/` directory layout)
- Fixture-based setup inferred from `test_authentication_service.py` size and scope
- Mock patterns used in MCP execution context integration tests

### Coverage Gaps

- **`EnterpriseIdpWrapper` has zero unit tests** — the adapter bridge at `src/codemie/enterprise/idp/dependencies.py` (the most critical codemie-side fix point) has no corresponding test file under `tests/enterprise/idp/`
- **No tests for `IdpUser` → `User` field mapping** — the explicit field assignment block at lines 80-92 of `dependencies.py` is untested; new `extra_attributes` passthrough will also be untested without new tests
- **No enterprise IDP graceful degradation tests for IDP path** — `tests/enterprise/` covers Langfuse but not IDP; the `HAS_IDP` guard path for `extra_attributes` is not exercised
- **`provider_tool_factory.py` test** may need updating to assert `extra_attributes` is included when present and absent when `None`

---

## 5. Configuration and Environment

### Environment Variables

- No environment variables specific to JWT claim mapping or `extra_attributes` extraction were found
- IDP configuration is managed via application settings consumed by `IdpFactory` and the enterprise IDP providers

### Configuration Files

- IDP provider registration and configuration are managed through `src/codemie/rest_api/security/idp/factory.py` (`IdpFactory`) and `src/codemie/enterprise/idp/dependencies.py` (`register_enterprise_idps()`)
- No dedicated config file for which JWT claims to pass through — the `extra_attributes` design should pass through ALL unmapped claims without requiring configuration

### Feature Flags and Deployment Concerns

- `HAS_IDP` flag (derived from `try/except ImportError` of `codemie_enterprise`) gates all enterprise IDP logic — the `extra_attributes` change must remain behind this guard
- **Multi-repo deployment concern**: the fix spans two repositories (`codemie` and `codemie_enterprise`). The enterprise package must be updated and released/installed before the codemie-side fix will function. Deployment ordering matters.

---

## 6. Risk Indicators

- **Enterprise package not in this repository** — `_build_idp_user()` and `IdpUser` live in `codemie_enterprise`, a separate repo. This is Drop Point 1. The fix requires a coordinated two-repo change. The `codemie` side will compile and pass type checks even without the enterprise package update (since `IdpUser` is imported conditionally), but `extra_attributes` will be `None` at runtime until the enterprise package is updated and deployed.
- **`EnterpriseIdpWrapper` has no unit tests** — `src/codemie/enterprise/idp/dependencies.py` lines 80-92 (the primary codemie fix point) is completely uncovered. New tests must be written from scratch — no existing fixture or mock pattern to follow for this class.
- **`test_user_context.py` explicitly counts 12 fields** — the test docstring/assertion references "12 non-sensitive fields"; adding `extra_attributes` as field 13 will cause this test to fail or become misleading unless updated.
- **MCP wire protocol excludes `user_context`** — `MCPExecutionContext.to_request_fields()` deliberately excludes `user_context` per EPMCDME-13546. If the ticket requirement is for `extra_attributes` to reach remotely-hosted MCP-Connect tool servers (not just local in-process MCP execution), a separate change to the wire protocol is needed — this is a non-trivial scope expansion.
- **`roles: list` type inconsistency** — `User.roles` is bare untyped `list` while `UserContext.roles` is `list[str] | None`. The `extra_attributes` field should use `dict[str, Any] | None` consistently across both models; the existing type inconsistency is a signal that this area has had incremental additions without full type-safety review.
- **`model_dump(exclude_none=True)` in provider tool path** — at `provider_tool_factory.py:175`, using `None` as default for `extra_attributes` (rather than `{}`) ensures the field is omitted from serialization when absent, which is correct. If `{}` is used as default, it will appear in every serialized `ToolInvocationRequest` even when no extra attributes exist — a subtle behavioral change to downstream provider services.
- **No documentation for JWT claim passthrough pattern** — no guide, ADR, or inline comment explains the intended design. The `extra_attributes` implementation will set a new convention without documentation. `.ai-run/guides/development/security-patterns.md` should be updated post-implementation.
- **No `extra_attributes` or similar dict passthrough pattern exists anywhere in the codebase** — this is a genuinely new pattern; no reference implementation to follow for naming, typing, or serialization behavior.
- **x-userinfo header path shares the same `_build_idp_user()` entry point** — if the enterprise package fix targets only the JWT bearer token path and not the x-userinfo path, extra attributes from pre-parsed userinfo headers will still be dropped. The enterprise package fix must cover both code paths.

---

## 7. Summary for Complexity Assessment

This task spans two repositories and five architectural layers. Within the codemie repository, three files require direct modification: `src/codemie/rest_api/security/user.py` (add `extra_attributes` field to both `User` and `UserContext`, update `from_user()`), `src/codemie/enterprise/idp/dependencies.py` (forward `idp_user.extra_attributes` in the `User(...)` constructor), and the external `codemie_enterprise` package (`IdpUser` model and `_build_idp_user()` in Keycloak/OIDC providers). The two call sites of `UserContext.from_user()` in `toolkit_service.py` and `provider_tool_factory.py` require no code changes — they will pick up `extra_attributes` automatically once it is present in `UserContext`. Workflow context via the ContextVar also requires no additional propagation changes. Total in-repo file change surface: 2-3 files. Total cross-repo surface: 2 repositories, 3-4 additional files in the enterprise package.

The task introduces a new pattern — a passthrough `dict[str, Any]` field on identity models — that has no existing precedent in the codebase. However, the structural change is straightforward: a Pydantic optional field addition and a one-liner forwarding assignment at each layer boundary. The main technical novelty risk is the two-repo coordination and deployment ordering (enterprise package must ship first). A secondary risk is the MCP wire protocol gap: `user_context` (and therefore `extra_attributes`) is deliberately excluded from `MCPExecutionContext.to_request_fields()` per EPMCDME-13546, meaning the fix will NOT propagate `extra_attributes` to remotely-hosted MCP-Connect servers — only to local in-process MCP execution and the provider/DSP tool HTTP path.

Test coverage posture is mixed. The `UserContext.from_user()` path is well-tested in `test_user_context.py` but will require updates (the test explicitly counts 12 fields). The adapter bridge at `dependencies.py` — the most critical codemie fix point — has zero test coverage and will require new tests to be written from scratch. The enterprise package fix will need corresponding test updates in the enterprise repo. Overall complexity is moderate: the logic is simple but the multi-repo coordination, the new pattern, and the test gap in `EnterpriseIdpWrapper` elevate risk. A reviewer should pay particular attention to `None` vs `{}` as the default for `extra_attributes` (use `None`), consistent `dict[str, Any] | None` typing across both models, and whether the MCP wire-protocol exclusion is acceptable for this ticket's scope.
