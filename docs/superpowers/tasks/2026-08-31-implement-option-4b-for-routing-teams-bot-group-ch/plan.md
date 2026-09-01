# Teams Bot Per-User Budget Routing (Option 4b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the allow-listed Teams service account calls the assistant-ask endpoints with a Teams end-user email header, budget/LLM-context resolution is billed to the DB user matching that email instead of the service account, while access control stays gated on the service account.

**Architecture:** Add a `TEAMS_SERVICE_ACCOUNT_ID` config value and a new sync helper, `resolve_billing_user(caller, sender_email)`, that returns the caller unchanged unless the caller is exactly that allow-listed service account (by `user.id`) and a Teams end-user email — carried via a new `X-Teams-Sender-Email` request header, not a body field — resolves to a DB user (raising a clear 404 if it doesn't). Wire this helper into all three ask-endpoints so the *original* caller still drives access control, and only the *resolved* user drives usage recording, request-summary attribution, and `get_request_handler` (hence `set_llm_context`'s budget-key selection).

**Tech Stack:** FastAPI routers, Pydantic request models, SQLModel sync `Session`, pytest with `unittest.mock.patch`.

**Requirements:** inline text supplied by the caller (no `spec.md` for this task) — see Acceptance criteria below.

**Technical analysis:** `docs/superpowers/tasks/2026-08-31-implement-option-4b-for-routing-teams-bot-group-ch/technical-analysis.md`

## Global Constraints

- Commit per task using the repository's existing convention (no separate commit-format instructions here).
- Do not modify `src/codemie/rest_api/security/user_providers/persistent.py` or `src/codemie/service/user/authentication_service.py` — both carry unrelated, uncommitted local debugging edits that are out of scope.
- Only the exact caller identified by `config.TEAMS_SERVICE_ACCOUNT_ID` (and `user_type == "service_account"`) may trigger sender-email substitution.
- Access-control checks (`Ability(user).can(...)` / `_check_user_can_access_assistant`) must always evaluate the original authenticated caller, never the resolved billing user.
- The Teams end-user's email travels as the `X-Teams-Sender-Email` request header, never as a body field — no changes to `AssistantChatRequest`/`VirtualAssistantChatRequest` schemas.

---

## Acceptance criteria

- [ ] A new `TEAMS_SERVICE_ACCOUNT_ID` config value (default `"codemie-teams-bot"`) identifies the single allow-listed Teams service account by user id, following the `ADMIN_USER_ID` single-value pattern.
- [ ] The three ask-endpoints accept the Teams end-user's email via a new `X-Teams-Sender-Email` request header (defined as a named constant, following the `USER_ID_HEADER`/`BIND_KEY_HEADER` convention in `authentication.py`) — no request-body-model changes.
- [ ] When the caller's `user.id` equals `config.TEAMS_SERVICE_ACCOUNT_ID` and the header value matches a DB user, budget/LLM-context resolution (`record_usage`, `create_request_summary`, `get_request_handler` → `set_llm_context`) uses that resolved user for `ask_assistant_by_id`, `ask_assistant_by_slug`, and `ask_virtual_assistant`.
- [ ] Access control (`_check_user_can_access_assistant`) in `_ask_assistant` continues to evaluate the original service-account caller, never the resolved user.
- [ ] The header is ignored when the caller's `user.id` is not `config.TEAMS_SERVICE_ACCOUNT_ID` — no substitution happens and the request proceeds billed to the original caller.
- [ ] Header present, caller is the allow-listed service account, but no DB user matches the email: request is rejected with a clear 4xx (`ExtendedHTTPException`), never silently billed to the service account.
- [ ] `ask_virtual_assistant`'s in-memory assistant is still scoped to the *original* caller's `current_project`; only budget resolution uses the swapped-in user.
- [ ] `persistent.py` / `authentication_service.py` local debug edits are untouched.

---

### Task 1: Add `TEAMS_SERVICE_ACCOUNT_ID` config value

**Files:**
- Modify: `src/codemie/configs/config.py:188` (immediately after `ADMIN_USER_ID: str = ""`)

