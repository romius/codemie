# EPMCDME-8827 Backend History Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development (sdlc-light Stage 4). Steps use checkbox (`- [ ]`) syntax for tracking. Do not dispatch per-task subagents. Do not run finishing-a-development-branch; after implementation continue sdlc-light Stage 5.

**Goal:** Delete docs this branch added vs `origin/main`, then restore history-safe workflow persists and semantic YAML-skip so Save / schema / Bedrock updates cannot wipe `yaml_config_history`.

**Architecture:** Keep prior-only history (`YamlConfigHistory`: `yaml_config`, `date`, `created_by`) and existing `_update_workflow_history` JSONB prepend. Add `WorkflowConfigRepository.update_excluding_history` and `update_schema_url`. Rewire `WorkflowService._update_workflow_values` and `save_workflow_schema`, and Bedrock `_create_or_update_entity` update, off full-row `session.merge`. Do not restore the versions platform.

**Tech Stack:** Python 3, SQLModel/SQLAlchemy, FastAPI services, pytest, PyYAML.

## Requirements

Remove the 23 tracked files this branch added vs `origin/main` (prior SDLC artifacts + `docs/EPMCDME-8827-assistants-parity-backend.md`). Then apply `docs/EPMCDME-8827-backend-hardening-to-keep.md`:

1. `WorkflowConfigRepository.update_excluding_history`
2. `WorkflowConfigRepository.update_schema_url`
3. `WorkflowService.save_workflow_schema` → `update_schema_url`
4. `WorkflowService._yaml_content_changed`
5. `_update_workflow_values`: `update_excluding_history` + `_update_workflow_history` **only if** YAML changed
6. Bedrock update path → `update_excluding_history`
7. Hardening tests in §4 of the SoT (not versions API / rollback / seed)

## Global Constraints

- Do **not** restore `seed_initial_version`, `create_version_atomic`, `rollback_to_version_atomic`, `YamlConfigHistory` version fields, Alembic backfill, `WorkflowVersionHistoryCorruptError`, `get_raw_workflow`, `_project_legacy_history`, or `workflow_versions` router/service.
- Do **not** restore HEAD’s always-prepend on every update.
- YAML history uses semantic `yaml.safe_load` comparison. Comment-only and formatting-only edits restore the previously stored YAML and do not prepend history. Treat `None` as an empty YAML document so a comment-only update cannot replace an absent config.
- Adapted yaml-changed sequence: persist **new** live YAML via `update_excluding_history`, then prepend **old** YAML via `_update_workflow_history`. Do not copy tip `6e5588a99` “restore old YAML then `create_version_atomic`” — that atomic wrote live YAML + history together and is a non-goal.
- `YamlConfigHistory.yaml_config` is `str`; when prepending, use `old_yaml or ""`.
- No Alembic, no API contract change, no env/flag.
- Commits: `EPMCDME-8827: <sentence> (epmcdme-8827-backend-hardening task N)`.
- Tests: `poetry run pytest <path> -v`. Source of truth: `Makefile` `test` target uses `poetry run pytest tests/`.
- Do not delete `docs/EPMCDME-8827-backend-hardening-to-keep.md` or `docs/superpowers/tasks/2026-08-13-epmcdme-8827-backend-hardening/`.

## File map

| File | Responsibility |
|---|---|
| `src/codemie/repository/workflow_config_repository.py` | Add `update_excluding_history`, `update_schema_url` |
| `src/codemie/service/workflow_service.py` | `__init__` + `_workflow_repo`; `_yaml_content_changed`; schema + update persist |
| `src/codemie/service/aws_bedrock/bedrock_flow_service.py` | Bedrock update uses `update_excluding_history` |
| `tests/codemie/repository/test_workflow_config_repository.py` | Create: schema URL + exclude-history tests |
| `tests/codemie/service/test_workflow_service.py` | Adapt ORM-update asserts; add YAML skip / schema / composite tests |
| `tests/codemie/service/aws_bedrock/test_bedrock_flow_service.py` | Add update-excludes-history test |
| 23 tracked docs vs `origin/main` | Delete |

---

### Task 1: Delete branch-added docs

**Test-first: no — docs-only cleanup; no production behavior.**

