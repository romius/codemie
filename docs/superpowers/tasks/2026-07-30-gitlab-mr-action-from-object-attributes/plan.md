# Plan — Read GitLab MR action from `object_attributes`

Ticket: EPMCDME-8384 · Flow: sdlc-light · Branch: `EPMCDME-8384_gitlab-mr-action-from-object-attributes`

## Clarification assumptions

- Fix lands as a follow-up on the original ticket rather than a new bug ticket.
- The top-level `action` key stays supported as a fallback (custom webhook
  templates can produce it, and the SDK harness currently sends it).

## Task 1 — Make the fixtures reflect a real GitLab delivery

Test-first: yes — moving `action` into `object_attributes` (and dropping the
top-level key) must turn the existing filter tests RED against the current
implementation, proving the defect.

- Rewrite `_mr_payload` in `tests/codemie/triggers/bindings/conftest.py` to the
  verified real shape: `object_attributes.action`, no top-level `action`, plus
  `labels`, `changes`, `repository`.
- Add `_mr_payload_top_level_action` + fixture
  `gitlab_mr_open_payload_top_level_action` for the fallback path.

Observed RED: 19 failed / 99 passed (12 pre-existing tests flipped to failing).

## Task 2 — Add explicit regression tests

Test-first: yes — each new test fails before the implementation change.

In `tests/codemie/triggers/bindings/test_gitlab_webhook_security.py`:

- action read from `object_attributes` (asserts the absence of a top-level key)
- top-level fallback still honored
- `object_attributes` wins when both are present
- unknown action in `object_attributes` → `None`
- allowed action on a real payload → dispatch
- disallowed action on a real payload → filtered with the action reported
- raw-bytes body path behaves like the dict path

## Task 3 — Fix the extractor

Test-first: covered by tasks 1–2.

- `extract_mr_action`: parse via the existing `_parse_body`, read
  `object_attributes.action`, fall back to the top-level `action`, then validate
  against `MR_ACTIONS`.

Observed GREEN: 118 passed in `tests/codemie/triggers/bindings/`.

## Task 4 — Correct the documentation

Test-first: no — documentation only.

- `docs/webhook-configuration.md`: filtered actions ACK with **200**
  (`Webhook received, event filtered out`), not 400; state why (GitLab
  auto-disables hooks after repeated non-2xx) and document where the action is
  read from.

## Out of scope (follow-ups)

- `codemie-sdk/test-harness/.../webhook_utils.py` shares the unrealistic payload
  shape. Separate MR in the SDK repo.
- Redis-backed webhook rate limiter is unavailable in the local compose default
  profile (`Error 111 connecting to localhost:6379`); noisy but degrades
  correctly. Unrelated to this fix.
