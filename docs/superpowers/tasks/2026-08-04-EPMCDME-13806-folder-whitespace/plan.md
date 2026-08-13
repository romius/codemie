# EPMCDME-13806: Folder Whitespace Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trim leading/trailing whitespace from conversation folder names on every backend write path and read-side response, and provide a separate, gated (dry-run-by-default) migration to normalize existing data.

**Architecture:** Trim at the API boundary via Pydantic `StringConstraints(strip_whitespace=True)` on request DTOs (matching the existing `cql`/`jql` idiom), plus explicit `.strip()` calls in `ConversationFolder` model methods and one router query-param path that bypass DTO validation. Uniqueness checks compare trimmed values so duplicates can't be created going forward. Response models strip on serialize as a defensive read-side layer. A separate Alembic migration (gated behind an env var, dry-run by default) merges/trims existing data.

**Tech Stack:** FastAPI, Pydantic v2, SQLModel, Alembic, pytest.

## Global Constraints

- Backend (`codemie`) only — no frontend changes in this plan.
- Empty-after-trim on a *required* folder field is a validation error (422); on an *optional* field it normalizes to `None`, not an error.
- The migration's actual data-mutation mode never runs by default — it is gated behind `EPMCDME_13806_APPLY_FOLDER_MERGE=true`; without it, the migration only logs what it would do.
- `downgrade()` for the migration is an intentional no-op with an explanatory docstring, matching `234f8f339638_backfill_conversation_names.py`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/codemie/core/models.py` | `CreateConversationRequest.folder`, `UpdateConversationFolderRequest.folder`, `UpdateConversationRequest.folder` — trim/validate at API boundary |
| `src/codemie/rest_api/models/conversation.py` | `UpsertHistoryRequest.folder` (DTO trim); `ConversationListItem.folder`, `ConversationResponse.folder`, `SearchResultItem.folder` (read-side trim) |
| `src/codemie/rest_api/models/conversation_folder.py` | `create_folder()`, `validate_fields()`, `touch_folder()` — explicit trim at persistence/lookup points |
| `src/codemie/service/conversation_service.py` | `update_conversation_folder()` — trim the *old* folder name used for lookup |
| `src/codemie/rest_api/routers/conversation.py` | `get_conversation_template()` (`GET /conversations/new`) — trim raw query param |
| `src/external/alembic/versions/<new>_normalize_folder_whitespace.py` | New gated migration (dry-run default, apply via env var) |
| `tests/codemie/core/test_models.py` *(or existing DTO test location)* | New tests for DTO trim/validation |
| `tests/codemie/rest_api/models/test_conversation_folder.py` | New tests for model-layer trim |
| `tests/codemie/service/test_conversation_service.py` | New test for old-folder-name trim on rename |
| `tests/codemie/rest_api/models/test_conversation.py` *(or existing response-model test location)* | New tests for read-side trim |
| `tests/external/alembic/test_normalize_folder_whitespace.py` | New migration test harness (dry-run + apply modes) |

---

### Task 1: Trim folder-carrying request DTOs

**Files:**
- Modify: `src/codemie/core/models.py:682-700` (`CreateConversationRequest`, `UpdateConversationFolderRequest`, `UpdateConversationRequest`)
- Modify: `src/codemie/rest_api/models/conversation.py:160-168` (`UpsertHistoryRequest`)
- Test: `tests/codemie/core/test_models.py` (new file if none exists for this module)

**Interfaces:**
- Produces: `UpdateConversationFolderRequest.folder: str` — always non-empty after trim (validation error otherwise). `CreateConversationRequest.folder`, `UpdateConversationRequest.folder`, `UpsertHistoryRequest.folder`: `Optional[str]` — trimmed, and normalized to `None` if empty after trim (never an empty string reaching downstream code).

`core/models.py` already imports `StringConstraints` from `pydantic` (line 27-36) and `Annotated` from `typing_extensions` (line 21) — no new imports needed there.

- [ ] **Step 1: Write the failing tests**

Create `tests/codemie/core/test_models.py` (or add to it if it exists — check first with `Read`/`Glob` before creating):

```python
import pytest
from pydantic import ValidationError