**Files:**
- Delete: `docs/EPMCDME-8827-assistants-parity-backend.md`
- Delete directory: `docs/superpowers/tasks/2026-08-11-epmcdme-8827-workflow-version-history-backend-update/`
- Delete directory: `docs/superpowers/tasks/2026-08-13-epmcdme-8827-assistants-parity-cleanup/`

**Interfaces:**
- Consumes: nothing
- Produces: working tree whose tracked diff vs `origin/main` has no added files

- [ ] **Step 1: Remove the 23 tracked files and the two task directories**

```bash
git rm docs/EPMCDME-8827-assistants-parity-backend.md
git rm -r docs/superpowers/tasks/2026-08-11-epmcdme-8827-workflow-version-history-backend-update
git rm -r docs/superpowers/tasks/2026-08-13-epmcdme-8827-assistants-parity-cleanup
rm -f docs/superpowers/tasks/2026-08-11-epmcdme-8827-workflow-version-history-backend-update/code-review.diff \
      docs/superpowers/tasks/2026-08-11-epmcdme-8827-workflow-version-history-backend-update/code-review-check.diff \
      docs/superpowers/tasks/2026-08-13-epmcdme-8827-assistants-parity-cleanup/code-review.diff
```

If `git rm -r` already removed the directories, the `rm -f` of gitignored `*.diff` files is a no-op.

Do **not** delete `docs/EPMCDME-8827-backend-hardening-to-keep.md` or `docs/superpowers/tasks/2026-08-13-epmcdme-8827-backend-hardening/`.

- [ ] **Step 2: Confirm vs origin/main**

```bash
git diff --diff-filter=A --name-only origin/main...HEAD
git status --porcelain
```

Expected: no added paths remaining in the index for those two task trees / parity doc. Untracked hardening SoT and this run dir may still be present.

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
EPMCDME-8827: Remove branch-added planning docs (epmcdme-8827-backend-hardening task 1)

EOF
)"
```

---

### Task 2: History-safe repository persists

**Test-first: yes — `AttributeError` / missing methods `update_schema_url` and `update_excluding_history` on `WorkflowConfigRepository`.**

**Files:**
- Create: `tests/codemie/repository/test_workflow_config_repository.py`
- Modify: `src/codemie/repository/workflow_config_repository.py`

**Interfaces:**
- Consumes: `WorkflowConfig`, `NotFoundException`, `WorkflowConfig.get_engine()`
- Produces:
  - `WorkflowConfigRepository.update_schema_url(self, workflow_id: str, schema_url: str) -> None`
  - `WorkflowConfigRepository.update_excluding_history(self, workflow: WorkflowConfig) -> None`

- [ ] **Step 1: Write the failing tests**

Create `tests/codemie/repository/test_workflow_config_repository.py` with Apache 2026 EPAM header matching `tests/codemie/repository/test_category_repository.py`, then:

```python
from unittest.mock import MagicMock, patch

import pytest

from codemie.core.exceptions import NotFoundException
from codemie.core.workflow_models.workflow_config import WorkflowConfig
from codemie.repository.workflow_config_repository import WorkflowConfigRepository


@pytest.fixture
def repo():
    return WorkflowConfigRepository()


def _make_engine_mock(rowcount=1):
    mock_conn = MagicMock()
    mock_result = MagicMock()
    mock_result.rowcount = rowcount
    mock_conn.execute.return_value = mock_result
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_conn)
    mock_ctx.__exit__ = MagicMock(return_value=False)
    return mock_conn, mock_ctx


class TestUpdateSchemaUrl:
    def test_executes_column_scoped_update(self, repo):
        mock_conn, mock_ctx = _make_engine_mock(rowcount=1)
        with patch("codemie.repository.workflow_config_repository.WorkflowConfig.get_engine") as mock_engine:
            mock_engine.return_value.begin.return_value = mock_ctx
            repo.update_schema_url("wf_1", "https://example.com/wf.svg")

        assert mock_conn.execute.call_count == 1
        sql_text = str(mock_conn.execute.call_args[0][0])
        assert "schema_url" in sql_text
        assert "yaml_config_history" not in sql_text

    def test_raises_not_found_for_missing_workflow(self, repo):
        mock_conn, mock_ctx = _make_engine_mock(rowcount=0)
        with patch("codemie.repository.workflow_config_repository.WorkflowConfig.get_engine") as mock_engine:
            mock_engine.return_value.begin.return_value = mock_ctx
            with pytest.raises(NotFoundException):
                repo.update_schema_url("nonexistent", "https://example.com/wf.svg")


