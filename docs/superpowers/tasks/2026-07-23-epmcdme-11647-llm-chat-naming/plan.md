# EPMCDME-11647: LLM-Based Contextual Chat Naming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** New conversations get a concise, LLM-generated topical name instead of the verbatim truncated first message, gated behind a feature flag that defaults off everywhere, with the LLM call offloaded to a FastAPI background task so it never delays the first streamed response.

**Architecture:** A new `ChatNamingService` owns the entire "should I rename, and to what" decision (flag check + LLM call + 60-char cap + fallback). `ConversationService.upsert_chat_history` keeps its existing synchronous legacy-name assignment untouched, and additionally schedules `ChatNamingService.rename_conversation` as a background task at the two call sites that already detect a new/nameless conversation. `background_tasks` reaches those call sites by being stored on the handler instance (`self.background_tasks`) at the top of each `process_request` override, avoiding a signature change to every intermediate handler method.

**Tech Stack:** Python, FastAPI (`BackgroundTasks`), LangChain (`get_llm_by_credentials`), pytest/`unittest.mock`/`monkeypatch`.

## Global Constraints

- Hard cap on generated name: **60 characters** (plain slice, no ellipsis).
- Flag `CHAT_CONTEXTUAL_NAMING_ENABLED` defaults to `False` everywhere; toggled via `DynamicConfigService` (admin REST API `PUT /v1/dynamic-config/{key}`, no code deploy).
- `CHAT_CONTEXTUAL_NAMING_LLM_MODEL` is static config only (`config.py` + env var) — no dynamic-config string support, matches `CONVERSATION_ANALYSIS_LLM_MODEL` precedent.
- Any failure (flag off, LLM error/timeout, empty/malformed output) must fall back silently to the legacy truncated name already persisted — no user-facing error, no API/frontend surface change.
- Out of scope: `ConversationService._create_conversation_with_history` (bulk/import call site) keeps legacy truncation unchanged, no background task scheduled there.
- Legacy `_truncate_name` behavior at all 3 existing call sites must remain byte-for-byte unchanged (non-regression).

---

## Known limitation (documented, not fixed in this ticket)

`StandardAssistantHandler._handle_background` / `_background_generate` (the `request.background_task=True` mode) runs `save_chat_history` from *inside* a task that was itself scheduled on the same `BackgroundTasks` instance. Calling `self.background_tasks.add_task(...)` again from within `_background_generate` adds to a list Starlette may have already begun executing, so the naming background task is not guaranteed to run for that specific mode. This degrades gracefully — worst case, the conversation keeps its legacy name, identical to the flag-off behavior — so it is not a correctness bug, just an unreliable-in-this-one-mode edge case. Not addressed here; note it in the operational docs (Task 6).

---

### Task 1: Prompt template and `ChatNamingService`

**Files:**
- Create: `src/codemie/templates/chat_naming_prompt.py`
- Create: `src/codemie/service/chat_naming_service.py`
- Modify: `src/codemie/service/constants.py:47` (add key after the existing `AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED_KEY` line)
- Modify: `src/codemie/configs/config.py:721` (add two settings after the `CONVERSATION_ANALYSIS_*` block)
- Test: `tests/codemie/service/test_chat_naming_service.py`

**Interfaces:**
- Produces: `ChatNamingService.generate_name(first_message: str, assistant_response: str, request_id: str | None = None) -> str | None`
- Produces: `ChatNamingService.rename_conversation(conversation_id: str, first_message: str, assistant_response: str, request_id: str | None = None) -> None`
- Produces: module-level `_is_chat_contextual_naming_enabled() -> bool` in `chat_naming_service.py`
- Produces: `CHAT_CONTEXTUAL_NAMING_ENABLED_KEY` constant in `constants.py`
- Produces: `config.CHAT_CONTEXTUAL_NAMING_ENABLED: bool`, `config.CHAT_CONTEXTUAL_NAMING_LLM_MODEL: str`

- [ ] **Step 1: Add the constant, config settings, and prompt template (setup for this task)**

In `src/codemie/service/constants.py`, after line 47 (`AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED_KEY = "AI_AGENT_CONVERSATION_REPLAY_V2_ENABLED"`), add:

```python
CHAT_CONTEXTUAL_NAMING_ENABLED_KEY = "CHAT_CONTEXTUAL_NAMING_ENABLED"
```

In `src/codemie/configs/config.py`, after line 721 (the `CONVERSATION_ANALYSIS_PROJECTS_FILTER` line, before the `# Leaderboard Configuration` header), add:

```python
    # Chat Contextual Naming Configuration
    CHAT_CONTEXTUAL_NAMING_ENABLED: bool = False
    CHAT_CONTEXTUAL_NAMING_LLM_MODEL: str = "gemini-3-flash"
```

Create `src/codemie/templates/chat_naming_prompt.py`:

```python
# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

chat_naming_prompt = """
Generate a short, specific title for this conversation based on the exchange below.

Rules:
- Return only the title text, nothing else — no quotes, no labels, no trailing punctuation.
- Maximum 60 characters.
- Be concrete: reference the actual topic, not a generic phrase like "Conversation" or "Chat".

User message:
{first_message}

Assistant response:
{assistant_response}
""".strip()
```

- [ ] **Step 2: Write the failing tests for `ChatNamingService`**

Create `tests/codemie/service/test_chat_naming_service.py`:

```python
# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

from types import SimpleNamespace
from unittest.mock import MagicMock

from codemie.service.chat_naming_service import ChatNamingService
from codemie.service import chat_naming_service as chat_naming_module


def test_generate_name_returns_capped_llm_output(monkeypatch):
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = SimpleNamespace(content="A" * 80)
    monkeypatch.setattr(chat_naming_module, "get_llm_by_credentials", lambda **kwargs: fake_llm)

    name = ChatNamingService.generate_name(
        first_message="How do I set up OAuth?",
        assistant_response="Here's how to configure OAuth2 PKCE flow...",
        request_id="req-1",
    )

    assert name == "A" * 60


def test_generate_name_strips_surrounding_quotes(monkeypatch):
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = SimpleNamespace(content='"OAuth setup walkthrough"')
    monkeypatch.setattr(chat_naming_module, "get_llm_by_credentials", lambda **kwargs: fake_llm)

    name = ChatNamingService.generate_name(
        first_message="How do I set up OAuth?",
        assistant_response="...",
        request_id="req-1",
    )

    assert name == "OAuth setup walkthrough"


def test_generate_name_returns_none_on_llm_exception(monkeypatch):
    def raise_error(**kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(chat_naming_module, "get_llm_by_credentials", raise_error)

    name = ChatNamingService.generate_name(
        first_message="hello",
        assistant_response="hi",
        request_id="req-1",
    )

    assert name is None


def test_generate_name_returns_none_on_empty_response(monkeypatch):
    fake_llm = MagicMock()
    fake_llm.invoke.return_value = SimpleNamespace(content="   ")
    monkeypatch.setattr(chat_naming_module, "get_llm_by_credentials", lambda **kwargs: fake_llm)

    name = ChatNamingService.generate_name(
        first_message="hello",
        assistant_response="hi",
        request_id="req-1",
    )

    assert name is None


def test_rename_conversation_no_op_when_flag_disabled(monkeypatch):
    monkeypatch.setattr(
        chat_naming_module.DynamicConfigService, "get_bool_value_safe", lambda *args, **kwargs: False
    )
    monkeypatch.setattr(
        chat_naming_module.ChatNamingService,
        "generate_name",
        classmethod(lambda cls, *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not be called"))),
    )
    fake_conversation_cls = MagicMock()
    monkeypatch.setattr(chat_naming_module, "Conversation", fake_conversation_cls)

    ChatNamingService.rename_conversation(
        conversation_id="conv-1", first_message="hi", assistant_response="hello", request_id="req-1"
    )

    fake_conversation_cls.find_by_id.assert_not_called()


def test_rename_conversation_updates_conversation_on_success(monkeypatch):
    monkeypatch.setattr(
        chat_naming_module.DynamicConfigService, "get_bool_value_safe", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        chat_naming_module.ChatNamingService, "generate_name", classmethod(lambda cls, *a, **kw: "OAuth setup")
    )
    fake_conversation = MagicMock()
    fake_conversation_cls = MagicMock()
    fake_conversation_cls.find_by_id.return_value = fake_conversation
    monkeypatch.setattr(chat_naming_module, "Conversation", fake_conversation_cls)

    ChatNamingService.rename_conversation(
        conversation_id="conv-1", first_message="hi", assistant_response="hello", request_id="req-1"
    )

    assert fake_conversation.conversation_name == "OAuth setup"
    fake_conversation.update.assert_called_once()


def test_rename_conversation_leaves_conversation_untouched_on_generation_failure(monkeypatch):
    monkeypatch.setattr(
        chat_naming_module.DynamicConfigService, "get_bool_value_safe", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(chat_naming_module.ChatNamingService, "generate_name", classmethod(lambda cls, *a, **kw: None))
    fake_conversation_cls = MagicMock()
    monkeypatch.setattr(chat_naming_module, "Conversation", fake_conversation_cls)

    ChatNamingService.rename_conversation(
        conversation_id="conv-1", first_message="hi", assistant_response="hello", request_id="req-1"
    )

    fake_conversation_cls.find_by_id.assert_not_called()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/test_chat_naming_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'codemie.service.chat_naming_service'`

