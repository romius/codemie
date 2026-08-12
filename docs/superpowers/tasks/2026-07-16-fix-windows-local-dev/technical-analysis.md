# Technical Research

**Task**: windows docker readme setup local-dev
**Generated**: 2026-07-16T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Fix Windows local dev: make CodeMie backend build & run successfully + refine local dev README.

Problem: Building and running the CodeMie backend locally on Windows development machines is not possible when following the local development setup instructions in README and other *.md files.

Scope — what must work:
- Core services: PostgreSQL runs in a container, Elasticsearch runs in a container, CodeMie backend runs on the local dev machine (not in container) for the 'local backend dev' workflow
- Documentation deliverable: Fix and refine README.md and any referenced local setup docs, explicitly documenting and supporting two local development use cases:
  1. Develop and run CodeMie backend locally on dev machine — DB/Postgres and Elasticsearch in Docker containers, backend started locally, Windows supported
  2. Run CodeMie backend fully in Docker — intended for cases like developing codemie-ui against a running backend, backend started in Docker, Windows supported

Observed Windows blockers — verify and fix any Windows-specific steps/assumptions in docs and scripts that prevent:
- starting only Postgres + Elasticsearch in containers
- running the backend locally on Windows
- running the backend in Docker on Windows

README.md contradictions check: review README.md and any linked local setup docs for contradictions or inconsistent instructions (mismatched commands, conflicting prerequisites, conflicting env var names/values, inconsistent 'local vs docker' guidance).

Update agentic instructions: Instructions for agents must be updated to use both Windows and Linux compatible commands which need to run for local development purposes.

Out of scope: Fixing or making unit/integration tests pass on Windows.

Deliverables:
- Code changes so Windows developers can build and run the backend successfully for both supported workflows
- Documentation updates in README.md and referenced docs covering the two supported workflows end-to-end, including Windows and non-Windows notes where applicable, with contradictions removed.

---

## 2. Codebase Findings

### Existing Implementations

Docker / container layer:
- `docker-compose.yml` — Defines all services: codemie, postgres (`pgvector/pgvector:pg17`), elasticsearch (`8.18.4`), kibana, jaeger, litellm, pyroscope, prometheus, grafana. Contains the primary Windows blocker at line 187: `${HOME}` in the Docker secrets path for GCP credentials. The secrets block is parsed globally by Docker Compose at startup — this fails on Windows even when only starting `postgres` and `elasticsearch`, because Docker Compose validates secret file existence for all secrets at parse time.
- `Dockerfile` — Multi-stage Linux build (`python:3.12.12-slim`). Sets `POETRY_VIRTUALENVS_CREATE=false` and installs deps into `/venv`. Conditionally installs the `codemie-enterprise` optional extra via a GCP credentials secret. Not run on the Windows host — used only for Workflow 2.
- `.dockerignore` — Excludes `.venv`, `venv`, `.env.*`, `build`, `notebooks`, `utils` from the image context.

Python / Poetry layer:
- `pyproject.toml` — Python `>=3.12,<3.14`. Packages declared as `{ include = "codemie", from = "src" }` and `{ include = "codemie_tools", from = "src" }`. Poetry's package install wires up `src/` automatically — no manual `PYTHONPATH` or `cd src/` required for local runs.
- `Makefile` — Primary cross-platform entry point. `make run` = `poetry run uvicorn codemie.rest_api.main:app --host=0.0.0.0 --port=8080 --reload` from repo root. The `gitleaks` target uses `$$(pwd)` and `docker run -v $(pwd):/path` syntax which requires GNU make and bash. GNU make is not installed by default on Windows.

Application entry point:
- `src/codemie/rest_api/main.py` — FastAPI app factory at `codemie.rest_api.main:app`. Single uvicorn entrypoint used by both `make run` and the `codemie` Docker service.

Database migration layer:
- `src/external/alembic/alembic.ini` — Uses `%(here)s` for path resolution; comment in file notes forward slashes work on Windows. Paths are absolute relative to the ini file location.
- `src/external/alembic/env.py` — Imports `codemie.configs.config` and `codemie.clients.postgres.PostgresClient`. Requires Poetry-installed packages on the Python path.
- `src/external/alembic/README.MD` — Alembic migration workflows; bash commands only.

