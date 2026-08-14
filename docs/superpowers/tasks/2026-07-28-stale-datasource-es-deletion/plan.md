# EPMCDME-13161 — Stale Datasource ES Index Deletion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `StaleDatasourceService` to delete the Elasticsearch index of each STALE datasource and advance it to `ARCHIVED` state during the same nightly run, gated behind `STALE_DATASOURCE_DELETION_ENABLED`.

**Architecture:** The existing marking phase commits STALE rows first; when `STALE_DATASOURCE_DELETION_ENABLED=True` a new `_delete_stale_indexes()` method runs next in the same nightly job. It deletes ES indexes and batches a single PG UPDATE→ARCHIVED. A shared-index guard and circuit-breaker protect against over-deletion. `complete_progress()` gains a reactivation hook so scheduler-triggered reindexing restores ARCHIVED→ACTIVE.

**Tech Stack:** Python 3.11, FastAPI/SQLModel, SQLAlchemy async, Elasticsearch sync client via `asyncio.to_thread`, APScheduler, pytest+AsyncMock.

---

## File Map

| File | Change |
|---|---|
| `src/codemie/service/stale_datasource/config.py` | Fix lock ID: `987654324` → `987654326` |
| `src/codemie/service/analytics/metric_names.py` | Add `STALE_DATASOURCE_INDEX_DELETED` enum entry |
| `src/codemie/configs/config.py` | Add `STALE_DATASOURCE_DELETION_ENABLED` + `STALE_DATASOURCE_MAX_DELETIONS_PER_RUN`; add `finalize_settings` dependency guard |
| `src/codemie/rest_api/models/index.py` | Extend `update_progress()` and `_build_progress_update_kwargs()` with `lifecycle_state` / `clear_marked_stale_at`; add reactivation to `complete_progress()` |
| `src/codemie/service/stale_datasource/stale_datasource_service.py` | New imports; `_delete_stale_indexes()`; wire into `detect_and_mark_stale_datasources()`; remove dead `already_stale` counter |
| `src/codemie/service/stale_datasource/scheduler.py` | Update log line: remove `already_stale` key, add deletion stat keys |
| `deploy-templates/values.yaml` | Add all `STALE_DATASOURCE_*` env vars |
| `tests/codemie/configs/test_config.py` | 2 deletion-flag dependency guard tests |
| `tests/codemie/rest_api/models/test_index_info.py` | `_build_progress_update_kwargs` lifecycle tests + `complete_progress()` reactivation tests |
| `tests/codemie/service/stale_datasource/test_stale_datasource_service.py` | `TestDeleteStaleIndexes` + `TestDeleteStaleIndexesWiring` classes |

---

### Task 1: Fix advisory lock ID collision

**Files:**
- Modify: `src/codemie/service/stale_datasource/config.py`

**Test-first: no** — config constant; lock-collision correctness is an integration concern.

- [ ] **Step 1: Read the file**

  ```bash
  # Verify current content before editing
  head -30 src/codemie/service/stale_datasource/config.py
  ```

- [ ] **Step 2: Apply the fix**

  Replace the entire comment block and constant (currently `STALE_DATASOURCE_LOCK_ID = 987654324`):

  ```python
  # Full lock registry: CA=987654321, Spend=987654322/323/324, LB=987654325
  STALE_DATASOURCE_LOCK_ID = 987654326
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add src/codemie/service/stale_datasource/config.py
  git commit -m "fix(EPMCDME-13161): resolve advisory lock ID collision (987654324 -> 987654326)"
  ```

---

### Task 2: Add MetricName for stale datasource index deletion

**Files:**
- Modify: `src/codemie/service/analytics/metric_names.py`

**Test-first: no** — enum extension; no runtime behaviour to unit-test.

- [ ] **Step 1: Add the constant**

  After `UPDATE_DATASOURCE = "update_datasource"` (line 48), insert a new section:

  ```python
  # Stale datasource deletion
  STALE_DATASOURCE_INDEX_DELETED = "stale_datasource_index_deleted"
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add src/codemie/service/analytics/metric_names.py
  git commit -m "feat(EPMCDME-13161): add STALE_DATASOURCE_INDEX_DELETED metric name"
  ```

---

### Task 3: Add deletion config flags and dependency guard

**Files:**
- Modify: `src/codemie/configs/config.py`
- Modify: `tests/codemie/configs/test_config.py`

**Test-first: yes** — write the two guard tests, confirm RED, then add the fields and guard.

