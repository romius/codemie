# EPMCDME-14071 — Per-Project Member Spend Analytics (Backend)

**Date**: 2026-08-13
**Ticket**: EPMCDME-14071
**Handoff**: `codemie-ui-next/docs/superpowers/specs/2026-08-13-epmcdme-14071-backend-handoff.md`

---

## Problem

The admin UI needs two spend breakdowns that no endpoint currently provides:

1. **User → projects**: how much has user X spent, split by each project they belong to?
2. **Project → members**: how much has each member of project Y spent in that project?

Today the users page shows only *global* per-category spend, and the project members table
shows only *allocated limits* (`allocated_max_budget`), never actuals.

Both breakdowns need spend at the `(user, project, category)` intersection.

## Key finding: the data already exists

Project LLM traffic is attributed in LiteLLM against a customer id that encodes all three
dimensions (`budget_provider_adapter.py:1362-1368`):

```
codemie:project:{project_name}:category:{budget_category}:user:{user_id}
```

`LiteLLMSpendCollectorService` already persists these into `project_spend_tracking` rows with
`spend_subject_type='member_budget'` (`spend_collector_service.py:247-261`), where:

| Column | Holds |
|---|---|
| `project_name` | the real project name |
| `user_id` | Codemie user UUID |
| `budget_category` | `platform` \| `cli` \| `premium_models` |
| `budget_id` | the **main** project budget id |
| `budget_period_spend` | LiteLLM current-period spend |

Unique index: `(project_name, budget_id, user_id, spend_date) WHERE spend_subject_type='member_budget'`
(`c2d3e4f5a6b7:46`).

### Sources rejected

- **`ProjectMemberBudgetAssignment.spend` / `.last_synced_at`** — vestigial. Zero write sites, zero
  read sites across both repos. Sync bookkeeping moved to the `provider_metadata` JSONB blob.
  The `get_active_by_user` docstring at `project_budget_repository.py:774-795` claiming
  `/budget_usage` uses it is **wrong**; only tests call it.
- **Elasticsearch `codemie_metrics_logs*`** — carries user + project + cost per request, but has no
  budget-category dimension and cannot express "current budget cycle". The handoff requires these
  numbers to agree with the global Budgets column on the same screen; ES cannot guarantee that.

---

## Endpoints

Two literal routes on the existing analytics router. There is **no `{metric}` dispatcher** in this
codebase — `analytics.py` registers ~90 explicit literal paths — so we follow that convention.

```
GET /v1/analytics/user-project-spending?users=<email>
GET /v1/analytics/project-member-spending?projects=<name>&page=&per_page=
```

Both return `TabularResponse` built by `ResponseFormatter.format_tabular_response`.

### Deviation from handoff: pagination on Endpoint 1

`format_tabular_response` emits the `pagination` block only when `page`, `per_page` and
`total_count` are all supplied (`response_formatter.py:122-161`). `budget_usage` supplies none and
therefore returns no `pagination` key. The handoff shows a pagination block on Endpoint 1, so we
**supply the args and emit it** — a missing key is likelier to break the client than a present one.

### Columns