class TestUpdateExcludingHistory:
    def test_copies_columns_except_id_and_history(self, repo):
        workflow = WorkflowConfig(
            id="wf_1",
            name="New Name",
            description="d",
            yaml_config="new: yaml",
            yaml_config_history=[],
        )
        db_obj = MagicMock()
        db_obj.id = "wf_1"
        db_obj.yaml_config_history = [{"yaml_config": "old"}]
        original_history = db_obj.yaml_config_history

        mock_session = MagicMock()
        mock_session.get.return_value = db_obj
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with (
            patch("codemie.repository.workflow_config_repository.Session", return_value=mock_session),
            patch("codemie.repository.workflow_config_repository.WorkflowConfig.get_engine"),
        ):
            repo.update_excluding_history(workflow)

        assert db_obj.name == "New Name"
        assert db_obj.yaml_config == "new: yaml"
        assert db_obj.id == "wf_1"
        assert db_obj.yaml_config_history is original_history
        mock_session.add.assert_called_once_with(db_obj)
        mock_session.commit.assert_called_once()

    def test_raises_not_found_when_missing(self, repo):
        workflow = WorkflowConfig(id="missing", name="n", description="d")
        mock_session = MagicMock()
        mock_session.get.return_value = None
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with (
            patch("codemie.repository.workflow_config_repository.Session", return_value=mock_session),
            patch("codemie.repository.workflow_config_repository.WorkflowConfig.get_engine"),
            pytest.raises(NotFoundException),
        ):
            repo.update_excluding_history(workflow)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie/repository/test_workflow_config_repository.py -v
```

Expected: FAIL with `AttributeError: 'WorkflowConfigRepository' object has no attribute 'update_schema_url'` (and similarly for `update_excluding_history` if the first test were skipped).

- [ ] **Step 3: Implement the methods**

In `src/codemie/repository/workflow_config_repository.py` add:

```python
from datetime import datetime

from sqlmodel import Session
```

Keep existing `from sqlalchemy import text`. Then on the class, after `recompute_unique_users_count`:

```python
    def update_excluding_history(self, workflow: WorkflowConfig) -> None:
        """Copy mapped columns except id and yaml_config_history, then commit."""
        workflow.update_date = datetime.now()
        with Session(WorkflowConfig.get_engine()) as session:
            db_obj = session.get(WorkflowConfig, workflow.id)
            if db_obj is None:
                raise NotFoundException(f"Workflow '{workflow.id}' not found")
            for column in WorkflowConfig.__table__.columns:
                name = column.name
                if name in ("id", "yaml_config_history"):
                    continue
                setattr(db_obj, name, getattr(workflow, name))
            session.add(db_obj)
            session.commit()

    def update_schema_url(self, workflow_id: str, schema_url: str) -> None:
        """Update schema_url only; do not touch yaml_config_history."""
        with WorkflowConfig.get_engine().begin() as conn:
            result = conn.execute(
                text("UPDATE workflows SET schema_url = :url WHERE id = :wf_id").bindparams(
                    url=schema_url, wf_id=workflow_id
                )
            )
        if result.rowcount == 0:
            raise NotFoundException(f"Workflow '{workflow_id}' not found")
```

Do not add `seed_initial_version`, `create_version_atomic`, or rollback helpers.

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie/repository/test_workflow_config_repository.py -v
```

Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/repository/workflow_config_repository.py tests/codemie/repository/test_workflow_config_repository.py
git commit -m "$(cat <<'EOF'
EPMCDME-8827: Add history-safe workflow persists (epmcdme-8827-backend-hardening task 2)

