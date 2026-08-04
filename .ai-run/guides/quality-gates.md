# Quality Gates

## Gate Order

Use Makefile targets when available; they are the command source of truth for this repo. The Makefile defines install, build, test, ruff, license, gitleaks, verify, coverage, sonar-local, and run targets at `Makefile:15`.

### Lint And Format

**Policy**: Every language present in a change should be covered by a lint gate before delivery. Python is covered below; gaps for other languages (shell, container manifests, config formats) are tracked under EPMCDME-13739 and its sub-tasks.

**Run**: `make ruff`

**Pass**: Ruff format completes, `ruff check --fix` applies safe fixes, and final `ruff check` exits successfully. See `Makefile:30`.

**Fail**: Ruff reports remaining violations after auto-fix; fix the reported files before delivery.

**Auto-fix**: `make ruff` already runs format and fix steps.

### Build

**Run**: `make build`

**Pass**: Poetry builds the package successfully. See `Makefile:24`.

**Fail**: Packaging metadata, dependencies, or build configuration are invalid.

### License Headers

**Run**: `make license-check`

**Pass**: The Apache 2.0 header checker exits successfully. See `Makefile:45`.

**Fail**: One or more Python or shell files are missing required headers.

**Auto-fix**: `make license-fix`

### Secret Scan

Two gates run gitleaks against the same image (`ghcr.io/gitleaks/gitleaks:v8.30.1`) with the same `.gitleaks.toml` allowlist.

**CI / verify gate — `make gitleaks`**

Runs `gitleaks dir` against the full working tree. Docker-only; CI runners always have Docker. See `Makefile:51`.

- **Pass**: Docker runs gitleaks with `--config=/workspace/.gitleaks.toml` and no hardcoded secrets are found.
- **Fail**: A secret-like value is detected or Docker is unavailable.
- **Skip if**: Docker is unavailable; report the environment block explicitly.

**Local pre-commit gate — `codemie-gitleaks`**

Runs `gitleaks protect --staged` via `scripts/git-hooks/validate_secrets.sh`, wired as the `codemie-gitleaks` local hook in `.pre-commit-config.yaml`. Detects Docker → Podman → Apple Containers and uses the first live engine. Hard-blocks the commit (exit 1) with an actionable hint if no engine is running.

- **Pass**: gitleaks reports no leaks in the staged diff.
- **Fail**: A secret is detected in staged changes, or no container engine is available.
- **Bypass**: `CODEMIE_PRECOMMIT_ENABLED=false` skips both `codemie-pre-commit` and `codemie-gitleaks`. Use only when Docker/Podman is unavailable and the change is verified to contain no secrets — do NOT weaken to warn-and-continue.
- **Policy**: HIGH priority. Secrets must not enter a local commit.

### Tests

**Run**: `make test`

**Pass**: Pytest exits successfully over `tests/`. See `Makefile:27`.

**Fail**: A test failure, import error, fixture error, or environment prerequisite is missing.

**Skip if**: The user did not request tests and the active task policy says tests are explicit-only.

### Coverage

**Run**: `make coverage`

**Pass**: Coverage runs pytest and writes HTML coverage output. See `Makefile:56`.

**Fail**: Test or coverage command fails.

**Skip if**: The user did not request coverage.

### Static Analysis

**Run**: `make sonar-local`

**Pass**: The Node-based Sonar runner completes successfully. See `Makefile:63`.

**Fail**: Sonar prerequisites, token/config, Node runtime, coverage generation, or server-side quality gate fails.

**Skip if**: Sonar configuration, network access, or required credentials are unavailable.

### Full Verification

**Run**: `make verify`

**Pass**: Ruff, license, gitleaks, and tests complete successfully. See `Makefile:54`.

**Fail**: The first failing prerequisite determines the next debugging target.

**Skip if**: The task scope does not call for full verification or environment prerequisites are missing.

### Test Harness (MR compliance gate)

**Run**: `make test-harness`

**Pass**: The end-to-end test harness completes successfully. See `Makefile` `test-harness` target.

**Fail**: One or more scenarios fail; paste the terminal summary into the MR anyway so reviewers see the failure.

**Skip if**: You are not opening a merge request (local iteration only).

> **Required for the MR compliance bot**: paste the copy-pasted terminal summary of `make test-harness` into a `## Test harness` section of the MR description as a code block. Screenshots are not accepted. Without this section the `auto_epm-cdme_vcs` bot fails checks 3.1 and 3.2. Prereqs: docker stack up (`docker compose up -d`), superadmin fixtures, `~/.codemie/test-harness.json`; see the `codemie-test-harness-local-setup` memory / setup guide for the ENV=local Bearer-hijack patch.
