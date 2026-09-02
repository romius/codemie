# Technical Research

**Task**: workflow yaml_config_history repository bedrock
**Generated**: 2026-08-13
**Research path**: filesystem

---

## 1. Original Context

Remove all files that were added in this branch. Then review docs/EPMCDME-8827-backend-hardening-to-keep.md and proceed with hardening

Host-verified facts used to ground research (not paraphrased requirements):

- Repository: `/Users/bohdan_maliar/Projects/codemie-dev/codemie`
- Current branch: `EPMCDME-8827_workflow-versioning-clean` at `3464e2ad5`
- Base: `origin/main`
- Hardening source of truth (untracked): `docs/EPMCDME-8827-backend-hardening-to-keep.md`
- Reference implementation tip: `6e5588a99`
- Vs `origin/main` this branch currently ADDS only docs (no net `src/` / `tests/` diff). Versions platform was already stripped by assistants-parity cleanup.

Requirements extracted from the hardening doc (KEEP only; versions platform is a non-goal):

1. `WorkflowConfigRepository.update_excluding_history`
2. `WorkflowConfigRepository.update_schema_url`
3. `WorkflowService.save_workflow_schema` → `update_schema_url`
4. `WorkflowService._yaml_content_changed`
5. `_update_workflow_values`: history-safe persist + prepend history **only if** YAML changed (via `_update_workflow_history`, **not** `create_version_atomic`)
6. Bedrock `_create_or_update_entity` update path → `update_excluding_history`
7. Tests listed in hardening doc §4

Do **not** restore: versions API, `seed_initial_version`, `create_version_atomic`, rollback, `YamlConfigHistory` version fields, Alembic backfill, `WorkflowVersionHistoryCorruptError`, `get_raw_workflow`, `_project_legacy_history`.

---

## 2. Codebase Findings

### Existing Implementations

**Current HEAD is `origin/main`-equivalent for application code.** `git diff --stat origin/main...HEAD -- src tests` is empty. History-safe helpers from tip `6e5588a99` are absent.

#### Model — `src/codemie/core/workflow_models/workflow_config.py`

- `YamlConfigHistory` (lines 57–60): prior-only fields `yaml_config`, `date`, `created_by`. No `version_id` / `version_number` / `is_current` / `created_at` / `superseded_at`.
- `WorkflowConfigBase.yaml_config_history` (lines 96–98): `List[YamlConfigHistory]` stored as JSONB via `PydanticListType`.
- Table `workflows` (`WorkflowConfig`).

#### ORM wipe primitive — `src/codemie/rest_api/models/base.py`

`BaseModelWithSQLSupport.update` (lines 518–530):

- Sets `update_date`.
- `session.get` existence check.
- **`session.merge(self)`** then commit.

This is the **only** `session.merge` call in `src/`. Any `WorkflowConfig.update()` / `entity.update()` writes the in-memory `yaml_config_history` (often `[]` or stale) over the DB column.

`save()` (lines 500–516) is insert-only (`session.add`) and is not a wipe of an existing history column.

#### Repository — `src/codemie/repository/workflow_config_repository.py` (HEAD)

Current class has only:

- `set_publish_state` — column-scoped SQL for `is_global` / `categories`; `NotFoundException` when `rowcount == 0`.
- `recompute_unique_users_count` — atomic SQL recompute.

**Missing on HEAD:** `update_excluding_history`, `update_schema_url`.

**At reference tip `6e5588a99` (KEEP, copy as-is except docstring):**

```python
def update_excluding_history(self, workflow: WorkflowConfig) -> None:
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
    with WorkflowConfig.get_engine().begin() as conn:
        result = conn.execute(
            text("UPDATE workflows SET schema_url = :url WHERE id = :wf_id").bindparams(
                url=schema_url, wf_id=workflow_id
            )
        )
    if result.rowcount == 0:
        raise NotFoundException(f"Workflow '{workflow_id}' not found")
```

