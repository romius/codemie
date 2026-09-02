# Requirements — 20260818-1741-main

**Source**: ticket:EPMCDME-14260
**Work Item**: docs/superpowers/work-items/EPMCDME-14260.md
**Original input**: |
  EPMCDME-14260

## Goal

Update `_is_cli_request` in the LiteLLM proxy router to classify Chrome extension traffic as `BudgetCategory.PLATFORM` rather than `BudgetCategory.CLI`, by keying on `client_type` instead of the presence of the `X-CodeMie-CLI` header.

## Acceptance Criteria

1. `_is_cli_request` returns `False` when `client_type` is `codemie-chrome-extension`.
2. A non-empty `X-CodeMie-CLI` header alone does not classify a request as CLI when its `client_type` is not `codemie-cli` or `codemie_cli`.
3. Chrome extension spend through `/v1/chat/completions` is assigned to `BudgetCategory.PLATFORM`.
4. Chrome extension `codemie_litellm_proxy_usage` documents do not carry `cli_request: true`.
5. `_is_cli_request` returns `True` for `client_type=codemie-cli`.
6. `_is_cli_request` returns `True` for `client_type=codemie_cli`.
7. Client-type matching is case-insensitive.
8. Genuine CodeMie CLI spend continues to be assigned to `BudgetCategory.CLI`.
9. Existing billing and analytics queries using `attributes.cli_request == True` continue to include genuine CLI traffic but exclude Chrome extension traffic.
10. Automated tests cover: `codemie-chrome-extension`, `codemie-cli`, `codemie_cli`, different client-type casing, unrecognized client type with non-empty `X-CodeMie-CLI` header, and request without recognized client type.

## Context

- Affected file: `src/codemie/enterprise/litellm/proxy_router.py`
- Key functions: `_is_cli_request`, `_resolve_non_premium_tracking_identity`
- Related files: `cli_cost_processor.py`, `user_handler.py`, `project_handler.py`
- Chrome extension sends `client_type=codemie-chrome-extension` and `X-CodeMie-CLI: codemie-chrome-extension/<version>`
- Current bug: non-empty `X-CodeMie-CLI` header causes `_is_cli_request` to return `True` regardless of client type
- Fix: key on `client_type` only; no new `BudgetCategory` needed — use existing `BudgetCategory.PLATFORM`
- Epic: EPMCDME-13283 — Budgeting & Spend Control

## Open questions

(none)
