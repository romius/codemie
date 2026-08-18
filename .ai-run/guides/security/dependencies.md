# Dependency surfaces and version moves

Index: [`README.md`](README.md). Owns which surface a package lives on, the command that moves it,
and the local workflow for the enterprise package.

Lock-file discipline — minimal manifest edit, targeted relock, never a wholesale regeneration — is
owned by `secops:container-cve-fix`. A full re-resolution widens the diff past the package that was
meant to change, so the security fix cannot be separated from the churn around it.

## Which surface

```bash
git ls-files '*pyproject.toml' '*requirements*.txt' '*.lock'   # every Python manifest
```

There is one manifest pair — `pyproject.toml` and `poetry.lock`. Everything else that ships is
installed by the image.

| The package is installed | Surface | Pin style |
|---|---|---|
| Into the application's Python environment | `pyproject.toml` `[tool.poetry.dependencies]` | Range (caret) |
| Only with the `enterprise` extra | `pyproject.toml`, from the private source | Exact |
| By `apt-get` inside the image | `Dockerfile` | Exact `=version` — [`images.md`](images.md) |
| By Poetry's own bootstrap (`poetry self add`) | `Dockerfile`, builder stage | Exact |

The last two rows are the ones that get missed: a finding against TeX Live, Pandoc, OpenSSL,
Subversion or unixODBC has no manifest line to edit.

## Python package

```bash
poetry update <package>                 # direct dependency already in pyproject.toml
poetry add '<package>@^<fixed-version>' # transitive: add the constraint that forces the resolver
poetry show <package>                   # confirm what was resolved
poetry check --lock                     # confirm the lock still matches the manifest
```

`poetry check --lock` prints deprecation warnings about `[tool.poetry.extras]`,
`[tool.poetry.scripts]` and `[tool.poetry.dependencies]`. Those are warnings, not failures, and not
part of a security change.

Commit `pyproject.toml` and `poetry.lock` together, or the repository has a state where the lock
disagrees with the manifest.

Never hand-edit `poetry.lock`. A package absent from `pyproject.toml` is transitive, and the fix is
a constraint in the manifest that forces the resolver.

Every security constraint carries the mandatory `# Security (<TICKET-ID>)` comment, whose shape is
owned by `secops:container-cve-fix` (Pattern 6). Existing examples in this manifest:

```bash
grep -n "Security (EPMCDME" pyproject.toml
```

## Licences

`pyproject.toml` carries a `[tool.pip-licenses]` section with an explicit allow list and named
exceptions. A dependency whose licence falls outside it is not admissible, whatever its version.

Adding a brand-new package is not a bump: it needs review on licence, maintenance and transitive
footprint.

## The enterprise package

```bash
awk '/^\[\[tool.poetry.source\]\]/,/^$/' pyproject.toml
grep -n "codemie-enterprise\|^enterprise = " pyproject.toml
```

`codemie-enterprise` is an optional, exactly-pinned dependency served from a private Google
Artifact Registry source whose `priority = "explicit"`, which is why the install targets differ:

| Target | Installs |
|---|---|
| `make install` | Main plus dev, no extras |
| `make install-oss` | Same, and `--sync` prunes anything not in the lock |
| `make install-enterprise` | Adds `-E enterprise` — resolves from the registry, so it needs GCP credentials |

`make install-enterprise` is how the project is normally installed with the extra. Security and CVE
work uses the GitLab clone instead, whether or not credentials are held — see
[Working against a local enterprise checkout](#working-against-a-local-enterprise-checkout).

`codemie_enterprise` is imported lazily, so the test suite collects and runs without it.

## Working against a local enterprise checkout

This is the required procedure for security work: a fix whose change reaches `codemie_enterprise` is
validated against the GitLab clone, regardless of whether GCP Artifact Registry credentials are
held. The clone is the same package, and it is what makes the fix testable.

Every other case follows the onboarding repository instead —
<https://gitbud.epam.com/epm-cdme/codemie-onboarding#docker-compose-structure--usage>. Local only:
`main` must always resolve `codemie-enterprise` from the registry source.

```bash
# 1. Clone once, as a sibling of this repository
git clone https://gitbud.epam.com/epm-cdme/codemie-enterprise ../codemie-enterprise

# 2. Point the current virtualenv at it — editable, so edits there take effect immediately
poetry run pip install -e ../codemie-enterprise

# 3. Work and test as normal
poetry run pytest tests/enterprise -q

# 4. Undo when finished
poetry run pip uninstall -y codemie-enterprise
```

Without the package, a large block of `tests/enterprise` fails purely for want of the import. Run
the suite before the editable install as a baseline, so those failures are not read as a regression.

`git status` stays clean throughout: this touches only the virtualenv, and step 4 removes the
package again. Redirecting Poetry instead, by rewriting the dependency to a `path =` entry, edits
`pyproject.toml` and rewrites `poetry.lock`, so the redirect becomes a tracked change that reaches
an MR the first time someone forgets to revert it.

If the manifest form is used anyway, reverting it is part of the same task: restore both
`pyproject.toml` and `poetry.lock` from `main` and confirm with `git diff` before committing.

The clone and the registry are not the same code. `pyproject.toml` pins an exact registry version;
the clone's `main` moves ahead of it:

```bash
grep -n 'codemie-enterprise = ' pyproject.toml          # the pinned version
grep -n '^version' ../codemie-enterprise/pyproject.toml # what the clone is
```

A fix validated only against the clone is not validated against what ships. Reproduce it against the
pinned version before calling it done.
