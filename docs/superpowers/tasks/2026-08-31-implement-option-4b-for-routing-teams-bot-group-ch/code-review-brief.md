# Code review — 2026-08-31-implement-option-4b-for-routing-teams-bot-group-ch (2026-08-31)

**request-changes** · confidence: low · 10 blocking · 0 deferred · 9 filtered as noise
Coverage: blind ✓ · edge-case ✓ · verification-gap ✓ · acceptance — n/a (no spec)  (3/4 lenses ran)

Diff is oversized (`diff_metadata.oversized`): review read targeted windows around each finding's call
sites rather than every changed file end-to-end; treat this as partial coverage, not a full audit.

## Look here first

- `src/codemie/rest_api/handlers/assistant_handlers.py:125` — [auth] conversation-history `Ability(self.user)` checks run against the resolved Teams billing user, not the original authenticated caller — CR-001
- `src/external/alembic/versions/f1a2b3c4d5e6_add_ms_teams_singleton_unique_index.py:45` — [infra] singleton partial-unique-index predicate still targets the retired `'MS_TEAMS'` value after the enum was renamed to `'MSTeams'`; the DB-level race guard is now inert — CR-009
- `src/codemie/service/user/billing_user_resolver.py:38` — [billing] `resolve_billing_user` never checks the resolved user is active or excludes another service account — CR-006
- `src/codemie/rest_api/models/settings.py:331` — [infra] `prune_ms_teams_assistant_id` is an unbounded full-table scan whose failures are only logged, so pruning cost grows unbounded and orphaned assistant ids can linger silently — CR-002
- `src/codemie/rest_api/routers/project_settings.py:6136` — [other] MS_TEAMS setting update on an unknown `setting_id` raises `AttributeError` (422) instead of a 404 — CR-003

## Also flagged

- `src/codemie/service/settings/settings_request_validator.py:10653` — [other] `validate_ms_teams_request` has no project-existence/soft-delete check, unlike the retired mapping service — CR-005
- `src/codemie/service/settings/settings.py` — [other] no test exercises `SettingsService.delete_setting`'s OAuth-cleanup dispatch end-to-end, so a regression there (leaving revoked OAuth tokens live) would not be caught — CR-004
- `src/external/alembic/versions/c3d4e5f6a7b8_migrate_assistant_project_mapping_to_settings.py` — [infra] migrated ms_teams settings rows are inserted with a NULL `alias`, which later trips "Alias is required" on update — CR-008
- `src/codemie_tools/data_management/workspace/inspect_workspace_image_tool.py` — [other] vision-model call has a hardcoded `max_tokens` and no explicit timeout — CR-007

plus 1 more — see code-review-final.json

## Checked and clean

code-quality ✓ · security ✓ · commit-format ✗ 1 blocking finding (see CR-010) · 0 deferred
