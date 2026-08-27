# Spec: Skip Personal-Project Creation for External / Service-Account Users

## Problem

Personal ("private") project creation runs unconditionally at every call site that invokes
`PersonalProjectService.ensure_personal_project_async` (base repo) or, transitively, the
`MigrationDeps.ensure_personal_project` protocol method (enterprise repo). Users with
`user_type` `"external"` or `"service_account"` should never have a personal project created
for them, starting now. Users with any other `user_type` (`"regular"` and any unrecognized
value) are unaffected.

## New predicate (pin the interface, not the implementation)

Each repo gets its own verbatim copy — deliberate duplication, consistent with the existing
`VALID_USER_TYPES` split between `src/codemie/rest_api/security/user_type_validator.py:29` and
`src/codemie_enterprise/idp/user_type.py:41`. No shared package, no cross-repo import.

```python
PERSONAL_PROJECT_EXCLUDED_USER_TYPES = {"external", "service_account"}

def is_personal_project_excluded(user_type: str) -> bool:
    return user_type in PERSONAL_PROJECT_EXCLUDED_USER_TYPES
```

Base repo copy lives alongside `VALID_USER_TYPES` in `user_type_validator.py`; enterprise copy
lives alongside its `VALID_USER_TYPES` in `idp/user_type.py`. The predicate takes a raw
`user_type: str`, not a `User`/`UserDB` object, so every call site can use it regardless of
what shape of user record it happens to hold. `User.is_external_user`
(`src/codemie/rest_api/security/user.py:108`) is not touched — it deliberately excludes
`service_account` for an unrelated reason and stays exactly as-is.

## Where it must be checked

Guard-before-call at all six existing call sites; none of them change the signature of
`ensure_personal_project_async` or of the `MigrationDeps` protocol:

1. `AuthenticationService._finalize_authentication` — `authentication_service.py:392`
2. `AuthenticationService.authenticate_and_login` — `authentication_service.py:732`
3. `RegistrationService.register_user_with_flow` (email-verification path) — `registration_service.py:211`
4. `RegistrationService.register_user_with_flow` (instant-login path) — `registration_service.py:247`
5. `PersonalProjectService.reconcile_personal_project_on_email_change` — `personal_project_service.py:110`
6. `KeycloakMigrationCoordinator._process_batch` Phase 3 — `codemie_enterprise/migration/coordinator.py:357-370`

At sites 1-4, the caller already holds a user record with `user_type` in scope. At site 6,
`kc_user.user_type` is already validated and in scope in `_process_batch`; the check happens
there, before `self._deps.ensure_personal_project(...)` is invoked — the `MigrationDeps`
protocol signature (`ensure_personal_project(self, user_id, email) -> None`) is not extended
with a `user_type` parameter.

Site 5 is the one exception to "just skip the create call": `reconcile_personal_project_on_email_change`
today unconditionally soft-deletes the user's old personal project and then creates a new one
for the new email. For an excluded `user_type`, skip **both halves** — do not soft-delete the
old project either. An email change must not become a back-door deletion path for a personal
project that this story otherwise guarantees stays untouched for existing users. How site 5
obtains `user_type` (new parameter vs. internal lookup) is left to the implementation plan.

## Acceptance criteria

- A user with `user_type` `"external"` or `"service_account"` gets no personal project created
  at any of the 6 call sites, going forward.
- A user with `user_type` `"regular"` or any value outside `{"external", "service_account"}`
  keeps today's unconditional creation behavior at all 6 call sites, unchanged.
- `User.is_external_user` (`user.py:108`) is unchanged.
- The `MigrationDeps` protocol signature is unchanged.
- On an email change, an excluded-user-type's existing personal project (if any) is left
  completely untouched — neither soft-deleted nor recreated.
- A user with no personal project (because creation was skipped) falls back to `DEMO_PROJECT`
  via `User.current_project` (`user.py:112-115`) exactly as it does today for any other user
  with an empty `project_names` — this is existing, unmodified behavior, not new behavior
  introduced by this change.
- Pre-existing personal projects already created for external/service_account users before
  this change are left exactly as they are (see Non-goals).
- No schema or data migration; `UserDB.user_type` is unchanged.
- `is_personal_project_excluded` (or the repo-local equivalent name) exists verbatim in both
  repos with no cross-repo import.
- New tests cover, per repo, at least: creation skipped for `"external"`, creation skipped for
  `"service_account"`, creation unchanged for `"regular"`, and (base repo only) the site-5
  skip-both-halves behavior on email change.

## Non-goals

- Backfilling or cleaning up personal projects that already exist for external/service_account
  users — this story only prevents *future* creation.
- Any change to `User.is_external_user`.
- Any change to the `MigrationDeps` protocol signature, or a shared cross-repo package/import
  to deduplicate the new predicate.
- Any schema or data migration on `UserDB`.
- Any change to `User.current_project`'s `DEMO_PROJECT` fallback logic.
- Any change to user creation (`create_user_from_idp`) or to authentication/registration flows
  beyond the personal-project-creation call itself.

## Open risks

- Seat/licensing, ACL, and UI project-listing consumers of "does this user have a personal
  project" were not fully traced in Stage 1 research. This spec does not assert or change their
  behavior for users who now have no personal project; if any of them assume every user has
  exactly one, that surfaces only during implementation or later testing.
