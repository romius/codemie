# Security

Entry point for remediating a reported vulnerability in this repository. Owns the remediation
order, the discovery commands, the exit-code traps, and the `/sanity` handoff.

Process policy — classify, route, fix, verify, deliver — is owned by the `secops` bundle
(`secops:security-remediate` and the per-class fix skills). This file records only what is
specific to this repository.

| When | Read |
|---|---|
| You need the gate commands and skip policy | [`../quality-gates.md`](../quality-gates.md) |
| You are changing a package | [`dependencies.md`](dependencies.md) |
| The finding is in the image, a stage, or an OS package | [`images.md`](images.md) |
| You need branch, commit, or MR mechanics | [`../standards/git-workflow.md`](../standards/git-workflow.md) |
| You are writing code — auth, input validation, secret handling | [`../development/security-patterns.md`](../development/security-patterns.md) |

## Regression run (`/sanity`)

No pipeline in this repository re-checks a merge request — [`../quality-gates.md`](../quality-gates.md)
§ After merge. The regression suite runs when a human posts a comment on the MR:

```
/sanity
```

Local gates passing is therefore not the end of a security change. The MR description must ask a
reviewer to post `/sanity`, because an automation cannot post it for itself, and the change is not
verified until that pipeline has run:

```markdown
## Verification needed
Local gates passed (see above). Please post `/sanity` on this MR to trigger the regression
pipeline — this repository has no automatic CI, so this is the only regression run.
```

## Discovery commands

Versions, gate lists and package sets are not written into these guides. Run the command.

| You need | Run |
|---|---|
| The gate commands this repo declares | `grep -nE '^[a-z-]+:' Makefile` |
| What full verification covers | `grep -n '^verify:' Makefile` |
| Which pins are load-bearing security fixes | `grep -n "Security (EPMCDME" pyproject.toml Dockerfile` |
| Which pins are marked temporary | `grep -n "TODO: Remove once" Dockerfile` |
| Every image and its Dockerfile | `git ls-files '*Dockerfile*'` |
| Every Python manifest | `git ls-files '*pyproject.toml' '*requirements*.txt' '*.lock'` |
| What a package currently resolves to | `poetry show <package>` |
| Whether a package is direct or transitive | `poetry show --tree \| grep -B3 <package>` |
| Whether anything re-checks after merge | `ls .gitlab-ci.yml .github/workflows 2>&1` — nothing does |
| What a comparable fix looked like | `git log --oneline --grep='CVE' -15`, then `git show <sha>` |

The last row is the one most often skipped. This repository has fixed many CVEs already, and the
pattern for a given case usually exists in its history.

## Remediation order

1. **Locate the surface the package lives on** — Python manifest, image layer, or Poetry's own
   bootstrap. Table: [`dependencies.md`](dependencies.md). Getting this wrong produces a diff that
   changes nothing in the scanned artifact.
2. **Match the fix location to the finding.** An OS package is fixed in the image layer; a Python
   package is fixed in `pyproject.toml`, not as an image-level override, which would leave the lock
   file vulnerable for every other consumer.
3. **Apply the minimal change**, with a `# Security (EPMCDME-...)` comment naming the ticket, the
   CVEs and a one-line reason.
4. **Run what the change requires** — table below.
5. **Commit and open the MR**, then ask for `/sanity` in the MR body.

## Verification after a fix

Scope verification to what changed. Command details and skip policy:
[`../quality-gates.md`](../quality-gates.md).

| What you changed | Run | Why |
|---|---|---|
| `pyproject.toml` / `poetry.lock` | `poetry check --lock`, then `make verify` | The lock must agree with the manifest before any later gate means anything |
| Python source as part of the fix | `make verify`, plus a test covering the changed path | |
| Anything reaching enterprise code paths | Install the GitLab clone ([procedure](dependencies.md#working-against-a-local-enterprise-checkout)), then `poetry run pytest tests/enterprise` | Without the package a large block of those tests fails for lack of it, not for the change |
| `Dockerfile` only | Rebuild and rescan — [`images.md`](images.md) | No gate in this repo looks inside the image |
| Anything at all | Ask for `/sanity` on the MR | The only regression run that exists |

Confirm what `make verify` covers with `grep -n '^verify:' Makefile` rather than trusting a list.

## Exit codes that mislead

| Command | What happens |
|---|---|
| `make gitleaks` | Runs gitleaks in Docker. Without a container runtime or registry access it fails on the environment — a check that did not run, not a clean scan. Fallback: a locally installed `gitleaks dir --no-banner .`. |
| `git commit` | The pre-commit hook runs ruff, the license check, the full test suite and `make sonar-local`. Sonar needs Node and a reachable SonarQube; when it is unavailable the commit is blocked by tooling, not by the change. `CODEMIE_PRECOMMIT_ENABLED=false` disables the hook — then run `make verify` explicitly. |
| Collection errors after pulling | Usually a missing dependency, not broken code. Run `make install` and collect again before diagnosing. |

A gate that could not run is unverified, not passed. Registry failure, stale scanner database,
unreachable Sonar — report it and stop.

## Secrets

`gitleaks` is part of `make verify`. Rotation-first handling — a committed credential is
compromised and is rotated by a human, and a secret finding is never auto-closed — is owned by
`secops:secret-fix`. Scrubbing the working tree without rotating leaves a live secret in the history
and wherever it was already used.
