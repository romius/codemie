# Configurable File Datasource Upload Limit + Batched File Saving Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the File Datasource upload-count limit configurable (default stays 10) for both create and update endpoints, and save uploaded files concurrently instead of one-at-a-time so raising the limit to hundreds of files doesn't degrade request latency.

**Architecture:** One new `Config` attribute (`FILE_DATASOURCE_MAX_UPLOAD_COUNT`) replaces two hardcoded `ClassVar[int] = 10` constants read by the two `validate_files_count` Pydantic validators. One new `FileDatasourceService.process_files_batch` static method wraps the existing per-file `_process_upload_file` in a bounded `ThreadPoolExecutor(max_workers=3)` and replaces the two duplicated sequential `for file in files:` loops (router create path, service update path) with a single call each.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2 `field_validator`, `pydantic_settings.BaseSettings`, `concurrent.futures.ThreadPoolExecutor`, pytest, `unittest.mock`.

## Global Constraints

- Default file-count limit stays **10** — only the ceiling becomes configurable, via `Config.FILE_DATASOURCE_MAX_UPLOAD_COUNT: int = 10`, env-var overridable like every other `Config` attribute.
- No enforced upper ceiling in code on the new config value — a plain configurable int, no `le=`/max validation.
- Global config setting only — no per-client / per-datasource override.
- ZIP archive extraction limits (`_ZIP_MAX_FILE_COUNT`, `_ZIP_MAX_UNCOMPRESSED_BYTES` in `zip_utils.py`) are out of scope and must not be touched.
- Error-handling semantics from the caller's perspective are unchanged: a bad file still raises `ZipExtractionError`, which still surfaces as HTTP 422 at the router boundary.
- `process_files_batch` must preserve input order in its returned `(paths, filenames)` regardless of which file's thread finishes first.

---

## Task 1: Add configurable upload-count setting to `Config`

**Files:**
- Modify: `src/codemie/configs/config.py:139` (insert new line after this one)
- Test: `tests/codemie/configs/test_config.py` (create if it doesn't already cover simple attribute defaults; otherwise this task's test step may be skipped if an equivalent generic default-value test convention already exists — see Step 1)

**Interfaces:**
- Produces: `config.FILE_DATASOURCE_MAX_UPLOAD_COUNT: int` (default `10`), importable via `from codemie.configs import config` and monkeypatchable with `monkeypatch.setattr(config, "FILE_DATASOURCE_MAX_UPLOAD_COUNT", <value>)`. Tasks 2, 3, and 4 depend on this attribute existing.

**Test-first:** yes — a failing test asserting `config.FILE_DATASOURCE_MAX_UPLOAD_COUNT == 10` by default.

- [ ] **Step 1: Check for an existing config-defaults test file**

Run: `ls tests/codemie/configs/ 2>/dev/null || echo "no dir"`