from codemie.core.models import (
    CreateConversationRequest,
    UpdateConversationFolderRequest,
    UpdateConversationRequest,
)


def test_update_conversation_folder_request_trims_whitespace():
    request = UpdateConversationFolderRequest(folder="  FAQ  ")
    assert request.folder == "FAQ"


def test_update_conversation_folder_request_rejects_empty_after_trim():
    with pytest.raises(ValidationError):
        UpdateConversationFolderRequest(folder="   ")


def test_create_conversation_request_trims_optional_folder():
    request = CreateConversationRequest(folder="  FAQ  ")
    assert request.folder == "FAQ"


def test_create_conversation_request_normalizes_empty_after_trim_to_none():
    request = CreateConversationRequest(folder="   ")
    assert request.folder is None


def test_create_conversation_request_allows_none_folder():
    request = CreateConversationRequest(folder=None)
    assert request.folder is None


def test_update_conversation_request_trims_optional_folder():
    request = UpdateConversationRequest(folder="  FAQ  ")
    assert request.folder == "FAQ"


def test_update_conversation_request_normalizes_empty_after_trim_to_none():
    request = UpdateConversationRequest(folder="   ")
    assert request.folder is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/core/test_models.py -v -k "folder"`
Expected: FAIL — `AssertionError` (whitespace not trimmed) or no `ValidationError` raised.

- [ ] **Step 3: Implement DTO trim**

In `src/codemie/core/models.py`, replace lines 682-700:

```python
class CreateConversationRequest(ConfiguredModel):
    initial_assistant_id: Optional[str] = None
    folder: Optional[Annotated[str, StringConstraints(strip_whitespace=True)]] = None
    mcp_server_single_usage: Optional[bool] = False
    is_workflow: Optional[bool] = None

    @field_validator("folder")
    @classmethod
    def _empty_folder_to_none(cls, value: Optional[str]) -> Optional[str]:
        return value or None


class UpdateConversationFolderRequest(ConfiguredModel):
    folder: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class UpdateConversationRequest(ConfiguredModel):
    folder: Optional[Annotated[str, StringConstraints(strip_whitespace=True)]] = None
    pinned: Optional[bool] = None
    name: Optional[str] = None
    active_assistant_id: Optional[str] = None
    llm_model: Optional[str] = None
    enable_image_generation: Optional[bool] = None
    image_generation_model: Optional[str] = None

    @field_validator("folder")
    @classmethod
    def _empty_folder_to_none(cls, value: Optional[str]) -> Optional[str]:
        return value or None
```

`field_validator` is already imported at `core/models.py:31`. `StringConstraints(strip_whitespace=True)` trims before the validator runs, so an all-whitespace input arrives as `""`, and `value or None` normalizes it.

In `src/codemie/rest_api/models/conversation.py`, replace lines 160-168:

```python
class UpsertHistoryRequest(BaseModel):
    """
    Request model for upserting conversation history.
    Used by clients to bulk import or incrementally sync conversation data.
    """

    assistant_id: str = Field(description="Assistant ID (can be placeholder for imports)")
    folder: Optional[Annotated[str, StringConstraints(strip_whitespace=True)]] = Field(
        default=None, description="Folder for organizing conversations"
    )
    history: List[GeneratedMessage] = Field(description="List of conversation messages to upsert")

    @field_validator("folder")
    @classmethod
    def _empty_folder_to_none(cls, value: Optional[str]) -> Optional[str]:
        return value or None
