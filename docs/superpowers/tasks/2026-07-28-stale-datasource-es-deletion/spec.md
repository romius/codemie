# Spec: EPMCDME-13161 — Stale Datasource ES Index Deletion

## Context

`StaleDatasourceService` runs nightly via `StaleDatasourceScheduler` and marks unused
datasources with `lifecycle_state = STALE`. Today nothing consumes that state.
This feature adds the deletion phase: delete the ES index for each STALE datasource
and advance it to ARCHIVED. The marking phase is unchanged.

## Decisions

| Question | Decision |
|---|---|
| When to delete | Same nightly run — marking commits first, deletion phase follows when enabled |
| PG state after deletion | `ARCHIVED` — three-state lifecycle: `ACTIVE → STALE → ARCHIVED` |
| What is deleted | ES index only. No PG row, GitRepo clone, scheduler, or webhook teardown |
| Reactivation | `complete_progress()` unconditionally flips any lifecycle state back to `ACTIVE` |
| Safeguards | `STALE_DATASOURCE_DELETION_ENABLED` flag (default `False`) + `STALE_DATASOURCE_MAX_DELETIONS_PER_RUN` circuit-breaker (default `100`) |

Scheduler and webhook integrations are intentionally preserved on ARCHIVED datasources:
they are the auto-reactivation mechanism. When the scheduler fires, a full reindex runs
(the missing index causes incremental processors to fall back to full indexing), and
`complete_progress()` on success reactivates the datasource to ACTIVE.

## Architecture — bounded reconciling sweep

ARCHIVED is the idempotency marker: the working set is always bounded to STALE rows only.
Write ordering is crash-safe:
1. ES delete first (`ignore_unavailable=True`)
2. PG → ARCHIVED second, committed once at sweep end

Every failure mode converges without extra state:
- ES delete fails → row stays STALE → retried next night
- ES delete succeeds, PG commit fails → row stays STALE → next night delete is no-op, row archived
- Pre-existing STALE backlog → processed in the first enabled run

## Components

### 1. Config (`src/codemie/configs/config.py`)

Add to the existing stale config block (lines 748–754):

```python
STALE_DATASOURCE_DELETION_ENABLED: bool = False  # Enables ES index deletion phase
STALE_DATASOURCE_MAX_DELETIONS_PER_RUN: int = 100  # Circuit-breaker cap
```

Add a `finalize_settings` guard: raise `ValueError` if
`STALE_DATASOURCE_DELETION_ENABLED=True` and `STALE_DATASOURCE_ENABLED=False`.

### 2. Lock ID fix (`src/codemie/service/stale_datasource/config.py`)

`STALE_DATASOURCE_LOCK_ID = 987654324` collides with
`_SPEND_TRACKING_RESET_RECONCILIATION_LOCK_ID = 987654324` in
`src/codemie/service/spend_tracking/scheduler.py`. Both jobs run nightly; one
silently fails to acquire the lock. Fix:

```python
# Full lock registry: CA=987654321, Spend=987654322/323/324, LB=987654325
STALE_DATASOURCE_LOCK_ID = 987654326
```

### 3. `IndexInfo.update_progress()` extension (`src/codemie/rest_api/models/index.py`)

Add two parameters to `update_progress()` and `_build_progress_update_kwargs()`:

```python
def update_progress(
    self,
    ...,
    lifecycle_state: LifecycleState | None = None,
    clear_marked_stale_at: bool = False,
) -> None: ...

# in _build_progress_update_kwargs:
if lifecycle_state is not None:
    update_kwargs['lifecycle_state'] = lifecycle_state
if clear_marked_stale_at:
    update_kwargs['marked_stale_at'] = None
```

`clear_marked_stale_at` is a boolean flag rather than a nullable datetime because
the existing `_build_progress_update_kwargs` skips `None` values — a plain
`marked_stale_at=None` argument would be silently dropped from the SQL UPDATE.

### 4. Reactivation hook (`src/codemie/rest_api/models/index.py` — `complete_progress()`)

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
    self.lifecycle_state = LifecycleState.ACTIVE   # new
    self.marked_stale_at = None                    # new
    self.update_progress(
        current_state=self.current_state,
        complete_state=self.complete_state,
        completed=True,
        is_fetching=False,
        is_queued=False,
        error=False,
        lifecycle_state=LifecycleState.ACTIVE,     # new
        clear_marked_stale_at=True,                # new
    )
