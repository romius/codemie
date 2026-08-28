# Technical Research

**Task**: assistant update partial-patch fields_set rest_api model-mapping
**Generated**: 2026-08-25
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-14150 (Bug, Major) — "Assistant partial update silently nulls omitted optional fields with None defaults"

### Summary
Assistant partial update silently nulls omitted optional fields with None defaults.

### Description
`_should_update_field` treats omitted fields whose default is None as fields to update, wiping stored values despite the documented partial-patch contract.

### Preconditions
- Assistant has optional fields populated, such as description or system prompt.
- Client performs a partial update that omits some optional fields.

### Steps to Reproduce
1. Create or use an assistant with populated optional fields such as description and system_prompt.
2. Send a PUT partial body containing only required/intended fields.
3. Omit description and/or system_prompt.
4. Fetch the assistant again.

### Expected Result
Omitted fields remain unchanged.

### Actual Result
Optional fields are written as None / cleared.

### Affected Areas
- Assistant update API
- Assistant model mapping
- Partial update semantics

### Acceptance Criteria
- `_should_update_field` updates only explicitly set fields unless full update is intended.
- Omitted None-default fields are preserved.
- Regression test covers partial update preserving description and system_prompt.
- API behavior matches model docstring.

Known starting points (verify and expand): the helper lives at src/codemie/rest_api/models/assistant.py:867 (_should_update_field), :872 (_get_field_value_for_update), :878 (_should_update_system_prompt), :884 (_map_assistant_request). Existing tests at tests/codemie/rest_api/models/test_map_assistant_request_helpers.py encode some of the current behavior. Trace the full PUT/update assistant call path from the REST router down to _map_assistant_request, identify who passes fields_set / __fields_set__, and note where legacy "full update" (fields_set is None) callers exist.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/models/assistant.py` — **bug locus**. Contains the `AssistantRequest(BaseModel)` request model (class at :309, docstring :310-314), the `Assistant.update_assistant` mapping entry point (:1102), and the four helper methods under scrutiny:
  - `_should_update_field` (:866-869) — the root-cause helper.
  - `_get_field_value_for_update` (:872-876) — value coercion (None → `[]` for `prompt_variables`/`categories`).
  - `_should_update_system_prompt` (:878-882) — separate gate for `system_prompt`.
  - `_map_assistant_request` (:885-923) — reflection-driven mapping loop; reads `fields_set = getattr(request, '__fields_set__', None)` at :896.
- `src/codemie/rest_api/routers/assistant.py` — FastAPI `PUT /assistants/{assistant_id}` endpoint `update_assistant` (:828-835), calls `repository.update(assistant, request, user)` (:885).
- `src/codemie/service/assistant/assistant_repository.py` — `AssistantRepository.update` (:226-239), thin pass-through delegating to `assistant.update_assistant(assistant_request, user)`.

**Verbatim root-cause logic** (`assistant.py:866-882`):

```python
@staticmethod
def _should_update_field(field: str, field_value: Any, fields_set: set | None) -> bool:
    """Check if a field should be updated based on whether it was explicitly set."""
    return fields_set is None or field in fields_set or field_value is None

@staticmethod
def _get_field_value_for_update(field: str, field_value: Any) -> Any:
    """Get the appropriate value for a field update, handling special cases."""
    if field in ('prompt_variables', 'categories') and field_value is None:
        return []
    return field_value

def _should_update_system_prompt(self, request: AssistantRequest, fields_set: set | None) -> bool:
    """Determine if system_prompt should be updated."""
    if self.system_prompt == request.system_prompt:
        return False
    return fields_set is None or 'system_prompt' in fields_set
```

The mapping loop (`assistant.py:914-918`):

```python
for field in updatable_fields:
    field_value = getattr(request, field)
    if self._should_update_field(field, field_value, fields_set):
        value_to_set = self._get_field_value_for_update(field, field_value)
        setattr(self, field, value_to_set)
