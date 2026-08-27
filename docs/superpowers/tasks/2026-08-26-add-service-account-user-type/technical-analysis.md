# Technical Research

**Task**: user_type idp keycloak jwt claim user-creation
**Generated**: 2026-08-26
**Research path**: codegraph

---

## 1. Original Context

These are the possible user_type values now:
VALID_USER_TYPES = {"regular", "external"}
See `/home/taras_spashchenko/EPAM/cm/codemie-enterprise/src/codemie_enterprise/idp/user_type.py`

We need to add a new user_type value "service_account".
So, when a new user record is created and stored in a CodeMie DB, the user_type value is set to "service_account" when this value comes from the Keyclock JWT claim.

Your ticket number is EPMCDME-14413

---

## 2. Codebase Findings

### Existing Implementations

**Base repo (`codemie`)**
- `src/codemie/rest_api/security/user_type_validator.py` — `VALID_USER_TYPES = {"regular", "external"}` and `validate_user_type(value, idp_context=None) -> str`. `None` → `"regular"`; non-`str` or unrecognized value → `ExtendedHTTPException(code=401, ...)` with sanitized `idp_context` logging (provider/source/subject_hash, no PII). Docstring ties this to "Story 4: EPMCDME-10160".
- `tests/codemie/rest_api/security/test_user_type_validator.py` — 23 tests in `TestValidateUserType`, including `test_valid_user_types_constant` which asserts `{'regular','external'} == VALID_USER_TYPES` and `len(VALID_USER_TYPES) == 2`.
- `src/codemie/rest_api/models/user_management.py` — `UserDB(BaseModelWithSQLSupport, table=True)`, table `users`: `user_type: str = SQLField(default="regular")` — plain string column, no enum/CHECK constraint, comment `# 'regular' | 'external'` only. Also DTOs carrying `user_type: str`: `CodeMieUserDetail`, `AdminUserListItem` (required), `UserUpdateRequest` (`Optional[str]`, docstring: "Conditional - editable only in local mode (IDP mode: rejected)", Story 8), `UserListFilters` (`Optional[str]` filter).
- `src/codemie/rest_api/security/user.py` — `User(BaseModel)`: `user_type: str | None = 'regular'`; `is_external_user` property returns `self.user_type == config.EXTERNAL_USER_TYPE`. `UserContext(BaseModel)` also carries `user_type: str | None`, built via `UserContext.from_user(user)`.
- `src/codemie/service/user/authentication_service.py` — `create_user_from_idp(session, idp_user)` builds `UserDB(... user_type=idp_user.user_type, auth_source=config.IDP_PROVIDER, ...)` on first login and persists via `user_repository.acreate`. `sync_idp_user_profile(session, db_user, idp_user)` re-syncs `email`, `name`, `picture` on every subsequent login but does **not** re-sync `user_type`.
- `src/codemie/repository/user_repository.py` — `acreate`, `aupdate` (generic `setattr` loop guarded by `hasattr`, no field-level validation).
- `src/codemie/rest_api/security/idp/factory.py` — `IdpFactory._idp_registry` registers only `{IdentityProvider.LOCAL: LocalIdp}` in the base repo; wraps created instances with `JwksValidatingIdp` when `config.JWKS_VALIDATION_ENABLED` and the instance isn't `LocalIdp`.
- `src/codemie/enterprise/loader.py` — safe-import bridge: `try/except ImportError` around `from codemie_enterprise.idp import (..., validate_user_type as enterprise_validate_user_type)`, exposing `HAS_IDP` flag so the base app runs without the enterprise package installed.
- `src/codemie/configs/config.py:304` — `EXTERNAL_USER_TYPE: str = "external"` (plain config default, no enum).

