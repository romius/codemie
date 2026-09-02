# Extract ToolCallConfirmationHandler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the tool-call confirmation logic from `LangGraphAgent` into a new `ToolCallConfirmationHandler` composition class, keeping `LangGraphAgent`'s public API identical.

**Architecture:** `ToolCallConfirmationHandler` holds the `interrupted` flag, the `handle_interrupt` state-inspection logic (previously inline in `_stream_graph`), and the full bodies of `confirm_tool_call` / `reject_tool_call`. `LangGraphAgent` becomes a thin proxy: it creates the handler after `init_agent()` when `require_tool_confirmation=True`, and the `_interrupted` attribute is replaced by a property that reads from the handler.

**Tech Stack:** Python 3.11+, LangGraph, Pydantic v2, pytest, existing `ConversationCheckpointService`, `StreamedGenerationResult`, `ToolCallPendingEvent`.

## Global Constraints

- Apache 2.0 license header on the new file (copy from `langgraph_agent.py`).
- Public method signatures on `LangGraphAgent` (`confirm_tool_call`, `reject_tool_call`, `stream`, `_stream_graph`) must remain identical to callers.
- `_INTERRUPT_BEFORE_TOOLS` class constant stays on `LangGraphAgent` — do NOT move it.
- `require_tool_confirmation=False` path must be zero-change: no handler is created, `_interrupted` property returns `False`.
- Do NOT add or remove test files; only fix test failures caused by the refactor.
- Run commands from `/Users/Andriy_Lukashchuk/Dev/code-assistant`.

---

### Task 1: Create `ToolCallConfirmationHandler`

**Files:**
- Create: `src/codemie/agents/tool_call_confirmation_handler.py`

**Interfaces:**
- Consumes: `agent_executor` (LangGraph compiled graph), `conversation_id: str`, `thread_generator: ThreadedGenerator`, `agent_name: str`, `request_uuid: str`, `user`, `assistant`, `otel_context`, `run_config_fn: Callable`, `stream_graph_fn: Callable`, `process_chunks_fn: Callable`, `get_thread_context_fn: Callable`
- Produces:
  - `ToolCallConfirmationHandler` class with `interrupted: bool = False`
  - `handle_interrupt(self, state, run_config, last_message: str) -> str`
  - `confirm_tool_call(self) -> None`
  - `reject_tool_call(self, tool_call_id: str) -> None`

- [ ] **Step 1: Write the new handler file**