```

Add `field_validator`, `StringConstraints` to the `pydantic` import at `conversation.py:22` (currently `from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator`) and add `from typing import Annotated` alongside the existing `from typing import List, Literal, Optional` at line 19 (use `Annotated` from `typing`, not `typing_extensions`, to match this file's existing style).

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/core/test_models.py -v -k "folder"`
Expected: PASS (all 7 tests)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/core/models.py src/codemie/rest_api/models/conversation.py tests/codemie/core/test_models.py
git commit -m "EPMCDME-13806: trim folder-carrying request DTOs"
```

---

### Task 2: Trim and normalize `ConversationFolder` model methods

**Files:**
- Modify: `src/codemie/rest_api/models/conversation_folder.py:53-59,77-93,96-112`
- Test: `tests/codemie/rest_api/models/test_conversation_folder.py`

**Interfaces:**
- Consumes: none new (uses existing `get_by_fields`, `save`, `update` from `BaseModelWithSQLSupport`).
- Produces: `ConversationFolder.create_folder(folder_name: str, user_id: str) -> ConversationFolder` — persists trimmed `folder_name`. `ConversationFolder.validate_fields(self) -> Optional[str]` — uniqueness check against trimmed `self.folder_name`. `ConversationFolder.touch_folder(folder_name: str, user_id: str) -> None` — looks up by trimmed name.

This task belt-and-suspenders the DTO trim from Task 1: any caller of these methods — including ones that bypass a validated DTO — gets correct behavior.

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/rest_api/models/test_conversation_folder.py`:

```python
from unittest.mock import MagicMock, patch

from codemie.rest_api.models.conversation_folder import ConversationFolder


@patch("codemie.rest_api.models.conversation_folder.ConversationFolder.save")
def test_create_folder_trims_whitespace(mock_save):
    mock_save.return_value = None

    folder = ConversationFolder.create_folder(folder_name="  FAQ  ", user_id="user-1")

    assert folder.folder_name == "FAQ"


def test_validate_fields_uses_trimmed_name_for_uniqueness_check():
    folder = ConversationFolder(folder_name="  FAQ  ", user_id="user-1")
    existing = ConversationFolder(id="other-id", folder_name="FAQ", user_id="user-1")

    with patch.object(ConversationFolder, "get_by_fields", return_value=existing):
        result = folder.validate_fields()

    assert result == "Folder name should be unique"


def test_validate_fields_allows_unique_trimmed_name():
    folder = ConversationFolder(folder_name="  New Folder  ", user_id="user-1")

    with patch.object(ConversationFolder, "get_by_fields", return_value=None):
        result = folder.validate_fields()

    assert result == ""


@patch("codemie.rest_api.models.conversation_folder.ConversationFolder.get_by_folder")
def test_touch_folder_trims_before_lookup(mock_get_by_folder):
    existing = MagicMock()
    mock_get_by_folder.return_value = existing

    ConversationFolder.touch_folder(folder_name="  FAQ  ", user_id="user-1")

    mock_get_by_folder.assert_called_once_with("FAQ", "user-1")
    existing.update.assert_called_once_with(refresh=False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/rest_api/models/test_conversation_folder.py -v -k "trim or unique"`
Expected: FAIL — `folder.folder_name == "  FAQ  "` (untrimmed), and `validate_fields`/`touch_folder` compare/lookup on untrimmed value.

- [ ] **Step 3: Implement model-layer trim**

In `src/codemie/rest_api/models/conversation_folder.py`, update `validate_fields` (lines 53-59):

```python
    def validate_fields(self) -> Optional[str]:
        trimmed_name = self.folder_name.strip()
        folder = self.get_by_fields({FOLDER_NAME_KEYWORD: trimmed_name, USER_ID_KEYWORD: self.user_id})
        if folder and folder.id != self.id:
            return 'Folder name should be unique'
        if trimmed_name == "Default":
            return 'This folder name is forbidden'
        return ""
```

Update `create_folder` (lines 77-93):

```python
    @classmethod
    def create_folder(cls, folder_name: str, user_id: str) -> ConversationFolder:
        """
        Create a new conversation folder.

        Args:
            folder_name: Name of the folder to create
            user_id: ID of the user who owns the folder

        Returns:
            The created ConversationFolder instance
        """
        folder_record = cls(
            folder_name=folder_name.strip(),
            user_id=user_id,
        )
        folder_record.save(refresh=True)
        return folder_record
```