EOF
)"
```

---

### Task 3: YAML change helper and workflow repo wiring

**Test-first: yes — `AttributeError: type object 'WorkflowService' has no attribute '_yaml_content_changed'`.**

**Files:**
- Modify: `tests/codemie/service/test_workflow_service.py` (append `TestYamlContentChanged`)
- Modify: `src/codemie/service/workflow_service.py` (imports, `__init__`, `_yaml_content_changed`)

**Interfaces:**
- Consumes: `WorkflowConfigRepository` from Task 2
- Produces:
  - `WorkflowService.__init__(self) -> None` setting `self._workflow_repo = WorkflowConfigRepository()`
  - `WorkflowService._yaml_content_changed(old_yaml: Optional[str], new_yaml: Optional[str]) -> bool`

- [ ] **Step 1: Write the failing tests**

Append to `tests/codemie/service/test_workflow_service.py`:

```python
class TestYamlContentChanged:
    def test_identical_strings_returns_false(self):
        assert WorkflowService._yaml_content_changed("key: value", "key: value") is False

    @pytest.mark.parametrize(
        ("old_yaml", "new_yaml"),
        [
            ("first: 1\nsecond: 2", "second: 2\nfirst: 1"),
            ("key: value", 'key: "value"'),
            ("items:\n- one\n- two", "items: [one, two]"),
        ],
        ids=["reordered_keys", "quote_style", "block_vs_flow_list"],
    )
    def test_formatting_only_difference_returns_false(self, old_yaml, new_yaml):
        assert WorkflowService._yaml_content_changed(old_yaml, new_yaml) is False

    @pytest.mark.parametrize(
        ("old_yaml", "new_yaml"),
        [
            (None, ""),
            (None, "# comment only"),
            ("# comment only", None),
        ],
        ids=["none_to_empty", "none_to_comment", "comment_to_none"],
    )
    def test_absent_and_semantically_empty_yaml_returns_false(self, old_yaml, new_yaml):
        assert WorkflowService._yaml_content_changed(old_yaml, new_yaml) is False

    def test_different_yaml_content_returns_true(self):
        assert WorkflowService._yaml_content_changed("key: a", "key: b") is True

    def test_none_vs_none_returns_false(self):
        assert WorkflowService._yaml_content_changed(None, None) is False

    def test_none_vs_yaml_returns_true(self):
        assert WorkflowService._yaml_content_changed(None, "key: value") is True

    def test_yaml_vs_none_returns_true(self):
        assert WorkflowService._yaml_content_changed("key: value", None) is True

    def test_invalid_yaml_falls_back_to_string_comparison(self):
        assert WorkflowService._yaml_content_changed("{bad yaml", "{bad yaml") is False
        assert WorkflowService._yaml_content_changed("{bad yaml", "{different bad") is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
poetry run pytest tests/codemie/service/test_workflow_service.py::TestYamlContentChanged -v
```

Expected: FAIL with `AttributeError: type object 'WorkflowService' has no attribute '_yaml_content_changed'`.

- [ ] **Step 3: Implement helper and `__init__`**

In `src/codemie/service/workflow_service.py`:

Add imports:

```python
import yaml

from codemie.repository.workflow_config_repository import WorkflowConfigRepository
```

On `WorkflowService`, immediately after class attributes / before `get_workflow`:

```python
    def __init__(self) -> None:
        self._workflow_repo = WorkflowConfigRepository()

    @staticmethod
    def _yaml_content_changed(old_yaml: Optional[str], new_yaml: Optional[str]) -> bool:
        if old_yaml == new_yaml:
            return False
        try:
            return yaml.safe_load(old_yaml or "") != yaml.safe_load(new_yaml or "")
        except yaml.YAMLError:
            return old_yaml != new_yaml
```

Do not change persist paths yet.

- [ ] **Step 4: Run tests to verify they pass**

```bash
poetry run pytest tests/codemie/service/test_workflow_service.py::TestYamlContentChanged tests/codemie/service/test_workflow_service.py -v --tb=line
```

Expected: `TestYamlContentChanged` PASS. Existing update tests still pass (they still patch `WorkflowConfig.update`).

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/workflow_service.py tests/codemie/service/test_workflow_service.py
git commit -m "$(cat <<'EOF'
EPMCDME-8827: Add semantic YAML change detection (epmcdme-8827-backend-hardening task 3)

EOF
)"
```

---

### Task 4: Schema save must not ORM-merge history