```python
# src/codemie/agents/tool_call_confirmation_handler.py

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

import contextlib
import json
from time import time
from typing import Callable, Optional

from langchain_core.messages import ToolMessage
from pydantic import BaseModel

from codemie.chains.base import StreamedGenerationResult, ToolCallPendingEvent
from codemie.configs import config
from codemie.configs.logger import logger, set_logging_info
from codemie.core.otel_tracing import propagated_span, record_exception_on_span
from codemie.enterprise.litellm.proxy_router import handle_agent_exception
from codemie.service.conversation_checkpoint_service import ConversationCheckpointService
from codemie.service.llm_service.utils import set_llm_context


class ToolCallConfirmationHandler:
    """Encapsulates LangGraph interrupt / resume logic for tool-call confirmation."""

    def __init__(
        self,
        *,
        agent_executor,
        conversation_id: str,
        thread_generator,
        agent_name: str,
        request_uuid: str,
        user,
        assistant,
        otel_context,
        run_config_fn: Callable,
        stream_graph_fn: Callable,
        process_chunks_fn: Callable,
        get_thread_context_fn: Callable,
    ):
        self.agent_executor = agent_executor
        self.conversation_id = conversation_id
        self.thread_generator = thread_generator
        self.agent_name = agent_name
        self.request_uuid = request_uuid
        self.user = user
        self.assistant = assistant
        self._otel_context = otel_context
        self._run_config_fn = run_config_fn
        self._stream_graph_fn = stream_graph_fn
        self._process_chunks_fn = process_chunks_fn
        self._get_thread_context_fn = get_thread_context_fn
        self.interrupted: bool = False

    def handle_interrupt(self, state, run_config, last_message: str) -> str:
        """Detect interrupt, persist pending tool call, emit SSE. Returns last_message."""
        if not (state.next and "tools" in state.next):
            return last_message

        last_ai_message = state.values["messages"][-1]
        if not last_ai_message.tool_calls:
            logger.warning(
                f"Graph interrupted before tools node but last AI message has no tool_calls. "
                f"Agent={self.agent_name}, request_uuid={self.request_uuid}. Skipping interrupt handling."
            )
            return last_message

        tool_call = last_ai_message.tool_calls[0]
        pending_event = ToolCallPendingEvent(
            pending_tool_call_id=tool_call["id"],
            tool_name=tool_call["name"],
            tool_args=tool_call.get("args", {}),
        )
        ConversationCheckpointService().save_pending_tool_call(self.conversation_id, pending_event)
        self.thread_generator.send(
            StreamedGenerationResult(
                last=True,
                tool_call_pending=pending_event,
            ).model_dump_json()
        )
        self.interrupted = True
        return last_message

    def confirm_tool_call(self) -> None:
        """Resume the interrupted graph, allowing the pending tool call to execute."""
        with propagated_span(
            self._otel_context,
            "agent.confirm_tool_call",
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
                run_config = self._run_config_fn()
                trace_ctx = run_config.pop("_trace_ctx", contextlib.nullcontext())
                with trace_ctx:
                    result = self._stream_graph_fn(None, run_config, chunks_collector)

                if self.interrupted:
                    return
                result = json.dumps(result) if isinstance(result, (dict, BaseModel)) else result
                self.thread_generator.send(
                    StreamedGenerationResult(
                        generated=result,
                        generated_chunk="",
                        last=True,
                        time_elapsed=time() - execution_start,
                        context=self._get_thread_context_fn(),
                    ).model_dump_json()
                )
            except Exception as e:
                record_exception_on_span(e)
                time_elapsed = time() - execution_start
                error_response = handle_agent_exception(e)
                user_message = error_response.get_error().message
                chunks_collector.append(user_message)
                generated, execution_error = self._process_chunks_fn(chunks_collector, config, None)
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

    def reject_tool_call(self, tool_call_id: str) -> None:
        """Inject denial ToolMessage then resume graph via confirm_tool_call."""
        run_config = self._run_config_fn()
        deny_message = ToolMessage(
            content="Tool call was denied by the user.",
            tool_call_id=tool_call_id,
        )
        self.agent_executor.update_state(
            run_config,
            {"messages": [deny_message]},
            as_node="tools",
        )
        self.confirm_tool_call()
```

- [ ] **Step 2: Verify the file exists and has no syntax errors**

Run: `cd /Users/Andriy_Lukashchuk/Dev/code-assistant && poetry run python -c "from codemie.agents.tool_call_confirmation_handler import ToolCallConfirmationHandler; print('OK')"`

Expected: `OK`

---

### Task 2: Update `LangGraphAgent` to use `ToolCallConfirmationHandler`

**Files:**
- Modify: `src/codemie/agents/langgraph_agent.py`

**Interfaces:**
- Consumes: `ToolCallConfirmationHandler` (from Task 1)
- Produces: identical public API — `confirm_tool_call()`, `reject_tool_call(tool_call_id)`, `stream()`, `_stream_graph()` — unchanged signatures

- [ ] **Step 1: Add `_confirmation_handler` attribute and remove `_interrupted = False` from `__init__`**

In `__init__`, after `self.require_tool_confirmation = require_tool_confirmation`, **remove** the line:
```python
self._interrupted = False
```
And after `self.agent_executor = self.init_agent()`, add:
```python
        self._confirmation_handler: Optional["ToolCallConfirmationHandler"] = None
        if self.require_tool_confirmation:
            from codemie.agents.tool_call_confirmation_handler import ToolCallConfirmationHandler
            self._confirmation_handler = ToolCallConfirmationHandler(
                agent_executor=self.agent_executor,
                conversation_id=self.conversation_id,
                thread_generator=self.thread_generator,
                agent_name=self.agent_name,
                request_uuid=self.request_uuid,
                user=self.user,
                assistant=self.assistant,
                otel_context=self._otel_context,
                run_config_fn=self._get_run_config,
                stream_graph_fn=self._stream_graph,
                process_chunks_fn=self._process_chunks,
                get_thread_context_fn=lambda: self.thread_context,
            )
```

- [ ] **Step 2: Add `_interrupted` property after `__init__`**

After the `_handoff_run_ids` property block (around line 238), add:
```python
    @property
    def _interrupted(self) -> bool:
        return self._confirmation_handler.interrupted if self._confirmation_handler is not None else False
```

- [ ] **Step 3: Replace the interrupt block in `_stream_graph`**

