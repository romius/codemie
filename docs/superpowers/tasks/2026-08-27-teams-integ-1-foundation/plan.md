# ms_teams Integration Type (Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a project-scoped `ms_teams` integration type to the existing `CredentialTypes`/`Settings` model, with `project_settings.py` create/update validation for an `assistant_ids` payload and a per-project singleton rule — without touching the old `assistant_project_mapping` mechanism or its feature flag.

**Architecture:** Extend the shared `CredentialTypes` enum (+ its Postgres enum migration), add one new validator function to `settings_request_validator.py` reusing the existing `AssistantService.belongs_to_project` helper, and wire that validator into the existing `if/elif credential_type` branches of `create_project_setting`/`update_project_setting`. `GET`/`DELETE` need no code change. The assistant list itself lives inside `Settings.credential_values` as `CredentialValues(key="assistant_ids", value=[...])` — no new column/table.

**Tech Stack:** FastAPI, SQLModel/Postgres, Alembic (`alembic_postgresql_enum`), pytest/pytest-asyncio, httpx `ASGITransport`.

**Spec:** `docs/superpowers/tasks/2026-08-27-teams-integ-1-foundation/spec.md`

## Global Constraints

- `ms_teams` create/update is project-scope only (`SettingType.PROJECT`); reject `USER`.
- Every `assistant_ids` entry must reference an assistant that exists and belongs to the target `project_name`.
- At most one non-deleted `ms_teams` `Settings` row per `project_name` (app-level check, same read-then-write pattern as `check_alias_unique` — do not introduce a DB constraint).
- No `ms_teams` code path reads, references, or modifies `features:teamsBotIntegration`.
- No change to `assistant_project_mapping.py` (router/service/repository/model), to assistant-listing/merge logic, or to the existing `DELETE /v1/settings/project/{setting_id}` behavior (including its known `Settings.delete_setting`-bypass inconsistency).
- No Alembic data migration in this story — only the `CredentialTypes` enum-value migration.
- Commit per task using the repository's existing convention (`EPMCDME-14111: <description>`, per AGENTS.md git-workflow guide).

---

## Task 1: Add `MS_TEAMS` to `CredentialTypes` and its Alembic enum migration

**Files:**
- Modify: `src/codemie_tools/base/models.py:109` (after `DIAL = "DIAL"`)
- Create: `src/external/alembic/versions/<new_revision>_add_ms_teams_credential_type.py`
- Test: `tests/codemie_tools/base/test_models.py` (create if it does not exist)

**Interfaces:**
- Produces: `CredentialTypes.MS_TEAMS == "ms_teams"`, importable by Task 2/3.

- [ ] **Step 1: Write the failing test**

```python
from codemie_tools.base.models import CredentialTypes


def test_ms_teams_credential_type_exists():
    assert CredentialTypes.MS_TEAMS == "ms_teams"
    assert CredentialTypes.MS_TEAMS.name == "MS_TEAMS"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie_tools/base/test_models.py::test_ms_teams_credential_type_exists -v`
Expected: FAIL — `AttributeError: MS_TEAMS`

- [ ] **Step 3: Add the enum member**

In `src/codemie_tools/base/models.py`, under the `# Project settings` comment (line 108-109):

