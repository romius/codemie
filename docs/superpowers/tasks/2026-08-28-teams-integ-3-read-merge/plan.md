# Retire assistant_project_mapping Feature — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the entire assistant_project_mapping code path (router, service, repository, models, tests, wiring) now that Story 2 dropped its backing table, leaving `GET /v1/settings/project` (already ms_teams-capable) as the sole way to list Teams-enabled assistants.

**Architecture:** Pure deletion, no new logic. Remove files bottom-up (router → service → repository → model) so import errors surface immediately at each step, then clean up `main.py` wiring and delete/verify tests.

**Tech Stack:** FastAPI, SQLModel, pytest.

**Spec:** Requirements supplied inline by the caller (no spec.md) — see task brief in `docs/superpowers/tasks/2026-08-28-teams-integ-3-read-merge/technical-analysis.md` for file/line locations (analysis narrative is superseded; only its file-location facts apply).

## Global Constraints

- Do not modify `project_settings.py`'s create/update/list endpoints.
- Do not write any new read/merge/dual-read logic.
- Do not resolve the `features:teamsBotIntegration` flag's fate; leave `config/customer/customer-config.yaml` untouched.
- Do not touch the Alembic migration files (`7ca305066800_create_assistant_project_mapping.py`, `c3d4e5f6a7b8_migrate_assistant_project_mapping_to_settings.py`) — historical migration history, not application code.
- Commit per task using the repository's existing convention.

---

## Acceptance criteria

- [ ] `assistant_project_mapping.py` router no longer exists and is not registered in `main.py`.
- [ ] `AssistantProjectMappingService` and its exceptions no longer exist anywhere in `src/`.
- [ ] `AssistantProjectMappingRepository` (ABC and SQL impl) no longer exists.
- [ ] `AssistantProjectMappingSQL`, `AssistantProjectFeature`, `AssistantProjectMappingRequest` no longer exist.
- [ ] No file under `src/` or `tests/` imports any of the above (verified by grep).
- [ ] The app still starts (imports resolve) with the router removed from `main.py`.
- [ ] `project_settings.py`'s create/update/list endpoints are byte-for-byte unchanged.
- [ ] `config/customer/customer-config.yaml`'s `teamsBotIntegration` entry is unchanged.
- [ ] Alembic migration files referencing `assistant_project_mapping` are unchanged.

---

### Task 1: Remove router registration from `main.py`

**Files:**
- Modify: `src/codemie/rest_api/main.py:59` (remove `assistant_project_mapping,` from the router import block), `:830` (remove `app.include_router(assistant_project_mapping.router)`)

**Interfaces:** None — pure removal.

- [ ] **Step 1:** Delete line 59 (`assistant_project_mapping,`) and line 830 (`app.include_router(assistant_project_mapping.router)`) from `main.py`.
- [ ] **Step 2:** Run `python -c "import codemie.rest_api.main"` — expect `ModuleNotFoundError`/`ImportError` at this point because `assistant_project_mapping.py` still exists but is now unused; if it imports cleanly, that's fine too (both are acceptable transitional states before Task 2 deletes the router file).
- [ ] **Step 3:** Commit.

Test-first: no — deleting a dead import/registration; verification is the import check above, not a new test.

---

### Task 2: Delete the router file

**Files:**
- Delete: `src/codemie/rest_api/routers/assistant_project_mapping.py`

- [ ] **Step 1:** Delete the file.
- [ ] **Step 2:** Run `python -c "import codemie.rest_api.main"` — expect success (no more references to the deleted module from `main.py` after Task 1).
- [ ] **Step 3:** Commit.

Test-first: no — no behavior is being added; correctness is proven by the app importing cleanly.

---

### Task 3: Delete the service file

**Files:**
- Delete: `src/codemie/service/assistant/assistant_project_mapping_service.py`

- [ ] **Step 1:** Delete the file (removes `AssistantProjectMappingService`, `AssistantProjectMappingNotFound`, `AssistantProjectMappingForbidden`).
- [ ] **Step 2:** `grep -rn "assistant_project_mapping_service\|AssistantProjectMappingService\|AssistantProjectMappingNotFound\|AssistantProjectMappingForbidden" src/` — expect no matches.
- [ ] **Step 3:** Commit.

Test-first: no.

---

### Task 4: Delete the repository file

**Files:**
- Delete: `src/codemie/repository/assistants/assistant_project_mapping_repository.py`

- [ ] **Step 1:** Delete the file (removes `AssistantProjectMappingRepository` ABC and `SQLAssistantProjectMappingRepository`/`AssistantProjectMappingRepositoryImpl`).
- [ ] **Step 2:** `grep -rn "assistant_project_mapping_repository\|AssistantProjectMappingRepository" src/` — expect no matches.
- [ ] **Step 3:** Commit.

Test-first: no.

---

### Task 5: Delete the model file

**Files:**
- Delete: `src/codemie/rest_api/models/usage/assistant_project_mapping.py`

- [ ] **Step 1:** Delete the file (removes `AssistantProjectMappingSQL`, `AssistantProjectFeature`, `AssistantProjectMappingRequest`).
- [ ] **Step 2:** `grep -rln "assistant_project_mapping\|AssistantProjectMapping" src/ | grep -v "src/external/alembic/versions/"` — expect no matches (the two named Alembic migration files are the only permitted remaining hits and are excluded by this grep).
- [ ] **Step 3:** Run `python -c "import codemie.rest_api.main"` — expect success.
- [ ] **Step 4:** Commit.

Test-first: no.

---

### Task 6: Delete obsolete unit tests

**Files:**
- Delete: `tests/codemie/service/assistant/test_assistant_project_mapping_service.py`
- Delete: `tests/codemie/repository/assistants/test_assistant_project_mapping_repository.py`

(No router-level test file for `assistant_project_mapping.py` exists — confirmed by search; nothing further to delete there.)

- [ ] **Step 1:** Delete both files.
- [ ] **Step 2:** `grep -rln "assistant_project_mapping\|AssistantProjectMapping" tests/` — expect no matches.
- [ ] **Step 3:** Run `pytest tests/codemie/service/assistant/ tests/codemie/repository/assistants/ -v` — expect the suite collects and passes with no reference to the deleted classes.
- [ ] **Step 4:** Commit.

Test-first: no — removing tests for deleted code, not adding behavior.

---

## Negative-constraint pass

- No task adds read/merge/dual-read logic — every task is a file deletion or a two-line removal in `main.py`. **Pass.**
- No task modifies `project_settings.py` — it is never referenced in any task's Files list. **Pass.**
- No task touches `config/customer/customer-config.yaml` or the `teamsBotIntegration` flag — not referenced in any task. **Pass.**
- No task modifies the Alembic migration files under `src/external/alembic/versions/` — Task 5's grep explicitly excludes them and no task lists them as Modify/Delete. **Pass.**
- negative-constraints: all four addressed above; none skipped.