Scripts:
- `scripts/git-hooks/pre_commit.sh` — Bash-only pre-commit hook using `shopt`, `PIPESTATUS`, and other bash-specific builtins. On Windows, a fresh clone with `core.autocrlf=true` (the Git for Windows default) will give this file CRLF line endings, causing `bash: bad interpreter: No such file or directory` at hook execution time. There is no `.gitattributes` file in the repo to enforce LF for shell scripts.

Committed environment:
- `.env` — Base env committed to repo with safe local defaults: `IDP_PROVIDER=local`, `SUPERADMIN_EMAIL/PASSWORD`, `ENABLE_USER_MANAGEMENT=True`, `EMAIL_VERIFICATION_ENABLED=false`, `RATE_LIMIT_LOGIN=200/minute`.
- `.env.local` — Personal override file (currently only contains `AZURE_OPENAI_API_KEY`).

AI guidance:
- `.ai-run/guides/development/setup-guide.md` — Very thin (2 conventions: "use `poetry install`" and "use port 8080"). No Docker Compose commands, no OS-specific guidance, no step-by-step sequence.
- `.ai-run/guides/quality-gates.md` — All gate commands are `make <target>` — cross-platform as long as GNU make is available. This is the canonical source for validation commands.

### Architecture and Layers Affected

| Layer | Component | Change Required |
|---|---|---|
| Docker / Container Runtime | `docker-compose.yml` | Fix `${HOME}` GCP secret path (critical); document `host.docker.internal` behavior per OS |
| Python / Poetry Project | `pyproject.toml`, `Makefile` | No code changes needed; Makefile needs PowerShell equivalent commands documented |
| Application Entry Point | `src/codemie/rest_api/main.py` | No changes needed |
| Database Migration | `src/external/alembic/` | No code changes; `make migrate` target or documented cross-platform command needed |
| Documentation | `README.md`, `.ai-run/guides/development/setup-guide.md` | Major rewrite for Windows compatibility and workflow clarity |
| AI Guidance | `AGENTS.md` Shell rule | Update to support both Windows (PowerShell) and Linux syntax |

### Integration Points

Internal module dependencies:
- `src/external/alembic/env.py` → `codemie.configs`, `codemie.clients.postgres`, `codemie.rest_api.models.base` (all via Poetry-installed packages)
- `docker-compose.yml` → `Dockerfile` (build context for `codemie` service)
- `Makefile` targets → `pyproject.toml` (via `poetry run`)

External service connections relevant to local dev:
- PostgreSQL: `pgvector/pgvector:pg17` container; default `PG_URL=postgresql://postgres:password@localhost:5432/postgres` for Workflow 1; `postgresql://postgres:password@postgres:5432/postgres` for Workflow 2 (container-internal hostname)
- Elasticsearch: `docker.elastic.co/elasticsearch:8.18.4`; `xpack.security.enabled=false` for local dev; no auth needed
- GCP Artifact Registry: needed only if installing the `codemie-enterprise` optional extra; stub credentials file pattern used for local dev without GCP
- LiteLLM proxy: separate optional service in docker-compose; uses its own postgres schema; not in the minimal stack

Networking concern:
- `CALLBACK_API_BASE_URL` defaults to `http://host.docker.internal:8080` in `.env`. This is correct for Workflow 2 (backend inside Docker, callbacks loop through the host). For Workflow 1 (backend running on Windows localhost), agent callbacks will target `host.docker.internal:8080` rather than `localhost:8080`. This default must be documented and overridden for Workflow 1.

### Patterns and Conventions

