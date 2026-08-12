# EPMCDME-10785: Per-call timeout for CommandLineTool

## Problem

`CommandLineTool` in `codemie_tools` uses a single, class-level default timeout (60 s)
for every command it runs. When `dotnet test` is invoked on a large solution the process
exceeds this limit. There is no way to override the timeout for a single command without
subclassing the tool, and the fallback value is a magic integer rather than a named constant.

Root cause location: `src/codemie_tools/data_management/file_system/tools.py`

```python
class CommandLineTool(CodeMieTool):
    timeout: int = 60  # ← magic literal, no per-call override
    ...
    def execute(self, command: str, *args, **kwargs) -> Any:
        ...
        result = subprocess.run(..., timeout=float(self.timeout))
```

## Scope

**In scope — `codemie` repo only.**

A secondary instance of this pattern exists in `codemie-plugins/toolkits/core/file_system_tools.py`.
That repository is no longer actively maintained and does not accept new MRs, so it is
explicitly excluded from this fix. If the plugin instance is re-activated in the future,
the same pattern can be applied there independently.

## Decision

Add an optional `timeout` parameter to `CommandLineInput` and `CommandLineTool.execute()`.
When the caller supplies a value it overrides the class-level default; when omitted the
default applies unchanged. Extract the magic integer into a named constant `DEFAULT_TIMEOUT`.

Backward compatibility is fully preserved: existing callers that omit `timeout` see no
behavioral change.

## Changes

### `src/codemie_tools/data_management/file_system/tools.py`

```python
DEFAULT_TIMEOUT = 60  # seconds

class CommandLineInput(BaseModel):
    command: str = Field(description="Command to execute in the CLI.")
    timeout: Optional[int] = Field(
        None,
        description=(
            "Timeout in seconds for this command. "
            "When provided, overrides the tool's default timeout. "
            f"When omitted, the default ({DEFAULT_TIMEOUT} s) is used."
        ),
    )

class CommandLineTool(CodeMieTool):
    timeout: int = DEFAULT_TIMEOUT
    ...
    def execute(self, command: str, timeout: Optional[int] = None, *args, **kwargs) -> Any:
        ...
        effective_timeout = timeout if timeout is not None else self.timeout
        result = subprocess.run(
            ["/bin/bash", "-c", full_command],
            cwd=work_dir, shell=False, text=True, capture_output=True,
            timeout=float(effective_timeout),
        )
```

## Behaviour

| Scenario | Before | After |
|---|---|---|
| Caller omits `timeout` | Uses class default 60 s | Same — no regression |
| Caller passes `timeout=3600` | Not possible | Uses 3600 s for this call only |
| Magic literal in source | `60` | `DEFAULT_TIMEOUT` constant |
| `dotnet test` exceeds default timeout | Process killed, error returned | Caller passes larger value; process completes |

## Acceptance Criteria Mapping

| AC | Satisfied by |
|---|---|
| Timeout aligns with user-configured value | Per-call `timeout` parameter overrides class default |
| Timeout value can be increased and is respected | `timeout` field accepted in schema; passed to `subprocess.run` |
| No hardcoded static timeouts | `DEFAULT_TIMEOUT` constant replaces magic `60` |
| Clear error messaging when timeout is reached | Existing `subprocess.TimeoutExpired` propagation unchanged |

## Out of Scope

- `codemie-plugins/toolkits/core/file_system_tools.py` — repo no longer accepting MRs.
- `codemie-plugins/cli/coding/tools.py` — separate context, separate fix if needed.
- Changes to any workflow template or agent prompt — callers can pass `timeout` explicitly
  now that the schema supports it.