**Interfaces:**
- Produces: `config.TEAMS_SERVICE_ACCOUNT_ID: str` (default `"codemie-teams-bot"`).

- [ ] **Step 1:** Add `TEAMS_SERVICE_ACCOUNT_ID: str = "codemie-teams-bot"` on its own line directly below `ADMIN_USER_ID: str = ""` (config.py:188), matching that field's style.
- [ ] **Step 2:** Commit.

Test-first: no — a bare config default addition with no branching logic; it is exercised by Task 3's tests.

---

### Task 2: Define the `X-Teams-Sender-Email` header constant and extraction helper

**Files:**
- Modify: `src/codemie/rest_api/routers/assistant.py` (module-level constants, near the top with other router-local constants)
- Test: `tests/codemie/rest_api/routers/test_assistant.py` (new small test)

**Interfaces:**
- Produces: `TEAMS_SENDER_EMAIL_HEADER = "X-Teams-Sender-Email"` (module-level constant in `assistant.py`); `_get_teams_sender_email(raw_request: Request) -> str | None`, reading `raw_request.headers.get(TEAMS_SENDER_EMAIL_HEADER)`. Tasks 4 and 5 call `_get_teams_sender_email(raw_request)` instead of reading any request-body field.

- [ ] **Step 1: Write the failing test**

```python
# tests/codemie/rest_api/routers/test_assistant.py
from unittest.mock import MagicMock

from codemie.rest_api.routers.assistant import TEAMS_SENDER_EMAIL_HEADER, _get_teams_sender_email


def test_get_teams_sender_email_reads_header():
    raw_request = MagicMock()
    raw_request.headers = {TEAMS_SENDER_EMAIL_HEADER: "enduser@example.com"}
    assert _get_teams_sender_email(raw_request) == "enduser@example.com"


def test_get_teams_sender_email_absent_header_returns_none():
    raw_request = MagicMock()
    raw_request.headers = {}
    assert _get_teams_sender_email(raw_request) is None
```

- [ ] **Step 2:** Run `pytest tests/codemie/rest_api/routers/test_assistant.py::test_get_teams_sender_email_reads_header tests/codemie/rest_api/routers/test_assistant.py::test_get_teams_sender_email_absent_header_returns_none -v`. Expected: FAIL — `ImportError: cannot import name 'TEAMS_SENDER_EMAIL_HEADER'`.
- [ ] **Step 3:** Add near the top of `assistant.py` (with the other router-local constants):

```python
# Header carrying the Teams end-user's email for group-chat requests relayed by
# the Teams-bot service account. Mirrors the USER_ID_HEADER/BIND_KEY_HEADER
# convention in security/authentication.py. Never a body field — see resolve_billing_user.
TEAMS_SENDER_EMAIL_HEADER = "X-Teams-Sender-Email"


def _get_teams_sender_email(raw_request: Request) -> str | None:
    return raw_request.headers.get(TEAMS_SENDER_EMAIL_HEADER)
```

- [ ] **Step 4:** Run the same pytest command. Expected: PASS.
- [ ] **Step 5:** Commit.

Test-first: yes — both tests fail on import until the constant and function are added in Step 3.

---

### Task 3: Add `resolve_billing_user` helper with id-based allow-list and email lookup

**Files:**
- Create: `src/codemie/service/user/billing_user_resolver.py`
- Test: `tests/codemie/service/user/test_billing_user_resolver.py`

**Interfaces:**
- Consumes: `codemie.configs.config.TEAMS_SERVICE_ACCOUNT_ID: str`; `codemie.clients.postgres.get_session` (sync context manager); `codemie.repository.user_repository.user_repository.get_by_email(session, email)`; `codemie.repository.user_project_repository.user_project_repository.get_by_user_id(session, user_id)`; `codemie.repository.user_kb_repository.user_kb_repository.get_by_user_id(session, user_id)`; `codemie.rest_api.security.user.User`.
- Produces: `resolve_billing_user(caller: User, sender_email: str | None) -> User` — used by Tasks 4 and 5 as the single swap point. `sender_email` is whatever `_get_teams_sender_email(raw_request)` (Task 2) returned — this helper itself is header-agnostic and just takes a plain string.

