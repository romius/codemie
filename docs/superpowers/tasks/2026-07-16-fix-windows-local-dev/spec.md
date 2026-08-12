# Fix Windows Local Dev — Spec

**Date**: 2026-07-16
**Status**: Maintenance (post-delivery)
**Ticket**: EPMCDME-13549
**Slug**: fix-windows-local-dev
**Complexity**: M (17/36)

---

## Context

- `docker-compose.yml` used a local bind-mount (`.elasticsearch_data/`) for Elasticsearch data; Postgres had no volume at all. Data was lost on container removal. Fixed with named Docker volumes.
- `.gitattributes` was absent. Shell scripts checked out with `core.autocrlf=true` (the Git for Windows default) get CRLF line endings, breaking the shebang in `scripts/git-hooks/pre_commit.sh`. Fixed by enforcing LF for `*.sh` and `Makefile`.
- `.env` lacked local startup defaults, requiring developers to know which env vars to set manually. Added `ENV=development`, `MODELS_ENV=azure`, and pre-filled `AZURE_OPENAI_URL` with the EPAM AI proxy.
- `IdeToolArgument` had a field named `schema`, which shadows Pydantic's reserved `model_json_schema` internals and causes a field-name conflict. Renamed to `nested_schema` with `alias="schema"` to preserve the JSON wire format.
- `docker-compose.yml` used bind mounts (`./codemie-storage`, `./codemie-repos`) for file storage and repo cache. On Windows Docker Desktop + WSL2, NTFS-backed bind mounts appear as `root`-owned `755` inside the container; the `codemie` user (uid=1001) cannot create subdirectories, causing `[Errno 13] Permission denied` on every file upload. Converted to named Docker volumes (`codemie_storage`, `codemie_repos`) managed inside WSL2, which initialise with correct ownership. The `Dockerfile` now pre-creates both directories as the `codemie` user so Docker copies the ownership into empty volumes on first mount.

---

## Story

**As a** developer setting up the CodeMie backend locally,
**I want** a docker-compose that persists data, a .env with sensible defaults, and shell scripts that work on Windows clone,
**so that** I can start the stack without data loss or manual config hunting.

---

## Acceptance Criteria

- [x] Given `docker compose up --build codemie postgres elasticsearch`, Postgres and Elasticsearch data persists in named Docker volumes across container restarts and removals.
- [x] Given a fresh Windows clone with `core.autocrlf=true`, `scripts/git-hooks/pre_commit.sh` runs without a "bad interpreter: No such file or directory" error (`.gitattributes` enforces LF for `*.sh` and `Makefile`).
- [x] Given a fresh clone, `.env` already contains `ENV=development`, `MODELS_ENV=azure`, and `AZURE_OPENAI_URL` pre-filled so minimal manual editing is needed to start the backend.
- [x] Given `IdeToolArgument` with an array or object `type`, the Pydantic model resolves correctly without a field-name conflict (`schema` → `nested_schema` with `alias="schema"`).
- [x] Given a fresh Windows clone with `docker compose up --build`, file upload endpoints (`POST /v1/files/`, `POST /v1/files/bulk`) succeed without `[Errno 13] Permission denied` errors (`codemie-storage` and `codemie-repos` are named Docker volumes, not Windows bind mounts).

---

## Out of Scope

- README restructure or workflow documentation changes.
- `setup-guide.md` or `AGENTS.md` changes.
- Makefile changes (gitleaks target, migrate target).
- `pyproject.toml` dependency updates.
- Windows-specific PowerShell command alternatives.
- `INSTALL_ENTERPRISE=false` as a docker-compose default.
- Super admin bootstrapping in docker-compose.
- SharePoint / Google OAuth lazy-redis application code fixes.

---

## Implementation Notes

- **Named Docker volumes**: `postgres_data:/var/lib/postgresql/data` added to the `postgres` service; `.elasticsearch_data` bind-mount replaced with `elasticsearch_data:/usr/share/elasticsearch/data` named volume. Both declared in a top-level `volumes:` block.
- **`.gitattributes`**: new file at repo root — `*.sh text eol=lf` and `Makefile text eol=lf`.
- **`.gitignore`**: `docs/codemie/analytics/` line added.
- **`.env`**: `AZURE_OPENAI_URL` set to `https://ai-proxy.lab.epam.com`; `ENV=development` and `MODELS_ENV=azure` added under a `# For local startup` comment.
- **`IdeToolArgument.schema` → `nested_schema`**: `src/codemie/core/models.py` — field renamed with `Field(default=None, alias="schema")` to preserve the JSON alias while avoiding the Pydantic reserved-name conflict. `src/codemie/agents/tools/ide/ide_tool.py` — all three `param.schema` references updated to `param.nested_schema`.
- **Named volumes for file storage**: `docker-compose.yml` — `./codemie-storage:/app/codemie-storage` and `./codemie-repos:/app/codemie-repos` bind mounts replaced with named volumes `codemie_storage:/app/codemie-storage` and `codemie_repos:/app/codemie-repos`; both declared in the top-level `volumes:` block. `Dockerfile` — `RUN mkdir -p /app/codemie-storage /app/codemie-repos` added (as `USER codemie`, after `COPY /app`) so Docker propagates `codemie:codemie` ownership into empty volumes on first mount.