```python
    # Project settings
    DIAL = "DIAL"
    MS_TEAMS = "ms_teams"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `poetry run pytest tests/codemie_tools/base/test_models.py::test_ms_teams_credential_type_exists -v`
Expected: PASS

- [ ] **Step 5: Determine the actual current Alembic head**

Run: `poetry run alembic -c src/external/alembic/alembic.ini heads` (or, if that config path is wrong, locate `alembic.ini` first with `find . -name alembic.ini`). Note the returned revision id — it is the `down_revision` for the new migration file below. Do not guess it from the file list.

- [ ] **Step 6: Write the migration**

Create `src/external/alembic/versions/<new_revision>_add_ms_teams_credential_type.py`, following the exact structure of `src/external/alembic/versions/3b3358380aa8_add_xwiki_credential_type.py`: `upgrade()` calls `op.sync_enum_values(enum_schema='codemie', enum_name='credentialtypes', new_values=[...all current member NAMES from `CredentialTypes`..., 'MS_TEAMS'], affected_columns=[TableReference(table_schema='codemie', table_name='settings', column_name='credential_type')], enum_values_to_rename=[])`. Build the `new_values` list by reading the full current member-name list from `src/codemie_tools/base/models.py` (all names, in declaration order, ending with `MS_TEAMS`). `downgrade()` first runs `op.execute("DELETE FROM codemie.settings WHERE credential_type = 'MS_TEAMS'")` then calls `sync_enum_values` with the same list minus `'MS_TEAMS'`. Use the revision id Alembic generates (`alembic revision` or a fresh 12-char hex) and set `down_revision` to the value found in Step 5.

- [ ] **Step 7: Verify the migration applies and reverts**

Run: `poetry run alembic -c src/external/alembic/alembic.ini upgrade head` then `poetry run alembic -c src/external/alembic/alembic.ini downgrade -1` then `upgrade head` again, against the local dev DB per `.ai-run/guides/development/setup-guide.md`.
Expected: all three commands succeed with no error.

- [ ] **Step 8: Commit**

```bash
git add src/codemie_tools/base/models.py src/external/alembic/versions/<new_revision>_add_ms_teams_credential_type.py tests/codemie_tools/base/test_models.py
git commit -m "EPMCDME-14111: add ms_teams credential type enum and migration"
```

Test-first: yes — `test_ms_teams_credential_type_exists` fails before Step 3, passes after.

---

## Task 2: `validate_ms_teams_request` — scope, assistant-ids, and singleton validation

**Files:**
- Modify: `src/codemie/service/settings/settings_request_validator.py` (add function near `validate_webhook_request`, ~line 255)
- Modify: `src/codemie/rest_api/models/settings.py` (add `Settings.check_ms_teams_singleton` classmethod near `check_alias_unique`, ~line 297)
- Test: `tests/codemie/service/settings/test_settings_request_validator.py` (create if it does not exist)

**Interfaces:**
- Consumes: `SettingRequest` (`project_name`, `credential_type`, `credential_values`) and `SettingType` from `codemie.rest_api.models.settings`; `AssistantService.belongs_to_project(assistant_id: str, project_name: str) -> bool` (`src/codemie/service/assistant/assistant_service.py:112`, already imported elsewhere in this validator file for `validate_assistant_ownership`); `ExtendedHTTPException` from `codemie.core.exceptions`.
- Produces: `validate_ms_teams_request(request: SettingRequest, setting_type: SettingType, setting_id: Optional[str] = None) -> None` — raises `ExtendedHTTPException` on any failure, returns `None` on success. Consumed by Task 3.
- Produces: `Settings.check_ms_teams_singleton(project_name: str, setting_id: Optional[str] = None) -> bool` — mirrors `check_alias_unique`'s read-then-write pattern; raises `ValueError` if a different `ms_teams` row already exists for `project_name`, else returns `True`. Consumed by `validate_ms_teams_request`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/codemie/service/settings/test_settings_request_validator.py
from unittest.mock import patch, MagicMock

import pytest
from fastapi import status

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.models.settings import CredentialValues, SettingRequest, SettingType
from codemie_tools.base.models import CredentialTypes
from codemie.service.settings.settings_request_validator import validate_ms_teams_request


def _request(setting_type_credential_values, project_name="proj1"):
    return SettingRequest(
        project_name=project_name,
        alias="teams-integration",
        credential_type=CredentialTypes.MS_TEAMS,
        credential_values=setting_type_credential_values,
    )


@patch("codemie.rest_api.models.settings.Settings.check_ms_teams_singleton")
@patch("codemie.service.assistant.assistant_service.AssistantService.belongs_to_project")
def test_valid_request_passes(mock_belongs_to_project, mock_singleton):
    # Arrange
    mock_belongs_to_project.return_value = True
    mock_singleton.return_value = True
    request = _request([CredentialValues(key="assistant_ids", value=["a1", "a2"])])

    # Act / Assert — no exception
    validate_ms_teams_request(request, setting_type=SettingType.PROJECT)


def test_rejects_user_scope():
    # Arrange
    request = _request([CredentialValues(key="assistant_ids", value=["a1"])])

    # Act / Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_ms_teams_request(request, setting_type=SettingType.USER)
    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST


def test_rejects_missing_assistant_ids():
    # Arrange
    request = _request([])

    # Act / Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_ms_teams_request(request, setting_type=SettingType.PROJECT)
    assert exc_info.value.code == status.HTTP_400_BAD_REQUEST


@patch("codemie.service.assistant.assistant_service.AssistantService.belongs_to_project")
def test_rejects_assistant_not_in_project(mock_belongs_to_project):
    # Arrange
    mock_belongs_to_project.return_value = False
    request = _request([CredentialValues(key="assistant_ids", value=["bad-id"])])

    # Act / Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_ms_teams_request(request, setting_type=SettingType.PROJECT)
    assert "bad-id" in exc_info.value.details


@patch("codemie.rest_api.models.settings.Settings.check_ms_teams_singleton")
@patch("codemie.service.assistant.assistant_service.AssistantService.belongs_to_project")
def test_rejects_duplicate_ms_teams_row(mock_belongs_to_project, mock_singleton):
    # Arrange
    mock_belongs_to_project.return_value = True
    mock_singleton.side_effect = ValueError("duplicate")
    request = _request([CredentialValues(key="assistant_ids", value=["a1"])])

    # Act / Assert
    with pytest.raises(ExtendedHTTPException) as exc_info:
        validate_ms_teams_request(request, setting_type=SettingType.PROJECT)
    assert exc_info.value.code == status.HTTP_409_CONFLICT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/settings/test_settings_request_validator.py -v`
