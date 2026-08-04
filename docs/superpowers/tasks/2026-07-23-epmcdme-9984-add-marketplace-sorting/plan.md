# Plan: EPMCDME-9984 Add sorting options to Assistant Marketplace view

## Requirements

Add sort_by (usage/likes/dislikes/name), sort_order (asc/desc), and group_by_is_global (bool, default true) query parameters to GET /v1/assistants. When group_by_is_global=true, sort within groups (non-global first, then global) preserving existing behavior; when false, sort across the full list. Applies only to MARKETPLACE and PROJECT_WITH_MARKETPLACE scopes. Default behavior (no sort_by) is unchanged.

## Implementation Tasks

### Task 1 — Add AssistantSortBy enum to models
**Files**: `src/codemie/rest_api/models/assistant.py`
**Test-first**: yes — test that enum values equal their string representations
- Add `class AssistantSortBy(str, Enum)` after `AssistantScope` with values: `USAGE = "usage"`, `LIKES = "likes"`, `DISLIKES = "dislikes"`, `NAME = "name"`
- `SortOrder` already exists in `src/codemie/rest_api/models/index.py` — no new enum needed

### Task 2 — Add sort params to index_assistants router
**Files**: `src/codemie/rest_api/routers/assistant.py`
**Test-first**: yes — test HTTP 422 on invalid sort_by value; test default (no sort_by) returns 200
- Import `AssistantSortBy` from `codemie.rest_api.models.assistant`
- Import `SortOrder` from `codemie.rest_api.models.index`
- Add to `index_assistants` signature:
  - `sort_by: AssistantSortBy | None = Query(None, description="Sort assistants by field")`
  - `sort_order: SortOrder = Query(SortOrder.DESC, description="Sort direction")`
  - `group_by_is_global: bool = Query(True, description="When true, non-global assistants appear first within PROJECT_WITH_MARKETPLACE scope")`
- Pass `sort_by`, `sort_order`, `group_by_is_global` to `repository.query()`
- Guard: `sort_by` and `sort_order` and `group_by_is_global` are ignored when `scope == AssistantScope.TEMPLATES`

### Task 3 — Parameterize ORDER BY in AssistantRepository.query()
**Files**: `src/codemie/service/assistant/assistant_repository.py`
**Test-first**: yes — SQL assertion tests via `.compile(compile_kwargs={"literal_binds": True})` for each sort field (usage, likes, dislikes, name) × order (asc, desc) × scope (MARKETPLACE, PROJECT_WITH_MARKETPLACE), plus group_by_is_global=False path
- Add params to `query()` signature: `sort_by: AssistantSortBy | None = None`, `sort_order: SortOrder = SortOrder.DESC`, `group_by_is_global: bool = True`
- Import `AssistantSortBy` (from models) and `SortOrder` (from index)
- Add helper `_build_sort_column(sort_by, sort_order)` → SQLAlchemy column expression:
  - `USAGE` → `Assistant.unique_users_count`
  - `LIKES` → `Assistant.unique_likes_count`
  - `DISLIKES` → `Assistant.unique_dislikes_count`
  - `NAME` → `Assistant.name`
  - Apply `.asc().nullsfirst()` or `.desc().nullslast()` based on sort_order
- MARKETPLACE scope: if sort_by → primary = `_build_sort_column(sort_by, sort_order)`, then `update_date DESC NULLS LAST`, then `id ASC`; else preserve existing `unique_users_count DESC NULLS LAST, update_date DESC NULLS LAST, id ASC`
- PROJECT_WITH_MARKETPLACE scope:
  - if `sort_by is None`: preserve existing CASE-based behavior (unchanged)
  - if `sort_by` set and `group_by_is_global=True`: `is_global ASC`, then `_build_sort_column(sort_by, sort_order)`, then `update_date DESC NULLS LAST`, then `id ASC`
  - if `sort_by` set and `group_by_is_global=False`: `_build_sort_column(sort_by, sort_order)`, then `update_date DESC NULLS LAST`, then `id ASC`
- Other scopes: unchanged

### Task 4 — Alembic migration for sort indexes
**Files**: `src/external/alembic/versions/<new_file>.py`
**Test-first**: no
- Create a new Alembic migration (auto-generate or manual) adding:
  - `ix_assistants_unique_likes_count` — B-tree index on `assistants.unique_likes_count`
  - `ix_assistants_unique_dislikes_count` — B-tree index on `assistants.unique_dislikes_count`
  - `ix_assistants_name_btree` — B-tree index on `assistants.name` (for ORDER BY; separate from existing GIN trigram index)
- Downgrade removes all three indexes