- [ ] **Step 4: Implement `ChatNamingService`**

Create `src/codemie/service/chat_naming_service.py`:

```python
# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

from __future__ import annotations

from codemie.configs import config, logger
from codemie.core.dependecies import get_llm_by_credentials
from codemie.rest_api.models.conversation import Conversation
from codemie.service.constants import CHAT_CONTEXTUAL_NAMING_ENABLED_KEY
from codemie.service.dynamic_config_service import DynamicConfigService
from codemie.templates.chat_naming_prompt import chat_naming_prompt

MAX_NAME_LENGTH = 60


def _is_chat_contextual_naming_enabled() -> bool:
    return DynamicConfigService.get_bool_value_safe(
        CHAT_CONTEXTUAL_NAMING_ENABLED_KEY,
        default=config.CHAT_CONTEXTUAL_NAMING_ENABLED,
    )


class ChatNamingService:
    """Generates a concise LLM-based conversation name, with legacy-name fallback on any failure."""

    @classmethod
    def rename_conversation(
        cls,
        conversation_id: str,
        first_message: str,
        assistant_response: str,
        request_id: str | None = None,
    ) -> None:
        if not _is_chat_contextual_naming_enabled():
            return

        name = cls.generate_name(first_message, assistant_response, request_id)
        if not name:
            return

        conversation = Conversation.find_by_id(conversation_id)
        if not conversation:
            return

        conversation.conversation_name = name
        conversation.update()

    @classmethod
    def generate_name(
        cls,
        first_message: str,
        assistant_response: str,
        request_id: str | None = None,
    ) -> str | None:
        try:
            llm = get_llm_by_credentials(
                llm_model=config.CHAT_CONTEXTUAL_NAMING_LLM_MODEL,
                streaming=False,
                request_id=request_id,
            )
            prompt = chat_naming_prompt.format(
                first_message=first_message or "",
                assistant_response=assistant_response or "",
            )
            response = llm.invoke(prompt)
            name = cls._extract_content(response).strip().strip('"').strip("'").strip()
            if not name:
                return None
            return name[:MAX_NAME_LENGTH]
        except Exception as error:
            logger.error(f"Chat contextual naming failed. RequestId={request_id}, Error={error}", exc_info=True)
            return None

    @staticmethod
    def _extract_content(response) -> str:
        content = getattr(response, "content", response)
        return content if isinstance(content, str) else str(content)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/test_chat_naming_service.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add src/codemie/templates/chat_naming_prompt.py src/codemie/service/chat_naming_service.py \
  src/codemie/service/constants.py src/codemie/configs/config.py \
  tests/codemie/service/test_chat_naming_service.py
git commit -m "EPMCDME-11647: Add ChatNamingService with feature-flagged LLM naming"
```

---

### Task 2: Wire background naming into `ConversationService.upsert_chat_history`

