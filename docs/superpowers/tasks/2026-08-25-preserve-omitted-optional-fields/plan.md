# EPMCDME-14150: Preserve Omitted Optional Fields Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix assistant partial update so omitted None-default optional fields are preserved instead of nulled.

**Architecture:** Correct the `_should_update_field` helper on the assistant domain model to update a field only when it is explicitly present in the request's set of provided fields (or in legacy full-update mode), and read that set from the Pydantic v2 accessor `model_fields_set`. Existing unit tests that encode the buggy contract are rewritten; a regression test locks in partial-update preservation.

**Tech Stack:** Python, Pydantic v2.9.2, pytest.

**Spec:** docs/superpowers/tasks/2026-08-25-preserve-omitted-optional-fields/spec.md

## Global Constraints

- No schema, migration, or configuration changes.
- Do not modify `_should_update_system_prompt` logic.
- Commit messages use the format `EPMCDME-14150: <description>`.
- Keep the harmless `fields_set is None` legacy guard.

---

### Task 1: Rewrite unit tests to assert the corrected partial-update contract

**Files:**
- Modify: `tests/codemie/rest_api/models/test_map_assistant_request_helpers.py`

**Test-first: yes — rewrite TC-1.4 and TC-1.5 so they assert an omitted None-valued field (not in fields_set) is NOT updated; these fail against current buggy code.**

- [ ] **Step 1: Write the failing tests**

  Replace `test_should_update_when_value_is_none` (TC-1.4, lines 53-61) and `test_empty_string_vs_none` (TC-1.5, lines 63-71) with these corrected versions:

  ```python
  def test_should_not_update_none_value_when_not_in_fields_set(self):
      """TC-1.4: A None value for a field NOT in fields_set must NOT be updated (partial-patch)."""
      assistant = AssistantBase(name="Test", description="Test", system_prompt="Test", project="demo")
      fields_set = {'description'}  # custom_metadata / temperature omitted

      # Omitted None-default fields are preserved, not written back as None
      assert assistant._should_update_field('custom_metadata', None, fields_set) is False
      assert assistant._should_update_field('temperature', None, fields_set) is False

      # Explicitly-set-to-None field is still cleared
      assert assistant._should_update_field('temperature', None, {'temperature'}) is True

  def test_empty_string_vs_none(self):
      """TC-1.5: Edge case - Empty string and None both follow the fields_set contract."""
      assistant = AssistantBase(name="Test", description="Test", system_prompt="Test", project="demo")
      fields_set = {'description'}  # system_prompt omitted

      assert assistant._should_update_field('system_prompt', '', fields_set) is False
      assert assistant._should_update_field('system_prompt', None, fields_set) is False
  ```

- [ ] **Step 2: Run to verify they fail**

  Run: `poetry run pytest tests/codemie/rest_api/models/test_map_assistant_request_helpers.py -v`
  Expected: the two rewritten tests FAIL (current code returns True for None values).

- [ ] **Step 3: No implementation yet**

  Implementation happens in Task 2; proceed there to make these pass.

### Task 2: Fix _should_update_field and migrate to model_fields_set

**Files:**
- Modify: `src/codemie/rest_api/models/assistant.py:869`
- Modify: `src/codemie/rest_api/models/assistant.py:896`

**Test-first: no — makes the Task 1 tests pass (fix step of the same red/green cycle).**

- [ ] **Step 1: Fix the helper**

  In `_should_update_field`, remove the `or field_value is None` clause:

  ```python
  @staticmethod
  def _should_update_field(field: str, field_value: Any, fields_set: set | None) -> bool:
      """Check if a field should be updated based on whether it was explicitly set."""
      return fields_set is None or field in fields_set
  ```

- [ ] **Step 2: Migrate accessor**

  In `_map_assistant_request`, replace the deprecated alias read:

  ```python
  # Type-safe field mapping using reflection
  fields_set = request.model_fields_set
  ```

- [ ] **Step 3: Run to verify green**

  Run: `poetry run pytest tests/codemie/rest_api/models/test_map_assistant_request_helpers.py -v`
  Expected: all tests PASS.

- [ ] **Step 4: Commit**

  ```bash
  git add src/codemie/rest_api/models/assistant.py tests/codemie/rest_api/models/test_map_assistant_request_helpers.py
  git commit -m "EPMCDME-14150: Preserve omitted optional fields in assistant partial update"
  ```

### Task 3: Add end-to-end regression test for _map_assistant_request

