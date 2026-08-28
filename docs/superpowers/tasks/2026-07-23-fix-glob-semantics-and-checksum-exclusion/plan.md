# Fix Glob Semantics and Checksum Exclusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three bugs surfaced by code review: broken glob matching in `_path_matches_glob` (right-anchored, `**` non-recursive), missing glob validation, and `checksum` leaking to the LLM via `ExecuteWorkspaceScriptTool`.

**Architecture:** Replace `PurePosixPath.match()` in the service with a custom regex-based glob converter that implements standard glob semantics (`*` = one segment, `**/` = zero-or-more segments). Add a `_validate_glob` guard that raises `ValidationException` for absolute paths and traversal patterns. Patch the independent `_dump_json` in `ExecuteWorkspaceScriptTool` to exclude `checksum` the same way `BaseWorkspaceTool._dump_json` already does.

**Tech Stack:** Python 3.12, `re` (stdlib), `pytest`, `unittest.mock`

---

## File Map

| File | Change |
|---|---|
| `src/codemie/service/agent_workspace_service.py` | Add `import re`; replace `_path_matches_glob`; add `_glob_to_pattern` (private helper); add `_validate_glob`; call `_validate_glob` at top of `list_files` and `grep_files` |
| `src/codemie_tools/data_management/workspace/execute_workspace_script_tool.py` | Add `exclude={"checksum"}` to both `model_dump()` calls in `_dump_json` |
| `tests/codemie/service/test_agent_workspace_glob.py` | New — unit tests for `_path_matches_glob` and `_validate_glob` |
| `tests/codemie_tools/data_management/workspace/test_execute_workspace_script_tool.py` | New — unit test for checksum exclusion in `ExecuteWorkspaceScriptTool._dump_json` |

---

### Task 1: Tests for `_path_matches_glob` (RED)

**Files:**
- Create: `tests/codemie/service/test_agent_workspace_glob.py`

- [ ] **Step 1: Write failing tests for `_path_matches_glob`**

Create `tests/codemie/service/test_agent_workspace_glob.py`:

```python
import pytest
from codemie.service.agent_workspace_service import AgentWorkspaceService

_match = AgentWorkspaceService._path_matches_glob


class TestPathMatchesGlob:
    def test_none_glob_matches_everything(self):
        assert _match("src/foo.py", None) is True
        assert _match("config.py", None) is True

    def test_bare_star_matches_only_root_level(self):
        assert _match("config.py", "*.py") is True
        assert _match("src/config.py", "*.py") is False
        assert _match("src/sub/deep.py", "*.py") is False

    def test_directory_star_matches_direct_children_only(self):
        assert _match("src/foo.py", "src/*.py") is True
        assert _match("src/sub/foo.py", "src/*.py") is False
        # must be left-anchored — project/src/foo.py must NOT match
        assert _match("project/src/foo.py", "src/*.py") is False

    def test_double_star_slash_matches_zero_or_more_segments(self):
        # zero segments: root-level file
        assert _match("foo.py", "**/foo.py") is True
        # one segment
        assert _match("src/foo.py", "**/foo.py") is True
        # two segments
        assert _match("a/b/foo.py", "**/foo.py") is True

    def test_recursive_glob_with_prefix(self):
        assert _match("src/sub/a.ts", "src/**/*.ts") is True
        assert _match("src/a/b/c/a.ts", "src/**/*.ts") is True
        assert _match("src/a.ts", "src/**/*.ts") is True
        # must NOT match outside src/
        assert _match("other/src/a.ts", "src/**/*.ts") is False

    def test_double_star_alone_matches_all(self):
        assert _match("foo.py", "**") is True
        assert _match("src/sub/deep.ts", "**") is True

    def test_src_double_star_matches_all_under_src(self):
        assert _match("src/foo.py", "src/**") is True
        assert _match("src/sub/deep.ts", "src/**") is True
        assert _match("other/foo.py", "src/**") is False

    def test_exact_name_matches(self):
        assert _match("config.py", "config.py") is True
        assert _match("src/config.py", "config.py") is False

    def test_question_mark_matches_single_non_separator_char(self):
        assert _match("foo.py", "fo?.py") is True
        assert _match("fooo.py", "fo?.py") is False
        assert _match("src/foo.py", "fo?.py") is False
```

