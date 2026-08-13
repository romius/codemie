# EPMCDME-13806: Folder whitespace normalization — Spec

## Problem

Backend folder-name write paths accept and persist leading/trailing whitespace unchanged. `"FAQ"` and `" FAQ"` are treated as distinct folders: uniqueness checks use exact string match, so both can exist for the same user, splitting chats across visually identical folders and introducing stray spacing in UI text ("Search in  FAQ chats").

## Scope

Backend (`codemie`) only. Frontend trim-before-send is a separate, already-tracked follow-up ticket sequenced after this lands — not implemented here.

## Delivery split

Two phases, delivered together in this ticket but functionally independent:

1. **Code fix** — trim/validate on all write paths, normalize uniqueness checks, normalize read-side serialization. No data mutation. Fixes the bug for all writes from this point forward.
2. **Migration (gated)** — a new Alembic revision that normalizes existing whitespace-containing folder names and merges trim-collisions. Defaults to dry-run (log-only, no writes); actual application requires an explicit env var, so a human reviews the dry-run report and applies deliberately (e.g. in staging first) rather than the merge firing automatically on next deploy.

This split exists because whether any current usage relies on `" FAQ"` and `"FAQ"` being distinct folders is unconfirmed. The code fix is safe to ship unconditionally (it only affects new writes going forward); the migration touches existing data and must not run silently until that's reviewed.

## Phase 1: Code fix

### Request DTOs — trim at the API boundary

Apply the existing `pydantic.StringConstraints(strip_whitespace=True)` idiom (already used for `cql`/`jql` in `src/codemie/rest_api/models/index.py:1388,1428,1435`) to the four folder-carrying request DTOs:

- `src/codemie/core/models.py` — `UpdateConversationFolderRequest.folder` (required, rename/create-folder path): `Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]`. `min_length=1` rejects empty-after-trim automatically via Pydantic validation (422).
- `src/codemie/core/models.py` — `CreateConversationRequest.folder` (optional): trims via `StringConstraints`, but since it's `Optional[str]`, add an explicit validator that treats an empty-after-trim string as `None` (an empty folder name on an optional field means "no folder", not a validation error) rather than rejecting the request.
- `src/codemie/core/models.py` — `UpdateConversationRequest.folder` (optional, move-chat-to-folder path): same optional-field treatment as above.
- `src/codemie/rest_api/models/conversation.py:167` — `UpsertHistoryRequest.folder` (optional, bulk-import/upsert path): same optional-field treatment as above.

### Model layer — explicit trim at persistence points

`src/codemie/rest_api/models/conversation_folder.py`:

- `create_folder()` (lines 77-93): trim `folder_name` before constructing/saving the row.
- `validate_fields()` (lines 53-59): compare the trimmed `folder_name` in the uniqueness lookup (`get_by_fields`), so `"FAQ"` and `" FAQ"` collide at creation/rename time going forward.
- `touch_folder()` (lines 96-112): trim before use — reached both from DTO-validated paths and from service-layer calls that bypass the DTO layer (e.g. `update_conversation`'s `touch_folder(request.folder, ...)`), so cannot assume the input is already trimmed.

This is belt-and-suspenders with the DTO-layer trim: DTO trim covers everything that enters through a router request body; explicit model-layer trim covers any caller (present or future) that constructs values internally or calls these methods directly.

### Service layer — 5 write paths

`src/codemie/service/conversation_service.py`. With DTO-layer trim in place, these mostly just need to not undo it — but verify each explicitly since some pass raw query params or construct values internally:

1. `_handle_conversation_folder()` (create-folder flow, lines 456-472) — receives an already-trimmed DTO value; no additional change needed beyond confirming it doesn't re-introduce whitespace.
2. `create_conversation()` (lines 553-600, `folder=folder` at line 580) — DTO-trimmed; confirm passthrough.
3. `_create_conversation_with_history()` (bulk-import/upsert, starts line 396, `folder=request.folder` at line 420) — DTO-trimmed; confirm passthrough.
4. `update_conversation_folder()` (rename, starts line 710) — DTO-trimmed for the new name; the *old* folder name used in `ConversationFolder.delete_by_folder()` lookup should also be trimmed defensively (in case an unnormalized value already exists pre-migration).
5. `update_conversation()` (move-chat-to-folder, starts line 732, `conversation.folder = request.folder` at line 747, `touch_folder(request.folder, ...)` at line 759) — DTO-trimmed; confirm passthrough.

`delete_conversation_folder()` (folder-clear, sets folder to `""`) — not a normalization gap; included in the write-path test audit for completeness, no code change expected.

Also verify `GET /conversations/new` (`src/codemie/rest_api/routers/conversation.py:158-180`) — folder arrives as a raw query param, not through a Pydantic body DTO, so this path needs an explicit trim call at the router level (query params aren't covered by the body-DTO `StringConstraints` fix).

### Read-side normalization (defensive layer)

Trim on serialize, independent of whether the underlying row has been migrated yet:

- `ConversationResponse`, `ConversationListItem` (`src/codemie/rest_api/models/conversation.py`)
- `SearchResultItem` / `search_conversations()` (`src/codemie/service/conversation_service.py:1461-1517,818-824`)
- Folder-list endpoint (`GET /conversations/folders/list`, `src/codemie/rest_api/routers/conversation.py:616-624`)

### Error handling

- Empty-after-trim on a *required* folder field → standard Pydantic 422 validation error (no new error type).
- Empty-after-trim on an *optional* folder field → normalized to `None`/no-folder, not an error.
- Trim-collision at create/rename time (trimmed name already exists for user) → existing "folder already exists" error path in `validate_fields`, now trim-aware — this is the mechanism that prevents new duplicates from this point forward.

## Phase 2: Migration (gated)

New Alembic revision, `down_revision = 'i1n2t3e4r5a6'` (current head).

### Modes

Controlled by env var `EPMCDME_13806_APPLY_FOLDER_MERGE` (default unset/false = dry-run):

**Dry-run (default)**: walk `conversation_folders` grouped by `user_id`, group rows within each user by trimmed `folder_name`. For any group with >1 row (a trim-collision) or any single row whose stored name differs from its trimmed form (whitespace-only, no collision), log a line describing the planned action (e.g. `WOULD MERGE 'FAQ ' -> 'FAQ' (user=<id>, 3 conversations repointed)` or `WOULD TRIM ' FAQ' -> 'FAQ' (user=<id>, no collision)`). No writes.

**Apply (`EPMCDME_13806_APPLY_FOLDER_MERGE=true`)**: same detection, then per group:
- Non-colliding whitespace-only name: update `conversation_folders.folder_name` to the trimmed value; update all matching `conversations.folder` rows to the trimmed value.
- Colliding group (>1 row trims to the same name): canonical = row with earliest `date`; set its `folder_name` to the trimmed value; delete the other row(s); update every `conversations.folder` value that matched *any* name in the group (trimmed or not) to the canonical trimmed name.

Both tables (`conversation_folders`, `conversations`) are updated together per group so they never drift relative to each other mid-migration.

### Mechanics

Adapts the chunked `FOR UPDATE SKIP LOCKED` batching pattern from `src/external/alembic/versions/234f8f339638_backfill_conversation_names.py` — raw SQL via `sqlalchemy.text()`/`op.get_bind()`, no ORM, looping until no more rows match. The precedent is single-table with no merge step; this migration's grouping/merge logic is new and has no in-repo precedent, so it needs explicit review and — given the irreversibility below — should be run against a staging snapshot before production.

`downgrade()` is a no-op with a docstring explaining why (matches the `234f8f339638` convention: a merge that deleted rows and repointed foreign strings cannot be safely un-merged).

### Rollout

1. Deploy Phase 1 (code fix) — stops new duplicates immediately.
2. Run the migration in dry-run mode against production (or a recent snapshot) and review the log output with the ticket reporter (Vira Melnyk) — confirms or refutes whether any existing `" FAQ"`/`"FAQ"`-style pair is intentionally distinct.
3. Only after that review, re-run with `EPMCDME_13806_APPLY_FOLDER_MERGE=true` to apply.

## Testing

- `tests/codemie/rest_api/models/test_conversation_folder.py` — new tests for `create_folder()`, `validate_fields()`, `touch_folder()` with leading/trailing-whitespace inputs (currently zero coverage on these methods).
- `tests/codemie/service/test_conversation_service.py` — new tests for all 5 write paths asserting trimmed persistence and trim-aware collision rejection (currently only `create_folder`/`get_by_folder` are incidentally covered as passthrough).
- DTO-level tests: whitespace input trimmed, empty-after-trim rejected (required fields) or normalized to `None` (optional fields).
- Migration: script-level test harness against a seeded test DB — dry-run mode asserts log output and zero writes; apply mode asserts both tables end up consistent for a colliding group and a non-colliding whitespace-only group. No existing migration-test precedent in this repo, so this is new scaffolding.

## Out of scope

- Frontend trim-before-send (`chats.ts`, `FolderFormPopup.tsx`, defensive UI trims) — separate follow-up ticket.
- Automatic/unconditional migration execution — gated per above; running the apply mode in production is an explicit operational decision, not part of this ticket's deploy.
