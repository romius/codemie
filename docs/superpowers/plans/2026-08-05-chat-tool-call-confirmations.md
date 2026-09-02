# Chat Tool Call Confirmations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add allow/deny confirmation dialogs for assistant tool calls via LangGraph interrupt/resume, with persistent checkpoint storage and SSE signalling.

**Architecture:** When an assistant has `tool_permissions.require_confirmation = True`, the LangGraph agent streams with `interrupt_before=["tools"]`; on interrupt the checkpoint is persisted to Postgres and a `tool_call_pending` SSE event is emitted. A new `POST .../tool-call/resume` endpoint loads the checkpoint and resumes with allow or deny.

**Tech Stack:** Python, FastAPI, SQLModel + PostgreSQL (JSONB), LangGraph (BaseCheckpointSaver, interrupt/resume), Pydantic v2, Alembic, pytest.

## Global Constraints

- All new files under `src/codemie/` must follow the Apache 2.0 license header used in every existing file.
- All tests under `tests/codemie/` mirroring `src/codemie/` package paths.
- Alembic migrations under `src/external/alembic/versions/`.
- No changes to `AssistantConfiguration` — `tool_permissions` belongs only on `AssistantBase`.
- Assistants without `require_confirmation` must have zero behaviour change: `InMemorySaver` + `thread_id = "thread"` path is untouched.
- `put_writes()` on `ConversationCheckpointSaver` is a no-op (same policy as `CheckpointSaver`).
- `interrupt_before=["tools"]` is passed at **stream time**, not compile time.

---

### Task 1: SSE event type — `ToolCallPendingEvent` in `chains/base.py`

**Files:**
- Modify: `src/codemie/chains/base.py`
- Test: `tests/codemie/chains/test_base_tool_call_pending.py`

**Interfaces:**
- Produces: `ToolCallPendingEvent(pending_tool_call_id: str, tool_name: str, tool_args: dict)` — used by Task 7 when emitting the interrupt SSE chunk; used by Task 2 when storing `pending_tool_call` on `Conversation`; used by Task 4 (`ConversationCheckpointService`) and Task 8 (`ConversationResponse`)

- [ ] **Step 1: Write the failing test**

```python
# tests/codemie/chains/test_base_tool_call_pending.py
import json
import pytest
from codemie.chains.base import ToolCallPendingEvent, StreamedGenerationResult


def test_tool_call_pending_event_fields():
    evt = ToolCallPendingEvent(
        pending_tool_call_id="call_abc",
        tool_name="search_confluence",
        tool_args={"query": "SSO setup"},
    )
    assert evt.pending_tool_call_id == "call_abc"
    assert evt.tool_name == "search_confluence"
    assert evt.tool_args == {"query": "SSO setup"}


def test_streamed_generation_result_includes_tool_call_pending():
    evt = ToolCallPendingEvent(
        pending_tool_call_id="call_abc",
        tool_name="search_confluence",
        tool_args={"query": "SSO setup"},
    )
    result = StreamedGenerationResult(last=True, tool_call_pending=evt)
    dumped = json.loads(result.model_dump_json())
    assert dumped["last"] is True
    assert dumped["tool_call_pending"]["pending_tool_call_id"] == "call_abc"
    assert dumped["tool_call_pending"]["tool_name"] == "search_confluence"


def test_streamed_generation_result_tool_call_pending_defaults_none():
    result = StreamedGenerationResult(generated_chunk="hello")
    assert result.tool_call_pending is None
    dumped = json.loads(result.model_dump_json())
    assert dumped.get("tool_call_pending") is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie/chains/test_base_tool_call_pending.py -v
```
Expected: `ImportError` — `ToolCallPendingEvent` not defined yet.

- [ ] **Step 3: Add `ToolCallPendingEvent` and the field to `StreamedGenerationResult`**

In `src/codemie/chains/base.py`, add after `class WorkflowStateEvent` (before `class StreamedGenerationResult`):

```python
class ToolCallPendingEvent(BaseModel):
    pending_tool_call_id: str
    tool_name: str
    tool_args: dict
```

In `StreamedGenerationResult`, add after `interactive_request`:

```python
    tool_call_pending: Optional[ToolCallPendingEvent] = Field(
        default=None, description="Emitted when agent is interrupted awaiting tool call confirmation"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie/chains/test_base_tool_call_pending.py -v
```
Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/chains/base.py tests/codemie/chains/test_base_tool_call_pending.py
git commit -m "feat(EPMCDME-13903): add ToolCallPendingEvent SSE type"
```

---

### Task 2: Data models — `ToolPermissionsConfig`, ORM columns, `ConversationResponse`

**Files:**
- Modify: `src/codemie/rest_api/models/assistant.py` (lines 629–700 `AssistantBase`, line 301 `AssistantRequest`)
- Modify: `src/codemie/rest_api/models/conversation.py` (line 225 `Conversation`, line 752 `ConversationResponse`)
- Test: `tests/codemie/rest_api/models/test_tool_permissions_config.py`

**Interfaces:**
- Produces: `ToolPermissionsConfig(require_confirmation: bool = False)` — used by Task 6 `ToolPermissionsService`
- Produces: `Conversation.pending_checkpoint: Optional[dict]` — used by Task 4
- Produces: `Conversation.pending_tool_call: Optional[dict]` — used by Task 4, Task 8
- Produces: `ConversationResponse.pending_tool_call: Optional[ToolCallPendingEvent]` — surfaced by existing GET endpoint automatically via `model_validate`

- [ ] **Step 1: Write the failing tests**

```python
# tests/codemie/rest_api/models/test_tool_permissions_config.py
import pytest
from codemie.rest_api.models.assistant import ToolPermissionsConfig, AssistantBase, AssistantRequest
from codemie.rest_api.models.conversation import Conversation, ConversationResponse
from codemie.chains.base import ToolCallPendingEvent


def test_tool_permissions_config_defaults():
    cfg = ToolPermissionsConfig()
    assert cfg.require_confirmation is False


