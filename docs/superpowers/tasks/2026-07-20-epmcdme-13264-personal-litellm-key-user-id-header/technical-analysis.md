# Technical Research

**Task**: litellm personal-key user-id header budget-attribution
**Generated**: 2026-07-20
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-13264: Personal LiteLLM key requests incorrectly send user_id header and cause wrong budget assignment.

When a user has a personal LiteLLM key configured, requests that use this key still send the user_id header. As a result, budget usage is assigned incorrectly. For personal LiteLLM key usage, the user_id header must be empty or not sent at all, so that budget attribution remains correct and does not mix personal key traffic with user-based budget assignment.

Steps To Reproduce:
1. Configure a personal LiteLLM key for a user.
2. Trigger a request that uses the personal LiteLLM key.
3. Inspect the outgoing request headers sent to LiteLLM or review the resulting budget assignment.
4. Observe that the user_id header is still sent.
5. Check the budget attribution result.

Expected result:
- When a personal LiteLLM key is used, the user_id header is empty or not sent.
- Budget assignment is performed correctly for the personal LiteLLM key scenario.
- No incorrect user-based budget attribution occurs.

Acceptance criteria:
- Requests using a personal LiteLLM key do not send a populated user_id header.
- Budget attribution for personal LiteLLM key requests is correct.
- Existing flows that rely on user_id for non-personal key scenarios continue to work as expected.
- Regression coverage confirms correct behavior for both personal and non-personal LiteLLM key usage.

---

## 2. Codebase Findings

### Terminology clarification (important for scoping)

There is no literal `user_id` HTTP header anywhere in this repo's LiteLLM integration. The ticket's "user_id header" maps to two distinct mechanisms, one per code path:

- **Direct LangChain SDK path**: the OpenAI-compatible `user` field passed as `model_kwargs["user"]` to `AzureChatOpenAI`, which LiteLLM treats as the caller's `user_id` for budget attribution.
- **HTTP proxy-forwarding path**: a `user` field injected directly into the outgoing JSON request body (`inject_user_into_body`).

A separate, unrelated header `x-litellm-customer-id` (`LITELLM_CUSTOMER_ID_HEADER`) also exists and is set only in non-personal (budget-tracked) branches — do not confuse this with the `user`/`user_id` field the ticket describes. There is also an unrelated `USER_ID_HEADER = "user-id"` used purely for local/dev authentication identity (`src/codemie/rest_api/security/user.py`) — not part of this domain.

### Existing Implementations

Two parallel implementations of "should we inject a user/budget identity" exist and must be reconciled:

- `src/codemie/enterprise/litellm/llm_factory.py` — direct (non-proxy) LangChain model construction.
  - `_configure_direct_runtime_overrides` (L423-506) branches on `creds` (personal key present via `litellm_context.credentials`):
    - Personal key present → `RuntimeBudgetMode.USER_CREDENTIALS_BYPASS` (L433-451). **This branch still conditionally sets `model_kwargs["user"]` if a project-runtime override user is separately resolved** ("to inject end_user for override-customer spending tracking" per inline comment) — this is the most likely root-cause location for the bug.
    - No personal key → resolves project/global budget, sets `request_params["model_kwargs"] = {"user": user_email}` (L505).
  - `_resolve_direct_project_budget_runtime` (L678-770) is called from *both* branches above and **hardcodes `has_user_litellm_credentials=False`** at L746 when calling `select_runtime_budget_mode`, regardless of whether a personal key is actually present — meaning project-member-tracking override logic can inject a `user` value even during personal-key bypass mode. Needs verification: is this intentional (deliberate override-tracking) or itself the defect?
- `src/codemie/enterprise/litellm/proxy_router.py` — HTTP/CLI proxy-forwarding path.
  - `_create_body_stream_with_optional_injection` (L595-709): when `user_credentials.is_personal` (L618) is true, returns `_resolve_bypass_mode_stream` — pure passthrough, **no `user` injected at all**. Otherwise resolves budget category/username and calls `_inject_user_into_request_body_from_bytes` (L351-379).
  - `_apply_budget_provider_headers` (L204-217) only sets `x-litellm-customer-id` when set in the non-personal branches.
  - This path already appears to correctly bypass user injection for personal keys, and has existing regression coverage (`test_bypass_mode_with_personal_credentials_passes_through_body`).