**Files:**
- Modify: `src/codemie/service/conversation_service.py:106-230` (class `ConversationService`, method `upsert_chat_history`)
- Test: `tests/codemie/service/test_conversation_service.py`

**Interfaces:**
- Consumes: `ChatNamingService.rename_conversation(conversation_id, first_message, assistant_response, request_id)` from Task 1
- Produces: `ConversationService.upsert_chat_history(..., background_tasks: "BackgroundTasks | None" = None)` — new optional trailing keyword parameter, backward-compatible with all existing callers/tests that omit it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/service/test_conversation_service.py` (after the existing imports, add `from unittest.mock import MagicMock` is already imported; add `from codemie.service.chat_naming_service import ChatNamingService` and a `from fastapi import BackgroundTasks` import near the top):

```python
from fastapi import BackgroundTasks

from codemie.service.chat_naming_service import ChatNamingService
```

Append these test functions:

```python
@patch(
    "codemie.service.monitoring.conversation_monitoring_service.ConversationMonitoringService.send_conversation_metric"
)
@patch("codemie.rest_api.models.conversation.ConversationMetrics.calculate_metrics")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.save")
@patch("codemie.rest_api.models.conversation.Conversation.save")
@patch("codemie.rest_api.models.conversation.Conversation.find_by_id")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.get_by_conversation_id")
@patch("codemie.service.conversation_service.AgentWorkspaceService.sync_uploaded_files")
def test_upsert_chat_history_schedules_naming_task_for_new_conversation(
    mock_sync_uploaded_files,
    mock_metrics_get,
    mock_conv_find,
    mock_conv_save,
    mock_metrics_save,
    mock_calculate_metrics,
    _mock_send_metric,
    mock_request,
    mock_assistant,
    mock_admin_user,
    mock_conversation_metrics,
):
    mock_conv_find.return_value = None  # No existing conversation -> new-conversation branch
    mock_metrics_get.side_effect = KeyError("not found")
    mock_conv_save.return_value = True

    background_tasks = MagicMock(spec=BackgroundTasks)

    ConversationService.upsert_chat_history(
        assistant_response="Hi there!",
        user=mock_admin_user,
        thoughts=[],
        time_elapsed=0,
        tokens_usage=TokensUsage(output_tokens=0, input_tokens=0, money_spent=0.0),
        assistant=mock_assistant,
        request=mock_request,
        background_tasks=background_tasks,
    )

    background_tasks.add_task.assert_called_once_with(
        ChatNamingService.rename_conversation,
        conversation_id=mock_request.conversation_id,
        first_message=mock_request.text,
        assistant_response="Hi there!",
        request_id=None,
    )


@patch(
    "codemie.service.monitoring.conversation_monitoring_service.ConversationMonitoringService.send_conversation_metric"
)
@patch("codemie.rest_api.models.conversation.ConversationMetrics.calculate_metrics")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.update")
@patch("codemie.rest_api.models.conversation.Conversation.update")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.get_by_conversation_id")
@patch("codemie.service.conversation_service.AgentWorkspaceService.sync_uploaded_files")
def test_upsert_chat_history_does_not_schedule_naming_for_existing_named_conversation(
    mock_sync_uploaded_files,
    mock_metrics_get,
    mock_conv_update,
    mock_metrics_update,
    mock_calculate_metrics,
    _mock_send_metric,
    mock_request,
    mock_assistant,
    mock_admin_user,
    mock_conversation,
    mock_conversation_metrics,
):
    mock_conversation.conversation_name = "Already named"
    mock_metrics_get.return_value = mock_conversation_metrics
    mock_conv_update.return_value = True
    mock_metrics_update.return_value = True

    background_tasks = MagicMock(spec=BackgroundTasks)

    with patch("codemie.rest_api.models.conversation.Conversation.find_by_id", return_value=mock_conversation):
        ConversationService.upsert_chat_history(
            assistant_response="Hi there!",
            user=mock_admin_user,
            thoughts=[],
            time_elapsed=0,
            tokens_usage=TokensUsage(output_tokens=0, input_tokens=0, money_spent=0.0),
            assistant=mock_assistant,
            request=mock_request,
            background_tasks=background_tasks,
        )

    background_tasks.add_task.assert_not_called()


