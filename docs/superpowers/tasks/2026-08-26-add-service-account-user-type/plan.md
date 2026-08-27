# Add "service_account" user_type Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accept `"service_account"` as a third valid `user_type` value in both repos' independent validators, with identical accept-normalize / default-to-`"regular"` / fail-closed behavior, and confirm it persists and is not treated as external.

**Architecture:** Additive change to two duplicated (intentionally zero-coupled) `VALID_USER_TYPES` sets — one per repo — plus their docstrings and dependent tests. No new abstraction, no schema change.

**Tech Stack:** Python, pytest, FastAPI (base repo), SQLModel.

**Spec:** `docs/superpowers/tasks/2026-08-26-add-service-account-user-type/spec.md`

## Global Constraints

- Ticket EPMCDME-14413. Two separate git repos, both already on branch `EPMCDME-14413`; **commit per task using the repository's existing convention, and never mix repos in one commit.**
- Preserve each repo's own exception type: `ExtendedHTTPException` (base), `InvalidUserTypeError` (enterprise).
- No DB migration, no enum refactor, no shared-constant extraction across the repo boundary, no `sync_idp_user_profile` change, no admin-API editability change, no authorization/licensing/budget/analytics change, no `is_external_user` semantic change, no UI/notification filtering.

---

### Task 1 — Base: extend the validator and its tests

**Repo:** `codemie` (`/home/taras_spashchenko/EPAM/cm/codemie`)
**Files:**
- Modify: `src/codemie/rest_api/security/user_type_validator.py:29` (`VALID_USER_TYPES`), `:15-20` (module docstring), `:37`, `:45` (function docstring) — add `"service_account"` to the set and to the three enumerated-value mentions. Do not touch the runtime error-message strings (`:79`, `:100-102`).
- Modify: `tests/codemie/rest_api/security/test_user_type_validator.py:205-208` (`test_valid_user_types_constant`) — replace with `assert VALID_USER_TYPES == {"regular", "external", "service_account"}` (drop the `len == 2` assertion).
- Modify: same file — add three tests mirroring `test_validate_external_lowercase`/`_uppercase`/`_with_whitespace` (lines 45/55/70) for `"service_account"`, `"SERVICE_ACCOUNT"`, and `"  Service_Account  "`, each asserting the result is `"service_account"`.

**Test-first:** yes — the rewritten `test_valid_user_types_constant` and the three new acceptance tests fail against the current two-value `VALID_USER_TYPES` (membership/exact-set mismatch) until the constant is updated in this same task.

- [ ] Write the rewritten constant test and the three new `"service_account"` acceptance tests; run `pytest tests/codemie/rest_api/security/test_user_type_validator.py -k service_account or test_valid_user_types_constant` and confirm they fail.
- [ ] Add `"service_account"` to `VALID_USER_TYPES` and update the two docstrings' enumerated-value text; rerun the same tests and confirm they pass, then run the full file to confirm the other 23 pre-existing tests (regular/external/None/invalid/logging) are untouched and still pass.
- [ ] Commit.

---

### Task 2 — Base: `is_external_user` regression test

**Repo:** `codemie`
**Files:**
- Modify: `tests/codemie/rest_api/security/test_user.py` — add `test_is_external_user_when_user_type_service_account` to `TestIsExternalUser` (after line 112), mirroring `test_is_external_user_when_user_type_internal`: `User(id="test", username="test", user_type="service_account").is_external_user is False`.

**Test-first:** no — `is_external_user` (`src/codemie/rest_api/security/user.py:108`) already only compares against `config.EXTERNAL_USER_TYPE` (`"external"`), so this passes without any production change; it records the spec's acceptance criterion as a permanent regression test. No production file is touched.

- [ ] Add the test; run `pytest tests/codemie/rest_api/security/test_user.py -k service_account` and confirm it passes as-is.
- [ ] Commit.

---

### Task 3 — Base: first-login persistence regression test

**Repo:** `codemie`
**Files:**
- Modify: `tests/codemie/service/user/test_authentication_service.py` — add a test to `TestCreateUserFromIdp` (class starts line 349) mirroring `test_create_user_from_idp_admin_role` (line 446): build a `security_user.User` idp_user with `user_type="service_account"`, set `mock_user_repo.acreate = AsyncMock(side_effect=lambda sess, user: user)`, call `await AuthenticationService.create_user_from_idp(session, idp_user)`, capture the `UserDB` passed as the second positional arg to `acreate`, and assert `created_user.user_type == "service_account"`.

