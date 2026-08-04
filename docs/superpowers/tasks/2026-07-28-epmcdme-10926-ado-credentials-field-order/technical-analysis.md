# Technical Research

**Task**: azure devops credentials integration validation schema
**Generated**: 2026-07-28T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

Implement the backend part of EPMCDME-10926: Order of fields for AzureDevOps Credential type in Integrations. The required changes are:
1. Rename the URL field to Hostname in the AzureDevOps credential type (full URL is not supported, only hostname)
2. Reorder fields: Hostname first, then Organization, then Project name, then PAT (Personal Access Token)
3. Make Hostname, Organization, and PAT mandatory fields (currently integration can be saved without Org & PAT)
4. Keep Project name optional (PAT is scoped to Org level, one PAT can be used for all projects)

This is the backend part only — model schemas, validation, API endpoint changes for the AzureDevOps integration credential type.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/rest_api/models/settings.py` (lines 194–198) — `AzureDevOpsCredentials` Pydantic model: current fields are `base_url`, `project`, `organization`, `access_token`; all typed `str` with no min_length or required validators; this is the PRIMARY schema target for field rename, reorder, and mandatory enforcement
- `src/codemie_tools/azure_devops/generic/models.py` — `GenericAzureDevOpsConfig`: the UI-facing credential config class; currently has only `url` and `token` fields (missing `organization` and `project` entirely); field declaration order drives JSON schema order and therefore UI rendering
- `src/codemie_tools/core/vcs/azure_devops_git/models.py` — `AzureDevOpsGitConfig`: fields `url`, `organization`, `project` (Optional), `token`; uses `RequiredField()` for required fields at LangChain validation time
- `src/codemie_tools/azure_devops/wiki/models.py` — `AzureDevOpsWikiConfig`: uses `model_validator(mode="before")` to build `organization_url` from `url` + `organization`
- `src/codemie_tools/azure_devops/work_item/models.py` — `AzureDevOpsWorkItemConfig`: same `organization_url` construction pattern as Wiki config
- `src/codemie_tools/azure_devops/test_plan/models.py` — `AzureDevOpsTestPlanConfig`: same pattern as Wiki and Work Item configs
- `src/codemie/service/settings/settings.py` (line 185) — `AZURE_DEVOPS_FIELDS = {URL: "base_url", PROJECT: "project", ORGANIZATION: "organization", TOKEN: "access_token"}`: maps storage keys (plain strings) to `AzureDevOpsCredentials` field names; the `URL` storage key currently maps to `base_url`
- `src/codemie/service/settings/settings.py` (lines 1222–1286) — `get_azure_devops_creds()`: retrieves `CredentialTypes.AZURE_DEVOPS` settings and hydrates an `AzureDevOpsCredentials` instance
- `src/codemie/datasource/loader/azure_devops_wiki_loader.py` — `AzureDevOpsWikiLoader.__init__`: accepts `base_url`, `organization`, `project`, `access_token`; constructs `{base_url}/{organization}` as the full org URL
- `src/codemie/datasource/loader/azure_devops_work_item_loader.py` — `AzureDevOpsWorkItemLoader`: same URL-construction logic (`{base_url}/{organization}`)
- `src/codemie_tools/base/models.py` — `CredentialTypes.AZURE_DEVOPS` enum value used as the credential type identifier across all layers

### Architecture and Layers Affected

- **REST API layer**: `src/codemie/rest_api/models/settings.py` — `AzureDevOpsCredentials` schema; `src/codemie/rest_api/routers/user_settings.py` and `project_settings.py` — endpoints that accept and return this model
- **Service layer**: `src/codemie/service/settings/settings.py` — `SettingsService.AZURE_DEVOPS_FIELDS` storage key map and `get_azure_devops_creds()` hydration method
- **Tool/config layer**: `src/codemie_tools/azure_devops/generic/models.py` — `GenericAzureDevOpsConfig` (UI credential form schema); `src/codemie_tools/azure_devops/wiki/models.py`, `work_item/models.py`, `test_plan/models.py`, `core/vcs/azure_devops_git/models.py` — all consume `url`/`organization`/`project`/`token` via `model_validator` or `AliasChoices`
- **Datasource/loader layer**: `src/codemie/datasource/loader/azure_devops_wiki_loader.py`, `azure_devops_work_item_loader.py` — construct org URL as `{base_url}/{organization}`; if `base_url` becomes a bare hostname, these must prepend `https://dev.azure.com/`

### Integration Points

- `SettingsService.AZURE_DEVOPS_FIELDS` dict is the single mapping between the DB storage key `"url"` and the Python field `base_url`; renaming the storage key to `"hostname"` or the Python field to `hostname` requires updating this dict and all callers
- Both loaders build `{base_url}/{organization}` — the assumption is that `base_url` is a full URL (e.g. `https://dev.azure.com`); if the field is renamed to `hostname` and stores only the bare hostname (e.g. `dev.azure.com`), the loaders must prepend the scheme
- Four tool configs (`Wiki`, `WorkItem`, `TestPlan`, `AzureDevOpsGit`) all use a `model_validator(mode="before")` that reads `url` or `base_url`; any alias change cascades to all four validators
- `GenericAzureDevOpsConfig` is the credential form driver — it must gain `organization` (required) and `project` (optional) fields in addition to renaming `url` to `hostname`