- [ ] **Step 1: Write the failing tests**

```python
# tests/codemie/service/user/test_billing_user_resolver.py
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User
from codemie.service.user.billing_user_resolver import resolve_billing_user


def _caller(user_type="service_account", user_id="codemie-teams-bot"):
    return User(id=user_id, email="bot@svc.example.com", user_type=user_type, project_names=[])


def test_no_sender_email_returns_caller_unchanged():
    caller = _caller()
    assert resolve_billing_user(caller, None) is caller


@patch("codemie.configs.config.TEAMS_SERVICE_ACCOUNT_ID", "codemie-teams-bot")
def test_non_allowlisted_caller_ignores_sender_email():
    caller = _caller(user_id="some-other-user")
    assert resolve_billing_user(caller, "enduser@example.com") is caller


@patch("codemie.configs.config.TEAMS_SERVICE_ACCOUNT_ID", "codemie-teams-bot")
@patch("codemie.service.user.billing_user_resolver.get_session")
@patch("codemie.service.user.billing_user_resolver.user_repository")
def test_unmatched_sender_email_raises_clear_error(mock_user_repo, mock_get_session):
    mock_get_session.return_value.__enter__.return_value = MagicMock()
    mock_user_repo.get_by_email.return_value = None
    caller = _caller()
    with pytest.raises(ExtendedHTTPException):
        resolve_billing_user(caller, "unknown@example.com")


@patch("codemie.configs.config.TEAMS_SERVICE_ACCOUNT_ID", "codemie-teams-bot")
@patch("codemie.service.user.billing_user_resolver.get_session")
@patch("codemie.service.user.billing_user_resolver.user_repository")
@patch("codemie.service.user.billing_user_resolver.user_project_repository")
@patch("codemie.service.user.billing_user_resolver.user_kb_repository")
def test_matched_sender_email_returns_resolved_user(
    mock_kb_repo, mock_project_repo, mock_user_repo, mock_get_session
):
    mock_get_session.return_value.__enter__.return_value = MagicMock()
    db_user = MagicMock(
        id="end-user-1", username="enduser", email="enduser@example.com",
        user_type="regular", is_admin=False, is_maintainer=False, is_auditor=False,
        project_limit=None,
    )
    mock_user_repo.get_by_email.return_value = db_user
    mock_project_repo.get_by_user_id.return_value = [
        MagicMock(project_name="proj-a", is_project_admin=True)
    ]
    mock_kb_repo.get_by_user_id.return_value = []

    caller = _caller()
    resolved = resolve_billing_user(caller, "enduser@example.com")

    assert resolved.id == "end-user-1"
    assert resolved.project_names == ["proj-a"]
    assert resolved.admin_project_names == ["proj-a"]
```

- [ ] **Step 2:** Run `pytest tests/codemie/service/user/test_billing_user_resolver.py -v`. Expected: FAIL — module does not exist.
- [ ] **Step 3: Implement the helper**

