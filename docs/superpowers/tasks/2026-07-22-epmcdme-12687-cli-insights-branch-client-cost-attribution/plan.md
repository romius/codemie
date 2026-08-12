# EPMCDME-12687: CLI Insights Repository/Branch/Client Cost Attribution — Implementation Plan

**Goal:** Make the CLI Insights Repositories table show one row per `(repository, branch,
client)` combination instead of one row per repository, without losing cost that belongs to
sessions whose events are inconsistently tagged across metric types or whose repository
could not be resolved at all.

**Architecture:** Elasticsearch aggregation nested one level deeper (`repositories → branches
→ clients`, each with its own `usage`/`sessions`/`proxy` sub-aggs). Application-layer merge in
`classification_engine.py` collapses ES buckets that represent the same logical session but
differ only in which metric type populated `branch`/`client`, keyed by normalized
`(repository, branch, client)`.

**Tech Stack:** Python 3.12, FastAPI, Elasticsearch DSL (raw dict aggregations), pytest.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/codemie/service/analytics/handlers/cli/constants.py` | Modify | Add `CODEMIE_CLIENT_KEYWORD_FIELD`, `COWORK_VALUE` |
| `src/codemie/service/analytics/handlers/cli/classification_engine.py` | Modify | `(repository, branch, client)` grouping, normalization, merge, Cowork fallback |
| `src/codemie/service/analytics/handlers/cli/insights_handler.py` | Modify | Nested ES aggregation; user-id resolution fallback |
| `src/codemie/service/analytics/handlers/cli/handler.py` | Modify | Skip LLM-proxy-only noise rows |
| `src/codemie/service/monitoring/llm_proxy_monitoring_service.py` | Modify | Emit `codemie_client` attribute |

---

## Task 1: Group repository rows by `(repository, branch, client)`

**Test-first: no.** No existing unit test file covers
`_build_cli_repository_classifications`; verified via a direct interactive call (see Step 4)
against synthetic ES-shaped bucket dicts, matching how this module is exercised elsewhere in
the codebase (no `tests/` coverage for this handler family was found).

**Files:**
- Modify: `src/codemie/service/analytics/handlers/cli/constants.py`
- Modify: `src/codemie/service/analytics/handlers/cli/classification_engine.py`

---

- [x] **Step 1: Add `CODEMIE_CLIENT_KEYWORD_FIELD` constant**

```python
CODEMIE_CLIENT_KEYWORD_FIELD = "attributes.codemie_client.keyword"
```

- [x] **Step 2: Add `_normalize_branch` / `_normalize_client` and rewrite the grouping loop**

```python
_CLI_CLIENT_KEYS = frozenset({"CLI", "codemie-daemon", "codemie-claude", "codemie-claude-acp", "codemie-code"})

@staticmethod
def _normalize_client(raw: str) -> str:
    return "CLI" if raw in CLIClassificationEngine._CLI_CLIENT_KEYS else raw

@staticmethod
def _normalize_branch(raw: str) -> str:
    return "" if raw == "HEAD" else raw