Expected: FAIL — `ImportError: cannot import name 'validate_ms_teams_request'`

- [ ] **Step 3: Add `Settings.check_ms_teams_singleton`**

In `src/codemie/rest_api/models/settings.py`, add directly after `check_alias_unique` (after line 315):

```python
    @classmethod
    def check_ms_teams_singleton(cls, project_name: str, setting_id: Optional[str] = None) -> bool:
        existing = cls.get_by_fields(
            {PROJECT_NAME_TERM: project_name, "credential_type.keyword": CredentialTypes.MS_TEAMS.value}
        )
        if existing and (not setting_id or setting_id != existing.id):
            raise ValueError(f"An ms_teams integration already exists for project {project_name!r}")
        return True
```

- [ ] **Step 4: Add `validate_ms_teams_request`**

In `src/codemie/service/settings/settings_request_validator.py`, add directly after `validate_webhook_request` (after line 265). Also add, near the top-of-file imports (alongside the existing `from codemie.rest_api.models.settings import CredentialValues, SettingRequest` at line 22): `SettingType` to that import, and `from codemie.rest_api.models.settings import Settings` (or extend the existing import line), plus `from codemie_tools.base.models import CredentialTypes` and `from codemie.service.assistant.assistant_service import AssistantService`:

```python
def validate_ms_teams_request(
    request: SettingRequest, setting_type: SettingType, setting_id: Optional[str] = None
) -> None:
    """
    Validate an ms_teams project-integration request: PROJECT-scope only, every
    assistant_ids entry must belong to the target project, and at most one
    ms_teams row may exist per project.
    """
    if setting_type != SettingType.PROJECT:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="ms_teams integrations are project-scoped only",
            details="ms_teams integrations cannot be created or updated at USER scope.",
            help="Submit this request with setting_type=PROJECT.",
        )

    assistant_ids_cred = next((cv for cv in request.credential_values if cv.key == "assistant_ids"), None)
    if assistant_ids_cred is None or not isinstance(assistant_ids_cred.value, list):
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="assistant_ids is required",
            details="ms_teams integrations require a credential_values entry "
            "with key='assistant_ids' and a list value.",
            help="Provide CredentialValues(key='assistant_ids', value=[<assistant_id>, ...]).",
        )

    invalid_ids = [
        assistant_id
        for assistant_id in assistant_ids_cred.value
        if not AssistantService.belongs_to_project(assistant_id, request.project_name)
    ]
    if invalid_ids:
        raise ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST,
            message="Invalid assistant_ids",
            details=f"The following assistant id(s) do not exist or do not belong to "
            f"project {request.project_name!r}: {', '.join(invalid_ids)}.",
            help="Only assistants belonging to the target project may be listed in assistant_ids.",
        )

    try:
        Settings.check_ms_teams_singleton(request.project_name, setting_id=setting_id)
    except ValueError as e:
        raise ExtendedHTTPException(
            code=status.HTTP_409_CONFLICT,
            message="ms_teams integration already exists",
            details=str(e),
            help="Update or delete the existing ms_teams integration for this project instead of creating a new one.",
        ) from e
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/settings/test_settings_request_validator.py -v`
Expected: PASS (all 5 tests)

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/settings/settings_request_validator.py src/codemie/rest_api/models/settings.py tests/codemie/service/settings/test_settings_request_validator.py
git commit -m "EPMCDME-14111: add ms_teams request validation and singleton check"
```

Test-first: yes — the 5 tests in Step 1 fail on import before Steps 3-4, pass after.

---

## Task 3: Wire `validate_ms_teams_request` into `create_project_setting`/`update_project_setting`

**Files:**
- Modify: `src/codemie/rest_api/routers/project_settings.py:87-127` (`create_project_setting`), `:136-184` (`update_project_setting`)
- Test: `tests/codemie/rest_api/routers/test_project_settings.py` (add cases)

**Interfaces:**
- Consumes: `validate_ms_teams_request(request, setting_type, setting_id=None)` from Task 2.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/codemie/rest_api/routers/test_project_settings.py

@pytest.mark.anyio
@patch('codemie.service.settings.settings.SettingsService.create_setting')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_create_ms_teams_project_setting_succeeds(mock_authenticate, mock_create_setting):
    user = User(id="user123", username="testuser", project_names=["proj1"])
    user.is_admin = True
    mock_authenticate.return_value = user

    payload = {
        "project_name": "proj1",
        "alias": "teams-integration",
        "credential_type": "ms_teams",
        "credential_values": [{"key": "assistant_ids", "value": ["a1"]}],
    }

    with patch(
        "codemie.rest_api.routers.project_settings.validate_ms_teams_request"
    ) as mock_validate:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/settings/project", json=payload, headers={"user-id": "user123"}
            )

    assert response.status_code == 200
    mock_validate.assert_called_once()
    mock_create_setting.assert_called_once()


@pytest.mark.anyio
@patch('codemie.service.settings.settings.SettingsService.create_setting')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_create_ms_teams_project_setting_rejects_invalid_request(mock_authenticate, mock_create_setting):
    user = User(id="user123", username="testuser", project_names=["proj1"])
    user.is_admin = True
    mock_authenticate.return_value = user

    payload = {
        "project_name": "proj1",
        "alias": "teams-integration",
        "credential_type": "ms_teams",
        "credential_values": [{"key": "assistant_ids", "value": ["bad-id"]}],
    }

    with patch(
        "codemie.rest_api.routers.project_settings.validate_ms_teams_request",
        side_effect=ExtendedHTTPException(
            code=status.HTTP_400_BAD_REQUEST, message="Invalid assistant_ids", details="bad-id", help="fix it"
        ),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.post(
                "/v1/settings/project", json=payload, headers={"user-id": "user123"}
            )

    assert response.status_code == 400
    mock_create_setting.assert_not_called()


@pytest.mark.anyio
@patch('codemie.service.settings.settings.SettingsService.update_settings')
@patch('codemie.service.settings.settings.SettingsService.get_setting_ability')
@patch("codemie.rest_api.security.idp.local.LocalIdp.authenticate")
async def test_update_ms_teams_project_setting_revalidates(
    mock_authenticate, mock_get_setting_ability, mock_update_settings
):
    from codemie.core.ability import Owned

    user = User(id="user123", username="testuser", project_names=["proj1"])
    user.is_admin = True
    mock_authenticate.return_value = user
    mock_get_setting_ability.return_value = Owned(owner_id="user123", project_name="proj1")

    payload = {
        "project_name": "proj1",
        "alias": "teams-integration",
        "credential_type": "ms_teams",
        "credential_values": [{"key": "assistant_ids", "value": ["a1", "a2"]}],
    }

    with patch(
        "codemie.rest_api.routers.project_settings.validate_ms_teams_request"
    ) as mock_validate:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
            response = await ac.put(
                "/v1/settings/project/setting-1", json=payload, headers={"user-id": "user123"}
            )

    assert response.status_code == 200
    mock_validate.assert_called_once_with(mock_validate.call_args[0][0], setting_type=SettingType.PROJECT, setting_id="setting-1")
    mock_update_settings.assert_called_once()
```