**Test-first: yes — `save_workflow_schema` still calls `workflow_config.update()`; new test asserts `update_schema_url` and `WorkflowConfig.update` not called.**

**Files:**
- Modify: `tests/codemie/service/test_workflow_service.py`
- Modify: `src/codemie/service/workflow_service.py` (`save_workflow_schema` around lines 543–558)

**Interfaces:**
- Consumes: `self._workflow_repo.update_schema_url(workflow_id: str, schema_url: str) -> None`
- Produces: schema persist that cannot overwrite `yaml_config_history`

- [ ] **Step 1: Write the failing test**

```python
def test_save_workflow_schema_does_not_call_orm_update(
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
) -> None:
    mock_file_result = MagicMock()
    mock_file_result.to_encoded_url.return_value = "https://example.com/wf.svg"
    mock_files_repo = MagicMock()
    mock_files_repo.write_file.return_value = mock_file_result

    workflow_service._workflow_repo = MagicMock()

    with (
        patch("codemie.service.workflow_service.FileRepositoryFactory") as mock_factory,
        patch("codemie.core.workflow_models.WorkflowConfig.update") as mock_orm_update,
    ):
        mock_factory.return_value.get_current_repository.return_value = mock_files_repo
        workflow_service.save_workflow_schema(workflow_config, b"<svg/>")

    workflow_service._workflow_repo.update_schema_url.assert_called_once_with(
        str(workflow_config.id), "https://example.com/wf.svg"
    )
    mock_orm_update.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie/service/test_workflow_service.py::test_save_workflow_schema_does_not_call_orm_update -v
```

Expected: FAIL — `update_schema_url` not called and/or `WorkflowConfig.update` was called.

- [ ] **Step 3: Implement**

Replace the persist in `save_workflow_schema`:

```python
                workflow_config.schema_url = result.to_encoded_url()
                self._workflow_repo.update_schema_url(str(workflow_config.id), workflow_config.schema_url)
```

Do not call `workflow_config.update()`.

- [ ] **Step 4: Run test to verify it passes**

```bash
poetry run pytest tests/codemie/service/test_workflow_service.py::test_save_workflow_schema_does_not_call_orm_update -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/workflow_service.py tests/codemie/service/test_workflow_service.py
git commit -m "$(cat <<'EOF'
EPMCDME-8827: Persist schema_url without merging history (epmcdme-8827-backend-hardening task 4)

EOF
)"
```

---

### Task 5: History-safe update path and existing-test adaptation

**Test-first: yes — existing tests assert `WorkflowConfig.update(refresh=True)` and always-prepend; new tests require `update_excluding_history` and prepend only when YAML changes.**

**Files:**
- Modify: `tests/codemie/service/test_workflow_service.py`
- Modify: `src/codemie/service/workflow_service.py` (`_update_workflow_values` lines 651–693)

**Interfaces:**
- Consumes: `_yaml_content_changed`, `update_excluding_history`, `_update_workflow_history(workflow_config, YamlConfigHistory)`
- Produces: update persist that never `session.merge`s `yaml_config_history`; history prepend only when YAML semantically changed

Existing tests that currently patch `WorkflowConfig.update` / `.refresh` and must be adapted in this same task:

- `test_update_workflow`
- `test_update_workflow_nothing_to_update`
- `test_update_workflow_clears_description_with_empty_string`
- `test_update_workflow_skips_none_fields`
- `test_yaml_reflects_assistants_and_states`
- `test_invalid_yaml_logs_error`
- `test_update_workflow_values_start_hint_updated_when_different`
- `test_update_workflow_values_start_hint_cleared`

Adaptation pattern for each: drop `@patch(...WorkflowConfig.update)` and `@patch(...refresh)` unless still needed. At the start of the test body:

```python
    workflow_service._workflow_repo = MagicMock()
```

Patch `workflow_service._update_workflow_history` (or `codemie.service.workflow_service.WorkflowService._update_workflow_history`) so tests do not hit SQL.

- YAML-changing tests (`test_update_workflow`, `test_yaml_reflects_assistants_and_states`): assert `update_excluding_history` called once; `_update_workflow_history` called once.
- Metadata-only tests (`test_update_workflow_nothing_to_update`, description/icon/start_hint): assert `update_excluding_history` called once; `_update_workflow_history` **not** called.
- `test_invalid_yaml_logs_error`: assert `update_excluding_history` not called.