```

Rewrite `_build_cli_repository_classifications` to iterate `repo_bucket.branches.buckets ->
branch_bucket.clients.buckets`, key a `merged: dict[tuple[str, str, str], dict]` by
`(repository, branch, client)` (normalized), and sum `sessions`/`cost`/`net_lines` into the
existing entry when the key repeats.

- [x] **Step 3: Fix the empty-repository drop (found during review)**

Original: `if not repository: continue` (dropped the bucket, and its cost, entirely).

```python
repository = str(repo_bucket.get("key", "")).strip() or COWORK_VALUE
```

Add `COWORK_VALUE = "Cowork"` to `constants.py` next to the pre-existing `N_A_VALUE`.

- [x] **Step 4: Verify with a synthetic bucket**

```bash
poetry run python -c "
from src.codemie.service.analytics.handlers.cli.classification_engine import CLIClassificationEngine
buckets = [{'key': '', 'branches': {'buckets': [{'key': '', 'clients': {'buckets': [
    {'key': 'CLI', 'usage': {'projects': {'buckets': []}, 'lines_added': {'value': 0}, 'lines_removed': {'value': 0}},
     'proxy': {'total_cost': {'value': 5.0}}, 'sessions': {'count': {'value': 2}}}
]}}]}}]
print(CLIClassificationEngine._build_cli_repository_classifications(buckets))
"
```

Expected: `[{'repository': 'Cowork', 'branch': '', 'client': 'CLI', 'sessions': 2, 'cost': 5.0, ...}]`
— row present (not dropped).

- [x] **Step 5: Lint**

```bash
poetry run ruff check src/codemie/service/analytics/handlers/cli/classification_engine.py src/codemie/service/analytics/handlers/cli/constants.py
```

---

## Task 2: Nest the ES aggregation by branch and client

**Test-first: no.** Aggregation-shape change verified against the real preview Elasticsearch
cluster (via `kubectl port-forward` to `preview-elastic`), not a mocked unit test — no
existing ES-query unit harness for this handler.

**Files:**
- Modify: `src/codemie/service/analytics/handlers/cli/insights_handler.py`

---

- [x] **Step 1: Restructure `repositories_data` in `_build_cli_insights_user_detail_aggregation`**

Replace the flat `repositories` terms agg with `repositories_data.filter(metric_name in
[cli_tool_usage_total, codemie_litellm_proxy_usage]).repositories.branches (missing:
"").clients (missing: "CLI")`, each client bucket carrying its own `usage`/`sessions`/`proxy`
sub-aggs (see spec.md for the full tree).

- [x] **Step 2: Update the two read sites**

`aggs.get("repositories", {})...` → `aggs.get("repositories_data", {}).get("repositories",
{})...` in both `get_cli_insights_project_classification` (repository name list) and
`get_cli_insights_user_detail` (feeds `_build_cli_repository_classifications`).

- [x] **Step 3: Add `branch`/`client` to the response row shape**

In the table-formatting step: add `"branch": row.get("branch", "")` and `"client":
row.get("client", "")` to each row dict; add matching `branch`/`client` column definitions.

- [x] **Step 4: Add the `UserIdentityResolver` id-based fallback**

In `_match_user_id_by_email_fallback`, after the existing email match fails, add:

```python
resolved_id = await UserIdentityResolver.resolve(entity_name, target="id")
if resolved_id and resolved_id != entity_name:
    normalized_resolved_id = resolved_id.strip().lower()
    for b in buckets:
        if normalized_resolved_id == str(b["key"]).strip().lower():
            return str(b["key"])
```

- [x] **Step 5: Verify against real data**

```bash
KUBECONFIG=~/Downloads/kubeconfig kubectl port-forward -n preview-elastic svc/elasticsearch-master 9200:9200
curl -sk -u codemie-backend:$ELASTIC_PASSWORD https://localhost:9200/codemie_metrics_logs/_search -d '...'
```

Confirmed a real user's Repositories table now returns per-`(repository, branch, client)`
rows matching the manual ES query.

---

## Task 3: Filter LLM-proxy-only noise from the CLI summary

**Test-first: no.**

**Files:**
- Modify: `src/codemie/service/analytics/handlers/cli/handler.py`

---

- [x] **Step 1: Skip zero-doc-count session rows**

```python
session_doc_count = int(session_data.get("doc_count", 0))
if session_doc_count == 0:
    continue
```

Placed immediately after `session_data = user_bucket.get("session_data", {})`, before any
field extraction that assumes real activity.

---

## Task 4: Propagate `codemie_client` on every LLM-proxy metric

**Test-first: no.**

**Files:**
- Modify: `src/codemie/service/monitoring/llm_proxy_monitoring_service.py`

---

- [x] **Step 1: Add the attribute**

```python
MetricsAttributes.CODEMIE_CLIENT: request_info.get(CLIENT_TYPE, ""),
```

Added to the attributes dict built for every LLM-proxy request metric, so
`CODEMIE_CLIENT_KEYWORD_FIELD` (Task 1/2) is actually populated for metrics emitted going
forward.