- `poetry run <command>` from **repo root** is the universal entry point for all Python invocations. `cd src/` is never required — Poetry's package declarations in `pyproject.toml` wire up `src/` automatically.
- `make <target>` wraps all common operations (`run`, `lint`, `test`, `format`, `gitleaks`). These are the lowest-friction cross-platform entry points when GNU make is available.
- `docker compose` (no hyphen, modern CLI syntax) used consistently. Requires Docker Desktop ≥ v2.0.
- Committed `.env` at repo root with safe defaults; personal overrides in `.env.local`. No `.env.example` or `.env.template` exists.
- Docker secrets pattern for GCP credentials: a stub empty file at the expected path satisfies Docker Compose when GCP is not needed locally.
- `host.docker.internal:host-gateway` `extra_hosts` entry in docker-compose is Linux Docker Engine-specific (where `host.docker.internal` is not automatic). Docker Desktop on Windows and Mac resolves it automatically without this entry.
- `POETRY_VIRTUALENVS_CREATE=false` in Dockerfile; virtual environment is `/venv` inside the image. For local dev, Poetry manages the venv normally.
- Enterprise vs. OSS: `INSTALL_ENTERPRISE=true` is the docker-compose default. `make install-oss` installs only the OSS Python packages.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `C:\Repos\EPM-CDME\codemie\.ai-run\guides\development\setup-guide.md` — Covers this domain but is extremely thin (2 bullets). Points to README:83 and Makefile:15 but does not reproduce steps. No Windows content.
- `C:\Repos\EPM-CDME\codemie\.ai-run\guides\development\local-testing.md` — Test policy only; not relevant to setup.
- `C:\Repos\EPM-CDME\codemie\.ai-run\guides\development\configuration-patterns.md` — Env var naming conventions only; no setup or OS-specific content.
- `C:\Repos\EPM-CDME\codemie\.ai-run\guides\quality-gates.md` — Canonical source for `make` validation commands. All targets are cross-platform (assuming GNU make). Does not cover Windows setup prerequisites.
- `C:\Repos\EPM-CDME\codemie\.ai-run\guides\project.md` — Project identity (EPMCDME / GitLab); not directly relevant to local dev.
- No docs in `docs/` cover local development setup; `docs/` contains sprint/spec/handoff files only.

### Architectural Decisions

- No ADR directory or formal decision records found anywhere in the repo.
- Two supported local-dev workflows are implicit from `docker-compose.yml` and `README.md` structure but not formally recorded.
- `INSTALL_ENTERPRISE=true` is the implicit default in docker-compose (docker build arg); OSS-only install requires `make install-oss`. This decision is not documented in any guide.

### Derived Conventions

- **Workflow 1 (local backend + Docker DB/ES)**: `docker compose up postgres elasticsearch` → `poetry install` → `poetry run alembic -c src/external/alembic/alembic.ini upgrade head` (from repo root) → `make run` (or `poetry run uvicorn codemie.rest_api.main:app --host=0.0.0.0 --port=8080 --reload`)
- **Workflow 2 (all in Docker)**: `docker compose up --build codemie postgres elasticsearch` — the `codemie` service mounts `./src`, `./config`, `./.env`, `./codemie-storage` as volumes; container waits for healthchecks on postgres and elasticsearch before starting
- All local Python commands must be run from the **repo root** (not from `src/`) so that `.env`, `.keys/`, `codemie-storage/`, and `codemie-repos/` relative paths resolve correctly
- `docker compose` (not `docker-compose`) is the correct modern CLI form used throughout the project

---

## 4. Testing Landscape

### Existing Coverage

- `tests/conftest.py` — Session-scoped autouse fixture patches `PostgresClient.get_engine` globally; loads `tests/.env.test`. No real DB or ES connection is ever made in tests.
- `tests/.env.test` — Overrides: `ENABLE_USER_MANAGEMENT=false`, `IDP_PROVIDER=keycloak`, `ENABLE_FILE_MULTIPROCESSING=false`.
- `tests/enterprise/conftest.py` — Fixtures simulating enterprise package installed / not installed.
- `tests/codemie/rest_api/routers/conftest.py` — Disables rate limiter; injects `request.state.uuid` for bare FastAPI test apps.
- `tests/codemie/service/monitoring/conftest.py` — Resets `litellm_context` ContextVar per test.
- `tests/codemie/rest_api/test_startup_integration.py` — Tests FastAPI lifespan / LiteLLM startup; fully mocked.
- `tests/external/deployment_scripts/test_preconfigured_assistants.py` — Tests preconfigured-assistant deployment scripts; uses `MagicMock`.
- `tests/external/deployment_scripts/test_preconfigured_workflows.py` — Same for workflow scripts.
- No test file covers Docker setup, docker-compose.yml validity, Windows path handling, or local-dev environment bootstrap.

### Testing Framework and Patterns