- [ ] **Step 1: Write the failing new tests (and adapt existing ones so RED is the missing persist behavior)**

Add:

```python
@patch.object(WorkflowService, "_update_workflow_history")
def test_update_workflow_yaml_change_prepends_history(
    mock_history: MagicMock,
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    user: User,
) -> None:
    workflow_service._workflow_repo = MagicMock()
    workflow_config.yaml_config = "assistants: []"
    updated = WorkflowConfig(
        name=workflow_config.name,
        description=workflow_config.description,
        project="demo",
        yaml_config="assistants:\n- id: new_assistant",
    )

    workflow_service._update_workflow_values(workflow_config, updated, user)

    workflow_service._workflow_repo.update_excluding_history.assert_called_once()
    persisted = workflow_service._workflow_repo.update_excluding_history.call_args.args[0]
    assert persisted.yaml_config == "assistants:\n- id: new_assistant"
    mock_history.assert_called_once()
    history_entry = mock_history.call_args.args[1]
    assert history_entry.yaml_config == "assistants: []"


@patch.object(WorkflowService, "_update_workflow_history")
def test_update_workflow_metadata_only_does_not_prepend_history(
    mock_history: MagicMock,
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    user: User,
) -> None:
    workflow_service._workflow_repo = MagicMock()
    workflow_config.yaml_config = "assistants: []"
    updated = WorkflowConfig(
        name="New Name",
        description=workflow_config.description,
        project="demo",
    )

    workflow_service._update_workflow_values(workflow_config, updated, user)

    workflow_service._workflow_repo.update_excluding_history.assert_called_once()
    mock_history.assert_not_called()


@patch.object(WorkflowService, "_update_workflow_history")
def test_update_workflow_same_yaml_does_not_prepend_history(
    mock_history: MagicMock,
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    user: User,
) -> None:
    workflow_service._workflow_repo = MagicMock()
    workflow_config.yaml_config = "assistants: []"
    updated = WorkflowConfig(
        name=workflow_config.name,
        description=workflow_config.description,
        project="demo",
        yaml_config="assistants: []",
    )

    workflow_service._update_workflow_values(workflow_config, updated, user)

    workflow_service._workflow_repo.update_excluding_history.assert_called_once()
    mock_history.assert_not_called()


@patch.object(WorkflowService, "_update_workflow_history")
def test_update_workflow_comment_only_yaml_does_not_drift_live_config(
    mock_history: MagicMock,
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    user: User,
) -> None:
    workflow_service._workflow_repo = MagicMock()
    workflow_config.yaml_config = "key: value"
    updated = WorkflowConfig(
        name=workflow_config.name,
        description=workflow_config.description,
        project="demo",
        yaml_config="key: value\n# comment only",
    )

    workflow_service._update_workflow_values(workflow_config, updated, user)

    mock_history.assert_not_called()
    assert workflow_config.yaml_config == "key: value"
    persisted = workflow_service._workflow_repo.update_excluding_history.call_args.args[0]
    assert persisted.yaml_config == "key: value"


def test_update_then_save_schema_does_not_call_orm_update(
    workflow_service: WorkflowService,
    workflow_config: WorkflowConfig,
    user: User,
) -> None:
    from datetime import timezone

    from codemie.core.workflow_models.workflow_config import YamlConfigHistory

    workflow_service._workflow_repo = MagicMock()
    workflow_config.yaml_config = "assistants: []"
    workflow_config.yaml_config_history = [
        YamlConfigHistory(
            yaml_config="assistants: []",
            date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            created_by=user.as_user_model(),
        )
    ]
    updated = WorkflowConfig(
        name="Renamed Workflow",
        description=workflow_config.description,
        project="demo",
        yaml_config="assistants: []",
    )

    with patch.object(workflow_service, "_update_workflow_history") as mock_history:
        workflow_service.update_workflow(workflow_config, updated, user)

    mock_history.assert_not_called()
    assert len(workflow_config.yaml_config_history) >= 1

    mock_files_repo = MagicMock()
    mock_file_result = MagicMock()
    mock_file_result.to_encoded_url.return_value = "https://example.com/schema.svg"
    mock_files_repo.write_file.return_value = mock_file_result

    with (
        patch("codemie.service.workflow_service.FileRepositoryFactory") as mock_factory,
        patch("codemie.core.workflow_models.WorkflowConfig.update") as mock_orm_update,
    ):
        mock_factory.return_value.get_current_repository.return_value = mock_files_repo
        workflow_service.save_workflow_schema(workflow_config, b"<svg/>")

    assert len(workflow_config.yaml_config_history) >= 1
    workflow_service._workflow_repo.update_schema_url.assert_called_once_with(
        str(workflow_config.id), "https://example.com/schema.svg"
    )
    mock_orm_update.assert_not_called()
```

