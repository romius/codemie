# Skip Personal-Project Creation for External/Service-Account Users — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guard all 6 existing personal-project-creation call sites (5 in `codemie`, 1 in `codemie-enterprise`) so a user with `user_type` `"external"` or `"service_account"` never gets a personal project created or reconciled.

**Architecture:** Add one verbatim-duplicated predicate per repo (`is_personal_project_excluded(user_type) -> bool`), alongside each repo's own `VALID_USER_TYPES`. Guard-before-call at every site; no shared code between repos, no signature change to `ensure_personal_project_async` or `MigrationDeps`. Commit per task using the repository's existing convention.

**Tech Stack:** Python, pytest (`pytest.mark.asyncio`, `unittest.mock`), FastAPI service layer.

**Spec:** `/home/taras_spashchenko/EPAM/cm/codemie/docs/superpowers/tasks/2026-08-27-skip-private-project-creation/spec.md`

## Global Constraints

- `is_personal_project_excluded` is duplicated verbatim in both repos — no shared package, no cross-repo import.
- `User.is_external_user` (`codemie/src/codemie/rest_api/security/user.py:108`) is NOT touched.
- `ensure_personal_project_async(user_id, user_email)`'s signature is NOT extended — guard happens at each call site, before the call.
- `MigrationDeps.ensure_personal_project(self, user_id, email) -> None` protocol signature is NOT changed.
- No schema/data migration; no backfill/cleanup of pre-existing personal projects.
- No comment, docstring, or annotation anywhere about service-account access scope or privilege — skip/log text (where present) states only the `user_type` value and the fact it's excluded.
- Two independent repos, two independent branches, two independent commits per task. Never one task touching both.

## Negative constraints honored (self-check)

| Constraint | Honored by |
|---|---|
| `is_external_user` unchanged | No task modifies `user.py`. |
| `MigrationDeps` signature unchanged | Task 5 guards before `self._deps.ensure_personal_project(...)`, doesn't touch the protocol. |
| `ensure_personal_project_async` signature unchanged | Tasks 2–3 guard before the call; Task 4 changes only `reconcile_personal_project_on_email_change`'s signature (unconstrained). |
| No backfill/migration | No task includes one (Non-goal). |
| No service-account privilege/scope commentary | Explicit note in Task 5 step 3; no such text proposed anywhere else. |
| Site 5 skips BOTH halves | Task 4 guards before the old-project lookup, so neither soft-delete nor create runs. |
| No cross-repo import for the predicate | Tasks 1 and 5 each define their own copy; Task 5 does not import Task 1's. |
| No change to `User.current_project`/DEMO_PROJECT fallback | No task touches it. |
| No change to `create_user_from_idp` or flows beyond the creation call | Tasks only touch the specific guarded lines cited below. |

---

## Repo: codemie (base) — Tasks 1–4

### Task 1: Add `is_personal_project_excluded` predicate

**Files:**
- Modify: `src/codemie/rest_api/security/user_type_validator.py:29` (right after `VALID_USER_TYPES`)
- Test: `tests/codemie/rest_api/security/test_user_type_validator.py`

**Interfaces:**
- Produces: `PERSONAL_PROJECT_EXCLUDED_USER_TYPES: set[str]` and `is_personal_project_excluded(user_type: str) -> bool` in `codemie.rest_api.security.user_type_validator` — consumed by Tasks 2–4.

- [ ] Step 1: Write failing test — parametrize `is_personal_project_excluded` over `("external", True), ("service_account", True), ("regular", False), ("unknown", False)`.
- [ ] Step 2: Run `pytest tests/codemie/rest_api/security/test_user_type_validator.py -v` — expect FAIL (name not defined).
- [ ] Step 3: Implement, verbatim from the spec's pinned interface:
```python
PERSONAL_PROJECT_EXCLUDED_USER_TYPES = {"external", "service_account"}


def is_personal_project_excluded(user_type: str) -> bool:
    return user_type in PERSONAL_PROJECT_EXCLUDED_USER_TYPES
```
- [ ] Step 4: Re-run the test — expect PASS.

