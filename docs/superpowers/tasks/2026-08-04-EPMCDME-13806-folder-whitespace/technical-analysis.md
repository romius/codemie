# Technical Research

**Task**: folder conversation_folder conversation_service migration
**Generated**: 2026-08-04
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-13806: trim leading/trailing whitespace from folder names across all backend write paths (create, bulk-import upsert, rename, move-chat-to-folder, folder-clear) and read-side serialization, and migrate existing whitespace-containing folder names in production data.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/models/conversation.py:225` — `class Conversation(BaseModelWithSQLSupport, Owned, table=True)`, `__tablename__ = "conversations"`; `folder: Optional[str] = None` at line 233. Plain string column, **not** an FK to `ConversationFolder`. Two tables must be kept consistent by any migration/fix.
- `src/codemie/rest_api/models/conversation_folder.py`:
  - `create_folder()` lines 77-93 — writes `folder_name=folder_name` raw (line 89), no `.strip()`.
  - `validate_fields()` lines 53-59 — uniqueness check via `get_by_fields({FOLDER_NAME_KEYWORD: self.folder_name, USER_ID_KEYWORD: self.user_id})` (line 54); exact string match, not normalized (no trim, no case-fold).
  - `touch_folder()` lines 96-112 — no trim.
  - `FOLDER_NAME_KEYWORD = "folder_name.keyword"` (line 28), `USER_ID_KEYWORD = "user_id.keyword"` (line 29).
  - `search_by_name_and_user()` lines 115-155 — uses `LOWER(folder_name) LIKE :pattern` (line 137); lowercases but does not trim.
  - `get_by_folder()` / `delete_by_folder()` exist and are used by the write paths below; no coverage/trim logic in either.
- `src/codemie/rest_api/models/base.py:463-475` — `get_by_fields()` uses plain `==` equality (`cls.get_field_expression(key) == value`, line 474); case- and whitespace-sensitive.
- `src/codemie/service/conversation_service.py` — 5 backend write paths, all pass folder names through raw:
  1. `create_folder` flow: `_handle_conversation_folder()` lines 456-472.
  2. `create_conversation()` lines 553-600, `folder=folder` raw at line 580.
  3. Bulk-import/upsert create: `_create_conversation_with_history()` starts line 396, `folder=request.folder` raw at line 420.
  4. Rename: `update_conversation_folder()` starts line 710 (decorator at 709) — `ConversationFolder.delete_by_folder` then `create_folder`, then loop over `folder_conversations` setting `conversation.folder = new_folder` raw.
  5. Move-chat-to-folder: `update_conversation()` starts line 732 (decorator at 731) — `conversation.folder = request.folder` raw at line 747; `ConversationFolder.touch_folder(request.folder, ...)` raw at line 759.
  6. Folder-clear: `delete_conversation_folder()` sets folder to `""` (per prior notes; not re-verified line-by-line by name in this pass but consistent with pattern above).
  - `search_conversations()` starts line 1461; `folder=chat.folder or None` at line 1497 — read path, no normalization.
- `src/codemie/rest_api/routers/conversation.py` — router endpoints, all pass folder raw:
  - create-folder POST: lines 677-700
  - rename PUT: lines 648-673
  - `GET /conversations/new`: lines 158-180 (folder query param line 161, passed raw line 176)
  - `POST /conversations`: lines 572-613 (`folder=request.folder` line 585)
  - folders-list GET `/conversations/folders/list`: lines 616-624, returns `ConversationFolder.get_all_by_fields(...)` as stored.

### Architecture and Layers Affected

- **API layer**: FastAPI router `conversation.py` (endpoints above) and request DTOs.
- **Service layer**: `conversation_service.py` (5 write-path methods).
- **Model/data layer**: `conversation_folder.py`, `conversation.py` model (`ActiveRecord`-style `BaseModelWithSQLSupport`, no separate repository class — see Patterns below).
- **Migration layer**: Alembic (`src/external/alembic/versions/`), raw-SQL data migration.
- **Read/serialization layer**: response DTOs in `conversation.py` and request DTOs in `core/models.py`.

### Integration Points

- No external services involved; this is a self-contained DB + API change.
- Alembic migration chain: current head is `i1n2t3e4r5a6` (`i1n2t3e4r5a6_add_interactive_features_to_assistants.py`, confirmed via `alembic heads`, 146 files under `src/external/alembic/versions/`). New migration's `down_revision` must be `'i1n2t3e4r5a6'`.

### Patterns and Conventions

- **No repository abstraction for this domain.** `src/codemie/repository/` is for storage-provider abstraction (AWS/Azure/GCP/filesystem), unrelated to `Conversation`/`ConversationFolder`. Those models use `BaseModelWithSQLSupport` ActiveRecord-style methods (`get_by_fields`, `get_all_by_fields`, `.save()`, `.update()`, `.delete()`) directly on the model class — `.ai-run/guides/data/repository-patterns.md`'s "extend the matching repository" guidance does not apply here; fixes belong directly in `conversation_folder.py` / `conversation_service.py`.
- **Established trim idiom already in codebase — reuse it.** `pydantic.StringConstraints(strip_whitespace=True)` is used at:
  - `src/codemie/rest_api/models/index.py:1388` — `cql: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]`
  - `src/codemie/rest_api/models/index.py:1428, 1435` — same for `jql`.
  - `src/codemie/service/analytics/queries/ai_adoption_framework/config.py:1165` — model-level `str_strip_whitespace: True` config flag.
  This is the natural mechanism to apply at the API boundary (request DTOs) rather than hand-rolling `.strip()` calls in 5 separate service methods.
- **Request DTOs that carry folder names** (best trim point, complementary to service-layer fixes) — all in `src/codemie/core/models.py` except one:
  - `UpdateConversationFolderRequest` (rename/create-folder) — lines 689-690, `folder: str` (required).
  - `CreateConversationRequest` — lines 682-686, `folder: Optional[str] = None`.
  - `UpdateConversationRequest` (move-chat-to-folder) — lines 693-700, `folder: Optional[str] = None`.
  - `UpsertHistoryRequest` (bulk-import/upsert) — `src/codemie/rest_api/models/conversation.py:160-168`, `folder: Optional[str] = Field(default=None, ...)` at line 167.
  - Applying `Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]` to the required-folder DTO rejects empty-after-trim for free; `Optional` variants need an explicit "becomes empty after trim" check since `min_length` is bypassed when value is `None`.
- **Migration precedent**: `src/external/alembic/versions/234f8f339638_backfill_conversation_names.py` (revision `234f8f339638`, down_revision `d7e8f9a0b1c2`). `upgrade()` uses raw SQL via `sqlalchemy.text()` through `op.get_bind()` (no ORM), batches 1000 rows at a time inside a CTE using `FOR UPDATE SKIP LOCKED`, loops until `rowcount == 0`. `downgrade()` is an intentional no-op with a docstring explaining the backfill isn't safely reversible — same posture likely appropriate here since trim + collision-merge is not cleanly reversible. **Caveat**: precedent is single-table (`conversations.conversation_name` only); this task is two-table (`conversation_folders.folder_name` + `conversations.folder`) plus a collision-merge/delete step — precedent covers batching/locking mechanics only, not merge logic, which must be designed fresh.
- Raw SQL in Alembic migrations is accepted practice here despite `.ai-run/guides/data/database-patterns.md` generally preferring SQLModel/SQLAlchemy expressions — that guide's preference applies to app code, not chunked-performance data migrations.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/data/database-patterns.md` — SQLModel/session conventions; migration should still follow raw-SQL/chunked pattern like the precedent, not force ORM into Alembic.
- `.ai-run/guides/data/repository-patterns.md` — describes repository extension pattern; does not apply to this domain (no repository class exists for `Conversation`/`ConversationFolder`).
- `.ai-run/guides/standards/git-workflow.md`, `.ai-run/guides/quality-gates.md` — standard branch/commit/validation conventions, no folder-specific content.
- A prior internal notes file (`notes/projects/EPMCDME-13806-folder-whitespace.md`, not part of the repo/docs tree) already contains detailed prior analysis and a decided collision-handling strategy; this document supersedes/verifies it against current source (see Section 6 for drift).