Update `touch_folder` (lines 96-112):

```python
    @classmethod
    def touch_folder(cls, folder_name: str, user_id: str) -> None:
        """
        Update the update_date timestamp for a folder.
        This should be called whenever a conversation in the folder is created or updated.

        Args:
            folder_name: Name of the folder to update
            user_id: ID of the user who owns the folder
        """
        if not folder_name or not folder_name.strip():
            # Skip for conversations without a folder
            return

        folder = cls.get_by_folder(folder_name.strip(), user_id)
        if folder:
            folder.update_date = datetime.now(timezone.utc)
            folder.update(refresh=False)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/models/test_conversation_folder.py -v`
Expected: PASS (all tests, including the 4 pre-existing `search_by_name_and_user` tests)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/models/conversation_folder.py tests/codemie/rest_api/models/test_conversation_folder.py
git commit -m "EPMCDME-13806: trim folder name at ConversationFolder persistence and lookup points"
```

---

### Task 3: Trim the two write paths that bypass DTO validation

**Files:**
- Modify: `src/codemie/service/conversation_service.py:710-714` (`update_conversation_folder`)
- Modify: `src/codemie/rest_api/routers/conversation.py:158-180` (`get_conversation_template`)
- Test: `tests/codemie/service/test_conversation_service.py`

**Interfaces:**
- Consumes: `ConversationFolder.delete_by_folder(folder_name: str, user_id: str)` (from Task 2's file, unchanged signature), `ConversationFolder.create_folder(folder_name: str, user_id: str)` (Task 2, now trims internally).
- Produces: no new interfaces — these are internal consistency fixes.

Two remaining gaps not covered by Tasks 1-2:

1. `update_conversation_folder(user, folder, new_folder)` — the *old* `folder` argument comes from the router's `{folder:path}` URL segment (`conversation.py:648-673`, not a DTO body field), used to look up the folder to rename/delete. Pre-migration, a stored folder name might have whitespace; this lookup should trim it so rename/delete still finds the right row.
2. `GET /conversations/new`'s `folder` is a raw FastAPI `Query(None)` parameter (`conversation.py:161`), not a Pydantic body DTO, so Task 1's `StringConstraints` fix does not apply to it.

- [ ] **Step 1: Write the failing test**

Add to `tests/codemie/service/test_conversation_service.py`:

```python
@patch("codemie.service.conversation_service.Conversation.get_all_by_fields")
@patch("codemie.rest_api.models.conversation_folder.ConversationFolder.create_folder")
@patch("codemie.rest_api.models.conversation_folder.ConversationFolder.delete_by_folder")
def test_update_conversation_folder_trims_old_folder_name_for_lookup(
    mock_delete_by_folder,
    mock_create_folder,
    mock_get_all_by_fields,
    mock_user,
):
    mock_get_all_by_fields.return_value = []

    ConversationService.update_conversation_folder(
        user=mock_user,
        folder="  FAQ  ",
        new_folder="FAQ Renamed",
    )

    mock_delete_by_folder.assert_called_once_with("FAQ", mock_user.id)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/service/test_conversation_service.py -v -k "trims_old_folder"`
Expected: FAIL — `delete_by_folder` called with `"  FAQ  "` (untrimmed), not `"FAQ"`.

- [ ] **Step 3: Implement the two fixes**

In `src/codemie/service/conversation_service.py`, update `update_conversation_folder` (lines 709-729):

```python
    @classmethod
    def update_conversation_folder(cls, user: User, folder: str, new_folder: str):
        """Rename a conversation folder."""
        old_folder = folder.strip()
        ConversationFolder.delete_by_folder(old_folder, user.id)
        # Create new folder using model method
        ConversationFolder.create_folder(new_folder, user.id)

        # Update all conversations in the old folder
        folder_conversations = (
            Conversation.get_all_by_fields(
                {
                    CATEGORY_FIELD_KEY: old_folder,
                    USER_FIELD_KEY: user.id,
                }
            )
            or []
        )

        for conversation in folder_conversations:
            conversation.folder = new_folder
            conversation.update(refresh=True)
