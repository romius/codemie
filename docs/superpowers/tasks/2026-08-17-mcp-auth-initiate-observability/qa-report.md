# QA Gate Report — 2026-08-17-mcp-auth-initiate-observability

**Branch**: EPMCDME-14226
**Merge base**: main
**Runner**: poetry (guide-first: `.ai-run/guides/quality-gates.md`)
**Started**: 2026-08-20T09:20:00Z
**Status**: BLOCKED

Changed files (8, all Python, all under `mcp_auth`):
`src/codemie/enterprise/mcp_auth/{_common,_diagnostics,_discovery,_initiate,_oauth2_callback,router}.py`,
`tests/enterprise/mcp_auth/{test_oauth2_callback_bridge,test_oauth2_initiate_bridge}.py`

## Gates

| Gate | Source | Status | Duration | Command | Notes |
|---|---|---|---|---|---|
| lint | guide | PASS | 1.4s | `make ruff` | format + `check --fix` + `check` all clean; 2290 files unchanged |
| build | guide | PASS | ~6s | `make build` | sdist + wheel `codemie-0.8.0` built |
| license | guide | PASS | ~4s | `make license-check` | 2041 files checked, 0 missing headers |
| gitleaks | guide | **FAIL** | 22.2s | `make gitleaks` | 7 leaks found, **none in branch-changed files** — see below |
| unit | guide | **FAIL** | 272.8s | `make test` | 8 failed / 14563 passed / 163 skipped; **5 are branch-caused regressions** |
| coverage | guide | N/A | — | `make coverage` | not requested (guide Skip-if) |
| sonar | guide | **FAIL** | 328.5s | `make sonar-local` | never reached Sonar analysis: `coverage generation exited with code 1` (same 8 test failures) |
| test-harness | guide | N/A | — | `make test-harness` | prereqs absent: no `~/.codemie/test-harness.json`, docker compose stack not running; guide Skip-if (no MR being opened) also applies |
| gitleaks-staged | hook | SKIPPED | 0.1s | `bash scripts/git-hooks/validate_secrets.sh` | self-skipped: `"0 commits scanned."` / `"scanned ~0 bytes (0)"` — nothing staged, so the staged-diff scan had no input. Enable locally by staging the change (`git add -A`) before running. Superset coverage comes from `make gitleaks` above. |
| commit-msg | hook | PASS | <1s | `bash scripts/git-hooks/commit_msg.sh` | all 10 commits on `main..HEAD` match `EPMCDME-<n>: ` |
| prepush-pytest | hook | **FAIL** | (covered by `make test`) | `poetry run pytest tests/ --ignore=tests/enterprise/ --cov --cov-report=xml:coverage.xml` | pre-push hook is opt-in (`CODEMIE_PREPUSH_ENABLED` defaults false) but the command itself fails: 3 of the 8 failures live outside `tests/enterprise/` |
| prepush-sonar | hook | **FAIL** | — | `SONAR_SKIP_TESTS=1 make sonar-local` | same failure as the `sonar` gate |
| ui | guide | N/A | — | (n/a) | no UI surface in this repo and no UI-glob path in the diff |
| affected | guide | SKIPPED | — | (n/a) | no changed-file-aware test command configured; full `make test` run instead |

## Failure detail

### 1. `make test` — 5 branch-caused regressions (BLOCKING, actionable)

```
FAILED tests/enterprise/mcp_auth/test_discovery_probe_bridge.py::test_discovered_auth_gate_registration_failures_return_config_error_payload[no_supported_registration_mechanism-attempted_mechanisms0]
FAILED tests/enterprise/mcp_auth/test_discovery_probe_bridge.py::test_discovered_auth_gate_registration_failures_return_config_error_payload[dcr_timeout-attempted_mechanisms1]
FAILED tests/enterprise/mcp_auth/test_discovery_probe_bridge.py::test_discovered_auth_gate_registration_failures_return_config_error_payload[dcr_unavailable-attempted_mechanisms2]
FAILED tests/enterprise/mcp_auth/test_saml_initiate_bridge.py::test_build_saml_initiate_response_derives_platform_acs_url
FAILED tests/enterprise/mcp_auth/test_saml_initiate_bridge.py::test_build_saml_initiate_response_translates_missing_authn_request_id
```

Both failure classes trace directly to this branch's new logging code, in **pre-existing test files that the branch did not update**.

