# Technical Research

**Task**: auth budget teams service-account
**Generated**: 2026-08-31T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Implement option 4b for routing Teams bot group-chat requests to per-user budgets instead of the service account's budget. Add a way for the assistant-ask endpoints (ask_assistant_by_id, ask_assistant_by_slug, ask_virtual_assistant in src/codemie/rest_api/routers/assistant.py) to accept the Teams end-user's sender_email and, when the authenticated caller is specifically the Teams integration's allow-listed service account, swap `user` to the DB user resolved by that email before budget checks / LLM dispatch happen. Reject clearly if no matching user is found for that email.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/routers/assistant.py` — the three target endpoints:
  - `ask_assistant_by_id` (line ~1056, async, POST `/v1/assistants/{assistant_id}/model`): resolves assistant, calls `_prepare_assistant_for_execution`, then `await asyncio.to_thread(_ask_assistant, execution_assistant, raw_request, request, user, background_tasks, include_tool_errors, error_detail_level)`.
  - `ask_assistant_by_slug` (line ~1109, sync, POST `/v1/assistants/slug/{assistant_slug:path}/model`): same shape, calls `_ask_assistant` directly (no thread offload).
  - `ask_virtual_assistant` (line ~970, async, POST `/v1/assistants/virtual/model`): builds an in-memory `Assistant.model_construct(...)` (no DB record, `project=user.current_project`), then `await asyncio.to_thread(_ask_virtual_assistant, assistant, raw_request, chat_request, user, background_tasks, include_tool_errors, error_detail_level)`.
  - All three obtain `user: User = Depends(authenticate)` and pass that single `user` object straight through to the private `_ask_assistant` / `_ask_virtual_assistant` helpers (lines 2267 and 2194 respectively), which perform, in order: ownership/access check (`_check_user_can_access_assistant` / skipped for virtual), guardrails, `assistant_user_interaction_service.record_usage(assistant=assistant, user=user)` (skipped for virtual since `assistant.id is None`), `request_summary_manager.create_request_summary(..., user=user.as_user_model())`, and `get_request_handler(assistant, user, request_uuid)` — the handler's `process_request` internally calls `set_llm_context(self.assistant, None, self.user)` (`assistant_handlers.py:385-387`), which is the point at which the LiteLLM/budget key is chosen.
