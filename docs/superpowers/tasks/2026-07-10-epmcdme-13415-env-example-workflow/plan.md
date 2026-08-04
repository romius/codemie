# Plan: EPMCDME-13415 — Replace git-tracked .env with .env.example

## Requirements Summary

Replace the committed `.env` file with a `.env.example` template. Add `.env` to
`.gitignore` and untrack it. Wire `.env.local` as a second env-file source in
`config.py`. Fix `codemie_export_service.py` to handle an absent `.env`
gracefully. Update `README.md`, `CONTRIBUTING.md`, and the setup guide.

**Clarification answer**: Wire `.env.local` loading into `config.py`
(functional, not gitignore-only).

**Post-review scope correction**: `.env.example` must contain exactly the
17 vars that existed in the original tracked `.env` — no more, no fewer.
The `make setup-env` Makefile target was removed as redundant (a developer
can already run `cp -n .env.example .env`). An unrelated test-fix commit to
`tests/enterprise/litellm/test_llm_factory.py` was reverted as out of scope.

---

## Tasks

### T1 — Create `.env.example` (17 vars, matching the original `.env` 1:1)

Create `.env.example` at the repo root with exactly the 17 vars from the
original tracked `.env`: `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_URL`,
`OPENAI_API_VERSION`, `GOOGLE_PROJECT_ID`, `GOOGLE_REGION`,
`GOOGLE_VERTEXAI_REGION`, `GOOGLE_CLAUDE_VERTEXAI_REGION`,
`AZURE_SPEECH_REGION`, `GITLAB_IDENTIFIERS`, `CODEMIE_PRECOMMIT_ENABLED`,
`LITELLM_PREMIUM_MODELS_ALIASES`, `ENABLE_USER_MANAGEMENT`, `IDP_PROVIDER`,
`SUPERADMIN_EMAIL`, `SUPERADMIN_PASSWORD`, `EMAIL_VERIFICATION_ENABLED`,
`PASSWORD_MIN_LENGTH`, `RATE_LIMIT_LOGIN`.

Values match the original `.env` exactly, including `SUPERADMIN_PASSWORD=password`
(not a `changeme` placeholder — this mirrors what was already committed, so the
diff stays a pure format change rather than a value change). Quoting style is
unquoted throughout for consistency, since `GITLAB_IDENTIFIERS` and
`LITELLM_PREMIUM_MODELS_ALIASES` already contain embedded double quotes as
JSON-list syntax. Header comment documents `cp -n .env.example .env`.

Extra vars that have safe code defaults in `Config` (`ENV`, `MODELS_ENV`,
`GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`,
`CALLBACK_API_BASE_URL`, `MEMORY_PROFILING_ENABLED`, `MCP_AUTH_ENABLED`,
`MCP_AUTH_HMAC_SECRET`) are intentionally **not** templated in — they were
never required for local dev and templating them would let `.env.example`
drift from the original `.env`'s actual scope.

**Files**: `.env.example` (new)
**Test-first**: Yes — `tests/codemie/configs/test_env_example.py`: parses
`.env.example` and asserts its key set is in exact 1:1 correspondence with
`REQUIRED_KEYS` (both a missing-keys check and a no-extra-keys check).

---

### T2 — Add `.env` to `.gitignore` and untrack it

Add `.env` to `.gitignore` after the existing `.env.local` entry. Remove it
from the index without deleting the file from disk.

**Files**: `.gitignore`
**Test-first**: No — git-index change; no unit test applicable.

---

### T3 — Update `config.py`: wire `.env.local`, restore `os.environ` population

In `src/codemie/configs/config.py`:

- `SettingsConfigDict(env_file=(...))` loads `.env` then `.env.local` (as an
  override layer) into `Config`'s declared fields.
- Restored an explicit `load_dotenv()` call (`.env` then `.env.local`,
  `override=True` on the second) before `Config()` is instantiated. This is
  **not** redundant: pydantic-settings' `env_file` only populates `Config`'s
  own fields — it does not populate `os.environ`. Code that reads env vars
  directly via `os.environ.get(...)` instead of through `Config` (e.g.
  `OTEL_EXPORTER_OTLP_ENDPOINT` in `otel_config.py`, `LANGFUSE_PUBLIC_KEY`/
  `LANGFUSE_SECRET_KEY` in the langfuse `dependencies.py`) needs `os.environ`
  populated directly, or those reads silently see nothing.

**Files**: `src/codemie/configs/config.py`
**Test-first**: Yes — `tests/codemie/configs/test_config.py` covers `.env.local`
override precedence.

---

### T4 — Fix `codemie_export_service.py` for absent `.env`

In `src/codemie/service/codemie_export_service.py`: wrap the `.env` copy into
the tar archive with an explicit presence check. If the file is absent, skip
the copy and log a warning instead of silently no-op'ing or raising. This is a
direct consequence of untracking `.env` — previously the file was always
present so this path was unreachable.

**Files**: `src/codemie/service/codemie_export_service.py`
**Test-first**: Yes — `test_tar_logs_warning_when_env_absent` asserts the
export completes without raising and logs the expected warning.

---

### T5 — Document the copy workflow (no Makefile target)

No `setup-env` Makefile target — a developer can already run
`cp -n .env.example .env` directly, and adding a wrapper target for a
one-line command is redundant. `README.md`, `CONTRIBUTING.md`, and the setup
guide all document `cp -n .env.example .env` directly (`-n`/`--no-clobber` is
a no-op if `.env` already exists).

**Files**: `README.md`, `CONTRIBUTING.md`, `.ai-run/guides/development/setup-guide.md`
**Test-first**: No — documentation change.
