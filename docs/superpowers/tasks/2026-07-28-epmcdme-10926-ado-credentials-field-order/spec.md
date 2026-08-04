# Spec: EPMCDME-10926 — AzureDevOps Credential Field Order and Validation

## Goal

Tighten the AzureDevOps credential model at the REST API boundary: reorder fields to match the logical setup flow, enforce mandatory fields, and make `project` explicitly optional.

## Scope

Backend only. No field renames, no DB migrations, no loader changes.

## Changes by Layer

### 1. `AzureDevOpsCredentials` — `src/codemie/rest_api/models/settings.py`

Reorder fields and add validation constraints:

| Field | Type | Constraint | Change |
|---|---|---|---|
| `base_url` | `str` | `Field(min_length=1)` | Add constraint, move to first |
| `organization` | `str` | `Field(min_length=1)` | Add constraint, move to second |
| `project` | `Optional[str]` | `= None` | Was `str`, now optional |
| `access_token` | `str` | `Field(min_length=1)` | Add constraint |

`Field(min_length=1)` rejects empty strings at the Pydantic validation layer. An integration cannot be saved if any mandatory field is blank.

`project` becomes `Optional[str] = None`. Empty string submitted from the API is treated as absent (callers that pass `""` must pass `None` instead, or rely on the service layer coercion described below).

### 2. `get_azure_devops_creds()` — `src/codemie/service/settings/settings.py`

One-line change on the return statement: convert empty string project to `None`.

```python
# Before
return AzureDevOpsCredentials(base_url=base_url, project=project, organization=organization, access_token=token)

# After
return AzureDevOpsCredentials(base_url=base_url, project=project or None, organization=organization, access_token=token)
```

This ensures that legacy stored integrations that have no project value (empty string) produce a valid `AzureDevOpsCredentials` instance rather than a Pydantic validation error.

### 3. `GenericAzureDevOpsConfig` — `src/codemie_tools/azure_devops/generic/models.py`

Add `organization` (required) and `project` (optional) fields. Reorder declarations to match the credential model: `url`, `organization`, `project`, `token`.

```python
url: str = RequiredField(...)           # unchanged
organization: str = RequiredField(description="Azure DevOps organization name")
project: Optional[str] = Field(default=None, description="Azure DevOps project name (optional)")
token: str = RequiredField(...)         # unchanged
```

Field declaration order drives JSON schema order, which drives UI rendering order.

## What is NOT changed

- `base_url` field name in `AzureDevOpsCredentials` and all downstream code
- DB storage key `"url"` and `AZURE_DEVOPS_FIELDS` dict
- Datasource processors (`azure_devops_wiki_datasource_processor.py`, `azure_devops_work_item_datasource_processor.py`)
- Loader constructor signatures
- Tool config `model_validator`s (Wiki / WorkItem / TestPlan / AzureDevOpsGit)
- Customer config YAML keys (`tool_defaults.azuredevops.url`)

## Acceptance Criteria

1. `POST /settings` (and equivalent project settings endpoint) rejects an AzureDevOps credential payload with blank `base_url`, blank `organization`, or blank `access_token` — Pydantic raises `422 Unprocessable Entity`.
2. `POST /settings` accepts a payload with `project` absent or `null` — the credential is saved successfully.
3. The JSON schema for `AzureDevOpsCredentials` lists fields in order: `base_url`, `organization`, `project`, `access_token`.
4. `GenericAzureDevOpsConfig` JSON schema lists fields in order: `url`, `organization`, `project`, `token`.
5. `GenericAzureDevOpsConfig` rejects instantiation with missing `organization`.
6. `get_azure_devops_creds()` returns `project=None` (not `""`) for integrations that have no stored project value.
7. All existing tests pass. New tests cover mandatory-field rejection and optional `project`.

## Testing

New tests needed:
- `tests/codemie/rest_api/models/test_azure_devops_credentials.py` — mandatory field rejection (empty string), optional project, field order in JSON schema.
- `tests/codemie_tools/azure_devops/generic/test_models.py` — new `organization` field required, `project` optional, field order.
- `tests/codemie/service/settings/test_settings_service.py` — `get_azure_devops_creds()` returns `project=None` when stored value is empty string.
