# Code review — 2026-08-20-epmcdme-14305-managed-mcp-oauth-issuer (2026-08-20)

**request-changes** · confidence: medium · 1 blocking · 0 deferred · 20 filtered as noise
Coverage: blind ✓ · edge-case ✓ · verification-gap ✓ · acceptance ✓  (4/4 lenses ran)

## Look here first

- `Makefile` — [other: verification] AC7's required gates were never run — only two targeted `pytest` calls are recorded, so lint, license and the full suite are unverified — CR-001

## Also flagged

- AC6 — the definitive signal (Claude Desktop connects with no RFC 9207 mismatch) is out-of-repo and post-deploy; the spec sequences it after merge → deploy → ConfigMap, so no in-repo evidence exists yet. Not a code defect: the mechanism is verified YAML → HTTP at `tests/codemie/rest_api/routers/test_mcp_managed.py:158`.

## Checked and clean

commit-format ✓ · security ✓ · code-quality partial (`Optional[List[str]]` where the guide prefers `list[str] | None`; module-wide legacy style, Ruff green, and the ticket prescribes this exact declaration — recorded for visibility, not blocking) · 20 candidates dismissed after reading source, incl. the missing-import, test-auth-header and second-catalog-copy claims (false positives) and the `authorizationServer: null` shape change (the endpoint already serves `null` for every absent optional field).
