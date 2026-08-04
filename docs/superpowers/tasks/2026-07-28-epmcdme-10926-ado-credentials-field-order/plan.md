# EPMCDME-10926 AzureDevOps Credential Field Order and Validation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorder fields in the AzureDevOps credential model, enforce mandatory fields (`base_url`, `organization`, `access_token`), and make `project` explicitly optional.

**Architecture:** Three independent surgical edits across the REST model, service layer, and tool config. No DB migrations, no field renames, no loader changes. Each task is independently testable.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest

## Global Constraints

- All new test files must carry the Apache 2.0 license header (same header as `tests/codemie/rest_api/models/test_settings_litellm.py`)
- `make ruff` must pass after each task
- `make test` must pass after each task
- Commit message format: `EPMCDME-10926: <short description>`

---

### Task 1: Tighten `AzureDevOpsCredentials` model

**Test-first: yes — `test_mandatory_fields_reject_empty_string` fails before the `Field(min_length=1)` constraints are added**

**Files:**
- Modify: `src/codemie/rest_api/models/settings.py:194-198`
- Create: `tests/codemie/rest_api/models/test_azure_devops_credentials.py`

**Interfaces:**
- Produces: `AzureDevOpsCredentials` with fields ordered `base_url, organization, project, access_token`; `base_url`/`organization`/`access_token` reject empty strings; `project` is `Optional[str] = None`

- [ ] **Step 1: Create the test file**

```python
# tests/codemie/rest_api/models/test_azure_devops_credentials.py
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

import pytest
from pydantic import ValidationError

from codemie.rest_api.models.settings import AzureDevOpsCredentials


class TestAzureDevOpsCredentials:
    def _valid_payload(self, **overrides):
        return {
            "base_url": "https://dev.azure.com",
            "organization": "my-org",
            "access_token": "secret-pat",
            **overrides,
        }

    def test_valid_with_project(self):
        creds = AzureDevOpsCredentials(**self._valid_payload(project="my-project"))
        assert creds.base_url == "https://dev.azure.com"
        assert creds.organization == "my-org"
        assert creds.project == "my-project"
        assert creds.access_token == "secret-pat"

    def test_valid_without_project(self):
        creds = AzureDevOpsCredentials(**self._valid_payload())
        assert creds.project is None

    def test_project_none_explicit(self):
        creds = AzureDevOpsCredentials(**self._valid_payload(project=None))
        assert creds.project is None

    @pytest.mark.parametrize("field", ["base_url", "organization", "access_token"])
    def test_mandatory_fields_reject_empty_string(self, field):
        with pytest.raises(ValidationError) as exc_info:
            AzureDevOpsCredentials(**self._valid_payload(**{field: ""}))
        assert field in str(exc_info.value)

    @pytest.mark.parametrize("field", ["base_url", "organization", "access_token"])
    def test_mandatory_fields_reject_missing(self, field):
        payload = self._valid_payload()
        del payload[field]
        with pytest.raises(ValidationError) as exc_info:
            AzureDevOpsCredentials(**payload)
        assert field in str(exc_info.value)

    def test_field_order(self):
        fields = list(AzureDevOpsCredentials.model_fields.keys())
        assert fields == ["base_url", "organization", "project", "access_token"]
```

- [ ] **Step 2: Run tests to verify they fail**

```
poetry run pytest tests/codemie/rest_api/models/test_azure_devops_credentials.py -v
```

Expected: `FAILED` — `test_mandatory_fields_reject_empty_string` and `test_field_order` fail because the current model has no constraints and the wrong field order.

- [ ] **Step 3: Update `AzureDevOpsCredentials` in `src/codemie/rest_api/models/settings.py`**

Replace the four-line class body (lines 194–198). `Field` and `Optional` are already imported at the top of the file.

```python
class AzureDevOpsCredentials(BaseModel):
    base_url: str = Field(min_length=1)
    organization: str = Field(min_length=1)
    project: Optional[str] = None
    access_token: str = Field(min_length=1)
```

- [ ] **Step 4: Run tests to verify they pass**

```
poetry run pytest tests/codemie/rest_api/models/test_azure_devops_credentials.py -v
```