- `src/codemie/service/llm_service/utils.py::set_llm_context` (line 69) — resolves `effective_project` from the asset/user, then `SettingsService.get_litellm_creds(project_name=effective_project, user_id=user.id)`. **This is the exact mechanism that must see the swapped user** — whichever `User` instance is passed to `_ask_assistant`/`_ask_virtual_assistant` (hence into `get_request_handler`) drives which LiteLLM key/budget is charged. `_resolve_effective_project` also reads `user.project_names`, `user.admin_project_names`, and `user.email` for global/shared assets.
- `src/codemie/rest_api/security/user.py::User` (Pydantic `BaseModel`) — fields relevant to budget/LLM routing: `id`, `username`, `email`, `project_names`, `admin_project_names`, `user_type`, `is_admin`, `is_maintainer`, `project_limit`, `auth_token` (excluded from serialization), `client_access_token` (excluded). `user_type: str | None = 'regular'`; `is_external_user` checks `self.user_type == config.EXTERNAL_USER_TYPE` (`"external"`).
- `src/codemie/rest_api/security/user_type_validator.py` — `VALID_USER_TYPES = {'regular', 'external', 'service_account'}` (the `service_account` value was added by the sibling task `2026-08-26-add-service-account-user-type`, already merged — see docs/superpowers/tasks/2026-08-26-add-service-account-user-type/spec.md). `PERSONAL_PROJECT_EXCLUDED_USER_TYPES = {"external", "service_account"}`. `validate_user_type(value, idp_context)` normalizes/validates the IDP `user_type` claim (case-insensitive, default `'regular'`, else 401).
- `src/codemie/repository/user_repository.py::UserRepository` (sync, SQLModel `Session`) — has `get_by_id`, `get_active_by_id`, `get_by_username`, and **`get_by_email(session, email)`** (line 62, case-insensitive via `func.lower(UserDB.email) == email.lower()`) plus an async counterpart `aget_by_email`. This is the lookup primitive available to resolve `sender_email` → `UserDB` synchronously inside a router.
- `src/codemie/service/user/authentication_service.py::AuthenticationService` — `load_user_for_auth` (async, line 139) is the closest existing "build a full `security_user.User` from `UserDB`" helper: loads `user_project_repository`/`user_kb_repository` relationships and constructs `security_user.User(id=..., username=..., email=..., user_type=..., project_names=[...], admin_project_names=[...], knowledge_bases=[...], is_admin=..., is_maintainer=..., is_auditor=..., project_limit=...)`. It is `async` and takes an `AsyncSession`; there is no synchronous equivalent today. `_build_security_user` (line 344, sync, takes a `UserDB` from an *already-open* sync/async session) builds the scalar fields only, with **empty** `project_names`/`admin_project_names`/`knowledge_bases` — callers (`authenticate_dev_header`) separately call `_finalize_authentication` to populate relationships. No existing code builds a fully-populated `security_user.User` synchronously from a bare email string; the routers only ever receive an already-built `User` via `Depends(authenticate)`.
- `src/codemie/rest_api/security/authentication.py::authenticate` — the FastAPI dependency all three endpoints use; ultimately delegates to `PersistentUserProvider.authenticate_and_load_user` (`src/codemie/rest_api/security/user_providers/persistent.py`) → `AuthenticationService.authenticate_persistent_user` (async, caches result in a token-keyed `TTLCache`).
- `src/codemie/service/assistant/assistant_user_interaction_service.py::record_usage(assistant, user)` — records usage keyed by whatever `user` is passed; called only for non-virtual assistants (real DB id) in `_ask_assistant`.
- No existing `sender_email` field on any request model: `AssistantChatRequest` (`src/codemie/core/models.py:554`, used by both `ask_assistant_by_id`/`ask_assistant_by_slug`) and `VirtualAssistantChatRequest` (`src/codemie/rest_api/models/assistant.py`) currently have no field carrying an end-user identity distinct from the authenticated caller. This is a greenfield addition for this task.
- No existing "allow-listed service account" concept was found anywhere in the codebase (`ADMIN_USER_ID` in `src/codemie/configs/config.py:188` is the closest existing pattern — a single privileged-user id held in config — but nothing analogous exists yet for a Teams-bot service account). The Teams integration itself (`CredentialTypes.MS_TEAMS`/`MSTeams`, `src/codemie/rest_api/models/settings.py`, `Settings.check_ms_teams_exist`/`prune_ms_teams_assistant_id`) stores only `project_name` and `assistant_ids` in `credential_values`; it has no field identifying which caller identity (service-account user id/email) is permitted to invoke on its behalf.
- `src/codemie/rest_api/security/user_providers/persistent.py` and `src/codemie/service/user/authentication_service.py` have local, uncommitted debugging edits unrelated to this feature (a hardcoded `"devuser"`/hardcoded UUID short-circuit in the `ENV == "local"` dev-header path). These are pre-existing local modifications, not part of any committed Teams work — flagged here so they are not mistaken for task-relevant baseline behavior.

### Architecture and Layers Affected

