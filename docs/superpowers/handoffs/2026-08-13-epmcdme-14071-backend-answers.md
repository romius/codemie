# EPMCDME-14071 — Backend Answers to Frontend Handoff

**Date**: 2026-08-13
**Responding to**: `codemie-ui-next/docs/superpowers/specs/2026-08-13-epmcdme-14071-backend-handoff.md`
**Backend spec**: `docs/superpowers/specs/2026-08-13-epmcdme-14071-member-spend-analytics-design.md`

## Your five open questions

| # | Question | Answer |
|---|---|---|
| 1 | Include spend from projects the user has left? | **Omit** — confirmed. Rows come from current membership. |
| 2 | Include personal-project spend? | **No — personal projects are excluded entirely.** See below, with #3. |
| 3 | Is project-unattributable spend possible? | **Yes.** See below — this contradicts your assumption. |
| 4 | Include inactive users in member spending? | **Include** — membership rows persist after deactivation. |
| 5 | Can per-category budget cycles differ? | **Yes** — each category follows its own budget's reset schedule. |

## Questions 2 and 3 together — read these as one answer

Personal-budget spend is metered against the **user**, not against any project. It cannot appear in
a per-project breakdown, because no project is attached to it.

**Personal projects are excluded from Endpoint 1 — you will not see them at all.** A personal
project could only ever report `0` in this breakdown, and a `$0.00` row reads as "spent nothing"
when the truth is "not tracked here". Rather than ship a row that is guaranteed meaningless and ask
every client to filter it, the backend omits them. A user whose only project is their personal one
gets `rows: []`.

Personal spend is still reported — by `GET /v1/analytics/budget_usage`, which is what already backs
the Budgets column.

**The per-project rows will not sum to the global Budgets column** on the same page. The difference
is exactly that personal spend, plus any other spend not routed through a project budget. This is
not a bug and cannot be fixed by the backend without changing how personal spend is metered.

You assumed #3 was impossible. Please decide how to present the difference — an explanatory tooltip,
an "Unassigned" row (backend can add one if you want it), or leaving the two figures visually
separated so no one reads them as a sum.

## Deviation from your contract: pagination on Endpoint 1

Your example response for `user-project-spending` includes a `pagination` block. The shared
formatter omits that block unless pagination arguments are supplied, and `budget_usage` supplies
none. **We supply them, so the block is present** on both endpoints, as your example shows.

Both endpoints accept `page` (default `0`, zero-indexed) and `per_page` (default `20`, max `1000`),
and return `pagination: { page, per_page, total_count, has_more }`.

## Endpoints as shipped

| Endpoint | Key column | Required query param |
|---|---|---|
| `GET /v1/analytics/user-project-spending` | `project_name` | `users` — target user email, exactly one |
| `GET /v1/analytics/project-member-spending` | `user_id` | `projects` — target project name, exactly one |

If a comma-separated list is supplied, only the first value is used. `user_id` in Endpoint 2 matches
the `id` returned by `GET /v1/admin/users`, which is your join key.

Each row carries one key column plus, per budget category, a spend field (`platform`, `cli`,
`premium_models`) and its matching `*_limit` field.

**`display_name` is not sent.** Your Endpoint 1 example shows it, but since `columns[]` is
authoritative and describes no such column, a field you cannot render would be dead weight. Endpoint
1 rows are keyed by `project_name` only. If you want project display names in this table, say so and
we will add it to both `columns[]` and the rows.

## Everything else

Confirmed as specified: no `total` field, `BudgetCategory` row keys, authoritative `columns[]`,
optional `*_limit` (null never zero), zero spend as `0`, empty results as `200` with `rows: []`,
JSON numbers, server-side authorization.