```

**Root cause**: the `or field_value is None` disjunct in `_should_update_field` makes any field whose current request value is `None` (because it was omitted and has a `None` default) satisfy the update condition even when `field not in fields_set`. So omitted optional fields with `None` defaults get written back as `None`, clearing stored values — directly contradicting the model docstring's partial-patch contract.

**Secondary concern**: `_map_assistant_request` reads `getattr(request, '__fields_set__', None)`. `__fields_set__` is Pydantic v1's deprecated alias; the canonical Pydantic v2 attribute is `model_fields_set`. This file is the codebase outlier — `models/index.py:122`, `routers/index.py`, `service/assistant_service.py:95-104`, and `service/mcp_config_service.py:399-403` all use `model_fields_set` (often via `model_dump(exclude_unset=True)`). The `getattr(..., None)` fallback also silently enables the "legacy full-update" (`fields_set is None`) branch if the attribute ever fails to resolve, which would null every omitted field.

**Note**: `system_prompt` is handled separately via `_should_update_system_prompt`, which short-circuits to `False` on equality (`self.system_prompt == request.system_prompt`). This partially protects it from the plain-field None-nulling path, but it still carries the same `fields_set is None` legacy branch. The ticket's report of `system_prompt` being nulled is most consistent with the legacy-branch / attribute-resolution path or a case where stored and requested values differ.

### Architecture and Layers Affected

1. **REST API router** — `routers/assistant.py` (FastAPI `PUT` endpoint, request parsing). FastAPI populates `AssistantRequest.__fields_set__`/`model_fields_set` from the JSON body, so omitted fields are correctly absent from the set — the fix relies on this signal.
2. **Repository/service layer** — `service/assistant/assistant_repository.py` (`update` pass-through). No logic change needed here.
3. **Domain model / mapping** — `models/assistant.py` (`update_assistant` → `_map_assistant_request` and the three helpers). **Primary fix site.**
4. **Versioning services (side effect)** — `AssistantVersionService` and `AssistantVersionCompareService` (lazily imported in `update_assistant` at :1110-1111; `has_configuration_changes` at :1118) consume the mapped result. Behavior sensitive: fixing the null-out changes what counts as a configuration change, so version diffs must be considered.
5. **Persistence** — `self.update()` (SQLModel) at `assistant.py:1116`.

### Integration Points

- `routers/assistant.py` → `service/assistant/assistant_repository.py` → `models/assistant.py` (`Assistant.update_assistant`).
- `models/assistant.py.update_assistant` lazily imports the two version services (circular-dependency avoidance).
- **Only one production caller** of `_map_assistant_request`: `Assistant.update_assistant` (:1114). `request` is always a real `AssistantRequest` BaseModel parsed by FastAPI, so `__fields_set__`/`model_fields_set` is always a populated set containing only JSON-present fields — **never `None`** in the current router path. The `fields_set is None` "legacy full-update" branch in all three helpers is effectively dead code for production, kept alive only by the `getattr(..., None)` fallback and the existing unit tests.

### Patterns and Conventions

- `fields_set` is threaded via Pydantic's per-instance set-fields tracker, read inside `_map_assistant_request` rather than passed as an argument. Canonical codebase idiom is `request.model_fields_set` (or `model_dump(exclude_unset=True)`); `assistant.py` is the sole file using the legacy `__fields_set__` alias.
- Reflection-driven mapping: iterates `request.model_fields`, excluding required/special fields (`name`, `system_prompt`, `guardrail_assignments`, `skip_integration_validation`, `source_assistant_id`) at :900-911. `name` is always overwritten (:891-892); `system_prompt` is handled separately (:922).
- Special-case empty-list coercion for `prompt_variables`/`categories` lives correctly in `_get_field_value_for_update`, so removing the `or field_value is None` clause from `_should_update_field` does not affect that value transform.
- Documented partial-patch contract lives in the `AssistantRequest` docstring (the acceptance reference).

**Optional fields with `None` defaults that get silently nulled** (`AssistantRequest`): `description` (:317), `system_prompt` (:318), `icon_url` (:322), `llm_model_type` (:323), `image_generation_model` (:325), `is_global` (:330), `plan_prompt` (:332), `slug` (:333), `temperature` (:334), `top_p` (:335), `tools_tokens_size_limit` (:336), `hedging_config` (:340), `interactive_features` (:341), `bedrock` (:352), `bedrock_agentcore_runtime` (:353), `agent_card` (:355), `custom_metadata` (:360). `prompt_variables` and `categories` default to list/factory and are special-cased.

---

## 3. Documentation Findings

### Guides and Architecture Docs

`.ai-run/guides/` is present (39 files). Relevant guides:
- `.ai-run/guides/api/rest-api-patterns.md` — FastAPI router patterns (P0 for API tasks). No mention of "partial"/"fields_set"/"PATCH" — the partial-patch contract is not documented here.
- `.ai-run/guides/api/endpoint-conventions.md` — route/response conventions. No partial-update semantics.
- `.ai-run/guides/testing/testing-api-patterns.md` — API test conventions (relevant when writing the regression test).
- `.ai-run/guides/data/database-patterns.md` — SQLModel patterns (Assistant is a SQLModel table).

No guide encodes the partial-patch rule; the contract lives only in the model docstring.

### Architectural Decisions

- No dedicated ADR or design doc for assistant partial-update / EPMCDME-14150. `docs/` has no partial-patch design note; `CHANGELOG.md` has no assistant-update/partial entry.
- Active SDLC task scaffold exists but is empty of design content: `docs/superpowers/tasks/2026-08-25-preserve-omitted-optional-fields/.state.json` — flow `sdlc-standard`, branch `EPMCDME-14150_preserve-omitted-optional-fields`, phase `main`. Only `.state.json` present (no spec.md/plan.md yet).
- Related but distinct prior work: `docs/superpowers/specs/2026-07-08-assistant-project-mapping-design.md` (assistant project mapping) — not about partial patch.

### Derived Conventions

**The docstring the acceptance criteria targets** (`AssistantRequest`, `assistant.py:310-314`, verbatim):

```
"""
Model for creating or updating an assistant.
When updating an assistant, only fields that are explicitly set in the request will be updated.
Fields that use default values (not explicitly set) will not override existing values.
"""
```

Supporting docstrings: `_map_assistant_request` (:885-890) — "Maps values from an AssistantRequest to this assistant instance. Only updates fields that were explicitly set in the request." The implementation violates both docstrings.

Inline comment at `assistant.py:913`: `# Update fields based on whether they were explicitly set or we're in legacy mode` — documents the "legacy mode" (`fields_set is None`) branch that participates in the bug.