### Architectural Decisions

- Collision-handling strategy already decided (from prior notes, not yet implemented in code): on trim producing a name collision with an existing folder, merge into the canonical folder, earliest `date` wins, repoint `conversations.folder` string values to the canonical name. No code currently implements or contradicts this — open implementation work.
- Migration `234f8f339638` establishes the project's convention for irreversible data-backfill migrations: no-op `downgrade()` with an explanatory docstring.

### Derived Conventions

- FastAPI request validation conventions favor `Annotated[..., StringConstraints(...)]` for string normalization at the API boundary, per the two prior usages cited above.
- ActiveRecord-style model methods (not repository classes) are the established access pattern for `Conversation`/`ConversationFolder`.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/models/test_conversation_folder.py` — only covers `search_by_name_and_user()` (4 tests). **Zero coverage** of `create_folder()`, `validate_fields()`, `touch_folder()`, `get_by_folder()`, `delete_by_folder()` — the exact methods this ticket must modify.
- `tests/codemie/service/test_conversation_service.py` — folder appears only incidentally as an opaque passthrough value: lines ~109-136 (create + touch_folder mock), ~176-193 (`get_by_folder` mocked to `None`), ~352-356 and ~593-597 (`build_new_conversation` returns folder unchanged). **No tests** for `update_conversation_folder()` (rename), `update_conversation()` (move), `delete_conversation_folder()` (clear), or `_create_conversation_with_history()` folder handling.
- No router-level test found exercising folder trimming/validation behavior in the two files searched.
- No test file found for the `234f8f339638` migration — migrations in this repo appear untested (or tested only implicitly).

### Testing Framework and Patterns

- pytest, per `.ai-run/guides/testing/testing-patterns.md` (not deep-dived this pass — standard project convention).

### Coverage Gaps

- All 5 write paths this ticket touches have no dedicated folder-trim test coverage today — essentially 100% new test surface required.
- No existing scaffolding for asserting uniqueness-on-trim or collision-merge behavior.
- No precedent for testing an Alembic migration script in this repo — new migration will need a test approach decided (or explicitly deferred per project convention).

---

## 5. Configuration and Environment

### Environment Variables

None identified specific to folder handling.

### Configuration Files

None identified specific to folder handling.

### Feature Flags and Deployment Concerns

- None identified. Standard Alembic migration deployment (runs as part of normal migration pipeline) applies to the data-backfill portion.

---

## 6. Risk Indicators

- Zero existing unit test coverage for 4 of the 5 write-path methods (`update_conversation_folder`, `update_conversation`, `delete_conversation_folder`, `_create_conversation_with_history`) and for the mutation methods on `ConversationFolder` itself — all new test surface.
- Two-table consistency requirement (`conversation_folders.folder_name` + `conversations.folder` string) is easy to get half-right; migration must update both, and any future write path must too.
- Collision-merge logic (merge into canonical folder, earliest `date` wins, repoint `conversations.folder`) has no established precedent in this codebase — the cited migration precedent (`234f8f339638`) is single-table and does not cover merge/dedup logic, so this part must be designed and reviewed carefully, especially concurrent-write safety (the precedent's `FOR UPDATE SKIP LOCKED` batching pattern should be adapted, not naively reused, for a merge operation).
- Uniqueness check (`validate_fields` in `conversation_folder.py:53-59`) is currently exact-match; once trimming is introduced, this check must be re-evaluated against trimmed input consistently everywhere trim is applied, or duplicate/near-duplicate folders can still slip through.
- No migration test precedent in this repo — hard to verify migration correctness pre-deploy other than manual/staging verification.
- Minor line-number drift (±1 line) found in `update_conversation_folder()` and `update_conversation()` versus prior notes — cosmetic only, but confirms line numbers should be re-verified again immediately before implementation, not trusted from this document either.

---

## 7. Summary for Complexity Assessment

This task touches four architectural layers: API request DTOs (`core/models.py`, `rest_api/models/conversation.py`), service layer (`conversation_service.py`, 5 distinct write-path methods), model/data layer (`conversation_folder.py`'s create/validate/touch/delete methods plus the `Conversation.folder` string field), and a one-off Alembic data migration. Read-side serialization (`ConversationResponse`, `ConversationListItem`, `SearchResultItem`, folder-list endpoint) is currently a non-issue if write paths are fixed correctly, since those DTOs merely echo stored data — but should be double-checked post-migration. Estimated file-change surface: roughly 4-6 source files (2-4 request DTOs for `StringConstraints`, `conversation_folder.py`, `conversation_service.py`) plus 1 new Alembic migration file, plus new test files for at least 2 existing (currently sparse) test modules.

Technical novelty is low for the trim mechanism itself — the codebase already has an established `StringConstraints(strip_whitespace=True)` idiom used twice elsewhere, so applying it to the 4 folder-carrying request DTOs is low-risk and idiomatic. The higher-novelty piece is the migration's collision-merge logic: the only precedent in the repo is a single-table backfill with no merge/dedup step, so the two-table merge-with-earliest-date-wins logic must be designed from scratch, and its correctness under concurrent access needs explicit attention (adapting the precedent's `FOR UPDATE SKIP LOCKED` batching rather than copying it directly).

Test coverage posture is poor: 4 of 5 write-path methods and all of `ConversationFolder`'s mutation methods have zero existing unit tests, meaning nearly all verification work for this ticket is net-new rather than incremental. Combined with the migration's collision-merge novelty and the two-table consistency requirement, this pushes complexity toward moderate-to-high despite the trim mechanism itself being simple — most of the risk and effort is in the migration correctness and the ground-up test authoring, not the application-code trim logic.