@patch(
    "codemie.service.monitoring.conversation_monitoring_service.ConversationMonitoringService.send_conversation_metric"
)
@patch("codemie.rest_api.models.conversation.ConversationMetrics.calculate_metrics")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.save")
@patch("codemie.rest_api.models.conversation.Conversation.save")
@patch("codemie.rest_api.models.conversation.Conversation.find_by_id")
@patch("codemie.rest_api.models.conversation.ConversationMetrics.get_by_conversation_id")
@patch("codemie.service.conversation_service.AgentWorkspaceService.sync_uploaded_files")
def test_upsert_chat_history_without_background_tasks_still_sets_legacy_name(
    mock_sync_uploaded_files,
    mock_metrics_get,
    mock_conv_find,
    mock_conv_save,
    mock_metrics_save,
    mock_calculate_metrics,
    _mock_send_metric,
    mock_request,
    mock_assistant,
    mock_admin_user,
):
    """Legacy non-regression: omitting background_tasks (as all pre-existing callers do) must not raise,
    and the synchronous legacy name assignment must be unaffected."""
    mock_conv_find.return_value = None
    mock_metrics_get.side_effect = KeyError("not found")
    mock_conv_save.return_value = True

    ConversationService.upsert_chat_history(
        assistant_response="Hi there!",
        user=mock_admin_user,
        thoughts=[],
        time_elapsed=0,
        tokens_usage=TokensUsage(output_tokens=0, input_tokens=0, money_spent=0.0),
        assistant=mock_assistant,
        request=mock_request,
    )

    mock_conv_save.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/test_conversation_service.py -k "schedules_naming or does_not_schedule or without_background_tasks" -v`
Expected: FAIL — `TypeError: upsert_chat_history() got an unexpected keyword argument 'background_tasks'`

- [ ] **Step 3: Implement the change**

In `src/codemie/service/conversation_service.py`, add the import near the other `codemie.service.*` imports (after line 53's `AgentWorkspaceService` import):

```python
from codemie.service.chat_naming_service import ChatNamingService
```

Add `from typing import TYPE_CHECKING` handling for the `BackgroundTasks` type — since this is a lightweight optional param, import it directly (no circular import risk; `fastapi` has no dependency on this service):

```python
from fastapi import BackgroundTasks
```

Replace the `upsert_chat_history` signature (currently lines 129-143):

```python
    @classmethod
    def upsert_chat_history(
        cls,
        assistant_response: str,
        time_elapsed: float,
        tokens_usage: TokensUsage,
        request: AssistantChatRequest,
        assistant: Assistant,
        user: User,
        thoughts: List[Thought],
        status: ConversationStatus = ConversationStatus.SUCCESS,
        user_message_received_at: datetime | None = None,
        interactive_request: InteractiveRequest | None = None,
        request_id: Optional[str] = None,
        background_tasks: BackgroundTasks | None = None,
    ):
```

Replace the conversation-lookup block (currently lines 144-167):

```python
        should_create_conversation = False
        schedule_naming = False
        llm_model = request.llm_model if request.llm_model else assistant.llm_model_type

        # Find or create conversation
        conversation = Conversation.find_by_id(request.conversation_id)
        if not conversation:
            initial_image_settings = cls._get_initial_image_generation_settings(assistant)
            # Create since it does not exist
            conversation = Conversation(
                id=request.conversation_id,
                conversation_id=request.conversation_id,
                conversation_name=cls._truncate_name(request.text),
                user_id=user.id,
                user_name=user.name,
                assistant_ids=[assistant.id],
                initial_assistant_id=assistant.id,
                project=assistant.project,
                enable_image_generation=initial_image_settings["enable_image_generation"],
                image_generation_model=initial_image_settings["image_generation_model"],
            )
            should_create_conversation = True
            schedule_naming = True
        elif not conversation.conversation_name and not conversation.history:
            # Conversation was pre-created without a name; set it from the first message.
            conversation.conversation_name = cls._truncate_name(request.text)
            schedule_naming = True
