# EPMCDME-10889: Marketplace clone counter (backend)

## Context

The Marketplace shows like/dislike counts per assistant but has no signal for how often an assistant is cloned. EPMCDME-10889 asks for a `clone_count` metric, persisted per-assistant and displayed alongside likes/dislikes, so clone activity contributes to an assistant's visible popularity. This spec covers backend scope only; frontend (codemie-ui) builds independently against an agreed field-name contract (`source_assistant_id` on the create request, `clone_count` on the response).

No clone tracking exists anywhere today — confirmed via codebase investigation. The closest existing precedent is the reaction (like/dislike) counter feature, which this design's storage architecture mirrors — event table, derived count, write-through cache — while diverging on one point (see Design decision below).

## Scope

In scope:
- `clone_count` field on `AssistantBase` / `AssistantListResponse`, persisted via migration.
- `source_assistant_id` optional field on `AssistantRequest`, used to mark a create request as a clone.
- New `assistant_clone_event` table logging each clone action.
- Repository methods to log a clone event, derive the count, and write it through to the source assistant when a clone-create succeeds.
- Tests for event logging, count derivation, and the create-endpoint wiring.

Out of scope (deferred to follow-up work, not requested by the ticket text as phased):
- Folding `clone_count` into Marketplace ranking/sort order.
- Any explicit access check on `source_assistant_id` (see Design decision below).
- Rate-limiting/throttling clone events (see Known limitation below — the event table is designed so this becomes cheap to add later, but it isn't built now).

## Design decision: event table, no uniqueness constraint

Storage architecture mirrors likes/dislikes: a dedicated event table (`assistant_clone_event`), a count derived from it (`COUNT(*)`), and a cached write-through column (`Assistant.clone_count`) so Marketplace list reads stay O(1) — no join or aggregate on the hot read path.

Where this diverges from likes: likes' `assistant_user_interaction` table has `UniqueConstraint('assistant_id', 'user_id')`, which makes a like a *state* (you either like something or you don't — liking twice is a no-op toggle). A clone is a *repeatable action* — a user cloning the same assistant twice, for two different projects, is two real clone actions. The ticket's acceptance criteria are explicit on this: "increment each time any assistant is cloned," "counter increments reliably on **every** clone action." A uniqueness constraint on `(assistant_id, user_id)` would silently violate that — the second clone by the same user wouldn't increment. So `assistant_clone_event` has **no uniqueness constraint**, only non-unique indexes for query performance (see Architecture).

`source_assistant_id` itself is client-supplied and not validated for existence or access before use. If the referenced assistant doesn't exist (e.g. deleted between page load and submit) or belongs to a project the user can't see, logging the event / deriving the count silently no-ops — the create-assistant request still succeeds regardless. Rationale: `clone_count` is a soft, vanity/ranking metric, not integrity-critical data, and the endpoint is already project-scoped for the *target* project only — a strict validation path would need a new access check on the source assistant for a low-value edge case.

## Architecture

1. **Model layer** — `src/codemie/rest_api/models/assistant.py`:
   - `AssistantBase` (~line 680): add `clone_count: Optional[int] = 0`, alongside `unique_likes_count` / `unique_dislikes_count`.
   - `AssistantListResponse` (~line 572): add the same field.
   - `AssistantRequest` (~line 294): add `source_assistant_id: Optional[str] = None`.

2. **New model** — `src/codemie/rest_api/models/usage/assistant_clone_event.py` (new file, sibling to `assistant_user_interaction.py`):
   - `AssistantCloneEvent` table: `id` (uuid pk), `assistant_id` (indexed), `user_id` (indexed), `created_at` (timestamp, default now).
   - `__table_args__`: non-unique `Index` on `assistant_id`, non-unique `Index` on `user_id`, and a composite `Index('ix_assistant_clone_event_assistant_user_created', 'assistant_id', 'user_id', 'created_at')` — the composite is what makes a future throttling query (`COUNT(*) WHERE assistant_id=X AND user_id=Y AND created_at > now()-interval`) cheap without another migration. Deliberately **no** `UniqueConstraint`.

3. **Migration** — `src/external/alembic/versions/`:
   - Single revision containing both: `op.add_column('assistants', sa.Column('clone_count', sa.Integer(), server_default='0', nullable=True))` and `op.create_table('assistant_clone_event', ...)` with the indexes above.
   - `down_revision` must chain off the actual current head, resolved via `alembic heads` at implementation time — the repo currently has ~19 unmerged heads, so this cannot be assumed from a stale file listing.