**Enterprise repo (`codemie-enterprise`)**
- `src/codemie_enterprise/idp/user_type.py` — independent `VALID_USER_TYPES = {"regular", "external"}` and `validate_user_type(value, idp_context=None) -> str`, same rules as the base validator (`None`→`"regular"`, `.strip().lower()`, membership check) but raises `InvalidUserTypeError(ValueError)` instead of an HTTP exception. Docstring states "zero coupling to codemie.*".
- `src/codemie_enterprise/idp/keycloak.py` — `KeycloakIdpProvider`. `_from_user_info_header` (reads `x-userinfo` header) and `_from_oauth_token` (reads bearer token via `jwt.decode(access_token, options={"verify_signature": False})`) both call `_build_idp_user(...)`, which does `user_type = validate_user_type(claims.get("user_type"), idp_context={"provider": "keycloak", ...})` — the literal claim key read is `"user_type"`.
- `src/codemie_enterprise/idp/oidc.py`, `src/codemie_enterprise/idp/entraid_oidc.py` — same pattern, `payload.get("user_type")`.
- `src/codemie_enterprise/idp/models.py` — `IdpUser` frozen dataclass, `user_type: str = "regular"`.
- `src/codemie_enterprise/idp/jwks/validator.py` — `TokenSignatureValidator`, independent of claim extraction; verifies signature/`iss`/`aud`/`exp`/`kid` via JWKS. This runs separately from (and before, when enabled) the unverified `jwt.decode` calls above that read `user_type`.
- `src/codemie_enterprise/migration/keycloak_admin_user.py` — `KeycloakAdminUser.user_type` property reads `raw_attributes.get("user_type")` (a list; `[0]` used) and calls the same `validate_user_type`; used for bulk Keycloak Admin API user migration, not the runtime login path.
- `src/codemie_enterprise/migration/coordinator.py` — `MigrationDeps(Protocol)` declares `async def create_user(self, session, user_data: dict) -> None`; the concrete implementation of this protocol was not located in this research pass (see Section 6).

### Architecture and Layers Affected
- **Enterprise IDP layer** (`codemie_enterprise.idp.*`) — claim extraction (Keycloak/OIDC/EntraID providers) and JWT signature verification (`jwks/validator.py`).
- **Validation layer** — two independent implementations: `codemie.rest_api.security.user_type_validator` (base) and `codemie_enterprise.idp.user_type` (enterprise).
- **Domain/DTO layer** — `IdpUser` (enterprise dataclass), `User`/`UserContext` (base Pydantic models).
- **Persistence layer** — `UserDB` SQLModel table (`users`), unconstrained string column.
- **Service/orchestration layer** — `AuthenticationService` (base) creates and syncs the DB record from the IDP-derived user.
- **API/admin layer** — `UserUpdateRequest`, `UserListFilters`, `AdminUserListItem`, `CodeMieUserDetail` DTOs surface `user_type` to admin endpoints/responses.

### Integration Points
- Enterprise IDP providers are registered into the base repo's `IdpFactory` registry at startup (registry defined in base `factory.py`; registration calls originate from the enterprise side per the `IdpFactory.register()` API, not directly inspected in this pass).
- `src/codemie/enterprise/loader.py` is the sole import bridge between the two packages; it exposes `enterprise_validate_user_type` as an alias, but no caller of this alias was found in the base repo during this research pass.
- No Elasticsearch index or document field referencing `user_type` was found in either repo during this research pass.
- No budget/quota/licensing logic branching on `user_type` was found; `BudgetConfig`/`BudgetUsageService` operate on budget categories and user IDs, not `user_type`.

### Patterns and Conventions
- Both repos independently implement identical normalization rules (`None`→`"regular"`, case-insensitive `.strip().lower()`, set-membership check) rather than sharing code — the enterprise repo's stated design principle (see Section 3) is zero coupling to `codemie.*`.
- `idp_context` dict (`provider`/`source`/`subject_id_hash`) is passed through both validators for structured, PII-free error logging.
- `UserDB` follows a plain-string-with-comment convention for enum-like fields (`user_type`, `auth_source` both use this pattern — neither has a DB-level constraint).

