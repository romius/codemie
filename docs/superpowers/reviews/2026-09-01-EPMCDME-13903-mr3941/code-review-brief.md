# Code review — 2026-09-01-EPMCDME-13903-mr3941 (2026-09-01)

**request-changes** · confidence: low · 12 blocking · 0 deferred · 13 filtered as noise

## Resolution (2026-09-01)

- **fixed**: CR-006 (multi-tool-call confirmation bypass — sibling calls now denied before resume), CR-009 (resume ownership check added), CR-010 (request override now only raises strictness, never lowers it), CR-011 (floor policy coerced to enum before use)
- **skipped**: CR-003 (decided against — a new chat message is allowed to supersede/decline a pending confirmation by design), CR-001, CR-002, CR-004, CR-005, CR-007, CR-008, CR-012 (triaged as noise, no action taken)
- No new tests added; existing tests asserting the old CR-010/CR-011 behavior (e.g. `test_request_override_beats_assistant`) will now fail and need updating separately.
Coverage: blind ✓ · edge-case ✓ · verification-gap ✓ · acceptance ✓ (4/4 lenses ran)

The diff is oversized (`diff_metadata.oversized`); coverage of the full ~106-file, ~18k-line change is necessarily partial — findings below are what verification reached, not an exhaustive sweep.

## Look here first

- `src/codemie/service/tool_permissions_service.py:58` — [security] a per-request `tool_call_policy` fully replaces a stricter assistant-configured policy instead of only raising it — confirmation-bypass for a mutating tool call — CR-010
- `src/codemie/agents/tool_confirmation/tool_call_confirmation_mixin.py:62` — [security] Allow on a multi-tool-call turn executes every tool call in the batch, even ones never shown in the confirmation dialog — CR-006
- `src/codemie/rest_api/routers/assistant.py:1125` — [security] tool-call resume never checks that `conversation_id` belongs to `assistant_id`, allowing cross-assistant permission/config confusion on resume — CR-009
- `src/codemie/service/tool_permissions_service.py:63` — [security] customer floor policy setting is a raw string, not the enum `stricter()` expects — `AttributeError` on every request once a customer configures it — CR-011
- `src/codemie/agents/langgraph_agent.py:735` — [other] concurrency — a new chat message on the same conversation silently wipes a genuinely pending tool-call confirmation — CR-003

## Also flagged

- `src/codemie/agents/langgraph_agent.py:902` — [other] unbounded recursion on consecutive APPROVE_FOR_ME auto-resumes can hit `RecursionError` — CR-004
- `src/codemie/agents/tool_confirmation/tool_call_confirmation_mixin.py:143` — [other] `reject_tool_call(None)` under a resume race leaves the client's stream open indefinitely — CR-007
- `src/codemie/rest_api/models/conversation.py:276` — [other] `Conversation.tool_call_policy` contradicts the design's explicit "no conversation-entity changes" scope — needs a decision — CR-008
- `Makefile:71` — [infra] `make run` no longer starts the app (bare `poetry run uvicorn`, no app/host/port) — CR-001
- `config/customer/customer-config.yaml` — [config] unrelated `teamsBotIntegration.enabled` flip to `false`, unexplained — CR-002

plus 2 more — see code-review-final.json

## Checked and clean

security ✓ (sampled is_safe()/HTTP-method classification fails closed across jira/gitlab/github/azure_devops/keycloak/servicenow/telegram/sonar/zephyr) · migration-integrity ✓ (this MR's 2 new migrations form a clean single-line, symmetric chain)
commit-format ✗ blocking (CR-012) · code-quality ? unverified in new modules (Optional[X] vs X | None, CR-005)

Note: the review may be incomplete — the diff is flagged oversized, so parts of this ~106-file change were not individually re-verified against the real code beyond the sampled paths above.
