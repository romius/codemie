# Spec — EPMCDME-12687: CLI Insights Repository/Branch/Client Cost Attribution

## Problem

`EPMCDME-12687` is the umbrella ticket for improving Claude Desktop data retrieval and
display across Analytics and Chats. This repo (`codemie`, backend) carries the sub-task:

- Analytics CLI Insights: Total cost is displayed incorrectly for Claude Desktop and does
  not match the Repositories table.

Root cause: `cli-insights-user-repositories` aggregated all branches and clients (CLI,
Claude Desktop) per repository into a single row. This hid *which* branch/client combination
contributed which cost, and — because Elasticsearch stores different event types for the
same session with inconsistent branch/client tagging — produced duplicate or missing rows
depending on how those event types happened to bucket.

---

## Change 1 — `classification_engine.py`: group by `(repository, branch, client)`

`_build_cli_repository_classifications` (`classification_engine.py`) now builds one row per
`(repository, branch, client)` combination instead of one row per `repository`.

### Why merging is needed, not just grouping

A single CLI/Desktop session emits **multiple ES documents of different metric types**, and
they don't all carry `branch`/`client` consistently:

- `cli_tool_usage_total` documents carry a real `branch` value when available.
- `codemie_litellm_proxy_usage` (proxy cost) documents frequently have **no** `branch`/
  `client` attribute at all (empty string).

Without merging, the same logical session would split into two separate table rows — one
with a real branch and zero cost (from the tool-usage doc), one with no branch and the real
cost (from the proxy doc) — because they'd bucket under different `(repository, branch,
client)` keys. `_normalize_branch()` and `_normalize_client()` collapse the noisy dimension
(`"HEAD"` → `""` for branch merge purposes only; `codemie-daemon`/`codemie-claude`/
`codemie-claude-acp`/`codemie-code` → canonical `"CLI"` for client) so that both documents
land in the same merge bucket, and their `sessions`/`cost`/`net_lines` are summed into one
row instead of split across two.

### Branch display semantics (clarified during review — not a bug)

The **displayed** `branch` value is the raw (non-normalized) value from whichever bucket
had one, not the normalized merge key. This is intentional, confirmed against real usage
patterns:

| Scenario | Raw `branch` | Displayed |
|---|---|---|
| CLI run from a global/home terminal (no git remote, git itself reports detached `HEAD`) | `"HEAD"` | `HEAD` |
| CLI run from a folder with no git branch info, or Claude Desktop Cowork session with no git context | `""` (absent) | `-` |
| Claude Desktop Cowork tab, folder opened, but not the Code tab | `""` (Desktop never writes a branch for this session type) | `-` |
| Claude Desktop Code tab | real branch name | branch name |

`"HEAD"` and `""` are two genuinely different raw values coming from different real
scenarios (confirmed against live repository-breakdown data: `HEAD` appears only paired with
CLI-family clients; `-` appears with both CLI and Claude Desktop rows where no branch
attribute was ever written). The merge key normalizes `"HEAD"` to `""` *only* so that
same-session documents with inconsistent tagging land in one row (see above) — it does not
mean `"HEAD"` should always display as `-`.

### Fix — empty-repository rows were silently dropped, losing real cost

Found during review: `if not repository: continue` unconditionally skipped any bucket whose
`repository` value was empty, **discarding its cost from this table** while that same cost
was still included in the flat, ungrouped top-level `total_cost` sum (`proxy_usage.total_cost`
in `_extract_cli_insights_user_detail_metrics`, which has no repository filter at all). This
is a second, independent source of the "Total cost doesn't match Repositories table" symptom,
on top of the row-splitting problem the `(repository, branch, client)` grouping already fixes.

Verified against real Elasticsearch data (`codemie_litellm_proxy_usage`, via the preview
cluster): **9,389 of 711,917** proxy-cost documents (~1.3%) have `attributes.repository = ""`,
totaling **$321.58**. Breakdown:

- By month: **90% of the dollar amount ($288.34) is from March 2026** — before repository
  attribution existed at all in the current form. Monthly cost has been small and declining
  since (`$8.57` Apr, `$13.69` May, `$9.79` Jun, `$1.19` Jul-to-date) — not an actively
  growing problem, but real historical and ongoing leakage.
- By `client_type`: **90% of the dollar amount ($288.13) is `codemie-claude`** (Claude Code
  CLI plugin, not Desktop). `claude-desktop` accounts for only $14.59 (~4.5%). The remainder
  is spread across `codemie-claude-vscode` ($12.58), `unknown` ($2.51), `codemie-cli`
  ($1.66), `codemie-daemon` ($1.42), `test-harness` ($0.35, automated QA traffic — `cli_request:
  false`, `user_agent: python-requests`), and `chrome-extension` ($0.33).

**Fix:** `repository = str(repo_bucket.get("key", "")).strip() or COWORK_VALUE` — fall back
to the `"Cowork"` label (already the established convention for "no resolvable project
context", used client-side by `codemie-code`'s `extractRepository()`) instead of dropping the
bucket. New constant `COWORK_VALUE = "Cowork"` added to `constants.py`, alongside the
pre-existing `N_A_VALUE = "N/A"` (unrelated, unchanged, confirmed pre-existing since
`EPMCDME-11635`).

---

## Change 2 — `insights_handler.py`: nested ES aggregation

`_build_cli_insights_user_detail_aggregation`'s `repositories_data` aggregation restructured
from a flat `repositories` terms agg to a nested `repositories → branches → clients` terms
agg, each level with its own `usage`/`sessions`/`proxy` sub-aggs:

```
repositories_data (filter: metric_name in [cli_tool_usage_total, codemie_litellm_proxy_usage])
  └─ repositories (terms: repository.keyword, size 1000)
       └─ branches (terms: branch.keyword, size 50, missing: "")
            └─ clients (terms: codemie_client.keyword, size 10, missing: "CLI")
                 ├─ usage (filter: cli_tool_usage_total)   → lines_added, lines_removed, projects
                 ├─ sessions (filter: cli_tool_usage_total) → session_id cardinality
                 └─ proxy (filter: codemie_litellm_usage_total) → total_cost
