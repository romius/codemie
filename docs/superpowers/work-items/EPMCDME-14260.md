# Work Item: EPMCDME-14260

**Status:** Ready for review
**External Ticket:** https://jiraeu.epam.com/browse/EPMCDME-14260
**External Key:** EPMCDME-14260
**Assignee:** Dmytro Bondarenko5
**Epic:** EPMCDME-13283 — Budgeting & Spend Control
**Issue Type:** Task

## Summary

[Internal] Classify Chrome extension LiteLLM spend separately from CLI traffic

## Description

Update `_is_cli_request` in `src/codemie/enterprise/litellm/proxy_router.py` to classify CLI vs Chrome extension traffic based on `client_type` rather than the presence of the `X-CodeMie-CLI` header. Chrome extension requests (`client_type=codemie-chrome-extension`) must be assigned to `BudgetCategory.PLATFORM`, not `BudgetCategory.CLI`.

## Acceptance Criteria

1. `_is_cli_request` returns `False` when `client_type` is `codemie-chrome-extension`.
2. A non-empty `X-CodeMie-CLI` header does not classify a request as CLI when client type is not `codemie-cli` or `codemie_cli`.
3. Chrome extension spend is assigned to `BudgetCategory.PLATFORM`.
4. Chrome extension `codemie_litellm_proxy_usage` documents do not carry `cli_request: true`.
5. `_is_cli_request` returns `True` for `client_type=codemie-cli`.
6. `_is_cli_request` returns `True` for `client_type=codemie_cli`.
7. Client-type matching is case-insensitive.
8. Genuine CodeMie CLI spend continues to be assigned to `BudgetCategory.CLI`.
9. Existing billing/analytics queries using `attributes.cli_request == True` continue to include genuine CLI traffic but exclude Chrome extension traffic.
10. Automated tests cover: `codemie-chrome-extension`, `codemie-cli`, `codemie_cli`, different client-type casing, unrecognized client type with non-empty `X-CodeMie-CLI` header, and request without recognized client type.

## Branch

`EPMCDME-14260_classify-chrome-extension-spend` (from `main`, in-sync)

## Linked Artifacts

- docs/superpowers/runs/20260818-1741-main/requirements.md
- docs/superpowers/runs/20260818-1741-main/plan.md
- docs/superpowers/runs/20260818-1741-main/complexity.json
- docs/superpowers/runs/20260818-1741-main/actual-complexity.json
- docs/superpowers/runs/20260818-1741-main/code-review-final.json
- docs/superpowers/runs/20260818-1741-main/qa-report.md

## History

| Timestamp | Event | Actor |
|-----------|-------|-------|
| 2026-08-18T17:42:00Z | work_item.created | requirements-intake |
| 2026-08-18T17:42:00Z | work_item.adapter_receipt | requirements-intake |
| 2026-08-18T17:42:00Z | work_item.linked_artifact | requirements-intake |
| 2026-08-18T17:43:00Z | work_item.assigned: EPMCDME-14260_classify-chrome-extension-spend | sdlc-pipeline |
| 2026-08-18T17:43:00Z | prepare_for_development emitted — adapter_warning (intent not configured) | sdlc-pipeline |
| 2026-08-18T19:20:00Z | code-review.final: approve (confidence high, 0 findings) | code-review-orchestrator |
| 2026-08-18T19:25:00Z | qa-gates: PASSED (lint PASS; build/license/gitleaks/test SKIPPED env) | qa-gates |
| 2026-08-18T19:28:00Z | actual-complexity: S (10/36), delta -2 from initial 12/36 | complexity-assessor |
| 2026-08-18T19:30:00Z | work_item.transitioned: Ready for review | sdlc-pipeline |