- pytest `^8.3.1`, pytest-asyncio `^0.23.7`, pytest-cov `^5.0.0`, pytest-env `^1.1.3`, pytest-mock `^3.14.0`, pytest-httpx `^0.35.0`
- `pytest.ini`: `testpaths = tests`, `pythonpath = src`, `addopts = --import-mode=importlib`
- Note: `pytest.ini` default `PG_URL=postgresql://pg:pg123@localhost:111/postgres` uses port `111` — this is a placeholder, not the running DB port (5432). This could confuse Windows developers.
- Dominant mock pattern: `unittest.mock.MagicMock` / `AsyncMock` / `patch()` (~5700 occurrences across ~250 test files)
- Domain-specific `conftest.py` fixtures co-located with test subdirectories
- No CI/CD pipeline configured: `.github/` contains only issue and PR templates — zero workflow YAML files. Tests are manually triggered only.

### Coverage Gaps

- No tests for `docker-compose.yml` service definitions or healthcheck correctness
- No tests for Windows-specific path handling (e.g., `REPOS_LOCAL_DIR`, `FILES_STORAGE_DIR` with backslash separators)
- No tests for environment variable loading under Windows (line endings in `.env`, path separators)
- No integration or smoke tests for Workflow 1 (backend local + DB/ES in Docker)
- No integration or smoke tests for Workflow 2 (fully Dockerized on Windows)
- No tests validating Poetry environment setup on Windows
- No CI/CD to automatically verify that setup instructions work on any platform

(These gaps are noted for awareness. Per task scope, fixing or creating tests is out of scope.)

---

## 5. Configuration and Environment

### Environment Variables

DB (PostgreSQL):
- `PG_URL` — Full DSN override; if set, `POSTGRES_*` fields are ignored. docker-compose sets `postgresql://postgres:password@postgres:5432/postgres` (container-internal hostname). For Workflow 1 (local backend), leave unset and rely on `POSTGRES_HOST=localhost`.
- `POSTGRES_HOST` — Defaults to `localhost` (correct for Workflow 1)
- `POSTGRES_PORT` — Defaults to `5432`
- `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` — Default to `postgres` / `password` / `postgres`

Elasticsearch:
- `ELASTIC_URL` — Defaults to `http://localhost:9200` in code; docker-compose overrides to `http://elasticsearch:9200` for container-internal routing
- `ELASTIC_USERNAME` / `ELASTIC_PASSWORD` — Both default to empty string; docker-compose sets `xpack.security.enabled=false` so no auth needed for local dev

Auth:
- `IDP_PROVIDER=local` — Set in `.env`; local auth mode, no external IdP required
- `SUPERADMIN_EMAIL` / `SUPERADMIN_PASSWORD` — Auto-creates superadmin on first startup
- `JWT_PRIVATE_KEY_PATH=".keys/jwt_private.pem"` / `JWT_PUBLIC_KEY_PATH=".keys/jwt_public.pem"` — Relative paths resolved against process CWD. **If the backend is started from `src/` (as README instructs), these paths will not resolve.** Must run from repo root.

Networking:
- `CALLBACK_API_BASE_URL` — Defaults to `http://host.docker.internal:8080`. Correct for Workflow 2; incorrect for Workflow 1 (should be `http://localhost:8080` when backend runs on Windows host).

Storage (relative paths — CWD-sensitive):
- `REPOS_LOCAL_DIR=./codemie-repos` — Relative to process CWD
- `FILES_STORAGE_DIR=./codemie-storage` — Relative to process CWD; docker-compose mounts `./codemie-storage:/app/codemie-storage`

GCP:
- `HOME` — Referenced in docker-compose secrets: `file: ${HOME}/.config/gcloud/application_default_credentials.json`. Not defined in Windows PowerShell by default (`USERPROFILE` is the Windows equivalent). GCP stores ADC on Windows at `%APPDATA%\gcloud\application_default_credentials.json`, not `~/.config/gcloud/`.

### Configuration Files

- `C:\Repos\EPM-CDME\codemie\.env` — Base runtime env with committed safe defaults; loaded by pydantic-settings via `find_dotenv(".env")` from process CWD walking upward. Works from both repo root and `src/`.
- `C:\Repos\EPM-CDME\codemie\.env.local` — Personal override stub (only `AZURE_OPENAI_API_KEY` currently).
- `C:\Repos\EPM-CDME\codemie\tests\.env.test` — Test-only overrides; loaded at test collection time.
- `C:\Repos\EPM-CDME\codemie\src\codemie\configs\config.py` — Master pydantic-settings `Config` class; source of truth for all env var names, types, and defaults.
- `C:\Repos\EPM-CDME\codemie\docker-compose.yml` — Full service topology with env injections, port bindings, volume mounts, healthchecks.
- No `.env.example` or `.env.template` exists. New developers have no reference file showing required variables.