Tip also contains versions-platform methods that **must not** be restored: `seed_initial_version`, `create_version_atomic`, `rollback_to_version_atomic`, `_version_entry`, `_history_for_*`, `_to_utc_iso`, `_coerce_history`.

#### Service — `src/codemie/service/workflow_service.py` (HEAD)

- **No `__init__`**, no `self._workflow_repo`. Tip had `def __init__(self) -> None: self._workflow_repo = WorkflowConfigRepository()`.
- **No `import yaml`**. Tip imported `yaml` for `_yaml_content_changed`.
- `create_workflow` (119–150): `workflow_config.save(refresh=True)` — no history seed. Correct for prior-only (do not add `seed_initial_version`).
- `update_workflow` (166–173): delegates to `_update_workflow_values`.
- `save_workflow_schema` (543–558): writes SVG via `FileRepositoryFactory`, sets `workflow_config.schema_url`, then **`workflow_config.update()`** (full merge). Tip used `self._workflow_repo.update_schema_url(str(workflow_config.id), result.to_encoded_url())`.
- `_update_workflow_history` (637–649): **present**. Raw SQL prepend `SET yaml_config_history = :new_history_entry || yaml_config_history`, then `workflow_config.refresh()`. Entry shape is `{yaml_config, date, created_by}` via `YamlConfigHistory.model_dump(mode="json")`. **Keep this method; it is the history writer for the adapted path.**
- `_update_workflow_values` (651–693) current behavior:
  1. Always builds `YamlConfigHistory` from **current** `stored_config.yaml_config`.
  2. Copies `_editable_non_boolean_fields` (skips `None`).
  3. If `yaml_config` among updates: `parse_execution_config()`.
  4. Updates `shared` / `start_hint` / `updated_by`.
  5. **`stored_config.update(refresh=True)`** — full ORM merge (wipe).
  6. **Always** `_update_workflow_history(...)` — even metadata-only / unchanged YAML.

- **`_yaml_content_changed`**: absent on HEAD.

**Tip `6e5588a99` `_yaml_content_changed` (semantic baseline, adapted to treat absent YAML as empty):**

```python
@staticmethod
def _yaml_content_changed(old_yaml: Optional[str], new_yaml: Optional[str]) -> bool:
    if old_yaml == new_yaml:
        return False
    try:
        return yaml.safe_load(old_yaml or "") != yaml.safe_load(new_yaml or "")
    except yaml.YAMLError:
        return old_yaml != new_yaml
```

Normalizing `None` to an empty YAML document before parsing ensures `None`, an empty string, and comment-only YAML are semantically equivalent. A comment-only update therefore cannot replace an absent stored config or prepend history.

**Adapted `_update_workflow_values` (KEEP intent, rewire history writer):**

Tip flow:

1. Capture `old_yaml = stored_config.yaml_config`.
2. Apply editable fields / shared / start_hint / `updated_by` (same as HEAD).
3. `yaml_changed = self._yaml_content_changed(old_yaml, stored_config.yaml_config)`.
4. If changed: temporarily restore `old_yaml`, `update_excluding_history`, then **`create_version_atomic`** (writes live YAML + canonical history together), restore new YAML, `refresh()`.
5. If not changed: restore `old_yaml` (prevents comment-only drift), `update_excluding_history` only.

**Adaptation required because `create_version_atomic` is a non-goal:** that method wrote `yaml_config` **and** history in one SQL UPDATE. `_update_workflow_history` only prepends history; it does **not** write live `yaml_config`. Therefore the yaml-changed branch cannot copy the tip’s “persist old YAML then let atomic write the new YAML” sequence.

Correct adapted sequence:

1. Capture `old_yaml`.
2. Apply fields as today.
3. `yaml_changed = _yaml_content_changed(old_yaml, stored_config.yaml_config)`.
4. If **not** changed: restore `stored_config.yaml_config = old_yaml` (comment/formatting-only must not drift live YAML); `self._workflow_repo.update_excluding_history(stored_config)`; **do not** call `_update_workflow_history`.
5. If **changed**: persist **new** live YAML via `self._workflow_repo.update_excluding_history(stored_config)` (do not restore old YAML first — otherwise live YAML never advances); then `_update_workflow_history(stored_config, YamlConfigHistory(yaml_config=old_yaml, date=now, created_by=user.as_user_model()))`. `_update_workflow_history` already `refresh()`es.

