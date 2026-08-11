# QA Gate Report — 2026-08-11-epmcdme-14072-managed-mcp-oauth-config

**Branch**: EPMCDME-14072_managed-mcp-oauth
**Merge base**: main
**Runner**: poetry (gates resolved guide-first from `.ai-run/guides/quality-gates.md`)
**Started**: 2026-08-11T17:00:44+02:00
**Status**: BLOCKED

Changed files vs `main` (4):

- `src/codemie/configs/managed_mcp_config.py`
- `config/customer/managed-mcp-servers.example.yaml`
- `tests/codemie/configs/test_managed_mcp_config.py`
- `tests/codemie/rest_api/routers/test_mcp_managed.py`

## Gates

| Gate | Source | Status | Duration | Command | Notes |
|------|--------|--------|----------|---------|-------|
| lint | guide | PASS | 1s | `make ruff` | ruff format: 2256 files unchanged; `ruff check --fix` and `ruff check` both "All checks passed!" |
| build | guide | PASS | 6s | `make build` | poetry built sdist + wheel for codemie 0.8.0 |
| license | guide | PASS | 0s | `make license-check` | "Checked 2014 files, 0 missing license headers" |
| gitleaks | guide | FAIL | 6s | `make gitleaks` | 5 findings, **all in untracked local files, none in the branch diff** — see "Secret scan detail" |
| unit | guide | FAIL | 206s | `make test` | 2 failed, 14151 passed, 163 skipped. Both failures pre-exist the branch — see "Failure detail" |
| affected | guide | PASS | 23s | `poetry run pytest tests/codemie/configs/test_managed_mcp_config.py tests/codemie/rest_api/routers/test_mcp_managed.py` | 22 passed — the change's own tests are green |
| coverage | guide | SKIPPED | — | `make coverage` | Guide "Skip if": coverage not requested. A coverage run did happen inside `make sonar-local` and wrote `coverage.xml` |
| sonar | guide | FAIL | 353s | `make sonar-local` | Aborted at its own coverage-generation step with the **same 2 pre-existing test failures**; sonar-scanner never ran, so there is no independent Sonar signal |
| verify | guide | N/A | — | `make verify` | Superset of ruff + license + gitleaks + test; all four were run individually above |
| test-harness | guide | N/A | — | `make test-harness` | Guide "Skip if": not opening an MR from this run. Also missing prerequisites: `~/.codemie/test-harness.json` absent, docker compose stack not started. Required for the `auto_epm-cdme_vcs` MR compliance bot (checks 3.1 / 3.2) when the MR is opened |
| ui | guide | SKIPPED | — | (n/a) | No UI surface changed — diff is Python + YAML only. This is a green outcome |
| hook:ruff-staged | hook | SKIPPED | 0s | `bash scripts/git-hooks/_ruff_staged.sh` | self-skipped: "[pre-commit] No staged Python files - skipping Ruff format/fix check." Staged set is docs artifacts only. Formatting of the branch's Python is covered by the `make ruff` gate above |
| hook:ruff-check | hook | PASS | — | `poetry run ruff check` | Same command the `codemie-pre-commit` hook chains; covered by the `make ruff` gate |
| hook:license-check | hook | PASS | — | `poetry run python scripts/license_headers/check_license_headers.py --check --quiet` | Same command the `codemie-pre-commit` hook chains; covered by the `make license-check` gate |
| hook:gitleaks-staged | hook | PASS | 1s | `bash scripts/git-hooks/validate_secrets.sh` | Ran for real via docker: "scanned ~83658 bytes ... no leaks found". Scope caveat: `gitleaks protect --staged` only saw the 9 staged `docs/superpowers/...` artifacts, not the branch's 4 committed source files — those are covered by the full-tree `make gitleaks` scan, which reported no findings in them |
| hook:commit-msg | hook | PASS | 0s | `bash scripts/git-hooks/commit_msg.sh <msg-file>` | All 4 commits on `main..HEAD` match `^EPMCDME-[0-9]+:` |
| hook:pre-push | hook | SKIPPED | 0s | `bash scripts/git-hooks/pre_push.sh` | self-skipped: "[pre-push] CODEMIE_PREPUSH_ENABLED=false -> skipping heavy checks." Enable locally with `export CODEMIE_PREPUSH_ENABLED=true`. Its chained commands (full pytest + `make sonar-local`) were both run directly by this report — both FAIL, so this push hook *would* block a push if enabled |
| ci:wait-ci-init | ci | N/A | — | `.gitlab-ci.yml` → `.wait-ci-init-template` | Requires GitLab CI infrastructure; unreachable locally. It waits on external commit contexts `ci-pipeline` and `compliance-report`, which are produced outside this repo — only the real MR pipeline can settle them |