In `_stream_graph`, replace the block (lines 956–985):
```python
        if self.require_tool_confirmation:
            state = self.agent_executor.get_state(config)

            if state.next and "tools" in state.next:
                last_ai_message = state.values["messages"][-1]
                if not last_ai_message.tool_calls:
                    logger.warning(
                        f"Graph interrupted before tools node but last AI message has no tool_calls. "
                        f"Agent={self.agent_name}, request_uuid={self.request_uuid}. Skipping interrupt handling."
                    )
                    return last_message
                tool_call = last_ai_message.tool_calls[0]
                pending_event = ToolCallPendingEvent(
                    pending_tool_call_id=tool_call["id"],
                    tool_name=tool_call["name"],
                    tool_args=tool_call.get("args", {}),
                )
                # Save lightweight pending info for page refresh
                from codemie.service.conversation_checkpoint_service import ConversationCheckpointService

                ConversationCheckpointService().save_pending_tool_call(self.conversation_id, pending_event)
                # Signal frontend to show Allow/Deny dialog
                self.thread_generator.send(
                    StreamedGenerationResult(
                        last=True,
                        tool_call_pending=pending_event,
                    ).model_dump_json()
                )
                self._interrupted = True
                return last_message
```

with:
```python
        if self.require_tool_confirmation:
            state = self.agent_executor.get_state(config)
            last_message = self._confirmation_handler.handle_interrupt(state, config, last_message)
```

- [ ] **Step 4: Replace `confirm_tool_call` body with a thin proxy**

Replace the full body of `confirm_tool_call` (lines 777–832) with:
```python
    def confirm_tool_call(self) -> None:
        """Resume the interrupted graph, allowing the pending tool call to execute."""
        self._confirmation_handler.confirm_tool_call()
```

- [ ] **Step 5: Replace `reject_tool_call` body with a thin proxy**

Replace the full body of `reject_tool_call` (lines 834–846) with:
```python
    def reject_tool_call(self, tool_call_id: str) -> None:
        """Resume the interrupted graph, injecting a denial result for the pending tool call."""
        self._confirmation_handler.reject_tool_call(tool_call_id)
```

- [ ] **Step 6: Verify syntax**

Run: `cd /Users/Andriy_Lukashchuk/Dev/code-assistant && poetry run python -c "from codemie.agents.langgraph_agent import LangGraphAgent; print('OK')"`

Expected: `OK`

---

### Task 3: Run tests and fix failures

**Files:**
- Modify if needed: `src/codemie/agents/langgraph_agent.py`, `src/codemie/agents/tool_call_confirmation_handler.py`

**Interfaces:**
- None new

- [ ] **Step 1: Run the targeted test suite**

```bash
cd /Users/Andriy_Lukashchuk/Dev/code-assistant && poetry run pytest tests/codemie/agents/test_langgraph_tool_confirmation.py tests/codemie/rest_api/routers/test_tool_call_resume.py -v 2>&1 | tail -30
```

Expected: All tests pass (green).

- [ ] **Step 2: Fix any failures**

If `test_langgraph_tool_confirmation.py` fails because the test bypasses `__init__` and manually sets `agent.require_tool_confirmation = True` but does **not** set `agent._confirmation_handler`, the `_interrupted` property will try to read from `None` (correct: it returns `False`). Verify the property guard:
```python
@property
def _interrupted(self) -> bool:
    return self._confirmation_handler.interrupted if self._confirmation_handler is not None else False
```
If `test_tool_call_resume.py` fails because `confirm_tool_call` or `reject_tool_call` on a `MagicMock` agent was asserted, no change is needed — those tests only assert that the router calls the method on the agent mock, not on the handler.

- [ ] **Step 3: Re-run tests to confirm green**

```bash
cd /Users/Andriy_Lukashchuk/Dev/code-assistant && poetry run pytest tests/codemie/agents/test_langgraph_tool_confirmation.py tests/codemie/rest_api/routers/test_tool_call_resume.py -v 2>&1 | tail -30
```

Expected: `X passed` with no failures.

---

### Task 4: Run linter and commit

**Files:**
- No new files for this task.

- [ ] **Step 1: Run ruff lint and format check**

```bash
cd /Users/Andriy_Lukashchuk/Dev/code-assistant && make ruff
```

Expected: No errors. If ruff flags unused imports (e.g., `ToolCallPendingEvent` import left on `langgraph_agent.py`), remove them.

- [ ] **Step 2: Commit**

```bash
cd /Users/Andriy_Lukashchuk/Dev/code-assistant && git add src/codemie/agents/tool_call_confirmation_handler.py src/codemie/agents/langgraph_agent.py && git commit -m "refactor(EPMCDME-13903): extract ToolCallConfirmationHandler from LangGraphAgent"
```

Expected: Commit created.