Do **not** restore HEAD’s “always prepend after every update”.

#### Bedrock — `src/codemie/service/aws_bedrock/bedrock_flow_service.py` (HEAD)

- **No import** of `WorkflowConfigRepository`.
- `_create_or_update_entity` (618–656):
  - Update branch (alias already in map): copy fields (`name`, `description`, `project`, `mode`, `shared`, `states`, `custom_nodes`, `bedrock`) then **`entity.update(refresh=True)`**.
  - Create branch: `ensure_application_exists` + `workflow_config.save(refresh=True)`. No seed (correct; do not add `seed_initial_version`).

**Tip update branch (KEEP):** `WorkflowConfigRepository().update_excluding_history(entity)` instead of `entity.update(refresh=True)`.

**Tip create branch (DO NOT KEEP):** `WorkflowConfigRepository().seed_initial_version(...)`. Leave HEAD create branch as `save(refresh=True)` only.

#### Marketplace pattern already in use

`src/codemie/service/workflow_config/workflow_marketplace_service.py` is the only current caller of `WorkflowConfigRepository` (`set_publish_state`, `recompute_unique_users_count`). Same column-scoped SQL style as `update_schema_url`.

### Architecture and Layers Affected

| Layer | Components |
|---|---|
| **API / router** | `src/codemie/rest_api/routers/workflow.py` — `PUT` update → `WorkflowService.update_workflow` then background `save_workflow_schema`; `POST` create → `save()` then background schema. No router signature change expected. `src/codemie/rest_api/routers/vendor.py` — Bedrock import → `_create_or_update_entity`. |
| **Service** | `WorkflowService` (`save_workflow_schema`, `_update_workflow_values`, add `_yaml_content_changed` + `_workflow_repo`); `BedrockFlowService._create_or_update_entity`. |
| **Repository** | `WorkflowConfigRepository` — add `update_excluding_history`, `update_schema_url`. |
| **DB / persistence** | `workflows.yaml_config_history` JSONB; `BaseModelWithSQLSupport.update` → `session.merge`. No Alembic change (model already prior-only). |
| **External** | AWS Bedrock import path; `FileRepositoryFactory` for schema SVG (`CODEMIE_STORAGE_BUCKET_NAME`). |
| **Agent / workflow execution** | Not on the persist path. `LLMRetirementService._retire_workflows` mutates YAML via `session.add` without history prepend — out of KEEP scope. |

Likely file change surface (application + tests): **6 files**.

- `src/codemie/repository/workflow_config_repository.py`
- `src/codemie/service/workflow_service.py`
- `src/codemie/service/aws_bedrock/bedrock_flow_service.py`
- `tests/codemie/repository/test_workflow_config_repository.py` (**create**; missing on HEAD)
- `tests/codemie/service/test_workflow_service.py` (**adapt** existing update tests + add hardening tests)
- `tests/codemie/service/aws_bedrock/test_bedrock_flow_service.py` (**add** update-path assertion)

Plus deleting the 23 branch-added docs (and gitignored extras in those dirs). No model/migration/router files.

### Integration Points

**Internal**

- `WorkflowService` is constructed as a module-level singleton in routers. Adding `__init__` that only assigns `_workflow_repo` is compatible.
- `BedrockFlowService` already holds a module-level `WorkflowService` for delete/unimport; persist hardening uses a **local** `WorkflowConfigRepository()` (tip pattern), not the workflow service.
- `WorkflowConfigIndexService` `defer`s `yaml_config_history` on list — unrelated to persist.
- File storage: `save_workflow_schema` → `FileRepositoryFactory().get_current_repository().write_file(...)`.

**External**

