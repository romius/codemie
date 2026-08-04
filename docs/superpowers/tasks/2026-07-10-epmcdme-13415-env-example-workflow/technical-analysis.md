# Technical Research

**Task**: dotenv gitignore env configuration security
**Generated**: 2026-07-10T00:00:00Z
**Research path**: filesystem

---

## 1. Original Context

Replace git-tracked local .env workflow with .env.example and ignored .env/.env.local

The backend .env is committed to git, and onboarding documentation tells contributors to put real API keys into it. This makes accidental credential leaks likely.

Current documentation conflict: The developer FAQ guidance says not to edit .env directly and to use .env.local for personal environment variables. However, the onboarding README currently instructs contributors to create/modify codemie/.env and put their DIAL API key there.

Proposed fix:
- Commit .env.example with placeholders only
- Add .env to .gitignore
- Keep .env.local ignored for personal overrides if supported
- Update onboarding docs to instruct contributors to copy .env.example to .env or use .env.local
- Ensure no real API keys or personal credentials are committed

Repositories: codemie (current), codemie-onboarding (separate repo)

---

## 2. Codebase Findings

### Existing Implementations

- `src/codemie/configs/config.py` — sole app configuration entrypoint; defines `Config(BaseSettings)` with all env-variable fields; loads `.env` via two overlapping paths: `SettingsConfigDict(env_file=find_dotenv(".env", raise_error_if_not_found=False))` at line 751 AND `load_dotenv(find_dotenv(".env", raise_error_if_not_found=False))` at module scope (line 920); `pydantic_settings` alone is sufficient — `load_dotenv` is redundant
- `.env` — committed to git; contains dev defaults with empty-string secret placeholders (`AZURE_OPENAI_API_KEY=""`) except `SUPERADMIN_PASSWORD=password` which is committed in plaintext; currently functions as an accidental template
- `.env.local` — exists on disk; already covered by `.gitignore`; holds personal overrides (`ENV`, `MODELS_ENV`, `ENABLE_USER_MANAGEMENT`, `IDP_PROVIDER`, `EMAIL_VERIFICATION_ENABLED`); however `config.py` does NOT load this file — support is gitignore-only, not runtime
- `.gitignore` — ignores `.env.local` and `.env_local` (lines 10–11) but does NOT ignore `.env`; no `.env.example` entry needed (example files should remain tracked)
- `docker-compose.yml` — `codemie` service uses a volume mount `- ./.env:/app/.env` (line 34); this bind-mounts the file rather than using the `env_file:` directive
- `src/codemie/service/codemie_export_service.py` — copies the `.env` file into a tar archive (lines 165 and 190); will raise an error or produce an incomplete export if `.env` is absent from disk
- `tests/conftest.py` — loads `tests/.env.test` via `load_dotenv(..., override=True)` at module level before any `Config()` instantiation; `tests/.env.test` is git-tracked and contains test-only flags (no real credentials)
- `scripts/license_headers/check_license_headers.py` — already excludes `.env`, `.env.example`, and `.env.template` from license header enforcement (lines 147–149); no change needed here
- `config/` (root) — holds YAML configuration for categories, budgets, and LLM routing; entirely separate from dotenv loading; unaffected

### Architecture and Layers Affected

- **Configuration layer** — `src/codemie/configs/config.py`: must be updated if `.env.local` loading is to be made functional; the redundant `load_dotenv` call may be cleaned up
- **Git/source-control layer** — `.gitignore`: requires one new entry (`.env`)
- **Container/deployment layer** — `docker-compose.yml`: the file-level volume mount `./.env:/app/.env` will silently create a directory rather than mount a file if `.env` does not exist when `docker compose up` is run; this is a local-developer experience risk
- **Service layer** — `src/codemie/service/codemie_export_service.py`: copies `.env` into exports; needs a conditional check or graceful fallback if `.env` is absent
- **Documentation layer** — `README.md`, `CONTRIBUTING.md`, `.ai-run/guides/development/setup-guide.md`, and scripts under `scripts/memory_analysis/`

### Integration Points

