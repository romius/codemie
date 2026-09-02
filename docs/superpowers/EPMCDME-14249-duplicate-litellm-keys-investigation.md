# EPMCDME-14249 — Duplicate LiteLLM Virtual Keys Investigation

**Ticket:** [EPMCDME-14249](https://jiraeu.epam.com/browse/EPMCDME-14249) — Project details page shows incorrect spending for xom-resm  
**Investigated:** 2026-08-18

---

## Summary

The `xom-resm` project displays $0.62 in CodeMie while LiteLLM contains **6 keys with the same alias** (`codemie:project:xom-resm:category:platform`, team `codemie-projects`), carrying significantly more total spend.

CodeMie's spend tracker only reads from **one** stored key token, so the other 5 keys' spending is invisible to the system. The root cause is duplicate key creation, not a spend aggregation bug.

---

## How Keys Are Named

CodeMie creates LiteLLM virtual keys with a deterministic alias per `(project, budget_category)`:

```
codemie:project:{project_name}:category:{budget_category}
```

LiteLLM does **not** enforce alias uniqueness — calling `POST /key/generate` with the same alias always creates a new, separate key.

---

## Why Only $0.62 Is Shown

`_load_project_key_spend_from_litellm` calls `get_key_spending_info(credentials.api_key)` using the single API key token stored in `SettingsService`. Only the most recently created key token is stored. The other 5 keys' accumulated spend is never fetched.

---

## Duplicate Creation Scenarios

### Scenario 1 — Silent delete failure during key recreation (most likely)

**Trigger:** Any `PATCH /v1/admin/project-budgets/{id}` or `POST /v1/admin/project-budgets` where the key sync fallback path is hit.

**Code path:**
```
update_project_budget (adapter)
  → _sync_existing_project_key_alias returns None
      (key exists but has no key_hash/token in /key/list response)
  → _recreate_project_budget_key_alias
      → _delete_project_provider_key_alias   ← exception SWALLOWED
      → service._generate_project_key        ← runs regardless → NEW KEY CREATED
```

**Key code** (`budget_provider_adapter.py:310–317`):
```python
async def _delete_project_provider_key_alias(self, *, service, key_alias):
    try:
        await asyncio.to_thread(service.api_client.post, "/key/delete", data={"key_aliases": [key_alias]})
    except Exception as exc:
        logger.warning(...)   # exception swallowed — execution continues
```

**Steps to reproduce:**
1. Create a project budget — first key is created.
2. Trigger a LiteLLM API blip (network partition, restart, slow response) at the moment `POST /key/delete` executes.
3. Update the project budget (change amount, models, or duration).
4. `_sync_existing_project_key_alias` returns `None` (key has no `key_hash`) → `_recreate` fallback runs.
5. Delete call fails → exception swallowed → `POST /key/generate` runs → **2nd key created**.

Repeating steps 3–5 on each subsequent update produces one additional orphaned key per failure.

---

### Scenario 2 — Multi-replica race condition

**Trigger:** Two API replicas simultaneously process a budget create/update for the same `(project_name, budget_category)`.

**Code path** (`codemie_enterprise/litellm/service.py:809–855`):
```python
def _upsert_project_key(self, *, key_alias, ...):
    existing_key = self._get_project_key_by_alias(key_alias)  # READ
    if existing_key is None:
        self._generate_project_key(...)                        # CREATE — no lock
```

**Steps to reproduce:**
1. Deploy 2+ API replicas.
2. Send two concurrent `POST /v1/admin/project-budgets` or `PATCH /v1/admin/project-budgets/{id}` for the same `(project_name, budget_category)`.
3. Both replicas call `_get_project_key_by_alias` before either key is visible in LiteLLM → both get `None` → both call `POST /key/generate` → **2 keys with identical aliases**.

Softer variant: LiteLLM `/key/list` returns stale data briefly after creation → second replica reads "no key" and generates a duplicate.

---

### Scenario 3 — Partial key creation + client retry

**Trigger:** `POST /v1/admin/project-budgets` or `POST /v1/admin/project-budget-groups` where the network cuts off after LiteLLM creates the key but before CodeMie receives the response.

**Steps to reproduce:**
1. Call `POST /v1/admin/project-budgets` for a project.
2. Network drops after LiteLLM writes the key but before the HTTP response arrives.
3. CodeMie treats the request as failed and retries.
4. `_generate_project_key` is called again → **2nd key created** (LiteLLM has no idempotency on `/key/generate`).
5. On the next budget update, `_sync_existing_project_key_alias` sees the key with potentially missing `key_hash` → returns `None` → `_recreate` runs → delete (may fail silently) + create → **3rd key**.

---

### Scenario 4 — Budget update on a key with missing key_hash

**Trigger:** Any `PATCH /v1/admin/project-budgets/{id}` after a key was created with `key_hash: null` in CodeMie's stored metadata.

**How `key_hash` goes missing:** If `/key/generate` returns successfully but CodeMie's metadata write fails (or the response is malformed), the key exists in LiteLLM but `key_hash` is `null` in CodeMie's `Budget.provider_metadata`.

**Steps to reproduce:**
1. Create a project budget — key is created but `key_hash` is not persisted (DB write failure, response truncation).
2. Update the project budget.
3. `_sync_existing_project_key_alias` finds the key in LiteLLM but `key_hash` is absent → returns `None`.
4. `_recreate_project_budget_key_alias` runs → delete (may fail) + create → **new key**.
5. This repeats on every subsequent budget update until `key_hash` is correctly stored.

---

## Summary Table

| # | Trigger | Precondition | Key change per occurrence |
|---|---|---|---|
| 1 | Budget update + LiteLLM blip | Delete fails silently in `_recreate` | +1 key |
| 2 | Concurrent requests, multi-replica | Two workers see "key absent" simultaneously | +1 key |
| 3 | Budget create + client retry | Partial `/key/generate` + retry | +1 key |
| 4 | Budget update + orphaned key | Key exists but `key_hash` missing | +1 key per update |

**Scenario 1 is the most likely** cause for `xom-resm`. It requires no concurrency — only a transient LiteLLM error during a budget update. With 6 duplicate keys, the budget was likely updated 5 times while LiteLLM was briefly unavailable or slow.

---

## Root Causes in Code

Both root causes are in `codemie_enterprise` (external package):

| Root cause | Location | Detail |
|---|---|---|
| Delete failure is invisible | `codemie_enterprise/litellm/service.py` via `budget_provider_adapter.py:310–317` | `_delete_project_provider_key_alias` swallows all exceptions; create always runs after |
| No alias uniqueness on create | LiteLLM `POST /key/generate` | Always creates a new key regardless of existing aliases |
| No distributed lock on create | `codemie_enterprise/litellm/service.py:809–855` | `_upsert_project_key` is a check-then-act without mutex |

---

## Affected Files

| File | Role |
|---|---|
| `codemie_enterprise/litellm/service.py` | `_generate_project_key`, `_upsert_project_key`, `_get_project_key_by_alias` |
| `src/codemie/enterprise/litellm/budget_provider_adapter.py` | `_recreate_project_budget_key_alias`, `_delete_project_provider_key_alias`, `ensure_project_budget`, `update_project_budget` |
| `src/codemie/service/budget/project_budget_service.py` | `_sync_created_project_budget`, `_sync_updated_project_budget` |
| `src/codemie/rest_api/routers/project_budget_router.py` | API endpoints triggering budget sync |
| `src/codemie/service/spend_tracking/spend_collector_service.py` | Reads spend per stored key token — misses orphaned key spend |

---

## Proposed Fixes

### Fix 1 — Immediate: aggregate spend across all duplicate keys (in-repo)

In `_load_project_key_spend_from_litellm`, query LiteLLM for **all** keys matching the canonical alias and sum their `total_spend`. This unblocks the spend display without touching key creation logic.

### Fix 2 — Short-term: fix silent delete in `_recreate` (codemie_enterprise)

`_delete_project_provider_key_alias` should either:
- Re-raise the exception so `_recreate` aborts instead of creating a new key, or
- Verify the key is gone before calling `_generate_project_key`

### Fix 3 — Short-term: delete ALL keys by alias, not just one (codemie_enterprise)

When `/key/delete` with `key_aliases` is called, verify LiteLLM deletes all matching keys. If LiteLLM only deletes one, loop until `_get_project_key_by_alias` returns `None`.

### Fix 4 — Long-term: distributed lock on key creation (codemie_enterprise)

Add a Redis-backed or DB-backed advisory lock around the `_get_project_key_by_alias` + `_generate_project_key` sequence to prevent the race condition in multi-replica deployments.
