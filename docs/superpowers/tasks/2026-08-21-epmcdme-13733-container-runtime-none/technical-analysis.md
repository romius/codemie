# Technical Research

**Task**: container runtime job definition executor
**Generated**: 2026-08-21T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

EPMCDME-13733: Need to implement support for setting container runtime to 'none'. Currently it's not supported and job definition requires passing at least any container runtime name. The Jira ticket is at https://jiraeu.epam.com/browse/EPMCDME-13733. The goal is to allow users to set container runtime to 'none' (no container runtime), which is currently blocked by validation or required fields in job definitions.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie_tools/data_management/code_executor/models.py` — `CodeExecutorConfig` Pydantic v2 model; defines `runtime_class_name: str = Field(default="gvisor")`; loaded from `CODE_EXECUTOR_RUNTIME_CLASS_NAME` env var via `from_env()` classmethod; field has no validator and no `None`/`"none"` handling
- `src/codemie_tools/data_management/code_executor/batch_job_runner.py` — `BatchJobRunner` singleton; `_build_manifest()` at line 238 writes `"runtimeClassName": cfg.runtime_class_name` unconditionally into the Kubernetes Job pod spec; this is the only place in the codebase that emits `runtimeClassName` into a live K8s manifest
- `src/codemie_tools/data_management/code_executor/code_executor_tool.py` — `CodeExecutorTool`; dispatches between JOBS (`BatchJobRunner`) and SHARED (`SandboxSessionManager`) execution modes; `_create_default_pod_manifest()` for SHARED mode does NOT include `runtimeClassName` at all — SHARED mode already behaves as "no explicit runtime class"
- `src/codemie_tools/data_management/code_executor/session_manager.py` — `SandboxSessionManager` singleton; manages shared pod pool; receives pod manifest dict but does not read `runtime_class_name` directly
- `src/codemie_tools/data_management/code_executor/session_factory.py` — `SessionFactory`; creates `ArtifactSandboxSession` objects for SHARED mode; does not reference `runtimeClassName`

### Architecture and Layers Affected

- **Configuration/model layer** (`models.py`): `runtime_class_name` field definition, type annotation (`str` → `str | None`), `from_env()` env var parsing logic, and optional `field_validator` for normalization of `"none"` / `""` sentinel values
- **Infrastructure/runner layer** (`batch_job_runner.py`): `_build_manifest()` — the Kubernetes Job manifest constructor; conditional inclusion of `"runtimeClassName"` key based on whether `cfg.runtime_class_name` is non-null
- **Tool/business-logic layer** (`code_executor_tool.py`): SHARED mode `_create_default_pod_manifest()` does not need changes; JOBS mode dispatch path is unaffected beyond the config model change flowing through to `BatchJobRunner`
- **Test layer** (`tests/codemie_tools/data_management/code_executor/`): `test_batch_job_runner.py` and `test_models.py` both require updates and new test cases

### Integration Points

- **Kubernetes API** (`kubernetes` Python client): `BatchV1Api.create_namespaced_job()` receives the manifest dict built by `_build_manifest()`; Kubernetes interprets `runtimeClassName: ""` differently than absent key — absence is required to use the cluster default runtime; setting the string `"none"` would cause K8s to look for a `RuntimeClass` object named `"none"` and fail pod scheduling
- **`llm-sandbox`** (`llm_sandbox.ArtifactSandboxSession`): used in SHARED mode only; not affected by this change
- **`CodeMieTool` base** (`codemie_tools.base`): base class consumed by `CodeExecutorTool`; not affected
- **`codemie.repository.base_file_repository`**: file export path in `CodeExecutorTool`; not affected

### Patterns and Conventions