(These 3 new tests exercise, together with the untouched existing `test_index_user_settings`-style GET coverage and the untouched DELETE endpoint, all 6 spec acceptance-criteria scenarios except the "second POST rejected" and "invalid assistant id rejected" cases, which Task 2's unit tests already cover at the validator level — this task's tests confirm the router *calls* the validator with the right arguments and propagates its exceptions/success, not the validator's internal logic again.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_project_settings.py -v -k ms_teams`
Expected: FAIL — router raises no `ms_teams` branch, so `validate_ms_teams_request` is never called (`mock_validate.assert_called_once()` fails) and `create_setting`/`update_settings` proceed unvalidated.

- [ ] **Step 3: Wire the branch into both endpoints**

In `src/codemie/rest_api/routers/project_settings.py`, add the import (alongside the existing `settings_request_validator` import block at lines 31-36):

```python
from codemie.service.settings.settings_request_validator import (
    validate_git_request,
    validate_litellm_request,
    validate_ms_teams_request,
    validate_scheduler_request,
    validate_webhook_request,
)
```

In `create_project_setting` (after the `elif request.credential_type == CredentialTypes.WEBHOOK:` branch, line 108-109):

```python
    elif request.credential_type == CredentialTypes.MS_TEAMS:
        validate_ms_teams_request(request, setting_type=SettingType.PROJECT)
```

In `update_project_setting` (after the equivalent `elif` branch, line 157-158):

```python
    elif request.credential_type == CredentialTypes.MS_TEAMS:
        validate_ms_teams_request(request, setting_type=SettingType.PROJECT, setting_id=setting_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_project_settings.py -v -k ms_teams`
Expected: PASS (3 new tests)

- [ ] **Step 5: Run the full router test file to confirm no regression**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_project_settings.py -v`
Expected: PASS (all tests, including pre-existing SCHEDULER/LITE_LLM/GIT/WEBHOOK cases)

- [ ] **Step 6: Commit**

```bash
git add src/codemie/rest_api/routers/project_settings.py tests/codemie/rest_api/routers/test_project_settings.py
git commit -m "EPMCDME-14111: wire ms_teams validation into project settings create/update"
```

Test-first: yes — the 3 tests in Step 1 fail before Step 3, pass after.

---

## Negative-Constraint Pass

- **No Alembic data migration of `assistant_project_mapping` rows** — Task 1's migration only runs `sync_enum_values`/`DELETE ... WHERE credential_type = 'MS_TEAMS'` against the `credentialtypes` enum and `settings` table; no task touches `assistant_project_mapping`.
- **`assistant_project_mapping.py` (router/service/repository/model) untouched** — no task lists that file under Files: Modify/Create anywhere in this plan.
- **`features:teamsBotIntegration` not read/referenced/modified** — no task's code (Task 1 enum/migration, Task 2 validator, Task 3 router wiring) mentions `customer_config`, `is_feature_enabled`, or `teamsBotIntegration`.
- **Assistant-listing/merge logic unchanged** — no task modifies `AssistantProjectMappingService`, `AssistantRepository.query`, or any Teams-enabled-assistants listing path; Task 2 only calls the existing read-only `AssistantService.belongs_to_project`.
- **Alias-uniqueness race and DELETE bypass not "fixed"** — Task 2's `check_ms_teams_singleton` copies `check_alias_unique`'s existing read-then-write shape rather than adding a DB constraint or transaction; no task modifies `delete_project_setting` or `Settings.delete_setting`.
- **No frontend/UI work** — no task touches any file outside `src/codemie`, `src/codemie_tools`, `src/external/alembic`, and `tests/` (all Python/backend).