- AWS Bedrock (`bedrock-agent` / `bedrock-agent-runtime`) via `BedrockFlowService`; credentials from Settings, not env for this path.
- PostgreSQL JSONB `||` prepend in `_update_workflow_history`.
- No message queue / feature flag / new env var.

**Wipe call sites (WorkflowConfig full-row merge)**

| Call site | File | Mechanism |
|---|---|---|
| `_update_workflow_values` | `workflow_service.py:682` | `stored_config.update(refresh=True)` **before** history prepend |
| `save_workflow_schema` | `workflow_service.py:556` | `workflow_config.update()` after setting `schema_url` only — can wipe history written earlier in the same request |
| Bedrock `_create_or_update_entity` update | `bedrock_flow_service.py:641` | `entity.update(refresh=True)` |

Not wipe paths: `set_publish_state`, `recompute_unique_users_count`, `_update_workflow_history` (SQL prepend only), `create_workflow` / Bedrock create `.save()`.

### Patterns and Conventions

- Layering: router → service → repository (`.ai-run/guides/architecture/layered-architecture.md`).
- Column-scoped SQL already lives on `WorkflowConfigRepository` (`engine.begin()`, parameterized `text()`, `NotFoundException` on `rowcount == 0`) — **copy this style for `update_schema_url`**.
- `update_excluding_history` is session-get + copy mapped columns except `id` and `yaml_config_history` (not raw SQL). Needs `from sqlmodel import Session` and `from datetime import datetime` on the repository (HEAD currently imports neither).
- History prepend stays on the service (`_update_workflow_history`), not a versions-platform atomic.
- Tests mirror `src/` under `tests/codemie/`. Nearby repo tests use `class Test…` + `unittest.mock.patch`; workflow/Bedrock service tests are mostly free functions + fixtures.
- Do not add versions-platform exceptions (`WorkflowVersionHistoryCorruptError` is absent from HEAD `codemie.core.exceptions`).

### Files to delete (branch-added vs `origin/main`)

`git diff --name-only --diff-filter=A origin/main...HEAD` = **23 tracked files**. No `src/` / `tests/` adds. Delete these (and the two task directories) to satisfy “remove all files that were added in this branch”:

1. `docs/EPMCDME-8827-assistants-parity-backend.md`
2. `docs/superpowers/tasks/2026-08-11-epmcdme-8827-workflow-version-history-backend-update/.state.json`
3. `docs/superpowers/tasks/2026-08-11-epmcdme-8827-workflow-version-history-backend-update/code-review-check.json`
4. `docs/superpowers/tasks/2026-08-11-epmcdme-8827-workflow-version-history-backend-update/code-review-final.json`
5. `docs/superpowers/tasks/2026-08-11-epmcdme-8827-workflow-version-history-backend-update/complexity-assessment.md`
6. `docs/superpowers/tasks/2026-08-11-epmcdme-8827-workflow-version-history-backend-update/decisions.jsonl`
7. `docs/superpowers/tasks/2026-08-11-epmcdme-8827-workflow-version-history-backend-update/events.jsonl`
8. `docs/superpowers/tasks/2026-08-11-epmcdme-8827-workflow-version-history-backend-update/gate-plan.json`
9. `docs/superpowers/tasks/2026-08-11-epmcdme-8827-workflow-version-history-backend-update/plan.md`
10. `docs/superpowers/tasks/2026-08-11-epmcdme-8827-workflow-version-history-backend-update/qa-report.md`
11. `docs/superpowers/tasks/2026-08-11-epmcdme-8827-workflow-version-history-backend-update/spec.md`
12. `docs/superpowers/tasks/2026-08-11-epmcdme-8827-workflow-version-history-backend-update/technical-analysis.md`
13. `docs/superpowers/tasks/2026-08-13-epmcdme-8827-assistants-parity-cleanup/.state.json`
14. `docs/superpowers/tasks/2026-08-13-epmcdme-8827-assistants-parity-cleanup/code-review-final.json`
15. `docs/superpowers/tasks/2026-08-13-epmcdme-8827-assistants-parity-cleanup/complexity-assessment.json`
16. `docs/superpowers/tasks/2026-08-13-epmcdme-8827-assistants-parity-cleanup/complexity-assessment.md`
17. `docs/superpowers/tasks/2026-08-13-epmcdme-8827-assistants-parity-cleanup/decisions.jsonl`
18. `docs/superpowers/tasks/2026-08-13-epmcdme-8827-assistants-parity-cleanup/events.jsonl`
19. `docs/superpowers/tasks/2026-08-13-epmcdme-8827-assistants-parity-cleanup/gate-plan.json`
20. `docs/superpowers/tasks/2026-08-13-epmcdme-8827-assistants-parity-cleanup/plan.md`
21. `docs/superpowers/tasks/2026-08-13-epmcdme-8827-assistants-parity-cleanup/qa-report.md`
22. `docs/superpowers/tasks/2026-08-13-epmcdme-8827-assistants-parity-cleanup/spec.md`
23. `docs/superpowers/tasks/2026-08-13-epmcdme-8827-assistants-parity-cleanup/technical-analysis.md`

