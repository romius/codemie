# Technical Research

**Task**: logging audit project budget user management
**Generated**: 2026-08-04T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Add logging coverage for project management and budget management operations in CodeMie. Currently user management has proper logging. Project management is not fully covered and budget management has no logging at all. Need to close the coverage gap so all three domains are consistently logged and auditable.

---

## 2. Codebase Findings

### Existing Implementations

**Service layer (relevant files):**
- `src/codemie/service/user/user_management_service.py` (1193 lines) — reference implementation; all mutating ops have both logger.info + activity_event_repository.insert
- `src/codemie/service/project/project_service.py` (458 lines) — shared project CRUD; `create_shared_project` and `update_project` have activity event insert but **missing logger.info**; `_check_and_delete_project` already has both tracks (complete reference path in this file)
- `src/codemie/service/project/project_assignment_service.py` (820 lines) — project membership CRUD; logger.info asserted in tests (covered)
- `src/codemie/service/budget/budget_service.py` (1600 lines) — global budget CRUD + user assignments; per-item activity events present but **no completion-level logger.info** for `assign_budget_to_user` and `bulk_set_user_budgets`
- `src/codemie/service/budget/project_budget_service.py` (2119 lines) — project-scoped budget CRUD; `delete_project_budget_group` has logger.info but **missing activity_event_repository.async_insert`**; `override_member_allocation` and `clear_member_override` have activity events but **missing logger.info`**

**Activity infrastructure:**
- `src/codemie/service/activity/activity_models.py` — `ActivityEvent` ORM, `ActivityDomain`, `ProjectManagementEvent`, `UserManagementEvent`, `BudgetManagementEvent` enums, `ActivityEventCreate` Pydantic DTO
- `src/codemie/service/activity/activity_repository.py` — `activity_event_repository` with sync `insert(dto, session)` and async `async_insert(dto, session)`

**Logging infrastructure:**
- `src/codemie/configs/logger.py` — logger singleton, contextvar-based structured logging, LogFormatter
- `src/codemie/configs/__init__.py` — re-exports `logger` from configs package

**API routers:**
- `src/codemie/rest_api/routers/projects.py` (46.9 KB)
- `src/codemie/rest_api/routers/budget_router.py` (7.9 KB)
- `src/codemie/rest_api/routers/project_budget_router.py` (28.0 KB)
- `src/codemie/rest_api/routers/user_management_router.py` (17.4 KB) — reference

### Architecture and Layers Affected

```
REST API router (routers/)
  → Service (service/project/, service/budget/)
    → Activity repository (service/activity/activity_repository.py)
    → Logger singleton (configs/logger.py)
    → DB session (SQLModel)
```

Only the **service layer** is affected — no router or repository changes needed.

### Integration Points

- `activity_event_repository` — shared append-only audit table; used by both sync and async services
- `logger` singleton — single structured logger consumed across all service files
- `ActivityEventCreate` DTO — bridges service-layer domain knowledge to the repository

### Patterns and Conventions

**Two-track audit pattern** (mandatory for every mutating operation):
1. `logger.info(f"<message>")` — structured key=value message
2. `activity_event_repository.insert(ActivityEventCreate(...), session)` (sync) or `await activity_event_repository.async_insert(ActivityEventCreate(...), session)` (async)

**Log message formats by domain:**
- User management: `"action_taken: actor_user_id={id}, target_<entity>_id={id}, domain=<domain>"`
- Project assignment: prose sentence — `"User assigned to project: user_id={id}, project={name}, is_admin={bool}, by={id}"`
- Budget: structured key=value — `"budget_event=<event_name> component=<service_name> <fields...>"`

**Logger import:**
- `from codemie.configs.logger import logger` (preferred, used in project/user services)
- `from codemie.configs import logger` (also valid, used in budget services — same singleton)

**ActivityEventCreate fields:** `domain`, `event_type`, `entity_type`, `entity_id`, `actor_id`, `attributes`

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/development/logging-patterns.md` — log message format (key=value, IDs not secrets), severity matching
- `.ai-run/guides/architecture/service-layer-patterns.md` — logging as a service concern
- `.ai-run/guides/testing/testing-patterns.md` — test location and scope policy
- `.ai-run/guides/testing/testing-service-patterns.md` — service isolation and async test patterns

### Architectural Decisions

No ADRs found. The two-track audit pattern is the de facto standard derived from the user management implementation and the existing project_assignment_service.

### Derived Conventions

1. Both `logger.info` and `activity_event_repository` insert are required per mutating operation — either alone is incomplete.
2. Async service methods use `await activity_event_repository.async_insert(...)`.
3. Sync service methods use `activity_event_repository.insert(..., session)`.
4. Log messages must not expose PII or secret values; use IDs, not names or tokens.
5. Message style should match the existing domain style (user/project-assignment vs. budget differs slightly — follow whichever is already in that file).

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/service/user/test_user_management_service.py` — activity events asserted; **no logger.info assertions** (logger not mocked — gap in reference tests)
- `tests/codemie/service/project/test_project_service.py` — no logger or activity event assertions for create_shared_project
- `tests/codemie/service/project/test_project_service_delete_update.py` — no logger assertions for update_project or delete
- `tests/codemie/service/project/test_project_assignment_service.py` — **has logger assertions** (`mock_logger.info.assert_called_once()`) — the pattern to follow
- `tests/codemie/service/budget/test_budget_service_activity.py` (25.9 KB) — activity events asserted; no logger assertions
- `tests/codemie/service/budget/test_budget_service.py` (37.1 KB) — one `mock_logger.warning` assertion
- `tests/codemie/service/budget/test_project_budget_service_lifecycle.py` (20.6 KB) — no logger or activity event assertions for override/clear/delete_group paths
- `tests/codemie/service/budget/test_project_budget_service.py` (9.7 KB) — no logger assertions
- `tests/codemie/service/budget/test_project_budget_service_group_creation.py` (3.4 KB) — no logger assertions