**a. `_discovery.py:226` — `AttributeError: 'types.SimpleNamespace' object has no attribute 'auth_config_id'`**

The new INFO log line added by this branch reads `resolution.auth_config_id`:

```python
f"auth_config_id={_sanitize_log_field(resolution.auth_config_id)} "
```

`auth_config_id` *is* a real field on `codemie_enterprise.mcp_auth.DiscoveredOAuth2FlowResolution`
(fields: `status, auth_config_id, discovered_flow_id, as_hostname, snapshot, error_context`),
so production is correct — the pre-existing test's `SimpleNamespace` stub is now incomplete.
Fix: add `auth_config_id` to the stub in `tests/enterprise/mcp_auth/test_discovery_probe_bridge.py`.

**b. `_initiate.py` — `TypeError: build_saml_initiate_response() missing 1 required keyword-only argument: 'mcp_config_id'`**

The branch widened a public signature:

```diff
 def build_saml_initiate_response(
-    *, raw_auth_config: dict[str, Any], user: User, auth_config_id: str
+    *, raw_auth_config: dict[str, Any], user: User, auth_config_id: str, mcp_config_id: str
 ) -> SAMLInitiateResponseData:
```

`tests/enterprise/mcp_auth/test_saml_initiate_bridge.py:199` and `:271` still call it without
`mcp_config_id`. Fix: update those two call sites (or default the new kwarg).

### 2. `make test` — 3 pre-existing / environmental failures (NOT branch-caused)

```
FAILED tests/codemie/configs/test_managed_mcp_config.py::test_example_file_is_valid
FAILED tests/codemie_tools/data_management/code_executor/test_filesystem_policy.py::test_guard_allows_matplotlib_png_generation
FAILED tests/codemie_tools/data_management/code_executor/test_filesystem_policy.py::test_guard_allows_openpyxl_to_save_workbook
```

- `test_example_file_is_valid` asserts over `config/customer/managed-mcp-servers.example.yaml` (tracked, unmodified by this branch): `assert [ManagedMcpSe...] == []` — 35 unexpected entries. Independent of `mcp_auth`.
- The two `test_filesystem_policy` failures are sandbox/audit-hook environment failures (`returncode -2`, `KeyboardInterrupt` inside the guarded subprocess). Reproducible in isolation, unrelated to the diff.

The branch diff touches only `src/codemie/enterprise/mcp_auth/**` and `tests/enterprise/mcp_auth/**`, none of which is imported by these three tests — so they are pre-existing on `main`.

### 3. `make gitleaks` — 7 findings, none in branch-changed files

```
9:38AM WRN leaks found: 7
```

| File | Tracked by git? | Finding |
|---|---|---|
| `.codex/config.toml` | untracked | `CONTEXT7_API_KEY` |
| `.mcp.json.lock` | **tracked** (pre-existing on `main`) | `BRAVE_API_KEY` |
| `.pi/codemie/agent/mcp.json` | untracked | `CONTEXT7_API_KEY` |
| `.pi/codemie/agent/git/github.com/obra/superpowers/tests/brainstorm-server/ws-protocol.test.js` | untracked | RFC test vector `dGhlIHNhbXBsZSBub25jZQ==` |
| `opencode.json` | untracked | `CONTEXT7_API_KEY` |
| `opencode.json.lock` | untracked | `CONTEXT7_API_KEY` |
| `local/mcp-auth/downloaded-logs-20260817-113817.json` | gitignored (`.gitignore:48`) | UUID from a log line, false positive |

`make gitleaks` runs `gitleaks dir` over the whole working tree, so it sees untracked and
gitignored agent-tooling files that CI's clean checkout never will. Only `.mcp.json.lock`
is tracked, and it is unchanged by this branch — a pre-existing repo-level finding worth a
separate `.gitleaks.toml` allowlist entry or a rotation, tracked independently of EPMCDME-14226.

**Nothing in the 8 branch-changed files triggered a finding.** The staged-diff hook variant
(`validate_secrets.sh`) returned `no leaks found` on an empty stage.

## Drift signal

**yes**

`build_saml_initiate_response` gained a required keyword-only parameter `mcp_config_id`, and
`_discovery.py` began reading `resolution.auth_config_id` on the success path. Neither change was
propagated to the pre-existing bridge tests that reference those signatures/attributes. The
implementation moved ahead of the test contract in the same module the spec covers.