**Gitignored extras on disk (not in git; `.gitignore:70` `docs/superpowers/tasks/**/*.diff`).** Removing the task dirs should remove them; they are not in the 23-file git list:

- `docs/superpowers/tasks/2026-08-11-epmcdme-8827-workflow-version-history-backend-update/code-review-check.diff`
- `docs/superpowers/tasks/2026-08-11-epmcdme-8827-workflow-version-history-backend-update/code-review.diff`
- `docs/superpowers/tasks/2026-08-13-epmcdme-8827-assistants-parity-cleanup/code-review.diff`

**Do not delete as part of that cleanup:**

- `docs/EPMCDME-8827-backend-hardening-to-keep.md` — untracked **source of truth** for this run.
- `docs/superpowers/tasks/2026-08-13-epmcdme-8827-backend-hardening/` — this run directory.
- `.ai-run/sdlc-factory/doctor.json` — modified, not a branch-added docs file.

---

## 3. Documentation Findings

### Guides and Architecture Docs

Guides exist under `.ai-run/guides/`. Relevant:

| Guide | Relevance |
|---|---|
| `.ai-run/guides/architecture/layered-architecture.md` | Router → service → repository |
| `.ai-run/guides/architecture/service-layer-patterns.md` | Orchestration in `workflow_service.py` |
| `.ai-run/guides/data/repository-patterns.md` | Extend matching repository; do not persist from service via ORM when a repo method exists |
| `.ai-run/guides/data/database-patterns.md` | SQLModel sessions; Alembic under `src/external/alembic/versions/` — **no new migration for this task** |
| `.ai-run/guides/testing/testing-patterns.md` | Mirror `src/` under `tests/codemie/` |
| `.ai-run/guides/testing/testing-service-patterns.md` | Mock repositories in service tests |
| `.ai-run/guides/integration/cloud-integrations.md` | AWS / file repositories (schema SVG) |
| `.ai-run/guides/workflows/langgraph-workflows.md` | Execution only — does not document YAML history |

No dedicated Bedrock persist guide.

**SoT for this task:** `docs/EPMCDME-8827-backend-hardening-to-keep.md` (untracked).

Related (to be deleted with the 23 files): `docs/EPMCDME-8827-assistants-parity-backend.md`; task trees `2026-08-11-epmcdme-8827-workflow-version-history-backend-update/` and `2026-08-13-epmcdme-8827-assistants-parity-cleanup/`.

`CHANGELOG.md` has no `EPMCDME-8827` / `yaml_config_history` / `update_excluding_history` entries.

### Architectural Decisions

