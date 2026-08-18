# SharePoint Standalone Toolkit — Design

**Ticket:** EPMCDME-14219  
**Date:** 2026-08-17

## Summary

Extract the `SharePointTool` from `DataManagementToolkit` into its own top-level `SharePointToolkit`. Move all SharePoint tool source files to a new `src/codemie_tools/sharepoint/` package. Rename the tool to "SharePoint Site Tool".

## Motivation

`data_management/` groups raw data-store integrations (Elasticsearch, SQL). SharePoint is a collaboration/document platform and does not belong there. A standalone toolkit gives it the same top-level status as other integrations (Notification, Cloud, VCS).

## New Directory Structure

```
src/codemie_tools/sharepoint/          ← new top-level package
    __init__.py
    toolkit.py                         ← new
    tools.py                           ← moved from data_management/sharepoint/
    models.py                          ← moved from data_management/sharepoint/
    tools_vars.py                      ← moved from data_management/sharepoint/
```

`src/codemie_tools/data_management/sharepoint/` is deleted entirely.

## Changes

### `src/codemie_tools/base/models.py`

Add `SHAREPOINT = "SharePoint"` to the `ToolSet` enum (consistent with the existing `CredentialTypes.SHAREPOINT` string value).

### `src/codemie_tools/sharepoint/toolkit.py` (new)

```python
class SharePointToolkitUI(ToolKit):
    toolkit: ToolSet = ToolSet.SHAREPOINT
    tools: List[Tool] = [
        Tool.from_metadata(SHAREPOINT_TOOL, tool_class=SharePointTool),
    ]
    label: str = ToolSet.SHAREPOINT.value

class SharePointToolkit(DiscoverableToolkit):
    @classmethod
    def get_definition(cls):
        return SharePointToolkitUI()
```

### `src/codemie_tools/sharepoint/tools_vars.py`

| Field | Before | After |
|---|---|---|
| `name` | `"sharepoint"` | `"sharepoint_site"` |
| `label` | `"SharePoint"` | `"SharePoint Site Tool"` |

All other fields (`description`, `user_description`, `settings_config`, `config_class`) are unchanged.

### `src/codemie_tools/data_management/toolkit.py`

Remove SharePoint imports and the `Tool.from_metadata(SHAREPOINT_TOOL, ...)` entry from `DataManagementToolkitUI`.

### Import path updates (source)

| File | Change |
|---|---|
| `data_management/sharepoint/tools.py` | Self-references updated after move |
| `data_management/sharepoint/tools_vars.py` | Self-references updated after move |
| `codemie/service/settings/settings.py` | Import path updated to `codemie_tools.sharepoint.*` |
| `codemie/service/settings/settings_tester.py` | Import path updated to `codemie_tools.sharepoint.*` |

### Test changes

| File | Change |
|---|---|
| `tests/codemie_tools/data_management/sharepoint/` | Directory relocated to `tests/codemie_tools/sharepoint/`; import paths updated |
| `tests/codemie_tools/data_management/test_data_management_toolkit.py` | Remove `assert "sharepoint" in tool_names` |

No new test files are added.

## Out of Scope

- Changes to the datasource layer (`codemie/datasource/sharepoint/`)
- Changes to the OAuth router or PKCE service
- Changes to the Alembic migration