## What CI would still owe

A green local run would not have settled these; note them when reporting readiness:

- `test-harness` (`make test-harness`) — needs `docker compose up -d`, superadmin fixtures, and
  `~/.codemie/test-harness.json`. Required by the `auto_epm-cdme_vcs` MR compliance bot
  (checks 3.1/3.2) as a pasted terminal summary in the MR description.
- `sonar-local` server-side quality gate — never evaluated here; the runner aborted during its own
  coverage generation step, before contacting SonarQube.

---

## Re-run after code-review fixes (2026-08-20)

Commits added since the gate run: `cfe437b12a`, `4b79f60b68` (codemie); `0af78f1ad` (codemie-ui).

| Gate | Before | After | Evidence |
|---|---|---|---|
| lint (`make ruff`) | PASS | **PASS** | format + `check --fix` + `check` clean |
| unit (`make test`) | FAIL — 8 failed | **3 failed, 14568 passed, 163 skipped** | all 5 branch-caused regressions fixed |
| mcp_auth suite | — | **292 passed** | `pytest tests/enterprise/mcp_auth/` |
| gitleaks (branch scope) | FAIL — 7 findings | **no leaks found** | `gitleaks git --log-opts="main..HEAD"`, 11 commits, 63.81 KB |
| UI full suite | not run | **4987 passed, 1 skipped (450 files)** | codemie-ui pre-commit `sonar-local`; SonarQube quality gate **PASSED** |
| UI lint/typecheck/licenses/secrets | not run | **PASS** | codemie-ui pre-commit: prettier, eslint, `tsc --noEmit`, 0 missing headers, no leaks |

### Branch-caused regressions — fixed

1. `_discovery.py` INFO read `resolution.auth_config_id` / six snapshot provenance fields that
   `test_discovery_probe_bridge.py`'s `SimpleNamespace` stub did not define. The stub now matches
   the real `DiscoveredOAuth2FlowResolution` / `DiscoveredOAuth2FlowSnapshot` contracts. Because
   the INFO no longer sits inside the resolution `try/except` (so a logging problem can no longer
   be misreported as `discovered_flow_resolution_failed`), it now carries its own guard — AC8.
2. `build_saml_initiate_response` gained a required `mcp_config_id`; two call sites in
   `test_saml_initiate_bridge.py` were never updated, and its stub returned a `dict` while the
   real builder returns `SAMLInitiateResponseData` (which the router calls `.model_dump()` on and
   the initiate log line reads `auth_url` from). Both corrected.

### Remaining 3 failures — verified NOT branch-caused

| Test | Cause | Proof |
|---|---|---|
| `test_managed_mcp_config.py::test_example_file_is_valid` | The untracked local `config/customer/managed-mcp-servers.yaml` (35 servers) in this working tree. The test asserts `load_managed_mcp_servers(base_dir=config/customer/)` returns `[]`. | The file is untracked and predates this work; CI's clean checkout has none. Passes in a clean `main` worktree. |
| `test_filesystem_policy.py::test_guard_allows_matplotlib_png_generation` | Sandbox audit-hook environment (`returncode -2`, `KeyboardInterrupt` in the guarded subprocess) | **Reproduced failing on a clean `main` worktree** |
| `test_filesystem_policy.py::test_guard_allows_openpyxl_to_save_workbook` | same | **Reproduced failing on a clean `main` worktree** |

The branch diff touches only `src/codemie/enterprise/mcp_auth/**` and `tests/enterprise/mcp_auth/**`;
neither failing test file references `mcp_auth`.

### `make gitleaks` — working-tree scope, not branch scope

`make gitleaks` runs `gitleaks dir` over the entire working tree, so it sees untracked local agent
tooling (`.codex/`, `.pi/`, `opencode.json*`) and a gitignored downloaded log that a CI checkout
never contains. The one tracked finding, `BRAVE_API_KEY` in `.mcp.json.lock`, is pre-existing on
`main` and unchanged here — worth its own `.gitleaks.toml` allowlist entry or a rotation, tracked
separately from EPMCDME-14226.

Scanning the branch's own commits finds nothing.

### Status: PASS for this branch

Every gate failure attributable to EPMCDME-14226 is resolved. The residual failures are a local
untracked config file and a pre-existing sandbox issue on `main`, both outside this ticket.