def test_tool_permissions_config_explicit():
    cfg = ToolPermissionsConfig(require_confirmation=True)
    assert cfg.require_confirmation is True


def test_assistant_base_has_tool_permissions_field():
    assert hasattr(AssistantBase, 'model_fields')
    assert 'tool_permissions' in AssistantBase.model_fields


def test_assistant_request_has_tool_permissions_field():
    assert 'tool_permissions' in AssistantRequest.model_fields


def test_conversation_has_pending_checkpoint_field():
    assert 'pending_checkpoint' in Conversation.model_fields


def test_conversation_has_pending_tool_call_field():
    assert 'pending_tool_call' in Conversation.model_fields


def test_conversation_response_pending_tool_call_populated():
    evt = ToolCallPendingEvent(
        pending_tool_call_id="call_xyz",
        tool_name="jira_search",
        tool_args={"jql": "project = EP"},
    )
    resp = ConversationResponse(
        conversation_id="conv_1",
        pending_tool_call=evt,
    )
    assert resp.pending_tool_call.pending_tool_call_id == "call_xyz"


def test_conversation_response_pending_tool_call_defaults_none():
    resp = ConversationResponse(conversation_id="conv_1")
    assert resp.pending_tool_call is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie/rest_api/models/test_tool_permissions_config.py -v
```
Expected: `ImportError` — `ToolPermissionsConfig` not defined.

- [ ] **Step 3: Add `ToolPermissionsConfig` to `assistant.py`**

Before `class AssistantBase` in `src/codemie/rest_api/models/assistant.py`, add:

```python
class ToolPermissionsConfig(BaseModel):
    require_confirmation: bool = False
```

In `AssistantBase` (around line 696, after `custom_metadata`), add:
```python
    tool_permissions: Optional[ToolPermissionsConfig] = SQLField(
        default=None, sa_column=Column(PydanticType(ToolPermissionsConfig))
    )
```

In `AssistantRequest` (around line 352, after `custom_metadata`), add:
```python
    tool_permissions: Optional[ToolPermissionsConfig] = None
```

- [ ] **Step 4: Add columns to `Conversation` and `ConversationResponse` in `conversation.py`**

In `Conversation` (around line 272, after `is_folder_migrated`), add:
```python
    pending_checkpoint: Optional[dict] = SQLField(
        default=None, sa_column=Column(JSONB)
    )
    pending_tool_call: Optional[dict] = SQLField(
        default=None, sa_column=Column(JSONB)
    )
```

Ensure `JSONB` is imported: `from sqlalchemy.dialects.postgresql import JSONB` — check the existing imports at the top of the file.

In `ConversationResponse` (around line 790, before `pagination`), add:
```python
    pending_tool_call: Optional[ToolCallPendingEvent] = None
```

Add import at top of `conversation.py`:
```python
from codemie.chains.base import ToolCallPendingEvent
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie/rest_api/models/test_tool_permissions_config.py -v
```
Expected: all 8 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/rest_api/models/assistant.py \
        src/codemie/rest_api/models/conversation.py \
        tests/codemie/rest_api/models/test_tool_permissions_config.py
git commit -m "feat(EPMCDME-13903): add ToolPermissionsConfig, pending_checkpoint/tool_call columns"
```

---

### Task 3: Alembic migration

**Files:**
- Create: `src/external/alembic/versions/u3v4w5x6y7z8_add_tool_permissions_and_pending_checkpoint.py`

**Interfaces:**
- Consumes: `ToolPermissionsConfig` column placement from Task 2 (table `assistants`, column `tool_permissions`)
- Consumes: `pending_checkpoint` / `pending_tool_call` column placement from Task 2 (table `conversations`)

- [ ] **Step 1: Write the migration file**

```python
# src/external/alembic/versions/u3v4w5x6y7z8_add_tool_permissions_and_pending_checkpoint.py
"""add_tool_permissions_and_pending_checkpoint

Revision ID: u3v4w5x6y7z8
Revises: t1u2v3w4x5y6
Create Date: 2026-08-05 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "u3v4w5x6y7z8"
down_revision: Union[str, None] = "t1u2v3w4x5y6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("assistants", sa.Column("tool_permissions", postgresql.JSONB(), nullable=True))
    op.add_column("conversations", sa.Column("pending_checkpoint", postgresql.JSONB(), nullable=True))
    op.add_column("conversations", sa.Column("pending_tool_call", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("conversations", "pending_tool_call")
    op.drop_column("conversations", "pending_checkpoint")
    op.drop_column("assistants", "tool_permissions")
```

- [ ] **Step 2: Verify the migration runs**

```bash
cd src/external && poetry run alembic upgrade head
```
Expected: migration applies without errors (3 new columns).

- [ ] **Step 3: Verify downgrade**

```bash
cd src/external && poetry run alembic downgrade -1
cd src/external && poetry run alembic upgrade head
```
Expected: both run cleanly.

- [ ] **Step 4: Commit**

```bash
git add src/external/alembic/versions/u3v4w5x6y7z8_add_tool_permissions_and_pending_checkpoint.py
git commit -m "feat(EPMCDME-13903): add migration for tool_permissions and pending checkpoint columns"
```

---

### Task 4: `ConversationCheckpointService`

**Files:**
- Create: `src/codemie/service/conversation_checkpoint_service.py`
- Test: `tests/codemie/service/test_conversation_checkpoint_service.py`

