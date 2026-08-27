# Technical Research

**Task**: private-project creation user-type authentication
**Generated**: 2026-08-27T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

For users with user_type "external" or "service_account", the private project should not be created at all!

---

## 2. Codebase Findings

### Existing Implementations

**Base repo (`codemie`) — this is where private/personal project creation lives.**

- `PersonalProjectService.ensure_personal_project_async(user_id, user_email)` — `src/codemie/service/project/personal_project_service.py:44` — the single creation entry point. Idempotent: opens an isolated session, calls `_has_personal_project_complete` (line 146), and only if that returns `False` calls `_create_personal_project` (line 177) then commits. Catches all exceptions and returns `False` (non-blocking — never raises into the caller).
- `PersonalProjectService._create_personal_project(session, user_id, user_email)` — line 177 — creates (or converts) an `Application` row with `project_type=Application.ProjectType.PERSONAL`, `created_by=user_id`, `description=f"Personal Project for {user_email}"`, and the corresponding `user_projects` mapping with `is_project_admin=False`.
- `PersonalProjectService._has_personal_project_complete(session, user_id, user_email)` — line 146 — completeness check requiring both the `Application` row and the `user_projects` mapping.
- `PersonalProjectService.reconcile_personal_project_on_email_change(user_id, old_email, new_email)` — line 110 — soft-deletes the old personal project and calls `ensure_personal_project_async` for the new email.
- **Call sites of `ensure_personal_project_async`** (none pass or check `user_type`):
  - `AuthenticationService._finalize_authentication(security_user_ins, auth_source)` — `src/codemie/service/user/authentication_service.py:392` — called unconditionally after persistent-mode (IDP/dev-header) authentication, before loading the user's `project_names`/`admin_project_names`.
  - `AuthenticationService.authenticate_and_login(email, password)` — same file, line 732 — called unconditionally after every successful local email/password login.
  - `RegistrationService.register_user_with_flow(...)` — `src/codemie/service/user/registration_service.py:211` and `:247` — called unconditionally once for the email-verification path and once for the instant-login path.
  - `PersonalProjectService.reconcile_personal_project_on_email_change` — line 136 — called unconditionally on email change.
  - `CodemieMigrationDeps.ensure_personal_project(user_id, email)` — `src/codemie/enterprise/migration/coordinator.py:102` — the base repo's adapter implementation of the `MigrationDeps` protocol used by the enterprise Keycloak bulk-migration coordinator; delegates unconditionally to `ensure_personal_project_async`.
- `AuthenticationService.create_user_from_idp(session, idp_user)` — `src/codemie/service/user/authentication_service.py:180` — creates the `UserDB` row (with `user_type=idp_user.user_type`) on first IDP login; does **not** itself create a personal project (comment at line 235: "Story 9: Personal project creation deferred until after commit").
- `User.is_external_user` — `src/codemie/rest_api/security/user.py:108` — `return self.user_type == config.EXTERNAL_USER_TYPE`. Not referenced anywhere in the personal-project creation call path.
- `VALID_USER_TYPES = {'regular', 'external', 'service_account'}` — `src/codemie/rest_api/security/user_type_validator.py:29`.
- `UserDB.user_type: str = SQLField(default="regular")` — `src/codemie/rest_api/models/user_management.py:49` — plain string column, no DB enum/CHECK constraint.

**Enterprise repo (`codemie-enterprise`) — private-project creation logic does NOT live here, but the migration flow triggers it symmetrically and unconditionally.**