- `docker-compose.yml` volume mount `- ./.env:/app/.env` couples local developer `.env` to container startup — if `.env` is gitignored, first-run setup must create it before `docker compose up` or the mount will fail
- Production Helm/Kubernetes deployment (`deploy-templates/values.yaml`) supplies all secrets via `secretKeyRef` — no `.env` involvement; production is fully unaffected by this change
- `codemie_export_service.py` reads the local `.env` file from disk during export operations — this is an internal coupling to the presence of the file

### Patterns and Conventions

- `pydantic_settings.BaseSettings` with `SettingsConfigDict(env_file=...)` is the canonical config pattern; env vars override file values via pydantic-settings source precedence
- `Config(_env_file=None)` pattern is used in tests (`tests/enterprise/mcp_auth/test_feature_gating.py`) to suppress file loading for isolation — this pattern works correctly and will continue to work after `.env` is gitignored
- `find_dotenv(".env", raise_error_if_not_found=False)` is used in both load paths — the app will start cleanly with no `.env` on disk as long as required env vars are supplied another way (shell exports, docker `environment:` block, etc.)
- `tests/.env.test` is git-tracked (as a test fixture with only non-sensitive flags); this is a parallel but lower-risk pattern that is not in scope for this task

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/development/setup-guide.md` — references README for setup steps but contains no direct `.env` instructions; will need to be updated to describe the `cp .env.example .env` step
- `.ai-run/guides/development/configuration-patterns.md` — covers YAML config trees and `MODELS_ENV`; no `.env` file handling described; no update required but could reference the new workflow
- `.ai-run/guides/development/security-patterns.md` — states "Keep credentials in configuration and never log sensitive values"; references `README.md:47` as the source of env var documentation; will remain accurate after README is updated
- `.ai-run/guides/integration/request-hedging.md` — passing mention of `.env`-backed defaults; informational only; no update required

### Architectural Decisions

- No ADR files exist anywhere in the repo
- No recorded decision about env file strategy
- Two prior technical-analysis documents (`docs/superpowers/tasks/google-auth-oauthlib-adoption/technical-analysis.md:166` and `docs/superpowers/tasks/2026-07-08-keep-mcp-auth-callback-tab-open/technical-analysis.md:177`) independently flagged the absence of `.env.example` as a finding, establishing community awareness of this gap before this task
- `skills_architecture.md` lists `"*.env": "ask"` and `"*.env.*": "ask"` as permission rules for sensitive files — acknowledging env file sensitivity at the tool level while the repo simultaneously commits one; no code change required but this is a documented tension

### Derived Conventions

- Personal overrides belong in `.env.local` (gitignored) — this convention exists in `.gitignore` but is not wired in `config.py` and is not documented anywhere for contributors
- The committed `.env` currently carries the comment "Override any value in your personal .env (loaded after this file)" — this comment is inaccurate: `config.py` has no mechanism to load a second env file; the comment must be removed or the code must be updated to match it
- `docker-compose.yml` uses a volume mount (not `env_file:`), meaning `.env` must be a real file on disk before container startup — this is a local workflow constraint that must be documented

---

## 4. Testing Landscape

### Existing Coverage

- `tests/conftest.py` — loads `tests/.env.test` at module level; provides `ENABLE_USER_MANAGEMENT`, `IDP_PROVIDER`, `ENABLE_FILE_MULTIPROCESSING` to all tests
- `tests/codemie/configs/test_config.py` — tests `Config` field defaults and env-var overrides via `monkeypatch.setenv`; does not pass `_env_file` override
- `tests/codemie/configs/test_config_mcp_headers.py` — tests `FORWARDED_HEADERS_BLOCKLIST` loading via `@patch.dict(os.environ, {...})`
- `tests/enterprise/mcp_auth/test_feature_gating.py` — uses `Config(_env_file=None)` to suppress `.env` file loading for isolation; the most test-hygiene-aware pattern in the test suite
- `tests/codemie_tools/data_management/code_executor/` (multiple files) — heavy `patch.dict(os.environ, {...}, clear=True)` pattern for Pydantic settings models
- `tests/codemie/configs/test_budget_config.py`, `test_llm_config.py`, `test_customer_config.py` etc. — config model field and validation tests unrelated to env file loading

### Testing Framework and Patterns

- pytest ^8.3.x with pytest-env ^1.1.3 (installed 1.1.5)
- `pytest.ini [env]` section sets `ENV=local`, `REPOS_LOCAL_DIR`, `PG_URL` via pytest-env plugin before any test runs
- Dominant patterns: `monkeypatch.setenv`, `@patch.dict(os.environ, {...})`, `Config(_env_file=None)` for isolation
- `load_dotenv(path, override=True)` at conftest module level to seed env from `tests/.env.test`

### Coverage Gaps

- No test validates that `.env.example` exists, is parseable, or contains all required `Config` fields — a contract test would prevent future drift
- No test covers `codemie_export_service.py` behavior when `.env` is absent from disk (the state after gitignoring it without creating a local copy)
- No test covers `.env.local` override priority vs `.env` — relevant if `config.py` is updated to support loading `.env.local`
- No test covers the behavior of `Config()` when neither `.env` nor shell exports are present (the `raise_error_if_not_found=False` silent path)
- `tests/.env.test` has no assertion that it covers all mandatory `Config` fields

---

## 5. Configuration and Environment

### Environment Variables

Variables currently in the committed `.env` (names only, no values):

- Azure AI: `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_URL`, `AZURE_SPEECH_REGION`, `OPENAI_API_VERSION`
- Google Cloud: `GOOGLE_PROJECT_ID`, `GOOGLE_REGION`, `GOOGLE_VERTEXAI_REGION`, `GOOGLE_CLAUDE_VERTEXAI_REGION`
- VCS: `GITLAB_IDENTIFIERS`
- Feature flags: `CODEMIE_PRECOMMIT_ENABLED`, `LITELLM_PREMIUM_MODELS_ALIASES`
- Local auth/user management: `ENABLE_USER_MANAGEMENT`, `IDP_PROVIDER`, `SUPERADMIN_EMAIL`, `SUPERADMIN_PASSWORD`, `EMAIL_VERIFICATION_ENABLED`, `PASSWORD_MIN_LENGTH`, `RATE_LIMIT_LOGIN`

Variables referenced in docs/values.yaml but absent from the committed `.env`:
- `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `CALLBACK_API_BASE_URL` (noted in prior technical-analysis docs as needing `.env.example` entries)
- `MEMORY_PROFILING_ENABLED` (referenced in `scripts/memory_analysis/` docs)
- `ENV`, `MODELS_ENV` (present in `.env.local` only)

