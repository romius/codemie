# Spec: Fix spendings widget showing 0 after budget reassignment

**Ticket:** EPMCDME-13669
**Type:** Bug fix

## Problem

When a user with existing LLM spending is assigned a new budget, the Spendings widget immediately shows 0 instead of the correct existing spend. The spend corrects itself only after the user makes a new LLM request.

## Root Cause

In `_collect_spend_rows()` (`budget_usage_service.py`), after a budget reassignment:

1. `current_spend_map.get(assignment.budget_id)` returns `None` — no `project_spend_tracking` rows exist for the new `budget_id` yet.
2. `prev_row` falls back to `prev_day_map.get(assignment.category)` — the old budget's row for this category.
3. `fresh_spend == prev_row.budget_period_spend` (spend hasn't changed since the last snapshot), so `daily_spend = 0`.
4. `allow_zero = False` (not a reset transition), so the row is skipped entirely.
5. `_build_budget_usage_rows` finds no entry for the new `budget_id` and returns `0.0`.

## Fix

One-line change in `_collect_spend_rows()`: set `allow_zero=True` when `existing is None` (new `budget_id`, no prior tracking rows), so a tracking row is always seeded with the correct `budget_period_spend` even when the delta is zero.

```python
# budget_usage_service.py — _collect_spend_rows()
allow_zero=existing is None or _is_reset_transition(prev_row, budget, fresh_spend, now),
```

This follows the same "force-write on first-seen" principle used by `reset_user_budget_spending` in `budget_service.py` (lines 1326–1355).

## Acceptance Criteria

- Spendings widget displays the correct existing spend immediately after a new budget is assigned.
- Existing spending is not shown as 0 unless the actual value is 0.
- No new LLM usage is required to trigger the correct display after assignment.
- Users with zero actual spend still correctly show 0.
- Subsequent LLM usage continues to update spend correctly.

## Scope

- **Changed:** `src/codemie/service/analytics/handlers/budget_usage_service.py` — one-line fix
- **Tests added:** `tests/codemie/service/analytics/handlers/test_budget_usage_service.py` — new test covering the budget-reassignment scenario in `_collect_spend_rows`
- **No schema changes, no new dependencies, no API changes**
