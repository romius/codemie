# Marketplace Clone Counter (Backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track and expose `clone_count` per assistant on the Marketplace, incrementing every time an assistant is cloned via `POST /v1/assistants` with `source_assistant_id` set.

**Architecture:** Mirrors the existing like/dislike reaction feature's storage shape exactly: a dedicated event table (`assistant_clone_event`), a count derived from it via `COUNT(*)`, and a cached write-through column (`Assistant.clone_count`) for O(1) reads. Unlike likes/dislikes, there is deliberately no uniqueness constraint on the event table — every clone action must count, not just one per user.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy, PostgreSQL, Alembic, pytest (unittest.mock for repository/service unit tests).

## Global Constraints

- No uniqueness constraint on `assistant_clone_event(assistant_id, user_id)` — every clone action increments the count (per ticket EPMCDME-10889 acceptance criteria: "increment each time," "every clone action").
- `source_assistant_id` is not validated for existence/access before use — a missing/inaccessible source silently no-ops (spec decision, see `docs/superpowers/tasks/2026-07-27-marketplace-clone-counter-backend/spec.md`).
- Any exception in the clone-count update sequence must be caught and logged, never propagated — it must not fail assistant creation.
- Migration's `down_revision` must be `i1n2t3e4r5a6` (confirmed current head via `alembic -c src/external/alembic/alembic.ini heads` at plan time — re-verify at implementation time in case new heads landed).
- `AssistantRequest.model_dump(exclude={...})` at `assistant.py:701` must exclude `source_assistant_id`, or it gets passed into the `Assistant(...)` constructor, which has no such field, and will raise.

---

### Task 1: `AssistantCloneEvent` model

**Files:**
- Create: `src/codemie/rest_api/models/usage/assistant_clone_event.py`
- Test: `tests/codemie/rest_api/models/usage/test_assistant_clone_event.py`

**Interfaces:**
- Produces: `AssistantCloneEvent` (table model), fields `id: str`, `assistant_id: str`, `user_id: str`, `created_at: datetime`.

- [ ] **Step 1: Write the failing test**

```python
# tests/codemie/rest_api/models/usage/test_assistant_clone_event.py
from codemie.rest_api.models.usage.assistant_clone_event import AssistantCloneEvent


def test_assistant_clone_event_has_expected_fields():
    event = AssistantCloneEvent(assistant_id="a1", user_id="u1")
    assert event.assistant_id == "a1"
    assert event.user_id == "u1"
    assert event.id  # auto-generated uuid string
    assert event.created_at is not None


def test_assistant_clone_event_table_args_have_no_unique_constraint():
    from sqlalchemy import UniqueConstraint

    for arg in AssistantCloneEvent.__table_args__:
        assert not isinstance(arg, UniqueConstraint), (
            "assistant_clone_event must not have a UniqueConstraint — every clone action must count"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/rest_api/models/usage/test_assistant_clone_event.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codemie.rest_api.models.usage.assistant_clone_event'`

- [ ] **Step 3: Write minimal implementation**

```python
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Model for tracking individual clone actions on an assistant.

Deliberately has NO uniqueness constraint on (assistant_id, user_id): every clone action
must be recorded, unlike a like/dislike reaction which is a toggleable per-user state.
"""

from datetime import datetime, UTC
from uuid import uuid4

from sqlmodel import Field, Index

from codemie.rest_api.models.base import BaseModelWithSQLSupport, CommonBaseModel


class AssistantCloneEventBase(CommonBaseModel):
    """Base model for a single clone action on an assistant."""

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    assistant_id: str = Field(index=True)
    user_id: str = Field(index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    __table_args__ = (
        Index('ix_assistant_clone_event_assistant_id', 'assistant_id'),
        Index('ix_assistant_clone_event_user_id', 'user_id'),
        Index(
            'ix_assistant_clone_event_assistant_user_created',
            'assistant_id',
            'user_id',
            'created_at',
        ),
    )


class AssistantCloneEventSQL(BaseModelWithSQLSupport, AssistantCloneEventBase, table=True):
    """SQLModel version of AssistantCloneEvent for PostgreSQL storage."""

    __tablename__ = "assistant_clone_event"


AssistantCloneEvent = AssistantCloneEventSQL
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/rest_api/models/usage/test_assistant_clone_event.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/models/usage/assistant_clone_event.py tests/codemie/rest_api/models/usage/test_assistant_clone_event.py
git commit -m "EPMCDME-10889: add AssistantCloneEvent model"
```