### Configuration Files

- `.env` — primary local dev config; currently git-tracked; must become gitignored
- `.env.local` — personal dev overrides; already gitignored; must be documented and optionally wired into `config.py`
- `src/codemie/configs/config.py` — Pydantic BaseSettings entrypoint; loads `.env` via two overlapping mechanisms
- `docker-compose.yml` — orchestrates local stack; mounts `.env` as a volume into the `codemie` container
- `deploy-templates/values.yaml` — Helm values for production; all secrets via Kubernetes `secretKeyRef`; unaffected
- `pytest.ini` — `[env]` section sets test-time env vars via pytest-env plugin

### Feature Flags and Deployment Concerns

- `CODEMIE_PRECOMMIT_ENABLED` — toggles pre-commit behavior; currently in committed `.env`
- `ENABLE_USER_MANAGEMENT`, `IDP_PROVIDER` — auth configuration flags; both in committed `.env` and in `.env.local`; the `.env.local` values would shadow `.env` values if `.env.local` loading were wired up
- `make gitleaks` target exists in `Makefile` as a secrets-scanning backstop — but it is a reactive control, not preventive
- Docker volume mount `- ./.env:/app/.env` will fail silently (create a directory) if `.env` does not exist when `docker compose up` runs — critical first-run concern
- Production (Helm/Kubernetes) is completely unaffected — no `.env` files used in any deployment manifests

---

## 6. Risk Indicators

