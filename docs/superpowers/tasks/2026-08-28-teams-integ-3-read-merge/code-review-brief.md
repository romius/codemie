# Code review — 2026-08-28-teams-integ-3-read-merge (2026-08-27)

**request-changes** · confidence: low · 9 blocking · 0 deferred · 4 filtered as noise
Coverage: blind ✓ · edge-case ✓ · verification-gap ✓ · acceptance — n/a (no spec)  (3/4 lenses ran)

No spec/story artifact existed for this round, so acceptance never ran — the business_review section is
empty by contract, not evidence of a clean check.

## Look here first

- `src/external/alembic/versions/c3d4e5f6a7b8_migrate_assistant_project_mapping_to_settings.py:95` — [infra] downgrade() deletes every ms_teams settings row a revision earlier than needed, with no backup or comment — CR-008
- `src/codemie/rest_api/routers/project_settings.py:114` — [security] no teamsBotIntegration feature-flag check left anywhere in the ms_teams create/update path — CR-001
- `src/external/alembic/versions/c3d4e5f6a7b8_migrate_assistant_project_mapping_to_settings.py:73` — [infra] upgrade() drops the source table even when rows were skipped as unmigrated — CR-007
- `src/codemie/service/settings/settings_request_validator.py:269` — [other, data-integrity] ms_teams assistant_ids are never revalidated after an assistant is deleted or moved; the old FK cascade is gone — CR-005
- `src/codemie/rest_api/routers/project_settings.py:180` — [security] update path validates assistant membership against an unverified request.project_name — CR-004

## Also flagged

- `src/codemie/rest_api/routers/project_settings.py:124` — [other] IntegrityError mismapped to the ms_teams-only 409 message for other credential types — CR-002
- `src/codemie/rest_api/routers/project_settings.py:124` — [other] no test exercises the IntegrityError→409 mapping — CR-003
- `src/external/alembic/versions/c3d4e5f6a7b8_migrate_assistant_project_mapping_to_settings.py:43` — [infra] migration transform logic has no unit test, unlike the repo's own pattern — CR-006
- (commit history) — [other] two commits omit the EPMCDME-14111 prefix — CR-009

## Checked and clean

code-quality ✓ · security ✓ · commit-format ? unverified (partial — 2 of 16 commit subjects non-conforming, see CR-009)