### Patterns and Conventions

- Pydantic field declaration order = JSON schema field order = UI rendering order; reordering requires physically reordering the field declarations in the model class
- `RequiredField()` is the project convention for marking fields as required in tool-layer configs (used in `AzureDevOpsGitConfig`); for REST API models, mandatory enforcement is done via Pydantic field constraints (`min_length=1` or removing `Optional`)
- `model_validator(mode="before")` is used in Wiki/WorkItem/TestPlan configs to construct `organization_url` from `url` + `organization`; this pattern must be preserved or adapted if the storage key is renamed
- Credentials are stored as `List[CredentialValues]` (key-value pairs) in the DB; field names are plain string keys matched by `AZURE_DEVOPS_FIELDS`
- `AliasChoices` is used in some tool configs to accept multiple input key names for the same field — a useful pattern for backward-compatible renaming

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/api/rest-api-patterns.md` — FastAPI router and Pydantic model patterns; relevant for any schema changes to `AzureDevOpsCredentials`
- `.ai-run/guides/architecture/layered-architecture.md` — layer boundary rules; confirms REST API models are the boundary for external-facing schema
- `.ai-run/guides/standards/code-quality.md` — Ruff/Python standards that apply to all edits

### Architectural Decisions

- EPMCDME-10928 (recent commit): `DEFAULT_AZURE_DEVOPS_TOOL_URL` and `customer_config.tool_defaults["azuredevops"]["url"]` were introduced as configurable defaults — this means the `url` field has a configured default path; renaming to `hostname` must account for whether the default value is a full URL or a bare hostname
- Credential storage uses key-value pairs deliberately (not a structured column per field) to allow flexible schema evolution without DB migrations for new fields; field renames do require updating the key constant

### Derived Conventions

- Mandatory string fields in REST API Pydantic models use `str` type with no `Optional` wrapper and, where enforcement is needed, a `Field(min_length=1)` or `@field_validator` to reject empty strings
- Optional fields use `str | None = None` or `Optional[str] = None`
- Field rename with backward compatibility is achieved via `AliasChoices` or a `model_validator` alias, not by adding a DB migration (since credentials are stored as key-value strings, not structured columns)

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie_tools/azure_devops/generic/test_models.py` — covers `GenericAzureDevOpsConfig` with `url` and `token` fields only; no tests for `organization` or `project`
- `tests/codemie_tools/core/vcs/azure_devops_git/test_models.py` — covers `AzureDevOpsGitConfig` fields, defaults, and required-field validation
- `tests/codemie_tools/azure_devops/wiki/test_config_mapping.py` — covers `AzureDevOpsWikiConfig` mapping from `url` + `organization` → `organization_url`
- `tests/codemie_tools/azure_devops/work_item/test_config_mapping.py` — same mapping for Work Item config
- `tests/codemie_tools/azure_devops/test_plan/test_config_mapping.py` — same mapping for Test Plan config
- `tests/codemie/service/settings/test_settings_service.py` — covers `SettingsService` including `get_azure_devops_creds()` credential retrieval
- `tests/codemie/datasource/loader/test_azure_devops_wiki_loader.py` — covers loader initialization with `base_url` + `organization`
- `tests/codemie/datasource/loader/test_azure_devops_work_item_loader.py` — same for work item loader

### Testing Framework and Patterns

- pytest (project standard)
- Config mapping tests instantiate the model class directly with a dict and assert field values
- Loader tests mock the Azure DevOps SDK client and assert URL construction
- `SettingsService` tests use fixtures with mock DB sessions and pre-populated `CredentialValues` lists

### Coverage Gaps

- `GenericAzureDevOpsConfig`: no tests for `organization` field (mandatory), `project` field (optional), or field order in JSON schema — all must be added
- No test verifies that `AzureDevOpsCredentials` rejects an empty `organization` or empty `access_token` at the REST API boundary
- No test verifies that `AzureDevOpsWikiLoader` and `AzureDevOpsWorkItemLoader` correctly prepend `https://dev.azure.com/` when given a bare hostname instead of a full URL
- `test_config_mapping.py` files for Wiki/WorkItem/TestPlan configs would need updating if the `url` → `hostname` storage key rename propagates to those validators

---

## 5. Configuration and Environment

### Environment Variables

- `DEFAULT_AZURE_DEVOPS_TOOL_URL` — configurable default value for the ADO URL/hostname field, introduced in EPMCDME-10928; if this is a full URL, it must be updated to a bare hostname or the loader layer must normalize it
- `AZURE_DEVOPS_CACHE_DIR` — runtime cache directory for wiki/work item/test plan tools; not directly affected

### Configuration Files

- `src/codemie/configs/config.py` (line 400) — `AZURE_DEVOPS_REPOS_IDENTIFIERS`: regex/string patterns used to detect ADO repository URLs; may need updating if hostname format changes
- `customer_config.tool_defaults["azuredevops"]["url"]` and `["url_placeholder"]` — customer-configurable UI defaults for the URL/hostname field; key name `"url"` would need updating if the storage key is renamed to `"hostname"`

