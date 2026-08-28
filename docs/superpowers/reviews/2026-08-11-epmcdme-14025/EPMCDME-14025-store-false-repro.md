# EPMCDME-14025: Codex CLI Fails with `store=false` — Findings & Reproduction

## Problem Summary

Codex CLI multi-turn sessions fail with a `400 invalid_request_error` from Azure OpenAI:

```
Item with id 'rs_<hash>' not found.
Items are not persisted when `store` is set to false.
Try again with `store` set to true, or remove this item from your input.
```

**Root cause:** LiteLLM is configured with `store: false` in `litellm_params` for Responses API models. On the first turn, the model returns reasoning items with `rs_*` IDs and `encrypted_content`. On the second turn, the Codex CLI includes these items in the request. LiteLLM adds `store: false` again — Azure rejects because the items were never persisted.

---

## Technical Background

### OpenAI Responses API and Multi-Turn Sessions

The Responses API (`POST /v1/responses`) is a stateful API used by the Codex CLI. Unlike Chat Completions, it returns typed output items including **reasoning items** with:
- `id`: a server-assigned `rs_*` identifier
- `encrypted_content`: opaque encrypted reasoning tokens for cross-turn continuity

On subsequent turns, the Codex CLI includes these items in the `input` array so the model can continue its reasoning chain.

### `store: false` Semantics

When `store: false` is sent in a Responses API request, Azure does **not** persist the response or its output items. Any subsequent request that references those items (by `rs_*` ID or via `encrypted_content`) fails with 400.

### How LiteLLM Injects `store: false`

LiteLLM forwards `litellm_params` fields to the upstream request body. If a model entry has `litellm_params.store: false`, every request routed through that model — including second and later turns — gets `store: false` appended. This makes reasoning item references from the previous turn permanently unresolvable.

---

## Reproduction Environment

### Components

| Component | Role |
|---|---|
| `codemie-codex --provider litellm` | Codex CLI sending Responses API requests |
| LiteLLM proxy (`localhost:4000`) | Adds `store: false` to outgoing requests |
| Mock Azure Responses API (`localhost:8765`) | Simulates Azure's `store=false` rejection |

### Mock Server

File: `scratchpad/mock_debug.py` (session scratchpad — copy to persist).

Key behaviors:
- **Turn 1:** Returns a successful response with a `type=reasoning` item containing a real `rs_*` id and fake `encrypted_content`.
- **Turn 2+:** If the input contains a reasoning item with `encrypted_content` (or a known `rs_*` id), returns `400 invalid_request_error` matching the exact Azure error.
- Response does **not** include `store: false` — matching real Azure behavior where the response doesn't echo the request's store flag.

### LiteLLM Model Entry

The model was registered via the LiteLLM admin API with:

```json
{
  "model_name": "gpt-5.6-terra-2026-07-09",
  "litellm_params": {
    "model": "azure/gpt-5-mini",
    "api_base": "http://host.docker.internal:8765",
    "api_key": "mock-key",
    "store": false
  },
  "model_info": { "id": "gpt-5-6-terra-mock" }
}
```

Virtual key scoped to this model: `sk-1234`

---

## Reproduction Steps

### Prerequisites

1. Docker Desktop running with the `codemie-dev` stack up:
   ```bash
   cd /Users/sergeynikitin/projects/codemie-dev
   docker compose up -d
   ```
2. Verify LiteLLM is healthy:
   ```bash
   curl http://localhost:4000/health
   # Expected: 401 (auth required) — means LiteLLM is up
   ```
3. Verify the mock model entry exists:
   ```bash
   curl -s http://localhost:4000/model/info \
     -H "Authorization: Bearer sk-1234" | \
     python3 -c "import sys,json; d=json.load(sys.stdin); [print(m['model_name']) for m in d.get('data',[])]"
   # Expected: gpt-5.6-terra-2026-07-09
   ```

### Start the Mock Server

```bash
python3 /path/to/mock_debug.py &
```

Confirm it's listening:
```bash
curl http://localhost:8765/
# Expected: {"status": "ok"}
```

### Trigger the Bug

**Option A — via `curl` (fastest):**