Add `from datetime import datetime` to the test module if not already imported via other tests — the file currently has no `datetime` import; add:

```python
from datetime import datetime
```

Adapt the eight existing update tests as described above **in the same change** so they fail on `update_excluding_history` not being called rather than on leftover `mock_update` asserts after implementation.

- [ ] **Step 2: Run new tests to verify they fail**

```bash
poetry run pytest tests/codemie/service/test_workflow_service.py::test_update_workflow_yaml_change_prepends_history tests/codemie/service/test_workflow_service.py::test_update_workflow_metadata_only_does_not_prepend_history tests/codemie/service/test_workflow_service.py::test_update_workflow_same_yaml_does_not_prepend_history tests/codemie/service/test_workflow_service.py::test_update_workflow_comment_only_yaml_does_not_drift_live_config tests/codemie/service/test_workflow_service.py::test_update_then_save_schema_does_not_call_orm_update -v
```

Expected: FAIL — `update_excluding_history` not called and/or `_update_workflow_history` still always called.

- [ ] **Step 3: Implement `_update_workflow_values`**

Replace the method body so it:

1. Captures `old_yaml = stored_config.yaml_config`.
2. Applies editable fields / `parse_execution_config` / `shared` / `start_hint` / `updated_by` exactly as today.
3. `yaml_changed = self._yaml_content_changed(old_yaml, stored_config.yaml_config)`.
4. If not changed: `stored_config.yaml_config = old_yaml`.
5. `self._workflow_repo.update_excluding_history(stored_config)`.
6. If changed: `_update_workflow_history` with `YamlConfigHistory(yaml_config=old_yaml or "", date=datetime.now(), created_by=user.as_user_model())`.
7. Keep the success metric log block.

Do not call `stored_config.update(...)`. Do not always prepend.

```python
    def _update_workflow_values(
        self,
        stored_config: WorkflowConfig,
        updated_workflow_config: WorkflowConfig,
        user: User,
    ) -> None:
        old_yaml = stored_config.yaml_config
        yaml_config_updated = False

        for attr_name in self._editable_non_boolean_fields:
            new_value = getattr(updated_workflow_config, attr_name)
            if new_value is not None:
                if attr_name == "yaml_config":
                    yaml_config_updated = True
                setattr(stored_config, attr_name, new_value)

        if yaml_config_updated:
            stored_config.parse_execution_config()

        if stored_config.shared != updated_workflow_config.shared:
            stored_config.shared = updated_workflow_config.shared

        if updated_workflow_config.start_hint != stored_config.start_hint:
            stored_config.start_hint = updated_workflow_config.start_hint

        stored_config.updated_by = user.as_user_model()
        logger.debug(f"Store workflow: {stored_config.yaml_config}")

        yaml_changed = self._yaml_content_changed(old_yaml, stored_config.yaml_config)
        if not yaml_changed:
            stored_config.yaml_config = old_yaml

        self._workflow_repo.update_excluding_history(stored_config)

        if yaml_changed:
            self._update_workflow_history(
                stored_config,
                YamlConfigHistory(
                    yaml_config=old_yaml or "",
                    date=datetime.now(),
                    created_by=user.as_user_model(),
                ),
            )

        logger.info(f"Workflow updated with ID: {stored_config.id}")
        WorkflowMonitoringService.send_update_workflow_metric(
            workflow_id=stored_config.id,
            user_id=user.id,
            user_name=user.name,
            workflow_name=stored_config.name,
            project=stored_config.project,
            success=True,
            mode=stored_config.mode,
        )
```