**Test-first:** no — `create_user_from_idp` already passes `idp_user.user_type` through untyped (existing tests already do this with `user_type="human"`), so this passes without any production change; it is the spec's "first-login persistence" acceptance criterion made permanent as a regression test at the existing service-test level. No production file is touched.

- [ ] Add the test; run `pytest tests/codemie/service/user/test_authentication_service.py -k service_account` and confirm it passes as-is.
- [ ] Commit.

---

### Task 4 — Base: `UserDB.user_type` comment update

**Repo:** `codemie`
**Files:**
- Modify: `src/codemie/rest_api/models/user_management.py:49` — change the trailing comment from `# 'regular' | 'external'` to `# 'regular' | 'external' | 'service_account'`. Comment only; do not touch `UserUpdateRequest`'s comment at `:219` or any other DTO.

**Test-first:** no — comment-only change, nothing to test.

- [ ] Edit the comment.
- [ ] Commit.

---

### Task 5 — Enterprise: extend the validator and its tests

**Repo:** `codemie-enterprise` (`/home/taras_spashchenko/EPAM/cm/codemie-enterprise`)
**Files:**
- Modify: `src/codemie_enterprise/idp/user_type.py:15` (`VALID_USER_TYPES`), `:1-6` (module docstring), `:38-54` (function docstring) — add `"service_account"` to the set and to the enumerated-value mentions. Keep `InvalidUserTypeError` unchanged.
- Modify: `tests/idp/test_keycloak_idp.py` — add one test to `TestKeycloakIdpFromUserInfoHeader` (class at line 34) mirroring `test_all_optional_fields` (line 58): a header claim of `"user_type": "service_account"` → `idp_user.user_type == "service_account"`. Add the mirrored OAuth-token version to `TestKeycloakIdpFromOAuthToken` (class at line 161), mirroring `test_all_fields` (line 168).
- Modify: `tests/migration/test_keycloak_admin_user.py` — add `test_valid_service_account_type` to `TestKeycloakAdminUserUserTypeProperty` (line 134), mirroring `test_valid_external_type` (line 137): `_make_user(raw_attributes={"user_type": ["service_account"]})` → `user.user_type == "service_account"`.
- No change to `oidc.py`, `entraid_oidc.py`, `migration/keycloak_admin_user.py`, or `tests/idp/test_entraid_oidc_idp.py` — they call the shared `validate_user_type` and the spec does not scope test additions there.

**Test-first:** yes — the three new tests fail with `InvalidUserTypeError` against the current two-value `VALID_USER_TYPES` until the constant is updated in this same task.

- [ ] Write the three new tests; run `pytest tests/idp/test_keycloak_idp.py tests/migration/test_keycloak_admin_user.py -k service_account` and confirm they fail with `InvalidUserTypeError`.
- [ ] Add `"service_account"` to `VALID_USER_TYPES` and update the two docstrings' enumerated-value text; rerun the same tests and confirm they pass, then run both full files to confirm existing tests (including `test_invalid_user_type_rejects`, `test_valid_external_type`, `test_valid_regular_type`) are untouched and still pass.
- [ ] Commit.

---

## Non-goals check

- No DB migration/CHECK constraint: no task touches the schema; Task 4 is comment-only.
- No enum refactor: `VALID_USER_TYPES` stays a plain `set`.
- No shared-constant extraction: Tasks 1 and 5 edit each repo's own constant independently; nothing new is imported across the boundary.
- No `sync_idp_user_profile` change: not referenced by any task.
- No admin-API editability change: `UserUpdateRequest`/`UserListFilters`/`AdminUserListItem`/`CodeMieUserDetail` are not touched (Task 4 explicitly excludes `UserUpdateRequest`'s comment).
- No authorization/licensing/budget/analytics change: no task touches those layers.
- No `is_external_user` semantic change: Task 2 only adds a test; the property's comparison against `config.EXTERNAL_USER_TYPE` is unmodified.
- No UI/notification/onboarding filtering: no such task exists; this change has no UI surface.