### Feature Flags and Deployment Concerns

- No feature flags found for this domain
- No DB migration required — credentials are stored as `List[CredentialValues]` (key-value pairs); however, existing stored credentials that have key `"url"` will not be found if the service layer starts looking for key `"hostname"` — backward compatibility via dual-key lookup or a one-time data migration script must be considered
- `customer_config` YAML/JSON files in customer deployments reference `tool_defaults["azuredevops"]["url"]`; a key rename would break existing customer config files

---

## 6. Risk Indicators

- **Backward compatibility — stored credential key**: `AZURE_DEVOPS_FIELDS` maps storage key `"url"` to `base_url`; renaming the storage key to `"hostname"` will silently break all existing stored integrations unless dual-key lookup or a migration is added in `SettingsService.get_azure_devops_creds()`
- **Backward compatibility — customer config**: `customer_config.tool_defaults["azuredevops"]["url"]` and `["url_placeholder"]` are customer-facing YAML keys; renaming requires either a deprecation alias or coordinated customer config update
- **Loader URL construction**: both `AzureDevOpsWikiLoader` and `AzureDevOpsWorkItemLoader` build `{base_url}/{organization}`; if `base_url` stores a bare hostname (`dev.azure.com`), the loaders will produce `dev.azure.com/my-org` instead of `https://dev.azure.com/my-org` — this is a runtime breakage that would affect all existing datasource processing
- **`GenericAzureDevOpsConfig` is the UI schema driver**: it is missing `organization` and `project` fields entirely; adding them is required for the fields to appear in the credential form, but their addition may also affect any code that instantiates `GenericAzureDevOpsConfig` with only `url` + `token`
- **Four tool configs share the `url`/`organization` pattern**: `AzureDevOpsWikiConfig`, `AzureDevOpsWorkItemConfig`, `AzureDevOpsTestPlanConfig`, and `AzureDevOpsGitConfig` all have `model_validator(mode="before")` that reads `url` — if the storage key changes, all four validators must be updated or extended with `AliasChoices`
- **No mandatory-field enforcement at REST boundary**: `AzureDevOpsCredentials.organization` and `.access_token` are currently plain `str` with no `min_length` validator; empty-string submissions would pass validation — `Field(min_length=1)` or a `@field_validator` must be added
- **Test coverage gap for `GenericAzureDevOpsConfig`**: existing tests only cover `url` + `token`; new tests needed for all four fields in the updated model, field ordering in JSON schema, and mandatory enforcement
- **`DEFAULT_AZURE_DEVOPS_TOOL_URL` env var**: its current value format (full URL vs. hostname) is unknown without reading the `.env.example`; if it is a full URL, it cannot be used as a direct `hostname` default without normalization logic

---

## 7. Summary for Complexity Assessment

This task touches four architectural layers: the REST API model layer (`AzureDevOpsCredentials` in `rest_api/models/settings.py`), the service layer (`SettingsService.AZURE_DEVOPS_FIELDS` dict and `get_azure_devops_creds()`), the tool/config layer (`GenericAzureDevOpsConfig` and four other tool config classes), and the datasource/loader layer (`AzureDevOpsWikiLoader` and `AzureDevOpsWorkItemLoader`). The minimum file change surface for a correct implementation is approximately 8–10 files: the REST API model, the service layer field map, `GenericAzureDevOpsConfig`, up to four tool config model validators (if the storage key is renamed), and the two loaders (to handle bare hostname → full URL normalization). The UI field order change alone (purely cosmetic) requires only reordering declarations in `AzureDevOpsCredentials` and `GenericAzureDevOpsConfig`, but mandatory enforcement and the `url` → `hostname` rename expand the surface significantly.

The task introduces one genuinely novel concern: backward compatibility with existing stored credentials. All live integrations have `"url"` as their DB storage key; renaming it to `"hostname"` without a dual-key lookup or migration would silently break them. This is not a pattern that has precedent in recent commits (EPMCDME-10928 added config defaults but did not rename any existing storage keys), so the implementation team must decide between: (a) keeping the storage key as `"url"` while only renaming the Python field to `hostname` via an alias, or (b) introducing a dual-key lookup in `SettingsService` with a deprecation path. The loader URL construction change (prepend `https://dev.azure.com/` for bare hostnames) is also non-trivial because it changes runtime behavior for all datasource processing jobs, not just the credential form.

Test coverage for the affected area is moderate: the config mapping tests and loader tests exist and would catch regressions in the tool-layer validators and loader URL construction respectively, but there are no API-layer tests that validate mandatory-field enforcement for `AzureDevOpsCredentials`, and `GenericAzureDevOpsConfig` tests cover only the two current fields. New tests are needed for mandatory `organization` and `access_token` enforcement, the optional `project` field, and loader behavior with bare hostnames. Overall complexity is medium-high due to the multi-layer surface, the backward compatibility risk for stored credentials, and the loader URL construction change.