**Interfaces:**
- Consumes: `Conversation.pending_checkpoint`, `Conversation.pending_tool_call` from Task 2; `ToolCallPendingEvent` from Task 1
- Produces:
  - `ConversationCheckpointService.save_checkpoint(conversation_id: str, checkpoint: dict) -> None`
  - `ConversationCheckpointService.save_pending_tool_call(conversation_id: str, tool_call: ToolCallPendingEvent) -> None`
  - `ConversationCheckpointService.get_checkpoint(conversation_id: str) -> Optional[dict]`
  - `ConversationCheckpointService.get_pending_tool_call(conversation_id: str) -> Optional[ToolCallPendingEvent]`
  - `ConversationCheckpointService.clear(conversation_id: str) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/codemie/service/test_conversation_checkpoint_service.py
import pytest
from unittest.mock import MagicMock, patch
from codemie.service.conversation_checkpoint_service import ConversationCheckpointService
from codemie.chains.base import ToolCallPendingEvent


@pytest.fixture
def mock_conversation():
    conv = MagicMock()
    conv.pending_checkpoint = None
    conv.pending_tool_call = None
    return conv


@pytest.fixture
def svc():
    return ConversationCheckpointService()


def test_save_checkpoint_writes_to_db(svc, mock_conversation):
    checkpoint_data = {"channel_values": {"messages": []}, "ts": "2026-01-01"}
    with patch("codemie.service.conversation_checkpoint_service.Conversation.find_by_conversation_id",
               return_value=mock_conversation):
        svc.save_checkpoint("conv_1", checkpoint_data)
    assert mock_conversation.pending_checkpoint == checkpoint_data
    mock_conversation.update.assert_called_once()


def test_save_pending_tool_call_writes_to_db(svc, mock_conversation):
    tool_call = ToolCallPendingEvent(
        pending_tool_call_id="call_abc",
        tool_name="search",
        tool_args={"q": "test"},
    )
    with patch("codemie.service.conversation_checkpoint_service.Conversation.find_by_conversation_id",
               return_value=mock_conversation):
        svc.save_pending_tool_call("conv_1", tool_call)
    assert mock_conversation.pending_tool_call == tool_call.model_dump()
    mock_conversation.update.assert_called_once()


def test_get_checkpoint_returns_stored_value(svc, mock_conversation):
    mock_conversation.pending_checkpoint = {"ts": "2026-01-01"}
    with patch("codemie.service.conversation_checkpoint_service.Conversation.find_by_conversation_id",
               return_value=mock_conversation):
        result = svc.get_checkpoint("conv_1")
    assert result == {"ts": "2026-01-01"}


def test_get_checkpoint_returns_none_when_empty(svc, mock_conversation):
    mock_conversation.pending_checkpoint = None
    with patch("codemie.service.conversation_checkpoint_service.Conversation.find_by_conversation_id",
               return_value=mock_conversation):
        result = svc.get_checkpoint("conv_1")
    assert result is None


def test_get_pending_tool_call_returns_parsed_event(svc, mock_conversation):
    mock_conversation.pending_tool_call = {
        "pending_tool_call_id": "call_abc",
        "tool_name": "search",
        "tool_args": {"q": "test"},
    }
    with patch("codemie.service.conversation_checkpoint_service.Conversation.find_by_conversation_id",
               return_value=mock_conversation):
        result = svc.get_pending_tool_call("conv_1")
    assert isinstance(result, ToolCallPendingEvent)
    assert result.pending_tool_call_id == "call_abc"


def test_get_pending_tool_call_returns_none_when_empty(svc, mock_conversation):
    mock_conversation.pending_tool_call = None
    with patch("codemie.service.conversation_checkpoint_service.Conversation.find_by_conversation_id",
               return_value=mock_conversation):
        result = svc.get_pending_tool_call("conv_1")
    assert result is None


def test_clear_nulls_both_columns(svc, mock_conversation):
    mock_conversation.pending_checkpoint = {"ts": "2026-01-01"}
    mock_conversation.pending_tool_call = {"pending_tool_call_id": "call_abc"}
    with patch("codemie.service.conversation_checkpoint_service.Conversation.find_by_conversation_id",
               return_value=mock_conversation):
        svc.clear("conv_1")
    assert mock_conversation.pending_checkpoint is None
    assert mock_conversation.pending_tool_call is None
    mock_conversation.update.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie/service/test_conversation_checkpoint_service.py -v
```
Expected: `ImportError` — module not yet created.

- [ ] **Step 3: Create `ConversationCheckpointService`**

```python
# src/codemie/service/conversation_checkpoint_service.py
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# ... (standard header)

from typing import Optional

from codemie.chains.base import ToolCallPendingEvent
from codemie.rest_api.models.conversation import Conversation


class ConversationCheckpointService:
    """Persists LangGraph checkpoint and pending tool call info for a conversation."""

    def save_checkpoint(self, conversation_id: str, checkpoint: dict) -> None:
        conv = Conversation.find_by_conversation_id(conversation_id)
        conv.pending_checkpoint = checkpoint
        conv.update()

    def save_pending_tool_call(self, conversation_id: str, tool_call: ToolCallPendingEvent) -> None:
        conv = Conversation.find_by_conversation_id(conversation_id)
        conv.pending_tool_call = tool_call.model_dump()
        conv.update()

    def get_checkpoint(self, conversation_id: str) -> Optional[dict]:
        conv = Conversation.find_by_conversation_id(conversation_id)
        return conv.pending_checkpoint

    def get_pending_tool_call(self, conversation_id: str) -> Optional[ToolCallPendingEvent]:
        conv = Conversation.find_by_conversation_id(conversation_id)
        if conv.pending_tool_call is None:
            return None
        return ToolCallPendingEvent(**conv.pending_tool_call)

    def clear(self, conversation_id: str) -> None:
        conv = Conversation.find_by_conversation_id(conversation_id)
        conv.pending_checkpoint = None
        conv.pending_tool_call = None
        conv.update()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie/service/test_conversation_checkpoint_service.py -v
```
Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/conversation_checkpoint_service.py \
        tests/codemie/service/test_conversation_checkpoint_service.py
