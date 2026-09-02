# Plan — EPMCDME-10913 backend

Ticket: EPMCDME-10913 · Branch: `EPMCDME-10913_deprecate-zephyrsquad` · Spec: [spec.md](./spec.md)

Five inline TDD tasks; small, additive, no schema change.

---

## T1 — Add `deprecated: bool = False` to `ToolMetadata` and `Tool`

**Files**: `src/codemie_tools/base/models.py`

**Change**: add `deprecated: Optional[bool] = False` field to both `ToolMetadata` (line 51) and `Tool` (line 146). `Tool.from_metadata` classmethod (already forwards other metadata fields) must also carry the `deprecated` flag through.

**Test-first: yes** — `tests/codemie_tools/base/test_models_deprecated_flag.py` (new file). Two cases:
1. `ToolMetadata(name="x").model_dump()["deprecated"] is False` (default).
2. `Tool.from_metadata(ToolMetadata(name="x", deprecated=True)).model_dump()["deprecated"] is True`.

Both must fail before adding the field.

---

## T2 — Mark `ZEPHYR_SQUAD_TOOL` as deprecated

**Files**: `src/codemie_tools/qa/zephyr_squad/tools_vars.py`

**Change**: add `deprecated=True` to the `ZEPHYR_SQUAD_TOOL = ToolMetadata(...)` constructor.

**Test-first: yes** — add a case to `tests/codemie_tools/qa/test_qa_toolkit.py` (or a companion test) asserting that the ZephyrSquad entry in `QualityAssuranceToolkitUI.get_definition().model_dump()["tools"]` has `deprecated: True`. Verify other QA tools have `deprecated: False` (i.e. the default carries through).

---

## T3a — Shared deprecated-credential-type mechanism

**Files**: `src/codemie/service/settings/settings_request_validator.py`

**Change**: add the registry and the single check that all four write endpoints reuse:

```python
DEPRECATED_CREDENTIAL_TYPES: dict[CredentialTypes, str] = {
    CredentialTypes.ZEPHYR_SQUAD: "Use an alternative test-management integration (e.g. Zephyr Scale) for new setups.",
}


def validate_credential_type_not_deprecated(request: SettingRequest) -> None:
    help_message = DEPRECATED_CREDENTIAL_TYPES.get(request.credential_type)
    if help_message is None:
        return
    credential_type_name = request.credential_type.value
    raise ExtendedHTTPException(
        code=status.HTTP_410_GONE,
        message=f"{credential_type_name} integration is deprecated",
        details=(
            f"New {credential_type_name} settings can no longer be created or updated. "
            "Existing configurations remain read-only."
        ),
        help=help_message,
    )
```

Deprecating the next integration = one dict entry, zero router edits.

**Test-first: yes** — extend `tests/codemie/service/settings/test_settings_request_validator.py`: every type in the registry raises 410 with the right message/details/help (parametrized over the registry itself, so future entries are covered automatically), and active types (`ZephyrScale`, `Git`, `Jira`, `Xray`) pass through untouched.

---

## T3 — Block ZephyrSquad on `POST /v1/settings/user` and `PUT /v1/settings/user/{id}`

**Files**: `src/codemie/rest_api/routers/user_settings.py`

**Change**: call the shared `validate_credential_type_not_deprecated(request)` (see T3a) once at the top of both `create_user_setting` and `update_user_setting`, *before* the scheduler/litellm/git/webhook `if/elif` dispatch chain — deprecation is orthogonal to type dispatch and must not depend on branch order.

**Test-first: yes** — extend `tests/codemie/rest_api/routers/test_user_settings.py`. Two cases:
1. `POST /v1/settings/user` with `credential_type=ZephyrSquad` → 410, `response.json()["error"]["message"] == "ZephyrSquad integration is deprecated"`.
2. `PUT /v1/settings/user/{setting_id}` with `credential_type=ZephyrSquad` → 410, same error envelope.

Both must fail before the guard is added.

---

## T4 — Block ZephyrSquad on `POST /v1/settings/project` and `PUT /v1/settings/project/{id}`

**Files**: `src/codemie/rest_api/routers/project_settings.py`

**Change**: same single `validate_credential_type_not_deprecated(request)` call, injected into `create_project_setting` and `update_project_setting`. No per-type block is duplicated — the 410 payload is built once in the validator.

**Test-first: yes** — extend `tests/codemie/rest_api/routers/test_project_settings.py` with the same two cases (create + update) → both 410.

---

## T5 — Reconcile `test_qa_toolkit.py`

**Files**: `tests/codemie_tools/qa/test_qa_toolkit.py`

**Change**: ZephyrSquad stays in the toolkit list (see spec Non-goals), so `len(toolkit_ui.tools) == 5` and the `"ZephyrSquad" in tool_names` assertion remain valid. Update the test only to also assert that ZephyrSquad's entry has `deprecated=True` and the other four QA tools have `deprecated=False`. If T2 already covers this, keep only the assertions that fit this file's scope.

**Test-first: n/a** — this is a test-file update task; the assertion is added first, run to confirm it reflects the current behavior from T2, then no impl change is needed.

---

## Execution order

T1 → T2 → T3 → T4 → T5. T2 depends on T1 (needs the field). T3/T4 are independent of T1/T2 and could run in parallel, but sequential keeps commits atomic and the review diff readable.

## Out of scope

- UI (codemie-ui/) — separate run + MR.
- Removing `ZephyrSquad` from `CredentialTypes` enum.
- Schema migration for the `settings` table.
- Tool implementation changes (`ZephyrSquadGenericTool`, `ZephyrRestAPI`).
- Doc updates in `codemie-onboarding/`.