1. Assistants UX parity correctly removed the versions platform (prior-only `{yaml_config, date, created_by}` prepend). **Do not reverse that.**
2. Hardening-to-keep.md: still restore history-safe persist + semantic YAML skip. The 2026-08-13 parity-cleanup **approved spec/plan** said “do not keep `update_excluding_history` / `update_schema_url`”; **this run’s SoT overrides that** — restore the helpers.
3. No formal ADR files. Feature-area source has no `NOTE:` / `TODO:` / `ADR:` markers on the persist path.
4. Tip `6e5588a99` is the cherry-pick reference; strip versions-platform symbols and rewire history append to `_update_workflow_history`.

### Derived Conventions

- Marketplace already uses column-scoped SQL on `WorkflowConfigRepository` — extend that class rather than adding ORM merge workarounds in the service.
- `_update_workflow_history` JSONB `||` prepend is the prior-only history writer to keep.
- Service tests currently **lock in** `WorkflowConfig.update(refresh=True)`; those assertions must move to `_workflow_repo.update_excluding_history`.

---

## 4. Testing Landscape

### Existing Coverage

`tests/codemie/repository/test_workflow_config_repository.py` **does not exist** on HEAD.

`tests/codemie/service/test_workflow_service.py` — pytest free functions, fixtures (`user`, `admin_user`, `user_model`, `workflow_config`, `create_workflow_request`, `update_workflow_request` parametrized `shared`, `update_workflow_request_defaults`, `workflow_service`), `unittest.mock.patch` / `MagicMock`. **No** `yaml_config_history` / `update_excluding_history` / `_yaml_content_changed` / `_update_workflow_history` / `save_workflow_schema` coverage.

Update-path tests that **currently assert ORM `update(refresh=True)`** and will need adapting:

- `test_update_workflow` — `mock_update.assert_called_once_with(refresh=True)` plus `mock_refresh` (refresh today comes from `_update_workflow_history` after merge).
- `test_update_workflow_nothing_to_update`
- `test_update_workflow_clears_description_with_empty_string`
- `test_update_workflow_skips_none_fields`
- `test_yaml_reflects_assistants_and_states` — ORM update once
- `test_invalid_yaml_logs_error` — `mock_update.assert_not_called()` when `ParserError`
- `test_update_workflow_values_start_hint_updated_when_different` — patches `update`/`refresh`
- `test_update_workflow_values_start_hint_cleared`

`tests/codemie/service/aws_bedrock/test_bedrock_flow_service.py` — fixtures `mock_user`, `mock_setting`, `mock_aws_creds`, `flow_data`, `flow_aliases`, `flow_version_info`, `flow_import`. `test_import_entities_success` **patches** `_create_or_update_entity` and does not exercise persist. No `test_create_or_update_entity_update_excludes_history` on HEAD.

Grep under `tests/` for `yaml_config_history`, `update_excluding_history`, `update_schema_url`, `_yaml_content_changed`, `_update_workflow_history`: **zero matches**.

### Testing Framework and Patterns

- pytest 8.x (`pytest.ini`: `testpaths = tests`, `pythonpath = src`, `--import-mode=importlib`).
- Service tests: module-level `@pytest.fixture`, `@patch` on `codemie.core.workflow_models.WorkflowConfig.update` / `.refresh`, `@pytest.mark.parametrize` for field variants. **No** `class Test*` in the two service files.
- Repository tests nearby (e.g. `tests/codemie/repository/test_category_repository.py`): `class Test…` + `MagicMock` + `patch`.
- Tip repo tests used: `repo` fixture, `_make_engine_mock(rowcount=…)` patching `WorkflowConfig.get_engine().begin()`, assert SQL text contains `schema_url` and **not** `yaml_config_history`, `pytest.raises(NotFoundException)`.
- Tip service tests assigned `workflow_service._workflow_repo = MagicMock()` then asserted call/not-call. After adding `__init__`, tests can still replace `_workflow_repo` on the instance.
- Tip Bedrock test: `@patch("codemie.service.aws_bedrock.bedrock_flow_service.WorkflowConfigRepository")` then `_create_or_update_entity(...)` with `existing_entities_map={"alias-1": existing}` and `mock_repo.update_excluding_history.assert_called_once_with(existing)`. Drop the sibling `test_create_or_update_entity_seeds_initial_version_on_create`.