```python
# src/codemie/service/user/billing_user_resolver.py
from fastapi import status

from codemie.clients.postgres import get_session
from codemie.configs import config, logger
from codemie.core.exceptions import ExtendedHTTPException
from codemie.repository.user_kb_repository import user_kb_repository
from codemie.repository.user_project_repository import user_project_repository
from codemie.repository.user_repository import user_repository
from codemie.rest_api.security.user import User

_TEAMS_SERVICE_ACCOUNT_USER_TYPE = "service_account"


def resolve_billing_user(caller: User, sender_email: str | None) -> User:
    """Return the user whose budget should be charged for this request.

    Returns `caller` unchanged unless `caller.id` is exactly the allow-listed
    Teams service account id (config.TEAMS_SERVICE_ACCOUNT_ID) AND
    `sender_email` is provided — in that case the DB user matching
    `sender_email` is returned instead. Never affects access control:
    callers must keep using the original `caller` for authorization checks.
    """
    if not sender_email:
        return caller

    is_allowlisted = (
        bool(config.TEAMS_SERVICE_ACCOUNT_ID)
        and caller.user_type == _TEAMS_SERVICE_ACCOUNT_USER_TYPE
        and caller.id == config.TEAMS_SERVICE_ACCOUNT_ID
    )
    if not is_allowlisted:
        logger.warning(
            f"sender_email_ignored: caller_id={caller.id!r} is not the allow-listed "
            "Teams service account; substitution skipped"
        )
        return caller

    with get_session() as session:
        db_user = user_repository.get_by_email(session, sender_email)
        if db_user is None:
            raise ExtendedHTTPException(
                code=status.HTTP_404_NOT_FOUND,
                message="No user found for sender_email",
                details=f"sender_email={sender_email!r} does not match any known user.",
                help="Verify the Teams end-user's email is registered in CodeMie.",
            )
        projects = user_project_repository.get_by_user_id(session, db_user.id)
        kbs = user_kb_repository.get_by_user_id(session, db_user.id)
        return User(
            id=db_user.id,
            username=db_user.username,
            name=db_user.name or "",
            email=db_user.email,
            picture=db_user.picture or "",
            user_type=db_user.user_type,
            project_names=[p.project_name for p in projects],
            admin_project_names=[p.project_name for p in projects if p.is_project_admin],
            knowledge_bases=[kb.kb_name for kb in kbs],
            is_admin=db_user.is_admin,
            is_maintainer=db_user.is_maintainer,
            is_auditor=db_user.is_auditor,
            project_limit=db_user.project_limit,
        )
```

- [ ] **Step 4:** Run `pytest tests/codemie/service/user/test_billing_user_resolver.py -v`. Expected: PASS (4 tests).
- [ ] **Step 5:** Commit.

Test-first: yes — the four behavioral tests above (no-op passthrough, non-allow-listed ignore, unmatched-email 4xx, matched-email swap) all fail against a nonexistent module before Step 3.

*Negative constraints enforced here:* (b) non-allow-listed callers (by `id`, not email) never trigger substitution (`test_non_allowlisted_caller_ignores_sender_email`); (c) an unmatched email raises `ExtendedHTTPException` rather than falling back to the caller (`test_unmatched_sender_email_raises_clear_error`).

---

### Task 4: Wire billing-user resolution into `_ask_assistant` / `ask_assistant_by_id` / `ask_assistant_by_slug`

**Files:**
- Modify: `src/codemie/rest_api/routers/assistant.py:2267-2329` (`_ask_assistant`)
- Modify: `src/codemie/rest_api/routers/assistant.py:1056-1100` (`ask_assistant_by_id`)
- Modify: `src/codemie/rest_api/routers/assistant.py:1109-1150` (`ask_assistant_by_slug`)
- Test: `tests/codemie/rest_api/routers/test_assistant.py` (append to `TestAskAssistantWithGuardrails` or a new `TestAskAssistantBillingUserSwap` class)