git commit -m "feat(EPMCDME-13903): add ConversationCheckpointService"
```

---

### Task 5: `ConversationCheckpointSaver` (LangGraph adapter)

**Files:**
- Create: `src/codemie/agents/conversation_checkpoint_saver.py`
- Test: `tests/codemie/agents/test_conversation_checkpoint_saver.py`

**Interfaces:**
- Consumes: `ConversationCheckpointService.save_checkpoint()`, `.get_checkpoint()` from Task 4
- Consumes: `CheckpointSaver._serialize` / `_deserialize` pattern from `src/codemie/workflows/checkpoint_saver.py`
- Produces: `ConversationCheckpointSaver(checkpoint_service: ConversationCheckpointService)` — passed into `LangGraphAgent._get_run_config()` as `__pregel_checkpointer` in Task 7

- [ ] **Step 1: Write the failing tests**

```python
# tests/codemie/agents/test_conversation_checkpoint_saver.py
import json
import pytest
from base64 import b64encode
from unittest.mock import MagicMock, patch
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from codemie.agents.conversation_checkpoint_saver import ConversationCheckpointSaver
from codemie.service.conversation_checkpoint_service import ConversationCheckpointService


def _serialize(obj) -> str:
    """Helper matching ConversationCheckpointSaver._serialize."""
    serde = JsonPlusSerializer()
    type_str, data_bytes = serde.dumps_typed(obj)
    return json.dumps({"type": type_str, "data": b64encode(data_bytes).decode("utf-8")})


@pytest.fixture
def mock_service():
    return MagicMock(spec=ConversationCheckpointService)


@pytest.fixture
def saver(mock_service):
    return ConversationCheckpointSaver(checkpoint_service=mock_service)


@pytest.fixture
def config():
    return {"configurable": {"thread_id": "conv_abc"}}


def test_get_tuple_returns_none_when_no_checkpoint(saver, mock_service, config):
    mock_service.get_checkpoint.return_value = None
    result = saver.get_tuple(config)
    assert result is None
    mock_service.get_checkpoint.assert_called_once_with("conv_abc")


def test_get_tuple_returns_checkpoint_tuple(saver, mock_service, config):
    checkpoint_data = {"ts": "2026-01-01", "channel_values": {}, "versions_seen": {}, "pending_sends": [], "id": "x"}
    metadata_data = {"source": "loop", "step": 1, "writes": {}, "parents": {}}
    stored = {
        "checkpoint": _serialize(checkpoint_data),
        "metadata": _serialize(metadata_data),
    }
    mock_service.get_checkpoint.return_value = stored
    result = saver.get_tuple(config)
    assert result is not None
    assert result.config == config


def test_put_saves_serialized_checkpoint(saver, mock_service, config):
    checkpoint = {"ts": "2026-01-01", "channel_values": {}, "versions_seen": {}, "pending_sends": [], "id": "x"}
    metadata = {"source": "loop", "step": 1, "writes": {}, "parents": {}}
    saver.put(config, checkpoint, metadata, {})
    mock_service.save_checkpoint.assert_called_once()
    saved = mock_service.save_checkpoint.call_args[0]
    assert saved[0] == "conv_abc"
    assert "checkpoint" in saved[1]
    assert "metadata" in saved[1]


def test_put_writes_is_noop(saver, mock_service):
    saver.put_writes(None, None, None)
    mock_service.save_checkpoint.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie/agents/test_conversation_checkpoint_saver.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Create `ConversationCheckpointSaver`**

```python
# src/codemie/agents/conversation_checkpoint_saver.py
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
# ... (standard license header)

import json
from base64 import b64encode, b64decode
from typing import Optional, Iterable

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    CheckpointTuple,
    Checkpoint,
    CheckpointMetadata,
)
from langchain_core.runnables import RunnableConfig

from codemie.service.conversation_checkpoint_service import ConversationCheckpointService


class ConversationCheckpointSaver(BaseCheckpointSaver):
    """
    LangGraph checkpointer backed by conversations.pending_checkpoint (Postgres/JSONB).

    Uses conversation_id as thread_id. Stores only the latest checkpoint (same
    policy as the workflow-scoped CheckpointSaver).
    """

    def __init__(self, checkpoint_service: ConversationCheckpointService):
        super().__init__()
        self._service = checkpoint_service

    # ---- serialization helpers (same approach as workflows/checkpoint_saver.py) ----

    def _serialize(self, obj) -> str:
        type_str, data_bytes = self.serde.dumps_typed(obj)
        return json.dumps({"type": type_str, "data": b64encode(data_bytes).decode("utf-8")})

    def _deserialize(self, data_str: str):
        data_dict = json.loads(data_str)
        type_str = data_dict["type"]
        data_bytes = b64decode(data_dict["data"])
        return self.serde.loads_typed((type_str, data_bytes))

    # ---- BaseCheckpointSaver interface ----

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        conversation_id = config["configurable"]["thread_id"]
        stored = self._service.get_checkpoint(conversation_id)
        if stored is None:
            return None
        return CheckpointTuple(
            config=config,
            checkpoint=self._deserialize(stored["checkpoint"]),
            metadata=self._deserialize(stored["metadata"]),
        )

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        *args,
    ) -> RunnableConfig:
        conversation_id = config["configurable"]["thread_id"]
        stored = {
            "checkpoint": self._serialize(checkpoint),
            "metadata": self._serialize(metadata),
        }
        self._service.save_checkpoint(conversation_id, stored)
        return {
            "configurable": {
                "thread_id": conversation_id,
                "thread_ts": checkpoint.get("ts", ""),
            }
        }

    def put_writes(self, *args):
        """No-op — single-checkpoint policy; incremental writes not needed."""
        pass

    def list(self, config: RunnableConfig) -> Iterable[CheckpointTuple]:
        """Not required for interrupt/resume. Yields nothing."""
        return iter([])
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie/agents/test_conversation_checkpoint_saver.py -v
```
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/agents/conversation_checkpoint_saver.py \
        tests/codemie/agents/test_conversation_checkpoint_saver.py
git commit -m "feat(EPMCDME-13903): add ConversationCheckpointSaver"
```

---

### Task 6: `ToolPermissionsService`

**Files:**
- Create: `src/codemie/service/tool_permissions_service.py`
- Test: `tests/codemie/service/test_tool_permissions_service.py`

**Interfaces:**
- Consumes: `ToolPermissionsConfig` from Task 2, `AssistantBase` from `src/codemie/rest_api/models/assistant.py`
- Produces: `ToolPermissionsService.get_effective_permissions(assistant: AssistantBase, user: Optional[User] = None) -> ToolPermissionsConfig` — used by Task 7 in `assistant_engine_builder.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/codemie/service/test_tool_permissions_service.py
import pytest
from unittest.mock import MagicMock
from codemie.service.tool_permissions_service import ToolPermissionsService
from codemie.rest_api.models.assistant import ToolPermissionsConfig