---

## 3. Documentation Findings

### Guides and Architecture Docs
- `/home/taras_spashchenko/EPAM/cm/codemie/.ai-run/guides/development/security-patterns.md` — covers authentication dependency usage, secret handling, and untrusted-input handling; does not mention `user_type` or IDP claim enumeration specifically.
- `/home/taras_spashchenko/EPAM/cm/codemie-enterprise/.ai-run/guides/architecture/architecture.md` — explicitly documents the "Zero Coupling" rule ("The enterprise package must not import from `codemie.*`. Core integrations flow through injected config and dependencies") and the "Feature Module Shape" / "Constructor Injection" conventions. This corroborates the code-level finding that the enterprise `user_type.py` is an independent implementation, not an import of the base validator.
- Remaining guide files (39 in `codemie`, 13 in `codemie-enterprise`) were enumerated via Glob but not opened in this pass; none of their filenames suggest dedicated user-type/IDP-claim documentation beyond what was read.

### Architectural Decisions
- Both validator modules' docstrings/tests reference "Story 4: EPMCDME-10160" as the historical decision that introduced the `regular`/`external` two-value set.
- `UserUpdateRequest`'s docstring documents "Story 8": `user_type` is editable via the admin API only in local mode; IDP mode rejects `user_type` edits.

### Derived Conventions
- The zero-coupling boundary between `codemie` and `codemie-enterprise` is both documented (architecture.md) and confirmed in code (duplicate validator implementations, `enterprise/loader.py` try/except bridge).

---

## 4. Testing Landscape

### Existing Coverage
- `tests/codemie/rest_api/security/test_user_type_validator.py` (base, 229 lines) — 23 tests in `TestValidateUserType` covering valid values, case normalization, whitespace, `None`-default, invalid types/values, error message content, logging (`@patch('codemie.rest_api.security.user_type_validator.logger')`), and the constant itself (`test_valid_user_types_constant`, asserts exactly `{'regular','external'}`, `len == 2`).
- `tests/idp/test_keycloak_idp.py` (enterprise) — `TestKeycloakIdpFromUserInfoHeader`, `TestKeycloakIdpFromOAuthToken`, `TestKeycloakIdpAuthenticate`. Key tests: `test_all_optional_fields` (asserts `idp_user.user_type == "external"`), `test_without_user_type_defaults_to_regular` (both header and OAuth variants), `test_invalid_user_type_rejects` (both variants, expects `InvalidUserTypeError`).
- `tests/migration/test_keycloak_admin_user.py` (enterprise) — `TestKeycloakAdminUserUserTypeProperty`: `test_valid_external_type`, `test_valid_regular_type`, `test_absent_user_type_defaults_to_regular`, `test_empty_list_user_type_defaults_to_regular`, `test_invalid_user_type_raises`.

### Testing Framework and Patterns
- `pytest` with `unittest.mock.patch` for logger/dependency mocking; class-based grouping by behavior (`TestValidateUserType`, `TestKeycloakIdpFrom...`), one assertion focus per test method, docstrings tagged with `AC:` (acceptance criterion) references in the base validator tests.

### Coverage Gaps
- No test exercises `AuthenticationService.create_user_from_idp` end-to-end with a non-`regular`/`external` `user_type` value.
- No test in either repo currently exercises a third user_type value (expected, since only two currently exist).
- No test was found for `KeycloakMigrationCoordinator`'s `create_user` path with an alternate `user_type`.
- No test was found for `enterprise_validate_user_type` (the alias exposed by `src/codemie/enterprise/loader.py`) — consistent with no caller of that alias having been located.

---

## 5. Configuration and Environment

