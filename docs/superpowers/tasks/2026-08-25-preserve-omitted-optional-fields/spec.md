# EPMCDME-14150: Assistant partial update silently nulls omitted optional fields with None defaults

> Jira: https://jiraeu.epam.com/browse/EPMCDME-14150 · Type: Bug (Major) · Flow: sdlc-standard

## Problem
On a partial `PUT` update of an assistant, optional fields omitted by the client (which have `None` defaults) are overwritten with `None`, wiping stored values. This violates the partial-patch contract.

## Root Cause
1. **Buggy logic**: `_should_update_field` in `src/codemie/rest_api/models/assistant.py` (line 869) returns `fields_set is None or field in fields_set or field_value is None`. The final `or field_value is None` clause forces any omitted `None`-valued field to be written.
2. **Deprecated accessor**: `_map_assistant_request` (line 896) reads the deprecated Pydantic v1 alias `__fields_set__` via `getattr` instead of the v2 accessor `model_fields_set` used elsewhere in the codebase. On Pydantic 2.9.2 the alias still resolves, so this is a correctness-adjacent cleanup rather than the primary defect.

## Approved Solution
1. Remove the `or field_value is None` clause from `_should_update_field`, resulting in: `return fields_set is None or field in fields_set`.
2. Migrate `fields_set = getattr(request, '__fields_set__', None)` to `fields_set = request.model_fields_set`. Retain the `fields_set is None` legacy guard as a harmless safety net.
3. Update tests in `tests/codemie/rest_api/models/test_map_assistant_request_helpers.py`:
   - Rewrite `test_should_update_when_value_is_none` to assert the corrected contract (omitted None-valued field not in `fields_set` → not updated).
   - Rewrite the `system_prompt`-`None` assertion accordingly.
   - Add a regression test proving:
     - Partial update preserves populated `description` and `system_prompt`.
     - Full/explicit update applies changes.
     - Explicit `null` still clears fields.
4. Extend the same partial-update contract to the assistant **versioning path** (scope expanded during code review — finding CR-001). Both helpers previously read raw `request.*` values, so a partial update created a spurious new version and wrote a version snapshot with omitted versioned fields wiped, diverging from the corrected master record:
   - `AssistantVersionCompareService.has_configuration_changes` — overlay only the fields present in `request.model_fields_set` onto the current version's comparison dict; omitted fields keep the current value, so a partial update that changes nothing versioned returns `False` (no spurious version).
   - `AssistantVersionService.create_new_version` — snapshot omitted versioned fields from the merged assistant (`assistant.*`) instead of the request's `None`/`[]` defaults; explicitly-set fields still come from the request.
   - Add regression tests: `has_configuration_changes` returns `False` for a partial update omitting versioned fields (and `True` when a set field actually changes); `create_new_version` preserves omitted versioned fields and applies explicitly-set ones.

## Behavior Matrix

| Scenario | field in model_fields_set | Old behavior | New behavior |
| :--- | :---: | :--- | :--- |
| Omitted optional field | No | Overwritten with `None` | Preserved (stored value retained) |
| Field explicitly set to a value | Yes | Updated to new value | Updated to new value |
| Field explicitly set to null | Yes | Cleared to `None` | Cleared to `None` |
| Required field | Yes | Updated to new value | Updated to new value |

## Testing
- **Unit Tests**: Modify existing assertions in `test_map_assistant_request_helpers.py` to reflect the removal of the `field_value is None` clause.
- **Regression Test**: Add a test simulating a partial update where `description` and `system_prompt` are omitted but have existing non-`None` values on the stored assistant. Assert the mapped instance retains the existing values.
- **Explicit Clear Test**: Assert that sending `description=null` (field present in `model_fields_set`) results in `description` being set to `None`.
- **System Prompt Guard**: Assert `_should_update_system_prompt` behavior is unchanged (preserves omitted values, applies changed values).

## Acceptance Criteria
- `_should_update_field` updates only explicitly set fields unless a full update is intended.
- Omitted `None`-default fields are preserved during partial updates.
- Regression test covers partial update preserving `description` and `system_prompt`.
- API behavior matches the model docstring.

## Out of Scope
- Removing the dead legacy `fields_set is None` branch entirely.
- Any schema, migration, or configuration changes.
- Changes to `_should_update_system_prompt` logic.

> **Scope note:** The versioning path (CR-001) was originally out of scope but was brought in-scope during code review by user decision. See item 4 in Approved Solution.