```

(`new_folder` is already trimmed by Task 1's `UpdateConversationFolderRequest.folder` `StringConstraints`, since the router passes `request.folder` straight through — see `conversation.py:664`.)

In `src/codemie/rest_api/routers/conversation.py`, update `get_conversation_template` (lines 158-180):

```python
def get_conversation_template(
    initial_assistant_id: Optional[str] = Query(None),
    is_workflow: Optional[bool] = Query(False),
    folder: Optional[str] = Query(None),
    user: User = Depends(authenticate),
) -> ConversationResponse:
    """Get a new conversation payload.

    Args:
        initial_assistant_id: Assistant/workflow id to generate conversation data. Optional.
        is_workflow: If true, treat initial_assistant_id as a WorkflowConfig id. Optional.
        folder: Chat folder name. Optional.
    """
    trimmed_folder = folder.strip() if folder else None

    template = ConversationService.build_new_conversation(
        user=user,
        initial_assistant_id=initial_assistant_id,
        is_workflow=bool(is_workflow),
        folder=trimmed_folder or None,
    )

    response = ConversationResponse.model_validate(template)
    return response
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/test_conversation_service.py -v -k "trims_old_folder"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/conversation_service.py src/codemie/rest_api/routers/conversation.py tests/codemie/service/test_conversation_service.py
git commit -m "EPMCDME-13806: trim folder name in rename lookup and query-param write path"
```

---

### Task 4: Read-side normalization (defensive layer)

**Files:**
- Modify: `src/codemie/rest_api/models/conversation.py:728-825` (`ConversationListItem`, `ConversationResponse`, `SearchResultItem`)
- Modify: `src/codemie/rest_api/routers/conversation.py:616-624` (`get_conversation_folder_list`)
- Test: `tests/codemie/rest_api/models/test_conversation_response.py` (new file — check first whether a response-model test file already exists for `conversation.py`)

**Interfaces:**
- Produces: `ConversationListItem.folder`, `ConversationResponse.folder`, `SearchResultItem.folder` — always trimmed (or `None`) once serialized through these models, regardless of whether the underlying row has been migrated.

This is intentionally independent of Task 5's migration: it protects the API response shape even for rows the migration hasn't touched yet (or when the migration is deliberately run in dry-run-only mode indefinitely).

- [ ] **Step 1: Write the failing tests**

Create `tests/codemie/rest_api/models/test_conversation_response.py`:

```python
from datetime import datetime

from codemie.rest_api.models.conversation import ConversationListItem, ConversationResponse, SearchResultItem


def test_conversation_list_item_trims_folder():
    item = ConversationListItem(id="1", folder="  FAQ  ", date=datetime(2026, 1, 1))
    assert item.folder == "FAQ"


def test_conversation_list_item_none_folder_stays_none():
    item = ConversationListItem(id="1", folder=None, date=datetime(2026, 1, 1))
    assert item.folder is None


def test_conversation_response_trims_folder():
    response = ConversationResponse(conversation_id="1", folder="  FAQ  ")
    assert response.folder == "FAQ"