- `SUPERADMIN_PASSWORD=password` is committed in plaintext in the current `.env`; must be replaced with a placeholder (e.g. `SUPERADMIN_PASSWORD=changeme`) in `.env.example` before the original `.env` is removed from git tracking — the secret has already been exposed in git history, which may warrant a `git filter-repo` scrub
- `src/codemie/service/codemie_export_service.py` copies `.env` from disk into a tar archive (lines 165 and 190); after `.env` is gitignored, any developer who has not yet created their local `.env` will get a broken or incomplete export; this code path has no test coverage for the missing-file case
- `docker-compose.yml` volume-mounts `.env` as a file (`- ./.env:/app/.env`); Docker creates a directory at the mount point if the source file is absent, causing the container to fail to read its configuration; new contributors following the `cp .env.example .env` step must complete it before `docker compose up`; the `Makefile` has no setup target that performs this copy — onboarding docs alone carry this risk
- `.env.local` is gitignored but `config.py` never loads it; the comment inside `.env` claiming `.env.local` is loaded after is inaccurate and will mislead contributors; the proposed task mentions "keep .env.local ignored for personal overrides if supported" — this requires a `config.py` code change to be functional, not just a documentation update
- Two load paths for `.env` coexist in `config.py` (pydantic `SettingsConfigDict` + bare `load_dotenv`); the redundant `load_dotenv` call should be removed as part of this task to avoid surprises when the file is absent
- No `.env.example` file exists anywhere in the repo; creating it is net-new work — variables must be audited from `Config(BaseSettings)` field definitions in `config.py` to ensure completeness; prior technical-analysis docs have already noted that `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `CALLBACK_API_BASE_URL`, and `MCP_AUTH_*` flags are also missing from the committed `.env` and should be in `.env.example`
- `README.md` has three separate locations that direct contributors to edit `.env` directly (lines 28, 49–57, 139); all three must be updated and the document is the primary onboarding surface — a missed update leaves the leak vector open
- `CONTRIBUTING.md` has zero env setup steps — contributors following only CONTRIBUTING.md would not know any env configuration is required; this is a documentation completeness gap independent of the security fix
- The separate `codemie-onboarding` repository referenced in the task is out of scope for this codebase analysis but is an execution dependency — the fix in this repo is incomplete without a corresponding update there
- `skills_architecture.md` marks `*.env` and `*.env.*` as `"ask"`-sensitivity files; this setting did not prevent `.env` from being committed; no technical change needed but the tension between this rule and the git-tracked `.env` should be documented in the post-fix state

---

## 7. Summary for Complexity Assessment

This task spans five distinct layers — git/source-control, configuration, container/deployment, service, and documentation — touching an estimated 8–12 files across the `codemie` repo plus the out-of-scope `codemie-onboarding` repo. The core mechanical changes (add `.env` to `.gitignore`, create `.env.example`, update README and CONTRIBUTING) are low-complexity. However, two non-obvious side effects elevate the overall change surface: (1) `codemie_export_service.py` copies `.env` from disk into export archives and will break silently when the file is absent for developers who have not yet run the setup step; (2) `docker-compose.yml` volume-mounts `.env` as a file rather than using `env_file:`, so the container will malfunction on first run if the file has not been created — and the Makefile has no target that automates the `cp .env.example .env` step.

The task also surfaces a latent gap: `.env.local` is gitignored but is not loaded by `config.py`. The task description says "keep .env.local ignored for personal overrides if supported" — this conditional phrasing is important because making `.env.local` functional requires a code change to `config.py` (adding a second `env_file` source or an explicit `load_dotenv` call for `.env.local` after `.env`). If the task scope includes wiring `.env.local` support, the redundant bare `load_dotenv` call in `config.py` should be cleaned up at the same time. If not, the misleading comment inside `.env` must be removed to avoid contributor confusion.

Test coverage for the affected area is sparse — no test covers `codemie_export_service.py`'s `.env`-copy path, no test validates `.env.example` completeness against `Config` field definitions, and no test exercises the absent-file branch of `config.py`'s `raise_error_if_not_found=False` loader. The `SUPERADMIN_PASSWORD=password` plaintext value in the committed `.env` may also require a git history scrub beyond the scope of a standard `.gitignore` addition. These factors collectively place this task in the medium-complexity range: the happy path is straightforward, but correct execution requires addressing the export service, the docker-compose mount, the config.py load-path cleanup, and three separate documentation surfaces.