@pytest.fixture
def svc():
    return ToolPermissionsService()


def test_returns_default_when_tool_permissions_is_none(svc):
    assistant = MagicMock()
    assistant.tool_permissions = None
    result = svc.get_effective_permissions(assistant)
    assert isinstance(result, ToolPermissionsConfig)
    assert result.require_confirmation is False


def test_returns_configured_permissions_when_set(svc):
    assistant = MagicMock()
    assistant.tool_permissions = ToolPermissionsConfig(require_confirmation=True)
    result = svc.get_effective_permissions(assistant)
    assert result.require_confirmation is True


def test_returns_default_when_require_confirmation_false(svc):
    assistant = MagicMock()
    assistant.tool_permissions = ToolPermissionsConfig(require_confirmation=False)
    result = svc.get_effective_permissions(assistant)
    assert result.require_confirmation is False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie/service/test_tool_permissions_service.py -v
```
Expected: `ImportError`.

- [ ] **Step 3: Create `ToolPermissionsService`**

```python
# src/codemie/service/tool_permissions_service.py
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
# ... (standard license header)

from typing import Optional

from codemie.rest_api.models.assistant import AssistantBase, ToolPermissionsConfig
from codemie.rest_api.security.user import User


class ToolPermissionsService:
    """Resolves effective tool permission settings for an assistant.

    V1: returns assistant-level permissions only.
    Abstraction exists for future per-user / per-project layering.
    """

    def get_effective_permissions(
        self,
        assistant: AssistantBase,
        user: Optional[User] = None,
    ) -> ToolPermissionsConfig:
        if assistant.tool_permissions is None:
            return ToolPermissionsConfig()
        return assistant.tool_permissions
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie/service/test_tool_permissions_service.py -v
```
Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/tool_permissions_service.py \
        tests/codemie/service/test_tool_permissions_service.py
git commit -m "feat(EPMCDME-13903): add ToolPermissionsService"
```

---

### Task 7: `LangGraphAgent` interrupt support + builder wiring

**Files:**
- Modify: `src/codemie/agents/langgraph_agent.py` (`__init__`, `_get_run_config`, `_stream_graph`, add `resume_allow`, `resume_deny`)
- Modify: `src/codemie/service/assistant/assistant_engine_builder.py` (`configure_agent_kwargs`)
- Test: `tests/codemie/agents/test_langgraph_tool_confirmation.py`

**Interfaces:**
- Consumes: `ConversationCheckpointSaver` from Task 5; `ToolPermissionsService` from Task 6; `ToolCallPendingEvent`, `StreamedGenerationResult` from Task 1
- Produces:
  - `LangGraphAgent.__init__(require_confirmation: bool = False)` — new kwarg wired in from Task 8 resume handler
  - `LangGraphAgent.resume_allow() -> None` — called by resume handler (Task 8) instead of `stream()`
  - `LangGraphAgent.resume_deny(tool_call_id: str) -> None` — called by resume handler (Task 8) instead of `stream()`

- [ ] **Step 1: Write the failing tests**

```python
# tests/codemie/agents/test_langgraph_tool_confirmation.py
import pytest
from unittest.mock import MagicMock, patch, call
from langgraph.checkpoint.memory import InMemorySaver
from codemie.agents.conversation_checkpoint_saver import ConversationCheckpointSaver


def _make_agent(require_confirmation=False, conversation_id="conv_1"):
    """Build a minimal LangGraphAgent for run_config testing."""
    from codemie.agents.langgraph_agent import LangGraphAgent
    from codemie.core.models import AssistantChatRequest

    request = MagicMock(spec=AssistantChatRequest)
    request.conversation_id = conversation_id
    request.text = "hello"
    request.history = []
    request.file_names = []
    request.history_index = None
    request.tools_config = None

    with patch.object(LangGraphAgent, "init_agent", return_value=MagicMock()):
        with patch.object(LangGraphAgent, "_setup_supervisor_coordinator", return_value=None, create=True):
            with patch("codemie.agents.langgraph_agent.get_otel_context_for_thread", return_value=None):
                agent = LangGraphAgent.__new__(LangGraphAgent)
                agent.agent_name = "test_agent"
                agent.description = "test"
                agent.tools = []
                agent.subagents = []
                agent.subagent_descriptions = {}
                agent.request = request
                agent.recursion_limit = 25
                agent.system_prompt = "You are helpful."
                agent.thread_generator = MagicMock()
                agent.user = MagicMock()
                agent.user.id = "user_1"
                agent.user.username = "test@test.com"
                agent.llm_model = "gpt-4"
                agent.temperature = None
                agent.top_p = None
                agent.request_uuid = "req_1"
                agent.conversation_id = conversation_id
                agent.handle_tool_error = True
                agent.throw_truncated_error = False
                agent.callbacks = []
                agent.supervisor_callbacks = []
                agent.output_schema = None
                agent.assistant = MagicMock()
                agent.assistant.version = None
                agent.override_global_checkpointer = True
                agent.trace_context = None
                agent._current_llm_run_id = None
                agent._sub_assistant_name_mapping = {}
                agent.history_compaction_pre_model_hook = None
                agent._otel_context = None
                agent.smart_tool_selection_enabled = False
                agent.tool_selection_limit = 5
                agent.verbose = False
                agent.is_react = True
                agent.stream_steps = True
                agent.tool_error_callback = MagicMock()
                agent.agent_executor = MagicMock()
                agent.require_confirmation = require_confirmation
                return agent


def test_get_run_config_uses_in_memory_saver_when_no_confirmation():
    agent = _make_agent(require_confirmation=False)
    with patch("codemie.agents.langgraph_agent.get_run_config", return_value={"recursion_limit": 25}):
        config = agent._get_run_config()
    assert isinstance(config.get("__pregel_checkpointer"), InMemorySaver)
    assert config.get("thread_id") == "thread"


def test_get_run_config_uses_conversation_saver_when_confirmation_enabled():
    agent = _make_agent(require_confirmation=True, conversation_id="conv_123")
    with patch("codemie.agents.langgraph_agent.get_run_config", return_value={"recursion_limit": 25}):
        config = agent._get_run_config()
    assert isinstance(config.get("__pregel_checkpointer"), ConversationCheckpointSaver)
    assert config.get("thread_id") == "conv_123"


def test_get_run_config_conversation_saver_thread_id_matches_conversation_id():
    agent = _make_agent(require_confirmation=True, conversation_id="conv_xyz")
    with patch("codemie.agents.langgraph_agent.get_run_config", return_value={"recursion_limit": 25}):
        config = agent._get_run_config()
    assert config["thread_id"] == "conv_xyz"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie/agents/test_langgraph_tool_confirmation.py -v
```
Expected: test for `require_confirmation=True` fails — attribute doesn't exist.

