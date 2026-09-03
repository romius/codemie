# Plan: Fix spendings widget showing 0 after budget reassignment

## Task 1 — Write failing test for budget-reassignment scenario

**Test-first: yes** — test `_collect_spend_rows` with `current_spend_map={}` (new budget_id), a non-zero `prev_day_map` entry (old budget row for same category), and `fresh_spend == prev_row.budget_period_spend`. Assert that a tracking row IS returned (not skipped) and that `budget_period_spend` equals `fresh_spend`.

File: `tests/codemie/service/analytics/handlers/test_budget_usage_service.py`

## Task 2 — Apply the one-line fix in `_collect_spend_rows`

Change:
```python
allow_zero=_is_reset_transition(prev_row, budget, fresh_spend, now),
```
To:
```python
allow_zero=existing is None or _is_reset_transition(prev_row, budget, fresh_spend, now),
```

File: `src/codemie/service/analytics/handlers/budget_usage_service.py`

## Task 3 — Add regression tests

1. `existing is None` + `prev_day_map` empty + `fresh_spend = 0` → row written with `budget_period_spend = 0`, `daily_spend = 0` (correct for genuinely new user).
2. `existing is not None` + unchanged spend → row still skipped (`spend_unchanged` path unchanged).
3. `existing is not None` + changed spend → row written as before.