```bash
# Turn 1: get a response with an rs_* reasoning item
RESP1=$(curl -s --max-time 15 -X POST http://localhost:4000/responses \
  -H "Authorization: Bearer sk-1234" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-5.6-terra-2026-07-09","input":[{"role":"user","content":"hello"}],"stream":true}')

RS_ID=$(echo "$RESP1" | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if line.startswith('data:') and '[DONE]' not in line:
        try:
            d = json.loads(line[5:].strip())
            if d.get('type') == 'response.output_item.added':
                item = d.get('item', {})
                if item.get('id','').startswith('rs_'): print(item['id'])
        except: pass
")
echo "rs_id: $RS_ID"

# Turn 2: reference the reasoning item — triggers the 400
curl -s --max-time 15 -X POST http://localhost:4000/responses \
  -H "Authorization: Bearer sk-1234" \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"gpt-5.6-terra-2026-07-09\",\"input\":[{\"id\":\"${RS_ID}\",\"type\":\"reasoning\",\"encrypted_content\":\"ZmFrZQ==\"},{\"role\":\"user\",\"content\":\"follow up\"}],\"stream\":true}"
```

**Option B — via codemie-codex CLI:**

```bash
codemie-codex --provider litellm \
  --base-url http://localhost:4000 \
  --api-key sk-h1234 \
  -m gpt-5.6-terra-2026-07-09
```

Send a first message (succeeds), then a second message. The second fails with:

```
{"error":{"message":"litellm.BadRequestError: AzureException BadRequestError -
{\"error\": {\"message\": \"Item with id 'rs_...' not found.
Items are not persisted when `store` is set to false.
Try again with `store` set to true, or remove this item from your input.\",
\"type\": \"invalid_request_error\", \"param\": \"input\", \"code\": null}}.
Received Model Group=gpt-5.6-terra-2026-07-09\nAvailable Model Group Fallbacks=None",
"type":null,"param":null,"code":"400"}}
```

---

## Key Findings

### What the Codex CLI sends on turn 2

The Codex CLI includes the previous turn's reasoning item in the `input` array:

```json
{
  "type": "reasoning",
  "id": "",
  "encrypted_content": "<base64 opaque token>"
}
```

The `id` field is stripped by the Codex CLI's internal logic (it tracks items by `encrypted_content`, not `rs_*` id). However, Azure still rejects the request because the `encrypted_content` references server-side state that was never stored (`store: false`).

### Why `store: false` in `litellm_params` breaks multi-turn

LiteLLM injects `store: false` into **every** request for that model — including turn 2+ which reference reasoning items from a turn-1 response that was never stored. Azure has no record of those items and returns 400.

### Why production sessions succeeded

The production `gpt-5.6-terra-2026-07-09` entry in the production LiteLLM instance does **not** have `store: false` in its `litellm_params`. The bug is triggered only in deployments where this flag is set.

### DIAL proxy is not compatible

The DIAL proxy (`ai-proxy.lab.epam.com`) returns `404` for `/openai/responses` — it only supports Chat Completions. Any model entry using DIAL as the backend cannot reproduce this bug.

---

## Fix (implemented)

`async_pre_call_deployment_hook` in `BedrockCostModelFixLogger` strips reasoning items with
`encrypted_content` from the `input` array whenever `store=False` is present in the merged
request kwargs. The hook fires after deployment selection and the `{**litellm_params, **kwargs}`
merge in the router, so `store=False` set via a model entry's `litellm_params` is visible.

Branch: `EPMCDME-14025_strip-reasoning-items-store-false`
Commit: `cc7c89745`

---

## Deferred: id-only reasoning items (CR-003)

The current filter strips items where **both** `type == "reasoning"` and `encrypted_content` is
truthy. Items of the form `{"type": "reasoning", "id": "rs_xxx"}` with no `encrypted_content`
pass through.

**Why this is deferred:** The Codex CLI always strips the `id` field before sending, so every
reasoning item it produces has `encrypted_content` and no `id`. The filter covers 100% of the
Codex CLI's actual payload. Whether Azure rejects id-only reasoning items when `store=False` is
unverified — it may accept them since there is nothing to look up server-side.

**Follow-up if needed:** If another client surfaces a 400 from an id-only reasoning item, broaden
the filter to strip all `type=reasoning` items regardless of `encrypted_content`:

```python
isinstance(item, dict) and item.get("type") == "reasoning"
```

Verify against Azure first, then update `test_preserves_reasoning_items_without_encrypted_content`
with a comment citing the observed Azure behavior.