Expected: all 11 tests `PASSED`.

- [ ] **Step 5: Run ruff**

```
make ruff
```

Expected: exits 0.

- [ ] **Step 6: Commit**

```bash
git add src/codemie/rest_api/models/settings.py \
        tests/codemie/rest_api/models/test_azure_devops_credentials.py
git commit -m "EPMCDME-10926: Reorder AzureDevOps credential fields and add mandatory validation"
```

---

### Task 2: Fix `get_azure_devops_creds()` project coercion

**Test-first: yes — `test_get_azure_devops_creds_returns_none_project_when_empty` fails before the `project or None` coercion is added**

**Files:**
- Modify: `src/codemie/service/settings/settings.py:1286`
- Modify: `tests/codemie/service/settings/test_settings_service.py` (add test)

**Interfaces:**
- Consumes: `AzureDevOpsCredentials` from Task 1 (project is `Optional[str] = None`)
- Produces: `get_azure_devops_creds()` returns `project=None` when DB stores `""` for project

- [ ] **Step 1: Add the failing test to `tests/codemie/service/settings/test_settings_service.py`**

Add this function at the end of the file (after `test_get_azure_devops_creds_uses_setting_id_when_provided`):

```python
@patch.object(SettingsService, 'retrieve_setting')
def test_get_azure_devops_creds_returns_none_project_when_empty(mock_retrieve):
    # Given: a stored integration where project is empty string
    mock_setting = MagicMock()
    def credential_side_effect(key):
        return {
            SettingsService.URL: "https://dev.azure.com",
            SettingsService.PROJECT: "",
            SettingsService.ORGANIZATION: "my-org",
            SettingsService.TOKEN: "secret-pat",
        }.get(key, "")
    mock_setting.credential.side_effect = credential_side_effect
    mock_retrieve.return_value = mock_setting

    # When
    result = SettingsService.get_azure_devops_creds(user_id="u1", project_name="p1")

    # Then: project is None, not empty string
    assert result.project is None
    assert result.base_url == "https://dev.azure.com"
    assert result.organization == "my-org"
    assert result.access_token == "secret-pat"
```

- [ ] **Step 2: Run the new test to verify it fails**

```
poetry run pytest tests/codemie/service/settings/test_settings_service.py::test_get_azure_devops_creds_returns_none_project_when_empty -v
```

Expected: `FAILED` — `result.project` is `""` not `None`.

- [ ] **Step 3: Update the return statement in `get_azure_devops_creds()` (`src/codemie/service/settings/settings.py:1286`)**

```python
# Before
return AzureDevOpsCredentials(base_url=base_url, project=project, organization=organization, access_token=token)

# After
return AzureDevOpsCredentials(base_url=base_url, project=project or None, organization=organization, access_token=token)
```

- [ ] **Step 4: Run the new test to verify it passes**

```
poetry run pytest tests/codemie/service/settings/test_settings_service.py::test_get_azure_devops_creds_returns_none_project_when_empty -v
```

Expected: `PASSED`.

- [ ] **Step 5: Run the full settings service test suite to check for regressions**

```
poetry run pytest tests/codemie/service/settings/test_settings_service.py -v
```

Expected: all tests `PASSED`.

- [ ] **Step 6: Run ruff**

```
make ruff
```

Expected: exits 0.

- [ ] **Step 7: Commit**

```bash
git add src/codemie/service/settings/settings.py \
        tests/codemie/service/settings/test_settings_service.py
git commit -m "EPMCDME-10926: Coerce empty project string to None in get_azure_devops_creds"
```

---

### Task 3: Add `organization` and `project` fields to `GenericAzureDevOpsConfig`

**Test-first: yes — `test_organization_required` fails before the `organization` field is added**

