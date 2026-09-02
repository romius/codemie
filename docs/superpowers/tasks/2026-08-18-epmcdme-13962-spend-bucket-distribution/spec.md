# Spec: Allow Project Admins to Change Spend Bucket Distribution

**Ticket**: EPMCDME-13962
**Branch**: EPMCDME-13962_spend-bucket-distribution
**Complexity**: M (15/36)

## Problem

Project admins can currently view spend bucket distribution for budgets in their project but cannot modify it. All write operations on `ProjectBudgetGroup` are gated behind `maintainer_access_only`, which requires system-wide maintainer privileges. Project admins need to update category distribution percentages for the groups they administer.

## Solution

Introduce a new project-scoped write dependency that grants project admins write access to budget groups belonging to their project, while preserving full access for maintainers. Enrich the existing audit event to capture old and new category percentages.

## Changes

### 1. New security dependency (`authentication.py`)

Add `project_admin_budget_group_write_access(group_id, user, session)`:
- If `user.is_maintainer`: return user (full access preserved)
- Load `ProjectBudgetGroup` by `group_id` from DB; raise HTTP 404 if not found
- If `group.project_name in user.admin_project_names`: return user
- Otherwise: raise HTTP 403

The dependency performs a DB read before the authorization decision to prevent scope leaks — a project admin must not mutate groups outside their project.

### 2. Router guard swap (`project_budget_router.py`)

On `PUT /v1/admin/project-budget-groups/{group_id}`: replace `Depends(maintainer_access_only)` with `Depends(project_admin_budget_group_write_access)`.

No other endpoint changes. All other mutating endpoints (`PATCH`, `DELETE`, `POST rebalance/reset/override`) remain gated on `maintainer_access_only`.

### 3. Audit enrichment (`project_budget_service.py`)

In `update_project_budget_group`, before emitting `PROJECT_BUDGET_GROUP_UPDATED`, capture the current category percentages and include `old_categories` and `new_categories` in the event payload.

## Authorization Matrix

| Actor | View distribution | Change distribution |
|---|---|---|
| Maintainer | Yes | Yes |
| Project Admin (own project) | Yes | Yes (new) |
| Project Admin (other project) | No | No |
| Other authenticated user | No | No |

## Validation

Distribution validation is unchanged — `_validate_group_categories` enforces category validity, `platform` presence, and `sum(pct)` within 0.5 tolerance of 100.0. Project admin submissions pass through the same validation path.

## Acceptance Criteria

- Project Admin can view spend bucket distribution for budgets in their project (already satisfied).
- Project Admin can change spend bucket distribution for budgets they administer.
- The system validates that distribution values are correct before saving.
- Unauthorized users cannot change spend bucket distribution.
- Updated distribution is reflected in budget tracking and reporting (no new work — existing provider sync handles this).
- All changes are auditable: `PROJECT_BUDGET_GROUP_UPDATED` event includes `old_categories` and `new_categories`.
- Tests cover: successful update by project admin, invalid distribution, cross-project unauthorized access, non-admin unauthorized access, and audit event payload.

## Out of Scope

- Changes to other mutating endpoints (`PATCH budget`, `DELETE`, `POST rebalance/reset/override`, `override_member_allocation`)
- New database models or migrations
- Changes to the analytics/Elasticsearch reporting pipeline