Generated from the `BudgetCategory` enum, not hardcoded, so a fourth category flows through without
a frontend release (handoff rule #3). Money columns carry `format: "currency"`.

Endpoint 1 row key: `project_name` (plus `display_name`).
Endpoint 2 row key: `user_id`, matching `id` from `GET /v1/admin/users`.

---

## Service layer

New `src/codemie/service/analytics/handlers/member_spend_service.py`, mirroring the structure of
`budget_usage_service.py`.

```python
get_user_project_spend(session, user_id)       # rows keyed by project
get_project_member_spend(session, project)     # rows keyed by user_id
```

Both funnel through one internal read-check-refresh-fallback routine:

1. **Read** `member_budget` rows for the requested slice.
2. **Check staleness** (see below).
3. **If stale** → global refresh via the provider adapter's existing
   `_load_member_spend_from_litellm` (one bulk `GET /customer/list`), deltas computed with the
   collector's `_compute_spend_delta` / `_did_budget_reset` / `_quantize_spend`, persisted via
   `insert_member_budget_entries`.
4. **On LiteLLM failure** → log a warning, return DB rows as-is. Never fail the request.
5. Reload and build rows.

New config `MEMBER_SPEND_STALENESS_THRESHOLD_MS`, defaulting to `600000` (10 min), matching
`BUDGET_USAGE_STALENESS_THRESHOLD_MS` (`config.py:750`).

### Why a new service rather than extending existing code

`budget_usage_service` is the established precedent for exactly this shape: a lazy-refresh service
that borrows the collector's math. Extending `LiteLLMSpendCollectorService` with a request-path
entry point would make a batch job a live dependency; generalizing `budget_usage_service` would
rework a live, tested service into two paths behind one door. A sibling service keeps the live
endpoint untouched, and anti-drift protection comes from sharing the **helpers**, which is how the
existing code already achieves it.

### Why the refresh is global

`GET /customer/list` returns every customer regardless of what we asked for — the upstream call has
no narrower grain. Filtering the response down to one slice and discarding the rest costs the same
outbound call and leaves the next request to pay again. Writing all of it means the first admin
click warms the cache for every subsequent click and every project page, which directly addresses
the burst behaviour the handoff describes for Endpoint 1.

Consequence to accept: a request for user X writes rows for everyone, and a refresh failure is
correspondingly noisier in logs. Both are acceptable; neither affects correctness.

---

## Staleness

**Staleness is the age of the newest `member_budget` row in the table, table-wide — not per slice.**

```sql
SELECT max(spend_date) FROM project_spend_tracking WHERE spend_subject_type = 'member_budget'
```

Because the refresh is global, the freshest row anywhere is an accurate answer to *"when did we last
check?"*. No marker table, no migration, no new model.

### Why not per-slice max

The collector **skips zero-delta rows** (`spend_collector_service.py:241-246`), so the table is
sparse. A member who has not spent in a week carries a week-old `spend_date`. A per-slice
`max(spend_date)` would read as stale on every request for that member, triggering a global refresh
every time — the TTL would fail for exactly the quiet users it should help most.

### Why not a marker table

An earlier draft proposed one. It is unnecessary: the newest row already *is* the marker. Rejected
to avoid a migration for a single timestamp.

### Why not `budget_usage`'s touch trick

`budget_usage` bumps `spend_date` on unchanged rows to restart its TTL clock
(`touch_budget_spend_dates`). At global scale that rewrites thousands of rows and makes `spend_date`
dishonest — claiming money was spent when we merely looked. We do not adopt it. Rows whose spend did
not move keep their old `spend_date`, which correctly records when that money was last measured.

Cold table → no rows → refresh, same as `budget_usage` with an empty map. Correct, slow once.

---

## Row construction

**Rows are projected from membership; spend is left-joined onto them.** Never the reverse.

Because the spend table is sparse, building rows from spend rows would make projects and members
**disappear** whenever their spend had not changed recently — an admin would read that as removed
membership. Driving from membership keeps the row set complete and stable.

### Endpoint 1 — user → projects

- **Row source**: `user_projects` for the target user.
- **Limits**: left-join `project_member_budget_assignments` → `allocated_max_budget` per category.
- **Spend**: left-join the latest `member_budget` row per `(project_name, user_id, budget_category)`,
  taking `budget_period_spend`. Note the table's unique index is
  `(project_name, budget_id, user_id, spend_date)`; `budget_category` is carried as a column and must
  be matched explicitly rather than inferred from `budget_id`.
- Missing spend → `0`. Missing allocation → `null` limits.

`user_projects` (not budget assignments) is the row source deliberately: a project the user belongs
to but which has no budget configured must still appear, with `null` limits (handoff rule #4). This
also makes handoff edge case #1 fall out naturally — leaving a project removes the `user_projects`
row, so the project drops off, matching the frontend's "omit" assumption.

Accepted cost: budget-less projects generally show `0` across all categories, since spend only
accrues where a budget routes it. Honest and preferable to hiding the row.

### Endpoint 2 — project → members

- **Row source**: members of the project from `user_projects`; `user_id` is `user_projects.user_id`.
- Same left-joins for limits and spend.

### Zero vs null

`0` means "spent nothing". `null` (or absent) means "no limit configured". Never send `0` for a
missing limit — it renders as fully consumed (handoff rule #4).

---

## Authorization

Reuse `_authorize_admin_budget_view` (`analytics.py:588-602`): global admin, or project-admin of a
project the target belongs to. Endpoint 2 authorizes against the requested project directly.
Enforced server-side regardless of the UI gate (handoff rule #8).

Empty results return `200` with `rows: []`, never `404` (handoff rule #6).

---

## Answers to the handoff's open questions

| # | Question | Answer |
|---|---|---|
| 1 | Include spend from projects the user has left? | **Omit** — confirmed; falls out of `user_projects` |
| 2 | Include personal-project spend? | **Include** if present in `user_projects` |
| 3 | Is project-unattributable spend possible? | **Yes** — frontend assumed no. See below. |
| 4 | Include inactive users in member spending? | **Include** — membership rows persist |
| 5 | Can per-category budget cycles differ? | **Yes** — each follows its own budget's `budget_reset_at` |

### #3 needs frontend attention

Personal-budget spend is tracked against the user (`spend_subject_type='budget'`, where
`project_name` holds the user's username), not against any project. It therefore cannot appear in a
per-project breakdown. **Per-project rows will not sum to the global Budgets column.** The frontend
assumed this was impossible; they should decide how to present the discrepancy before shipping.

---

## Testing

Service tests (`tests/codemie/service/analytics/handlers/`):

- cold table → refresh
- fresh table → no LiteLLM call
- stale table → refresh
- LiteLLM failure → stale DB fallback, no exception
- sparse rows: member with old `spend_date` but fresh table-wide max → no refresh
- zero-spend row projection → `0`, row present
- missing allocation → `null` limits, not `0`

Router tests (`tests/codemie/rest_api/routers/`):

- response shape for both endpoints, columns derived from enum
- pagination block present on both
- 403 for non-admin, non-project-admin
- empty result → `200` with `rows: []`

---

## Out of scope

- `ProjectMemberBudgetAssignment.spend` / `last_synced_at` — left untouched (vestigial).
- The misleading `get_active_by_user` docstring (`project_budget_repository.py:774-795`).
- Duplicated `_get_key_spending_columns` / `_build_spending_row` between `analytics.py:483-543` and
  `budget_usage_service.py:77-160`, including their behavioural drift on expired resets.
- Adding `user_id` to `SpendTrackingRow` on `GET /v1/projects/{name}/spends` (`projects.py:968-974`),
  which currently fetches member rows and drops the user dimension.

Each is a defensible follow-up; none is required by this ticket.