- Pydantic v2 `field_validator(..., mode="before")` is the established pattern for config normalization — used for `execution_mode`, `sandbox_mode`, and `security_threshold` in `models.py`; the same pattern should be applied for `runtime_class_name` to convert `"none"` / `""` to `None`
- `from_env()` classmethod populates `CodeExecutorConfig` from `os.getenv()` — `CODE_EXECUTOR_RUNTIME_CLASS_NAME` defaults to `"gvisor"`; the loader must map empty-string or the literal string `"none"` to Python `None`
- Type annotations follow modern Python 3.12+ style per `code-quality.md`: `str | None`, not `Optional[str]`
- Kubernetes pod manifest is a plain `dict` — conditional key inclusion uses dict unpacking: `**({"runtimeClassName": value} if value else {})`
- Singleton pattern with `__new__` + double-checked locking on `BatchJobRunner` and `SandboxSessionManager` — singleton state does not affect this change
- `_make_config(**overrides)` factory pattern in tests is used to build `CodeExecutorConfig` with minimal valid state

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/development/configuration-patterns.md` — configuration fields must go through `CodeMieToolConfig`/`from_env()`; directly governs how the nullable `runtime_class_name` field and env var mapping should be structured
- `.ai-run/guides/architecture/layered-architecture.md` — config changes belong in the config/model layer; execution logic belongs in the tool/runner layer; the separation is already correct in this codebase
- `.ai-run/guides/architecture/service-layer-patterns.md` — code executor is correctly scoped to `codemie_tools`; orchestration boundary is `CodeExecutorTool`
- `.ai-run/guides/api/endpoint-conventions.md` — optional fields use `str | None` in Pydantic models; relevant to the field type change
- `.ai-run/guides/development/error-handling.md` — typed exceptions and single failure-mode messages; relevant if a validator raises on invalid `runtime_class_name` values
- `.ai-run/guides/standards/code-quality.md` — mandates `str | None` union syntax, parameterized generics, Python 3.12+ type hints

### Architectural Decisions

- **JOBS/SHARED asymmetry is intentional**: JOBS mode (`batch_job_runner.py`) always sets `runtimeClassName` in the manifest; SHARED mode (`code_executor_tool.py._create_default_pod_manifest`) never sets it. This is a deliberate design, not an omission — SHARED mode already runs without an explicit runtime class.
- **Defense-in-depth security layering**: disabling `runtimeClassName` is an explicit security downgrade of the kernel-isolation layer only; other layers (AST check, Python monkeypatching, K8s resource limits) remain active. The ticket intentionally permits this tradeoff.
- **No Helm-layer exposure**: `deploy-templates/values.yaml` does not expose `CODE_EXECUTOR_RUNTIME_CLASS_NAME`; operators must inject it via `extraEnv`. No Helm changes are needed for this task, but the omission may be worth a follow-up.

### Derived Conventions

- `from_env()` env var for nullable fields: use `os.getenv("VAR_NAME") or None` pattern (returns `None` for both absent and empty-string cases), or an explicit sentinel check for `"none"` (case-insensitive)
- Kubernetes `"runtimeClassName"` key must be absent — not `null`, not `""` — to use the cluster default runtime; conditional dict unpacking is the idiomatic Python approach
- Both manifest builders must be kept in sync; however SHARED mode already omits the key, so only JOBS mode (`batch_job_runner.py`) requires a code change

---

## 4. Testing Landscape

### Existing Coverage

- `tests/codemie_tools/data_management/code_executor/test_models.py` — `CodeExecutorConfig` field defaults and validators; tests `runtime_class_name == "gvisor"` (default) and override to `"kata-containers"`; uses `@patch.dict('os.environ', ...)` for env var isolation
- `tests/codemie_tools/data_management/code_executor/test_batch_job_runner.py` — `BatchJobRunner.run()` happy/error paths; line 145 hard-asserts `pod_spec["runtimeClassName"] == "gvisor"`; uses `_make_config(**overrides)` factory and `_patch_runner_internals()` helper
- `tests/codemie_tools/data_management/code_executor/test_execution_modes.py` — `CodeExecutorTool` execution mode dispatch
- `tests/codemie_tools/data_management/code_executor/test_sandbox_dispatch.py` — dispatch between SHARED and JOBS modes
- `tests/codemie_tools/data_management/code_executor/test_code_executor_tool.py` — `CodeExecutorTool` init and schema
- `tests/codemie_tools/data_management/code_executor/test_security_config.py` — `CodeExecutorConfig` + security policy integration
- `tests/codemie_tools/data_management/code_executor/test_sandbox_guard.py` — sandbox guard enforcement
- `tests/codemie_tools/data_management/code_executor/test_export_path_validation.py` — export path validation

### Testing Framework and Patterns

- pytest 8.3+ with `unittest.TestCase` style classes throughout the code executor test suite
- `_make_config(**overrides)` factory in `test_batch_job_runner.py` — minimal valid `CodeExecutorConfig` with per-test overrides; must be extended to support `runtime_class_name=None`
- `MagicMock` / `patch` from `unittest.mock` — used pervasively to patch `KubernetesClientManager`, `BatchJobRunner`, `SandboxSessionManager`
- `@patch.dict('os.environ', ...)` and `patch.dict(os.environ, {}, clear=True)` — env var isolation per test method
- `BatchJobRunner._instance = None` reset in `setUp()` — singleton teardown between tests
- Session-scoped `autouse` fixture `mock_database_engine` in `conftest.py` stubs Postgres

### Coverage Gaps

- No test for `runtime_class_name = "none"` or `runtime_class_name = None` in `CodeExecutorConfig` — neither `test_models.py` nor `test_batch_job_runner.py` covers this case
- No test asserting that `"runtimeClassName"` key is **absent** from the JOBS manifest when `runtime_class_name` is `None` or `"none"` — `test_batch_job_runner.py:145` only asserts the key is present and equals `"gvisor"`
- No test for the `from_env()` path where `CODE_EXECUTOR_RUNTIME_CLASS_NAME` is set to `"none"` or `""` — the expected behavior (map to `None`) is untested
- No test for the env var validator / `field_validator` that normalizes `"none"` to `None` (this validator does not yet exist)

---

## 5. Configuration and Environment

### Environment Variables

- `CODE_EXECUTOR_RUNTIME_CLASS_NAME` — sets `runtime_class_name` in `CodeExecutorConfig`; default `"gvisor"`; passed unconditionally into the Kubernetes Job pod spec as `runtimeClassName`; currently has no handling for `"none"` or empty string values
- `CODE_EXECUTOR_SANDBOX_MODE` — selects between `sandbox-shared` (SHARED pool) and `sandbox-jobs` (one Job per execution); `runtimeClassName` is only emitted in the JOBS path
- `CODE_EXECUTOR_EXECUTION_MODE` — only `"sandbox"` accepted; not affected by this task
- `CODE_EXECUTOR_NAMESPACE` — Kubernetes namespace for executor pods; not affected

### Configuration Files

- `src/codemie_tools/data_management/code_executor/models.py` — `CodeExecutorConfig`: primary config model governing all executor settings including `runtime_class_name`
- `src/codemie/configs/config.py` — global app `Config`; references code executor via `DYNAMIC_CODE_INTERPRETER_TOOLS` and `HTTP_BLOCKED_TOOLS`; no direct `runtime_class_name` field
- `deploy-templates/values.yaml` — Helm chart values; only exposes `features.tools.code_executor.rbac.namespace`; `CODE_EXECUTOR_RUNTIME_CLASS_NAME` is not exposed at the Helm layer

### Feature Flags and Deployment Concerns

- `sandbox_mode` (`SandboxMode` enum): SHARED vs JOBS — only JOBS mode emits `runtimeClassName`; the `"none"` fix only affects the JOBS code path
- `execution_mode` (`ExecutionMode` enum): only `SANDBOX` supported; not affected
- Kubernetes treats `runtimeClassName: ""` and `runtimeClassName: "none"` as string lookups for `RuntimeClass` objects — both will fail scheduling if no matching `RuntimeClass` exists; the key must be entirely absent to use the cluster default runtime
- No Helm-layer toggle for `CODE_EXECUTOR_RUNTIME_CLASS_NAME` — operators must inject the env var manually via `extraEnv`; this is a pre-existing gap and out of scope for this ticket

---

## 6. Risk Indicators

- **Hardcoded test assertion at `test_batch_job_runner.py:145`**: `assert pod_spec["runtimeClassName"] == "gvisor"` — this test will not break due to the change (the default behavior is preserved), but it will not cover the new `"none"` path; a new test case must be added alongside
- **`runtime_class_name` field is typed `str`, not `str | None`**: changing the type to `str | None` may break downstream code that passes `cfg.runtime_class_name` to Kubernetes client methods expecting a `str`; the manifest builder in `_build_manifest()` is the only consumer and must be updated atomically
- **Silent misconfiguration risk**: setting `CODE_EXECUTOR_RUNTIME_CLASS_NAME=none` currently would produce `runtimeClassName: "none"` in the Job spec, causing a Kubernetes `RuntimeClass` lookup failure and pod scheduling errors; no validation or error message exists today to catch this
- **SHARED mode already omits `runtimeClassName`** — no code change is needed for SHARED mode, but documentation or a code comment should clarify the asymmetry to prevent future regressions
- **Singleton state in `BatchJobRunner`**: `_instance` holds a reference to the config at construction time; if the singleton is already initialized with the old config, a new `runtime_class_name=None` setting will not take effect until the singleton is reset (`_instance = None`); this is only relevant in tests, where `setUp()` already resets the singleton
- **No Helm exposure of `CODE_EXECUTOR_RUNTIME_CLASS_NAME`**: operators cannot currently set this value via the Helm chart `values.yaml`; the fix is deployable only via manual `extraEnv` injection — this is a pre-existing gap, not introduced by this ticket, but worth noting
- **Security-sensitive change**: disabling the container runtime sandbox (gVisor/kata-containers) is a deliberate security downgrade; the ticket appears to intend this for legitimate use cases (e.g. clusters without gVisor); no additional auth or permission guard is proposed

---

## 7. Summary for Complexity Assessment

The task touches three architectural layers: the configuration/model layer (`models.py`), the infrastructure/runner layer (`batch_job_runner.py`), and the test layer. The total file change surface is small — likely 3–4 files: `models.py` (field type and `from_env()` change, plus an optional `field_validator`), `batch_job_runner.py` (one conditional in `_build_manifest()`), `test_models.py` (one new test case for `None`/`"none"` env var mapping), and `test_batch_job_runner.py` (one new test asserting `runtimeClassName` key absence). The SHARED mode path (`code_executor_tool.py`) requires no changes as it already omits `runtimeClassName`.

Technically, this task follows well-established patterns in the codebase: the `field_validator(mode="before")` pattern for config normalization is already used for three other fields in `models.py`, and the dict-unpacking pattern for conditional manifest keys is idiomatic Python. The only novel aspect is the introduction of a nullable `str | None` field in a config model that previously had only non-nullable string fields — this is a minor pattern extension, not a new pattern. The `from_env()` sentinel value mapping (`"none"` → `None`) is a straightforward addition.

Test coverage for the affected area is moderate: `BatchJobRunner` and `CodeExecutorConfig` both have dedicated test files with a factory-based pattern that makes adding new cases easy. The primary gap is that no test currently covers the `"none"` runtime class path or asserts that `runtimeClassName` is absent from the manifest. These gaps must be closed as part of this task. Key risk factors for complexity scoring: the Kubernetes API behavior (absent key vs. empty string vs. `"none"` string) requires precise conditional logic; the singleton pattern in `BatchJobRunner` means test isolation requires the existing `setUp()` reset; and the security implications of disabling the runtime sandbox layer should be flagged in the MR description.