**Interfaces:**
- Consumes: `resolve_billing_user(caller, sender_email)` from Task 3; `_get_teams_sender_email(raw_request)` from Task 2.
- Produces: `_ask_assistant(assistant, raw_request, request, user, background_tasks, include_tool_errors=..., error_detail_level=..., billing_user=None)` — `billing_user` is optional; when omitted it defaults to `user` (today's behavior unchanged).

- [ ] **Step 1: Write the failing test**

```python
# tests/codemie/rest_api/routers/test_assistant.py (new test in same style as existing class)
class TestAskAssistantBillingUserSwap:
    @patch("codemie.rest_api.routers.assistant.assistant_user_interaction_service.record_usage")
    @patch("codemie.rest_api.routers.assistant.request_summary_manager.create_request_summary")
    @patch("codemie.rest_api.routers.assistant.Ability")
    @patch("codemie.rest_api.routers.assistant.get_request_handler")
    def test_billing_user_drives_record_usage_while_access_check_uses_original_user(
        self, mock_get_handler, mock_ability, mock_request_summary, mock_record_usage,
        mock_user, mock_assistant,
    ):
        from codemie.rest_api.routers.assistant import _ask_assistant
        from codemie.core.models import AssistantChatRequest
        from unittest.mock import MagicMock

        mock_ability_instance = MagicMock()
        mock_ability_instance.can.return_value = True
        mock_ability.return_value = mock_ability_instance
        mock_handler = MagicMock()
        mock_handler.process_request.return_value = {"response": "ok"}
        mock_get_handler.return_value = mock_handler

        billing_user = MagicMock(spec=User)
        billing_user.id = "end-user-1"
        billing_user.as_user_model.return_value = MagicMock()

        request = AssistantChatRequest(text="hi")
        _ask_assistant(
            mock_assistant, MagicMock(state=MagicMock(uuid="req-1")), request,
            mock_user, MagicMock(), billing_user=billing_user,
        )

        # Access control checked against the ORIGINAL caller
        mock_ability.assert_called_with(mock_user)
        # Budget/usage attribution uses the RESOLVED billing user
        mock_record_usage.assert_called_once_with(assistant=mock_assistant, user=billing_user)
        mock_get_handler.assert_called_once_with(mock_assistant, billing_user, "req-1")
```

- [ ] **Step 2:** Run `pytest tests/codemie/rest_api/routers/test_assistant.py::TestAskAssistantBillingUserSwap -v`. Expected: FAIL — `_ask_assistant()` raises `TypeError: unexpected keyword argument 'billing_user'`.
- [ ] **Step 3:** In `_ask_assistant` (assistant.py:2267), add a `billing_user: User | None = None` parameter and, as the first line of the body, set `billing_user = billing_user or user`. Keep `_check_user_can_access_assistant(user, ...)` (line 2284) unchanged — it must keep receiving `user`. Change line 2312 to `assistant_user_interaction_service.record_usage(assistant=assistant, user=billing_user)`, line 2317's `user=user.as_user_model()` to `user=billing_user.as_user_model()`, and line 2320's `get_request_handler(assistant, user, request_uuid)` to `get_request_handler(assistant, billing_user, request_uuid)`.
- [ ] **Step 4:** In `ask_assistant_by_id` (assistant.py:1090-1099) and `ask_assistant_by_slug` (assistant.py, its `_ask_assistant` call site), add `sender_email = _get_teams_sender_email(raw_request)` and `billing_user = resolve_billing_user(user, sender_email)` before the call, importing `resolve_billing_user` from `codemie.service.user.billing_user_resolver`, and pass `billing_user=billing_user` into the `_ask_assistant`/`asyncio.to_thread(_ask_assistant, ...)` call. Do not read anything from `request` (the body model) — the email comes only from the header.
- [ ] **Step 5:** Run `pytest tests/codemie/rest_api/routers/test_assistant.py -v`. Expected: PASS (new test plus all pre-existing tests in the file, unaffected since `billing_user` defaults to `user`).
- [ ] **Step 6:** Commit.

Test-first: yes — `test_billing_user_drives_record_usage_while_access_check_uses_original_user` fails with `TypeError` until Step 3 adds the parameter, and its two assertions (`Ability` called with the original `mock_user`, `record_usage`/`get_request_handler` called with `billing_user`) pin the negative constraint.

*Negative constraint enforced here:* (a) access control (`Ability(user).can(...)` inside `_check_user_can_access_assistant`) is asserted to run against the original caller, never `billing_user`.

---

### Task 5: Wire billing-user resolution into `ask_virtual_assistant`

**Files:**
- Modify: `src/codemie/rest_api/routers/assistant.py:970-1047` (`ask_virtual_assistant`)
- Test: `tests/codemie/rest_api/routers/test_assistant.py` (new test alongside Task 4's)

**Interfaces:**
- Consumes: `resolve_billing_user(caller, sender_email)` from Task 3; `_get_teams_sender_email(raw_request)` from Task 2. `_ask_virtual_assistant`'s signature is unchanged — the router passes the already-resolved user as its single `user` argument, since that helper has no access-control check to protect.

- [ ] **Step 1: Write the failing test**

```python
class TestAskVirtualAssistantBillingUserSwap:
    @patch("codemie.rest_api.routers.assistant.resolve_billing_user")
    @patch("codemie.rest_api.routers.assistant.asyncio.to_thread")
    async def test_virtual_assistant_project_uses_caller_but_thread_gets_billing_user(
        self, mock_to_thread, mock_resolve, mock_user,
    ):
        from codemie.rest_api.routers.assistant import (
            ask_virtual_assistant, VirtualAssistantChatRequest, TEAMS_SENDER_EMAIL_HEADER,
        )
        from unittest.mock import MagicMock

        mock_user.current_project = "caller-project"
        billing_user = MagicMock(spec=User)
        mock_resolve.return_value = billing_user
        mock_to_thread.return_value = {"response": "ok"}

        raw_request = MagicMock()
        raw_request.state.wait_for_disconnect = MagicMock(return_value=MagicMock())
        raw_request.headers = {TEAMS_SENDER_EMAIL_HEADER: "enduser@example.com"}
        request = VirtualAssistantChatRequest()

        await ask_virtual_assistant(raw_request, MagicMock(), request, mock_user)

        mock_resolve.assert_called_once_with(mock_user, "enduser@example.com")
        # to_thread's positional args: (_ask_virtual_assistant, assistant, raw_request, chat_request, user, ...)
        call_args = mock_to_thread.call_args.args
        passed_assistant = call_args[1]
        passed_user = call_args[4]
        assert passed_assistant.project == "caller-project"  # project scoping stays on the original caller
        assert passed_user is billing_user  # budget resolution uses the resolved user
```

- [ ] **Step 2:** Run `pytest tests/codemie/rest_api/routers/test_assistant.py::TestAskVirtualAssistantBillingUserSwap -v`. Expected: FAIL — `resolve_billing_user` is not imported/called in `ask_virtual_assistant`, so `mock_resolve` is never invoked and `passed_user` is `mock_user`, not `billing_user`.
- [ ] **Step 3:** In `ask_virtual_assistant` (assistant.py:970), import `resolve_billing_user` from `codemie.service.user.billing_user_resolver` at module level. After the `assistant = Assistant.model_construct(...)` block (which must keep using the original `user.current_project` at line 997 — do not move or change that line), insert `sender_email = _get_teams_sender_email(raw_request)` and `billing_user = resolve_billing_user(user, sender_email)`. Change the `asyncio.to_thread(_ask_virtual_assistant, assistant, raw_request, chat_request, user, ...)` call (line 1037-1046) to pass `billing_user` in place of `user`.
- [ ] **Step 4:** Run `pytest tests/codemie/rest_api/routers/test_assistant.py::TestAskVirtualAssistantBillingUserSwap -v`. Expected: PASS.
- [ ] **Step 5:** Commit.

Test-first: yes — the assertions `mock_resolve.assert_called_once_with(mock_user, "enduser@example.com")` and `passed_user is billing_user` fail until Step 3 threads the header-derived resolved user through; `passed_assistant.project == "caller-project"` guards against regressing project scoping onto the swapped user.

*Negative constraint enforced here:* (d) verifies the assistant's `project` is still derived from the original caller's `current_project`, never from the resolved billing user, matching decision 6.

---

## Negative-constraints pass

- **Access control must never see the swapped user** — enforced and tested in Task 4 (`Ability` asserted against the original `mock_user`, not `billing_user`).
- **A non-allow-listed caller's sender-email header must never trigger substitution** — enforced and tested in Task 3 (`test_non_allowlisted_caller_ignores_sender_email`, keyed on `caller.id`, not email).
- **An unmatched sender email must reject clearly, never silently fall back to service-account billing** — enforced and tested in Task 3 (`test_unmatched_sender_email_raises_clear_error`, raises `ExtendedHTTPException` instead of returning `caller`).
- **The end-user email must travel via header, not body field** — enforced by Task 2 (`TEAMS_SENDER_EMAIL_HEADER` / `_get_teams_sender_email`) and by Tasks 4/5, which read only `raw_request` headers, never `request.sender_email` or any other body attribute.
- **`ask_virtual_assistant`'s project scoping must stay on the original caller** — enforced and tested in Task 5 (`passed_assistant.project == "caller-project"`).
- **`persistent.py` / `authentication_service.py` local debug edits are out of scope** — no task in this plan touches either file.