4. **Repository** — new `AssistantCloneEventRepository` (`src/codemie/repository/assistants/assistant_clone_event_repository.py`, mirroring `assistant_user_interaction_repository.py`'s shape):
   - `log_clone_event(assistant_id: str, user_id: str) -> None`: insert a row. No dedup check.
   - `get_clone_count(assistant_id: str) -> int`: `COUNT(*) WHERE assistant_id = X` — mirrors `get_like_count()`.

5. **Repository** — `src/codemie/service/assistant/assistant_repository.py`:
   - New static method `update_clone_count(assistant: Assistant, clone_count: int) -> Assistant | None`, mirroring `update_reaction_counts` (lines 267-297): fetch fresh row by id, set `clone_count`, save, return. Returns `None` if the row isn't found (no exception) — keeps the write-through loose, matching the reactions pattern's own "assistant deleted mid-flight" handling.

6. **Service/router wiring** — `src/codemie/rest_api/routers/assistant.py`, `create_assistant` handler (lines 86-190):
   - After `assistant.save()` succeeds, if `request.source_assistant_id` is set: call `AssistantCloneEventRepository.log_clone_event(source_assistant_id, user.id)`, then `get_clone_count(source_assistant_id)`, then `AssistantRepository.update_clone_count(source_assistant, count)` — mirroring `_update_reaction_counts`'s log-then-derive-then-write-through shape exactly.
   - Any exception in this sequence is caught and logged, not propagated — a broken clone_count must never fail assistant creation, since it runs after the primary create action has already committed.

7. **Ranking/sort** — `assistant_repository.py` (~lines 104-132): untouched. Deferred to follow-up.

## Data flow

1. Frontend clone flow sends `POST /v1/assistants` with `source_assistant_id` set to the cloned-from assistant's id.
2. Handler creates the new assistant as normal (unchanged path).
3. On success, handler logs a clone event row (`assistant_id=source_assistant_id, user_id=requesting user`), then recomputes the source assistant's clone count via `COUNT(*)`, then writes it through to `Assistant.clone_count`.
4. Next Marketplace list fetch returns the updated `clone_count` on that assistant via `AssistantListResponse` — a plain column read, no join.

## Error handling

- Missing/invalid `source_assistant_id`: silent no-op (by design, see above).
- Exception anywhere in the log/derive/write-through sequence (DB error, etc.): caught and logged; does not affect the create-assistant response.
- Migration: additive column (nullable, `server_default='0'`) plus a new table — safe for existing rows, no backfill needed.

## Testing

- Repository unit test for `AssistantCloneEventRepository`: `log_clone_event` + `get_clone_count` — multiple events from the same user all count (no dedup); count matches row count exactly.
- Repository unit test for `update_clone_count`: existing id → count written; missing id → returns `None`, no exception raised.
- Router/integration test: `POST /v1/assistants` with `source_assistant_id` set increments the source assistant's `clone_count` by 1 per call, including repeat calls from the same user; omitting the field is a no-op (regression guard against accidental unconditional increment).

## Known limitation: no anti-abuse throttling (accepted, follow-up)

`source_assistant_id` has no relationship check to the created assistant's actual content, so repeated `POST /v1/assistants` calls with the same `source_assistant_id` log events and inflate the count with no real cloning taking place. Since `clone_count` is intended to feed Marketplace ranking/visibility (per ticket), this is a ranking-manipulation vector, not just cosmetic.

This was true regardless of storage design — a raw counter increment has the identical exposure. The event-table architecture doesn't close this gap by itself, but it makes the follow-up cheap: the composite `(assistant_id, user_id, created_at)` index already exists, so a future throttling ticket is a `WHERE` clause and a policy decision (what window, what limit), not a new migration or table. It also gives that follow-up (or anyone auditing a disputed ranking) an actual event history to inspect, rather than an opaque integer.

Decision: ship the event-logged raw count as specified in the ticket. Rate-limiting is explicitly deferred to a follow-up ticket, not this scope.

## Frontend contract (already agreed, no coordination needed at merge)

- Request field: `source_assistant_id: Optional[str]` on `POST /v1/assistants`.
- Response field: `clone_count: Optional[int]` on `AssistantListResponse` / `Assistant`.
- `AssistantRequest` must silently drop unknown fields (pydantic default behavior) so frontend can send `source_assistant_id` ahead of this landing without a rejection — confirmed as part of implementation, not assumed.