---

### Task 2: `AssistantCloneEventRepository`

**Files:**
- Create: `src/codemie/repository/assistants/assistant_clone_event_repository.py`
- Test: `tests/codemie/repository/assistants/test_assistant_clone_event_repository.py`

**Interfaces:**
- Consumes: `AssistantCloneEventSQL` from Task 1 (`codemie.rest_api.models.usage.assistant_clone_event`).
- Produces: `AssistantCloneEventRepository.log_clone_event(assistant_id: str, user_id: str) -> None`, `AssistantCloneEventRepository.get_clone_count(assistant_id: str) -> int`.

- [ ] **Step 1: Write the failing test**

```python
# tests/codemie/repository/assistants/test_assistant_clone_event_repository.py
from unittest.mock import MagicMock, patch

from codemie.repository.assistants.assistant_clone_event_repository import (
    AssistantCloneEventRepository,
)


@patch("codemie.repository.assistants.assistant_clone_event_repository.Session")
def test_log_clone_event_inserts_row_without_dedup_check(mock_session_class):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session

    AssistantCloneEventRepository().log_clone_event("assistant-1", "user-1")

    assert mock_session.add.called
    assert mock_session.commit.called


@patch("codemie.repository.assistants.assistant_clone_event_repository.Session")
def test_log_clone_event_allows_repeat_events_from_same_user(mock_session_class):
    """Two clone events from the same user must both be logged — no dedup."""
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session

    repo = AssistantCloneEventRepository()
    repo.log_clone_event("assistant-1", "user-1")
    repo.log_clone_event("assistant-1", "user-1")

    assert mock_session.add.call_count == 2
    assert mock_session.commit.call_count == 2


@patch("codemie.repository.assistants.assistant_clone_event_repository.Session")
def test_get_clone_count_returns_row_count(mock_session_class):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.one.return_value = 3

    result = AssistantCloneEventRepository().get_clone_count("assistant-1")

    assert result == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/repository/assistants/test_assistant_clone_event_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'codemie.repository.assistants.assistant_clone_event_repository'`

- [ ] **Step 3: Write minimal implementation**

```python
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Repository for logging and counting assistant clone events.
"""

from sqlmodel import Session, select, func

from codemie.rest_api.models.usage.assistant_clone_event import AssistantCloneEventSQL


class AssistantCloneEventRepository:
    """Repository for the assistant_clone_event table. No dedup — every clone action counts."""

    def log_clone_event(self, assistant_id: str, user_id: str) -> None:
        """Insert a clone event row. Deliberately no dedup check."""
        event = AssistantCloneEventSQL(assistant_id=assistant_id, user_id=user_id)
        with Session(AssistantCloneEventSQL.get_engine()) as session:
            session.add(event)
            session.commit()

    def get_clone_count(self, assistant_id: str) -> int:
        """Return the total number of clone events for an assistant."""
        with Session(AssistantCloneEventSQL.get_engine()) as session:
            query = select(func.count()).select_from(
                select(AssistantCloneEventSQL)
                .where(AssistantCloneEventSQL.assistant_id == assistant_id)
                .subquery()
            )
            return session.exec(query).one()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/repository/assistants/test_assistant_clone_event_repository.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/repository/assistants/assistant_clone_event_repository.py tests/codemie/repository/assistants/test_assistant_clone_event_repository.py
git commit -m "EPMCDME-10889: add AssistantCloneEventRepository"
```

---

### Task 3: `clone_count` model fields + `AssistantRepository.update_clone_count`