### Coverage Gaps

Must add / adapt (hardening doc §4; drop versions-platform asserts):

**New file `tests/codemie/repository/test_workflow_config_repository.py`:**

- `TestUpdateSchemaUrl.test_executes_column_scoped_update` / `test_raises_not_found_for_missing_workflow` — copy from tip (lines 415–432 of tip file).
- Direct `update_excluding_history` tests were **absent at the tip**. Add them (hardening doc: “keep any direct tests if present”; they were not present, so introduce): copies all mapped columns except `id` and `yaml_config_history`; raises `NotFoundException` when `session.get` returns `None`. Pattern: patch `Session` / `WorkflowConfig.get_engine` similarly to other repo tests.

**Do not copy from tip:** `TestSeedInitialVersion`, `TestCreateVersionAtomic`, `TestRollbackToVersionAtomic`, `TestToUtcIso`, `TestSeedInitialVersionTimestamps`.

**Adapt `tests/codemie/service/test_workflow_service.py`:**

- Existing update tests: stop patching `WorkflowConfig.update`; inject `workflow_service._workflow_repo = MagicMock()` and assert `update_excluding_history`.
- Add `class TestYamlContentChanged` from tip (identical strings / parametrized formatting-only pairs / absent-empty-comment equivalence / different / invalid YAML fallback) — no versions symbols.
- YAML-changed → `_update_workflow_history` called once (adapt tip `test_update_workflow_yaml_change_creates_version` which asserted `create_version_atomic`).
- Metadata-only / same YAML / comment-only → `_update_workflow_history` **not** called; `update_excluding_history` called; comment-only restores old live YAML (adapt tip `test_update_workflow_metadata_only_does_not_create_version`, `test_update_workflow_same_yaml_does_not_create_version`, `test_update_workflow_comment_only_yaml_does_not_drift_live_config`).
- `test_save_workflow_schema_does_not_call_orm_update` — copy from tip (FileRepositoryFactory mock + assert `update_schema_url` + `WorkflowConfig.update` not called).
- Composite preserve-history: adapt `test_get_raw_then_update_then_save_schema_preserves_yaml_config_history` — **drop `get_raw_workflow`** (gone and a non-goal). Keep assertion that update + `save_workflow_schema` do not wipe `yaml_config_history` / that schema goes through `update_schema_url`. Use `get_workflow` or a loaded `WorkflowConfig` fixture instead of `get_raw_workflow`. Do not use `is_current` / `version_id` in history fixtures.

**Do not restore:** `test_yaml_change_refreshes_stored_config_after_version_create` as written (canonical `is_current` post-condition); `test_get_raw_workflow_returns_full_history`; any `create_version_atomic` / `seed_initial_version` asserts.

**Add to `test_bedrock_flow_service.py`:**

- Copy tip `test_create_or_update_entity_update_excludes_history` (patch `WorkflowConfigRepository`; update-existing path).
- **Do not** copy `test_create_or_update_entity_seeds_initial_version_on_create`.

---

## 5. Configuration and Environment

### Environment Variables

None control `yaml_config_history` persist. Adjacent only:

- `CODEMIE_STORAGE_BUCKET_NAME` — schema SVG owner in `save_workflow_schema`.
- `AWS_BEDROCK_*` / Settings AWS creds — Bedrock client, not history.
- `WORKFLOW_GENERATION_ENABLED`, `WORKFLOW_MAX_CONCURRENCY` — unrelated.

No `os.environ` / feature flag for wipe protection.

### Configuration Files

- `src/codemie/configs/config.py` — `BaseSettings`; not involved in history persist.
- `.env.example` / `tests/.env.test` — no history keys.
- Alembic: `src/external/alembic/versions/8f7d26d3ff1c_create_workflows.py` created `yaml_config_history` JSONB. Bedrock column: `1c40ba0a37e6_add_bedrock_data_to_workflow.py`. **No new migration.** Versions-platform backfill `w3x4y5z6a7b8_…` is absent — do not restore.