**Files:**
- Modify: `tests/codemie/rest_api/models/test_map_assistant_request_helpers.py`

**Test-first: yes — new regression test proving a partial update preserves populated description and system_prompt while explicit changes and explicit nulls still apply.**

- [ ] **Step 1: Write the regression test**

  Append this test class:

  ```python
  class TestMapAssistantRequestPartialUpdate:
      """Regression coverage for EPMCDME-14150 partial-update semantics."""

      def test_partial_update_preserves_omitted_optional_fields(self):
          """Omitting description and system_prompt must keep the stored values."""
          assistant = AssistantBase(
              name="Test", description="Stored description",
              system_prompt="Stored prompt", project="demo",
          )
          request = AssistantRequest(name="Test")  # only name provided

          assistant._map_assistant_request(request)

          assert assistant.description == "Stored description"
          assert assistant.system_prompt == "Stored prompt"

      def test_explicit_value_is_applied(self):
          """A field explicitly set in the request is applied."""
          assistant = AssistantBase(
              name="Test", description="Stored description", project="demo",
          )
          request = AssistantRequest(name="Test", description="New description")

          assistant._map_assistant_request(request)

          assert assistant.description == "New description"

      def test_explicit_null_clears_field(self):
          """A field explicitly set to None in the request is cleared."""
          assistant = AssistantBase(
              name="Test", description="Stored description", project="demo",
          )
          request = AssistantRequest(name="Test", description=None)

          assistant._map_assistant_request(request)

          assert assistant.description is None
  ```

- [ ] **Step 2: Run to verify**

  Run: `poetry run pytest tests/codemie/rest_api/models/test_map_assistant_request_helpers.py::TestMapAssistantRequestPartialUpdate -v`
  Expected: all three PASS (proves the fix; `test_explicit_null_clears_field` confirms explicit clears still work).

- [ ] **Step 3: Commit**

  ```bash
  git add tests/codemie/rest_api/models/test_map_assistant_request_helpers.py
  git commit -m "EPMCDME-14150: Add regression test for assistant partial-update field preservation"
  ```

### Task 4: RED tests for the versioning path (CR-001, scope added during review)

**Files:**
- Modify: `tests/codemie/service/assistant/test_assistant_version_compare_service.py`
- Modify: `tests/codemie/service/assistant/test_assistant_version_service_critical.py`

**Test-first: yes — a partial update that omits versioned fields must not create a spurious version, and the version snapshot must preserve omitted fields; both fail against current code.**

- [ ] **Step 1: Add compare-service test** — `has_configuration_changes` returns `False` for a partial update that omits versioned fields (only required fields sent unchanged), and `True` when a set field actually changes.
- [ ] **Step 2: Add version-service test** — `create_new_version` snapshots omitted versioned fields (`temperature`, `top_p`, `custom_metadata`, `conversation_starters`) from the merged assistant, and applies explicitly-set fields.
- [ ] **Step 3: Run to verify they fail** — `poetry run pytest tests/codemie/service/assistant/test_assistant_version_compare_service.py tests/codemie/service/assistant/test_assistant_version_service_critical.py -v`.

### Task 5: Fix the versioning path (CR-001)

**Files:**
- Modify: `src/codemie/service/assistant/assistant_version_compare_service.py` (`has_configuration_changes`)
- Modify: `src/codemie/service/assistant/assistant_version_service.py` (`create_new_version`)

**Test-first: no — makes the Task 4 tests pass.**

- [ ] **Step 1: has_configuration_changes** — overlay only `request.model_fields_set` fields onto `_prepare_for_comparison(current_config)`; omitted fields keep the current value; clean overlaid toolkits to match. Diff current vs overlaid.
- [ ] **Step 2: create_new_version** — snapshot each versioned field via `getattr(request, name) if name in request.model_fields_set else getattr(assistant, name)`.
- [ ] **Step 3: Run affected suites green** — the two service suites plus `test_assistant_version_service.py`, `test_assistant_version_compare_metadata.py`, `test_map_assistant_request_helpers.py`, and `test_assistant.py`; `ruff check` clean.
- [ ] **Step 4: Commit**

  ```bash
  git add src/codemie/service/assistant/assistant_version_service.py src/codemie/service/assistant/assistant_version_compare_service.py tests/codemie/service/assistant/test_assistant_version_service_critical.py tests/codemie/service/assistant/test_assistant_version_compare_service.py
  git commit -m "EPMCDME-14150: Extend partial-update contract to assistant versioning path (CR-001)"
  ```