- **API/router layer**: `src/codemie/rest_api/routers/assistant.py` — the three named endpoints and their shared private helpers `_ask_assistant` / `_ask_virtual_assistant`. This is the layer named explicitly by the task as the swap point.
- **Security/authentication layer**: `src/codemie/rest_api/security/user.py` (`User` model), `src/codemie/rest_api/security/user_type_validator.py` (`service_account` user type), `src/codemie/rest_api/security/authentication.py` (the `authenticate` dependency that produces the caller's `User`).
- **Repository layer**: `src/codemie/repository/user_repository.py` (`get_by_email` — sync lookup primitive for resolving `sender_email`).
- **Service layer**: `src/codemie/service/user/authentication_service.py` (pattern reference for building a full `User` from `UserDB` + relationships); `src/codemie/service/llm_service/utils.py::set_llm_context` (the budget/LLM-key resolution consumer of whichever `User` reaches the handler).
- **Configuration layer**: `src/codemie/configs/config.py` (existing single-privileged-id pattern via `ADMIN_USER_ID`; no Teams-bot-specific config exists yet).

### Integration Points

- `_ask_assistant`/`_ask_virtual_assistant` → `get_request_handler(assistant, user, request_uuid)` (`src/codemie/rest_api/handlers/assistant_handlers.py:1115`) → handler's `process_request` → `set_llm_context(self.assistant, None, self.user)` → `SettingsService.get_litellm_creds(project_name=..., user_id=user.id)`. Any user swap must happen before this chain starts (i.e., before the `_ask_assistant`/`_ask_virtual_assistant` call, or at the very top of those functions) to affect budget/LLM dispatch.
- `assistant_user_interaction_service.record_usage(assistant=assistant, user=user)` — usage/analytics recording keyed by `user`; will also pick up the swapped user if the swap happens before this call.
- `_check_user_can_access_assistant(user, assistant, "view", Action.READ)` inside `_ask_assistant` — access control (`Ability(user).can(...)`) is evaluated against whatever `user` is passed; swapping `user` before this call means the resolved end-user's own permissions gate access, not the service account's.
- `request_summary_manager.create_request_summary(..., user=user.as_user_model())` — also downstream of the swap point.

### Patterns and Conventions

- Standardized error responses via `ExtendedHTTPException(code, message, details, help)` (`src/codemie/core/exceptions.py`) — used throughout `assistant.py` for 400/403/404/422/500 cases; the "reject clearly if no matching user is found" requirement should follow this convention (likely 400 or 404, matching e.g. `_get_assistant_by_id_or_raise`'s style at line 1943).
- Feature-gating precedent: `TEAMS_BOT_INTEGRATION_FEATURE = "teamsBotIntegration"` checked via `customer_config.is_feature_enabled(...)` in `src/codemie/service/settings/settings_request_validator.py:270-287`, raising 403 when disabled — the existing convention for gating Teams-specific behavior.
- Single-privileged-identity-in-config precedent: `config.ADMIN_USER_ID` (`src/codemie/configs/config.py:188`), compared directly against `idp_user.id`/`user.id` — the closest existing pattern for an "allow-listed" singleton identity, though nothing currently ties a config value to a specific `user_type == "service_account"` row.
- Sync vs. async split: `ask_assistant_by_id`/`ask_virtual_assistant` are `async def` and offload the actual work to `asyncio.to_thread(...)`; `ask_assistant_by_slug` is a plain sync `def`. A user-by-email lookup must work correctly in both contexts — the sync `UserRepository.get_by_email` (used with a sync `Session`) is usable directly in the sync endpoint and inside the `asyncio.to_thread`-offloaded sync helpers, but there is no existing helper that assembles a *fully populated* `security_user.User` (with `project_names`, `admin_project_names`, `is_admin`, etc.) synchronously from a `UserDB` row outside of the async `AuthenticationService.load_user_for_auth`/`_finalize_authentication` path.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/development/security-patterns.md`, `.ai-run/guides/api/rest-api-patterns.md`, `.ai-run/guides/api/endpoint-conventions.md` exist in the repo's guide set (per `AGENTS.md`) and are the P0/P1 guides for this task's category (Security / API) — not read in depth here since this document is scoped to filesystem findings, but they are the primary AI guidance source per repo convention and should be consulted at spec/plan time.
- No guide file specifically documents the Teams-bot service-account budget-routing problem; it is not yet a documented pattern anywhere in `.ai-run/guides/`.

### Architectural Decisions

- No ADR or recorded decision for "option 4b" (or any numbered option set for Teams budget routing) was found anywhere in the repository — neither under `docs/`, `.ai-run/guides/`, nor in any prior `docs/superpowers/tasks/*` research/spec/plan file for the Teams-integration work (`2026-08-27-move-teams-to-integrations`, `2026-08-27-teams-integ-1-foundation`, `2026-08-27-teams-integ-2-migration`, `2026-08-28-teams-integ-3-read-merge`). None of those prior-phase documents mention `service_account`, `sender_email`, or per-user budget routing for Teams. The "option 4b" framing named in the task is not sourced from any file in this repository — see Section 8.
- Recorded decision from the sibling task `2026-08-26-add-service-account-user-type` (already implemented): `"service_account"` is a valid `user_type` value flowing through `IdpUser.user_type` → `validate_user_type` → `UserDB.user_type`; explicitly out of scope for that task were "budget/quota... based on `user_type`" — i.e., the service-account user type exists, but no budget-routing behavior was built for it yet. This task is the first to connect `user_type == "service_account"` to budget-routing logic.
- Recorded decision from `2026-08-27-move-teams-to-integrations/technical-analysis.md`: this repository is backend-only (FastAPI + Alembic + Python tool packages); "frontend components"/"old Teams bot configuration UI" were explicitly noted as not present in this repo. Confirms the Teams bot's actual message-relay logic (the caller that would send `sender_email`) is an external service not present in this filesystem.

### Derived Conventions

- Error handling: raise `ExtendedHTTPException` with explicit `code`/`message`/`details`/`help`, not bare `HTTPException`, matching every other validation branch in `assistant.py` and `settings_request_validator.py`.
- Config-driven allow-listing of a single privileged identity follows the `ADMIN_USER_ID` precedent (a plain `str` config field compared against `user.id`), suggesting a similar single-value (or list-value) config field would be idiomatic for an allow-listed Teams service-account identity, rather than embedding it in the `ms_teams` `Settings` row (which is project-scoped and per-integration, not global).
- User-identity swapping precedent for "act on behalf of" flows was not found elsewhere in the codebase (no existing impersonation/on-behalf-of pattern); this is a novel code path for this codebase.

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/routers/test_assistant.py` and sibling files (`test_assistant_mapping.py`, `test_assistant_categories.py`, `test_assistant_reactions.py`, `test_assistant_marketplace.py`, `test_assistant_plugin_tools.py`, `test_assistant_mcp_tools.py`, `test_assistant_sort_params.py`, `test_assistant_users.py`, `test_assistant_prompt_variable_mapping.py`) cover various endpoints in `assistant.py`, but none target `ask_assistant_by_id`, `ask_assistant_by_slug`, or `ask_virtual_assistant` by name in a way that surfaced in this scan for user-identity-swap or budget-routing behavior.
- `tests/codemie/service/user/test_authentication_service.py` covers `AuthenticationService` flows (including the `_sync_budget_for_idp_projects` pattern from the sibling `epmcdme-13582-keycloak-service-account-budget` task) but not any email-based user-swap logic (none exists yet).
- No test file anywhere references `sender_email`, an allow-listed service account, or per-user Teams budget routing — confirming this is entirely new behavior.

### Testing Framework and Patterns

- pytest is the framework in use throughout `tests/codemie/`. Router tests typically build a bare `FastAPI()` app, mount the router under test, and use `AsyncClient(transport=ASGITransport(app=app))`, patching the IDP authenticate call (e.g. `@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")`) as the innermost decorator (per `2026-08-27-move-teams-to-integrations/technical-analysis.md` findings, corroborated by the general test directory layout).

### Coverage Gaps

- No tests exist for the swap-user-by-email behavior this task introduces (necessarily, since the feature doesn't exist yet).
- No tests exist verifying that `set_llm_context`/budget selection is driven by whichever `User` object reaches `get_request_handler` — this is inferred from reading `set_llm_context` and `assistant_handlers.py`, not from a test that pins the behavior.
- No tests exist for the "reject clearly if no matching user is found" error path since there is no such lookup today.

---

## 5. Configuration and Environment

### Environment Variables

- `config.ADMIN_USER_ID` (`src/codemie/configs/config.py:188`) — existing single-privileged-user-id config value; closest existing pattern to an "allow-listed" identity, but not Teams-specific.
- `config.EXTERNAL_USER_TYPE` (`config.py:304`, `"external"`) and `EXTERNAL_USER_ALLOWED_PROJECTS` (`config.py:305`) — existing user-type-scoped config precedent, though for the `external` type, not `service_account`.
- No `TEAMS_*` or `SERVICE_ACCOUNT_*` environment variable exists in `config.py` today.

### Configuration Files

- `config/customer/customer-config.yaml` — holds the `features:teamsBotIntegration` flag (per `2026-08-27-move-teams-to-integrations/technical-analysis.md`); this flag currently gates the `ms_teams` `Settings` validation path in `settings_request_validator.py`, not any budget-routing logic.
- No configuration file was found that names or allow-lists a specific service-account identity for Teams.

### Feature Flags and Deployment Concerns

- `TEAMS_BOT_INTEGRATION_FEATURE = "teamsBotIntegration"` (`settings_request_validator.py:270`) is the only Teams-specific feature flag found; whether this task's swap logic should also be gated by it is an open design question, not something discovered as already decided.

---

## 6. Risk Indicators

- Speculative: The task will likely need a new config value (or a new lookup against `UserDB.user_type == "service_account"` plus a specific identifying field such as email/id) to determine "the Teams integration's allow-listed service account" — no such concept exists in the codebase today, so this is new design surface, not a discovered constraint.
- Speculative: A new request field (e.g. `sender_email`) will need to be added to `AssistantChatRequest` and/or `VirtualAssistantChatRequest`/a new request wrapper — none of the three endpoints' request models carry an end-user-distinct-from-caller field today.
- The three target endpoints have three different sync/async shapes (`ask_assistant_by_id` async+`to_thread`, `ask_assistant_by_slug` sync, `ask_virtual_assistant` async+`to_thread`), and there is no existing synchronous helper that builds a fully populated `security_user.User` (with `project_names`, `is_admin`, etc.) from a bare email — only the async `AuthenticationService.load_user_for_auth`/`_finalize_authentication` path does this today. Building or reusing a consistent lookup+construction path across all three call shapes is a genuine complexity/risk area.
- `ask_virtual_assistant` constructs an assistant with `project=user.current_project` from the *authenticated caller's* projects before any swap could occur; if the swap must also affect which project the virtual assistant is scoped to, the swap needs to happen before that assignment, not just before `_ask_virtual_assistant` is invoked — worth flagging precisely because the virtual endpoint's data flow differs from the other two (no DB assistant, no ownership check).
- `_check_user_can_access_assistant` (real-assistant path) evaluates `Ability(user).can(...)` against whichever `user` is passed to `_ask_assistant` — swapping the user before this call changes *access control*, not just budget attribution; this is a behavioral side effect beyond pure LLM-key selection that the spec/plan stage should address explicitly (is that intended, per "option 4b", or should access stay gated on the service account while only the budget key uses the resolved end-user?).
- No existing test coverage or documented decision for any part of this feature exists in the repository — this is a genuinely novel, undocumented code path with no prior-art implementation to mirror beyond the general `ADMIN_USER_ID`-style single-identity config pattern.
- Two files relevant to the authentication hot path (`persistent.py`, `authentication_service.py`) currently carry local, uncommitted, unrelated debugging changes (hardcoded dev-header short-circuits) per `git status`/`git diff` — not part of this feature, but anyone editing `authenticate_dev_header`/`PersistentUserProvider` during this task should be aware these lines are already modified locally and not committed baseline.

---

## 7. Summary for Complexity Assessment

This task touches the API/router layer (`assistant.py`'s three ask-endpoints and their shared private helpers `_ask_assistant`/`_ask_virtual_assistant`), the security/authentication layer (`User` model, `user_type_validator.py`'s existing `service_account` type, the `authenticate` dependency), the repository layer (`UserRepository.get_by_email`, already present and reusable), and the service layer (`set_llm_context` in `llm_service/utils.py`, which is confirmed to be the single downstream consumer that actually selects the LiteLLM budget key from `user.id`/`user.project_names`). The change surface in this repository is moderate: 3 endpoint functions plus 2 shared private helpers to modify, at least one new request field, and a new lookup-and-swap code path with no prior-art to mirror beyond the `ADMIN_USER_ID` single-identity-in-config pattern.

Technical novelty is high: no "act on behalf of another user" / impersonation pattern exists anywhere else in the codebase, no config or data model currently names "the Teams integration's allow-listed service account," and none of the three prior completed phases of this same Teams-integration ticket (`teams-integ-1-foundation`, `teams-integ-2-migration`, `teams-integ-3-read-merge`) or the `add-service-account-user-type` task addressed budget routing. The three target endpoints also have divergent sync/async data flows (notably `ask_virtual_assistant`, which builds an in-memory assistant scoped to the *caller's* project before any swap could happen), so a single swap helper must be designed carefully to apply consistently and at the right point (before access checks / project resolution / `set_llm_context`) across all three.

Test coverage posture is a clean slate — no existing tests reference `sender_email`, service-account allow-listing, or per-user Teams budget routing, so all test coverage for this feature will be net-new. Key risk factors: (1) the undecided scope of the config/allow-list mechanism for the service account identity, (2) the undecided interaction between the swap and existing access-control checks (`Ability(user).can(...)`) versus pure budget/LLM-key selection, and (3) the lack of a synchronous "build a full `User` from an email" helper reusable across the sync and async endpoint shapes.

---

## 8. External References

None named by the task. The task_context describes "option 4b" as a design label but does not point to a specific file, directory, or URL as the source of truth for that option's definition. A search of this repository (all `docs/superpowers/tasks/*` entries for the same Jira ticket EPMCDME-14111, `docs/codemie/`, and the full source tree) found no file mentioning "option 4b," "4b," a numbered options list for Teams budget routing, or an "allow-listed service account" concept for Teams. If an "option 4b" writeup exists, it is outside this filesystem (e.g., a Jira comment, a design doc in another repo, or a conversation) and was not sourced here.