### Feature Flags and Deployment Concerns

No flag, Helm key, Dockerfile, or CI change for this hardening. Application-code only. Secrets (Vault / `aws-secrets`) are unrelated.

---

## 6. Risk Indicators

- **History wipe on current HEAD:** three full-row merges (`_update_workflow_values`, `save_workflow_schema`, Bedrock update) go through `session.merge` in `base.py:528`. Schema save can wipe history prepended earlier in the same PUT.
- **Always-prepend on HEAD** creates duplicate/noise history on metadata-only and comment-only YAML edits. Hardening must **not** keep that behavior.
- **`create_version_atomic` cannot be copied:** it wrote live YAML + history together. Adapted yaml-changed path must persist new YAML via `update_excluding_history` **then** prepend old YAML via `_update_workflow_history`. Copying the tip’s “restore old YAML before persist” sequence without the atomic would leave live YAML unchanged.
- **Existing service tests encode the wipe path** (`mock_update.assert_called_once_with(refresh=True)`). Leaving them unchanged will fail after the persist switch; they must be adapted in the same change.
- **`WorkflowService` has no `_workflow_repo` today.** Call sites and tests must gain `__init__` + instance repo (or equivalent). Bedrock should instantiate `WorkflowConfigRepository()` locally as at the tip.
- **`tests/codemie/repository/test_workflow_config_repository.py` missing.** Tip’s file is dominated by versions-platform suites; only `TestUpdateSchemaUrl` is directly reusable. `update_excluding_history` had **no** dedicated tests at the tip — a coverage gap to fill, not a cherry-pick.
- **Parity-cleanup spec conflict:** approved 2026-08-13 plan forbade keeping wipe helpers. This run’s SoT is hardening-to-keep.md. Do not follow the cleanup plan’s “drop helpers” constraint.
- **No env/flag/docs in CHANGELOG** for this persist behavior — behavior is hard-coded.
- **Out-of-scope writer:** `LLMRetirementService._retire_workflows` updates workflow YAML without history prepend or `update_excluding_history`. Not in the KEEP checklist; do not expand scope.
- **Do not restore versions platform** (explicit): `seed_initial_version`, `create_version_atomic`, rollback, `YamlConfigHistory` version fields, Alembic `w3x4y5z6a7b8`, `WorkflowVersionHistoryCorruptError`, `get_raw_workflow`, `_project_legacy_history`, `workflow_versions` router/service.
- codegraph MCP was unavailable (`server does not exist: codegraph`) — research used filesystem exploration plus `git show 6e5588a99`.

---

## 7. Summary for Complexity Assessment

This task touches **three application layers** (repository, workflow service, Bedrock service) plus **three test files**, and a docs-only cleanup of **23 tracked files** added on this branch vs `origin/main`. There is **no net `src/`/`tests/` diff vs main today**; HEAD already matches main’s prior-only prepend + full ORM merge. Estimated change surface is about **6 code/test files**, no Alembic, no API contract change, no env/flag work.

Technical novelty is **low**: `update_excluding_history` and `update_schema_url` exist verbatim at tip `6e5588a99` and follow the same column-scoped repository style as `set_publish_state`. `_yaml_content_changed` is a small static helper. The one non-mechanical piece is **rewiring the yaml-changed branch** off `create_version_atomic` onto existing `_update_workflow_history` without leaving live YAML stuck or always-prepending like main.

Test coverage is **mixed-to-poor for the KEEP behavior**: update tests exist but they assert the wipe path; repository hardening tests are absent; Bedrock persist is mocked away. Tip tests can be cherry-picked for `TestUpdateSchemaUrl`, `TestYamlContentChanged`, schema ORM-skip, and Bedrock `update_excluding_history`, then adapted to drop versions-platform symbols. Key scoring risks: adapting existing update tests in the same change, getting the persist/prepend order right without `create_version_atomic`, and not reintroducing the versions platform under a hardening label.
