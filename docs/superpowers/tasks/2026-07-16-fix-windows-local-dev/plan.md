# Plan — Fix Windows Local Dev (EPMCDME-13549)

**Spec**: docs/superpowers/tasks/2026-07-16-fix-windows-local-dev/spec.md
**Branch**: EPMCDME-13549_fix-windows-local-dev
**Complexity**: M (17/36)

---

## Tasks

### T1 — docker-compose.yml: named volumes for Postgres and Elasticsearch

**File**: `docker-compose.yml`

Replace the local `.elasticsearch_data` bind-mount with a named volume and add a named volume for Postgres. Declare both in a top-level `volumes:` block so data persists across container removal.

```yaml
# postgres service
volumes:
  - postgres_data:/var/lib/postgresql/data

# elasticsearch service
volumes:
  - elasticsearch_data:/usr/share/elasticsearch/data  # was: .elasticsearch_data

# top-level
volumes:
  elasticsearch_data:
  postgres_data:
```

**Test-first**: no — Docker configuration; verified by running the stack and confirming data survives `docker compose down`.

---

### T2 — .gitattributes: enforce LF for shell scripts and Makefile

**File**: `.gitattributes` (new file at repo root)

```gitattributes
# Enforce LF to prevent CRLF corruption on Windows clone
*.sh text eol=lf
Makefile text eol=lf
```

Prevents "bad interpreter: No such file or directory" errors from CRLF corruption of `scripts/git-hooks/pre_commit.sh` on Windows clone with `core.autocrlf=true`.

**Test-first**: no — git configuration file.

---

### T3 — .env: add local startup defaults

**File**: `.env`

Pre-fill `AZURE_OPENAI_URL` and add `ENV` and `MODELS_ENV` so developers can start the backend with minimal manual editing:

```env
AZURE_OPENAI_URL="https://ai-proxy.lab.epam.com"

# For local startup
ENV=development
MODELS_ENV=azure
```

**Test-first**: no — configuration file.

---

### T4 — .gitignore: add analytics directory

**File**: `.gitignore`

Add `docs/codemie/analytics/` to prevent generated analytics output from being tracked.

**Test-first**: no.

---

### T5 — IdeToolArgument: fix Pydantic field name conflict

**Files**: `src/codemie/core/models.py`, `src/codemie/agents/tools/ide/ide_tool.py`

`IdeToolArgument.schema` shadows Pydantic's reserved `model_json_schema`. Rename the field to `nested_schema` with `alias="schema"` to preserve the JSON wire format:

```python
# core/models.py — before
schema: Optional["IdeToolArgsSchema"] = None

# after
nested_schema: Optional["IdeToolArgsSchema"] = Field(default=None, alias="schema")
```

Update all three `param.schema` references in `ide_tool.py` to `param.nested_schema`.

**Test-first**: no — the conflict surfaces as a Pydantic validation error at runtime; verified by confirming IDE tool calls with array/object parameters resolve correctly.

---

---

### T6 — docker-compose.yml: named volumes for file storage and repo cache

**File**: `docker-compose.yml`

`./codemie-storage` and `./codemie-repos` were bind mounts. On Windows Docker Desktop + WSL2, NTFS-backed bind mounts appear as `root`-owned `755` inside the container, causing `[Errno 13] Permission denied` for the `codemie` user (uid=1001) on every file upload. Replace with named volumes to let Docker manage ownership inside WSL2.

```yaml
# codemie service — before
- ./codemie-storage:/app/codemie-storage
- ./codemie-repos:/app/codemie-repos

# after
- codemie_storage:/app/codemie-storage
- codemie_repos:/app/codemie-repos

# top-level volumes block — add
volumes:
  codemie_storage:
  codemie_repos:
```

**Test-first**: no — Docker configuration; verified by running `make test-harness` and confirming file upload tests pass (17 failures → 0).

---

### T7 — Dockerfile: pre-create storage directories as codemie user

**File**: `Dockerfile`

When a named volume is mounted at a path that does not exist in the image, Docker creates the directory as `root`. Add `mkdir -p` after the `/app` COPY (while still `USER codemie`) so Docker copies the `codemie`-owned directory skeleton into empty volumes on first mount.

```dockerfile
# After: COPY --from=builder --chown=codemie:codemie /app /app
RUN mkdir -p /app/codemie-storage /app/codemie-repos
```

**Test-first**: no — build configuration; ownership verified via `docker exec codemie stat /app/codemie-storage`.

---

## Execution order

T1–T4 are independent. T5 is independent of all others. T6 and T7 are coupled (T7 must ship with T6 to ensure volume ownership).

| Task | Files |
|---|---|
| T1 — named Docker volumes (Postgres, Elasticsearch) | `docker-compose.yml` |
| T2 — .gitattributes | `.gitattributes` |
| T3 — .env local defaults | `.env` |
| T4 — .gitignore | `.gitignore` |
| T5 — IdeToolArgument field fix | `src/codemie/core/models.py`, `src/codemie/agents/tools/ide/ide_tool.py` |
| T6 — named Docker volumes (file storage, repos) | `docker-compose.yml` |
| T7 — Dockerfile: pre-create storage dirs | `Dockerfile` |