**Files:**
- Modify: `src/codemie_tools/azure_devops/generic/models.py`
- Modify: `tests/codemie_tools/azure_devops/generic/test_models.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (independent model)
- Produces: `GenericAzureDevOpsConfig` with fields `url`, `organization` (required), `project` (optional, `None`), `token`; `organization` missing raises `ValidationError`

- [ ] **Step 1: Add new tests to `tests/codemie_tools/azure_devops/generic/test_models.py`**

Add a new test class after the existing `TestGenericAzureDevOpsConfig` class and after `test_url_default_wired_to_tool_default`:

```python
class TestGenericAzureDevOpsConfigOrganizationAndProject:
    def test_organization_required(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError) as exc_info:
            GenericAzureDevOpsConfig(url="https://dev.azure.com", token="pat")
        assert "organization" in str(exc_info.value)

    def test_organization_accepted(self):
        config = GenericAzureDevOpsConfig(
            url="https://dev.azure.com",
            organization="my-org",
            token="pat",
        )
        assert config.organization == "my-org"

    def test_project_optional_defaults_to_none(self):
        config = GenericAzureDevOpsConfig(
            url="https://dev.azure.com",
            organization="my-org",
            token="pat",
        )
        assert config.project is None

    def test_project_accepted_when_provided(self):
        config = GenericAzureDevOpsConfig(
            url="https://dev.azure.com",
            organization="my-org",
            project="my-project",
            token="pat",
        )
        assert config.project == "my-project"

    def test_field_order(self):
        fields = [f for f in GenericAzureDevOpsConfig.model_fields if f != "credential_type"]
        assert fields == ["url", "organization", "project", "token"]
```

Also add `import pytest` to the top of the test file if not already present.

- [ ] **Step 2: Run new tests to verify they fail**

```
poetry run pytest tests/codemie_tools/azure_devops/generic/test_models.py::TestGenericAzureDevOpsConfigOrganizationAndProject -v
```

Expected: `FAILED` — `organization` field does not exist yet.

- [ ] **Step 3: Update `GenericAzureDevOpsConfig` in `src/codemie_tools/azure_devops/generic/models.py`**

Replace the entire file content:

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

from typing import ClassVar, Optional

from pydantic import Field

from codemie_tools.base.models import (
    CodeMieToolConfig,
    CredentialTypes,
    RequiredField,
    get_tool_default,
)


class GenericAzureDevOpsConfig(CodeMieToolConfig):
    """Generic Azure DevOps credential configuration for UI-based credential entry."""

    TOOL_NAME: ClassVar[str] = "azuredevops"

    credential_type: CredentialTypes = Field(default=CredentialTypes.AZURE_DEVOPS, frozen=True, exclude=True)

    url: str = RequiredField(
        default=get_tool_default(TOOL_NAME, "url") or "",
        description="Azure DevOps organization URL",
        json_schema_extra={"placeholder": get_tool_default(TOOL_NAME, "url_placeholder") or ""},
    )

    organization: str = RequiredField(description="Azure DevOps organization name")

    project: Optional[str] = Field(default=None, description="Azure DevOps project name (optional)")

    token: str = RequiredField(
        description="Personal Access Token",
        json_schema_extra={"sensitive": True},
    )
```

- [ ] **Step 4: Fix the existing `test_valid_config` test**

The existing test `TestGenericAzureDevOpsConfig.test_valid_config` now fails because `organization` is required. Update it in `tests/codemie_tools/azure_devops/generic/test_models.py`:

```python
def test_valid_config(self):
    config = GenericAzureDevOpsConfig(
        url="https://dev.azure.com/myorg",
        organization="myorg",
        token="test_token",
    )
    assert config.url == "https://dev.azure.com/myorg"
    assert config.organization == "myorg"
    assert config.token == "test_token"
```

- [ ] **Step 5: Run the full test file to verify all tests pass**

```
poetry run pytest tests/codemie_tools/azure_devops/generic/test_models.py -v
```

Expected: all tests `PASSED`.

- [ ] **Step 6: Run ruff**

```
make ruff
```

Expected: exits 0.

- [ ] **Step 7: Commit**

```bash
git add src/codemie_tools/azure_devops/generic/models.py \
        tests/codemie_tools/azure_devops/generic/test_models.py
git commit -m "EPMCDME-10926: Add organization and project fields to GenericAzureDevOpsConfig"
```

---

## Final Verification

- [ ] Run the full test suite:

```
make test
```

Expected: all tests `PASSED`.
