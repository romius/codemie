# Technical Analysis — GitLab MR action read from the wrong payload level

Ticket: EPMCDME-8384 (follow-up fix to the merged feature)
Branch: `EPMCDME-8384_gitlab-mr-action-from-object-attributes`

## How the defect was found

Not by code reading. A webhook was registered in a real GitLab project
(`oleg_sotnichenko/codemie-public-skills`, hook 146999) pointing at a
webhook.site collector, a real MR was opened, and the captured delivery was
replayed into the local stack. With `gitlab_event_filter = open,merge` and a
delivery whose action was `open`, the backend answered:

```
{"message":"Webhook received, event filtered out"}   HTTP 200
GitLab MR action 'None' filtered out ... (allowed: open,merge)
```

## Codebase Findings

- `src/codemie/triggers/bindings/gitlab_webhook_security.py`
  - `extract_mr_action` (pre-fix line 82) read `body.get("action")` — top level only.
  - `apply_mr_action_filter` / `validate_mr_event_type` / `verify_and_filter` all
    derive the action from `extract_mr_action`, so the defect propagates to every
    filter entry point.
  - `is_mr_event` / `_parse_body` were already correct (they only look at
    `object_kind`), which is why non-MR events behaved fine.
- `src/codemie/triggers/bindings/webhook.py`
  - `_verify_gitlab_token` consumes the `(dispatch, filtered_action)` tuple and
    ACKs with 200 on `dispatch=False`. No change needed; it faithfully reported
    `filtered_action='None'` in the logs, which is what exposed the root cause.
- Real GitLab payload shape (verified, GitLab 17.11.7)
  - Top-level keys: `changes`, `event_type`, `labels`, `object_attributes`,
    `object_kind`, `project`, `repository`, `user`.
  - **No top-level `action`.** The action is `object_attributes.action`.
- Test blind spot
  - `tests/codemie/triggers/bindings/conftest.py::_mr_payload` produced a hybrid
    that exists nowhere in reality: top-level `action` set, `object_attributes`
    present but *without* `action`. All 111 binding tests passed against a broken
    implementation.
  - `codemie-sdk/test-harness/codemie_test_harness/tests/utils/webhook_utils.py`
    has the same shape, so the SDK e2e coverage shared the blind spot.

## Impact

Any webhook with a configured MR-action filter dropped **all** merge_request
events, including allowed ones. Effectively the filter turned the webhook off
for MRs while still answering 200, so nothing surfaced as an error — no failed
deliveries in GitLab, no 4xx metrics, only a silent absence of invocations.

## Risk Indicators

- Single-function root cause, no API/schema change, no migration.
- Fallback retained for a top-level `action` → no breakage for any consumer
  (including the SDK harness) that sends the old synthetic shape.
- Behavior change is strictly "filter now works as documented"; a webhook whose
  filter previously suppressed everything will start invoking its resource, which
  is the intended semantics but is a live behavior change worth noting in the MR.