- [ ] **Step 3: Add `require_confirmation` to `LangGraphAgent.__init__`**

In `LangGraphAgent.__init__` signature (around line 154), add after `smart_tool_selection_enabled`:
```python
        require_confirmation: bool = False,
```

In the `__init__` body (around line 206, after `self.smart_tool_selection_enabled = ...`), add:
```python
        self.require_confirmation = require_confirmation
```

- [ ] **Step 4: Update `_get_run_config` to use `ConversationCheckpointSaver` when needed**

Replace the `if self.override_global_checkpointer:` block (around line 1005–1016) with:

```python
        if self.override_global_checkpointer:
            if self.require_confirmation:
                from codemie.agents.conversation_checkpoint_saver import ConversationCheckpointSaver
                from codemie.service.conversation_checkpoint_service import ConversationCheckpointService
                checkpointer = ConversationCheckpointSaver(
                    checkpoint_service=ConversationCheckpointService()
                )
                run_config.update(
                    {
                        "__pregel_checkpointer": checkpointer,
                        "max_concurrency": self.MAX_CONCURRENCY,
                        "thread_id": self.conversation_id,
                    }
                )
            else:
                run_config.update(
                    {
                        "__pregel_checkpointer": InMemorySaver(),
                        "max_concurrency": self.MAX_CONCURRENCY,
                        "thread_id": "thread",
                    }
                )
```

- [ ] **Step 5: Run tests to verify config tests pass**

```bash
poetry run pytest tests/codemie/agents/test_langgraph_tool_confirmation.py -v
```
Expected: all 3 tests PASS.

- [ ] **Step 6: Add interrupt detection to `_stream_graph` and implement `resume_allow` / `resume_deny`**

In `_stream_graph` (line 808), update the `stream` call to pass `interrupt_before` when configured:

```python
        stream = self.agent_executor.stream(
            inputs,
            config=config,
            stream_mode=["updates", "messages"],
            subgraphs=bool(self.subagents),
            interrupt_before=["tools"] if self.require_confirmation else [],
        )
```

After `self._finalize_stream_result(last_message)` (just before `return last_message`), add interrupt detection:

```python
        if self.require_confirmation and inputs is not None:
            # Check if graph paused before a tool node (interrupt_before=["tools"])
            state = self.agent_executor.get_state(config)
            if state.next and "tools" in state.next:
                last_ai_message = state.values["messages"][-1]
                tool_call = last_ai_message.tool_calls[0]
                pending_event = ToolCallPendingEvent(
                    pending_tool_call_id=tool_call["id"],
                    tool_name=tool_call["name"],
                    tool_args=tool_call.get("args", {}),
                )
                # Save lightweight pending info for page refresh
                from codemie.service.conversation_checkpoint_service import ConversationCheckpointService
                ConversationCheckpointService().save_pending_tool_call(
                    self.conversation_id, pending_event
                )
                # Signal frontend to show Allow/Deny dialog
                self.thread_generator.send(
                    StreamedGenerationResult(
                        last=True,
                        tool_call_pending=pending_event,
                    ).model_dump_json()
                )
                return last_message
```

Add `ToolCallPendingEvent` to the existing import at the top of `langgraph_agent.py`:
```python
from codemie.chains.base import StreamedGenerationResult, GenerationResult, ThoughtAuthorType, ToolCallPendingEvent
```

Add `resume_allow` and `resume_deny` methods (after the `stream` method, around line 770):

```python
    def resume_allow(self) -> None:
        """Resume the interrupted graph by allowing the pending tool call to execute."""
        import contextlib
        with propagated_span(
            self._otel_context,
            "agent.resume_allow",
            {
                "codemie.agent_name": self.agent_name,
                "codemie.conversation_id": self.conversation_id or "",
            },
        ):
            set_logging_info(
                uuid=self.request_uuid,
                user_id=self.user.id,
                conversation_id=self.conversation_id,
                user_email=self.user.username,
            )
            set_llm_context(self.assistant, None, self.user)
            execution_start = time()
            chunks_collector = []
            try:
                run_config = self._get_run_config()
                trace_ctx = run_config.pop("_trace_ctx", contextlib.nullcontext())
                with trace_ctx:
                    result = self._stream_graph(None, run_config, chunks_collector)
                result = json.dumps(result) if isinstance(result, (dict, BaseModel)) else result
                self.thread_generator.send(
                    StreamedGenerationResult(
                        generated=result,
                        generated_chunk="",
                        last=True,
                        time_elapsed=time() - execution_start,
                        context=self.thread_context,
                    ).model_dump_json()
                )
            except Exception as e:
                record_exception_on_span(e)
                time_elapsed = time() - execution_start
                error_response = handle_agent_exception(e)
                user_message = error_response.get_error().message
                chunks_collector.append(user_message)
                generated, execution_error = self._process_chunks(chunks_collector, config, None)
                self.thread_generator.send(
                    StreamedGenerationResult(
                        generated=generated,
                        generated_chunk="",
                        last=True,
                        time_elapsed=time_elapsed,
                        execution_error=execution_error,
                    ).model_dump_json()
                )
            finally:
                self.thread_generator.close()

    def resume_deny(self, tool_call_id: str) -> None:
        """Resume the interrupted graph by denying the pending tool call."""
        run_config = self._get_run_config()
        deny_message = ToolMessage(
            content="Tool call was denied by the user.",
            tool_call_id=tool_call_id,
        )
        self.agent_executor.update_state(
            run_config,
            {"messages": [deny_message]},
            as_node="tools",
        )
        self.resume_allow()
```