- [ ] **Step 2: Run tests to verify they FAIL**

```bash
poetry run pytest tests/codemie/service/test_agent_workspace_glob.py -v 2>&1 | tail -30
```

Expected: multiple failures including `test_bare_star_matches_only_root_level` (because `*.py` currently matches `src/sub/deep.py`) and `test_double_star_slash_matches_zero_or_more_segments` (because `**/foo.py` doesn't match root-level `foo.py`).

---

### Task 2: Fix `_path_matches_glob` (GREEN)

**Files:**
- Modify: `src/codemie/service/agent_workspace_service.py`

- [ ] **Step 1: Add `import re` at the top of the file**

In `src/codemie/service/agent_workspace_service.py`, the imports start around line 16. Add `import re` after `import hashlib`:

```python
import hashlib
import mimetypes
import re
from pathlib import PurePosixPath
```

- [ ] **Step 2: Replace `_path_matches_glob` with a regex-based implementation**

Replace lines 661–665 (the existing `_path_matches_glob` static method):

```python
    @staticmethod
    def _glob_to_pattern(glob: str) -> re.Pattern:
        parts = []
        i = 0
        while i < len(glob):
            if glob[i : i + 3] == "**/":
                parts.append("(?:[^/]+/)*")
                i += 3
            elif glob[i : i + 2] == "**":
                parts.append(".*")
                i += 2
            elif glob[i] == "*":
                parts.append("[^/]*")
                i += 1
            elif glob[i] == "?":
                parts.append("[^/]")
                i += 1
            else:
                parts.append(re.escape(glob[i]))
                i += 1
        return re.compile("^" + "".join(parts) + "$")

    @staticmethod
    def _path_matches_glob(file_path: str, glob: str | None) -> bool:
        if glob is None:
            return True
        return bool(AgentWorkspaceService._glob_to_pattern(glob).match(file_path))
```

- [ ] **Step 3: Run tests to verify they PASS**

```bash
poetry run pytest tests/codemie/service/test_agent_workspace_glob.py -v 2>&1 | tail -30
```

Expected: all tests in `TestPathMatchesGlob` pass.

- [ ] **Step 4: Commit**

```bash
git add src/codemie/service/agent_workspace_service.py tests/codemie/service/test_agent_workspace_glob.py
git commit -m "Fix _path_matches_glob: replace PurePosixPath.match with regex-based glob converter"
```

---

### Task 3: Tests for `_validate_glob` (RED)

**Files:**
- Modify: `tests/codemie/service/test_agent_workspace_glob.py`

- [ ] **Step 1: Add failing tests for `_validate_glob`**

Append this class to `tests/codemie/service/test_agent_workspace_glob.py`:

```python
from codemie.core.exceptions import ValidationException


class TestValidateGlob:
    def test_none_is_accepted(self):
        AgentWorkspaceService._validate_glob(None)  # must not raise

    def test_valid_patterns_accepted(self):
        for pattern in ["*.py", "src/*.py", "src/**/*.ts", "**/foo.py", "src/**", "**"]:
            AgentWorkspaceService._validate_glob(pattern)  # must not raise

    def test_absolute_path_rejected(self):
        with pytest.raises(ValidationException):
            AgentWorkspaceService._validate_glob("/etc/**")

    def test_traversal_rejected(self):
        with pytest.raises(ValidationException):
            AgentWorkspaceService._validate_glob("../../*")

    def test_traversal_in_middle_rejected(self):
        with pytest.raises(ValidationException):
            AgentWorkspaceService._validate_glob("src/../other/*.py")
```

- [ ] **Step 2: Run tests to verify they FAIL**

```bash
poetry run pytest tests/codemie/service/test_agent_workspace_glob.py::TestValidateGlob -v 2>&1 | tail -20
```

Expected: `AttributeError: type object 'AgentWorkspaceService' has no attribute '_validate_glob'`.

---

### Task 4: Add `_validate_glob` and wire it into `list_files` / `grep_files` (GREEN)

**Files:**
- Modify: `src/codemie/service/agent_workspace_service.py`

- [ ] **Step 1: Add `_validate_glob` static method**

Add the following static method directly after `_glob_to_pattern` / `_path_matches_glob` (around line 680, before `_get_blob_owner`):

```python
    @staticmethod
    def _validate_glob(glob: str | None) -> None:
        if glob is None:
            return
        normalized = glob.replace("\\", "/")
        if normalized.startswith("/"):
            raise ValidationException("glob must be a relative pattern, not an absolute path")
        for part in normalized.split("/"):
            if part == "..":
                raise ValidationException("glob must not contain path traversal components")
```

- [ ] **Step 2: Call `_validate_glob` at the top of `list_files`**

In `list_files` (around line 204), add the validation call right after fetching the workspace, before any filtering:

```python
    def list_files(
        self,
        workspace_id: str,
        user: User,
        glob: str | None = None,
    ) -> list[WorkspaceFileItemResponse]:
        self._validate_glob(glob)
        workspace = self.get_workspace(workspace_id, user)
        db_files = self.repository.list_files(workspace.id)
        # ... rest unchanged
```

- [ ] **Step 3: Call `_validate_glob` at the top of `grep_files`**

In `grep_files` (around line 321), add the same call:

```python
    def grep_files(
        self,
        workspace_id: str,
        query: str,
        user: User,
        glob: str | None = None,
    ) -> list[WorkspaceGrepMatchResponse]:
        self._validate_glob(glob)
        workspace = self.get_workspace(workspace_id, user)
        # ... rest unchanged
```

- [ ] **Step 4: Run tests to verify they PASS**

```bash
poetry run pytest tests/codemie/service/test_agent_workspace_glob.py -v 2>&1 | tail -30
```

Expected: all tests in both `TestPathMatchesGlob` and `TestValidateGlob` pass.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/agent_workspace_service.py tests/codemie/service/test_agent_workspace_glob.py
git commit -m "Add _validate_glob: reject absolute paths and traversal patterns in list_files/grep_files"
```

---

### Task 5: Test for checksum in `ExecuteWorkspaceScriptTool._dump_json` (RED)

**Files:**
- Create: `tests/codemie_tools/data_management/workspace/test_execute_workspace_script_tool.py`

- [ ] **Step 1: Write failing test for checksum exclusion**

Create `tests/codemie_tools/data_management/workspace/test_execute_workspace_script_tool.py`:

```python
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from codemie.rest_api.models.agent_workspace import (
    ExecuteWorkspaceScriptResponse,
    WorkspaceFileItemResponse,
)
from codemie_tools.data_management.workspace.execute_workspace_script_tool import (
    ExecuteWorkspaceScriptTool,
)


def _make_file_item() -> WorkspaceFileItemResponse:
    return WorkspaceFileItemResponse(
        path="src/foo.py",
        mime_type="text/x-python",
        checksum="abc123",
        size=42,
        version=1,
        update_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class TestExecuteWorkspaceScriptToolDumpJson(unittest.TestCase):
    def _make_tool(self) -> ExecuteWorkspaceScriptTool:
        tool = ExecuteWorkspaceScriptTool.__new__(ExecuteWorkspaceScriptTool)
        return tool

    def test_checksum_excluded_from_workspace_files_in_response(self):
        tool = self._make_tool()
        response = ExecuteWorkspaceScriptResponse(
            output="done",
            workspace_files=[_make_file_item()],
        )
        result = json.loads(tool._dump_json(response))
        self.assertNotIn("checksum", result["workspace_files"][0])

    def test_output_field_still_present(self):
        tool = self._make_tool()
        response = ExecuteWorkspaceScriptResponse(
            output="script output",
            workspace_files=[],
        )
        result = json.loads(tool._dump_json(response))
        self.assertEqual(result["output"], "script output")

    def test_path_and_size_present_in_workspace_files(self):
        tool = self._make_tool()
        response = ExecuteWorkspaceScriptResponse(
            output="",
            workspace_files=[_make_file_item()],
        )
        result = json.loads(tool._dump_json(response))
        file_entry = result["workspace_files"][0]
        self.assertEqual(file_entry["path"], "src/foo.py")
        self.assertEqual(file_entry["size"], 42)
```

- [ ] **Step 2: Run test to verify it FAILS**

```bash
poetry run pytest tests/codemie_tools/data_management/workspace/test_execute_workspace_script_tool.py::TestExecuteWorkspaceScriptToolDumpJson::test_checksum_excluded_from_workspace_files_in_response -v 2>&1 | tail -20
```

Expected: `AssertionError: 'checksum' unexpectedly found in result["workspace_files"][0]`.

---

### Task 6: Fix `ExecuteWorkspaceScriptTool._dump_json` (GREEN)

**Files:**
- Modify: `src/codemie_tools/data_management/workspace/execute_workspace_script_tool.py`

- [ ] **Step 1: Add `exclude={"checksum"}` to both `model_dump()` calls**

Replace lines 275–283 (the existing `_dump_json`):

```python
    @staticmethod
    def _dump_json(payload) -> str:
        if isinstance(payload, list):
            data = [item.model_dump(mode="json", exclude={"checksum"}) if hasattr(item, "model_dump") else item for item in payload]
        elif hasattr(payload, "model_dump"):
            data = payload.model_dump(mode="json", exclude={"checksum"})
        else:
            data = payload
        return json.dumps(data, ensure_ascii=False, indent=2)
```

- [ ] **Step 2: Run all tests to verify they PASS**

```bash
poetry run pytest tests/codemie_tools/data_management/workspace/test_execute_workspace_script_tool.py -v 2>&1 | tail -20
```

Expected: all three tests in `TestExecuteWorkspaceScriptToolDumpJson` pass.

- [ ] **Step 3: Run full test suite to check for regressions**

```bash
poetry run pytest tests/codemie/service/test_agent_workspace_glob.py tests/codemie_tools/data_management/workspace/test_execute_workspace_script_tool.py -v 2>&1 | tail -30
```

Expected: all tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/codemie_tools/data_management/workspace/execute_workspace_script_tool.py tests/codemie_tools/data_management/workspace/test_execute_workspace_script_tool.py
git commit -m "Fix ExecuteWorkspaceScriptTool._dump_json: exclude checksum from LLM-facing output"
```

---

## Self-Review

**Spec coverage:**
- Finding #1 (right-anchored `src/*.py`): covered by `test_directory_star_matches_direct_children_only` + Task 2 implementation ✓
- Finding #2 (`**/foo.py` misses root-level): covered by `test_double_star_slash_matches_zero_or_more_segments` + Task 2 ✓
- Finding #3 (checksum leak in `ExecuteWorkspaceScriptTool`): covered by Tasks 5–6 ✓
- Finding #5 (malformed glob → 200 instead of 400): covered by `TestValidateGlob` + Tasks 3–4 ✓
- Finding #7 (call-site exclusion decision): no source change needed (keeping call-site pattern intentionally; finding #3 achieves consistency) ✓

**Placeholder scan:** No TBD, TODO, or "similar to" references. All code steps show complete code.

**Type consistency:** `_validate_glob(glob: str | None) -> None` defined in Task 4, called in Task 4. `_glob_to_pattern(glob: str) -> re.Pattern` defined in Task 2, used in `_path_matches_glob` in Task 2. `_path_matches_glob` signature unchanged throughout.