If `tests/codemie/configs/test_config.py` does not exist, create it fresh with the content in Step 2 (add the license header used elsewhere in this repo, matching `tests/codemie/rest_api/models/test_index_models.py`'s header). If it exists, add the test function from Step 2 to it instead of creating a new file, keeping any existing tests in that file untouched.

- [ ] **Step 2: Write the failing test**

```python
# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
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

from codemie.configs import config


def test_file_datasource_max_upload_count_defaults_to_ten():
    assert config.FILE_DATASOURCE_MAX_UPLOAD_COUNT == 10
```

(Omit the license header if the file already exists and already has one at the top — just append the test function.)

- [ ] **Step 3: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/configs/test_config.py::test_file_datasource_max_upload_count_defaults_to_ten -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'FILE_DATASOURCE_MAX_UPLOAD_COUNT'`

- [ ] **Step 4: Add the setting**

In `src/codemie/configs/config.py`, change:

```python
    FILES_STORAGE_MAX_UPLOAD_SIZE: int = 100 * 1024 * 1024  # 100 MB
    IMAGE_INDEXING_MAX_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB
```

to:

```python
    FILES_STORAGE_MAX_UPLOAD_SIZE: int = 100 * 1024 * 1024  # 100 MB
    FILE_DATASOURCE_MAX_UPLOAD_COUNT: int = 10
    IMAGE_INDEXING_MAX_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB
```

- [ ] **Step 5: Run test to verify it passes**

Run: `poetry run pytest tests/codemie/configs/test_config.py::test_file_datasource_max_upload_count_defaults_to_ten -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/codemie/configs/config.py tests/codemie/configs/test_config.py
git commit -m "EPMCDME-13212: Add configurable FILE_DATASOURCE_MAX_UPLOAD_COUNT setting"
```

---

## Task 2: Make `UpdateKnowledgeBaseFileRequest.validate_files_count` use the config setting

**Files:**
- Modify: `src/codemie/rest_api/models/index.py:1550-1565`
- Test: `tests/codemie/rest_api/models/test_index_models.py` (new `TestUpdateKnowledgeBaseFileRequest` class — none exists today)

**Interfaces:**
- Consumes: `config.FILE_DATASOURCE_MAX_UPLOAD_COUNT` (Task 1).
- Produces: no new interface; `UpdateKnowledgeBaseFileRequest.validate_files_count` now reads the limit from config instead of `cls.MAX_FILE_COUNT`.

**Test-first:** yes — three failing tests: at-default-limit succeeds, above-default-limit raises, and a monkeypatched higher limit succeeds where the default would have failed.

- [ ] **Step 1: Write the failing tests**

Add to `tests/codemie/rest_api/models/test_index_models.py`. First, update the import block at the top of the file (currently lines 15-25) to add the `config` import and the `UpdateKnowledgeBaseFileRequest` import:

```python
import pytest
from unittest.mock import MagicMock
from fastapi import UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from codemie.configs import config
from codemie.rest_api.models.index import (
    IndexKnowledgeBaseFileTypes,
    IndexKnowledgeBaseFileRequest,
    IndexKnowledgeBaseRequest,
    UpdateKnowledgeBaseFileRequest,
)
```

Then append this new test class after `TestIndexKnowledgeBaseFileRequest` (i.e. after line 130, before `class TestIndexKnowledgeBaseRequest:`):

```python
class TestUpdateKnowledgeBaseFileRequest:
    @pytest.fixture
    def mock_attrs(self):
        return {
            'name': 'test_name',
            'project_name': 'test_project_name',
        }

    def test_validate_files_count_at_default_limit_ok(self, mock_attrs):
        mock_files = [MagicMock(spec=UploadFile) for _ in range(10)]
        for f in mock_files:
            f.size = 1024

        assert UpdateKnowledgeBaseFileRequest(**mock_attrs, files=mock_files)

    def test_validate_files_count_above_default_limit_raises(self, mock_attrs):
        mock_files = [MagicMock(spec=UploadFile) for _ in range(11)]

        with pytest.raises(RequestValidationError):
            UpdateKnowledgeBaseFileRequest(**mock_attrs, files=mock_files)

    def test_validate_files_count_respects_higher_configured_limit(self, mock_attrs, monkeypatch):
        monkeypatch.setattr(config, "FILE_DATASOURCE_MAX_UPLOAD_COUNT", 50)
        mock_files = [MagicMock(spec=UploadFile) for _ in range(20)]
        for f in mock_files:
            f.size = 1024

        assert UpdateKnowledgeBaseFileRequest(**mock_attrs, files=mock_files)
```

- [ ] **Step 2: Run tests to verify they fail as expected**

Run: `poetry run pytest tests/codemie/rest_api/models/test_index_models.py::TestUpdateKnowledgeBaseFileRequest -v`
Expected: `test_validate_files_count_at_default_limit_ok` and `test_validate_files_count_respects_higher_configured_limit` FAIL (default `cls.MAX_FILE_COUNT` is still `10`, so today's code is correct for 10 files coincidentally — to actually observe a failing test first, note that `test_validate_files_count_respects_higher_configured_limit` FAILs today because monkeypatching `config.FILE_DATASOURCE_MAX_UPLOAD_COUNT` has no effect yet: `cls.MAX_FILE_COUNT` is still hardcoded to `10`, so 20 files still raises `RequestValidationError` and the `assert` never reached). `test_validate_files_count_above_default_limit_raises` PASSes already (11 > 10 hardcoded) — that one is the regression check, not the new-behavior check.

- [ ] **Step 3: Update the validator**

In `src/codemie/rest_api/models/index.py`, change:

```python
    MAX_FILE_COUNT: ClassVar[int] = 10

    @field_validator("files")
    @classmethod
    def validate_files_count(cls, files: Optional[List[UploadFile]]) -> Optional[List[UploadFile]]:
        if files and len(files) > cls.MAX_FILE_COUNT:
            raise RequestValidationError(
                [
                    {
                        "loc": ["files"],
                        "msg": f"Too many files. Maximum count is {cls.MAX_FILE_COUNT}",
                        "type": "value_error",
                    }
                ]
            )
        return files
```

to:

```python
    @field_validator("files")
    @classmethod
    def validate_files_count(cls, files: Optional[List[UploadFile]]) -> Optional[List[UploadFile]]:
        if files and len(files) > config.FILE_DATASOURCE_MAX_UPLOAD_COUNT:
            raise RequestValidationError(
                [
                    {
                        "loc": ["files"],
                        "msg": f"Too many files. Maximum count is {config.FILE_DATASOURCE_MAX_UPLOAD_COUNT}",
                        "type": "value_error",
                    }
                ]
            )
        return files
```

(This deletes the `MAX_FILE_COUNT: ClassVar[int] = 10` line entirely — it is now unused on this class.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/models/test_index_models.py::TestUpdateKnowledgeBaseFileRequest -v`
Expected: all 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/models/index.py tests/codemie/rest_api/models/test_index_models.py
git commit -m "EPMCDME-13212: Make update-file-datasource upload limit configurable"
```

---

## Task 3: Make `IndexKnowledgeBaseFileRequest.validate_files_count` use the config setting

**Files:**
- Modify: `src/codemie/rest_api/models/index.py:1727-1753`
- Test: `tests/codemie/rest_api/models/test_index_models.py` (extend `TestIndexKnowledgeBaseFileRequest`)

**Interfaces:**
- Consumes: `config.FILE_DATASOURCE_MAX_UPLOAD_COUNT` (Task 1).
- Produces: no new interface; `IndexKnowledgeBaseFileRequest.validate_files_count` now reads the max from config while `MIN_FILE_COUNT` stays a `ClassVar` (unchanged, out of scope).

**Test-first:** yes — the existing `test_validate_files_count_high` must still pass unchanged (regression), plus a new failing test proving a monkeypatched higher config limit is honored.

- [ ] **Step 1: Write the failing test**

Add this test method to the existing `TestIndexKnowledgeBaseFileRequest` class in `tests/codemie/rest_api/models/test_index_models.py`, directly after `test_validate_files_count_high` (currently ending at line 86):

```python
    def test_validate_files_count_respects_higher_configured_limit(self, mock_attrs, monkeypatch):
        monkeypatch.setattr(config, "FILE_DATASOURCE_MAX_UPLOAD_COUNT", 50)
        mock_files = [MagicMock(spec=UploadFile) for _ in range(20)]
        for f in mock_files:
            f.size = 1024
            f.filename = 'test_file.txt'

        assert IndexKnowledgeBaseFileRequest(**mock_attrs, files=mock_files)
```

(The `config` import was already added in Task 2, Step 1 — no further import changes needed here.)

- [ ] **Step 2: Run test to verify it fails**

Run: `poetry run pytest tests/codemie/rest_api/models/test_index_models.py::TestIndexKnowledgeBaseFileRequest::test_validate_files_count_respects_higher_configured_limit -v`
Expected: FAIL — `cls.MAX_FILE_COUNT` is still hardcoded to `10`, so 20 files raises `RequestValidationError` and the `assert` is never reached.

Also confirm the regression test still passes before touching the code:

Run: `poetry run pytest tests/codemie/rest_api/models/test_index_models.py::TestIndexKnowledgeBaseFileRequest::test_validate_files_count_high -v`
Expected: PASS (11 mock files > hardcoded 10 today)

- [ ] **Step 3: Update the validator**

In `src/codemie/rest_api/models/index.py`, change:

```python
    MIN_FILE_COUNT: ClassVar[int] = 1
    MAX_FILE_COUNT: ClassVar[int] = 10

    @field_validator("files")
    def validate_files_count(cls, files):
        if len(files) < cls.MIN_FILE_COUNT:
            raise RequestValidationError(
                [
                    {
                        "loc": ["files"],
                        "msg": f"Too few files. Minimum count is {cls.MIN_FILE_COUNT}",
                        "type": "value_error",
                    }
                ]
            )

        if len(files) > cls.MAX_FILE_COUNT:
            raise RequestValidationError(
                [
                    {
                        "loc": ["files"],
                        "msg": f"Too many files. Maximum count is {cls.MAX_FILE_COUNT}",
                        "type": "value_error",
                    }
                ]
            )
        return files
```

to:

```python
    MIN_FILE_COUNT: ClassVar[int] = 1

    @field_validator("files")
    def validate_files_count(cls, files):
        if len(files) < cls.MIN_FILE_COUNT:
            raise RequestValidationError(
                [
                    {
                        "loc": ["files"],
                        "msg": f"Too few files. Minimum count is {cls.MIN_FILE_COUNT}",
                        "type": "value_error",
                    }
                ]
            )

        if len(files) > config.FILE_DATASOURCE_MAX_UPLOAD_COUNT:
            raise RequestValidationError(
                [
                    {
                        "loc": ["files"],
                        "msg": f"Too many files. Maximum count is {config.FILE_DATASOURCE_MAX_UPLOAD_COUNT}",
                        "type": "value_error",
                    }
                ]
            )
        return files
```

(This deletes the `MAX_FILE_COUNT: ClassVar[int] = 10` line — `MIN_FILE_COUNT` is untouched, per spec.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/models/test_index_models.py::TestIndexKnowledgeBaseFileRequest -v`
Expected: all tests in the class PASS, including the unchanged `test_validate_files_count_high` regression test and the new test.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/rest_api/models/index.py tests/codemie/rest_api/models/test_index_models.py
git commit -m "EPMCDME-13212: Make create-file-datasource upload limit configurable"
```

---

## Task 4: Add `FileDatasourceService.process_files_batch`

**Files:**
- Modify: `src/codemie/service/datasource/file_datasource_service.py` (add import at top; add new static method between `_process_upload_file` (ends line 290) and `upload_and_prepare_files` (starts line 292))
- Test: `tests/codemie/service/datasource/test_file_datasource_service.py` (new `TestProcessFilesBatch` class)

**Interfaces:**
- Consumes: `FileDatasourceService._process_upload_file(file, user_id, file_repo) -> tuple[list[FILE_PATH_DATA_NT], list[str]]` (existing, unchanged, defined at `file_datasource_service.py:250-290`).
- Produces: `FileDatasourceService.process_files_batch(files: list[UploadFile], user_id: str, file_repo) -> tuple[list[FILE_PATH_DATA_NT], list[str]]`. Tasks 5 and 6 call this directly. Returns `([], [])` for an empty `files` list. Raises whatever `_process_upload_file` raises (e.g. `ZipExtractionError`) the first time a failing result is consumed during iteration. Preserves the order of `files` in both output lists regardless of thread-completion order.

**Test-first:** yes — three failing tests: empty input returns empty lists, output order matches input order even when files complete out of order, and an exception from one file's processing propagates out of the call.

- [ ] **Step 1: Write the failing tests**

Update the import block at the top of `tests/codemie/service/datasource/test_file_datasource_service.py` (currently lines 17-29) from:

```python
import io
import json
import zipfile

import pytest
from elasticsearch.exceptions import NotFoundError
from fastapi import status
from unittest.mock import MagicMock, patch

from codemie.core.exceptions import ExtendedHTTPException
from codemie.rest_api.security.user import User
from codemie.service.datasource.file_datasource_service import FileDatasourceService, _validate_json_file
from codemie.service.datasource.zip_utils import ZipExtractionError, expand_zip_file
```

to:

```python
import io
import json
import time
import zipfile

import pytest
from elasticsearch.exceptions import NotFoundError
from fastapi import UploadFile, status
from unittest.mock import MagicMock, patch

from codemie.core.exceptions import ExtendedHTTPException
from codemie.datasource.file.file_datasource_processor import FILE_PATH_DATA_NT
from codemie.rest_api.security.user import User
from codemie.service.datasource.file_datasource_service import FileDatasourceService, _validate_json_file
from codemie.service.datasource.zip_utils import ZipExtractionError, expand_zip_file
```

Then append this new test class at the end of the file (after `TestUploadAndPrepareFilesZip`, i.e. after the current final line 700):

```python


# ---------------------------------------------------------------------------
# process_files_batch
# ---------------------------------------------------------------------------


class TestProcessFilesBatch:
    def _make_upload_file(self, filename):
        f = MagicMock(spec=UploadFile)
        f.filename = filename
        return f

    def test_empty_files_returns_empty_lists(self):
        paths, filenames = FileDatasourceService.process_files_batch([], "user1", MagicMock())
        assert paths == []
        assert filenames == []

    def test_preserves_input_order_even_when_files_complete_out_of_order(self):
        files = [self._make_upload_file(f"file{i}.txt") for i in range(3)]
        # file0 is slowest, file2 is fastest — completion order is reversed from input order.
        delays = {"file0.txt": 0.03, "file1.txt": 0.015, "file2.txt": 0.0}

        def fake_process(file, user_id, file_repo):
            time.sleep(delays[file.filename])
            return ([FILE_PATH_DATA_NT(name=file.filename, owner=user_id)], [file.filename])

        with patch.object(FileDatasourceService, "_process_upload_file", side_effect=fake_process):
            paths, filenames = FileDatasourceService.process_files_batch(files, "user1", MagicMock())

        assert filenames == ["file0.txt", "file1.txt", "file2.txt"]
        assert [p.name for p in paths] == ["file0.txt", "file1.txt", "file2.txt"]

    def test_propagates_exception_raised_by_one_file(self):
        files = [self._make_upload_file("ok.txt"), self._make_upload_file("bad.zip")]

        def fake_process(file, user_id, file_repo):
            if file.filename == "bad.zip":
                raise ZipExtractionError(
                    message="bad zip", detail="corrupt archive", help_text="try a different file"
                )
            return ([FILE_PATH_DATA_NT(name=file.filename, owner=user_id)], [file.filename])

        with patch.object(FileDatasourceService, "_process_upload_file", side_effect=fake_process):
            with pytest.raises(ZipExtractionError):
                FileDatasourceService.process_files_batch(files, "user1", MagicMock())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/service/datasource/test_file_datasource_service.py::TestProcessFilesBatch -v`
Expected: FAIL with `AttributeError: type object 'FileDatasourceService' has no attribute 'process_files_batch'`

- [ ] **Step 3: Add the `ThreadPoolExecutor` import**

In `src/codemie/service/datasource/file_datasource_service.py`, change:

```python
import json
import mimetypes
from dataclasses import dataclass, field
```

to:

```python
import json
import mimetypes
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
```

- [ ] **Step 4: Add `process_files_batch`**

In `src/codemie/service/datasource/file_datasource_service.py`, insert this new static method directly after `_process_upload_file` ends (after line 290, `return paths, filenames`) and before `@staticmethod` / `def upload_and_prepare_files` (line 292):

```python

    @staticmethod
    def process_files_batch(
        files: list[UploadFile],
        user_id: str,
        file_repo,
    ) -> tuple[list[FILE_PATH_DATA_NT], list[str]]:
        """Write multiple uploaded files to storage concurrently.

        Runs ``_process_upload_file`` over ``files`` using a small bounded thread pool
        and merges the per-file ``(paths, filenames)`` results back in input order.
        """
        if not files:
            return [], []

        max_workers = min(3, len(files))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = list(
                executor.map(
                    lambda file: FileDatasourceService._process_upload_file(file, user_id, file_repo),
                    files,
                )
            )

        paths: list[FILE_PATH_DATA_NT] = []
        filenames: list[str] = []
        for file_paths, file_names in results:
            paths.extend(file_paths)
            filenames.extend(file_names)
        return paths, filenames
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/service/datasource/test_file_datasource_service.py::TestProcessFilesBatch -v`
Expected: all 3 PASS

- [ ] **Step 6: Commit**

```bash
git add src/codemie/service/datasource/file_datasource_service.py tests/codemie/service/datasource/test_file_datasource_service.py
git commit -m "EPMCDME-13212: Add FileDatasourceService.process_files_batch for parallel file saving"
```

---

## Task 5: Use `process_files_batch` in `upload_and_prepare_files` (update path)

**Files:**
- Modify: `src/codemie/service/datasource/file_datasource_service.py:316-319`
- Test: `tests/codemie/service/datasource/test_file_datasource_service.py` (existing `TestUploadAndPrepareFiles` and `TestUploadAndPrepareFilesZip` classes — must keep passing unchanged; this is the regression check for this task)

**Interfaces:**
- Consumes: `FileDatasourceService.process_files_batch(files, user_id, file_repo)` (Task 4).
- Produces: no interface change to `upload_and_prepare_files` — same signature, same `PreparedFilesResult` return shape.

**Test-first:** yes — no new test is needed (the spec calls out no new test for this call-site swap); the existing `TestUploadAndPrepareFiles` and `TestUploadAndPrepareFilesZip` tests are the failing/passing signal for this task, since they exercise `upload_and_prepare_files` end-to-end and must continue to pass after the loop is replaced.

- [ ] **Step 1: Confirm existing tests pass before the change (baseline)**

Run: `poetry run pytest tests/codemie/service/datasource/test_file_datasource_service.py::TestUploadAndPrepareFiles tests/codemie/service/datasource/test_file_datasource_service.py::TestUploadAndPrepareFilesZip -v`
Expected: all PASS (this is the pre-change baseline, not a failing test — the behavior contract already exists; this task must not break it)

- [ ] **Step 2: Replace the sequential loop**

In `src/codemie/service/datasource/file_datasource_service.py`, change:

```python
        file_repo = FileRepositoryFactory.get_current_repository()
        new_paths: list[FILE_PATH_DATA_NT] = []
        new_filenames: list[str] = []

        for file in new_files:
            file_paths, file_names = FileDatasourceService._process_upload_file(file, user.id, file_repo)
            new_paths.extend(file_paths)
            new_filenames.extend(file_names)
```

to:

```python
        file_repo = FileRepositoryFactory.get_current_repository()
        new_paths, new_filenames = FileDatasourceService.process_files_batch(new_files, user.id, file_repo)
```

- [ ] **Step 3: Run tests to verify they still pass**

Run: `poetry run pytest tests/codemie/service/datasource/test_file_datasource_service.py::TestUploadAndPrepareFiles tests/codemie/service/datasource/test_file_datasource_service.py::TestUploadAndPrepareFilesZip -v`
Expected: all PASS, unchanged

- [ ] **Step 4: Commit**

```bash
git add src/codemie/service/datasource/file_datasource_service.py
git commit -m "EPMCDME-13212: Batch-save files in upload_and_prepare_files"
```

---

## Task 6: Use `process_files_batch` in the create-file-datasource router endpoint

**Files:**
- Modify: `src/codemie/rest_api/routers/index.py:2055-2078`
- Test: `tests/codemie/rest_api/routers/test_index.py` (new tests alongside the existing `test_post_file_datasource_*` tests, lines 1066-1158)

**Interfaces:**
- Consumes: `FileDatasourceService.process_files_batch(files, user_id, file_repo)` (Task 4).
- Produces: no interface change to `index_knowledge_base_files` — same route, same request/response shape, same `ZipExtractionError` → HTTP 422 mapping.

**Test-first:** yes — two failing tests: multiple files still all get forwarded to the processor (proves the batch call's merged, ordered output wires up correctly), and a `ZipExtractionError` raised by the batch call still surfaces as HTTP 422 (proves the router's try/except still wraps the new call site).

- [ ] **Step 1: Write the failing tests**

Add `from codemie.service.datasource.file_datasource_service import FileDatasourceService` and `from codemie.service.datasource.zip_utils import ZipExtractionError` to the top of `tests/codemie/rest_api/routers/test_index.py` (currently the import block ends at line 30 with `from codemie.rest_api.security.user import User`):

```python
from codemie.rest_api.security.user import User
from codemie.service.datasource.file_datasource_service import FileDatasourceService
from codemie.service.datasource.zip_utils import ZipExtractionError
```

Then append these two tests directly after `test_post_file_datasource_invalid_guardrail_assignments_raises_400` (currently ending at line 1158, before the `# Tests for PUT /index/knowledge_base/file` section header on line 1161):

```python

@patch('codemie.rest_api.routers.index._kb_demo_user_check')
@patch('codemie.rest_api.routers.index._index_unique_check')
@patch('codemie.rest_api.routers.index.FileRepositoryFactory.get_current_repository')
@patch('codemie.rest_api.routers.index.FileDatasourceProcessor')
def test_post_file_datasource_multiple_files_all_forwarded(
    mock_processor_cls, mock_file_repo_factory, mock_unique_check, mock_demo_check, auth_headers
):
    """Multiple files are all uploaded and forwarded to the processor via process_files_batch."""
    written_objects = {}

    def fake_write_file(name, mime_type, owner, content):
        obj = MagicMock()
        obj.name = name
        obj.owner = owner
        written_objects[name] = obj
        return obj

    mock_file_repo_factory.return_value.write_file.side_effect = fake_write_file

    mock_processor = MagicMock()
    mock_processor.started_message = f"Indexing of {_FILE_DS_NAME} has started in the background"
    mock_processor_cls.return_value = mock_processor

    response = app_client.post(
        _FILE_POST_URL,
        params={"name": _FILE_DS_NAME, "project_name": _FILE_DS_PROJECT, "description": _FILE_DS_DESCRIPTION},
        files=[
            ("files", ("a.txt", b"file a", "text/plain")),
            ("files", ("b.txt", b"file b", "text/plain")),
            ("files", ("c.txt", b"file c", "text/plain")),
        ],
        headers=auth_headers,
    )

    assert response.status_code == 200
    _, kwargs = mock_processor_cls.call_args
    assert kwargs["uploaded_files"] == ["a.txt", "b.txt", "c.txt"]
    assert len(kwargs["files_paths"]) == 3


@patch('codemie.rest_api.routers.index._kb_demo_user_check')
@patch('codemie.rest_api.routers.index._index_unique_check')
@patch('codemie.rest_api.routers.index.FileDatasourceService.process_files_batch')
def test_post_file_datasource_zip_extraction_error_raises_422(
    mock_process_batch, mock_unique_check, mock_demo_check, auth_headers
):
    """A ZipExtractionError from process_files_batch still surfaces as HTTP 422."""
    mock_process_batch.side_effect = ZipExtractionError(
        message="ZIP archive contained no extractable files",
        detail="The archive was empty.",
        help_text="Ensure the archive contains at least one supported file and try again.",
    )

    response = app_client.post(
        _FILE_POST_URL,
        params={"name": _FILE_DS_NAME, "project_name": _FILE_DS_PROJECT, "description": _FILE_DS_DESCRIPTION},
        files=[("files", ("empty.zip", b"PK\x05\x06" + b"\x00" * 18, "application/zip"))],
        headers=auth_headers,
    )

    assert response.status_code == 422
    assert response.json()['error']['message'] == "ZIP archive contained no extractable files"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_index.py::test_post_file_datasource_multiple_files_all_forwarded tests/codemie/rest_api/routers/test_index.py::test_post_file_datasource_zip_extraction_error_raises_422 -v`
Expected:
- `test_post_file_datasource_multiple_files_all_forwarded` FAILs or errors depending on mock plumbing today (the current code calls `_process_upload_file` per file directly, not `process_files_batch`, but should still produce the same `uploaded_files`/`files_paths` — so this one may already pass; if it does, that's fine, it becomes the regression check for this task instead of the new-behavior check).
- `test_post_file_datasource_zip_extraction_error_raises_422` FAILs with `AttributeError` or similar — `codemie.rest_api.routers.index.FileDatasourceService.process_files_batch` does not exist as a call target in the router yet (nothing calls it, so patching it has no effect and the real `_process_upload_file` path runs instead, returning 200 not 422).

- [ ] **Step 3: Replace the sequential loop in the router**

In `src/codemie/rest_api/routers/index.py`, change:

```python
    files_paths = []
    _index_unique_check(request.project_name, request.name)

    _kb_demo_user_check(raw_request.state.user)

    parsed_guardrail_assignments = FileDatasourceService.parse_guardrail_assignments(request.guardrail_assignments)

    file_repo = FileRepositoryFactory.get_current_repository()
    uploaded_files = []

    for file in request.files:
        try:
            file_paths, file_names = FileDatasourceService._process_upload_file(
                file, raw_request.state.user.id, file_repo
            )
        except ZipExtractionError as exc:
            raise ExtendedHTTPException(
                code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                message=str(exc),
                details=exc.detail,
                help=exc.help_text,
            ) from exc
        files_paths.extend(file_paths)
        uploaded_files.extend(file_names)
```

to:

```python
    _index_unique_check(request.project_name, request.name)

    _kb_demo_user_check(raw_request.state.user)

    parsed_guardrail_assignments = FileDatasourceService.parse_guardrail_assignments(request.guardrail_assignments)

    file_repo = FileRepositoryFactory.get_current_repository()

    try:
        files_paths, uploaded_files = FileDatasourceService.process_files_batch(
            request.files, raw_request.state.user.id, file_repo
        )
    except ZipExtractionError as exc:
        raise ExtendedHTTPException(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=str(exc),
            details=exc.detail,
            help=exc.help_text,
        ) from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_index.py::test_post_file_datasource_multiple_files_all_forwarded tests/codemie/rest_api/routers/test_index.py::test_post_file_datasource_zip_extraction_error_raises_422 tests/codemie/rest_api/routers/test_index.py::test_post_file_datasource_success -v`
Expected: all 3 PASS

- [ ] **Step 5: Run the full affected test files as a final regression pass**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_index.py tests/codemie/rest_api/models/test_index_models.py tests/codemie/service/datasource/test_file_datasource_service.py tests/codemie/configs/test_config.py -v`
Expected: all PASS, zero failures

- [ ] **Step 6: Commit**

```bash
git add src/codemie/rest_api/routers/index.py tests/codemie/rest_api/routers/test_index.py
git commit -m "EPMCDME-13212: Batch-save files in index_knowledge_base_files router endpoint"
```

---

## Task 7: Advertise the configured upload limit via `GET /v1/info`

**Files:**
- Modify: `src/codemie/core/models.py:762-764` (`InfoResponse`)
- Modify: `src/codemie/rest_api/routers/common.py:28-34` (`app_info`)
- Test: `tests/codemie/rest_api/routers/test_common.py` (new file)

**Interfaces:**
- Consumes: `config.FILE_DATASOURCE_MAX_UPLOAD_COUNT` (Task 1).
- Produces: `InfoResponse.file_datasource_max_upload_count: int`, returned by `GET /v1/info` (serialized as `fileDatasourceMaxUploadCount` via the model's camelCase alias generator). Consumed by the separate frontend repository's upload dropzone, out of scope here.

**Test-first:** yes — two tests: the default limit round-trips through the endpoint, and a monkeypatched higher config value is reflected.

- [x] **Step 1: Write the failing tests**

`tests/codemie/rest_api/routers/test_common.py` (new file, mirroring the FastAPI-app-plus-router pattern used in `test_background_router.py`):

```python
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from codemie.rest_api.routers.common import router

app = FastAPI()
app.include_router(router)


@pytest.mark.asyncio
async def test_app_info_advertises_default_file_datasource_max_upload_count():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/info")

    assert response.status_code == 200
    assert response.json()["fileDatasourceMaxUploadCount"] == 10


@pytest.mark.asyncio
async def test_app_info_advertises_configured_file_datasource_max_upload_count():
    with patch("codemie.rest_api.routers.common.config.FILE_DATASOURCE_MAX_UPLOAD_COUNT", 50):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v1/info")

    assert response.status_code == 200
    assert response.json()["fileDatasourceMaxUploadCount"] == 50
```

- [x] **Step 2: Run tests to verify they fail**

Both FAIL with a `KeyError` on `"fileDatasourceMaxUploadCount"` — `InfoResponse` had no such field yet.

- [x] **Step 3: Add the field to `InfoResponse`**

In `src/codemie/core/models.py`, change:

```python
class InfoResponse(BaseResponse):
    version: str
    description: str
```

to:

```python
class InfoResponse(BaseResponse):
    version: str
    description: str
    file_datasource_max_upload_count: int
```

- [x] **Step 4: Populate it in `app_info()`**

In `src/codemie/rest_api/routers/common.py`, change:

```python
    return InfoResponse(
        message="Codemie",
        version=config.APP_VERSION,
        description=APP_DESCRIPTION,
    )
```

to:

```python
    return InfoResponse(
        message="Codemie",
        version=config.APP_VERSION,
        description=APP_DESCRIPTION,
        file_datasource_max_upload_count=config.FILE_DATASOURCE_MAX_UPLOAD_COUNT,
    )
```

- [x] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/codemie/rest_api/routers/test_common.py -v`
Result: both PASS

- [x] **Step 6: Commit**

```bash
git add src/codemie/core/models.py src/codemie/rest_api/routers/common.py tests/codemie/rest_api/routers/test_common.py
git commit -m "EPMCDME-13212: Expose file datasource upload limit via /v1/info"
```

---

## Self-Review

**1. Spec coverage:**
- Config setting (`spec.md` Component 1) → Task 1. ✅
- `IndexKnowledgeBaseFileRequest`/`UpdateKnowledgeBaseFileRequest` validator changes (Component 2) → Tasks 2, 3. ✅
- `process_files_batch` helper (Component 3) → Task 4. ✅
- Both call sites replaced (Component 4) → Tasks 5 (service/update path), 6 (router/create path). ✅
- Testing section of the spec: regression test for `test_validate_files_count_high` → covered by Task 3 Step 4 explicitly re-running that test. New `UpdateKnowledgeBaseFileRequest` tests → Task 2. New config-driven `IndexKnowledgeBaseFileRequest` test → Task 3. New `process_files_batch` unit tests (order preservation, exception propagation) → Task 4. ✅
- Out-of-scope items (ZIP limits, indexing batch sizes, enforced ceiling) → untouched by every task; no task modifies `zip_utils.py` or `base_datasource_processor.py`. ✅
- `GET /v1/info` advertisement of the configured limit (Component 5) → Task 7. ✅

**2. Placeholder scan:** No "TBD"/"add appropriate handling"/"similar to Task N" phrasing found. Every code step has a real, complete code block. Task 5's "no new test" call is explicit and justified (existing tests are the regression signal), not a placeholder.

**3. Type consistency:** `process_files_batch(files: list[UploadFile], user_id: str, file_repo) -> tuple[list[FILE_PATH_DATA_NT], list[str]]` (Task 4) matches its call sites exactly: Task 5 (`new_paths, new_filenames = FileDatasourceService.process_files_batch(new_files, user.id, file_repo)`) and Task 6 (`files_paths, uploaded_files = FileDatasourceService.process_files_batch(request.files, raw_request.state.user.id, file_repo)`). `config.FILE_DATASOURCE_MAX_UPLOAD_COUNT` (Task 1) is referenced with the identical name in Tasks 2 and 3 — no drift.