def test_search_result_item_trims_folder():
    item = SearchResultItem(
        id="1", name="chat", updated_at=datetime(2026, 1, 1), type="chat", folder="  FAQ  "
    )
    assert item.folder == "FAQ"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/rest_api/models/test_conversation_response.py -v`
Expected: FAIL — `folder == "  FAQ  "` (untrimmed) on all three models.

- [ ] **Step 3: Implement read-side trim**

In `src/codemie/rest_api/models/conversation.py`, add a shared validator and apply it to the three models. Add near the top of the file (after imports, before `class ConversationListItem`):

```python
def _trim_folder(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None
```

Update `ConversationListItem` (lines 728-749) — add the validator:

```python
class ConversationListItem(BaseModel):
    id: str
    name: Optional[str] = None
    folder: Optional[str] = None
    pinned: Optional[bool] = False
    date: datetime
    update_date: Optional[datetime] = None

    assistant_ids: Optional[List[str]] = Field(default_factory=list)
    initial_assistant_id: Optional[str] = None

    very_first_msg_at: Optional[datetime] = None
    very_last_msg_at: Optional[datetime] = None

    # Workflow-specific fields
    is_workflow: Optional[bool] = False
    workflow_id: Optional[str] = None
    conversation_id: Optional[str] = None

    # Assistant display fields
    assistant_icon: Optional[str] = None
    assistant_names: Optional[List[str]] = Field(default_factory=list)

    @field_validator("folder")
    @classmethod
    def _trim_folder_field(cls, value: Optional[str]) -> Optional[str]:
        return _trim_folder(value)
```

Add the same `@field_validator("folder")` / `_trim_folder_field` method to `ConversationResponse` (insert directly before the existing `@model_serializer(mode="wrap")` method at line 793) and to `SearchResultItem` (lines 817-824, add the validator method inside the class body).

`field_validator` is already imported at `conversation.py:22`.

In `src/codemie/rest_api/routers/conversation.py`, update `get_conversation_folder_list` (lines 616-624) to trim on read as well, since it returns `ConversationFolder` rows directly rather than through one of the three response DTOs above:

```python
@router.get(
    "/conversations/folders/list",
    response_model=List[ConversationFolder],
)
def get_conversation_folder_list(user: User = Depends(authenticate)) -> List[BaseModelWithSQLSupport]:
    """
    Get a list if all user folders
    """
    folders = ConversationFolder.get_all_by_fields({"user_id.keyword": user.id})
    for folder in folders or []:
        folder.folder_name = folder.folder_name.strip()
    return folders
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/models/test_conversation_response.py -v`
Expected: PASS (all 4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/models/conversation.py src/codemie/rest_api/routers/conversation.py tests/codemie/rest_api/models/test_conversation_response.py
git commit -m "EPMCDME-13806: trim folder name on read-side serialization"
```

---

### Task 5: Gated Alembic migration for existing data

**Files:**
- Create: `src/external/alembic/versions/<revision>_normalize_folder_whitespace.py` (generate the revision id per repo convention — check the most recent migration files under `src/external/alembic/versions/` for the id format used, e.g. run `alembic revision --rev-id <id> -m "normalize_folder_whitespace"` or follow the existing 12-char lowercase-hex pattern manually if the repo doesn't invoke `alembic revision` for data migrations)
- Test: `tests/external/alembic/test_normalize_folder_whitespace.py`

**Interfaces:**
- Produces: `upgrade()` — dry-run by default, applies when `EPMCDME_13806_APPLY_FOLDER_MERGE` env var is `"true"` (case-insensitive). `downgrade()` — no-op.
- Consumes: raw SQL via `op.get_bind()` / `sqlalchemy.text()`, following `234f8f339638_backfill_conversation_names.py`'s chunked `FOR UPDATE SKIP LOCKED` pattern (adapted for two tables + merge logic, not copied verbatim — the precedent is single-table with no merge step).

**down_revision:** `'i1n2t3e4r5a6'` (current Alembic head, confirmed via technical-analysis.md — re-verify with `alembic heads` immediately before writing this file, since other migrations may have landed on `main` since research was done).

- [ ] **Step 1: Verify current Alembic head**

Run: `poetry run alembic heads`
Expected: single head; use its revision id as `down_revision`. If it differs from `i1n2t3e4r5a6`, use the actual value.

- [ ] **Step 2: Write the failing test**

Create `tests/external/alembic/test_normalize_folder_whitespace.py` (create `tests/external/alembic/__init__.py` too if the directory doesn't already have one — check first):

```python
import os
from unittest.mock import MagicMock, patch

import pytest

from external.alembic.versions import <module_name_of_new_migration> as migration


@pytest.fixture
def mock_connection():
    return MagicMock()


@patch("external.alembic.versions.<module_name_of_new_migration>.op")
def test_upgrade_dry_run_by_default_makes_no_writes(mock_op, mock_connection, monkeypatch):
    monkeypatch.delenv("EPMCDME_13806_APPLY_FOLDER_MERGE", raising=False)
    mock_op.get_bind.return_value = mock_connection

    # Simulate one collision group found, then no more rows
    detect_result = MagicMock()
    detect_result.fetchall.return_value = [
        MagicMock(user_id="user-1", trimmed_name="FAQ", folder_ids=["f1", "f2"], dates=["2026-01-01", "2026-01-02"])
    ]
    mock_connection.execute.return_value = detect_result

    migration.upgrade()

    # Dry-run: only SELECT/detection statements executed, never an UPDATE/DELETE
    executed_sql = " ".join(str(call.args[0]) for call in mock_connection.execute.call_args_list).upper()
    assert "UPDATE" not in executed_sql
    assert "DELETE" not in executed_sql


@patch("external.alembic.versions.<module_name_of_new_migration>.op")
def test_upgrade_applies_when_env_var_set(mock_op, mock_connection, monkeypatch):
    monkeypatch.setenv("EPMCDME_13806_APPLY_FOLDER_MERGE", "true")
    mock_op.get_bind.return_value = mock_connection

    empty_result = MagicMock()
    empty_result.fetchall.return_value = []
    empty_result.rowcount = 0
    mock_connection.execute.return_value = empty_result

    migration.upgrade()

    assert mock_connection.execute.called


def test_downgrade_is_noop():
    # Should not raise, should not touch the connection
    migration.downgrade()
```

Replace `<module_name_of_new_migration>` with the actual filename (without `.py`) once Step 1's revision id is known — e.g. `<revid>_normalize_folder_whitespace`.

- [ ] **Step 3: Run test to verify it fails**

Run: `poetry run pytest tests/external/alembic/test_normalize_folder_whitespace.py -v`
Expected: FAIL — `ModuleNotFoundError` (migration file doesn't exist yet).

- [ ] **Step 4: Implement the migration**

Create `src/external/alembic/versions/<revision>_normalize_folder_whitespace.py`:

```python
"""normalize_folder_whitespace

Revision ID: <revision>
Revises: <down_revision from Step 1>
Create Date: 2026-08-04 00:00:00.000000

"""

import os
from typing import Sequence, Union

from alembic import op
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision: str = '<revision>'
down_revision: Union[str, None] = '<down_revision from Step 1>'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

APPLY_ENV_VAR = "EPMCDME_13806_APPLY_FOLDER_MERGE"


def _apply_mode() -> bool:
    return os.environ.get(APPLY_ENV_VAR, "").strip().lower() == "true"


def upgrade() -> None:
    """Normalize whitespace in conversation_folders.folder_name and repoint conversations.folder.

    Two modes, controlled by the EPMCDME_13806_APPLY_FOLDER_MERGE env var:

    - Dry-run (default, env var unset/false): detects trim-collisions and whitespace-only
      names per user, logs what would change, makes no writes.
    - Apply (env var == "true"): performs the detected changes. For a trim-collision group
      (multiple folder_name rows for the same user that trim to the same string), the row
      with the earliest `date` is canonical: its folder_name is set to the trimmed value,
      the other row(s) are deleted, and every conversations.folder value that matched any
      name in the group is repointed to the canonical trimmed name. For a non-colliding
      whitespace-only name, folder_name is simply trimmed and matching conversations.folder
      rows are updated to match.

    Chunked (1 000 groups per batch) with FOR UPDATE SKIP LOCKED on conversation_folders,
    adapted from 234f8f339638_backfill_conversation_names.py's batching pattern — that
    precedent is single-table with no merge step, so the merge logic here is new.
    """
    connection = op.get_bind()
    apply_mode = _apply_mode()

    while True:
        # Detect one batch of groups (by user_id + trimmed name) that need normalization:
        # either >1 row trimming to the same name (collision) or exactly 1 row whose
        # stored name differs from its trimmed form (whitespace-only, no collision).
        groups = connection.execute(
            text("""
                WITH normalized AS (
                    SELECT id, user_id, folder_name, date, TRIM(folder_name) AS trimmed_name
                    FROM conversation_folders
                    FOR UPDATE SKIP LOCKED
                ),
                candidate_groups AS (
                    SELECT user_id, trimmed_name, COUNT(*) AS row_count,
                           array_agg(id ORDER BY date ASC) AS ids_by_date,
                           array_agg(folder_name) AS original_names
                    FROM normalized
                    GROUP BY user_id, trimmed_name
                    HAVING COUNT(*) > 1
                        OR MAX(folder_name) != trimmed_name
                )
                SELECT * FROM candidate_groups
                LIMIT 1000
            """)
        ).fetchall()

        if not groups:
            break

        for group in groups:
            canonical_id = group.ids_by_date[0]
            losing_ids = group.ids_by_date[1:]
            trimmed_name = group.trimmed_name
            original_names = list(set(group.original_names))

            if not apply_mode:
                if losing_ids:
                    print(
                        f"WOULD MERGE {original_names} -> '{trimmed_name}' "
                        f"(user={group.user_id}, {len(losing_ids)} folder row(s) merged)"
                    )
                else:
                    print(f"WOULD TRIM '{original_names[0]}' -> '{trimmed_name}' (user={group.user_id}, no collision)")
                continue

            # Repoint conversations.folder for every original name in this group to the
            # canonical trimmed name, before mutating conversation_folders.
            connection.execute(
                text("""
                    UPDATE conversations
                    SET folder = :trimmed_name
                    WHERE user_id = :user_id AND folder = ANY(:original_names)
                """).bindparams(trimmed_name=trimmed_name, user_id=group.user_id, original_names=original_names)
            )

            connection.execute(
                text("UPDATE conversation_folders SET folder_name = :trimmed_name WHERE id = :id").bindparams(
                    trimmed_name=trimmed_name, id=canonical_id
                )
            )

            if losing_ids:
                connection.execute(
                    text("DELETE FROM conversation_folders WHERE id = ANY(:losing_ids)").bindparams(
                        losing_ids=losing_ids
                    )
                )

        if not apply_mode:
            # Dry-run never shrinks the candidate set (no writes), so break after one
            # reporting pass instead of looping forever.
            break


def downgrade() -> None:
    """No-op — merged/trimmed folder names cannot be safely restored (rows were deleted)."""
    pass
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/external/alembic/test_normalize_folder_whitespace.py -v`
Expected: PASS (all 3 tests)

- [ ] **Step 6: Verify migration chains cleanly**

Run: `poetry run alembic heads` (expect the new revision as the sole head) and `poetry run alembic history | head -5` (expect the new revision listed with the correct `down_revision`).

- [ ] **Step 7: Commit**

```bash
git add src/external/alembic/versions/<revision>_normalize_folder_whitespace.py tests/external/alembic/test_normalize_folder_whitespace.py
git commit -m "EPMCDME-13806: add gated migration to normalize existing folder whitespace"
```

---

## Rollout note (not a code task — operational, for the PR description / handoff)

1. Deploy Tasks 1-4 (code fix) — stops new duplicates immediately, no data migration involved.
2. Run the Task 5 migration in dry-run mode (env var unset) against production or a recent snapshot; review the printed `WOULD MERGE` / `WOULD TRIM` lines with the ticket reporter (Vira Melnyk) to confirm no existing `" FAQ"`/`"FAQ"`-style pair is intentionally distinct.
3. Only after that review, re-run with `EPMCDME_13806_APPLY_FOLDER_MERGE=true` to apply.
