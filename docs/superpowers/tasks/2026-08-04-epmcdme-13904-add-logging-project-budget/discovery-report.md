# logger.info Discovery Report — EPMCDME-13904

**Scanned**: `src/codemie/service/`
**Date**: 2026-08-04
**Method**: AST walk — methods with `activity_event_repository.insert/async_insert` but no `logger.info` in body

---

## Gaps found beyond the 6 in spec

| File | Method | Line | Classification | Notes |
|---|---|---|---|---|
| `src/codemie/service/budget/budget_service.py` | `_persist_bulk_budget_assignments` | 1180 | ignore | Private internal helper called by `bulk_set_user_budgets`. The outer method (`bulk_set_user_budgets`) receives the completion-level `logger.info` in Task 3 — logging at the helper level would be double-counting. |
| `src/codemie/service/user/user_management_service.py` | `create_local_user` | 71 | ignore | **Not a gap — intentional.** Commit `7bd83e944` (EPMCDME-13273) deliberately removed `logger.info("user_created: ...")` from this method as a duplicate: `create_local_user` is called by both `bootstrap_superadmin` (which logs `superadmin_bootstrapped`) and `create_local_user_with_flow` (which logs `user_created` with actor context). Re-adding a log here would regress EPMCDME-13273. |

## Extended task list (classification=extend)

None — all gaps beyond the 6 in spec are either internal helpers (ignore) or belong to another domain (follow-up).

## Follow-up ticket candidates

- `user_access_service._build_project_access_log_details` — `project_access_granted` / `project_access_updated` / `project_access_removed` log only `actor_user_id` and `target_user_id`, never which project. Pre-existing; left untouched by EPMCDME-13273. Fails this ticket's AC "logs include the affected project or budget entity where applicable".
- Activity-event (DB audit) track gaps — `project_budget_service.delete_project_budget_group` and `budget_service.reset_user_budget_spending` both have `logger.info` but emit no activity event. Out of scope here (this ticket is the `logger.info` track).

**Withdrawn:** `user_management_service.create_local_user` was previously listed here as a follow-up candidate. It is not a gap — see the ignore classification above.