**Test-first:** yes — new parametrized test asserting `is_personal_project_excluded` returns `True` only for `"external"`/`"service_account"`, `False` otherwise.

---

### Task 2: Guard `AuthenticationService` call sites (1 & 2)

**Files:**
- Modify: `src/codemie/service/user/authentication_service.py:392` (`_finalize_authentication`), `:713-714` and `:732` (`authenticate_and_login`)
- Test: `tests/codemie/service/user/test_authentication_service.py`

**Interfaces:**
- Consumes: `is_personal_project_excluded(user_type: str) -> bool` (Task 1).

- [ ] Step 1: Write failing tests — for `_finalize_authentication`, parametrize `security_user_ins.user_type` over `{"external", "service_account"}` and assert `ensure_personal_project_async` is not called (still called for `"regular"`). Do the same for `authenticate_and_login`, parametrizing the mocked user's `user_type`.
- [ ] Step 2: Run the file — expect FAIL (calls happen unconditionally today).
- [ ] Step 3: Implement — import `is_personal_project_excluded`; wrap the line-392 call in `if not is_personal_project_excluded(security_user_ins.user_type):`. In `authenticate_and_login`, extract `user_type = user.user_type` next to the existing `user_id = user.id` / `user_email = user.email` capture (line ~713-714, before session close), then wrap the line-732 call the same way.
- [ ] Step 4: Re-run — expect PASS.

**Test-first:** yes — new parametrized tests: `ensure_personal_project_async` skipped for `external`/`service_account`, still called for `regular`, at both `_finalize_authentication` and `authenticate_and_login`.

---

### Task 3: Guard `RegistrationService` call sites (3 & 4)

**Files:**
- Modify: `src/codemie/service/user/registration_service.py:185-186` (capture `user_type` alongside `user_id`/`user_email`), `:211` (email-verification path), `:247` (instant-login path)
- Test: `tests/codemie/service/user/test_registration_service.py`

**Interfaces:**
- Consumes: `is_personal_project_excluded(user_type: str) -> bool` (Task 1).

- [ ] Step 1: Write failing tests — parametrize the registered `user.user_type` over `{"external", "service_account"}` and assert `ensure_personal_project_async` is not called on the email-verification path (line 211); repeat for the instant-login path (line 247). Add a `"regular"` case per path asserting the call still happens.
- [ ] Step 2: Run the file — expect FAIL.
- [ ] Step 3: Implement — after `user_id = user.id` / `user_email = user.email` (line ~185-186), add `user_type = user.user_type`; wrap both `ensure_personal_project_async` calls (lines 211, 247) in `if not is_personal_project_excluded(user_type):`.
- [ ] Step 4: Re-run — expect PASS.

**Test-first:** yes — new parametrized tests: `ensure_personal_project_async` skipped for `external`/`service_account` on both registration paths, still called for `regular`.

---

### Task 4: Site 5 — `reconcile_personal_project_on_email_change` skips both halves

Resolves the spec's open implementation decision: `user_type` arrives as a **new required parameter**, not an internal lookup — both existing callers already hold the user's row in scope right before calling reconcile, so passing the value costs nothing extra, while an internal lookup would re-query data the caller already has.

**Files:**
- Modify: `src/codemie/service/project/personal_project_service.py:110` (`reconcile_personal_project_on_email_change` signature + guard)
- Modify: `src/codemie/service/user/authentication_service.py:579` (caller inside `authenticate_persistent_user` — pass `security_user_ins.user_type`)
- Modify: `src/codemie/service/user/user_profile_service.py:162-172,201` (capture `db_user.user_type` alongside `old_email` at line 172; pass it at line 201)
- Test: `tests/codemie/service/project/test_personal_project_service.py:837-1030` (6 existing calls need the new arg; add new skip tests), `tests/codemie/service/user/test_authentication_service.py:918-920` (assertion needs the new arg), `tests/codemie/service/user/test_user_profile_service.py:455-457` (assertion needs the new arg)

**Interfaces:**
- Produces: `PersonalProjectService.reconcile_personal_project_on_email_change(user_id: str, old_email: str, new_email: str, user_type: str) -> bool` — signature change is local to this method; `ensure_personal_project_async` itself is untouched.
- Consumes: `is_personal_project_excluded(user_type: str) -> bool` (Task 1).