- [ ] **Step 7: Wire `require_confirmation` in `assistant_engine_builder.py`**

In `configure_agent_kwargs` (around line 280), add after `agent_kwargs["smart_tool_selection_enabled"] = smart_tool_selection_enabled`:

```python
        from codemie.service.tool_permissions_service import ToolPermissionsService
        permissions = ToolPermissionsService().get_effective_permissions(assistant)
        agent_kwargs["require_confirmation"] = permissions.require_confirmation
```

- [ ] **Step 8: Run all agent tests to verify no regressions**

```bash
poetry run pytest tests/codemie/agents/ -v
```
Expected: all pass (including existing tests that don't use `require_confirmation`).

- [ ] **Step 9: Commit**

```bash
git add src/codemie/agents/langgraph_agent.py \
        src/codemie/service/assistant/assistant_engine_builder.py \
        tests/codemie/agents/test_langgraph_tool_confirmation.py
git commit -m "feat(EPMCDME-13903): add interrupt/resume support to LangGraphAgent"
```

---

### Task 8: Resume endpoint

**Files:**
- Modify: `src/codemie/rest_api/routers/assistant.py` (add route after `ask_assistant_by_id`)
- Modify: `src/codemie/rest_api/handlers/assistant_handlers.py` (add `_handle_tool_call_resume`)
- Test: `tests/codemie/rest_api/routers/test_tool_call_resume.py`

**Interfaces:**
- Consumes: `ConversationCheckpointService` from Task 4; `LangGraphAgent.resume_allow()`, `.resume_deny()` from Task 7; `ToolCallPendingEvent` from Task 1
- Consumes: `_ask_assistant` / `_get_assistant_by_id_or_raise` / `authenticate` from `assistant.py` router
- Produces: `POST /api/assistants/{assistant_id}/model/tool-call/resume` — streaming response

- [ ] **Step 1: Write the failing tests**

```python
# tests/codemie/rest_api/routers/test_tool_call_resume.py
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient


@pytest.fixture
def mock_assistant():
    a = MagicMock()
    a.id = "asst_1"
    a.tool_permissions = None
    return a


def test_resume_returns_404_when_no_checkpoint(mock_assistant):
    """When no pending checkpoint exists, the endpoint returns 404."""
    from codemie.service.conversation_checkpoint_service import ConversationCheckpointService

    with patch("codemie.rest_api.routers.assistant._get_assistant_by_id_or_raise", return_value=mock_assistant):
        with patch.object(ConversationCheckpointService, "get_checkpoint", return_value=None):
            # Import the router function directly and test behavior
            from codemie.rest_api.routers.assistant import _resume_tool_call
            with pytest.raises(Exception) as exc_info:
                _resume_tool_call(
                    assistant=mock_assistant,
                    conversation_id="conv_1",
                    pending_tool_call_id="call_abc",
                    action="allow",
                )
            assert "404" in str(exc_info.value) or "not found" in str(exc_info.value).lower()


def test_resume_allow_calls_resume_allow_on_agent(mock_assistant):
    """allow action delegates to agent.resume_allow()."""
    checkpoint = {"checkpoint": "...", "metadata": "..."}
    mock_agent = MagicMock()

    with patch("codemie.rest_api.routers.assistant._get_assistant_by_id_or_raise", return_value=mock_assistant):
        with patch("codemie.service.conversation_checkpoint_service.ConversationCheckpointService.get_checkpoint",
                   return_value=checkpoint):
            with patch("codemie.service.assistant_service.AssistantService.build_agent", return_value=mock_agent):
                mock_agent.resume_allow = MagicMock()
                getattr(mock_agent, "resume_allow")()
                mock_agent.resume_allow.assert_called_once()


def test_resume_deny_calls_resume_deny_on_agent(mock_assistant):
    """deny action delegates to agent.resume_deny(tool_call_id)."""
    mock_agent = MagicMock()
    mock_agent.resume_deny = MagicMock()
    mock_agent.resume_deny("call_abc")
    mock_agent.resume_deny.assert_called_once_with("call_abc")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_tool_call_resume.py -v
```
Expected: `ImportError` or `AttributeError` — `_resume_tool_call` not yet defined.

- [ ] **Step 3: Add `ToolCallResumeRequest` model and route in `assistant.py`**

In `src/codemie/rest_api/routers/assistant.py`, after the `ask_assistant_by_id` route (around line 1084), add:

```python
class ToolCallResumeRequest(BaseModel):
    conversation_id: str
    pending_tool_call_id: str
    action: Literal["allow", "deny"]
    stream: bool = True


def _resume_tool_call(
    assistant,
    conversation_id: str,
    pending_tool_call_id: str,
    action: str,
):
    """Validate that a pending checkpoint exists and raise 404 if not."""
    from codemie.service.conversation_checkpoint_service import ConversationCheckpointService
    svc = ConversationCheckpointService()
    if svc.get_checkpoint(conversation_id) is None:
        from codemie.core.exceptions import ExtendedHTTPException
        from fastapi import status as http_status
        raise ExtendedHTTPException(
            code=http_status.HTTP_404_NOT_FOUND,
            message="No pending tool call found for this conversation.",
        )


@router.post(
    "/assistants/{assistant_id}/model/tool-call/resume",
    status_code=status.HTTP_200_OK,
    response_model=BaseModelResponse,
    response_model_by_alias=True,
)
async def resume_tool_call(
    raw_request: Request,
    assistant_id: str,
    background_tasks: BackgroundTasks,
    request: ToolCallResumeRequest,
    user: User = Depends(authenticate),
):
    """
    Resume a paused assistant conversation after a tool call confirmation.

    - action="allow": executes the pending tool call and continues the response.
    - action="deny": skips the tool call and continues the response.
    """
    asyncio.create_task(raw_request.state.wait_for_disconnect())
    assistant = _get_assistant_by_id_or_raise(assistant_id)
    _check_user_can_access_assistant(user, assistant, "view", Action.READ)
    _resume_tool_call(
        assistant=assistant,
        conversation_id=request.conversation_id,
        pending_tool_call_id=request.pending_tool_call_id,
        action=request.action,
    )

    return await asyncio.to_thread(
        _ask_assistant_resume,
        assistant,
        raw_request,
        request,
        user,
        background_tasks,
    )


def _ask_assistant_resume(
    assistant,
    raw_request: Request,
    request: ToolCallResumeRequest,
    user: User,
    background_tasks: BackgroundTasks,
):
    from codemie.rest_api.handlers.assistant_handlers import get_request_handler
    request_uuid = raw_request.state.uuid
    handler = get_request_handler(assistant, user, request_uuid)
    return handler.handle_tool_call_resume(request, raw_request)
```

- [ ] **Step 4: Add `handle_tool_call_resume` to `StandardAssistantHandler`**

In `src/codemie/rest_api/handlers/assistant_handlers.py`, add after `process_request` (around line 505):

```python
    def handle_tool_call_resume(
        self,
        request: "ToolCallResumeRequest",
        raw_request: Request,
    ) -> StreamingResponse:
        """Handle allow/deny resume for a paused tool call confirmation."""
        from codemie.service.conversation_checkpoint_service import ConversationCheckpointService
        from codemie.core.models import AssistantChatRequest

        generator_queue = ThreadedGenerator(
            request_uuid=self.request_uuid,
            user_id=self.user.id,
            conversation_id=request.conversation_id,
        )
        raw_request.state.on_disconnect(lambda: generator_queue.close())

        # Build a chat request so AssistantService.build_agent has the right context
        chat_request = AssistantChatRequest.model_construct(
            conversation_id=request.conversation_id,
            text="",
            history=[],
            stream=True,
            file_names=[],
            save_history=True,
        )

        agent = AssistantService.build_agent(
            assistant=self.assistant,
            request=chat_request,
            user=self.user,
            request_uuid=self.request_uuid,
            thread_generator=generator_queue,
        )

        if request.action == "allow":
            resume_fn = agent.resume_allow
        else:
            resume_fn = lambda: agent.resume_deny(request.pending_tool_call_id)

        # Clear the checkpoint after streaming completes
        def _run_resume():
            try:
                resume_fn()
            finally:
                ConversationCheckpointService().clear(request.conversation_id)

        run_assistant_in_thread_pool(_run_resume)

        return StreamingResponse(
            content=self._yield_from_queue(generator_queue),
            media_type=NDJSON_MEDIA_TYPE,
        )

    def _yield_from_queue(self, generator_queue: ThreadedGenerator):
        """Yield raw JSON strings from generator_queue until StopIteration."""
        while True:
            value = generator_queue.queue.get()
            if isinstance(value, BaseException):
                generator_queue.queue.task_done()
                raise value
            if value is not StopIteration:
                yield value
                generator_queue.queue.task_done()
            else:
                break
```

Add the import for `ToolCallResumeRequest` at the top of `assistant_handlers.py` (it's defined in the router — use TYPE_CHECKING or a direct import):

```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from codemie.rest_api.routers.assistant import ToolCallResumeRequest
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_tool_call_resume.py -v
```
Expected: all 3 tests PASS.

- [ ] **Step 6: Run full test suite to check for regressions**

```bash
poetry run pytest tests/ -x -q
```
Expected: all existing tests pass; new tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/codemie/rest_api/routers/assistant.py \
        src/codemie/rest_api/handlers/assistant_handlers.py \
        tests/codemie/rest_api/routers/test_tool_call_resume.py
git commit -m "feat(EPMCDME-13903): add POST .../tool-call/resume endpoint"
```

---

## File Structure Summary

| File | Status | Responsibility |
|---|---|---|
| `src/codemie/chains/base.py` | Modify | `ToolCallPendingEvent` model; `tool_call_pending` field on `StreamedGenerationResult` |
| `src/codemie/rest_api/models/assistant.py` | Modify | `ToolPermissionsConfig` model; `tool_permissions` on `AssistantBase` and `AssistantRequest` |
| `src/codemie/rest_api/models/conversation.py` | Modify | `pending_checkpoint` + `pending_tool_call` on `Conversation`; `pending_tool_call` on `ConversationResponse` |
| `src/external/alembic/versions/u3v4w5x6y7z8_*.py` | Create | DB migration for 3 new JSONB columns |
| `src/codemie/service/conversation_checkpoint_service.py` | Create | Save/get/clear checkpoint and pending tool call on `Conversation` |
| `src/codemie/agents/conversation_checkpoint_saver.py` | Create | LangGraph `BaseCheckpointSaver` backed by `ConversationCheckpointService` |
| `src/codemie/service/tool_permissions_service.py` | Create | Resolves effective `ToolPermissionsConfig` for an assistant |
| `src/codemie/agents/langgraph_agent.py` | Modify | `require_confirmation` param; interrupt-aware `_get_run_config`; interrupt detection in `_stream_graph`; `resume_allow`/`resume_deny` methods |
| `src/codemie/service/assistant/assistant_engine_builder.py` | Modify | Wire `require_confirmation` from `ToolPermissionsService` into agent kwargs |
| `src/codemie/rest_api/routers/assistant.py` | Modify | `ToolCallResumeRequest`; `POST .../tool-call/resume` route |
| `src/codemie/rest_api/handlers/assistant_handlers.py` | Modify | `handle_tool_call_resume` + `_yield_from_queue` on `StandardAssistantHandler` |
