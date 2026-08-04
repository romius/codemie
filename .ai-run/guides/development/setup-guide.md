# Setup Guide

## Environment File Setup

`.env` is gitignored. Copy the template before running anything:

```bash
cp -n .env.example .env   # safe to run repeatedly; no-op if .env already exists
```

Then fill in required values (API keys, database URL, etc.).

Use `.env.local` for personal overrides (machine-specific paths, local flag
toggles). It is gitignored and loaded **after** `.env`, so its values take
precedence. Neither file should ever be committed.

| Avoid | Prefer |
|---|---|
| Editing `.env.example` directly | Copy to `.env`, edit that copy |
| Committing `.env` or `.env.local` | Both are gitignored by design |
| Putting real credentials in any tracked file | Use `.env` / `.env.local` on disk only |

Evidence: `.env.example` at repo root.

## Local Dependencies

Install with Poetry and use Docker Compose for dependent services.

| Avoid | Prefer |
|---|---|
| Running Python commands before dependencies are installed | `poetry install` or `poetry install --sync` |
| Starting the API without PostgreSQL/Elasticsearch when needed | Start required services with Docker Compose |

## Running The API

Use the Makefile or README command depending on context.

| Avoid | Prefer |
|---|---|
| Inventing a new app entrypoint | Use `codemie.rest_api.main:app` |
| Changing ports without noting it | Default to port 8080 unless the user asks otherwise |

Evidence: Makefile run target starts Uvicorn at `Makefile:66`; README startup uses the same app at `README.md:96`.

## Claude Code Stop Hook

The Stop hook runs `make ruff` in a non-login subshell. PATH entries in
`.bashrc` (Linux) or `.zshrc` (macOS) are only loaded in interactive shells —
the Stop hook's non-login subshell does not source them.

To verify your setup:

```bash
env -i PATH="$PATH" poetry --version
```

If that fails, add poetry's install directory to your login profile.

> **WSL users:** To confirm the hook runs inside WSL (rather than the Windows
> shell), run `echo $WSL_DISTRO_NAME` from the terminal where you run Claude
> Code. A non-empty result means WSL is active — follow the Linux row below.

| Platform | File | Example entry |
|---|---|---|
| Linux | `~/.profile` | `export PATH="$HOME/.local/bin:$PATH"` |
| WSL | `~/.profile` inside the WSL distro | `export PATH="$HOME/.local/bin:$PATH"` |
| macOS (most reliable — all shells and launch paths) | `/etc/paths.d/poetry` | Create the file with one line: `/Users/your-username/.local/bin` — `$HOME` does not expand here, path must be absolute |
| macOS (user-scoped fallback, zsh) | `~/.zprofile` | `export PATH="$HOME/.local/bin:$PATH"` |
| macOS (user-scoped fallback, bash) | `~/.bash_profile` | `export PATH="$HOME/.local/bin:$PATH"` |
| Windows (native) | System → Advanced system settings → Environment Variables | Add the poetry `Scripts` directory to the user `Path` — typically `%APPDATA%\Python\Scripts`, or the path printed by the installer |

After editing, open a new login shell (or restart your terminal application
for `/etc/paths.d/` changes on macOS), then re-run the diagnostic to confirm.
