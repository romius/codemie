# EPMCDME-13733: Container Runtime None Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow `CODE_EXECUTOR_RUNTIME_CLASS_NAME=none` (or empty) to produce a Kubernetes Job manifest without a `runtimeClassName` key, enabling clusters that have no gVisor/kata-containers to schedule executor pods.

**Architecture:** The fix is two-layer: (1) `CodeExecutorConfig.runtime_class_name` changes from `str` to `str | None` with a `field_validator` that normalises `"none"` / `""` to `None`; (2) `BatchJobRunner._build_manifest()` conditionally omits the `runtimeClassName` key when the value is `None`. SHARED mode already omits the key and requires no changes.

**Tech Stack:** Python 3.12, Pydantic v2 (`field_validator`), Kubernetes Python client, pytest 8.3+.

## Global Constraints

- Use `str | None` union syntax — not `Optional[str]` — per `code-quality.md`.
- `field_validator(..., mode="before")` is the established normalisation pattern in `models.py` — match it exactly.
- Kubernetes requires the `runtimeClassName` key to be **absent** (not `null`, not `""`) to use the cluster-default runtime. Do NOT set it to `None` or `""` in the manifest dict.
- Conditional manifest key inclusion: `**({"runtimeClassName": cfg.runtime_class_name} if cfg.runtime_class_name else {})`.
- Do not touch SHARED mode (`code_executor_tool.py._create_default_pod_manifest`) — it already omits the key.
- Default `"gvisor"` behaviour is preserved; the only change is that `"none"` / `""` now resolve to absent key instead of a broken lookup.

---

### Task 1: Make `runtime_class_name` nullable in `CodeExecutorConfig`

**Files:**
- Modify: `src/codemie_tools/data_management/code_executor/models.py:61-64` (field definition)
- Modify: `src/codemie_tools/data_management/code_executor/models.py:346` (`from_env()` line)
- Test: `tests/codemie_tools/data_management/code_executor/test_models.py`

**Interfaces:**
- Consumes: nothing from prior tasks.
- Produces: `CodeExecutorConfig.runtime_class_name: str | None` — `None` when env var is `"none"`, `""`, or unset with explicit `"none"`. Default `"gvisor"` is unchanged.

- [ ] **Step 1: Write the failing tests**

Add these two test methods to `TestCodeExecutorConfigValidation` in `tests/codemie_tools/data_management/code_executor/test_models.py`:

```python
def test_runtime_class_name_none_string_normalised_to_none(self):
    """'none' string must be normalised to None by the validator."""
    config = CodeExecutorConfig(runtime_class_name="none")
    assert config.runtime_class_name is None

def test_runtime_class_name_empty_string_normalised_to_none(self):
    """Empty string must be normalised to None by the validator."""
    config = CodeExecutorConfig(runtime_class_name="")
    assert config.runtime_class_name is None
```