- [ ] **Step 4: Run the workflow service tests**

```bash
poetry run pytest tests/codemie/service/test_workflow_service.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/workflow_service.py tests/codemie/service/test_workflow_service.py
git commit -m "$(cat <<'EOF'
EPMCDME-8827: Persist workflow updates without wiping history (epmcdme-8827-backend-hardening task 5)

EOF
)"
```

---

### Task 6: Bedrock update must exclude history

**Test-first: yes — update-existing path still calls `entity.update(refresh=True)`; new test asserts `update_excluding_history`.**

**Files:**
- Modify: `tests/codemie/service/aws_bedrock/test_bedrock_flow_service.py`
- Modify: `src/codemie/service/aws_bedrock/bedrock_flow_service.py` (`_create_or_update_entity` around lines 618–656)

**Interfaces:**
- Consumes: `WorkflowConfigRepository.update_excluding_history(entity)`
- Produces: Bedrock update persist that does not ORM-merge `yaml_config_history`

- [ ] **Step 1: Write the failing test**

Append to `tests/codemie/service/aws_bedrock/test_bedrock_flow_service.py`:

```python
@patch("codemie.service.aws_bedrock.bedrock_flow_service.WorkflowConfigRepository")
def test_create_or_update_entity_update_excludes_history(mock_repo_cls):
    """Bedrock updates must not ORM-merge yaml_config_history."""
    mock_repo = MagicMock()
    mock_repo_cls.return_value = mock_repo

    existing = MagicMock()
    existing.id = "wf-existing"
    incoming = MagicMock()
    for field in ("name", "description", "project", "mode", "shared", "states", "custom_nodes", "bedrock"):
        setattr(incoming, field, f"new-{field}")

    result = BedrockFlowService._create_or_update_entity(
        flow_id="flow-1",
        flow_id_alias="alias-1",
        workflow_config=incoming,
        existing_entities_map={"alias-1": existing},
    )

    assert result == "wf-existing"
    mock_repo.update_excluding_history.assert_called_once_with(existing)
    existing.update.assert_not_called()
```

Do **not** add a `seed_initial_version` create-path test.

- [ ] **Step 2: Run test to verify it fails**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_flow_service.py::test_create_or_update_entity_update_excludes_history -v
```

Expected: FAIL — `update_excluding_history` not called (and `existing.update` was called).

- [ ] **Step 3: Implement**

Add import:

```python
from codemie.repository.workflow_config_repository import WorkflowConfigRepository
```

Replace `entity.update(refresh=True)` with:

```python
            WorkflowConfigRepository().update_excluding_history(entity)
```

Leave the create branch as `workflow_config.save(refresh=True)` only. Do not call `seed_initial_version`.

- [ ] **Step 4: Run Bedrock tests**

```bash
poetry run pytest tests/codemie/service/aws_bedrock/test_bedrock_flow_service.py::test_create_or_update_entity_update_excludes_history tests/codemie/service/aws_bedrock/test_bedrock_flow_service.py -v --tb=line
```

Expected: new test PASS; existing Bedrock tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/aws_bedrock/bedrock_flow_service.py tests/codemie/service/aws_bedrock/test_bedrock_flow_service.py
git commit -m "$(cat <<'EOF'
EPMCDME-8827: Persist Bedrock workflow updates without wiping history (epmcdme-8827-backend-hardening task 6)

EOF
)"
```

---

## Self-review

1. **SoT coverage:** checklist items 1–7 each have a task (2–6 plus tests). File deletion is Task 1. Versions-platform symbols are listed as do-not-restore in Global Constraints.
2. **Placeholder scan:** no TBD / “similar to Task N” without code.
3. **Types:** `update_schema_url(workflow_id: str, schema_url: str) -> None`; `update_excluding_history(workflow: WorkflowConfig) -> None`; `_yaml_content_changed(old_yaml: Optional[str], new_yaml: Optional[str]) -> bool`.
4. **Adapted yaml-changed order** matches research: persist new YAML first, then prepend old via `_update_workflow_history`.