- [ ] **Step 1: Write the failing tests**

  Append to `tests/codemie/configs/test_config.py`:

  ```python
  def test_deletion_enabled_without_stale_detection_fails_validation():
      with pytest.raises(ValidationError):
          Config(
              STALE_DATASOURCE_ENABLED=False,
              STALE_DATASOURCE_DELETION_ENABLED=True,
          )


  def test_deletion_enabled_with_detection_enabled_passes_validation():
      cfg = Config(
          STALE_DATASOURCE_ENABLED=True,
          STALE_DATASOURCE_DELETION_ENABLED=True,
      )
      assert cfg.STALE_DATASOURCE_DELETION_ENABLED is True
  ```

- [ ] **Step 2: Run to verify RED**

  ```bash
  pytest tests/codemie/configs/test_config.py::test_deletion_enabled_without_stale_detection_fails_validation tests/codemie/configs/test_config.py::test_deletion_enabled_with_detection_enabled_passes_validation -v
  ```

  Expected: `FAILED` — `AttributeError: type object 'Config' has no attribute 'STALE_DATASOURCE_DELETION_ENABLED'`

- [ ] **Step 3: Add config fields**

  In `src/codemie/configs/config.py`, after line 754 (`STALE_DATASOURCE_BATCH_SIZE: int = 100`):

  ```python
  STALE_DATASOURCE_DELETION_ENABLED: bool = False  # Enables ES index deletion phase
  STALE_DATASOURCE_MAX_DELETIONS_PER_RUN: int = 100  # Circuit-breaker cap
  ```

- [ ] **Step 4: Add finalize_settings guard**

  In `finalize_settings`, after the existing STALE_DATASOURCE cron validation block (which ends with `return self` at line 872), insert the guard **before** `return self`:

  ```python
  if self.STALE_DATASOURCE_DELETION_ENABLED and not self.STALE_DATASOURCE_ENABLED:
      raise ValueError(
          "STALE_DATASOURCE_DELETION_ENABLED=True requires STALE_DATASOURCE_ENABLED=True"
      )
  ```

  The end of `finalize_settings` becomes:

  ```python
          if self.STALE_DATASOURCE_ENABLED:
              try:
                  CronTrigger.from_crontab(self.STALE_DATASOURCE_SCHEDULE)
              except ValueError as exc:
                  raise ValueError(
                      f"STALE_DATASOURCE_SCHEDULE is not a valid cron expression: "
                      f"{self.STALE_DATASOURCE_SCHEDULE!r} ({exc})"
                  ) from exc

          if self.STALE_DATASOURCE_DELETION_ENABLED and not self.STALE_DATASOURCE_ENABLED:
              raise ValueError(
                  "STALE_DATASOURCE_DELETION_ENABLED=True requires STALE_DATASOURCE_ENABLED=True"
              )

          return self
  ```

- [ ] **Step 5: Run to verify GREEN**

  ```bash
  pytest tests/codemie/configs/test_config.py::test_deletion_enabled_without_stale_detection_fails_validation tests/codemie/configs/test_config.py::test_deletion_enabled_with_detection_enabled_passes_validation -v
  ```

  Expected: `PASSED PASSED`

- [ ] **Step 6: Commit**

  ```bash
  git add src/codemie/configs/config.py tests/codemie/configs/test_config.py
  git commit -m "feat(EPMCDME-13161): add STALE_DATASOURCE_DELETION_ENABLED config flags and guard"
  ```

---

### Task 4: Extend update_progress() with lifecycle_state and clear_marked_stale_at

**Files:**
- Modify: `src/codemie/rest_api/models/index.py` (lines 1056–1159)
- Modify: `tests/codemie/rest_api/models/test_index_info.py`

**Test-first: yes** — call `_build_progress_update_kwargs` directly; it's a pure dict-builder, no DB required.

