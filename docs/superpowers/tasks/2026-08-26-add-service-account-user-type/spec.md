# Spec: Add "service_account" as a valid user_type value

**Ticket:** EPMCDME-14413
**Repos:** `codemie` (base) and `codemie-enterprise` (enterprise), both on branch `EPMCDME-14413`

## Context

`VALID_USER_TYPES = {"regular", "external"}` is defined independently in both
repos (a documented zero-coupling design, not accidental duplication) and
gates which `user_type` values a Keycloak/OIDC/EntraID JWT claim may carry
before a user record is created. We need to add `"service_account"` as a
third accepted, normalized, and persisted value, with no other behavioral
change.

## Approach

Add `"service_account"` to both `VALID_USER_TYPES` sets, each preserving its
own exception type and all three existing validator behaviors: accept and
normalize (`.strip().lower()`), default missing/`None` claim to `"regular"`,
fail closed on anything else. No other production code changes — the value
already flows unmodified through the whole existing pipeline (claim
extraction → `validate_user_type` → `IdpUser.user_type` →
`AuthenticationService.create_user_from_idp` → `UserDB.user_type`).

## Changes by file

### `codemie-enterprise`
- `src/codemie_enterprise/idp/user_type.py` — add `"service_account"` to
  `VALID_USER_TYPES`; update the docstring's enumerated values.
- `tests/idp/test_keycloak_idp.py` — add a case asserting
  `IdpUser.user_type == "service_account"` for both `_from_user_info_header`
  and `_from_oauth_token`, mirroring the existing `test_all_optional_fields` /
  `test_without_user_type_defaults_to_regular` structure.
- `tests/migration/test_keycloak_admin_user.py` — add
  `test_valid_service_account_type` to `TestKeycloakAdminUserUserTypeProperty`,
  mirroring `test_valid_external_type`.
- `src/codemie_enterprise/idp/oidc.py`, `entraid_oidc.py`,
  `src/codemie_enterprise/migration/keycloak_admin_user.py` — no code change;
  they all call the shared `validate_user_type`, so the new value flows
  through automatically once the constant is updated.

### `codemie`
- `src/codemie/rest_api/security/user_type_validator.py` — add
  `"service_account"` to `VALID_USER_TYPES`.
- `src/codemie/rest_api/models/user_management.py` — update the `user_type`
  field's inline comment from `# 'regular' | 'external'` to
  `# 'regular' | 'external' | 'service_account'`.
- `tests/codemie/rest_api/security/test_user_type_validator.py`:
  - Update `test_valid_user_types_constant` to assert
    `VALID_USER_TYPES == {"regular", "external", "service_account"}` (exact
    membership) rather than a hardcoded `len == 2`, so the next addition
    doesn't break the same assertion the same way.
  - Add accept/normalize cases for `"service_account"` (plus case and
    whitespace variants) mirroring the existing `"external"` cases.

**No changes to:** `UserUpdateRequest`, `UserListFilters`,
`AdminUserListItem`, `CodeMieUserDetail` (already `str`/`Optional[str]`, need
no change to carry the new value); `User.is_external_user` (must keep
comparing only against `config.EXTERNAL_USER_TYPE`); `AuthenticationService`
(`create_user_from_idp` / `sync_idp_user_profile` already pass `user_type`
through untyped); `UserDB` schema (no migration — unconstrained string
column).

## Acceptance criteria

- A `user_type` claim (case-insensitive, trimmed) equal to `"service_account"`
  is accepted by both repos' validators and normalized to `"service_account"`.
- A first-login user whose IDP claim resolves to `"service_account"` is
  persisted with `UserDB.user_type == "service_account"`.
- Existing behavior for `"regular"`, `"external"`, missing/`None` (→
  `"regular"` default), and genuinely invalid values (fail closed, existing
  exception types unchanged: `ExtendedHTTPException` in `codemie`,
  `InvalidUserTypeError` in `codemie-enterprise`) is unaffected.
- `User.is_external_user` returns `False` for a user with
  `user_type == "service_account"`.
- `test_valid_user_types_constant` and the added enterprise tests pass
  against the new three-value set.
- `UserUpdateRequest`'s admin-API behavior for `user_type` is unchanged
  (still local-mode-only).

## Non-goals

- No DB migration or CHECK constraint — `UserDB.user_type` remains an
  unconstrained string column.
- No enum refactor of `user_type` in either repo.
- No shared-constant extraction across the `codemie`/`codemie-enterprise`
  boundary — the duplicated `VALID_USER_TYPES` definitions are an intentional
  zero-coupling property of this codebase and stay duplicated.
- No change to `sync_idp_user_profile`: it continues to re-sync only
  `email`/`name`/`picture`, not `user_type`.
- No admin-API editability change for `user_type`.
- No change to authorization/permissions, licensing/seat-counting,
  budget/quota, or analytics grouping based on `user_type`.
- No change to `User.is_external_user` semantics — `"service_account"` is
  neither `"regular"` nor treated as external.
- No user-facing UI/notification/onboarding filtering for service accounts.

## Known limitation (carried forward, not fixed here)

An already-provisioned user record is not upgraded to `"service_account"` on
a later login even if their IDP claim changes, because
`sync_idp_user_profile` never re-syncs `user_type` after first-login
creation. Pre-existing behavior; out of scope for this ticket.