### Environment Variables
- `IDP_PROVIDER: Literal["keycloak","local","oidc","entraid-oidc"] = "local"` (`config.py:181`) — selects which IDP provider `IdpFactory` instantiates.
- `ENABLE_USER_MANAGEMENT: bool = False` (`config.py:194`) — gates whether user records are DB-backed vs. JWT-only.
- `JWKS_VALIDATION_ENABLED: bool = False` (`config.py:232`) and related `JWKS_TRUSTED_ISSUERS`, `JWKS_CACHE_TTL_SECONDS`, etc. — gate cryptographic verification wrapping around non-local IDP instances.
- `EXTERNAL_USER_TYPE: str = "external"` (`config.py:304`) — the config value `User.is_external_user` compares against; not itself an enum of allowed values.
- `KEYCLOAK_MIGRATION_ENABLED`, `KEYCLOAK_ADMIN_URL`, `KEYCLOAK_ADMIN_REALM`, `KEYCLOAK_ADMIN_CLIENT_ID/SECRET` (`config.py:208-215`) — govern the Keycloak Admin API migration path that also reads `user_type` via `KeycloakAdminUser`.

### Configuration Files
- `src/codemie/configs/config.py` (base) — single `Config(BaseSettings)` class, ~960 lines, no separate enum/constants file for `user_type`.
- No enterprise-side config file defines or overrides `VALID_USER_TYPES`; the enterprise `user_type.py` module hardcodes its own set.

### Feature Flags and Deployment Concerns
- `JWKS_VALIDATION_ENABLED` is an opt-in defence-in-depth flag; when off, `user_type` (and all other claims) is read from an unverified `jwt.decode(..., verify_signature=False)` call, with actual signature verification (when enabled) happening in a separate wrapper (`JwksValidatingIdp`) around the IDP instance.
- No feature flag currently gates which `user_type` values are accepted — acceptance is purely a function of the hardcoded `VALID_USER_TYPES` set in each repo's validator module.

---

## 6. Risk Indicators

- **Duplicate validator logic across repos**: `codemie.rest_api.security.user_type_validator.VALID_USER_TYPES` (base) and `codemie_enterprise.idp.user_type.VALID_USER_TYPES` (enterprise) are two independently maintained, textually near-identical sets, not shared code. Speculative: keeping the two repos consistent for a new value will require changing both files, and any divergence would let one provider path accept a value the other rejects.
- **Existing constant-shape test asserts exactly two values**: `tests/codemie/rest_api/security/test_user_type_validator.py::test_valid_user_types_constant` asserts `len(VALID_USER_TYPES) == 2`. Speculative: this assertion will fail as soon as a third value is added and will need updating.
- **`sync_idp_user_profile` does not re-sync `user_type` after first login** (confirmed current behavior, `authentication_service.py`) — a returning user's stored `user_type` will not change even if a later JWT presents a different claim value; this is a pre-existing behavior of the persistence/sync path, independent of adding a new value.
- **`UserDB.user_type` is an unconstrained plain string column** (`user_management.py:49`), with no DB-level enum or CHECK constraint, and no Alembic migration currently defines allowed values. Speculative: no migration is needed to store a new string value today, but if constraint enforcement is later added at the DB layer, a migration would be required then.
- **`MigrationDeps.create_user` (enterprise `coordinator.py` Protocol) implementation not located** in this research pass — the base-repo-side concrete class implementing this Protocol (referenced by blast-radius metadata as living under an `enterprise/migration/coordinator.py`-shaped path) was not opened; its handling of `user_type` for bulk-migrated users is unverified.
- **`enterprise_validate_user_type` alias (`src/codemie/enterprise/loader.py`) has no located caller** in the base repo — unclear whether this is dead code, a planned integration point, or simply not reachable via the search terms used.
- **`User.is_external_user` callers not exhaustively traced** — this is the only confirmed user_type-based behavioral branch in the base repo; whether any downstream permission, licensing, or marketplace logic keys off it (beyond the property definition itself) was not fully confirmed.
- **`codemie/enterprise/idp/dependencies.py`, referenced in `IdpUser`'s docstring as the integration layer mapping `IdpUser`→`User`, was not directly opened** in this pass; the actual mapping code path from enterprise `IdpUser.user_type` to base `User.user_type`/`UserDB.user_type` is inferred from `AuthenticationService.create_user_from_idp` rather than confirmed at that specific file.
- **`UserUpdateRequest` restricts `user_type` edits to local mode only** ("IDP mode: rejected" per its docstring, Story 8) — a pre-existing constraint on the admin-facing update path, relevant to how a `service_account` value could or could not be set/changed via the admin API.
- No codegraph results confirming any Elasticsearch document field or analytics/reporting aggregation keyed on `user_type` — treated as a true absence based on searches performed, but not exhaustively ruled out via direct file enumeration of the analytics/ES layer.