### Feature Flags and Deployment Concerns

Feature flags (all Boolean env vars):
- `ENABLE_USER_MANAGEMENT` — Master switch; `True` in `.env`
- `EMAIL_VERIFICATION_ENABLED` — `false` in `.env` for local dev
- `CODEMIE_PRECOMMIT_ENABLED` — `false` in `.env`
- `ENABLE_LANGGRAPH_AITOOLS_AGENT` — Defaults `True`
- `OTEL_ENABLED`, `PROMETHEUS_ENABLED`, `PYROSCOPE_ENABLED`, `LANGFUSE_TRACES` — All `False` by default (observability stack)
- `LLM_PROXY_ENABLED`, `MCP_AUTH_ENABLED`, `SHAREPOINT_PKCE_ENABLED`, `TOOL_SELECTION_ENABLED` — All `False` by default

Deployment concerns:
- **`${HOME}` in docker-compose secrets**: Undefined on Windows; Docker Compose validates the secret file path at parse time for all secrets, even when only `postgres` and `elasticsearch` services are started. This is the single most impactful blocker — it prevents Workflow 1 container startup on Windows.
- **Docker Desktop memory**: ES `mem_limit: 4g` requires Docker Desktop memory allocation ≥ 4 GB; default on some Windows installations is 2 GB. `ulimits: memlock` and `nofile` are silently ignored on Docker Desktop for Windows (Linux kernel features).
- **`host.docker.internal:host-gateway`**: The `extra_hosts` entry on the `codemie` service is Linux Docker Engine-specific. Docker Desktop for Windows auto-resolves `host.docker.internal` without it. Harmless but potentially confusing.
- **Missing bind-mount source directories**: `./codemie-repos` (used by codemie service) and `./prometheus_data` (used by prometheus service) do not exist on a fresh clone. Docker Desktop auto-creates missing bind-mount directories, but this is undocumented and unexpected behavior.
- **`PYTHONPATH` for local runs**: Not set in `.env` or `.env.local`. Correctly handled by Poetry's package install for `poetry run` and `make run` invocations. Would fail only if running `python` directly without `poetry run`.

---

## 6. Risk Indicators

- **CRITICAL — `docker-compose.yml` line 187: `${HOME}` secret path fails on Windows at parse time.** Docker Compose validates the `google_credentials` secret file for all services when any `docker compose up` command runs, even `docker compose up postgres elasticsearch`. On Windows PowerShell, `$HOME` is not set, so Docker Compose reports a missing secret file and refuses to start. This single issue blocks both Workflow 1 and Workflow 2 on Windows before a single container starts.

- **CRITICAL — README.md `cd src/` instruction (line 97) is wrong and breaks local auth.** README instructs developers to `cd src/` before running uvicorn. Poetry's `packages` config makes `cd src/` unnecessary. Worse, running from `src/` causes relative paths for `JWT_PRIVATE_KEY_PATH=".keys/jwt_private.pem"` and storage dirs to resolve incorrectly — local auth will fail on first startup.

- **HIGH — AGENTS.md Shell rule instructs agents to use bash/Linux syntax** (`AGENTS.md` line 77: "Shell | ANY shell command | Use bash/Linux syntax"). The dev host is Windows 11. This means every AI-assisted `make`, `export`, `chmod`, `awk`, or `poetry` command suggested by an agent will fail in PowerShell without modification. Updating this rule is in scope.

- **HIGH — No `.env.example` file.** New Windows developers have no reference showing which env vars are required, which are optional, and what safe defaults look like. They must read `config.py` and `docker-compose.yml` to reconstruct what to set.

- **HIGH — `CALLBACK_API_BASE_URL` default is wrong for Workflow 1.** Default `http://host.docker.internal:8080` is correct only when the backend runs inside Docker. For Workflow 1, agent callback URLs will target a non-existent host, silently breaking multi-step agent workflows.

- **MEDIUM — No `.gitattributes` file.** `scripts/git-hooks/pre_commit.sh` will receive CRLF line endings on Windows clone (`core.autocrlf=true` default). The `#!/usr/bin/env bash` shebang will be interpreted with a trailing `\r`, causing "bad interpreter" errors. Fix: add `.gitattributes` enforcing LF for `*.sh` files.

