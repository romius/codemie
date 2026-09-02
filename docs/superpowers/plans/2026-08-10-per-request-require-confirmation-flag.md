# Per-Request `require_confirmation` Flag — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-request `require_confirmation` field to `AssistantChatRequest` that activates tool-call confirmation for a single request, subject to the assistant-level hard override.

**Architecture:** Three surgical changes — new field on the request model, priority logic in `ToolPermissionsService.get_effective_permissions()`, and a one-line wiring change in `LangGraphAssistantBuilder.configure_agent_kwargs()`. No migrations, no new endpoints, no new files.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest.

## Global Constraints

- All modified `src/codemie/` files must carry the Apache 2.0 license header present in every existing file (copyright year 2026, EPAM Systems).
- No changes to `AssistantConfiguration`, `POST v1/conversations`, or the conversation entity.
- `IdeChatRequest` inherits from `AssistantChatRequest` and picks up the new field automatically — no separate change needed.
- Priority rule (enforced entirely inside `get_effective_permissions`): `assistant.tool_permissions.require_confirmation = True` always wins; `request.require_confirmation = True` activates confirmation only when the assistant doesn't already require it.
- Run tests with: `poetry run pytest <test-file> -v`
- Run lint with: `make ruff`

---

### Task 1: `require_confirmation` field on `AssistantChatRequest`

**Files:**
- Modify: `src/codemie/core/models.py` — add field after `enable_code_interpreter` (~line 637)
- Test: `tests/codemie/core/test_models_assistant_chat_request.py` — add new test class

**Interfaces:**
- Produces: `AssistantChatRequest.require_confirmation: Optional[bool] = None` — consumed by Task 3

- [ ] **Step 1: Write the failing tests**

Append this class to `tests/codemie/core/test_models_assistant_chat_request.py`:

```python
class TestAssistantChatRequestRequireConfirmation:
    def test_defaults_to_none(self):
        request = AssistantChatRequest(text="Hello")
        assert request.require_confirmation is None

    def test_explicit_true(self):
        request = AssistantChatRequest(text="Hello", require_confirmation=True)
        assert request.require_confirmation is True

    def test_explicit_false(self):
        request = AssistantChatRequest(text="Hello", require_confirmation=False)
        assert request.require_confirmation is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie/core/test_models_assistant_chat_request.py::TestAssistantChatRequestRequireConfirmation -v
```

Expected: 3 FAILED with `ValidationError` or attribute error.

- [ ] **Step 3: Add the field to `AssistantChatRequest`**

In `src/codemie/core/models.py`, after the `enable_code_interpreter` block (after line 637, before `@model_validator`):

```python
    require_confirmation: Optional[bool] = Field(
        default=None,
        description=(
            "Require tool-call confirmation for this request. "
            "Ignored if the assistant already requires confirmation."
        ),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie/core/test_models_assistant_chat_request.py::TestAssistantChatRequestRequireConfirmation -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/core/models.py tests/codemie/core/test_models_assistant_chat_request.py
git commit -m "feat(EPMCDME-13903): add require_confirmation field to AssistantChatRequest"
```

---

### Task 2: Priority logic in `ToolPermissionsService`

**Files:**
- Modify: `src/codemie/service/tool_permissions_service.py` — add `require_confirmation_override` param + priority branching
- Test: `tests/codemie/service/test_tool_permissions_service.py` — add 4 new tests

**Interfaces:**
- Consumes: `AssistantChatRequest.require_confirmation: Optional[bool]` (Task 1)
- Produces: `ToolPermissionsService.get_effective_permissions(assistant, user=None, require_confirmation_override: Optional[bool] = None) -> ToolPermissionsConfig` — consumed by Task 3

- [ ] **Step 1: Write the failing tests**

Append to `tests/codemie/service/test_tool_permissions_service.py`:

```python
def test_override_true_activates_when_assistant_has_none(svc):
    assistant = MagicMock()
    assistant.tool_permissions = None
    result = svc.get_effective_permissions(assistant, require_confirmation_override=True)
    assert result.require_confirmation is True


def test_override_true_activates_when_assistant_requires_false(svc):
    assistant = MagicMock()
    assistant.tool_permissions = ToolPermissionsConfig(require_confirmation=False)
    result = svc.get_effective_permissions(assistant, require_confirmation_override=True)
    assert result.require_confirmation is True


def test_assistant_hard_override_beats_request_false(svc):
    assistant = MagicMock()
    assistant.tool_permissions = ToolPermissionsConfig(require_confirmation=True)
    result = svc.get_effective_permissions(assistant, require_confirmation_override=False)
    assert result.require_confirmation is True


def test_override_none_does_not_activate_confirmation(svc):
    assistant = MagicMock()
    assistant.tool_permissions = None
    result = svc.get_effective_permissions(assistant, require_confirmation_override=None)
    assert result.require_confirmation is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie/service/test_tool_permissions_service.py -v
```

