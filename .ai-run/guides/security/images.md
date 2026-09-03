# Image: build and scan for verification

Index: [`README.md`](README.md). Owns the image, its stages, the enterprise build secret, and how OS
packages are pinned in it.

Rebuild-and-rescan as the verification predicate for a container CVE is owned by
`secops:container-cve-fix` and `secops:trivy-scan`. No gate in this repository looks inside the
image.

Scope is remediation. Running the project locally follows the onboarding repository —
<https://gitbud.epam.com/epm-cdme/codemie-onboarding#docker-compose-structure--usage>.

## Stages

Derive the stage list rather than trusting a summary:

```bash
grep -nE '^(FROM|ARG|USER)' Dockerfile
```

There is a `builder` stage and a `production` stage, both on
`codemie/codemie-base-python:${PYTHON_VERSION}-debian-{builder,runtime}` — not a plain upstream
`python:${PYTHON_VERSION}-slim` image. `builder` installs Poetry and the Python dependencies into
`/venv`; `production` installs only runtime OS packages, creates the unprivileged `codemie` user,
and copies `/venv` and `/app` across. Knowing which stage applies prevents fixing a package in the
layer that is thrown away.

### Base image source

`codemie/codemie-base-python` is built from a separate repository:
<https://gitbud.epam.com/epm-cdme/codemie-base-images/-/blob/main/python/PYTHON_VERSION/debian/Dockerfile>
— substitute this repo's `PYTHON_VERSION` (from the `ARG` in this `Dockerfile`) for the placeholder.

**A Debian OS package vulnerability (e.g. `libssl`, `glibc`, anything from `apt`) is not fixable
here.** Go fix it in `codemie-base-images` instead — that is the only repo where a Debian package
gets patched. Do not open an MR in this repo for a Debian CVE.

## Enterprise dependencies

`ARG INSTALL_ENTERPRISE` defaults to `true`, and on that path the build resolves
`codemie-enterprise` from a private Google Artifact Registry using a mounted `google_credentials`
secret.

Security work does not go through that secret. When a fix reaches `codemie_enterprise`, clone the
package from GitLab and install it editable — the procedure is in
[`dependencies.md`](dependencies.md#working-against-a-local-enterprise-checkout). Development, local
setup and running the stack follow the onboarding repository named above.

The secret gates one thing: producing the enterprise image. If a fix must be proven against that
image, hand the rebuild and rescan to whoever holds the secret. An image built without it omits the
private package, so scanning it proves nothing about the shipped one.

## OS package pins

Every `apt-get install` in this `Dockerfile` pins an exact version. Read the current pins rather
than copying a list:

```bash
grep -nE '^\s+[a-z0-9.+-]+=[0-9]' Dockerfile      # every exact pin
grep -n "only-upgrade" Dockerfile                  # the security upgrade block
```

The production stage carries a dedicated `--only-upgrade` block for packages patched ahead of the
base image, each with a `# Security (EPMCDME-...)` comment naming the CVEs. It also runs
`apt-get purge -y linux-libc-dev`.

Both comment conventions in that block — `# Security (<TICKET-ID>)` and `# TODO: Remove once ...` —
are owned by `secops:container-cve-fix`. When the base image catches up, delete the pin in its own
commit.

Bumping the base image itself is `ARG PYTHON_VERSION`. That changes the runtime for everything and
needs sign-off, not an edit inside a package fix.

## Runtime hardening

The production stage sets `chmod 555` on the system binary and library paths and on `$VIRTUAL_ENV`,
then switches to `USER codemie`. A fix that needs write access to any of those paths at run time is
reversing a deliberate control.

## Scanning

Rebuild, then scan the tag just built, with the scanner that produced the finding. Tag each
verification build distinctly — a stale local tag scans clean while the fix is untested.

Building the image needs the credentials described above. Without them this step belongs to someone
else: report it as not run rather than substituting a build that omits the private package.

Verify the named CVE is absent. A shorter findings list is not proof that the named one went away.

## Failure modes

- A network or credential failure is not a clean scan. A build that dies on registry access or on a
  missing `google_credentials` secret is unverified.
- The image carries far more than the service — TeX Live, Pandoc, Subversion, unixODBC and FreeTDS
  all ship in it, and none appear in `pyproject.toml`. Check
  [`dependencies.md`](dependencies.md) before editing.
- `linux-libc-dev` is purged on purpose. If a scanner reports it, confirm which stage was scanned.