```

New field `CODEMIE_CLIENT_KEYWORD_FIELD = "attributes.codemie_client.keyword"` added to
`constants.py` to support the new client dimension.

`get_cli_insights_user_repositories` response rows now include `branch` and `client` as
top-level scalar fields (previously: `branches: list[str]`, aggregated per repository).

Also added: a further identity-resolution fallback in `_match_user_id_by_email_fallback` —
after the existing email-based match fails, try `UserIdentityResolver.resolve(entity_name,
target="id")` and match directly against the ES bucket key. Reviewed for safety: uses the
existing `UserIdentityResolver` (parameterized ORM lookup via `user_repository`), no new
data-access path.

---

## Change 3 — `handler.py`: filter LLM-proxy-only noise from CLI summary

`CLIHandler`'s user-summary loop now skips rows where `session_data.doc_count == 0` — rows
backed only by LLM-proxy metrics (e.g. Claude Desktop ping requests) with no corresponding
`cli_tool_usage_total` document. Prevents these from appearing as spurious zero-activity CLI
users.

## Change 4 — `llm_proxy_monitoring_service.py`: propagate client type

One line: `MetricsAttributes.CODEMIE_CLIENT: request_info.get(CLIENT_TYPE, "")` added to the
metric attributes emitted for every LLM-proxy request, so the client dimension used by
Change 1/2 is actually populated going forward.

---

## Known Gaps

- Historical proxy-cost documents with `codemie_client` entirely absent (pre-dating this
  ticket's `CODEMIE_CLIENT_KEYWORD_FIELD` write path) will show as `client: ""` /
  `-` in the UI once the `Cowork` fallback repository fix ships — this is expected and
  correct given the data, not a new defect.

---

## Files Changed

| File | Change |
|---|---|
| `src/codemie/service/analytics/handlers/cli/classification_engine.py` | Group repository rows by `(repository, branch, client)`; add `_normalize_branch`/`_normalize_client`; fix empty-repository rows being dropped (now fall back to `Cowork`) |
| `src/codemie/service/analytics/handlers/cli/constants.py` | Add `CODEMIE_CLIENT_KEYWORD_FIELD`, `COWORK_VALUE` |
| `src/codemie/service/analytics/handlers/cli/insights_handler.py` | Restructure `repositories_data` ES aggregation to nest branch/client; add `UserIdentityResolver` id-based fallback match |
| `src/codemie/service/analytics/handlers/cli/handler.py` | Skip CLI summary rows with zero `cli_tool_usage_total` doc count (LLM-proxy-only noise) |
| `src/codemie/service/monitoring/llm_proxy_monitoring_service.py` | Emit `codemie_client` attribute on every LLM-proxy metric |

---

## Acceptance Criteria

- `cli-insights-user-repositories` returns one row per `(repository, branch, client)`
  combination actually observed for the user/period, with `branch` and `client` as scalar
  fields.
- A session whose documents are inconsistently tagged (some with a real branch, some blank)
  still produces exactly one row for that repository/branch/client, not two.
- No repository row's cost is silently excluded from the table — rows with no resolvable
  repository fall back to `Cowork` rather than being dropped.
- The Repositories table total cost, summed across all its rows, matches the flat top-level
  Total cost figure for the same user/time-period.
- CLI-summary rows backed only by LLM-proxy noise (no real tool-usage activity) do not appear
  as spurious users/sessions.