- `src/codemie/enterprise/litellm/credentials.py` — `ResolvedLiteLLMUserCredentials` dataclass (`is_personal: bool = True`, L28-33); `resolve_litellm_user_credentials` / `_resolve_litellm_user_credentials_uncached` (L58-168) determine whether a user has a personal key vs. project-scoped/premium key (explicit `is_personal=False` for premium project fallback, L195). Guards against project-scoped keys being misclassified as personal.
- `src/codemie/service/llm_service/utils.py` — `set_llm_context` (L59-99) populates `LiteLLMContext.credentials` (consumed by `llm_factory.py`'s `creds` branch); explicitly nulls personal creds for project-scoped settings and shared assets under `LLM_PROXY_SHARED_ASSET_PROJECT_BUDGET_ROUTING_ENABLED` (L80-89) to avoid leaking personal-key bypass into non-personal contexts.
- `src/codemie/enterprise/litellm/dependencies.py` — `check_user_budget` (L327+), only called in the non-personal branch.
- `src/codemie/enterprise/litellm/runtime_budget_selection.py` — `RuntimeBudgetMode` enum (`USER_CREDENTIALS_BYPASS`, `PROJECT_BUDGET_WITH_MEMBER_TRACKING`, `PROJECT_BUDGET_PROJECT_ONLY`, `GLOBAL_OR_PERSONAL_BUDGET`) and `select_runtime_budget_mode` (L36-65) — pure decision function, but a caller (`llm_factory.py` L746) passes a hardcoded input rather than the real personal-key flag.
- `src/codemie/enterprise/litellm/budget_categories.py` — `build_user_id()` / `derive_category_from_user_id()` — canonical `user_id` construction for non-personal (budget-tracked) flows.
- `src/codemie/enterprise/litellm/budget_provider_adapter.py` — `dispatch_runtime` / `body_overrides={"user": provider_member_ref}` (L1351) — project-member-tracking override user construction consumed by both code paths.
- `src/codemie/enterprise/litellm/project_member_runtime_sync.py` — syncs LiteLLM project member records prior to runtime resolution.
- `src/codemie/enterprise/loader.py` (L103-117) — imports `inject_user_into_body` / `parse_usage_from_response` from the external `codemie_enterprise` private wheel; the actual body-mutation implementation for the proxy path is **not present in this checked-out repo**.

### Architecture and Layers Affected

- REST API / proxy router layer — `proxy_router.py` (FastAPI endpoints forwarding to LiteLLM)
- Enterprise LLM integration layer — `llm_factory.py`, `credentials.py`, `dependencies.py`, `client.py` (builds LangChain `AzureChatOpenAI` clients pointed at LiteLLM proxy)
- Budget/business-logic services — `service/budget/budget_service.py`, `budget_resolution_service.py`, `runtime_budget_selection.py`, `budget_provider_adapter.py`
- Settings/credentials repository access — `service/settings/settings.py` (`SettingsService.get_litellm_creds`), `service/llm_service/utils.py`
- External enterprise package boundary — `enterprise/loader.py` → `codemie_enterprise` private wheel (out-of-repo code that implements `inject_user_into_body` for the proxy path)

### Integration Points

- `codemie.enterprise.litellm.proxy_router` → `codemie.enterprise.litellm.{client,credentials,dependencies,llm_factory,project_member_runtime_sync,runtime_budget_selection}`
- `codemie.enterprise.litellm.proxy_router` → `codemie.service.budget.{budget_service,budget_resolution_service}`
- `codemie.enterprise.litellm.llm_factory` → `codemie.enterprise.litellm.{dependencies,budget_categories,runtime_budget_selection}` and `codemie.service.budget.{budget_service,budget_resolution_service,budget_enums}`
- `codemie.service.llm_service.utils` → `codemie.service.settings.settings.SettingsService`, `codemie.core.dependecies.set_litellm_context` (populates `LiteLLMContext.credentials` consumed by `llm_factory`)
- `codemie.enterprise.litellm.*` → `codemie.enterprise.loader` → external `codemie_enterprise` package
- `codemie.enterprise.litellm.credentials` → `codemie.service.settings.settings.SettingsService`, `codemie_tools.base.models.CredentialTypes`
- External: `httpx` client to real LiteLLM proxy (`client.py`); `langchain_openai.AzureChatOpenAI` as OpenAI-compatible client talking to LiteLLM.

### Patterns and Conventions

- "Resolved credentials" dataclass with an explicit `is_personal` flag decided once at resolution time, then branched on downstream (`ResolvedLiteLLMUserCredentials`).
- Dual parallel implementations of the same "inject user/budget identity or not" decision — one for direct LangChain path, one for HTTP-proxy-forwarding path — must be kept in sync; this task appears to be exactly the case where they have drifted.
- Structured `budget_event=... component=... user_id=... username=...` log-line convention used consistently across `llm_factory.py`, `project_member_runtime_sync.py`, and `proxy_router.py` — preserve this convention in any fix.
- `RuntimeBudgetMode` / `select_runtime_budget_mode` as a small pure decision function, but at least one caller hardcodes an input flag rather than passing the real personal-key state through.
- Body/user injection for the proxy path is abstracted to an external enterprise package via a loader/feature-flag pattern (`HAS_LITELLM`).
- Prior related tickets establish an applicable convention: EPMCDME-12960 (`docs/superpowers/tasks/2026-06-23-fix-budget-override-litellm-sync/spec.md`) established the rule "always pass explicit values into LiteLLM sync calls rather than letting adapters fall back to stale/incorrect defaults" — directly analogous to the `has_user_litellm_credentials=False` hardcoding found in `llm_factory.py:746`.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/integration/llm-providers.md` — covers LiteLLM as an enterprise provider, `is_litellm_enabled` gating, and a header-hygiene rule for tagging headers ("Injecting user-identifying information | Tagging headers carry version and project only", line 45). This is about the `X-CodeMie-Version`/`X-CodeMie-Project` tagging headers, not the `user`/`user_id` body field used on the LiteLLM budget-attribution path — related in spirit but does not directly document this ticket's mechanism.
- No guide directly documents the `user`/personal-key interaction or the dual-path (`llm_factory` vs `proxy_router`) architecture.

### Architectural Decisions

- `docs/superpowers/tasks/2026-06-23-fix-budget-override-litellm-sync/spec.md` (EPMCDME-12960) — established: `provider.sync_member_allocation` must receive an explicit `effective_max_budget` rather than relying on stale allocation values, to avoid wrong LiteLLM budget enforcement. Same class of defect as this ticket (implicit/stale values leaking into LiteLLM calls).
- `docs/superpowers/tasks/2026-06-22-epmcdme-12959-budget-stale-allocation/technical-analysis.md` — documents the canonical effective-budget resolution formula (`allocated_max_budget if enforce_limit else budget.max_budget`) used in `project_member_runtime_sync.py`.
- An SDLC task folder already exists at this exact run_dir (`docs/superpowers/tasks/2026-07-20-epmcdme-13264-personal-litellm-key-user-id-header/.state.json`, branch `EPMCDME-13264_personal-litellm-key-user-id-header`, flow `sdlc-standard`) but contained only the state file prior to this analysis — no spec/plan had been written yet.

### Derived Conventions

- Personal LiteLLM key = "bypass mode" (`RuntimeBudgetMode.USER_CREDENTIALS_BYPASS`), detected via `user_credentials.is_personal` (`proxy_router.py:618`) and `creds` truthy in `llm_factory.py:433` (`_configure_direct_runtime_overrides`).
- The proxy-forwarding path (`proxy_router.py`) already excludes `user` injection for personal-key bypass; the direct-SDK path (`llm_factory.py`) does not fully follow the same exclusion — this asymmetry is the most probable root cause.

No TODO/HACK/DECISION markers were found near the litellm/budget/user_id code.

---

## 4. Testing Landscape

### Existing Coverage

All relevant tests live under `tests/enterprise/litellm/`:

- `test_llm_factory.py` (663 lines) — `TestCreateLiteLLMChatModel::test_skips_budget_check_when_has_credentials` (L135-172) asserts `check_user_budget` is NOT called when personal credentials are present, but **does not assert `model_kwargs`/`user` is absent** — this is exactly the gap this ticket needs to close. `TestResolveDirectProjectBudgetRuntime` covers budget-category/provider-override branches but does not exercise the bypass sub-case where `_configure_direct_runtime_overrides` still injects `user` via a project-runtime override.
- `test_credentials.py` (623 lines) — covers `is_personal` differentiation and credential resolution/caching, but nothing downstream about resulting headers/body fields.
- `test_litellm_dependencies.py` (600 lines) — covers `check_user_budget`, budget-category derivation, `user_id`-construction helpers — not the personal-key suppression path.
- `test_proxy_router.py` — **already has** `test_bypass_mode_with_personal_credentials_passes_through_body` (L1264-1298), asserting body-injection is skipped for `is_personal` credentials on the HTTP-proxy path. This is a good existing pattern to mirror for `llm_factory.py`.
- `test_client.py`, `test_budget_categories.py`, `test_budget_helpers.py`, `test_budget_provider_adapter.py`, `test_project_member_runtime_sync.py`, `test_models.py`, `test_embedding_wrapper.py`, `test_premium_models_budget.py` — round out budget/runtime-sync coverage but do not target the personal-key/bypass branch of `create_litellm_chat_model`.
- `tests/codemie/service/llm_service/test_utils_litellm_context.py` — tests `set_llm_context` credential resolution but only asserts DB/service calls, not resulting header/body behavior.
- **No test file exists at all** for `runtime_budget_selection.py` (`select_runtime_budget_mode`, `RuntimeBudgetMode`) — confirmed zero matches via grep.

### Testing Framework and Patterns

- pytest `^8.3.1`, `pytest-asyncio ^0.23.7`, `pytest-cov ^5.0.0`, `pytest-env ^1.1.3`, `pytest-mock ^3.14.0`, `pytest-httpx ^0.35.0` (declared but underused for this domain — request/response-level assertions are not currently used here).
- Class-per-function grouping, method-per-scenario naming (`test_<condition>_<expected_behavior>`).
- Heavy `unittest.mock.patch`/`patch.object` stacks over `codemie.configs.config` and external SDK classes (`AzureChatOpenAI`).
- `MagicMock()` fixtures for `llm_model_details` rather than a shared factory.
- `tests/conftest.py` (global) and `tests/enterprise/conftest.py` (enterprise-specific, `mock_enterprise_installed`/`mock_enterprise_not_installed` via `monkeypatch`).
- Assertions favor `assert_called_once_with(...)`/`assert_not_called()` on collaborator calls rather than inspecting final constructed kwargs — this is precisely why the `model_kwargs["user"]` gap for personal keys currently exists untested.
- No BDD-style conventions; plain pytest/unittest throughout.

### Coverage Gaps

- No test asserts `create_litellm_chat_model`'s `model_kwargs` excludes/empties `"user"` when personal credentials are present and no project-member override applies (`llm_factory.py:433-451`).
- No test covers the interaction between personal-key bypass mode and `_resolve_direct_project_budget_runtime` returning a non-empty `project_runtime_user` — the exact edge case suspected as the defect.
- Zero direct coverage of `runtime_budget_selection.py`.
- No regression test comparing personal-key vs. non-personal-key request payload/header contents side-by-side, despite this being explicitly required by the acceptance criteria.
- `create_litellm_embedding_model` shares the same `_configure_direct_runtime_overrides` call path and has the same untested gap for embeddings.
- All current tests mock at the `AzureChatOpenAI`/config layer rather than verifying real outgoing request body/headers, even though `pytest-httpx` is already a declared dependency and unused for this flow.

---

## 5. Configuration and Environment

### Environment Variables

- `LLM_PROXY_ENABLED` / `LLM_PROXY_MODE` — turns on the LiteLLM proxy integration overall.
- `LLM_PROXY_BUDGET_CHECK_ENABLED` — enables budget checking (bypassed for personal-key requests).
- `LITE_LLM_URL`, `LITE_LLM_APP_KEY`, `LITE_LLM_PROXY_APP_KEY`, `LITE_LLM_MASTER_KEY` — proxy base URL and platform-level keys (used when no personal key is present).
- `LITELLM_USER_CREDENTIALS_CACHE_TTL` (600s default) — TTL for cached personal-credential resolution; relevant to test/regression design since a stale cache entry could mask a fix during manual verification.
- `LLM_PROXY_SHARED_ASSET_PROJECT_BUDGET_ROUTING_ENABLED` — forces project (non-personal) budget attribution even when a user has a personal key, for shared assets.
- `LITELLM_PREMIUM_MODELS_ALIASES` — affects whether a project premium key (non-personal) is substituted instead of a personal key.
- No dedicated `PERSONAL_KEY`/`USER_ID`/`BUDGET_*` env vars beyond the above; budget IDs are resolved from DB tables, not env vars.

### Configuration Files

- `src/codemie/configs/config.py` (~L585-745) — central LiteLLM proxy settings: base URL, master key, budget-check/reconciliation toggles, premium model aliases, credential cache TTL.

### Feature Flags and Deployment Concerns

- `is_personal` (dataclass field, not an env flag) is the runtime switch that governs whether `user` should be omitted — enforced independently in two places (`llm_factory.py` and `proxy_router.py`) that currently do not agree.
- `RuntimeBudgetMode.USER_CREDENTIALS_BYPASS` vs. other modes (`runtime_budget_selection.py`) — enum whose selection determines injection behavior.
- No deploy-templates or CI/CD manifests reference LiteLLM/budget env vars directly — configuration is entirely runtime/DB-driven.
- Personal LiteLLM keys are stored per-user as encrypted `Settings.credential_values` rows (`CredentialTypes.LITE_LLM`, scoped by `user_id`/`project_name`/`alias`), encrypted via a pluggable `EncryptionFactory` (`src/codemie/service/settings/base_settings.py`, `src/codemie/service/encryption/`). No plaintext env-var storage of personal keys — not a deployment-manifest concern.

---

## 6. Risk Indicators

- Two independent implementations of "should we inject a user identity" (`llm_factory.py` direct path vs. `proxy_router.py` HTTP-proxy path) have drifted: the proxy path already correctly bypasses injection for personal keys, but the direct-SDK path can still inject `model_kwargs["user"]` via a project-runtime override even in personal-key bypass mode. A fix must reconcile both paths, not just one, or the bug will persist for whichever path is not touched.
- `_resolve_direct_project_budget_runtime` (`llm_factory.py:746`) hardcodes `has_user_litellm_credentials=False` regardless of actual personal-key state when calling `select_runtime_budget_mode` — needs explicit product/engineering confirmation on whether project-member override tracking is intended to still apply for personal-key users, since this directly conflicts with the acceptance criteria as read literally.
- No existing test asserts the absence of `model_kwargs["user"]` for the direct-SDK personal-key path — `test_skips_budget_check_when_has_credentials` only checks that budget-check is skipped, not the actual request payload, which is exactly why this defect shipped undetected.
- Zero test coverage exists for `runtime_budget_selection.select_runtime_budget_mode` / `RuntimeBudgetMode` directly, despite it being the central decision function this fix will likely touch.
- The proxy-forwarding path's body-mutation implementation (`inject_user_into_body`) lives in the external `codemie_enterprise` private wheel, not present in this checked-out repo — if the fix requires changes there, it cannot be made or verified from within this repository alone.
- Terminology mismatch risk: the ticket says "user_id header" but the actual mechanism is a body/kwarg field (`user`) on one path and a body-injected field on the other, plus an unrelated real header (`x-litellm-customer-id`). The implementer must scope the fix to the correct mechanism(s) and should not modify the unrelated `x-litellm-customer-id` header or the unrelated authentication `user-id` header.
- `LITELLM_USER_CREDENTIALS_CACHE_TTL` (600s) caching of resolved credentials could mask verification during manual/local reproduction if not accounted for in test setup.
- No guide in `.ai-run/guides/` documents this specific `user`/personal-key mechanism — conventions had to be derived entirely from code and adjacent ticket history (EPMCDME-12960, EPMCDME-12959).

---

## 7. Summary for Complexity Assessment

This task touches three architectural layers: the enterprise LLM integration layer (`llm_factory.py`, `credentials.py`, `runtime_budget_selection.py`), the REST proxy-router layer (`proxy_router.py`), and budget business-logic services (`budget_provider_adapter.py`, `project_member_runtime_sync.py`) that both integration paths call into. The likely file-change surface is small but precise: the primary fix is almost certainly confined to `src/codemie/enterprise/litellm/llm_factory.py` (`_configure_direct_runtime_overrides` and `_resolve_direct_project_budget_runtime`), with a possible secondary confirmation/no-op change in `proxy_router.py` (which already appears correct) and `runtime_budget_selection.py` (where a hardcoded input flag may need to become a real parameter). No database migrations, new models, or new endpoints are implicated.

Technical novelty is low — this follows an established pattern already used correctly on the sibling proxy-router path (`is_personal` branching to skip injection), and mirrors a previously-resolved defect class (EPMCDME-12960's "always pass explicit values, don't rely on stale/hardcoded defaults into LiteLLM calls"). The main complexity driver is not new pattern design but reconciling two independently-evolved implementations of the same decision and confirming the intended behavior of the project-member override sub-case, which may require product clarification since the current code's comment suggests the override-injection was deliberate.

Test coverage posture is mixed-to-weak specifically for this defect: the codebase has strong test scaffolding and existing conventions for this domain (extensive `tests/enterprise/litellm/` suite, an existing correct-behavior test on the proxy path to mirror), but zero assertions currently exist on the actual `model_kwargs`/`user` payload for the direct-SDK path, and zero coverage exists for the `runtime_budget_selection` decision function itself. Regression coverage per the acceptance criteria (side-by-side personal vs. non-personal assertions) does not yet exist and must be added new, following the established `test_bypass_mode_with_personal_credentials_passes_through_body` pattern from `test_proxy_router.py`. Overall this is a low-to-medium complexity, well-scoped bugfix with a moderate-risk ambiguity around one specific override sub-case that should be flagged for clarification before implementation.