- `KeycloakMigrationCoordinator._process_batch(batch, admin_user_ids, stats)` — `src/codemie_enterprise/migration/coordinator.py:314` — Phase 3 (lines 357-370): for every newly-created (`result.status == "created"`) migrated user with a non-empty email, calls `self._deps.ensure_personal_project(kc_user.id, kc_user.email)` **unconditionally** — no `user_type` check. (Users with no email are skipped for a different reason: "Personal project will be created on first login.")
- `KeycloakMigrationCoordinator._migrate_single_user(session, kc_user, admin_user_ids)` — line 245 — reads and validates `kc_user.user_type` (line 275) and stores it on the created user record (line 292), but the value is never used to decide whether a personal project should later be created.
- `KeycloakAdminUser.user_type` — `src/codemie_enterprise/migration/keycloak_admin_user.py:66` — validated via `validate_user_type` (this repo's own copy).
- `validate_user_type` / `VALID_USER_TYPES = {"regular", "external", "service_account"}` — `src/codemie_enterprise/idp/user_type.py:16` and `:41` — deliberately duplicated copy (zero-coupling IdP design), consistent with the base repo's set.
- `MigrationDeps` Protocol — `src/codemie_enterprise/migration/coordinator.py:43` — declares `ensure_personal_project(self, user_id, email) -> None` (line 100) with no `user_type` parameter; the base repo's `CodemieMigrationDeps` is the only implementer found.

### Architecture and Layers Affected

- **Service layer** (base repo): `PersonalProjectService`, `AuthenticationService`, `RegistrationService` — `src/codemie/service/...`.
- **Cross-repo integration layer**: `MigrationDeps` protocol (enterprise) implemented by `CodemieMigrationDeps` (base) — `src/codemie/enterprise/migration/coordinator.py` / `src/codemie_enterprise/migration/coordinator.py`. Business logic (`KeycloakMigrationCoordinator`) lives in the enterprise package; DB-specific wiring lives in the base package.
- **Security/authentication-context layer**: `User` (security context model, `src/codemie/rest_api/security/user.py`) and `UserDB` (persistence model, `src/codemie/rest_api/models/user_management.py`) both carry `user_type`, but neither is threaded into the personal-project creation calls (which take only `user_id`/`email`).
- **Data model layer**: `Application` (project record) and `UserProject` (membership mapping) — created by `_create_personal_project`.

### Integration Points

- `ensure_personal_project_async` opens its own isolated DB session (`get_async_session`) separate from the caller's transaction, by design, so its failures don't roll back authentication/registration.
- The enterprise migration coordinator depends on the base repo only through the `MigrationDeps` protocol — no direct import of base-repo types, consistent with the "zero coupling" IdP design mentioned in the task context.
- `_finalize_authentication` also reloads `project_names`/`admin_project_names`/`knowledge_bases` from `user_project_repository`/`user_kb_repository` right after the `ensure_personal_project_async` call, so whatever this call does or skips is immediately reflected in the `User` object returned to the request.

### Patterns and Conventions

- Idempotent "check-then-create" pattern (`_has_personal_project_complete` → `_create_personal_project`) used consistently, wrapped in try/except that logs and returns `False` rather than raising (non-blocking authentication).
- `VALID_USER_TYPES` and `validate_user_type` are deliberately duplicated verbatim between the two repos rather than shared, per the existing zero-coupling IdP design noted in prior work on this branch.
- Static-method service classes (`PersonalProjectService`, `AuthenticationService`, `RegistrationService`) with module-level singleton instances (e.g. `personal_project_service = PersonalProjectService()`), imported lazily inside functions to avoid circular imports.

---

## 3. Documentation Findings

### Guides and Architecture Docs

`.ai-run/guides/` contains general architecture/service-layer/security guides (`architecture/layered-architecture.md`, `architecture/service-layer-patterns.md`, `development/security-patterns.md`) but none specifically documents personal/private project provisioning or user_type gating rules. No guide file matched "private project" or "personal project" by name.

### Architectural Decisions

- Inline comments in `personal_project_service.py` reference "FR-7.1 Personal Project Rules" and "Story 9" (project name = email, `project_type='personal'`, `created_by=user_id`, member with `is_project_admin=false`, non-blocking).
- Inline comment in `authentication_service.py:196`: "Determine if IDP user should be admin (legacy logic)" — unrelated to user_type but shows other user_type-adjacent legacy logic exists nearby.
- No ADR or guide found stating a rule about excluding `external`/`service_account` users from personal-project creation — this task introduces that rule; it is not already documented anywhere in either repo.

### Derived Conventions

- Skip/gating logic elsewhere in the codebase (e.g. `is_external_user`) is expressed as a `User`-model property computed from `user_type`, then checked by callers before taking an action — the closest existing convention for "checking user_type before doing X" — but no direct precedent for skipping *inside* a service call that only receives `user_id`/`email`.

---

## 4. Testing Landscape

### Existing Coverage

**Base repo:**
- `tests/codemie/service/project/test_personal_project_service.py` — covers `ensure_personal_project_async` (creation, idempotent skip, non-blocking failure, PII-safe logging, isolated transaction), `_has_personal_project_complete`, `_create_personal_project` (correct fields, idempotency, unsafe-shared-conversion guard, email-as-name, description), and the first-time registration flow. **No test parametrizes by `user_type`.**
- `tests/codemie/service/user/test_authentication_service.py` — covers `authenticate_and_login`, `create_user_from_idp`, and related flows (referenced by blast radius; not fully read).
- `tests/codemie/service/user/test_registration_service.py` — covers `register_user_with_flow`.
- `tests/codemie/rest_api/security/test_user_type_validator.py` — covers `validate_user_type`/`VALID_USER_TYPES` normalization and rejection, not project creation.
- Enterprise-adapter tests referencing `ensure_personal_project` delegation: `test_ensure_personal_project_delegates_correctly` (file path shown as `tests/enterprise/migration/test_coordinator.py` under the base repo tree per blast-radius data — verifies `CodemieMigrationDeps.ensure_personal_project` calls `ensure_personal_project_async`, with no `user_type` involved).

**Enterprise repo:**
- `tests/migration/test_coordinator.py` — covers `KeycloakMigrationCoordinator.run`, `_migrate_single_user`, `_process_batch`. No test found asserting a user_type-based skip of `ensure_personal_project`.
- `tests/idp/test_entraid_oidc_idp.py`, `tests/idp/test_keycloak_idp.py`, `tests/migration/test_keycloak_admin_user.py` — cover `validate_user_type`/`InvalidUserTypeError`, not project creation.

### Testing Framework and Patterns

pytest with `pytest.mark.asyncio` for async service methods; heavy use of `unittest.mock.patch`/`AsyncMock` to mock repositories and sessions (`_make_async_session_cm` helper builds an async context-manager mock). Enterprise coordinator tests mock the `MigrationDeps` protocol directly.

### Coverage Gaps

- No existing test in either repo exercises personal-project creation (or skipping it) for `external` or `service_account` users specifically.
- No test covers the enterprise `_process_batch` Phase 3 personal-project creation with a non-`regular` `user_type`.

---

## 5. Configuration and Environment

### Environment Variables

- No dedicated environment variable controls personal-project creation. `config.EXTERNAL_USER_TYPE` (referenced by `User.is_external_user`, value not located in the explored slice) is the only user_type-related config constant found; `ENABLE_USER_MANAGEMENT` gates the broader user-management feature but not personal-project creation specifically.

### Configuration Files

- None found specific to personal/private project provisioning.

### Feature Flags and Deployment Concerns

- None found. Personal project creation is unconditional application logic, not behind a feature flag.

---

## 6. Risk Indicators

- No existing gating logic anywhere in the creation path — `ensure_personal_project_async(user_id, user_email)` and all 5 call sites (`_finalize_authentication`, `authenticate_and_login`, `register_user_with_flow` ×2, `reconcile_personal_project_on_email_change`) plus enterprise `_process_batch` Phase 3 never receive or check `user_type`.
- `is_external_user` (`user.py:108`) returns `False` for `service_account` and must not change per task constraint; a new predicate must check `user_type` directly (e.g. against `{"external", "service_account"}`).
- Symmetric gap in enterprise `KeycloakMigrationCoordinator._process_batch` (`coordinator.py:357-370`) — creates personal projects unconditionally for migrated users regardless of `user_type`, even though `user_type` is already validated/stored per-user there.
- Existing external/service_account users likely already have personal projects, since creation has run unconditionally to date; no cleanup/backfill path exists in either repo — cleanup scope is a spec question, not derivable from code.
- Downstream consumers only partially traced: confirmed `User.current_project` (`user.py:112-115`) falls back to `DEMO_PROJECT` when `project_names` is empty; seat/licensing, ACL, and UI-listing consumers were not located within the research budget.
- Zero test coverage for a user_type-based skip in `test_personal_project_service.py` (base) or `test_coordinator.py` (enterprise) — greenfield test surface.
- No schema/migration involved — `UserDB.user_type` is already an unconstrained string column; this is a pure service/business-logic change.

---

## 7. Summary for Complexity Assessment

This task touches the service layer in both repos: `PersonalProjectService.ensure_personal_project_async` (base repo) and its five call sites across `AuthenticationService`, `RegistrationService`, and `PersonalProjectService` itself, plus `KeycloakMigrationCoordinator._process_batch` Phase 3 in the enterprise repo. No schema/migration layer is implicated — `user_type` is already an unconstrained string column on `UserDB`.

The change is conceptually simple (a user_type check before a service call) but structurally scattered: no gating exists today anywhere in the creation path, and `ensure_personal_project_async`'s signature (`user_id`, `user_email` only) carries no `user_type`, so the fix must either extend that signature or duplicate the check at each call site across two independently-versioned repos — consistent with this branch's existing zero-coupling IdP design, which already duplicates `VALID_USER_TYPES`/`validate_user_type` verbatim between repos. `is_external_user` cannot be reused as-is: it excludes `service_account` by design and must not change.

Test coverage for this exact behavior is zero in both repos — every existing personal-project and migration test exercises only `regular`-shaped users. The main open risks carried to design/spec are a data-state question (whether already-provisioned personal projects for existing external/service_account users need cleanup) and an incompletely-traced downstream consumer surface beyond the one confirmed consumer, `User.current_project`'s `DEMO_PROJECT` fallback.

---

## 8. External References

None named by the task.
