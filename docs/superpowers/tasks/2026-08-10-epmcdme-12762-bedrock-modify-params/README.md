# EPMCDME-12762: Bedrock `modify_params` Bug Reproduction & Fix

## Summary

LiteLLM raises `UnsupportedParamsError` when a Bedrock request contains
`tool_choice` or tool-use history without a `tools` array. This happens during
OpenCode compaction: the compaction request carries the full conversation
history (including tool calls / tool results) but does not include a `tools`
definition, triggering the error before any network call is made.

**Fix**: add `modify_params: true` to `litellm_settings` in
`litellm_config.yaml`. LiteLLM then auto-injects a dummy `tools` array to
satisfy Bedrock's requirement.

---

## Reproduction Script

**File**: `reproduce_epmcdme_12762.py` (project root)

```python
"""
Reproduction script for EPMCDME-12762.
LiteLLM raises UnsupportedParamsError BEFORE any network call,
so fake AWS credentials are enough to hit the validation layer.
"""
import litellm

MODEL = "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0"
FAKE_CREDS = dict(
    aws_access_key_id="fake",
    aws_secret_access_key="fake",
    aws_region_name="us-west-2",
)

# Conversation with tool use in history — no tools= defined.
# This is what OpenCode sends during compaction: the full history including
# tool_calls/tool results, but the summarization request itself has no tools array.
MESSAGES = [
    {"role": "user", "content": "list files in this directory"},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "tc_1",
            "type": "function",
            "function": {"name": "bash", "arguments": '{"command": "ls"}'},
        }],
    },
    {"role": "tool", "tool_call_id": "tc_1", "content": "README.md\npackage.json"},
    {"role": "user", "content": "Summarize our conversation so far"},
]

def attempt(label: str):
    print(f"\n{'='*60}")
    print(f"  {label}")
    print('='*60)
    try:
        litellm.completion(model=MODEL, messages=MESSAGES, tool_choice="auto", **FAKE_CREDS)
        print("  No error raised (request reached network layer)")
    except litellm.exceptions.UnsupportedParamsError as e:
        print(f"  REPRODUCED — UnsupportedParamsError:\n  {e}")
    except Exception as e:
        print(f"  Got past param validation (hit auth/network error as expected):\n  {type(e).__name__}: {str(e)[:120]}")

# BEFORE fix
litellm.modify_params = False
attempt("BEFORE fix  (modify_params=False)")

# AFTER fix
litellm.modify_params = True
attempt("AFTER fix   (modify_params=True)")
```

**How to run** (from `codemie/`):
```bash
poetry run python ../reproduce_epmcdme_12762.py
```

---

## Results

### Before fix — `modify_params=False`

```
============================================================
  BEFORE fix  (modify_params=False)
============================================================
  REPRODUCED — UnsupportedParamsError:
  litellm.UnsupportedParamsError: Bedrock doesn't support tool calling without
  `tools=` param specified. Pass `tools=` param OR set
  `litellm.modify_params = True` // `litellm_settings::modify_params: True`
  to add dummy tool to the request.
```

The error is raised by LiteLLM's validation layer **before** any network call,
confirming the root cause.

### After fix — `modify_params=True`

```
============================================================
  AFTER fix   (modify_params=True)
============================================================
  Got past param validation (hit auth/network error as expected):
  AuthenticationError: litellm.AuthenticationError: BedrockException Invalid
  Authentication - {"message":"The security token included in the re..."}
```

The `UnsupportedParamsError` is gone. The request reaches Bedrock's network
layer and fails only due to the intentionally fake AWS credentials — confirming
the fix works.

---

## Root Cause

During OpenCode's compaction flow the full conversation history is sent as
context for summarisation. When that history contains `tool_calls` /
`role: "tool"` messages, LiteLLM's Bedrock provider requires a `tools` array
to be present in the same request. Without it, LiteLLM raises
`UnsupportedParamsError` before the request ever leaves the proxy.

`drop_params: true` (already set globally) does **not** cover this case because
the parameter is not being dropped — it is missing in the first place.

---

## Fix Applied

**File**: `litellm_config.yaml`  
**Section**: `litellm_settings`  
**Change**: added `modify_params: true`

```yaml
litellm_settings:
  request_timeout: 600
  set_verbose: False
  json_logs: true
  drop_params: true
  modify_params: true        # <-- added: auto-injects dummy tools[] for Bedrock
  vertex_project: os.environ/VERTEX_PROJECT
  ...
```

`modify_params: true` instructs LiteLLM to automatically inject a minimal dummy
`tools` array whenever a Bedrock request arrives with `tool_choice` set (or tool
messages present) but no `tools` definition. This is the resolution recommended
in the LiteLLM error message itself.