Also add this test method to the `TestCodeExecutorConfigFromEnv` class (or create one if it doesn't exist) in the same file:

```python
def test_from_env_runtime_class_name_none_string(self):
    """CODE_EXECUTOR_RUNTIME_CLASS_NAME=none must produce runtime_class_name=None."""
    with patch.dict(os.environ, {"CODE_EXECUTOR_RUNTIME_CLASS_NAME": "none"}, clear=False):
        config = CodeExecutorConfig.from_env()
    assert config.runtime_class_name is None

def test_from_env_runtime_class_name_empty_string(self):
    """CODE_EXECUTOR_RUNTIME_CLASS_NAME='' must produce runtime_class_name=None."""
    with patch.dict(os.environ, {"CODE_EXECUTOR_RUNTIME_CLASS_NAME": ""}, clear=False):
        config = CodeExecutorConfig.from_env()
    assert config.runtime_class_name is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/Stanislav_Kostenko/Documents/projects/epm-cdme/_dev/codemie
poetry run pytest tests/codemie_tools/data_management/code_executor/test_models.py::TestCodeExecutorConfigValidation::test_runtime_class_name_none_string_normalised_to_none tests/codemie_tools/data_management/code_executor/test_models.py::TestCodeExecutorConfigValidation::test_runtime_class_name_empty_string_normalised_to_none -v
```

Expected: FAIL — `ValidationError` (Pydantic rejects `None` on a `str` field) or assertion error.

- [ ] **Step 3: Change the field type to `str | None`**

In `src/codemie_tools/data_management/code_executor/models.py`, replace lines 61–64:

```python
# Before
runtime_class_name: str = Field(
    default="gvisor",
    description="Kubernetes runtimeClassName for executor pods",
)

# After
runtime_class_name: str | None = Field(
    default="gvisor",
    description="Kubernetes runtimeClassName for executor pods. "
    "Set to 'none' or empty to omit runtimeClassName from the Job manifest and use the cluster default.",
)
```

- [ ] **Step 4: Add the `field_validator` for normalisation**

Add this validator method to `CodeExecutorConfig` (after the `sandbox_mode` validator, before `security_threshold`):

```python
@field_validator("runtime_class_name", mode="before")
@classmethod
def validate_runtime_class_name(cls, v) -> str | None:
    if v is None:
        return None
    if isinstance(v, str) and (v.strip().lower() == "none" or v.strip() == ""):
        return None
    return v
```

- [ ] **Step 5: Update `from_env()` to pass `None`-capable value**

In `from_env()`, replace line 346:

```python
# Before
runtime_class_name=os.getenv("CODE_EXECUTOR_RUNTIME_CLASS_NAME", "gvisor"),

# After
runtime_class_name=os.getenv("CODE_EXECUTOR_RUNTIME_CLASS_NAME", "gvisor") or None,
```

Wait — the `or None` coalesces empty string to `None` but the `field_validator` handles it anyway. The cleaner approach passes through `os.getenv()` raw and lets the validator normalise it:

```python
runtime_class_name=os.getenv("CODE_EXECUTOR_RUNTIME_CLASS_NAME", "gvisor"),
```

No change to the `from_env()` call itself is needed: the validator receives the env string and normalises `"none"` / `""` to `None`. The `or None` is unnecessary because the validator covers it. Leave the `from_env()` line as-is.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
poetry run pytest tests/codemie_tools/data_management/code_executor/test_models.py -v
```

Expected: all tests PASS, including the four new ones.

- [ ] **Step 7: Commit**

```bash
git add src/codemie_tools/data_management/code_executor/models.py \
        tests/codemie_tools/data_management/code_executor/test_models.py
git commit -m "EPMCDME-13733: Allow runtime_class_name to be None in CodeExecutorConfig"
```

---

### Task 2: Conditionally omit `runtimeClassName` in `BatchJobRunner._build_manifest()`

**Files:**
- Modify: `src/codemie_tools/data_management/code_executor/batch_job_runner.py:238`
- Test: `tests/codemie_tools/data_management/code_executor/test_batch_job_runner.py`

**Interfaces:**
- Consumes: `CodeExecutorConfig.runtime_class_name: str | None` from Task 1.
- Produces: Kubernetes Job manifest dict where `spec.template.spec.runtimeClassName` is absent when `runtime_class_name is None`.

- [ ] **Step 1: Write the failing test**

Add this test method to `TestBatchJobRunnerHappyPath` in `tests/codemie_tools/data_management/code_executor/test_batch_job_runner.py`:

```python
def test_manifest_omits_runtime_class_name_when_none(self):
    """When runtime_class_name is None, runtimeClassName must be absent from the manifest."""
    config = _make_config(runtime_class_name=None)
    batch = MagicMock(name="batch")
    core = MagicMock(name="core")
    batch.read_namespaced_job_status.return_value = _terminal_status(succeeded=1)
    core.list_namespaced_pod.return_value = MagicMock(
        items=[_pod(phase="Running", exit_code=0, name="the-pod")]
    )
    core.read_namespaced_pod_log.return_value = ""

    with patch(
        "codemie_tools.data_management.code_executor.batch_job_runner.KubernetesClientManager"
    ) as mgr_cls:
        mgr = mgr_cls.return_value
        mgr.get_batch_client.return_value = batch
        mgr.get_client.return_value = core
        runner = BatchJobRunner(config)
        _patch_runner_internals(runner, pod_name="the-pod")
        with (
            patch.object(runner, "_upload_payload"),
            patch.object(runner, "_download_exports", return_value={}),
        ):
            runner.run("print('ok')")

    manifest = batch.create_namespaced_job.call_args.kwargs["body"]
    pod_spec = manifest["spec"]["template"]["spec"]
    assert "runtimeClassName" not in pod_spec
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
poetry run pytest tests/codemie_tools/data_management/code_executor/test_batch_job_runner.py::TestBatchJobRunnerHappyPath::test_manifest_omits_runtime_class_name_when_none -v
```

Expected: FAIL — `AssertionError: assert 'runtimeClassName' not in {'runtimeClassName': None, ...}`.

- [ ] **Step 3: Apply the conditional manifest fix**

In `src/codemie_tools/data_management/code_executor/batch_job_runner.py`, replace line 238:

```python
# Before
"runtimeClassName": cfg.runtime_class_name,

# After
**({"runtimeClassName": cfg.runtime_class_name} if cfg.runtime_class_name else {}),
```

The full `spec` dict context around the change (lines 236–242):

```python
"spec": {
    "restartPolicy": "Never",
    **({"runtimeClassName": cfg.runtime_class_name} if cfg.runtime_class_name else {}),
    "automountServiceAccountToken": False,
    "enableServiceLinks": False,
    "hostNetwork": False,
    "hostPID": False,
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
poetry run pytest tests/codemie_tools/data_management/code_executor/test_batch_job_runner.py -v
```

Expected: all tests PASS including the new `test_manifest_omits_runtime_class_name_when_none`.

- [ ] **Step 5: Run the full code-executor test suite**

```bash
poetry run pytest tests/codemie_tools/data_management/code_executor/ -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/codemie_tools/data_management/code_executor/batch_job_runner.py \
        tests/codemie_tools/data_management/code_executor/test_batch_job_runner.py
git commit -m "EPMCDME-13733: Omit runtimeClassName from Job manifest when runtime_class_name is None"
```
