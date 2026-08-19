# Fix: Budget Key Recreation Loses Accumulated Spend

**Ticket:** EPMCDME-14249
**File:** `src/codemie/enterprise/litellm/budget_provider_adapter.py`

---

## Problem

When a project budget is updated, CodeMie calls `_recreate_project_budget_key_alias` which:
1. Deletes the existing LiteLLM virtual key (including its accumulated `spend`)
2. Creates a brand-new key starting at `spend = 0`

All previously accumulated spend is permanently lost. This is the root cause of the xom-resm project showing $0.62 in CodeMie while LiteLLM had ~$1000 in historical usage.

**Trigger path:**
```
update_project_budget()
  → _sync_existing_project_key_alias()   # returns None when key has no token/key_hash
  → _recreate_project_budget_key_alias() # deletes old key, creates new with spend=0
```

The `_sync_existing_project_key_alias` fallback to `None` (line 463–469) happens when the
key exists in LiteLLM but `/key/list` returns no `token`/`key_hash` in its response — an
edge case that triggers destructive recreation instead of an in-place update.

---

## Fix 1 — Carry spend forward in `_recreate_project_budget_key_alias`

**Location:** `budget_provider_adapter.py:347–404`

Before deleting the old key, read its current `spend`. After creating the new key, patch
the new key's `spend` to the captured value via `/key/update`.

```python
async def _recreate_project_budget_key_alias(self, ...) -> BudgetProviderState:
    key_alias = _project_key_alias(project_name, budget_category)

    # Capture spend from the existing key before deletion
    existing_key = await asyncio.to_thread(service._get_project_key_by_alias, key_alias)
    carry_spend: float = existing_key.get("spend", 0.0) if existing_key else 0.0

    await self._delete_project_provider_key_alias(service=service, key_alias=key_alias)
    await self._delete_project_api_key(project_name=project_name, key_alias=key_alias)

    key_state = await asyncio.to_thread(
        service._generate_project_key,
        ...
    )

    # Carry accumulated spend to the new key
    if key_state and carry_spend > 0.0:
        new_token = key_state.get("key_hash") or key_state.get("token")
        if new_token:
            try:
                await asyncio.to_thread(
                    service.api_client.post,
                    "/key/update",
                    data={"key": new_token, "spend": carry_spend},
                )
                key_state["spend"] = carry_spend
                logger.info(
                    f"budget_event=provider_project_key_spend_carried_forward "
                    f"project_name={project_name!r} carry_spend={carry_spend}"
                )
            except Exception as exc:
                logger.warning(
                    f"budget_event=provider_project_key_spend_carry_failed "
                    f"project_name={project_name!r} carry_spend={carry_spend} error={exc}"
                )
    ...
```

---

## Fix 2 — Avoid unnecessary recreation when key hash is missing

**Location:** `budget_provider_adapter.py:463–469`

When a key exists by alias but has no `token`/`key_hash` in the API response, the current
code returns `None`, forcing destructive recreation. Instead, try to re-fetch the key once
before giving up — LiteLLM sometimes returns incomplete data on the first `/key/list` call.

```python
else:
    # Key found but no hash — re-fetch once before falling back to recreate
    refreshed = await asyncio.to_thread(service._get_project_key_by_alias, key_alias)
    if refreshed and (refreshed.get("key_hash") or refreshed.get("token")):
        key_hash = refreshed.get("key_hash") or refreshed.get("token")
        refreshed = {**refreshed, "key_hash": key_hash}
        key_state = await asyncio.to_thread(
            service._update_project_key,
            existing_key=refreshed,
            key_alias=key_alias,
            ...
        )
    else:
        logger.debug("...reason=missing_key_hash_after_retry")
        return None  # caller falls back to _recreate (now with spend carry-forward)
```

---

## Fix 3 — Log deletion failures clearly

**Location:** `budget_provider_adapter.py:310–317`

`_delete_project_provider_key_alias` already catches exceptions — keep that, but also log
`carry_spend` when deletion fails so ops can spot silently orphaned keys.

No code change needed here; the carry-forward in Fix 1 is safe even if deletion fails
(LiteLLM deduplicates keys by alias on the next `/key/list` call).

---

## What this does NOT fix

- Historical spend already lost on prior recreations (xom-resm's ~$1000). That must be
  recovered manually from `LiteLLM_DeletedVerificationToken` by summing `spend` on deleted
  rows and patching the current active key via `/key/update`.
- The `get_latest_key_spending_for_project` `.limit(1)` bug — separate ticket.

---

## Tests to add

| Scenario | Expected |
|---|---|
| Budget update on key with `spend = 42.5` | New key starts with `spend = 42.5` |
| Budget update when `/key/list` returns key with no token | Re-fetch attempted; if still missing, recreate with carry-forward |
| `/key/update` for carry-forward fails | Warning logged, new key starts at 0 (graceful degradation) |
| Budget create (no existing key) | New key starts at 0 (no carry needed) |