- [ ] Step 1: Write failing test — call `reconcile_personal_project_on_email_change(user_id, old_email, new_email, user_type="external")` (and `"service_account"`) with an existing old personal `Application` mocked; assert the old app is NOT soft-deleted (`deleted_at is None`, `session.add`/`aremove_project` not called) and `ensure_personal_project_async` is NOT called. Add alongside the existing reconcile test class.
- [ ] Step 2: Run `tests/codemie/service/project/test_personal_project_service.py` — expect FAIL (`TypeError`: no such parameter yet).
- [ ] Step 3: Implement:
  - Add `user_type: str` as the 4th parameter; as the first statement inside the `try:` block, `if is_personal_project_excluded(user_type): return False` — before the old-app lookup, so neither half runs.
  - At `authentication_service.py:579`, pass `security_user_ins.user_type` as the 4th argument.
  - At `user_profile_service.py`, capture `user_type = db_user.user_type` next to `old_email = db_user.email` (line 172), pass it as the 4th argument at line 201.
  - Update the 6 pre-existing calls in `test_personal_project_service.py` (lines 864, 897, 929, 961, 988, 1026) to pass `user_type="regular"`, and the two caller-test assertions (`test_authentication_service.py:918-920`, `test_user_profile_service.py:455-457`) to expect the extra argument — mechanical, preserves today's behavior for `regular`.
- [ ] Step 4: Re-run all three affected test files — expect PASS.

**Test-first:** yes — new test asserting `reconcile_personal_project_on_email_change` skips both the old-project soft-delete and the `ensure_personal_project_async` call when `user_type` is `"external"` or `"service_account"`.

---

## Repo: codemie-enterprise (overlay) — Task 5

### Task 5: Guard `KeycloakMigrationCoordinator._process_batch` Phase 3 (call site 6)

**Files:**
- Modify: `src/codemie_enterprise/idp/user_type.py:16` (right after its own `VALID_USER_TYPES`) — own verbatim copy, no import from `codemie`
- Modify: `src/codemie_enterprise/migration/coordinator.py:357-370` (Phase 3 loop)
- Test: `tests/migration/test_coordinator.py` (uses the `_make_kc_user(raw_attributes=...)` helper at lines 77-94)

**Interfaces:**
- Produces (enterprise-local only): `PERSONAL_PROJECT_EXCLUDED_USER_TYPES: set[str]` and `is_personal_project_excluded(user_type: str) -> bool` in `codemie_enterprise.idp.user_type` — same body as Task 1, duplicated, not imported.
- Consumes: `KeycloakAdminUser.user_type` property (`keycloak_admin_user.py:66`); `MigrationDeps.ensure_personal_project` (protocol unchanged).

- [ ] Step 1: Write failing tests, mirroring the existing `test_skips_personal_project_when_email_empty` pattern (lines 528-548): `_make_kc_user(raw_attributes={"user_type": ["external"]})` (and `["service_account"]`) run through `_run_migration()` → `deps.ensure_personal_project.assert_not_called()`. A user with no `user_type` attribute (defaults to `"regular"` per `validate_user_type`) still triggers the call.
- [ ] Step 2: Run `tests/migration/test_coordinator.py` — expect FAIL (calls happen unconditionally today).
- [ ] Step 3: Implement — add the predicate to `idp/user_type.py` (same body as Task 1's, adapted to this file's `VALID_USER_TYPES` location). In `_process_batch` Phase 3 (`coordinator.py:357-370`), after the existing `if not kc_user.email: ... continue` check, add `if is_personal_project_excluded(kc_user.user_type): continue` before `await self._deps.ensure_personal_project(...)`. If logging the skip, state only the `user_type` value and that the project was skipped — no wording about privilege or access scope.
- [ ] Step 4: Re-run — expect PASS.

**Test-first:** yes — new tests: `ensure_personal_project` not called for migrated users with `user_type` `"external"`/`"service_account"`, still called for `"regular"`.
