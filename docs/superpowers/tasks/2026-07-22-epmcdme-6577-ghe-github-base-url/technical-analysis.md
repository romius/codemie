# Technical Research

**Task**: github integration GHE enterprise base_url
**Generated**: 2026-07-22T00:00:00Z
**Research path**: codegraph

---

## 1. Original Context

EPMCDME-6577 — GitHub integrations can't work with GHE, always default to api.github.com.

Bug: When configuring GitHub Enterprise (GHE) integration, the system defaults to api.github.com instead of the provided GHE domain.

The fix hint from the ticket description:
For GHE, the GitHub instance should be created in the code located at `codemie_tools/git/github/custom_github_api_wrapper.py:33`, with `base_url=https://{ghe_hostname}/api/v3/`. This approach is verified to work as intended but is not applied in the current implementation.

Acceptance criteria:
1. Users can input their GHE API URL during integration configuration.
2. The system utilizes the specified API URL instead of defaulting to `api.github.com`.
3. Successful connectivity to GHE is achieved when valid credentials and configurations are provided.
4. The fix is applied to `codemie_tools/git/github/custom_github_api_wrapper.py:33`, where the GitHub instance should be created with `base_url=https://{ghe_hostname}/api/v3/`.
5. The fix is tested to ensure no regressions for users using `api.github.com`.

Comment on the ticket: "There is a workaround to tell in system message what domain must be used" — this is a user-side workaround, not relevant to the fix.

---

## 2. Codebase Findings

### Existing Implementations

The bug stems from a consistent omission across five files: every `Github()` and `GithubIntegration()` constructor call ignores `base_url`, so all API traffic goes to `api.github.com` regardless of what repo URL the user configured.

