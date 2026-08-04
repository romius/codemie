# EPMCDME-11692 — Propagate request headers into workflow context store

## Problem

When a workflow execution starts, HTTP request headers (e.g. `X-Tenant`, custom routing headers) provided by the caller are available on `WorkflowExecutor.request_headers` but are not written into the LangGraph context store. Downstream nodes or tools that need to read them cannot access them consistently.

## Solution

Inject `request_headers` into `initial_context` under the key `PROPAGATED_HEADERS_CONTEXT_KEY = "propagated_headers"` inside `WorkflowExecutor.on_workflow_start`, conditional on:

- `self.request_headers` being truthy (non-empty dict); and
- this NOT being a resume execution (`self.resume_execution` is false).

The constant lives in `src/codemie/workflows/constants.py` alongside the existing `CONTEXT_STORE_VARIABLE`.

## Acceptance Criteria

1. When `WorkflowExecutor` is started (non-resume) with a non-empty `request_headers` dict, the returned `inputs[CONTEXT_STORE_VARIABLE]["propagated_headers"]` equals that dict.
2. When `request_headers` is `None` or empty, the `"propagated_headers"` key is absent from the context store.
3. On resume (`resume_execution=True`), `on_workflow_start` returns `None` as before — the headers are irrelevant there.
4. `make ruff` and `make test` pass with no regressions.

## Scope

- `src/codemie/workflows/constants.py` — add `PROPAGATED_HEADERS_CONTEXT_KEY` constant.
- `src/codemie/workflows/workflow.py` — add two lines in `on_workflow_start` to populate the context store key.
- `tests/codemie/workflows/test_workflow_on_start.py` — new test file covering the three scenarios above.