```

At the very end of the method, immediately before `request.mark_history_variant_persisted()` (currently line 229), add:

```python
        if schedule_naming and background_tasks is not None:
            background_tasks.add_task(
                ChatNamingService.rename_conversation,
                conversation_id=request.conversation_id,
                first_message=request.text,
                assistant_response=assistant_response,
                request_id=request_id,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/test_conversation_service.py -v`
Expected: PASS (all tests, including the 3 new ones and all pre-existing ones — confirms non-regression)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/conversation_service.py tests/codemie/service/test_conversation_service.py
git commit -m "EPMCDME-11647: Schedule background LLM naming from upsert_chat_history"
```

---

### Task 3: Thread `background_tasks` through the handler layer

**Files:**
- Modify: `src/codemie/rest_api/handlers/assistant_handlers.py:94-97` (`AssistantRequestHandler.__init__`)
- Modify: `src/codemie/rest_api/handlers/assistant_handlers.py:392-404` (`save_chat_history`, the `ConversationService.upsert_chat_history` call)
- Modify: `src/codemie/rest_api/handlers/assistant_handlers.py:464-475` (`StandardAssistantHandler.process_request`)
- Modify: `src/codemie/rest_api/handlers/assistant_handlers.py:968-981` (`A2AAssistantHandler.process_request`)
- Test: `tests/codemie/rest_api/handlers/test_assistant_handlers.py`

**Interfaces:**
- Consumes: `ConversationService.upsert_chat_history(..., background_tasks=...)` from Task 2
- Produces: `AssistantRequestHandler.background_tasks: BackgroundTasks | None` instance attribute, set at the top of every `process_request` override.

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/rest_api/handlers/test_assistant_handlers.py`, inside `class TestSaveChatHistory` (after `test_save_chat_history_logs_debug_when_skipped`, before the module-level `test_populate_conversation_history_...` function):

```python
    def test_save_chat_history_passes_background_tasks_through(self, handler, chat_history_data_save_true):
        """save_chat_history forwards self.background_tasks to upsert_chat_history"""
        sentinel_background_tasks = Mock()
        handler.background_tasks = sentinel_background_tasks

        with (
            patch("codemie.service.llm_service.utils.set_llm_context"),
            patch("codemie.rest_api.handlers.assistant_handlers.ConversationService") as mock_service,
            patch("codemie.rest_api.handlers.assistant_handlers.request_summary_manager") as mock_manager,
        ):
            mock_manager.get_summary.return_value = Mock(tokens_usage=Mock())

            handler.save_chat_history(chat_history_data_save_true)

            _, call_kwargs = mock_service.upsert_chat_history.call_args
            assert call_kwargs["background_tasks"] is sentinel_background_tasks

    def test_background_tasks_defaults_to_none_before_process_request(self, handler):
        """Handler instances start with no background_tasks until process_request sets it"""
        assert handler.background_tasks is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/rest_api/handlers/test_assistant_handlers.py -k "background_tasks" -v`
Expected: FAIL — `AttributeError: 'StandardAssistantHandler' object has no attribute 'background_tasks'`

- [ ] **Step 3: Implement the change**

In `src/codemie/rest_api/handlers/assistant_handlers.py`, update `AssistantRequestHandler.__init__` (currently lines 94-97):

```python
class AssistantRequestHandler(ABC):
    def __init__(self, assistant: Assistant, user: User, request_uuid: str):
        self.assistant = assistant
        self.user = user
        self.request_uuid = request_uuid
        self.background_tasks: BackgroundTasks | None = None
```

Update `save_chat_history`'s `ConversationService.upsert_chat_history` call (currently lines 392-404) to add the new keyword:

```python
        ConversationService.upsert_chat_history(
            request=data.request,
            user=self.user,
            assistant_response=data.response,
            time_elapsed=time() - data.execution_start,
            tokens_usage=tokens_usage,
            assistant=self.assistant,
            thoughts=self._filter_thoughts(data.thoughts),
            status=data.status,
            user_message_received_at=data.user_message_received_at,
            interactive_request=data.interactive_request,
            request_id=self.request_uuid,
            background_tasks=self.background_tasks,
        )
```

In `StandardAssistantHandler.process_request` (currently lines 464-475), add the assignment as the first line of the method body:

```python
    def process_request(
        self,
        request: AssistantChatRequest,
        background_tasks: BackgroundTasks,
        raw_request: Request,
        include_tool_errors: bool = False,
        error_detail_level: ErrorDetailLevel = ErrorDetailLevel.STANDARD,
    ) -> StreamingResponse | BaseModelResponse:
        """
        Process assistant request with error handling options.
        """
        self.background_tasks = background_tasks
        self._sync_uploaded_files_to_workspace(request)
```

In `A2AAssistantHandler.process_request` (currently lines 968-981), add the same assignment as the first line of the method body:

```python
    def process_request(
        self,
        request: AssistantChatRequest,
        background_tasks: BackgroundTasks,
        raw_request: Request,
        include_tool_errors: bool = False,
        error_detail_level: ErrorDetailLevel = ErrorDetailLevel.STANDARD,
    ) -> StreamingResponse | BaseModelResponse:
        """
        Process request for remote A2A assistant.

        Note: Error handling parameters not yet implemented for A2A.
        """
        self.background_tasks = background_tasks
        self._sync_uploaded_files_to_workspace(request)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/handlers/test_assistant_handlers.py -v`
Expected: PASS (all tests, including the 2 new ones and all pre-existing ones)

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/handlers/assistant_handlers.py tests/codemie/rest_api/handlers/test_assistant_handlers.py
git commit -m "EPMCDME-11647: Thread background_tasks into save_chat_history via handler instance"
```

---

### Task 4: Operational docs

**Files:**
- Modify: `.ai-run/guides/development/configuration-patterns.md`

**Interfaces:**
- None (documentation only).

- [ ] **Step 1: Add a documentation section**

Read `.ai-run/guides/development/configuration-patterns.md` first to match its existing section style, then add a new section (mirroring how other dynamic-config-backed flags are documented there, or adding a comparable pattern if none exists yet) covering:

```markdown
## Chat Contextual Naming

- `CHAT_CONTEXTUAL_NAMING_ENABLED` (bool, default `False`) — enables LLM-generated conversation names for new conversations. Read via `DynamicConfigService.get_bool_value_safe`, toggle at runtime with no code deploy via the admin REST API: `PUT /v1/dynamic-config/CHAT_CONTEXTUAL_NAMING_ENABLED` with a boolean body. Falls back to `config.CHAT_CONTEXTUAL_NAMING_ENABLED` if the dynamic-config lookup fails.
- `CHAT_CONTEXTUAL_NAMING_LLM_MODEL` (str, default `"gemini-3-flash"`) — static config / env var only, not dynamically toggleable. Changing it requires a restart/redeploy.
- On any failure (LLM error, timeout, empty output) or when the flag is off, the conversation keeps its legacy truncated-first-message name — no user-facing error.
- Known limitation: conversations created via `background_task=True` requests may not reliably get the LLM name (nested background-task scheduling); they still fall back to the legacy name safely.
```

- [ ] **Step 2: Commit**

```bash
git add .ai-run/guides/development/configuration-patterns.md
git commit -m "EPMCDME-11647: Document chat contextual naming configuration"
```

---

## Self-Review Notes

- **Spec coverage**: every spec section maps to a task — components (Task 1), trigger wiring in `ConversationService` (Task 2), handler-layer plumbing (Task 3), operational docs (Task 4). The "legacy name first, then rename" and "bulk/import out of scope" decisions are enforced by Task 2's design (background task only scheduled at the two existing branches, `_create_conversation_with_history` untouched).
- **Non-regression**: Task 2's third test (`test_upsert_chat_history_without_background_tasks_still_sets_legacy_name`) plus running the full pre-existing `test_conversation_service.py` suite in Step 4 protects the legacy path.
- **Type consistency**: `ChatNamingService.rename_conversation`'s signature (`conversation_id, first_message, assistant_response, request_id`) is used identically in Task 2's `background_tasks.add_task(...)` call and Task 1's test assertions.