- [ ] **Step 1: Write the failing tests**

  Append to `tests/codemie/rest_api/models/test_index_info.py`:

  ```python
  class TestBuildProgressUpdateKwargsLifecycle:
      def test_lifecycle_state_included_when_provided(self, index_info):
          kwargs = index_info._build_progress_update_kwargs(
              None, None, None, None, None, None, None, None, None, None, None, None, None,
              lifecycle_state=LifecycleState.ARCHIVED,
          )
          assert kwargs.get("lifecycle_state") == LifecycleState.ARCHIVED

      def test_lifecycle_state_excluded_when_none(self, index_info):
          kwargs = index_info._build_progress_update_kwargs(
              None, None, None, None, None, None, None, None, None, None, None, None, None,
          )
          assert "lifecycle_state" not in kwargs

      def test_clear_marked_stale_at_adds_marked_stale_at_none(self, index_info):
          kwargs = index_info._build_progress_update_kwargs(
              None, None, None, None, None, None, None, None, None, None, None, None, None,
              clear_marked_stale_at=True,
          )
          assert "marked_stale_at" in kwargs
          assert kwargs["marked_stale_at"] is None

      def test_clear_marked_stale_at_false_does_not_include_marked_stale_at(self, index_info):
          kwargs = index_info._build_progress_update_kwargs(
              None, None, None, None, None, None, None, None, None, None, None, None, None,
              clear_marked_stale_at=False,
          )
          assert "marked_stale_at" not in kwargs
  ```

  Also add the `LifecycleState` import at the top of the test file (it's already available via `from codemie.rest_api.models.index import IndexInfo, ...`); add `LifecycleState` to that import.

- [ ] **Step 2: Run to verify RED**

  ```bash
  pytest tests/codemie/rest_api/models/test_index_info.py::TestBuildProgressUpdateKwargsLifecycle -v
  ```

  Expected: `FAILED` — `TypeError: _build_progress_update_kwargs() got an unexpected keyword argument 'lifecycle_state'`

- [ ] **Step 3: Extend update_progress() signature**

  In `src/codemie/rest_api/models/index.py`, update `update_progress()` signature (starting at line 1056) to add two new keyword-only parameters at the end:

  ```python
  def update_progress(
      self,
      current_state: int | None = None,
      complete_state: int | None = None,
      completed: bool | None = None,
      error: bool | None = None,
      is_fetching: bool | None = None,
      is_queued: bool | None = None,
      current__chunks_state: int | None = None,
      processing_info: dict | None = None,
      processed_files: list | None = None,
      uploaded_files: list | None = None,
      text: str | None = None,
      last_reindex_triggered_at: datetime | None = None,
      tokens_usage: dict | None = None,
      lifecycle_state: "LifecycleState | None" = None,
      clear_marked_stale_at: bool = False,
  ) -> None:
  ```

  Also update the call to `_build_progress_update_kwargs` inside `update_progress` to pass the new params:

  ```python
  update_kwargs = self._build_progress_update_kwargs(
      current_state,
      complete_state,
      completed,
      error,
      is_fetching,
      is_queued,
      current__chunks_state,
      processing_info,
      processed_files,
      uploaded_files,
      text,
      last_reindex_triggered_at,
      tokens_usage,
      lifecycle_state=lifecycle_state,
      clear_marked_stale_at=clear_marked_stale_at,
  )
  ```

- [ ] **Step 4: Extend _build_progress_update_kwargs() signature and body**

  In `src/codemie/rest_api/models/index.py`, update `_build_progress_update_kwargs` signature (starting at line 1113) to add the two new keyword-only parameters:

  ```python
  def _build_progress_update_kwargs(
      self,
      current_state,
      complete_state,
      completed,
      error,
      is_fetching,
      is_queued,
      current__chunks_state,
      processing_info,
      processed_files,
      uploaded_files,
      text,
      last_reindex_triggered_at,
      tokens_usage,
      lifecycle_state=None,
      clear_marked_stale_at: bool = False,
  ) -> dict:
  ```

  Before `update_kwargs['update_date'] = datetime.now()` (line 1158), add:

  ```python
      if lifecycle_state is not None:
          update_kwargs['lifecycle_state'] = lifecycle_state
      if clear_marked_stale_at:
          update_kwargs['marked_stale_at'] = None
  ```

- [ ] **Step 5: Run to verify GREEN**

  ```bash
  pytest tests/codemie/rest_api/models/test_index_info.py::TestBuildProgressUpdateKwargsLifecycle -v
  ```

  Expected: `PASSED PASSED PASSED PASSED`

- [ ] **Step 6: Commit**

  ```bash
  git add src/codemie/rest_api/models/index.py tests/codemie/rest_api/models/test_index_info.py
  git commit -m "feat(EPMCDME-13161): extend update_progress with lifecycle_state and clear_marked_stale_at"
  ```

---

### Task 5: Add reactivation hook in complete_progress()

**Files:**
- Modify: `src/codemie/rest_api/models/index.py` (line 596)
- Modify: `tests/codemie/rest_api/models/test_index_info.py`

**Test-first: yes** — tests assert on in-memory object state after `complete_progress()`; DB calls go through the global `mock_database_engine` autouse fixture (no extra setup needed).

- [ ] **Step 1: Write the failing tests**

  Append to `tests/codemie/rest_api/models/test_index_info.py`:

  ```python
  class TestCompleteProgressReactivation:
      def test_complete_progress_reactivates_stale_datasource(self, mocker, index_info):
          mocker.patch("codemie.rest_api.models.base.BaseModelWithSQLSupport.update")
          from datetime import datetime
          index_info.lifecycle_state = LifecycleState.STALE
          index_info.marked_stale_at = datetime(2026, 1, 1)

          index_info.complete_progress()

          assert index_info.lifecycle_state == LifecycleState.ACTIVE
          assert index_info.marked_stale_at is None

      def test_complete_progress_reactivates_archived_datasource(self, mocker, index_info):
          mocker.patch("codemie.rest_api.models.base.BaseModelWithSQLSupport.update")
          from datetime import datetime
          index_info.lifecycle_state = LifecycleState.ARCHIVED
          index_info.marked_stale_at = datetime(2026, 1, 1)

          index_info.complete_progress()

          assert index_info.lifecycle_state == LifecycleState.ACTIVE
          assert index_info.marked_stale_at is None

      def test_set_error_does_not_reactivate_stale_datasource(self, mocker, index_info):
          mocker.patch("codemie.rest_api.models.base.BaseModelWithSQLSupport.update")
          index_info.lifecycle_state = LifecycleState.STALE

          index_info.set_error("some error")

          assert index_info.lifecycle_state == LifecycleState.STALE
  ```

- [ ] **Step 2: Run to verify RED**

  ```bash
  pytest tests/codemie/rest_api/models/test_index_info.py::TestCompleteProgressReactivation -v
  ```

  Expected: `FAILED` — `AssertionError: assert LifecycleState.STALE == LifecycleState.ACTIVE`

- [ ] **Step 3: Update complete_progress()**

  Replace the `complete_progress` method body at line 596:

  ```python
  def complete_progress(self, complete_state: int = None):
      if complete_state:
          self.complete_state = complete_state
          self.current_state = complete_state
      else:
          self.current_state = self.complete_state
      self.completed = True
      self.is_fetching = False
      self.is_queued = False
      self.error = False
      self.lifecycle_state = LifecycleState.ACTIVE
      self.marked_stale_at = None
      self.update_progress(
          current_state=self.current_state,
          complete_state=self.complete_state,
          completed=True,
          is_fetching=False,
          is_queued=False,
          error=False,
          lifecycle_state=LifecycleState.ACTIVE,
          clear_marked_stale_at=True,
      )
  ```

- [ ] **Step 4: Run to verify GREEN**

  ```bash
  pytest tests/codemie/rest_api/models/test_index_info.py::TestCompleteProgressReactivation -v
  ```

  Expected: `PASSED PASSED PASSED`

- [ ] **Step 5: Run full model test suite to check no regressions**

  ```bash
  pytest tests/codemie/rest_api/models/test_index_info.py -v
  ```

  Expected: all tests pass.

- [ ] **Step 6: Commit**

  ```bash
  git add src/codemie/rest_api/models/index.py tests/codemie/rest_api/models/test_index_info.py
  git commit -m "feat(EPMCDME-13161): reactivate datasource to ACTIVE on complete_progress"
  ```

---

### Task 6: Implement _delete_stale_indexes() and wiring

**Files:**
- Modify: `src/codemie/service/stale_datasource/stale_datasource_service.py`
- Modify: `tests/codemie/service/stale_datasource/test_stale_datasource_service.py`

**Test-first: yes** — tests call `_delete_stale_indexes()` directly with mocked session and ES client.

- [ ] **Step 1: Add test helpers and write the failing tests**

  Append to `tests/codemie/service/stale_datasource/test_stale_datasource_service.py`:

  ```python
  def _scalars_result(items):
      result = MagicMock()
      result.scalars.return_value.all.return_value = items
      return result


  def _make_stale_row(ds_id, project="proj", repo=None, index_type="knowledge_base_file"):
      ds = MagicMock()
      ds.id = ds_id
      ds.project_name = project
      ds.repo_name = repo or ds_id
      ds.index_type = index_type
      ds.lifecycle_state = LifecycleState.STALE
      ds.completed = True
      ds.is_fetching = False
      ds.is_queued = False
      ds.get_index_identifier.return_value = f"idx-{ds_id}"
      return ds


  class TestDeleteStaleIndexes:
      def _service(self) -> StaleDatasourceService:
          svc = _make_service()
          svc.session.commit = AsyncMock()
          return svc

      def _setup_session(self, svc, stale_rows, active_rows=None):
          """Configure session.execute side_effect for _delete_stale_indexes calls."""
          side_effects = [
              _scalars_result(stale_rows),
              _scalars_result(active_rows or []),
          ]
          if stale_rows and active_rows is None:
              # Add a mock for the sa_update execute call (archive step)
              side_effects.append(MagicMock())
          svc.session.execute = AsyncMock(side_effect=side_effects)

      @pytest.mark.asyncio
      async def test_returns_all_zero_stats_when_no_stale_rows(self):
          svc = self._service()
          svc.session.execute = AsyncMock(return_value=_scalars_result([]))

          stats = await svc._delete_stale_indexes()

          assert stats["stale_rows_swept"] == 0
          assert stats["indexes_deleted"] == 0
          assert stats["datasources_archived"] == 0
          svc.session.commit.assert_not_called()

      @pytest.mark.asyncio
      async def test_happy_path_single_stale_row_deleted_and_archived(self):
          svc = self._service()
          stale = _make_stale_row("ds-1")
          svc.session.execute = AsyncMock(side_effect=[
              _scalars_result([stale]),
              _scalars_result([]),
              MagicMock(),
          ])

          with (
              patch("asyncio.to_thread", new_callable=AsyncMock),
              patch("codemie.service.stale_datasource.stale_datasource_service.ElasticSearchClient") as mock_es_cls,
              patch("codemie.service.stale_datasource.stale_datasource_service.AgentMonitoringService") as mock_ams,
          ):
              mock_es_cls.get_client.return_value = MagicMock()
              stats = await svc._delete_stale_indexes()

          assert stats["stale_rows_swept"] == 1
          assert stats["indexes_deleted"] == 1
          assert stats["datasources_archived"] == 1
          assert stats["deletion_errors"] == 0
          assert not stats["deletion_aborted"]
          svc.session.commit.assert_called_once()
          mock_ams.send_count_metric.assert_called_once_with(
              name="stale_datasource_index_deleted",
              attributes={
                  "project": "proj",
                  "repo_name": "ds-1",
                  "datasource_type": "knowledge_base_file",
              },
          )

      @pytest.mark.asyncio
      async def test_group_archive_two_rows_same_index_one_delete_call_both_archived(self):
          svc = self._service()
          row1 = _make_stale_row("ds-1")
          row2 = _make_stale_row("ds-2", repo="ds-1")  # same index name
          row2.get_index_identifier.return_value = "idx-ds-1"  # same as row1
          svc.session.execute = AsyncMock(side_effect=[
              _scalars_result([row1, row2]),
              _scalars_result([]),
              MagicMock(),
          ])

          with (
              patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread,
              patch("codemie.service.stale_datasource.stale_datasource_service.ElasticSearchClient") as mock_es_cls,
              patch("codemie.service.stale_datasource.stale_datasource_service.AgentMonitoringService"),
          ):
              mock_es_cls.get_client.return_value = MagicMock()
              stats = await svc._delete_stale_indexes()

          assert stats["indexes_deleted"] == 1
          assert stats["datasources_archived"] == 2
          assert mock_thread.call_count == 1

      @pytest.mark.asyncio
      async def test_shared_index_guard_skips_stale_row_when_active_uses_same_index(self):
          svc = self._service()
          stale = _make_stale_row("ds-stale")
          active = MagicMock()
          active.get_index_identifier.return_value = "idx-ds-stale"  # same index as stale row
          svc.session.execute = AsyncMock(side_effect=[
              _scalars_result([stale]),
              _scalars_result([active]),
          ])

          with (
              patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread,
              patch("codemie.service.stale_datasource.stale_datasource_service.ElasticSearchClient"),
              patch("codemie.service.stale_datasource.stale_datasource_service.AgentMonitoringService"),
          ):
              stats = await svc._delete_stale_indexes()

          assert stats["skipped_shared_index"] == 1
          assert stats["indexes_deleted"] == 0
          assert mock_thread.call_count == 0
          svc.session.commit.assert_not_called()

      @pytest.mark.asyncio
      async def test_circuit_breaker_aborts_when_candidates_exceed_cap(self):
          svc = self._service()
          # 101 stale rows with unique index names — exceeds default cap of 100
          rows = [_make_stale_row(f"ds-{i}") for i in range(101)]
          svc.session.execute = AsyncMock(side_effect=[
              _scalars_result(rows),
              _scalars_result([]),
          ])

          with (
              patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread,
              patch("codemie.service.stale_datasource.stale_datasource_service.ElasticSearchClient"),
              patch("codemie.service.stale_datasource.stale_datasource_service.AgentMonitoringService"),
          ):
              stats = await svc._delete_stale_indexes()

          assert stats["deletion_aborted"] is True
          assert stats["indexes_deleted"] == 0
          assert mock_thread.call_count == 0
          svc.session.commit.assert_not_called()

      @pytest.mark.asyncio
      async def test_per_item_failure_continues_sweep_other_rows_still_archived(self):
          svc = self._service()
          row_fail = _make_stale_row("ds-fail")
          row_ok = _make_stale_row("ds-ok")
          svc.session.execute = AsyncMock(side_effect=[
              _scalars_result([row_fail, row_ok]),
              _scalars_result([]),
              MagicMock(),
          ])

          call_count = 0

          async def to_thread_side_effect(func, *args, **kwargs):
              nonlocal call_count
              call_count += 1
              if call_count == 1:
                  raise Exception("ES connection refused")

          with (
              patch("asyncio.to_thread", side_effect=to_thread_side_effect),
              patch("codemie.service.stale_datasource.stale_datasource_service.ElasticSearchClient") as mock_es_cls,
              patch("codemie.service.stale_datasource.stale_datasource_service.AgentMonitoringService"),
          ):
              mock_es_cls.get_client.return_value = MagicMock()
              stats = await svc._delete_stale_indexes()

          assert stats["deletion_errors"] == 1
          assert stats["indexes_deleted"] == 1
          assert stats["datasources_archived"] == 1
          svc.session.commit.assert_called_once()

      @pytest.mark.asyncio
      async def test_asyncio_to_thread_called_with_ignore_unavailable(self):
          svc = self._service()
          stale = _make_stale_row("ds-1")
          svc.session.execute = AsyncMock(side_effect=[
              _scalars_result([stale]),
              _scalars_result([]),
              MagicMock(),
          ])

          with (
              patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread,
              patch("codemie.service.stale_datasource.stale_datasource_service.ElasticSearchClient") as mock_es_cls,
              patch("codemie.service.stale_datasource.stale_datasource_service.AgentMonitoringService"),
          ):
              mock_es = MagicMock()
              mock_es_cls.get_client.return_value = mock_es
              await svc._delete_stale_indexes()

          mock_thread.assert_called_once_with(
              mock_es.indices.delete,
              index="idx-ds-1",
              ignore_unavailable=True,
          )


  class TestDeleteStaleIndexesWiring:
      @pytest.mark.asyncio
      async def test_deletion_not_called_when_flag_disabled(self):
          svc = _make_service()
          with (
              patch.object(svc, "_get_candidate_datasources", return_value=[]),
              patch.object(svc, "_fetch_lifecycle_metrics", return_value={}),
              patch.object(svc, "_fetch_tool_usage_metrics", return_value={}),
              patch.object(svc.session, "commit", new_callable=AsyncMock),
              patch.object(svc, "_delete_stale_indexes", new_callable=AsyncMock) as mock_delete,
              patch(
                  "codemie.service.stale_datasource.stale_datasource_service.config",
                  STALE_DATASOURCE_DELETION_ENABLED=False,
              ),
          ):
              stats = await svc.detect_and_mark_stale_datasources()

          mock_delete.assert_not_called()
          assert "stale_rows_swept" not in stats

      @pytest.mark.asyncio
      async def test_deletion_called_and_stats_merged_when_flag_enabled(self):
          svc = _make_service()
          deletion_stats = {
              "stale_rows_swept": 2,
              "indexes_deleted": 2,
              "datasources_archived": 2,
              "skipped_shared_index": 0,
              "deletion_errors": 0,
              "deletion_aborted": False,
          }
          with (
              patch.object(svc, "_get_candidate_datasources", return_value=[]),
              patch.object(svc, "_fetch_lifecycle_metrics", return_value={}),
              patch.object(svc, "_fetch_tool_usage_metrics", return_value={}),
              patch.object(svc.session, "commit", new_callable=AsyncMock),
              patch.object(svc, "_delete_stale_indexes", new_callable=AsyncMock, return_value=deletion_stats) as mock_delete,
              patch(
                  "codemie.service.stale_datasource.stale_datasource_service.config",
                  STALE_DATASOURCE_DELETION_ENABLED=True,
              ),
          ):
              stats = await svc.detect_and_mark_stale_datasources()

          mock_delete.assert_called_once()
          assert stats["stale_rows_swept"] == 2
          assert stats["indexes_deleted"] == 2
          assert stats["datasources_archived"] == 2
  ```

- [ ] **Step 2: Run to verify RED**

  ```bash
  pytest tests/codemie/service/stale_datasource/test_stale_datasource_service.py::TestDeleteStaleIndexes tests/codemie/service/stale_datasource/test_stale_datasource_service.py::TestDeleteStaleIndexesWiring -v
  ```

  Expected: `FAILED` — `AttributeError: 'StaleDatasourceService' object has no attribute '_delete_stale_indexes'`

- [ ] **Step 3: Add new imports to stale_datasource_service.py**

  In `src/codemie/service/stale_datasource/stale_datasource_service.py`, after the existing imports, add:

  ```python
  import asyncio

  from sqlalchemy import update as sa_update

  from codemie.clients.elasticsearch import ElasticSearchClient
  from codemie.service.monitoring.agent_monitoring_service import AgentMonitoringService
  ```

- [ ] **Step 4: Remove dead already_stale counter from detect_and_mark_stale_datasources()**

  In `detect_and_mark_stale_datasources`, change the stats dict (line 145) from:

  ```python
  stats = {
      "total_evaluated": 0,
      "newly_marked_stale": 0,
      "already_stale": 0,
      "errors": 0,
  }
  ```

  to:

  ```python
  stats = {
      "total_evaluated": 0,
      "newly_marked_stale": 0,
      "errors": 0,
  }
  ```

  Remove the dead code block inside the `for datasource in candidates` loop (lines 166–168):

  ```python
  # REMOVE this block:
  if datasource.lifecycle_state == LifecycleState.STALE:
      stats["already_stale"] += 1
      continue
  ```

- [ ] **Step 5: Add deletion wiring at the end of detect_and_mark_stale_datasources()**

  After `await self.session.commit()` (the commit for the marking phase, around line 193) and before the logger.info call, add:

  ```python
  if config.STALE_DATASOURCE_DELETION_ENABLED:
      deletion_stats = await self._delete_stale_indexes()
      stats.update(deletion_stats)
  ```

- [ ] **Step 6: Implement _delete_stale_indexes()**

  Add the following private method to `StaleDatasourceService` (after `_mark_as_stale`):

  ```python
  async def _delete_stale_indexes(self) -> dict:
      """Delete ES indexes for STALE datasources and advance them to ARCHIVED.

      Write ordering is crash-safe: ES delete first (ignore_unavailable=True),
      PG UPDATE to ARCHIVED second, committed once at sweep end.
      Rows that fail ES deletion stay STALE and are retried the next night.
      """
      stats = {
          "stale_rows_swept": 0,
          "indexes_deleted": 0,
          "datasources_archived": 0,
          "skipped_shared_index": 0,
          "deletion_errors": 0,
          "deletion_aborted": False,
      }

      # 1. Collect all idle STALE rows (exclude rows mid-reindex)
      stale_stmt = (
          select(IndexInfo)
          .where(IndexInfo.lifecycle_state == LifecycleState.STALE)
          .where(IndexInfo.completed == True)  # noqa: E712
          .where(
              or_(
                  IndexInfo.is_fetching == False,  # noqa: E712
                  IndexInfo.is_fetching.is_(None),
              )
          )
          .where(
              or_(
                  IndexInfo.is_queued == False,  # noqa: E712
                  IndexInfo.is_queued.is_(None),
              )
          )
      )
      stale_result = await self.session.execute(stale_stmt)
      stale_rows = list(stale_result.scalars().all())
      stats["stale_rows_swept"] = len(stale_rows)

      if not stale_rows:
          return stats

      # 2. Build shared-index guard: index names still used by ACTIVE datasources
      active_stmt = select(IndexInfo).where(IndexInfo.lifecycle_state == LifecycleState.ACTIVE)
      active_result = await self.session.execute(active_stmt)
      active_index_names = {row.get_index_identifier() for row in active_result.scalars().all()}

      # 3. Group stale rows by ES index name, skipping shared indexes
      candidates: dict[str, list[IndexInfo]] = defaultdict(list)
      for row in stale_rows:
          index_name = row.get_index_identifier()
          if index_name in active_index_names:
              stats["skipped_shared_index"] += 1
              logger.warning(
                  f"Skipping stale datasource {row.id} ({row.project_name}/{row.repo_name}): "
                  f"index '{index_name}' still used by an ACTIVE datasource"
              )
              continue
          candidates[index_name].append(row)

      # 4. Circuit breaker: abort entire phase if candidate count exceeds cap
      if len(candidates) > config.STALE_DATASOURCE_MAX_DELETIONS_PER_RUN:
          stats["deletion_aborted"] = True
          logger.error(
              f"Stale datasource deletion aborted: {len(candidates)} candidate indexes exceed "
              f"cap of {config.STALE_DATASOURCE_MAX_DELETIONS_PER_RUN}. "
              f"Raise STALE_DATASOURCE_MAX_DELETIONS_PER_RUN to proceed."
          )
          return stats

      # 5. Delete ES indexes; collect row IDs for the archive batch
      es_client = ElasticSearchClient.get_client()
      rows_to_archive: list[str] = []

      for index_name, rows in candidates.items():
          try:
              await asyncio.to_thread(
                  es_client.indices.delete, index=index_name, ignore_unavailable=True
              )
              stats["indexes_deleted"] += 1
              stats["datasources_archived"] += len(rows)
              rows_to_archive.extend(row.id for row in rows)
              for row in rows:
                  AgentMonitoringService.send_count_metric(
                      name=MetricName.STALE_DATASOURCE_INDEX_DELETED.value,
                      attributes={
                          "project": row.project_name,
                          "repo_name": row.repo_name,
                          "datasource_type": row.index_type,
                      },
                  )
              logger.info(
                  f"Deleted ES index '{index_name}' for datasource(s) "
                  f"{[row.id for row in rows]} ({rows[0].project_name}/{rows[0].repo_name})"
              )
          except Exception as e:
              stats["deletion_errors"] += 1
              logger.error(
                  f"Failed to delete ES index '{index_name}': {e}",
                  exc_info=True,
              )

      # 6. Single commit: archive all successfully deleted rows
      if rows_to_archive:
          await self.session.execute(
              sa_update(IndexInfo)
              .where(IndexInfo.id.in_(rows_to_archive))
              .values(lifecycle_state=LifecycleState.ARCHIVED)
          )
          await self.session.commit()

      return stats
  ```

- [ ] **Step 7: Run to verify GREEN**

  ```bash
  pytest tests/codemie/service/stale_datasource/test_stale_datasource_service.py::TestDeleteStaleIndexes tests/codemie/service/stale_datasource/test_stale_datasource_service.py::TestDeleteStaleIndexesWiring -v
  ```

  Expected: all `PASSED`

- [ ] **Step 8: Run the full stale datasource test suite**

  ```bash
  pytest tests/codemie/service/stale_datasource/ -v
  ```

  Expected: all existing tests pass.

- [ ] **Step 9: Commit**

  ```bash
  git add src/codemie/service/stale_datasource/stale_datasource_service.py tests/codemie/service/stale_datasource/test_stale_datasource_service.py
  git commit -m "feat(EPMCDME-13161): implement _delete_stale_indexes with circuit-breaker and shared-index guard"
  ```

---

### Task 7: Update scheduler log line

**Files:**
- Modify: `src/codemie/service/stale_datasource/scheduler.py` (lines 76–82)

**Test-first: no** — log-format change; no behaviour to unit-test.

- [ ] **Step 1: Update the log line**

  Replace the `logger.info` call at lines 76–82 in `_run_stale_detection`:

  Before:
  ```python
  logger.info(
      f"Stale datasource detection completed: "
      f"evaluated={stats['total_evaluated']}, "
      f"newly_stale={stats['newly_marked_stale']}, "
      f"already_stale={stats['already_stale']}, "
      f"errors={stats['errors']}"
  )
  ```

  After:
  ```python
  deletion_info = ""
  if stats.get("deletion_aborted"):
      deletion_info = ", deletion=ABORTED (circuit breaker)"
  elif stats.get("stale_rows_swept") is not None:
      deletion_info = (
          f", swept={stats['stale_rows_swept']}, "
          f"deleted={stats['indexes_deleted']}, "
          f"archived={stats['datasources_archived']}, "
          f"skipped_shared={stats['skipped_shared_index']}, "
          f"deletion_errors={stats['deletion_errors']}"
      )
  logger.info(
      f"Stale datasource detection completed: "
      f"evaluated={stats['total_evaluated']}, "
      f"newly_stale={stats['newly_marked_stale']}, "
      f"errors={stats['errors']}"
      f"{deletion_info}"
  )
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add src/codemie/service/stale_datasource/scheduler.py
  git commit -m "feat(EPMCDME-13161): update scheduler log to include deletion stats"
  ```

---

### Task 8: Add Helm values

**Files:**
- Modify: `deploy-templates/values.yaml`

**Test-first: no** — Helm template; no unit-testable behaviour.

- [ ] **Step 1: Locate the right insertion point**

  ```bash
  grep -n "STALE_DATASOURCE\|NATS" deploy-templates/values.yaml | head -20
  ```

  Find the section where STALE_DATASOURCE vars belong (after the NATS section, around line 196).

- [ ] **Step 2: Add all STALE_DATASOURCE env vars**

  Add the following block (all vars with safe defaults):

  ```yaml
  STALE_DATASOURCE_ENABLED: "false"
  STALE_DATASOURCE_SCHEDULE: "0 3 * * *"
  STALE_DATASOURCE_NO_USAGE_DAYS: "90"
  STALE_DATASOURCE_NO_UPDATE_DAYS: "120"
  STALE_DATASOURCE_GRACE_DAYS: "7"
  STALE_DATASOURCE_BATCH_SIZE: "100"
  STALE_DATASOURCE_DELETION_ENABLED: "false"
  STALE_DATASOURCE_MAX_DELETIONS_PER_RUN: "100"
  ```

- [ ] **Step 3: Commit**

  ```bash
  git add deploy-templates/values.yaml
  git commit -m "feat(EPMCDME-13161): add STALE_DATASOURCE env vars to Helm values"
  ```

---

## Post-Implementation Checklist

- [ ] `pytest tests/codemie/service/stale_datasource/ -v` — all pass
- [ ] `pytest tests/codemie/rest_api/models/test_index_info.py -v` — all pass
- [ ] `pytest tests/codemie/configs/test_config.py -v` — all pass
- [ ] `make ruff` (or `ruff check src/ tests/`) — no violations
- [ ] Verify `STALE_DATASOURCE_LOCK_ID` is `987654326` in `stale_datasource/config.py`
- [ ] Verify `STALE_DATASOURCE_LOCK_ID` does not appear as `987654324` anywhere in the codebase
