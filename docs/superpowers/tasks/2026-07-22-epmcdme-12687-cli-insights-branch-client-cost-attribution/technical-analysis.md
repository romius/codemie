# Technical Research

**Task**: EPMCDME-12687 — CLI Insights repository/branch/client cost attribution
**Generated**: 2026-07-22
**Research path**: filesystem (this repo) + live Elasticsearch query (preview cluster, via
`kubectl port-forward`) + cross-repo context (`codemie-code`, `codemie-ui`)

---

## 1. Original Context

`EPMCDME-12687` spans three repos. This repo (`codemie`, backend) owns the analytics query
layer: it decides how raw Elasticsearch metric documents are grouped into the rows the
frontend renders in the CLI Insights Repositories table, and computes the flat "Total cost"
figure shown at the top of the same page. The two numbers are expected to match for the same
user/time-period; the reported bug (task 3 of the umbrella ticket) is that they don't.

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/service/analytics/handlers/cli/classification_engine.py` —
  `_build_cli_repository_classifications` is the sole place that turns nested ES aggregation
  buckets into the rows behind `cli-insights-user-repositories`. Before this change it grouped
  by `repository` only, aggregating an array of `branches` per row with no client dimension
  and no per-branch cost breakdown.
- `src/codemie/service/analytics/handlers/cli/insights_handler.py` —
  `_build_cli_insights_user_detail_aggregation` builds the whole ES query for the CLI Insights
  user-detail endpoint (`get_cli_insights_user_detail`), including the `repositories_data`
  sub-tree consumed by `classification_engine.py`, and `_extract_cli_insights_user_detail_metrics`
  computes the flat top-level `total_cost` from a **separate**, unfiltered-by-repository
  `proxy_usage` aggregation.
- `src/codemie/service/analytics/handlers/cli/handler.py` — `CLIHandler`, a sibling handler
  class serving the plain `Analytics → CLI` page (distinct from `CLIInsightsHandler`'s "CLI
  Insights" sub-tab). Shares no code with `insights_handler.py` beyond the common
  `base_handler.py`.
- `src/codemie/service/analytics/handlers/user_identity_resolver.py` — `UserIdentityResolver`,
  a pre-existing centralized identity-resolution utility (suffix-stripping, UUID/email/name
  bulk lookup via `user_repository.afind_users_by_identifiers`, a parameterized ORM query).
  Already used by `_match_user_id_by_email_fallback`; this task adds one more fallback call to
  it, no new data-access surface.

### Architecture and Layers Affected

- **Elasticsearch aggregation layer** (`insights_handler.py`): nested the `repositories_data`
  sub-aggregation one level deeper to carry branch and client as ES-level bucket dimensions
  instead of a post-hoc terms list.
- **Application-layer merge** (`classification_engine.py`): a **normalization + merge** step
  is necessary in addition to the ES-level nesting, because a single logical session emits
  multiple ES documents of different `metric_name` values that don't consistently carry
  `branch`/`client`:
  - `cli_tool_usage_total` — carries `branch` (from the CLI's local git detection) when
    available.
  - `codemie_litellm_proxy_usage` — the LLM cost event — frequently carries no `branch`/
    `codemie_client` attribute (empty), independent of whether the CLI/Desktop client that
    generated the LLM call *did* have branch info; verified directly on the preview cluster
    (see below).

  Without merging, the same session would appear as two rows: a real-branch, zero-cost row
  from the tool-usage doc, and a blank-branch, real-cost row from the proxy doc. This is a
  second, distinct cause of "the numbers don't add up" beyond the cost-attribution gap
  described next — not just a display nicety.

### Root-Cause Verification Against Live Data (preview cluster)

Connected via `KUBECONFIG=~/Downloads/kubeconfig kubectl port-forward -n preview-elastic
svc/elasticsearch-master 9200:9200`, queried `codemie_metrics_logs` directly
(`metric_name.keyword: codemie_litellm_proxy_usage`):

- **711,917** total proxy-cost documents.
- **9,389** (~1.3%) have `attributes.repository.keyword = ""` (an explicit empty string —
  Elasticsearch keyword fields index empty string as a real term, so this is *not* caught by
  a `missing` aggregation clause; it requires an explicit falsy-string check in application
  code).
- These 9,389 documents total **$321.58** — money that was previously silently excluded from
  every Repositories table row (`if not repository: continue` in the pre-fix
  `_build_cli_repository_classifications`) while still counted in the flat top-level
  `total_cost` (`proxy_usage.total_cost`, unfiltered by repository). This is the concrete,
  measured root cause of the reported cost mismatch, independent of and in addition to the
  row-splitting problem the `(repository, branch, client)` grouping fixes.
- Time distribution (`date_histogram` by month): **90% of the dollar amount ($288.34 of
  $321.58) is from March 2026**, predating repository attribution in its current form.
  Subsequent months are small and declining (`$8.57` Apr → `$13.69` May → `$9.79` Jun → `$1.19`
  Jul-to-date) — a real but shrinking, mostly-historical leak, not an actively growing one.
- Client-type distribution (`attributes.client_type.keyword`, a **separate, pre-existing**
  attribute from the new `codemie_client` this task adds — see below): **90% of the dollar
  amount ($288.13) is `codemie-claude`** (the Claude Code CLI plugin, *not* Claude Desktop).
  `claude-desktop` itself accounts for only **$14.59 (~4.5%)**. Remainder: `codemie-claude-
  vscode` ($12.58), `unknown` ($2.51), `codemie-cli` ($1.66), `codemie-daemon` ($1.42),
  `test-harness` ($0.35 — confirmed via sample docs as automated QA traffic:
  `cli_request: false`, `user_agent: python-requests/2.32.5`, `user_name: test_user`), and
  `chrome-extension` ($0.33). **Conclusion: this specific cost-leak is not primarily a Claude
  Desktop problem**, despite the umbrella ticket's Desktop framing — it affects several client
  types roughly in proportion to their overall usage volume.

### Branch Display Semantics — Clarified During Review (not a bug)

Initial review flagged that the merged row's displayed `branch` field is the raw
(un-normalized) value rather than the normalized merge key — e.g. a bucket with raw
`branch="HEAD"` displays `HEAD`, not `-`, even though `_normalize_branch("HEAD") == ""`. This
was raised as a possible bug (docstring says "Treat 'HEAD' as empty") and traced against real
Repositories-table data provided by the ticket owner:

| Row (from live data) | Branch shown | Client |
|---|---|---|
| `Users/mykola_nehrych` | `HEAD` | CLI |
| `codemie-ai/codemie-code` | `HEAD` | CLI |
| `epm-cdme/codemie-ui` | `-` | CLI |
| `Cowork` | `-` | Claude Desktop |
| `epm-cdme/codemie-ui` | `-` | Claude Desktop |

Confirmed with the ticket owner: this is **intentional**, not a bug. Expected behavior per
CLI/Desktop UX:

- CLI run from a global/home terminal, no git remote → `branch: HEAD` (git itself reports
  detached `HEAD` for this case; this is real, correct information).
- CLI run from a folder with no branch info → `-`.
- CLI run from a folder with a branch but no git remote vs. with a remote → affects the
  **repository** column (local path vs. remote), not branch — out of scope for this
  investigation.
- Claude Desktop Cowork tab (no folder, or folder-but-not-Code-tab) → `-` always; Desktop only
  writes a branch when the **Code tab** specifically is open.

The `_normalize_branch("HEAD" → "")` step exists purely to make same-session document merging
work (§ above) — it deliberately does **not** propagate to the displayed value, and that's
correct. No code change made for this item.

### Patterns and Conventions

- `"Cowork"` as a fallback label for "no resolvable project/repository context" is an
  established convention **on the client side** (`codemie-code`'s
  `src/utils/paths.ts::extractRepository()`), confirmed via `grep -rin "cowork"` returning zero
  hits anywhere in this (`codemie`) backend repo prior to this task. This task introduces the
  first backend-side use of that label (`COWORK_VALUE` in `constants.py`), applied when the ES
  bucket key itself is empty — a distinct, lower-level case than the client-side Desktop
  attribution logic, but the same semantic ("no known place to attribute this activity to").
- `N_A_VALUE = "N/A"` (pre-existing since `EPMCDME-11635`, confirmed via `git log -S`) is a
  sibling "special value" constant in the same file, used elsewhere for a different kind of
  unknown (not repository-shaped). Not reused here — `Cowork` was chosen deliberately over
  `N/A` for the repository fallback, matching what an end user seeing this row would recognize
  from the rest of the CLI Insights UI.
- ES terms aggregations distinguish **missing field** (no value indexed) from **empty-string
  value** (`""`, a real indexed term for keyword fields). The pre-existing `branches`/`clients`
  sub-aggs in this task's new tree use `missing: ""` / `missing: "CLI"` to backfill true
  missing-field cases; the `repositories` terms agg has no such backfill (documents whose
  repository is truly absent, as opposed to empty-string, are dropped by ES *before* reaching
  application code, invisibly). The application-layer `or COWORK_VALUE` fallback added by this
  task only catches the empty-string case, which is what the live-data investigation confirmed
  is the actual failure mode (9,389 docs all had `repository: ""`, not a missing field) — worth
  re-checking if a future investigation finds cost still missing after this fix ships.

---

## 3. Documentation Findings

No `.ai-run/guides/` entry documents the CLI Insights aggregation shape or the
branch/client merge semantics; this file is the first record of both the multi-metric-type
merge rationale and the `Cowork`-fallback convention on the backend side.

### Cross-Repo Findings

- `codemie-code`, `docs/specs/claude-desktop-project-attribution/spec.md` (now relocated to
  the ticket owner's personal notes) — documents the CLI/proxy-side repository resolution
  (`X-CodeMie-Repository` header injection, session-file scan, process-CWD lookup). Does not
  cover backend-side aggregation or the empty-repository cost-leak found in this pass — that
  gap exists independently of whether the proxy successfully resolves a repository, since it
  affects documents where the header was apparently never set at all (predominantly historical,
  pre-dating the current attribution logic).
- `codemie-ui`, `src/pages/analytics/components/cliInsights/helpers.tsx`
  (`CLIENT_CONFIG`) — the frontend's client-type-to-icon/label map duplicates (defensively)
  this repo's `_normalize_client` CLI-variant collapsing. Confirmed consistent: frontend will
  only ever see the already-normalized `"CLI"` or `"claude-desktop"` from this repo's API
  response, not the raw variants.

### Derived Conventions

- When adding a new ES-bucket dimension to an existing terms aggregation (branch, client),
  check whether sibling dimensions already use `missing` fallbacks and whether the new one
  needs the same — an inconsistency here (as found for `repositories` vs. `branches`/
  `clients`) is easy to introduce and easy to miss without directly querying production/preview
  data for the actual failure mode.
- When two numbers in the UI are reported as "not matching" (a top-level summary vs. a
  detail-table sum), check whether both are computed from the *same* filtered aggregation
  subtree or from two independently-filtered ones — that structural difference, not a
  calculation error, was the actual cause here.

---

## 4. Testing Landscape

### Existing Coverage

No unit tests exist for `classification_engine.py`'s `_build_cli_repository_classifications`,
`_normalize_branch`, or `_normalize_client` (confirmed via `grep -rn` across `tests/` — zero
hits). Verification for this task was done via direct interactive Python calls against
synthetic ES-shaped bucket dicts (see plan.md Task 1 Step 4) and against the real preview
Elasticsearch cluster (Task 2 Step 5) — consistent with how this handler family has been
verified historically (no existing test harness to extend).

### Gaps

- No regression test locks in the empty-repository → `Cowork` fallback, the `(repository,
  branch, client)` merge behavior, or the `HEAD`-vs-`-` display distinction. All three are
  now documented here and in spec.md; a future pass should add unit coverage directly against
  `_build_cli_repository_classifications` using the synthetic-bucket pattern from plan.md
  Task 1 Step 4, since it requires no live ES connection.