Expected: the 4 new tests FAIL (unexpected keyword argument or wrong result), existing 3 tests still PASS.

- [ ] **Step 3: Update `get_effective_permissions`**

Replace the method body in `src/codemie/service/tool_permissions_service.py`:

```python
    def get_effective_permissions(
        self,
        assistant: AssistantBase,
        user: Optional[User] = None,
        require_confirmation_override: Optional[bool] = None,
    ) -> ToolPermissionsConfig:
        base = assistant.tool_permissions or ToolPermissionsConfig()
        if base.require_confirmation:
            return base  # assistant-level hard override; request cannot disable it
        if require_confirmation_override:
            return ToolPermissionsConfig(require_confirmation=True)
        return base
```

- [ ] **Step 4: Run all tool-permissions tests to verify they pass**

```bash
poetry run pytest tests/codemie/service/test_tool_permissions_service.py tests/codemie/rest_api/models/test_tool_permissions_config.py -v
```

Expected: all 7 tests PASS (3 existing + 4 new).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/tool_permissions_service.py tests/codemie/service/test_tool_permissions_service.py
git commit -m "feat(EPMCDME-13903): add require_confirmation_override to ToolPermissionsService"
```

---

### Task 3: Wire the override into `configure_agent_kwargs`

**Files:**
- Modify: `src/codemie/service/assistant/assistant_engine_builder.py` — pass `request.require_confirmation` to `get_effective_permissions`
- Create: `tests/codemie/service/assistant/test_configure_agent_kwargs_confirmation.py`

**Interfaces:**
- Consumes: `AssistantChatRequest.require_confirmation: Optional[bool]` (Task 1), `ToolPermissionsService.get_effective_permissions(..., require_confirmation_override)` (Task 2)

- [ ] **Step 1: Write the failing test**

Create `tests/codemie/service/assistant/test_configure_agent_kwargs_confirmation.py`:

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

from unittest.mock import MagicMock

from codemie.rest_api.models.assistant import ToolPermissionsConfig
from codemie.service.assistant.assistant_engine_builder import LangGraphAssistantBuilder


def _run_configure(*, request_require_confirmation, assistant_require_confirmation):
    agent_kwargs = {}
    assistant = MagicMock()
    assistant.tool_permissions = ToolPermissionsConfig(require_confirmation=assistant_require_confirmation)
    request = MagicMock()
    request.require_confirmation = request_require_confirmation

    LangGraphAssistantBuilder.configure_agent_kwargs(
        agent_kwargs=agent_kwargs,
        assistant=assistant,
        user=MagicMock(),
        request=request,
        request_uuid="uuid-1",
        thread_generator=MagicMock(),
        llm_model="gpt-4",
        smart_tool_selection_enabled=False,
        allow_tool_confirmation=True,
        create_subagent_executors=lambda **_: [],
        get_subagent_descriptions=lambda a, u: {},
    )
    return agent_kwargs["require_tool_confirmation"]


def test_request_flag_activates_confirmation_when_assistant_inactive():
    assert _run_configure(request_require_confirmation=True, assistant_require_confirmation=False) is True


def test_assistant_flag_wins_when_request_is_false():
    assert _run_configure(request_require_confirmation=False, assistant_require_confirmation=True) is True


def test_neither_flag_leaves_confirmation_off():
    assert _run_configure(request_require_confirmation=None, assistant_require_confirmation=False) is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie/service/assistant/test_configure_agent_kwargs_confirmation.py -v
```

Expected: `test_request_flag_activates_confirmation_when_assistant_inactive` FAILS (returns False instead of True). The other two may pass since they don't exercise the new path.

- [ ] **Step 3: Update `configure_agent_kwargs`**

In `src/codemie/service/assistant/assistant_engine_builder.py`, replace the two lines at ~298–299:

```python
        permissions = ToolPermissionsService().get_effective_permissions(assistant)
        agent_kwargs["require_tool_confirmation"] = allow_tool_confirmation and permissions.require_confirmation
```

with:

```python
        permissions = ToolPermissionsService().get_effective_permissions(
            assistant,
            require_confirmation_override=request.require_confirmation,
        )
        agent_kwargs["require_tool_confirmation"] = allow_tool_confirmation and permissions.require_confirmation
```

- [ ] **Step 4: Run all three test files to verify they pass**

```bash
poetry run pytest \
  tests/codemie/service/assistant/test_configure_agent_kwargs_confirmation.py \
  tests/codemie/service/test_tool_permissions_service.py \
  tests/codemie/core/test_models_assistant_chat_request.py \
  -v
```

Expected: all 10 tests PASS.

- [ ] **Step 5: Run lint**

```bash
make ruff
```

Expected: no violations.

- [ ] **Step 6: Commit**

```bash
git add \
  src/codemie/service/assistant/assistant_engine_builder.py \
  tests/codemie/service/assistant/test_configure_agent_kwargs_confirmation.py
git commit -m "feat(EPMCDME-13903): wire require_confirmation request flag into configure_agent_kwargs"
```