**Primary wrapper (the ticket's target file):**

- `/Users/oleg_sotnichenko/codemie-dev/codemie/src/codemie_tools/git/github/custom_github_api_wrapper.py`
  — `CustomGitHubAPIWrapper` extends LangChain's `GitHubAPIWrapper`. Has four Pydantic fields (`github_access_token`, `github_app_id`, `github_app_private_key`, `github_app_installation_id`). No `github_base_url` field exists.
  — `_get_installation_id` (lines 33–62): constructs `GithubIntegration(integration_id=app_id, private_key=private_key)` — no `base_url`.
  — `_create_github_app_auth` (lines 64–92): constructs `GithubIntegration(integration_id=app_id, private_key=private_key)` then `Github(auth=auth)` — no `base_url` on either.
  — `_create_pat_auth` (lines 94–109): constructs `Github(auth=auth)` — no `base_url`.
  — `validate_environment` (lines 147–175): routes to one of the two auth methods; reads no host/base-URL value from the environment or the settings dict.

**Factory functions that instantiate the wrapper:**

- `/Users/oleg_sotnichenko/codemie-dev/codemie/src/codemie_tools/git/utils.py` — `init_github_api_wrapper` (lines 156–192)
  — Calls `split_git_url(git_creds.repo_link)` which returns `(base_url, repo_path)`. For a GHE repo like `https://ghe.company.com/org/repo.git`, `base_url = "https://ghe.company.com"` is available here but **discarded** — only `repo_name` is forwarded to the wrapper. Handles both PAT and GitHub App auth.

- `/Users/oleg_sotnichenko/codemie-dev/codemie/src/codemie/service/git_api/git_api_service.py` — `init_github_api_wrapper` (lines 62–78)
  — Older, PAT-only factory. Same pattern: `split_git_url` is called, `base_url` returned from it is discarded.

**Additional affected call sites (found by context7 research):**

- `/Users/oleg_sotnichenko/codemie-dev/codemie/src/codemie_tools/git/github/github_client.py` (lines 101–103)
  — Constructs `GithubIntegration(integration_id=self.config.app_id, private_key=self.config.private_key)` without `base_url`.

- `/Users/oleg_sotnichenko/codemie-dev/codemie/src/codemie_tools/git/git_auth_utils.py` (line 44)
  — Constructs `GithubIntegration(integration_id=app_id, private_key=private_key)` without `base_url`.

**Credentials model:**

- `/Users/oleg_sotnichenko/codemie-dev/codemie/src/codemie_tools/git/utils.py` — `GitCredentials` (lines 52–130)
  — Pydantic model with fields: `auth_type`, `token`, `token_name`, `app_id`, `private_key`, `installation_id`, `repo_link`, `base_branch`, `repo_type`. **No `base_url`, `hostname`, or `ghe_url` field.** The hostname is embedded in `repo_link` and is already extractable via `split_git_url` — no model change is needed.

**GitLab pattern (the model to mirror):**

- `/Users/oleg_sotnichenko/codemie-dev/codemie/src/codemie_tools/git/utils.py` — `init_gitlab_api_wrapper` (lines 133–153)
  ```python
  def init_gitlab_api_wrapper(git_creds: GitCredentials) -> Optional[CustomGitLabAPIWrapper]:
      base_url, repo_name = split_git_url(git_creds.repo_link)
      return CustomGitLabAPIWrapper(
          gitlab_base_url=base_url,   # base_url IS passed through
          gitlab_repository=repo_name.replace(".git", "").replace("/", "", 1),
          ...
      )
  ```
  `CustomGitLabAPIWrapper` has a `gitlab_base_url: str` field that is read and forwarded to the GitLab client. This exact pattern must be applied to the GitHub wrapper.

**Settings and integration model layer:**

- `/Users/oleg_sotnichenko/codemie-dev/codemie/src/codemie/rest_api/models/settings.py` — `Settings` ORM model with `credential_values` stored as PostgreSQL JSONB `[{key, value}]` pairs. The `Credentials` value object is also here. GitHub credentials ride the generic `CredentialTypes.GIT` type — no dedicated `GITHUB` credential type exists.

- `/Users/oleg_sotnichenko/codemie-dev/codemie/src/codemie/service/settings/settings_tester.py` — `SettingsTester` dispatch table. GitHub goes through `_test_git` (via `CredentialTypes.GIT`) which uses `GitToolkit.git_integration_healthcheck`. No GitHub-specific healthcheck handler.

### Architecture and Layers Affected

| Layer | Component | Change needed |
|---|---|---|
| Tool/wrapper | `CustomGitHubAPIWrapper` | Add `github_base_url` field; thread into 3 static auth methods |
| Tool/wrapper | `_create_pat_auth` | Pass `base_url` to `Github()` |
| Tool/wrapper | `_create_github_app_auth` | Pass `base_url` to `GithubIntegration()` and `Github()` |
| Tool/wrapper | `_get_installation_id` | Pass `base_url` to `GithubIntegration()` |
| Tool/wrapper | `validate_environment` | Read `github_base_url` from `values`, thread into auth calls |
| Factory / Service | `init_github_api_wrapper` in `utils.py` | Stop discarding `base_url` from `split_git_url`; pass as `github_base_url` |
| Factory / Service | `init_github_api_wrapper` in `git_api_service.py` | Same fix; currently PAT-only, also discards `base_url` |
| Tool | `github_client.py` | Pass `base_url` to `GithubIntegration()` |
| Tool | `git_auth_utils.py` | Pass `base_url` to `GithubIntegration()` |

No changes required to the ORM model (`Settings`), `GitCredentials`, `SettingsTester`, or the REST API layer — the hostname is derivable from the existing `repo_link` field at the factory layer.

### Integration Points

- **Internal**: `init_github_api_wrapper` in `utils.py` is the primary entry point; called by all GitHub tool operations including branches, PRs, issues. `git_api_service.py` is a secondary/older path used in the service layer.
- **External**: PyGithub (`github` package) — `Github()` and `GithubIntegration()` constructors. Both accept `base_url`; both must receive it for GHE.
- **LangChain**: `GitHubAPIWrapper` (base class of `CustomGitHubAPIWrapper`) has `model_config = ConfigDict(extra="forbid")` — this means new fields cannot be added via Pydantic `extra`. They must be declared explicitly as class-level fields in the subclass. This is confirmed by the existing four fields in `CustomGitHubAPIWrapper`.

### Patterns and Conventions

- **GitLab mirror pattern**: `CustomGitLabAPIWrapper` has `gitlab_base_url: str`; `init_gitlab_api_wrapper` extracts `base_url` from `split_git_url` and passes it. Test for this pattern asserts `result['gitlab_base_url'] == 'https://example.gitlab.com'`. The GitHub fix must follow this exactly.
- **Pydantic `model_validator(mode="before")`**: used in the wrapper to inject environment variables into the model dict before validation.
- **`get_from_dict_or_env`**: LangChain utility used in `validate_environment` to read values from the incoming dict or fall back to environment variables. `github_base_url` should be read the same way: `get_from_dict_or_env(values, "github_base_url", "GITHUB_BASE_URL", default=None)`.
- **`split_git_url`**: utility that returns `(base_url, repo_path)` from a full repo URL. For `https://ghe.company.com/org/repo.git` it returns `("https://ghe.company.com", "/org/repo.git")`. Already used at both factory sites — just the return value is discarded.
- **Auth strategy**: `validate_environment` checks for `github_app_id` to choose App auth vs PAT auth. Both paths need `base_url` threaded through.
- **`base_url` derivation**: The host extracted from `split_git_url` is `"https://ghe.company.com"` — it does NOT include `/api/v3`. The `/api/v3` suffix must be appended to form the correct PyGithub `base_url`. The safest place to do this is inside the wrapper itself, not at the call sites, to avoid spreading the `/api/v3` logic.

---

## 3. Documentation Findings

### Guides and Architecture Docs

- `.ai-run/guides/` was checked by the research agents; no guide files covering the GitHub integration domain were surfaced.
- No conventions derived from guides — all conventions are derived from code patterns.

### Architectural Decisions

- No ADRs or inline `DECISION:`/`ADR:` markers found in the GitHub integration files.
- The implicit decision to use `CredentialTypes.GIT` for GitHub (no dedicated `GITHUB` type) means the settings healthcheck goes through the generic git path, not a GitHub-specific one. This is relevant context but does not need to change for this fix.

### Derived Conventions

- New fields on `CustomGitHubAPIWrapper` are added as `Optional[str] = None` Pydantic class attributes alongside the existing four fields.
- Factory functions in `utils.py` follow the pattern: extract from `GitCredentials`, build `wrapper_args` dict, call wrapper constructor with `**wrapper_args`.
- Tests patch `github.Github` and `github.GithubIntegration` at the module level and assert on the arguments passed to their constructors — the GHE regression test should follow this exact pattern.

---

## 4. Testing Landscape

### Existing Coverage

- `/Users/oleg_sotnichenko/codemie-dev/codemie/tests/codemie_tools/git/test_custom_git_api_wrapper.py`
  — `TestGitHubApiWrapper`: covers `validate_environment` success for PAT auth. Patches `github.Github`. No GHE/base_url coverage.
  — `TestGitLabApiWrapper`: covers `validate_environment` for GitLab with `gitlab_base_url=` asserted. This is the test template to follow.

- `/Users/oleg_sotnichenko/codemie-dev/codemie/tests/codemie_tools/git/test_github_app_auth.py`
  — `test_init_github_api_wrapper_with_pat`: patches `CustomGitHubAPIWrapper`, asserts `github_access_token`, `github_base_branch`, `github_repository` in `call_kwargs`. No `github_base_url` assertion.
  — `test_init_github_api_wrapper_with_github_app`: asserts `github_app_id`, `github_app_private_key`, `github_app_installation_id`. No `github_base_url` assertion.
  — `test_custom_github_api_wrapper_pat_auth`: patches `github.Auth`, `github.Github`, asserts `Auth.Token` called with token. Does NOT assert `base_url` on `Github()`.
  — `test_custom_github_api_wrapper_github_app_auth`: patches `github.Auth`, `github.Github`, `github.GithubIntegration`, asserts App auth flow. Does NOT assert `base_url` on `GithubIntegration()` or `Github()`.

### Testing Framework and Patterns

- pytest with `unittest.mock.patch` decorators.
- `MagicMock` used for `github.Github`, `github.GithubIntegration`, `github.Auth`.
- Assertions use `mock.assert_called_once_with(...)` and `call_kwargs["field_name"]` dictionary access on the mock's `call_args.kwargs`.
- Fixtures: `GitCredentials` constructed inline in each test function, not as shared fixtures.

### Coverage Gaps

- No test verifies that `Github()` receives `base_url` for a GHE repo URL (PAT path).
- No test verifies that `GithubIntegration()` receives `base_url` for a GHE repo URL (App auth path).
- No test verifies that `init_github_api_wrapper` (in `utils.py`) passes `github_base_url` when `repo_link` has a non-`github.com` hostname.
- No test verifies backward compatibility: `api.github.com` repos should not have `base_url` injected (or should receive `None` / the default).
- `github_client.py` and `git_auth_utils.py` have no test coverage for GHE.

---

## 5. Configuration and Environment

### Environment Variables

- `GITHUB_ACCESS_TOKEN` — PAT for GitHub auth; read via `get_from_dict_or_env` in `_create_pat_auth`.
- `GITHUB_BASE_BRANCH` — default base branch; already a field on the wrapper.
- `ACTIVE_BRANCH` — active branch override.
- `GITHUB_BASE_URL` (does not yet exist) — would be the new env var for the GHE API URL, read analogously to `GITHUB_ACCESS_TOKEN`. Must be added alongside the new field.

### Configuration Files

- `credential_values` JSONB field in the `settings` PostgreSQL table — stores integration secrets as `[{key, value}]` pairs. A new key (e.g. `github_base_url`) could be added here by the integration configuration flow, but no schema migration is needed since it is a schemaless JSONB field.

### Feature Flags and Deployment Concerns

- No feature flags found for the GitHub integration domain.
- The fix must be fully backward-compatible: when `github_base_url` is `None` (existing `api.github.com` users), PyGithub uses its default `'https://api.github.com'` — no behavior change for them.

---

## 6. Risk Indicators

- **Four methods in `custom_github_api_wrapper.py` need changing** — `_get_installation_id`, `_create_github_app_auth`, `_create_pat_auth`, and `validate_environment`. Missing any one of them will leave GHE partially broken (e.g. App auth list-installations call still hitting `api.github.com`).
- **Two additional files beyond the ticket's target** — `github_client.py` and `git_auth_utils.py` also construct `GithubIntegration` without `base_url`. The ticket points only to `custom_github_api_wrapper.py:33`; these files may be overlooked.
- **`/api/v3` suffix derivation** — `split_git_url` returns the bare hostname (e.g. `"https://ghe.company.com"`), not the full API path. The code must append `/api/v3` before passing to PyGithub. If this transformation is done inconsistently across call sites, it will produce malformed URLs.
- **LangChain base class `extra="forbid"`** — `GitHubAPIWrapper` uses `ConfigDict(extra="forbid")`, which means Pydantic will reject any unknown fields. The new `github_base_url` field must be declared explicitly as a class attribute in `CustomGitHubAPIWrapper`, not relied upon via Pydantic extras. This is already how the existing four fields work — the risk is a developer forgetting this constraint and expecting extras to work.
- **No existing GHE tests** — adding `github_base_url` without a regression test for `api.github.com` users (where `base_url` should be `None`) could silently break `github.com` integrations if the default-value handling has a bug.
- **`GithubIntegration` also needs `base_url`** — the ticket mentions only the `Github()` constructor at line 33; `GithubIntegration` is called earlier in the same flow (lines 55 and 82) and must also receive `base_url` for GHE App auth to work end-to-end. Fixing only `Github()` would leave App auth broken on GHE.
- **`git_api_service.py` older PAT-only path** — this path has no App auth and only handles PAT. It may serve a different call site (direct service layer calls vs. toolkit calls). Both paths must be fixed consistently.
- **No `CredentialTypes.GITHUB`** — GitHub uses `CredentialTypes.GIT`, so `SettingsTester` runs the generic git healthcheck. If the integration test verifies connectivity to the GHE endpoint, it may still fail silently because the healthcheck hits `api.github.com`. This is a separate issue but worth noting as related scope.

---

## 7. External Documentation Findings

### PyGithub — `Github()` constructor `base_url` parameter

**Source**: https://pygithub.readthedocs.io/en/latest/github.html and https://pygithub.readthedocs.io/en/stable/introduction.html

**Exact format** (no trailing slash, `/api/v3` included):
```python
g = Github(auth=auth, base_url="https://{hostname}/api/v3")
```

**Default value**: `'https://api.github.com'` (defined in `github/Consts.py` as `DEFAULT_BASE_URL`).

**Trailing slash**: MUST NOT be included. PyGithub internally does `url = f"{self.__prefix}{url}"` where endpoint paths start with `/`. A trailing slash on the prefix produces double slashes (e.g. `https://ghe.company.com/api/v3//repos/...`), which causes 404s or routing failures.

**`/api/v3` in `base_url`**: YES — required for GHE Server. GitHub.com uses `https://api.github.com` (no path component); GHE Server uses `https://{hostname}/api/v3`. These are structurally different and cannot be derived uniformly without knowing the server type.

**Canonical GHE example from PyGithub docs** (verbatim):
```python
# Github Enterprise with custom hostname
g = Github(auth=auth, base_url="https://{hostname}/api/v3")
```

### PyGithub — `GithubIntegration()` constructor `base_url` parameter

**Source**: https://pygithub.readthedocs.io/en/latest/github_integration.html

`GithubIntegration` accepts `base_url` with the same default (`'https://api.github.com'`) and the same format requirement. For GHE App auth, **both** the `GithubIntegration` instance and the derived `Github` instance must receive the same `base_url`:

```python
auth = github.Auth.AppAuth(app_id, private_key)
gi = github.GithubIntegration(auth=auth, base_url="https://{hostname}/api/v3")
installation = gi.get_installation(owner, repo)
g = gi.get_github_for_installation(installation.id)
```

Note: PyGithub is moving toward `auth=Auth.AppAuth(...)` style (vs. deprecated positional `integration_id`, `private_key`). The current codebase uses the older positional style. Both work, but the `base_url` parameter location is the same regardless.

### GitHub Enterprise Server REST API — official URL format

**Source**: https://docs.github.com/en/enterprise-server@3.12/rest/overview/resources-in-the-rest-api

Official quote: "The full path is a URL that includes the base URL for the GitHub REST API (`http(s)://HOSTNAME/api/v3`) and the path of the endpoint."

- **Correct format**: `https://HOSTNAME/api/v3` — no trailing slash on the base URL.
- **GHE Server vs GHE Cloud**: GHE Server uses `https://{hostname}/api/v3`; GitHub Enterprise Cloud (hosted by GitHub, not self-hosted) uses `https://api.github.com`. The fix must target GHE Server only — GHE Cloud users already work.
- Protocol (`http` vs `https`) depends on the instance's TLS configuration; `https` is the standard and should be the default assumption.

### LangChain `GitHubAPIWrapper` — GHE support status

**Source**: LangChain issue #24367 (https://github.com/langchain-ai/langchain/issues/24367, filed July 2024, closed as "not planned") and installed source at `/Users/oleg_sotnichenko/.cache/uv/archive-v0/jHZQqfvWFaWLY7Xm/lib/python3.13/site-packages/langchain_community/utilities/github.py`

`GitHubAPIWrapper` (LangChain) does **not** have a `github_base_url` parameter. The issue explicitly confirms: "the wrapper is not taking in the API URL for the GitHub Enterprise instance." It was closed as "not planned."

The installed source shows:
```python
class GitHubAPIWrapper(BaseModel):
    github: Any = None
    github_repo_instance: Any = None
    github_repository: Optional[str] = None
    github_app_id: Optional[str] = None
    github_app_private_key: Optional[str] = None
    active_branch: Optional[str] = None
    github_base_branch: Optional[str] = None

    model_config = ConfigDict(extra="forbid")   # no extra fields allowed
```

**Implication**: `CustomGitHubAPIWrapper` cannot rely on the base class to handle GHE. The fix must be entirely within `CustomGitHubAPIWrapper` itself, with `github_base_url: Optional[str] = None` declared as a first-class Pydantic field in the subclass.

---

## 8. Summary for Complexity Assessment

The GHE bug is a medium-complexity fix with a wider blast radius than the ticket implies. The core change — adding `github_base_url: Optional[str] = None` to `CustomGitHubAPIWrapper` and threading it into all `Github()` and `GithubIntegration()` constructor calls — touches one primary file (`custom_github_api_wrapper.py`) across four methods. However, two factory functions (`utils.py::init_github_api_wrapper` and `git_api_service.py::init_github_api_wrapper`) both discard the `base_url` returned by `split_git_url`, and two additional files (`github_client.py`, `git_auth_utils.py`) also construct `GithubIntegration` without `base_url`. The realistic file change surface is five files, not one. The GitLab wrapper already solves this identically — `gitlab_base_url` extracted from `split_git_url` and passed through — so the pattern is established and the implementation is mechanical once the scope is clear.

The fix follows an established codebase pattern (mirror the GitLab approach) and introduces no novel architectural concepts, but has two non-trivial subtleties that must be handled correctly. First, the `base_url` returned by `split_git_url` is just the hostname (`https://ghe.company.com`), but PyGithub requires `https://ghe.company.com/api/v3` (no trailing slash). The `/api/v3` suffix must be appended in exactly one place — the wrapper's `validate_environment` is the right location. Second, the LangChain base class has `extra="forbid"`, so `github_base_url` must be a declared class attribute, not an extras key; this is already how the wrapper works for its other four fields.

Test coverage posture is mixed: PAT and App auth paths both have tests in `test_custom_git_api_wrapper.py` and `test_github_app_auth.py`, but zero tests cover GHE base_url behavior for either auth path or either factory function. The regression tests required by AC5 must cover: (a) PAT auth with GHE repo URL passes correct `base_url` to `Github()`; (b) App auth with GHE repo URL passes correct `base_url` to both `GithubIntegration()` and `Github()`; (c) `api.github.com` PAT and App auth are unaffected when `github_base_url` is `None`. The risk of silent regression is real — the `GithubIntegration` App-auth path has three constructor calls that each need `base_url`, and missing any one will break GHE App auth at a different stage of the flow.