Hook installation note: `.git/hooks/pre-commit` is not present in this checkout, so the
`codemie-pre-commit` / `codemie-gitleaks` hooks are not currently wired locally
(`make install-hooks` installs them). Their commands were run explicitly here regardless.

Guide-vs-repo mismatch: none material. `.pre-commit-config.yaml` enforces exactly the commands the
guide documents (ruff, license headers, gitleaks, pytest, sonar) plus the `EPMCDME-<n>:` commit-msg
rule, which the quality-gates guide does not list as a gate; it is run and recorded above.

## Failure detail

### Blocking gate: `make test` (unit)

```
=========================== short test summary info ============================
FAILED tests/codemie_tools/data_management/code_executor/test_filesystem_policy.py::test_guard_allows_matplotlib_png_generation
FAILED tests/codemie_tools/data_management/code_executor/test_filesystem_policy.py::test_guard_allows_openpyxl_to_save_workbook
==== 2 failed, 14151 passed, 163 skipped, 192 warnings in 196.52s (0:03:16) ====
make: *** [Makefile:31: test] Error 1
```

Both assertions fail the same way — the guarded child process is killed by SIGINT:

```
_________________ test_guard_allows_matplotlib_png_generation __________________
        result = _run_guarded_as_file_in_workspace(
            workspace,
            "import matplotlib\nmatplotlib.use('Agg')\n...plt.savefig('chart.png')\n",
        )
>       assert result.returncode == 0
E       assert -2 == 0
E        +  where -2 = CompletedProcess(args=[... '/workspace/script.py', line 843, in audit_hook
E                                                 def audit_hook(event, args):
E                                             KeyboardInterrupt
E                                            ]).returncode
tests/codemie_tools/data_management/code_executor/test_filesystem_policy.py:731: AssertionError
```

**Attribution — this is NOT caused by the EPMCDME-14072 change, and it is NOT a missing dependency:**

1. Neither the failing test file (`tests/codemie_tools/data_management/code_executor/test_filesystem_policy.py`)
   nor the code under test (`src/codemie_tools/data_management/code_executor/`) is in this branch's
   4-file diff, and the working tree has no unstaged source modifications. The code exercised by
   these two tests is byte-identical to `main`, so the failure pre-exists the branch in this
   environment.
2. Not a dependency gap: `matplotlib` 3.10.8 and `openpyxl` 3.1.5 both import cleanly from
   `.venv/bin/python`. The failure is at process level — the sandboxed child exits with `-2`
   (SIGINT / `KeyboardInterrupt` raised inside the execution guard's audit hook), i.e. host- or
   sandbox-specific behaviour of the code-executor filesystem guard, not an import error.
3. Not cross-test interference: re-running that file alone reproduces exactly the same 2 failures
   (`2 failed, 54 passed in 7.53s`).
4. The change's own tests are green: 22 passed across
   `tests/codemie/configs/test_managed_mcp_config.py` and
   `tests/codemie/rest_api/routers/test_mcp_managed.py`.

`make sonar-local` failed for the same reason — it regenerates `coverage.xml` by re-running the same
suite, hit the same 2 failures, and exited before sonar-scanner started. It is a duplicate of this
failure, not a second independent one.

### Secret scan detail: `make gitleaks`

5 `generic-api-key` findings, all in files that are **untracked by git** and therefore not part of
this branch, this MR, or any CI checkout (`make gitleaks` runs `gitleaks dir` over the whole working
directory, including local developer tooling litter). Secret values deliberately not reproduced here:

| File | Line | Rule | Tracked by git |
|---|---|---|---|
| `.codex/config.toml` | 15 | generic-api-key | no |
| `.mcp.json.lock` | 11 | generic-api-key | no |
| `_bmad/_config/files-manifest.csv` | 47 | generic-api-key | no |
| `opencode.json` | 26 | generic-api-key | no |
| `opencode.json.lock` | 303 | generic-api-key | no |

Zero findings in the 4 changed files. The commit-time gate that actually guards the repository
(`gitleaks protect --staged`) passed. To clear this gate locally, remove or `.gitignore` the
untracked tooling files above (or add allowlist entries in `.gitleaks.toml` if they must stay).

## Drift signal

no

`ManagedMcpOAuthConfig` as implemented matches `spec.md` exactly: class name, field names and
camelCase aliases (`clientId`, `scope`, `callbackHost` defaulting to `localhost`, `callbackPort`
with `ge=1, le=65535`, `authorizationUrl`, `tokenUrl`), `extra="ignore"`, `populate_by_name` left
unset, `oauth: Optional[ManagedMcpOAuthConfig] = None` on `ManagedMcpServer`, and the scalar `auth`
field retained and marked deprecated rather than derived. The only behaviour beyond the spec text is
the `_use_default_if_none` `BeforeValidator` that lets a blank `callbackHost:` YAML key fall back to
the field default — an additive refinement consistent with the spec's stated default, added by
commit `9fb40f6c1`.