- **MEDIUM — GNU make not installed by default on Windows.** README mentions it as a prerequisite ("install via brew/choco if missing") but does not document the full Windows installation path. All quality-gate commands depend on `make`.

- **MEDIUM — README.md uses bash-only syntax throughout with no Windows alternatives.** Specific occurrences: `export CODEMIE_PRECOMMIT_ENABLED=false` (line 139; PS: `$env:CODEMIE_PRECOMMIT_ENABLED = "false"`), `chmod +x scripts/git-hooks/pre_commit.sh` (line 156; no-op on Windows NTFS), `$(poetry show --only main | awk '{print $1}' | tr '\n' ' ')` (pip-licenses; requires Linux pipeline tools).

- **MEDIUM — README.md Quick Start (step 3) omits the alembic migration step.** Running the backend without running migrations first will fail on a fresh database.

- **MEDIUM — No Makefile target for alembic migrations.** The migration command requires a directory change: `cd src/external/alembic && poetry run alembic upgrade head`. In PowerShell this must be chained differently: `Push-Location src\external\alembic; poetry run alembic upgrade head; Pop-Location`. A `make migrate` target would hide the OS-specific difference.

- **LOW — `pytest.ini` default `PG_URL` uses port `111` instead of `5432`.** This is a placeholder but may confuse developers who try to run tests against a real DB.

- **LOW — ES Docker ulimits (`memlock`, `nofile`) are silently ignored on Docker Desktop for Windows.** ES may emit warnings. `mem_limit: 4g` requires Docker Desktop RAM allocation ≥ 4 GB (some Windows installs default to 2 GB).

- **LOW — No CI/CD pipeline.** `.github/` contains only issue/PR templates. There is no automated verification that setup instructions work on any platform, including the Windows host.

- **LOW — `codemie-repos` and `prometheus_data` directories missing from repo.** Not mentioned in README or setup docs; Docker Desktop silently creates them.

- **INFO — `src/external/alembic/README.MD` uses bash-only commands** but is a tertiary doc; it should be updated to match any new cross-platform migration command.

---

## 7. Summary for Complexity Assessment

This task is primarily a documentation and configuration fix with a small number of targeted code changes. The affected layers are: Docker/Container Runtime (`docker-compose.yml`), the Python/Poetry project scaffolding (`Makefile`, `pyproject.toml`), application documentation (`README.md`), and AI agent guidance (`AGENTS.md`, `.ai-run/guides/development/setup-guide.md`). The application source code itself (`src/`) requires no changes. The estimated file change surface is 5–8 files: `docker-compose.yml` (one critical line fix plus documentation comments), `README.md` (major rewrite of the local-dev sections), `AGENTS.md` (Shell rule update), `.ai-run/guides/development/setup-guide.md` (expansion to a proper step-by-step guide), optionally `.gitattributes` (new file), optionally a new `.env.example` (new file), and optionally a `make migrate` target in `Makefile`.

The task follows established patterns in the project — no new architectural patterns are being introduced. The two supported workflows are already implicit in the existing `docker-compose.yml` and `Makefile`; the work is to make them explicit, correct, and Windows-compatible in documentation. The single most impactful code change is fixing the `${HOME}` GCP credentials secret path in `docker-compose.yml` (line 187), which currently blocks any `docker compose up` invocation on Windows even when only starting the database containers. The correct fix is to make the secret definition conditional or to document a platform-agnostic workaround (stub file creation with a Windows-compatible path or a shell wrapper that sets `HOME` before invoking Docker Compose).

Test coverage for the affected areas is zero — there are no existing tests for Docker setup, local-dev bootstrap, environment variable loading, or platform-specific path handling. The test suite is entirely mocked and never touches real infrastructure. This is not a risk for this specific task (test creation is explicitly out of scope), but it means there is no automated regression guard for the setup instructions. The absence of a CI/CD pipeline means correctness of all changes must be manually verified on a Windows machine. Key risk factors for complexity scoring: the `${HOME}` Docker Compose fix is deceptively simple to make but requires understanding of how Docker Compose evaluates secrets across all services; the README rewrite is the largest effort given the number of bash-only commands that need Windows-compatible alternatives; and the AGENTS.md Shell rule change has broad downstream impact since it governs all AI-generated commands in this repository.
