# Plan — EPMCDME-11692: Propagate request headers into workflow context store

## Task 1 — Add `PROPAGATED_HEADERS_CONTEXT_KEY` constant

- File: `src/codemie/workflows/constants.py`
- Action: append `PROPAGATED_HEADERS_CONTEXT_KEY: str = "propagated_headers"` after the existing constants block.
- Test-first: yes — import the constant in the test and assert its value equals `"propagated_headers"`.

## Task 2 — Inject headers in `WorkflowExecutor.on_workflow_start`

- File: `src/codemie/workflows/workflow.py`
- Action: inside `on_workflow_start`, after the `file_names` branch and before the `resume_execution` guard, add:
  ```python
  if self.request_headers:
      initial_context[PROPAGATED_HEADERS_CONTEXT_KEY] = self.request_headers
  ```
- Test-first: yes — write a failing test that asserts `inputs[CONTEXT_STORE_VARIABLE]["propagated_headers"]` matches the provided dict before adding the implementation.

## Task 3 — Write unit tests for the three scenarios

- File: `tests/codemie/workflows/test_workflow_on_start.py` (new)
- Tests:
  1. `test_on_workflow_start_injects_headers_when_present` — non-resume with headers present.
  2. `test_on_workflow_start_omits_headers_key_when_none` — non-resume with `request_headers=None`.
  3. `test_on_workflow_start_resume_returns_none_inputs` — resume path returns `None`.
- Test-first: yes — all three were written before implementation.

## Validation

- `make ruff` — no lint/format violations.
- `make test` — 13 195 passed, 56 pre-existing failures unrelated to this branch, 129 skipped.