---

## 7. Summary for Complexity Assessment

This task touches four layers spanning two repositories: the enterprise IDP claim-extraction layer (`codemie_enterprise.idp.keycloak/oidc/entraid_oidc.py`, all reading a literal `"user_type"` JWT/attribute claim), a validation layer duplicated across both repos (`codemie.rest_api.security.user_type_validator` and `codemie_enterprise.idp.user_type`, independently defining `VALID_USER_TYPES` with identical normalization rules but different exception types), the base repo's persistence/service layer (`UserDB` SQLModel column with no DB constraint, `AuthenticationService.create_user_from_idp`), and several DTOs that surface `user_type` in admin APIs (`CodeMieUserDetail`, `AdminUserListItem`, `UserUpdateRequest`, `UserListFilters`). The file-change surface for the core value addition is small and well-isolated — two `VALID_USER_TYPES` constants plus their associated docstrings — but the duplication itself is a structural fact of this codebase's IDP zero-coupling design, not a symptom of poor factoring, and it will affect the number of change sites and test files an implementer must touch symmetrically in both repos.

Technical novelty is low: this is an additive change to an already-established validation pattern (add a value to a set, no new abstraction, no new column type). No DB-level constraint or migration currently exists to change — `user_type` is a plain string column — which somewhat reduces mechanical complexity, though it also means the schema currently provides no enforcement backstop beyond the two validator functions.

Test coverage for the existing two-value behavior is thorough on both sides (23 parametrized-style tests in the base validator suite; dedicated Keycloak/migration test classes in the enterprise repo), but one existing assertion (`test_valid_user_types_constant`, asserting `len == 2`) is written in a way that hardcodes the current cardinality and will need direct attention. Key risk factors are the untraced downstream consumers (`enterprise_validate_user_type` alias with no found caller, `User.is_external_user` callers not exhaustively traced, and the base-repo implementation of `MigrationDeps.create_user` not located) — these represent genuine unknowns about whether a third `user_type` value could interact with permission, licensing, or bulk-migration logic beyond the validation layer itself.

---

## 8. External References

- **`/home/taras_spashchenko/EPAM/cm/codemie-enterprise/src/codemie_enterprise/idp/user_type.py`** — explicitly named by the task as the current source of truth for `VALID_USER_TYPES`. Resolved and read in full (91 lines). Facts an implementer needs, quotable without re-opening the file:
  - `VALID_USER_TYPES = {"regular", "external"}` (module-level set).
  - `class InvalidUserTypeError(ValueError)` with `__init__(self, value, detail, help_text)`, storing `self.value`, `self.detail`, `self.help_text`.
  - `def validate_user_type(value: Any, idp_context: dict | None = None) -> str:` — `value is None` → returns `"regular"`; non-`str` → logs and raises `InvalidUserTypeError`; else `normalized = value.strip().lower()`, returns `normalized` if `normalized in VALID_USER_TYPES`, otherwise logs and raises `InvalidUserTypeError` with a help text of "Contact your administrator...".
  - Module docstring states "zero coupling to codemie.*" and documents the same three behaviors (accept-normalize, default-to-regular-on-missing, fail-closed-on-invalid) that the task's requested change must preserve for any new value.

---
