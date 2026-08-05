# EPMCDME-13904: Add logger.info coverage for project and budget management

## Problem

Project and budget management service operations emit `activity_event_repository` inserts (DB audit track) but are missing plain `logger.info` calls (the operational log track that goes to long-term storage). User management `update_user` and `deactivate_user` serve as the reference — both tracks present. This spec closes the logger.info gap only; the activity_event_repository track is already in place.

## Scope

Six service methods across three files. No schema changes, no new abstractions, no router changes.

| File | Method | Gap |
|---|---|---|
| `src/codemie/service/project/project_service.py` | `create_shared_project` | logger.info missing |
| `src/codemie/service/project/project_service.py` | `update_project` | logger.info missing |
| `src/codemie/service/budget/project_budget_service.py` | `override_member_allocation` | logger.info missing |
| `src/codemie/service/budget/project_budget_service.py` | `clear_member_override` | logger.info missing |
| `src/codemie/service/budget/budget_service.py` | `assign_budget_to_user` | logger.info missing |
| `src/codemie/service/budget/budget_service.py` | `bulk_set_user_budgets` | logger.info missing |

Out of scope: `delete_project_budget_group` (already has logger.info; its missing activity event is a separate concern).

## Design

### Message format convention

Each file follows its own established style. No cross-domain normalization.

**project_service.py** — matches `project_deleted` (already in this file):
```
"project_created: project=<name>, by=<actor_id>"
"project_updated: project=<name>, by=<actor_id>"
```

**project_budget_service.py** — matches `budget_event=..._completed component=project_budget_service ...` (already in this file):
```
"budget_event=member_allocation_override_completed component=project_budget_service budget_id=<id> user_id=<id> actor_id=<id>"
"budget_event=member_allocation_override_cleared component=project_budget_service budget_id=<id> user_id=<id> actor_id=<id>"
```

**budget_service.py** — matches `budget_event=..._completed component=budget_service ...` (already in this file):
```
"budget_event=user_budget_assignment_completed component=budget_service user_id=<id> categories=[...] actor_id=<id>"
"budget_event=bulk_user_budget_assignment_completed component=budget_service user_count=<n> categories=[...] actor_id=<id>"
```

### Placement

- `project_service.create_shared_project` — add after `session.commit()`, before `return project` (inside the try block, after expunge+commit)
- `project_service.update_project` — add after `session.refresh(project)`, before `cls._resync_member_allocations_if_needed`
- `project_budget_service.override_member_allocation` — add after the `activity_event_repository.async_insert` call, before `return allocation`
- `project_budget_service.clear_member_override` — add after the `activity_event_repository.async_insert` call, before `return allocation`
- `budget_service.assign_budget_to_user` — add after the `for category, budget_id in assignments.items()` loop closes, before the method returns
- `budget_service.bulk_set_user_budgets` — add after `await self._propagate_bulk_budget_assignments(...)`, before the method returns

### Security

Log messages must not expose PII or secrets. All fields are IDs (UUIDs or project names) or counts. No email addresses, usernames, tokens, or budget amounts in any message.

## Tests

Add `logger.info` assertions to existing test cases for each of the six methods. Pattern from `test_project_assignment_service.py`:

```python
@patch("codemie.service.<domain>.<module>.logger")
def test_<method>_logs(mock_logger, ...):
    # arrange and act as before
    mock_logger.info.assert_called_once()
    assert "<event_key>" in mock_logger.info.call_args[0][0]
```

- Sync methods (project_service): `MagicMock` for logger, add to existing test setup.
- Async methods (project_budget_service, budget_service): `@pytest.mark.asyncio` + `@patch(...)`, `AsyncMock` for repository dependencies.
- Assertion depth: `assert_called_once()` plus one key-field substring check (e.g., `"project_created"`, `"member_allocation_override_completed"`).
- Do not assert actor_id or entity_id values directly — test isolation makes those fragile.

## Acceptance criteria

1. `create_shared_project` emits a `logger.info` message containing `"project_created"` on success.
2. `update_project` emits a `logger.info` message containing `"project_updated"` on success.
3. `override_member_allocation` emits a `logger.info` message containing `"member_allocation_override_completed"` on success.
4. `clear_member_override` emits a `logger.info` message containing `"member_allocation_override_cleared"` on success.
5. `assign_budget_to_user` emits a single completion-level `logger.info` containing `"user_budget_assignment_completed"` after all assignments are processed.
6. `bulk_set_user_budgets` emits a single completion-level `logger.info` containing `"bulk_user_budget_assignment_completed"` after all users are processed.
7. No log message exposes PII, secrets, or budget amounts.
8. Existing tests continue to pass (no regression in activity_event_repository behavior).
9. Each of the six methods has a test asserting `mock_logger.info.assert_called_once()`.