**Files:**
- Modify: `src/codemie/rest_api/models/assistant.py:571` (`AssistantListResponse`), `:294` (`AssistantRequest`), `:681` (`AssistantBase`)
- Modify: `src/codemie/service/assistant/assistant_repository.py:298` (after `update_reaction_counts`, add `update_clone_count`)
- Test: `tests/codemie/service/assistant/test_assistant_repository.py` (append)

**Interfaces:**
- Consumes: `Assistant.find_by_id` (existing, used by `update_reaction_counts`/`increment_usage_count`), `Assistant.save()` (existing).
- Produces: `AssistantRepository.update_clone_count(assistant: Assistant, clone_count: int) -> Assistant` — mirrors `update_reaction_counts`'s exact contract: fetches a fresh row, sets the field, saves, returns the fresh row; if the row isn't found, logs a warning and returns the original `assistant` object unchanged (matches the established `update_reaction_counts`/`increment_usage_count` idiom exactly — not `Optional[Assistant]`/`None`, since that's not what any existing sibling method does).

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/codemie/service/assistant/test_assistant_repository.py

@patch("codemie.service.assistant.assistant_repository.Assistant.find_by_id")
def test_update_clone_count_updates_and_saves(mock_find_by_id, mock_assistant):
    mock_find_by_id.return_value = mock_assistant
    with patch.object(mock_assistant, "save") as mock_save:
        result = AssistantRepository.update_clone_count(mock_assistant, 5)

    assert result.clone_count == 5
    mock_save.assert_called_once()


@patch("codemie.service.assistant.assistant_repository.Assistant.find_by_id")
def test_update_clone_count_returns_original_when_assistant_not_found(mock_find_by_id, mock_assistant):
    mock_find_by_id.return_value = None

    result = AssistantRepository.update_clone_count(mock_assistant, 5)

    assert result is mock_assistant
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/service/assistant/test_assistant_repository.py -k update_clone_count -v`
Expected: FAIL with `AttributeError: type object 'AssistantRepository' has no attribute 'update_clone_count'` (and `mock_assistant.clone_count` won't exist yet either — that surfaces next).

- [ ] **Step 3: Write minimal implementation**

In `src/codemie/rest_api/models/assistant.py`:

```python
# AssistantListResponse, after line 572 (unique_dislikes_count)
    clone_count: Optional[int] = None
```

```python
# AssistantRequest, after line ~353 (custom_metadata / near end of field list — any location among the optional fields is fine)
    source_assistant_id: Optional[str] = None
```

```python
# AssistantBase, after line 681 (unique_dislikes_count)
    clone_count: Optional[int] = SQLField(default=0, index=False)
```

In `src/codemie/service/assistant/assistant_repository.py`, after `update_reaction_counts` (after line 297):

```python
    @staticmethod
    def update_clone_count(assistant: Assistant, clone_count: int) -> Assistant:
        """
        Update the clone count for an assistant.

        Args:
            assistant: The assistant to update
            clone_count: The new clone count

        Returns:
            The updated assistant
        """
        fresh_assistant = Assistant.find_by_id(assistant.id)
        if not fresh_assistant:
            from codemie.configs.logger import logger

            logger.warning(
                f"Unable to fetch assistant {assistant.id} for clone count update. "
                "This may indicate the assistant was deleted or is detached from the session."
            )
            return assistant

        fresh_assistant.clone_count = clone_count
        fresh_assistant.save()
        return fresh_assistant
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/service/assistant/test_assistant_repository.py -k update_clone_count -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/models/assistant.py src/codemie/service/assistant/assistant_repository.py tests/codemie/service/assistant/test_assistant_repository.py
git commit -m "EPMCDME-10889: add clone_count field and AssistantRepository.update_clone_count"
```

---

### Task 4: Alembic migration

**Files:**
- Create: `src/external/alembic/versions/<new_revision_id>_add_clone_count_and_clone_event.py`

**Interfaces:**
- Consumes: nothing from prior tasks directly (schema only); must match the column/table shape defined in Tasks 1 and 3.
- Produces: `assistants.clone_count` column, `assistant_clone_event` table + its 3 indexes (matching Task 1's `__table_args__` exactly).

- [ ] **Step 1: Confirm current head (no test — this is a schema-only task, verified by applying the migration)**

Run: `poetry run alembic -c src/external/alembic/alembic.ini heads`
Expected: single head. As of plan time this was `i1n2t3e4r5a6` — **re-run this command before writing `down_revision`; if a new head has landed, use that value instead.**

- [ ] **Step 2: Generate a new revision id and write the migration**

Run: `poetry run alembic -c src/external/alembic/alembic.ini revision -m "add clone_count and assistant_clone_event"` to get a fresh revision id and template, then replace its contents with:

```python
"""add clone_count and assistant_clone_event

Revision ID: <generated_id>
Revises: i1n2t3e4r5a6
Create Date: <generated>

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "<generated_id>"
down_revision = "i1n2t3e4r5a6"  # confirmed current head at Task 4 Step 1 — re-verify before merging
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assistants",
        sa.Column("clone_count", sa.Integer(), server_default="0", nullable=True),
    )

    op.create_table(
        "assistant_clone_event",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("assistant_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_assistant_clone_event_assistant_id", "assistant_clone_event", ["assistant_id"]
    )
    op.create_index(
        "ix_assistant_clone_event_user_id", "assistant_clone_event", ["user_id"]
    )
    op.create_index(
        "ix_assistant_clone_event_assistant_user_created",
        "assistant_clone_event",
        ["assistant_id", "user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_assistant_clone_event_assistant_user_created", table_name="assistant_clone_event")
    op.drop_index("ix_assistant_clone_event_user_id", table_name="assistant_clone_event")
    op.drop_index("ix_assistant_clone_event_assistant_id", table_name="assistant_clone_event")
    op.drop_table("assistant_clone_event")
    op.drop_column("assistants", "clone_count")
```

- [ ] **Step 3: Verify the migration applies and rolls back cleanly**

Run: `poetry run alembic -c src/external/alembic/alembic.ini upgrade head`
Expected: succeeds, no errors.

Run: `poetry run alembic -c src/external/alembic/alembic.ini downgrade -1`
Expected: succeeds, no errors.

Run: `poetry run alembic -c src/external/alembic/alembic.ini upgrade head` again to leave the DB migrated.
Expected: succeeds.

- [ ] **Step 4: Commit**

```bash
git add src/external/alembic/versions/<new_revision_id>_add_clone_count_and_clone_event.py
git commit -m "EPMCDME-10889: add clone_count column and assistant_clone_event table migration"
```

---

### Task 5: Router wiring in `create_assistant`

**Files:**
- Modify: `src/codemie/rest_api/routers/assistant.py:701` (exclude fix), `:750` (after `_track_mcp_usage_on_create` call, before `_track_assistant_management_metric`)
- Test: `tests/codemie/rest_api/routers/test_assistant.py` (append)

**Interfaces:**
- Consumes: `AssistantCloneEventRepository.log_clone_event`/`get_clone_count` (Task 2), `AssistantRepository.update_clone_count` (Task 3), `request.source_assistant_id` (Task 3 field).
- Produces: nothing new consumed elsewhere — this is the integration point.

- [ ] **Step 1: Write the failing test**

First read `tests/codemie/rest_api/routers/test_assistant.py` to find the existing `create_assistant` test fixtures/client setup and match that pattern exactly (client fixture name, auth override, mock patterns) before writing this test — do not guess the fixture names.

```python
# Append to tests/codemie/rest_api/routers/test_assistant.py
# Adjust fixture names (client, auth override, etc.) to match the file's existing create_assistant tests.

@patch("codemie.rest_api.routers.assistant.AssistantRepository")
@patch("codemie.rest_api.routers.assistant.AssistantCloneEventRepository")
def test_create_assistant_with_source_assistant_id_increments_clone_count(
    mock_clone_event_repo_class, mock_assistant_repo_class, client, ...  # match existing fixtures
):
    mock_clone_event_repo = mock_clone_event_repo_class.return_value
    mock_clone_event_repo.get_clone_count.return_value = 1

    response = client.post(
        "/v1/assistants",
        json={"name": "Cloned Assistant", "project": "DEMO_PROJECT", "source_assistant_id": "source-id-1"},
    )

    assert response.status_code == 200
    mock_clone_event_repo.log_clone_event.assert_called_once()
    args, _ = mock_clone_event_repo.log_clone_event.call_args
    assert args[0] == "source-id-1"
    mock_assistant_repo_class.update_clone_count.assert_called_once()


@patch("codemie.rest_api.routers.assistant.AssistantCloneEventRepository")
def test_create_assistant_without_source_assistant_id_is_noop(mock_clone_event_repo_class, client, ...):
    mock_clone_event_repo = mock_clone_event_repo_class.return_value

    response = client.post(
        "/v1/assistants",
        json={"name": "Plain Assistant", "project": "DEMO_PROJECT"},
    )

    assert response.status_code == 200
    mock_clone_event_repo.log_clone_event.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_assistant.py -k clone_count -v`
Expected: FAIL — `source_assistant_id` clone logging doesn't exist yet, so `log_clone_event`/`update_clone_count` are never called.

- [ ] **Step 3: Write minimal implementation**

In `src/codemie/rest_api/routers/assistant.py`, add imports near the existing `AssistantRepository` import (~line 89):

```python
from codemie.repository.assistants.assistant_clone_event_repository import AssistantCloneEventRepository
```

Fix the exclude set at line 701:

```python
    assistant = Assistant(
        **request.model_dump(
            exclude={"guardrail_assignments", "skip_integration_validation", "source_assistant_id"}
        )
    )
```

After the `_track_mcp_usage_on_create(assistant.mcp_servers)` call (line 750), before `_track_assistant_management_metric("create_assistant", assistant, user, True)` (line 751):

```python
        if request.source_assistant_id:
            try:
                clone_event_repo = AssistantCloneEventRepository()
                clone_event_repo.log_clone_event(request.source_assistant_id, user.id)
                clone_count = clone_event_repo.get_clone_count(request.source_assistant_id)
                source_assistant = Assistant.find_by_id(request.source_assistant_id)
                if source_assistant:
                    AssistantRepository.update_clone_count(source_assistant, clone_count)
            except Exception:
                logger.error(
                    f"Failed to update clone count for source assistant {request.source_assistant_id}",
                    exc_info=True,
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_assistant.py -k clone_count -v`
Expected: PASS

- [ ] **Step 5: Run full existing create_assistant test suite to check for regressions**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_assistant.py -v`
Expected: all PASS, including pre-existing tests (confirms the `exclude` fix and new field didn't break unrelated create flows).

- [ ] **Step 6: Commit**

```bash
git add src/codemie/rest_api/routers/assistant.py tests/codemie/rest_api/routers/test_assistant.py
git commit -m "EPMCDME-10889: wire clone event logging into create_assistant"
```

---

## Self-Review Notes (for the implementer, not a task)

- **Spec coverage:** All in-scope spec items map to a task — model fields (Task 3), event table (Task 1), migration (Task 4), event repository (Task 2), write-through cache method (Task 3), router wiring (Task 5), tests (each task). Ranking/sort integration and access-check validation are explicitly out of scope per spec and have no task here.
- **Type consistency:** `update_clone_count` returns `Assistant` (not `Optional[Assistant]`) to match the established `update_reaction_counts`/`increment_usage_count` sibling methods exactly — this is a deliberate correction from the spec's earlier wording ("returns `None` if not found"), which didn't match the actual codebase idiom found during planning. Implementer should treat this plan's signature as authoritative over the spec's prose on this one point.
- **Known gotcha already fixed in Task 5:** `request.model_dump(exclude=...)` must exclude `source_assistant_id`, or `Assistant(**request.model_dump(...))` raises on the unknown kwarg. Verified against the actual line (`assistant.py:701`) during planning, not assumed.