```

Covers STALE→ACTIVE and ARCHIVED→ACTIVE in one hook. `set_error()` and
`_reset_state()` / `set_queued()` are unchanged: a STALE/ARCHIVED datasource keeps
its lifecycle state while a rebuild is in flight, and becomes ACTIVE only on
confirmed success.

### 5. `StaleDatasourceService._delete_stale_indexes()` (`src/codemie/service/stale_datasource/stale_datasource_service.py`)

New private method. `detect_and_mark_stale_datasources()` calls it after the marking
commit when `STALE_DATASOURCE_DELETION_ENABLED=True`; its stats are merged into the
returned dict.

**Flow:**

1. **Collect stale rows** — `SELECT` all `IndexInfo` with
   `lifecycle_state=STALE, completed=True, is_fetching IS NOT TRUE, is_queued IS NOT TRUE`.
   Excludes datasources mid-reindex.

2. **Build shared-index guard** — query all ACTIVE rows, compute
   `get_index_identifier()` for each in memory. Result: set of ES index names still
   in use by live datasources.

3. **Resolve candidates** — for each stale row compute its index name:
   - Name in guard set → skip, row stays STALE (`skipped_shared_index` stat + warning log).
     Handles legacy-KB naming collisions; the physical index is deleted only after every
     datasource resolving to it is stale.
   - Multiple stale rows resolving to the same name are grouped into one candidate;
     on successful deletion **all** rows in the group are archived.

4. **Circuit breaker** — if `len(candidates) > STALE_DATASOURCE_MAX_DELETIONS_PER_RUN`,
   abort the whole deletion phase before any delete (error-level log with count).
   A mass qualification is more likely a detection bug or metrics outage than a real
   cleanup need.

5. **Delete and archive** — for each candidate:
   ```python
   await asyncio.to_thread(
       es_client.indices.delete, index=name, ignore_unavailable=True
   )
   ```
   Per-item try/except (one failure does not stop the sweep; counted in
   `deletion_errors`, row stays STALE). On success, set
   `lifecycle_state=ARCHIVED` on every row in the group and emit
   `stale_datasource_index_deleted` count metric with
   `{project, repo_name, datasource_type}` attributes via
   `AgentMonitoringService.send_count_metric`.

6. **Commit** all ARCHIVED updates once at sweep end.

**ES client:** sync `ElasticSearchClient.get_client()` wrapped in
`asyncio.to_thread`. `IndexInfo.delete()` must NOT be used — it also removes GitRepo
rows, scheduler integrations, and webhook integrations.

**Stats keys added:**
`stale_rows_swept, indexes_deleted, datasources_archived, skipped_shared_index, deletion_errors, deletion_aborted`

### 6. Scheduler (`src/codemie/service/stale_datasource/scheduler.py`)

The scheduler already logs all keys from the stats dict. No structural change needed;
the new deletion stat keys are picked up automatically. Verify the log line at
lines 76–82 includes the merged keys.

### 7. Helm values (`deploy-templates/values.yaml`)

All `STALE_DATASOURCE_*` env vars are absent from the Helm chart. Add all of them
with safe defaults:

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

## Error handling

| Failure | Behaviour |
|---|---|
| ES unreachable | Abort deletion phase, error log. Marking results unaffected. |
| Single `indices.delete` fails | Counted as `deletion_errors`; sweep continues; row stays STALE |
| ES delete succeeds, PG commit fails / pod crash | Rows stay STALE; next night delete is no-op, rows archived |
| Cap exceeded | Phase aborted before any delete; error log with qualifying count |
| Detection phase failure | Unchanged existing behaviour; deletion phase never starts |

## Out of scope

- No PG row deletion of any kind
- No API or UI changes
- No schema migration (`ARCHIVED` enum value exists since migration `f9g0h1i2j3k4`)
- Detection logic and thresholds unchanged
- `STALE_DATASOURCE_NO_UPDATE_DAYS` drift (pre-existing, separate ticket)

## Testing

Extend `tests/codemie/service/stale_datasource/test_stale_datasource_service.py`
with a `TestDeleteStaleIndexes` class. Test cases:

- **Flag disabled** — `STALE_DATASOURCE_DELETION_ENABLED=False`: no ES calls, stats dict has no deletion keys
- **Happy path** — STALE row → ES index deleted → row ARCHIVED, correct stats and metric emitted
- **Backlog sweep** — STALE row not marked this run → deleted and archived
- **Idempotency** — STALE row whose ES index is already absent → `ignore_unavailable` no-op, row still archived
- **ARCHIVED rows excluded** — sweep never selects ARCHIVED rows
- **Shared-index guard** — STALE row resolving to same index name as ACTIVE row → skipped, stays STALE
- **Shared-index guard: last active becomes stale** — after the ACTIVE row is also STALE, both are deleted together
- **Group archive** — two STALE rows resolving to one index → single delete call, both ARCHIVED
- **In-flight protection** — STALE row with `is_fetching=True` / `is_queued=True` / `completed=False` excluded
- **Circuit breaker** — candidates over cap → no deletes, no archives, `deletion_aborted=True`
- **Per-item ES failure** — one delete fails → other candidates still processed and archived, failed row stays STALE
- **Reactivation: STALE→ACTIVE** — `complete_progress()` on STALE datasource sets `lifecycle_state=ACTIVE`, clears `marked_stale_at`
- **Reactivation: ARCHIVED→ACTIVE** — `complete_progress()` on ARCHIVED datasource sets `lifecycle_state=ACTIVE`, clears `marked_stale_at`
- **`set_error()` does not reactivate** — STALE/ARCHIVED datasource keeps lifecycle state after `set_error()`