Other NOTE markers near the code (not directly the bug): `assistant.py:707` (PrivateAttr/SQLModel note), `assistant.py:1106` (version-record note).

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie/rest_api/models/test_map_assistant_request_helpers.py` — unit tests for the three helper methods. **Encodes the current (buggy) behavior** and will need edits:
  - `TestShouldUpdateField.test_should_update_when_value_is_none` (TC-1.4) — **ENCODES THE BUG**. Asserts an omitted None field updates even when not in `fields_set`:
    ```python
    fields_set = {'description'}  # custom_metadata not in fields_set
    assert assistant._should_update_field('custom_metadata', None, fields_set) is True
    assert assistant._should_update_field('temperature', None, fields_set) is True
    ```
  - `TestShouldUpdateField.test_empty_string_vs_none` (TC-1.5) — also locks in the None-nulling contract: asserts `_should_update_field('system_prompt', None, fields_set) is True` (the empty-string case is already correctly `False`).
  - Other cases (TC-1.1 legacy `fields_set=None`, TC-1.2 field in set, TC-1.3 not-in-set + non-None) are unaffected by the fix.
  - `TestGetFieldValueForUpdate` (TC-2.1–2.4) — value coercion; not buggy.
  - `TestShouldUpdateSystemPrompt` (TC-3.1–3.5, marked CRITICAL) — shows `system_prompt` is guarded by an equality check; the helper-level partial-preservation case (TC-3.4: changed + not in set → `False`) is covered here.
- `tests/codemie/rest_api/models/test_map_assistant_request_metadata.py` — end-to-end `_map_assistant_request` tests focused on `custom_metadata`. Notably TC-4.6/TC-4.7 **work around the bug** by re-passing existing values (`custom_metadata={'key': 'old_value'}`, `temperature=0.7`) to simulate preservation — a symptom of the missing true partial-update behavior.
- `tests/codemie/rest_api/models/test_assistant_model.py` — additional callers of `_map_assistant_request` (:350, :368).
- `tests/codemie/rest_api/routers/test_assistant.py`, `.../test_assistant_mapping.py` — router-level tests; **no PUT/update-assistant partial-patch test exists** (mapping file only covers integration-config mapping endpoints).

### Testing Framework and Patterns

- pytest `^8.3.1` with `pytest-asyncio ^0.23.7`, `pytest-cov ^5.0.0`, `pytest-env ^1.1.3`, `pytest-mock ^3.14.0`, `pytest-httpx ^0.35.0`. Python `>=3.12,<3.14`, Poetry.
- Helper/metadata test files use plain class-grouped test methods with Arrange/Act/Assert comments and TC-x.y docstring IDs. **No fixtures, no mocks, no parametrize** — they instantiate `AssistantBase(...)`/`AssistantRequest(...)` directly and call helpers in-process (pure unit tests, no DB/HTTP).
- Router tests (`test_assistant_mapping.py`) use `@pytest.fixture`, `async def` tests, and mocking — the pattern to follow if an API-level PUT test is added.

### Coverage Gaps

- **No test asserts a genuine partial update preserves an existing `description`** (request built without `description`, relying on set-fields exclusion). This is the core regression to add.
- **No `_map_assistant_request`-level test verifying description AND system_prompt are both preserved together** on partial update.
- **No integration/API-level test for the PUT assistant endpoint** — partial-patch preservation is untested through the router.
- Adding the regression test will require changing TC-1.4 and the `None → is True` assertion in TC-1.5, since those lock in the buggy `_should_update_field` contract.

---

## 5. Configuration and Environment

### Environment Variables

None relevant. No env var / `BaseSettings` field under `src/codemie/configs` affects assistant update request handling or partial-patch behavior.

### Configuration Files

None relevant. `config/` contains no toggle governing full-vs-partial assistant update semantics. `AssistantRequest` (:309) is a plain `BaseModel` with no `model_config`/`ConfigDict` for extra/validation — partial-update semantics are enforced purely in code.

### Feature Flags and Deployment Concerns

- No feature flags. There is a single update endpoint (`PUT /assistants/{assistant_id}`); no PATCH variant and no flag switching full-vs-partial update. `request.skip_integration_validation` only gates integration validation, not field-nulling.
- Deployment: none — pure logic change. No migrations, no config/env additions, no infra concerns.

---

## 6. Risk Indicators

- **Existing unit tests encode the bug** — `test_map_assistant_request_helpers.py` TC-1.4 and TC-1.5 (`None → is True`) assert the buggy contract and MUST be updated in lockstep with the fix, or the fix will fail CI. Treat these edits as part of the change, not collateral breakage.
- **Two intertwined defects in one helper** — (a) the `or field_value is None` clause in `_should_update_field`, and (b) the legacy `__fields_set__` alias read via `getattr(..., None)` at :896. The fix should both drop the None clause and migrate to `model_fields_set` to match the rest of the codebase and eliminate the dead legacy-null-everything branch.
- **`system_prompt` handled on a separate code path** — `_should_update_system_prompt` uses an equality gate plus the same `fields_set is None` legacy branch. The fix and its regression test must cover both the generic-field path (`description`) and the system_prompt path, since they do not share logic.
- **Versioning side effects** — `AssistantVersionService` / `has_configuration_changes` run after mapping; changing which fields are written alters what registers as a configuration change and what version diffs are produced. Verify version behavior is unaffected (or intentionally corrected).
- **Wide silent-nulling blast radius** — ~17 optional None-default fields are affected, not just `description`/`system_prompt`. Any existing data already corrupted by prior partial updates is out of scope but worth noting; the fix prevents future recurrence.
- **No API-level regression test exists** for the PUT endpoint — partial-patch preservation is untested through the router, so a bug this severe shipped undetected. Consider adding both a helper/mapping-level test and an endpoint-level test.
- **Contract documented only in a docstring**, not in any guide — acceptance criterion "API behavior matches model docstring" hinges on `assistant.py:310-314`. The fix must make behavior match that text exactly.
- **Bug-masking tests** — `test_map_assistant_request_metadata.py` TC-4.6/TC-4.7 currently pass by re-supplying existing values; they demonstrate the workaround, not correct partial-update semantics.

---

## 7. Summary for Complexity Assessment

This is a low-surface, well-localized bug fix concentrated in a single file, `src/codemie/rest_api/models/assistant.py`. The production change is essentially confined to the `_should_update_field` helper (remove the `or field_value is None` disjunct) and the `fields_set` read at line 896 (migrate `__fields_set__` → `model_fields_set` to match the rest of the codebase and retire the dead "legacy full-update" branch). The call path is a single clean chain — router `PUT /assistants/{assistant_id}` → `AssistantRepository.update` → `Assistant.update_assistant` → `_map_assistant_request` — with exactly one production caller and no alternate callers passing a distinct `fields_set`. Layers touched at runtime are the domain-model mapping layer only; the router and repository are pass-throughs requiring no change, and there are no config, env var, feature flag, migration, or deployment concerns.

Technical novelty is low: the fix aligns `assistant.py` with an established, already-proven convention used elsewhere (`service/assistant_service.py:95-104`, `service/mcp_config_service.py:399-403`, `models/index.py:122` all use `model_fields_set`/`exclude_unset`). No new patterns are introduced. The two genuine complications are (1) `system_prompt` travels a separate helper (`_should_update_system_prompt`) with its own equality gate, so the fix and tests must explicitly cover both the generic-field and system_prompt paths; and (2) a downstream versioning side effect (`AssistantVersionService.has_configuration_changes`) consumes the mapped result, so the change must be checked against version-diff behavior.

Test coverage posture is mixed and slightly adversarial. The helpers have dedicated unit tests, but two of them (`test_map_assistant_request_helpers.py` TC-1.4 and the `None → is True` assertion in TC-1.5) actively encode the buggy contract and must be rewritten alongside the fix, and two metadata tests mask the bug by re-supplying existing values. There is no test today that proves a genuine partial update preserves `description` and `system_prompt`, and no API-level test for the PUT endpoint at all — so the required regression test is net-new. Estimated change surface: one production file (~2 focused edits), 2-3 existing test edits, and 1-2 new regression tests (helper/mapping level, optionally endpoint level). Overall this is a small, contained fix whose main risk is coordinating the fix with the tests that currently lock in the wrong behavior.