### Testing Framework and Patterns

- pytest + pytest-asyncio; `@pytest.mark.asyncio` required on async tests
- Logger mock: `@patch("codemie.service.<module_path>.logger")` → `mock_logger` parameter
- Logger assertion: `mock_logger.info.assert_called_once()` or content: `assert "field=value" in mock_logger.info.call_args[0][0]`
- Activity event assertion: `mock_activity.insert.assert_called_once()`, then `call_args[0][0].domain == ActivityDomain.X`
- `AsyncMock` for async repository methods; `MagicMock` for sync
- Session mocked via `mock_get_session.return_value.__enter__.return_value`

### Coverage Gaps

| Service method | logger.info test | activity event test |
|---|---|---|
| `project_service.create_shared_project` | missing | missing |
| `project_service.update_project` | missing | missing |
| `project_budget_service.override_member_allocation` | missing (no call in impl) | missing |
| `project_budget_service.clear_member_override` | missing (no call in impl) | missing |
| `project_budget_service.delete_project_budget_group` | missing | missing (no call in impl) |
| `budget_service.assign_budget_to_user` | missing (no call in impl) | present in test_budget_service_activity.py |
| `budget_service.bulk_set_user_budgets` | missing (no call in impl) | present in test_budget_service_activity.py |

---

## 5. Configuration and Environment

### Environment Variables

- `LOG_LEVEL` — controls logger verbosity; default `INFO` in `src/codemie/configs/config.py`; production config in `log_conf.prod.json`

### Configuration Files

- `src/codemie/configs/config.py` — `LOG_LEVEL: str = "INFO"` default
- `log_conf.prod.json` — production log configuration

### Feature Flags and Deployment Concerns

No logging-specific feature flags found. Logging is always-on at INFO level. No deployment changes needed.

---

## 6. Risk Indicators

- **Inconsistent log message styles across domains** — user management, project assignment, and budget all use slightly different key=value formats. Spec must choose whether to normalize or follow the per-domain style already in place.
- **Large service files** — `project_budget_service.py` is 2119 lines; `budget_service.py` is 1600 lines. Target methods are spread across the file; careful scope control needed to avoid unintended changes.
- **Async vs sync dispatch** — `project_budget_service.py` uses `async_insert`; `budget_service.py` and `project_service.py` use sync `insert`. Mixing them incorrectly causes runtime errors.
- **Activity enum values** — `ProjectManagementEvent` and `BudgetManagementEvent` enums must already contain appropriate event types for the operations being covered; verify before implementing. If missing, new enum values must be added without breaking existing activity event queries.
- **Test assertion depth** — existing tests for the reference path (`project_assignment_service`) assert only `assert_called_once()`, not message content. Scope decision: match that shallow assertion style, or add content assertions for this task?
- **No logger.info tests in user_management reference** — the stated "reference" (`user_management_service`) has activity event tests but does NOT assert `logger.info`. The actual logger assertion reference is `test_project_assignment_service.py`.
- **`delete_project_budget_group` already has logger.info** — only the activity event insert is missing here; do not duplicate the logger call.
- **.env modified in working tree** — local config changes present; not related to this task but could cause test environment drift if env vars changed.

---

## 7. Summary for Complexity Assessment

**Layers and file surface:** The task touches only the service layer — no router, repository, or model schema changes. The target files are `project_service.py` (2 methods), `project_budget_service.py` (3 methods), and `budget_service.py` (2 methods). Supporting test files exist for all three and need assertions added or new test cases for the uncovered paths. `activity_models.py` may need new enum values checked/added, which is a small schema change with no migration impact (enums are Python-side only).

**Technical novelty:** This is a gap-fill task following an established pattern. The two-track audit pattern (logger.info + activity_event_repository insert) is already demonstrated in `project_service._check_and_delete_project`, `project_assignment_service`, and all of `user_management_service`. The main risk is identifying the correct async/sync dispatch for each target method and choosing correct enum values. No new abstractions or infrastructure is needed.

**Test coverage posture and key risks:** Test coverage for the exact paths being changed is sparse — most test files for project and budget do not mock or assert the logger at all. New test cases (or assertions added to existing tests) are needed for each operation. The largest risk is the size of `project_budget_service.py` (2119 lines) and the spread of target methods within it; careful localized edits are required. A secondary risk is ActivityEvent enum completeness — if `ProjectManagementEvent` or `BudgetManagementEvent` are missing the relevant event type values, a new enum member must be added before it can be referenced in service code.
