# Plan: EPMCDME-7070 — Disable Chat File Attachment per Assistant and Project

## Requirements

Add a `file_attachment_enabled` boolean flag to both the `Assistant` and `Application`
(Project) models. When set to `False` on an assistant or its owning project, any chat
request that includes `file_names` is rejected with HTTP 403. The flag defaults to
`None` (i.e. attachment is allowed unless explicitly disabled).

Scope: backend only. No frontend changes. No customer-level feature flag.

---

## Tasks

### T1 — Add `file_attachment_enabled` field to models

**Files**:
- `src/codemie/rest_api/models/assistant.py`
- `src/codemie/core/models.py`

**Changes**:

1. `AssistantRequest` (after `smart_tool_selection_enabled` at line 331):
   ```python
   file_attachment_enabled: Optional[bool] = None
   ```

2. `AssistantBase` (after `smart_tool_selection_enabled` at line 657):
   ```python
   file_attachment_enabled: Optional[bool] = None
   ```

3. `AssistantConfiguration` (after `enable_image_generation` at line 1249):
   ```python
   file_attachment_enabled: Optional[bool] = None
   ```

4. `Application` in `core/models.py` (after `deleted_at` at line 393):
   ```python
   file_attachment_enabled: Optional[bool] = SQLField(default=None)
   ```

**Test-first**: no — model field additions are schema changes tested via migration and indirectly via T3.

---

### T2 — Create Alembic migration

**File**: `src/external/alembic/versions/<new_revision>_add_file_attachment_enabled.py`

Add `file_attachment_enabled` (Boolean, nullable, no server_default) to three tables:
`assistants`, `assistant_configurations`, `applications`.

```python
revision: str = '<new_id>'
down_revision: Union[str, None] = 't1u2v3w4x5y6'

def upgrade() -> None:
    op.add_column('assistants', sa.Column('file_attachment_enabled', sa.Boolean(), nullable=True))
    op.add_column('assistant_configurations', sa.Column('file_attachment_enabled', sa.Boolean(), nullable=True))
    op.add_column('applications', sa.Column('file_attachment_enabled', sa.Boolean(), nullable=True))

def downgrade() -> None:
    op.drop_column('assistants', 'file_attachment_enabled')
    op.drop_column('assistant_configurations', 'file_attachment_enabled')
    op.drop_column('applications', 'file_attachment_enabled')
```

**Test-first**: no — migrations are tested by running them.

---

### T3 — Add validation function to `assistant.py`

**File**: `src/codemie/rest_api/routers/assistant.py`

1. Add `Application` to the `from codemie.core.models import (...)` block.
2. Add a new private function `_validate_file_attachment_allowed_and_raise` (near the other `_validate_*` functions after line 2349):
   ```python
   def _validate_file_attachment_allowed_and_raise(
       assistant: Assistant, file_names: Optional[list[str]]
   ) -> None:
       if not file_names:
           return
       if assistant.file_attachment_enabled is False:
           raise ExtendedHTTPException(
               code=status.HTTP_403_FORBIDDEN,
               message="File attachment not allowed",
               details="File uploads are disabled for this assistant.",
               help="Contact your administrator to enable file attachment for this assistant.",
           )
       if assistant.project:
           project = Application.get_by_id(assistant.project)
           if project and project.file_attachment_enabled is False:
               raise ExtendedHTTPException(
                   code=status.HTTP_403_FORBIDDEN,
                   message="File attachment not allowed",
                   details="File uploads are disabled for this project.",
                   help="Contact your administrator to enable file attachment for this project.",
               )
   ```
3. Call `_validate_file_attachment_allowed_and_raise(assistant, request.file_names)` at both call sites (lines 2196 and 2270), immediately after the existing `_validate_assistant_supports_files_and_raise` call.

**Test-first**: yes — write failing tests in T4 before adding the function body.

---

### T4 — Unit tests for the validation function

**File**: `tests/unit/routers/test_assistant_file_attachment.py`

Use direct function call pattern (same as `test_analytics_enriched_user.py`):

```python
from codemie.rest_api.routers.assistant import _validate_file_attachment_allowed_and_raise
from codemie.core.exceptions import ExtendedHTTPException
from fastapi import status
from unittest.mock import MagicMock, patch
import pytest

class TestValidateFileAttachmentAllowed:
    def test_no_files_always_passes(self):
        # No exception when file_names is empty/None
        assistant = MagicMock(file_attachment_enabled=False)
        _validate_file_attachment_allowed_and_raise(assistant, None)
        _validate_file_attachment_allowed_and_raise(assistant, [])

    def test_assistant_disabled_raises_403(self):
        assistant = MagicMock(file_attachment_enabled=False)
        with pytest.raises(ExtendedHTTPException) as exc_info:
            _validate_file_attachment_allowed_and_raise(assistant, ["file.txt"])
        assert exc_info.value.code == status.HTTP_403_FORBIDDEN

    def test_assistant_enabled_does_not_raise(self, mocker):
        assistant = MagicMock(file_attachment_enabled=True, project=None)
        _validate_file_attachment_allowed_and_raise(assistant, ["file.txt"])  # no raise

    def test_assistant_not_configured_does_not_raise(self, mocker):
        assistant = MagicMock(file_attachment_enabled=None, project=None)
        _validate_file_attachment_allowed_and_raise(assistant, ["file.txt"])  # no raise

    def test_project_disabled_raises_403(self, mocker):
        mock_project = MagicMock(file_attachment_enabled=False)
        mocker.patch(
            "codemie.rest_api.routers.assistant.Application.get_by_id",
            return_value=mock_project,
        )
        assistant = MagicMock(file_attachment_enabled=None, project="my-project")
        with pytest.raises(ExtendedHTTPException) as exc_info:
            _validate_file_attachment_allowed_and_raise(assistant, ["file.txt"])
        assert exc_info.value.code == status.HTTP_403_FORBIDDEN

    def test_project_enabled_does_not_raise(self, mocker):
        mock_project = MagicMock(file_attachment_enabled=True)
        mocker.patch(
            "codemie.rest_api.routers.assistant.Application.get_by_id",
            return_value=mock_project,
        )
        assistant = MagicMock(file_attachment_enabled=None, project="my-project")
        _validate_file_attachment_allowed_and_raise(assistant, ["file.txt"])  # no raise
```

**Test-first**: yes — T4 is written before T3's function body is implemented.

---

## Delivery order

1. T4 (write failing tests) → RED
2. T3 (add validation function + call sites + import) → GREEN
3. T1 (add model fields)
4. T2 (create migration)
5. Remove `tests/unit/routers/test_files_chatfileattachment.py` (incorrect approach, now obsoleted)
6. Run tests

## Files changed

| File | Change |
|---|---|
| `src/codemie/rest_api/models/assistant.py` | Add `file_attachment_enabled` to `AssistantRequest`, `AssistantBase`, `AssistantConfiguration` |
| `src/codemie/core/models.py` | Add `file_attachment_enabled` to `Application` |
| `src/external/alembic/versions/<new>_add_file_attachment_enabled.py` | New migration: add column to 3 tables |
| `src/codemie/rest_api/routers/assistant.py` | Import `Application`, add `_validate_file_attachment_allowed_and_raise`, call it at 2 sites |
| `tests/unit/routers/test_assistant_file_attachment.py` | New unit tests for the validation function |
| `tests/unit/routers/test_files_chatfileattachment.py` | Delete (incorrect earlier approach) |
