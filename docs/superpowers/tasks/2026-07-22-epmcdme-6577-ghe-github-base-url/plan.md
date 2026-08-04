# EPMCDME-6577: GitHub Enterprise base_url Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development to implement this plan task-by-task (sdlc-light Stage 4 — inline TDD, no subagents). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CodeMie's GitHub integration reach GitHub Enterprise Server API instead of always defaulting to `api.github.com`, across every `Github()` / `GithubIntegration()` construction site (wrapper + two factories + `github_client.py` + `git_auth_utils.py`).

**Architecture:** The hostname is already available at every entry point either from `GitCredentials.repo_link` (via `split_git_url`) or from `GithubConfig.url`. We introduce a single normalization helper `_normalize_github_base_url(host_or_url)` in the wrapper module that returns either `None` (github.com / empty → PyGithub default) or `https://{hostname}/api/v3` (GHE). Every PyGithub constructor call receives `base_url=<normalized>` when non-None. Mirrors the existing GitLab pattern (`gitlab_base_url` field on `CustomGitLabAPIWrapper`).

**Tech Stack:** Python 3.12, pytest, PyGithub, LangChain `GitHubAPIWrapper` (base class), Pydantic v2 with `model_validator(mode="before")`.

## Global Constraints

- Backward compatibility: passing no `base_url` / empty / `github.com` / `api.github.com` MUST keep going to `api.github.com` (no regressions for cloud users).
- PyGithub `base_url` format: `https://{hostname}/api/v3` — no trailing slash, WITH `/api/v3` (verified in PyGithub + GHE REST docs; a trailing slash breaks all requests).
- LangChain base `GitHubAPIWrapper` sets `model_config = ConfigDict(extra="forbid")` — new field MUST be declared as a Pydantic class attribute on `CustomGitHubAPIWrapper`, not passed via extras.
- Both `Github()` and `GithubIntegration()` MUST receive `base_url` — App-auth flow uses both classes; missing either leaves App-auth partially broken on GHE.
- Do NOT change public API signatures of `init_github_api_wrapper`, `CustomGitHubAPIWrapper.__init__`, or `GithubClient.__init__`; only add optional fields / params where already required by an internal call.
- Commit messages: `EPMCDME-6577: <description>` (project convention).

---

### Task 1: Add `github_base_url` field and normalization helper on `CustomGitHubAPIWrapper`

**Files:**
- Modify: `src/codemie_tools/git/github/custom_github_api_wrapper.py:27-32` (add class field) and top of file (add helper)
- Test: `tests/codemie_tools/git/test_github_app_auth.py` (new test class `TestNormalizeGithubBaseUrl`)

**Interfaces:**
- Consumes: nothing (foundational).
- Produces:
  - New module-level helper `_normalize_github_base_url(value: Optional[str]) -> Optional[str]`. Rules:
    - `None`, `""`, whitespace-only → return `None`
    - value whose parsed hostname is `github.com` or `api.github.com` (case-insensitive) → return `None`
    - value with no scheme (bare hostname `ghe.company.com`) → prepend `https://`
    - value already ending with `/api/v3` or `/api/v3/` → normalize to `https://{host}/api/v3` (strip trailing slash, preserve scheme)
    - otherwise → `https://{host}/api/v3` (drop any path/query/fragment)
  - New Pydantic field on `CustomGitHubAPIWrapper`: `github_base_url: Optional[str] = None`

- [ ] **Step 1: Write failing tests for `_normalize_github_base_url`**

Append to `tests/codemie_tools/git/test_github_app_auth.py` (bottom of file):

```python
import pytest
from codemie_tools.git.github.custom_github_api_wrapper import _normalize_github_base_url


class TestNormalizeGithubBaseUrl:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (None, None),
            ("", None),
            ("   ", None),
            ("github.com", None),
            ("https://github.com", None),
            ("https://api.github.com", None),
            ("api.github.com", None),
            ("GitHub.com", None),
            # GHE — bare hostname
            ("ghe.company.com", "https://ghe.company.com/api/v3"),
            # GHE — scheme + hostname
            ("https://ghe.company.com", "https://ghe.company.com/api/v3"),
            # GHE — already has /api/v3
            ("https://ghe.company.com/api/v3", "https://ghe.company.com/api/v3"),
            # GHE — trailing slash removed
            ("https://ghe.company.com/api/v3/", "https://ghe.company.com/api/v3"),
            # Non-standard path is dropped
            ("https://ghe.company.com/some/path", "https://ghe.company.com/api/v3"),
            # http scheme preserved
            ("http://ghe.internal", "http://ghe.internal/api/v3"),
        ],
    )
    def test_normalize(self, value, expected):
        assert _normalize_github_base_url(value) == expected
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/codemie_tools/git/test_github_app_auth.py::TestNormalizeGithubBaseUrl -x -q`
Expected: FAIL with `ImportError` — `_normalize_github_base_url` does not exist.

- [ ] **Step 3: Implement helper and field**

At the top of `src/codemie_tools/git/github/custom_github_api_wrapper.py` (after existing imports, before the class definition), add:

```python
from urllib.parse import urlparse

_GITHUB_COM_HOSTS = frozenset({"github.com", "api.github.com"})


def _normalize_github_base_url(value: Optional[str]) -> Optional[str]:
    """
    Normalize a GHE hostname or URL to the PyGithub-compatible base URL.

    Returns None when the value is missing/blank or points at github.com
    (PyGithub's default of https://api.github.com already handles that case).

    Otherwise returns 'https://{host}/api/v3' — no trailing slash, WITH
    the /api/v3 suffix that GHE Server requires (per PyGithub + GHE REST
    docs). A trailing slash on the base URL would produce double-slash
    request paths and break every call.
    """
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    parsed = urlparse(trimmed if "://" in trimmed else f"https://{trimmed}")
    host = (parsed.hostname or "").lower()
    if not host or host in _GITHUB_COM_HOSTS:
        return None
    scheme = parsed.scheme or "https"
    return f"{scheme}://{host}/api/v3"
```

Then, inside `class CustomGitHubAPIWrapper`, add the field alongside the four existing ones (immediately after `github_app_installation_id: Optional[int] = None` at line 31):

```python
    github_base_url: Optional[str] = None
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/codemie_tools/git/test_github_app_auth.py::TestNormalizeGithubBaseUrl -x -q`
Expected: PASS (12 test cases).

- [ ] **Step 5: Commit**

```bash
git add src/codemie_tools/git/github/custom_github_api_wrapper.py tests/codemie_tools/git/test_github_app_auth.py
git commit -m "EPMCDME-6577: Add github_base_url field and normalization helper to CustomGitHubAPIWrapper"
```

---

### Task 2: Thread `github_base_url` through the wrapper's auth methods

**Files:**
- Modify: `src/codemie_tools/git/github/custom_github_api_wrapper.py:34-175` (four methods)
- Test: `tests/codemie_tools/git/test_github_app_auth.py` (extend existing PAT + App auth tests)

**Interfaces:**
- Consumes: `github_base_url: Optional[str]` field from Task 1; `_normalize_github_base_url` helper from Task 1.
- Produces: all `Github()` and `GithubIntegration()` constructor calls in the wrapper now receive `base_url=<normalized>` when the caller passed a GHE `github_base_url`. Method signatures change:
  - `_get_installation_id(app_id, private_key, provided_installation_id, base_url: Optional[str])`
  - `_create_github_app_auth(app_id, private_key, installation_id, base_url: Optional[str])`
  - `_create_pat_auth(values: Dict)` — reads `github_base_url` from `values` via `get_from_dict_or_env`
  - `validate_environment` — normalizes `github_base_url` once and threads through

- [ ] **Step 1: Write failing tests for GHE base_url threading**

Append to `tests/codemie_tools/git/test_github_app_auth.py`, right after the existing `test_custom_github_api_wrapper_github_app_auth`:

```python
@patch('github.Auth')
@patch('github.Github')
def test_custom_github_api_wrapper_pat_auth_with_ghe_base_url(mock_github, mock_auth):
    """PAT auth on GHE must pass base_url to Github()."""
    from codemie_tools.git.github.custom_github_api_wrapper import CustomGitHubAPIWrapper

    mock_github.return_value = Mock()

    CustomGitHubAPIWrapper(
        github_access_token="ghp_test",
        github_base_url="https://ghe.company.com",
        github_base_branch="main",
        active_branch="main",
    )

    mock_github.assert_called_once()
    assert mock_github.call_args.kwargs.get("base_url") == "https://ghe.company.com/api/v3"


@patch('github.Auth')
@patch('github.Github')
def test_custom_github_api_wrapper_pat_auth_github_com_no_base_url(mock_github, mock_auth):
    """PAT auth on github.com must NOT pass base_url (PyGithub default is api.github.com)."""
    from codemie_tools.git.github.custom_github_api_wrapper import CustomGitHubAPIWrapper

    mock_github.return_value = Mock()

    CustomGitHubAPIWrapper(
        github_access_token="ghp_test",
        github_base_branch="main",
        active_branch="main",
    )

    mock_github.assert_called_once()
    assert "base_url" not in mock_github.call_args.kwargs


@patch('github.Auth')
@patch('github.Github')
@patch('github.GithubIntegration')
def test_custom_github_api_wrapper_github_app_auth_with_ghe_base_url(
    mock_integration_class, mock_github, mock_auth
):
    """App auth on GHE must pass base_url to BOTH GithubIntegration() and Github()."""
    from codemie_tools.git.github.custom_github_api_wrapper import CustomGitHubAPIWrapper

    mock_access_token = Mock()
    mock_access_token.token = "ghs_app_token"
    mock_integration = Mock()
    mock_integration.get_access_token.return_value = mock_access_token
    mock_integration_class.return_value = mock_integration
    mock_github.return_value = Mock()

    CustomGitHubAPIWrapper(
        github_app_id=123456,
        github_app_private_key="test_private_key",
        github_app_installation_id=12345678,
        github_base_url="https://ghe.company.com",
        github_base_branch="main",
        active_branch="main",
    )

    # GithubIntegration was called twice in the wrapper's App flow — both times must carry base_url
    for call in mock_integration_class.call_args_list:
        assert call.kwargs.get("base_url") == "https://ghe.company.com/api/v3"
    # Github() call in _create_github_app_auth also carries base_url
    mock_github.assert_called_once()
    assert mock_github.call_args.kwargs.get("base_url") == "https://ghe.company.com/api/v3"


@patch('github.Auth')
@patch('github.Github')
@patch('github.GithubIntegration')
def test_custom_github_api_wrapper_github_app_auth_github_com_no_base_url(
    mock_integration_class, mock_github, mock_auth
):
    """App auth on github.com must NOT pass base_url to GithubIntegration() or Github()."""
    from codemie_tools.git.github.custom_github_api_wrapper import CustomGitHubAPIWrapper

    mock_access_token = Mock()
    mock_access_token.token = "ghs_app_token"
    mock_integration = Mock()
    mock_integration.get_access_token.return_value = mock_access_token
    mock_integration_class.return_value = mock_integration
    mock_github.return_value = Mock()

    CustomGitHubAPIWrapper(
        github_app_id=123456,
        github_app_private_key="test_private_key",
        github_app_installation_id=12345678,
        github_base_branch="main",
        active_branch="main",
    )

    for call in mock_integration_class.call_args_list:
        assert "base_url" not in call.kwargs
    assert "base_url" not in mock_github.call_args.kwargs
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/codemie_tools/git/test_github_app_auth.py::test_custom_github_api_wrapper_pat_auth_with_ghe_base_url tests/codemie_tools/git/test_github_app_auth.py::test_custom_github_api_wrapper_github_app_auth_with_ghe_base_url -x -q`
Expected: FAIL — assertions on `base_url` kwarg fail because implementation still ignores the field.

- [ ] **Step 3: Update the four wrapper methods**

Replace the four methods in `src/codemie_tools/git/github/custom_github_api_wrapper.py` with:

```python
    @staticmethod
    def _get_installation_id(
        app_id: int,
        private_key: str,
        provided_installation_id: Optional[int],
        base_url: Optional[str],
    ) -> int:
        if provided_installation_id is not None:
            return provided_installation_id

        from github import GithubIntegration

        integration_kwargs = {"integration_id": app_id, "private_key": private_key}
        if base_url:
            integration_kwargs["base_url"] = base_url
        integration = GithubIntegration(**integration_kwargs)
        installations = integration.get_installations()
        try:
            first_installation = next(iter(installations))
            return first_installation.id
        except StopIteration:
            raise ValueError(
                "No GitHub App installations found. Please install the app or provide installation_id"
            )

    @staticmethod
    def _create_github_app_auth(
        app_id: int,
        private_key: str,
        installation_id: Optional[int],
        base_url: Optional[str],
    ):
        from github import Auth, Github, GithubIntegration

        logging.info("Using GitHub App authentication")

        integration_kwargs = {"integration_id": app_id, "private_key": private_key}
        if base_url:
            integration_kwargs["base_url"] = base_url
        integration = GithubIntegration(**integration_kwargs)

        resolved_installation_id = CustomGitHubAPIWrapper._get_installation_id(
            app_id, private_key, installation_id, base_url
        )
        access_token = integration.get_access_token(resolved_installation_id)

        auth = Auth.Token(access_token.token)
        github_kwargs = {"auth": auth}
        if base_url:
            github_kwargs["base_url"] = base_url
        return Github(**github_kwargs)

    @staticmethod
    def _create_pat_auth(values: Dict):
        from github import Auth, Github

        github_access_token = get_from_dict_or_env(values, "github_access_token", "GITHUB_ACCESS_TOKEN")
        auth = Auth.Token(github_access_token)
        github_kwargs = {"auth": auth}
        base_url = values.get("github_base_url")
        if base_url:
            github_kwargs["base_url"] = base_url
        return Github(**github_kwargs)
```

Then replace `validate_environment` (the classmethod at line 147) with:

```python
    @classmethod
    @model_validator(mode="before")
    def validate_environment(cls, values: Dict) -> Dict:
        try:
            from github import Auth, Github  # noqa: F401
        except ImportError:
            raise ImportError("PyGithub is not installed. Please install it with `pip install PyGithub`")

        github_base_url = _normalize_github_base_url(
            get_from_dict_or_env(values, "github_base_url", "GITHUB_BASE_URL", default=None)
        )
        values["github_base_url"] = github_base_url

        has_app_id = "github_app_id" in values and values["github_app_id"]
        has_private_key = "github_app_private_key" in values and values["github_app_private_key"]
        has_github_app = has_app_id and has_private_key

        if has_github_app:
            github_instance = cls._create_github_app_auth(
                app_id=values["github_app_id"],
                private_key=values["github_app_private_key"],
                installation_id=values.get("github_app_installation_id"),
                base_url=github_base_url,
            )
        else:
            github_instance = cls._create_pat_auth(values)

        values["github"] = github_instance
        values = cls._setup_repository(github_instance, values)
        return values
```

- [ ] **Step 4: Run new + existing wrapper tests to verify pass**

Run: `pytest tests/codemie_tools/git/test_github_app_auth.py tests/codemie_tools/git/test_custom_git_api_wrapper.py -x -q`
Expected: PASS — all GHE assertions pass AND the existing `test_custom_github_api_wrapper_pat_auth`, `test_custom_github_api_wrapper_github_app_auth`, `test_validate_environment_success` remain green (backward compat).

- [ ] **Step 5: Commit**

```bash
git add src/codemie_tools/git/github/custom_github_api_wrapper.py tests/codemie_tools/git/test_github_app_auth.py
git commit -m "EPMCDME-6577: Thread github_base_url through wrapper auth methods"
```

---

### Task 3: Stop discarding `base_url` in `init_github_api_wrapper` (main factory)

**Files:**
- Modify: `src/codemie_tools/git/utils.py:156-192` (function `init_github_api_wrapper`)
- Test: `tests/codemie_tools/git/test_github_app_auth.py` (extend existing `test_init_github_api_wrapper_with_pat` and `_with_github_app` — do NOT break them)

**Interfaces:**
- Consumes: `github_base_url` field on `CustomGitHubAPIWrapper` (Task 1).
- Produces: when `git_creds.repo_link` is a GHE URL, `init_github_api_wrapper` now passes `github_base_url=<hostname>` into `wrapper_args`. For `github.com` repos, `github_base_url` is NOT passed (avoids injecting a value the wrapper would just null out).

- [ ] **Step 1: Write failing tests for GHE + backward compat**

Append to `tests/codemie_tools/git/test_github_app_auth.py`:

```python
@patch('codemie_tools.git.utils.CustomGitHubAPIWrapper')
def test_init_github_api_wrapper_with_pat_ghe_passes_base_url(mock_wrapper_class):
    """PAT + GHE repo_link forwards github_base_url to wrapper."""
    mock_wrapper_class.return_value = Mock()
    creds = GitCredentials(
        token="ghp_test",
        repo_link="https://ghe.company.com/user/repo.git",
        base_branch="main",
        repo_type="github",
    )
    init_github_api_wrapper(creds)
    call_kwargs = mock_wrapper_class.call_args[1]
    assert call_kwargs["github_base_url"] == "https://ghe.company.com"


@patch('codemie_tools.git.utils.CustomGitHubAPIWrapper')
def test_init_github_api_wrapper_with_github_app_ghe_passes_base_url(mock_wrapper_class):
    """App + GHE repo_link forwards github_base_url to wrapper."""
    mock_wrapper_class.return_value = Mock()
    creds = GitCredentials(
        app_id=123456,
        private_key="test_private_key",
        installation_id=12345678,
        repo_link="https://ghe.company.com/user/repo.git",
        base_branch="main",
        repo_type="github",
    )
    init_github_api_wrapper(creds)
    call_kwargs = mock_wrapper_class.call_args[1]
    assert call_kwargs["github_base_url"] == "https://ghe.company.com"


@patch('codemie_tools.git.utils.CustomGitHubAPIWrapper')
def test_init_github_api_wrapper_github_com_omits_base_url(mock_wrapper_class):
    """github.com repos MUST NOT include github_base_url — normalization is the wrapper's job for empty values."""
    mock_wrapper_class.return_value = Mock()
    creds = GitCredentials(
        token="ghp_test",
        repo_link="https://github.com/user/repo.git",
        base_branch="main",
        repo_type="github",
    )
    init_github_api_wrapper(creds)
    call_kwargs = mock_wrapper_class.call_args[1]
    assert "github_base_url" not in call_kwargs
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/codemie_tools/git/test_github_app_auth.py -x -q -k "ghe or github_com_omits"`
Expected: FAIL — factory does not thread the field yet.

- [ ] **Step 3: Update the factory**

Replace lines 156–192 of `src/codemie_tools/git/utils.py` with:

```python
def init_github_api_wrapper(git_creds: GitCredentials):
    try:
        if not git_creds.token and not git_creds.is_github_app:
            return None

        wrapper_args = {
            "github_base_branch": git_creds.base_branch,
            "active_branch": git_creds.base_branch,
        }

        if git_creds.repo_link is not None:
            base_url, repo_name = split_git_url(git_creds.repo_link)
            wrapper_args["github_repository"] = repo_name.replace(".git", "").replace("/", "", 1)
            hostname = urlparse(base_url).hostname or ""
            if hostname.lower() not in ("github.com", "api.github.com"):
                wrapper_args["github_base_url"] = base_url

        if git_creds.is_github_app:
            wrapper_args["github_app_id"] = git_creds.app_id
            wrapper_args["github_app_private_key"] = git_creds.private_key
            if git_creds.installation_id:
                wrapper_args["github_app_installation_id"] = git_creds.installation_id
        else:
            wrapper_args["github_access_token"] = git_creds.token

        return CustomGitHubAPIWrapper(**wrapper_args)
    except Exception:
        stacktrace = traceback.format_exc()
        logger.error(
            f"GitHub API wrapper initialisation failed with error: {stacktrace}",
            exc_info=True,
        )
        return None
```

Also add at the top of the same file, alongside the existing `import re, traceback`:

```python
from urllib.parse import urlparse
```

(Only if not already imported. Check `src/codemie_tools/git/utils.py` imports first — leave existing imports untouched.)

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/codemie_tools/git/test_github_app_auth.py -x -q`
Expected: PASS — new GHE assertions pass AND the pre-existing `test_init_github_api_wrapper_with_pat` / `_with_github_app` / `_with_github_app_no_installation_id` remain green.

- [ ] **Step 5: Commit**

```bash
git add src/codemie_tools/git/utils.py tests/codemie_tools/git/test_github_app_auth.py
git commit -m "EPMCDME-6577: Forward GHE base_url from init_github_api_wrapper (utils)"
```

---

### Task 4: Stop discarding `base_url` in the older `init_github_api_wrapper` (git_api_service)

**Files:**
- Modify: `src/codemie/service/git_api/git_api_service.py:62-78`
- Test: new test file `tests/codemie/service/git_api/test_git_api_service.py` (create if the folder has no test dir yet — check first)

**Interfaces:**
- Consumes: `github_base_url` field on `CustomGitHubAPIWrapper` (Task 1).
- Produces: `GitApiService.init_github_api_wrapper` passes `github_base_url` for GHE repos, omits it for `github.com`. Public signature unchanged.

- [ ] **Step 1: Check whether a test file already exists**

Run: `test -f tests/codemie/service/git_api/test_git_api_service.py && echo EXISTS || echo MISSING`
If EXISTS, extend it. If MISSING, create with the header below.

- [ ] **Step 2: Write failing tests**

Create/extend `tests/codemie/service/git_api/test_git_api_service.py`:

```python
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
# ... (copy the SPDX header from any nearby test file verbatim)

from unittest.mock import Mock, patch

from codemie.service.git_api.git_api_service import GitApiService


@patch('codemie.service.git_api.git_api_service.CustomGitHubAPIWrapper')
def test_init_github_api_wrapper_ghe_passes_base_url(mock_wrapper_class):
    mock_wrapper_class.return_value = Mock()
    GitApiService.init_github_api_wrapper(
        github_access_token="ghp_test",
        repo_link="https://ghe.company.com/user/repo.git",
        base_branch="main",
    )
    call_kwargs = mock_wrapper_class.call_args[1]
    assert call_kwargs["github_base_url"] == "https://ghe.company.com"
    assert call_kwargs["github_repository"] == "user/repo"


@patch('codemie.service.git_api.git_api_service.CustomGitHubAPIWrapper')
def test_init_github_api_wrapper_github_com_omits_base_url(mock_wrapper_class):
    mock_wrapper_class.return_value = Mock()
    GitApiService.init_github_api_wrapper(
        github_access_token="ghp_test",
        repo_link="https://github.com/user/repo.git",
        base_branch="main",
    )
    call_kwargs = mock_wrapper_class.call_args[1]
    assert "github_base_url" not in call_kwargs


@patch('codemie.service.git_api.git_api_service.CustomGitHubAPIWrapper')
def test_init_github_api_wrapper_no_repo_link(mock_wrapper_class):
    """No repo_link → wrapper called with token only, no base_url."""
    mock_wrapper_class.return_value = Mock()
    GitApiService.init_github_api_wrapper(github_access_token="ghp_test")
    call_kwargs = mock_wrapper_class.call_args[1]
    assert "github_base_url" not in call_kwargs
    assert call_kwargs["github_access_token"] == "ghp_test"
```

Run: `pytest tests/codemie/service/git_api/test_git_api_service.py -x -q`
Expected: FAIL — factory still discards `base_url`.

- [ ] **Step 3: Update the factory**

Replace `init_github_api_wrapper` in `src/codemie/service/git_api/git_api_service.py` (lines 62–78) with:

```python
    @classmethod
    def init_github_api_wrapper(cls, github_access_token: str, repo_link: str = None, base_branch: str = None):
        try:
            if repo_link is not None:
                from urllib.parse import urlparse

                base_url, repo_name = cls.split_git_url(repo_link)
                wrapper_kwargs = {
                    "github_repository": repo_name.replace(".git", "").replace('/', '', 1),
                    "github_base_branch": base_branch,
                    "active_branch": base_branch,
                    "github_access_token": github_access_token,
                }
                hostname = urlparse(base_url).hostname or ""
                if hostname.lower() not in ("github.com", "api.github.com"):
                    wrapper_kwargs["github_base_url"] = base_url
                github = CustomGitHubAPIWrapper(**wrapper_kwargs)
            else:
                github = CustomGitHubAPIWrapper(github_access_token=github_access_token)
            return github
        except Exception:
            stacktrace = traceback.format_exc()
            logger.error(f"GitHub API wrapper initialisation failed with error: {stacktrace}", exc_info=True)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/codemie/service/git_api/test_git_api_service.py -x -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/git_api/git_api_service.py tests/codemie/service/git_api/test_git_api_service.py
git commit -m "EPMCDME-6577: Forward GHE base_url from GitApiService.init_github_api_wrapper"
```

---

### Task 5: Pass `base_url` to `GithubIntegration` in `github_client.py`

**Files:**
- Modify: `src/codemie_tools/core/vcs/github/github_client.py:101-103`
- Test: `tests/codemie_tools/core/vcs/github/test_github_app_auth.py` (extend)

**Interfaces:**
- Consumes: `GithubConfig.url` (already exists — Pydantic field with default `"https://api.github.com"`).
- Produces: `github.GithubIntegration(..., base_url=<normalized>)` where the same normalization rules from Task 1 apply, reusing `_normalize_github_base_url` imported from the wrapper module. For the default `"https://api.github.com"` value, the normalizer returns `None` and we omit `base_url` (PyGithub default is `api.github.com`).

- [ ] **Step 1: Write failing tests**

Append to `tests/codemie_tools/core/vcs/github/test_github_app_auth.py`:

```python
@patch('github.GithubIntegration')
def test_github_client_github_app_ghe_passes_base_url(mock_integration_class):
    """GHE URL in GithubConfig forwards base_url to GithubIntegration."""
    mock_access_token = Mock()
    mock_access_token.token = "ghs_token"
    mock_access_token.expires_at = None
    mock_integration = Mock()
    mock_integration.get_access_token.return_value = mock_access_token
    mock_integration_class.return_value = mock_integration

    config = GithubConfig(
        app_id=123456,
        private_key="test_private_key",
        installation_id=12345678,
        url="https://ghe.company.com",
    )
    GithubClient(config).get_auth_token()

    mock_integration_class.assert_called_once()
    assert mock_integration_class.call_args.kwargs.get("base_url") == "https://ghe.company.com/api/v3"


@patch('github.GithubIntegration')
def test_github_client_github_app_github_com_omits_base_url(mock_integration_class):
    """github.com URL must NOT pass base_url."""
    mock_access_token = Mock()
    mock_access_token.token = "ghs_token"
    mock_access_token.expires_at = None
    mock_integration = Mock()
    mock_integration.get_access_token.return_value = mock_access_token
    mock_integration_class.return_value = mock_integration

    config = GithubConfig(
        app_id=123456,
        private_key="test_private_key",
        installation_id=12345678,
        # url defaults to https://api.github.com
    )
    GithubClient(config).get_auth_token()

    mock_integration_class.assert_called_once()
    assert "base_url" not in mock_integration_class.call_args.kwargs
```

Run: `pytest tests/codemie_tools/core/vcs/github/test_github_app_auth.py -x -q -k "ghe or github_com_omits"`
Expected: FAIL.

- [ ] **Step 2: Update the client**

In `src/codemie_tools/core/vcs/github/github_client.py`, at the top with other imports add:

```python
from codemie_tools.git.github.custom_github_api_wrapper import _normalize_github_base_url
```

Then replace lines 100–103 with:

```python
            # Create GithubIntegration instance
            integration_kwargs = {
                "integration_id": self.config.app_id,
                "private_key": self.config.private_key,
            }
            base_url = _normalize_github_base_url(self.config.url)
            if base_url:
                integration_kwargs["base_url"] = base_url
            integration = github.GithubIntegration(**integration_kwargs)
```

- [ ] **Step 3: Run tests to verify pass**

Run: `pytest tests/codemie_tools/core/vcs/github/test_github_app_auth.py -x -q`
Expected: PASS — new tests plus all existing ones.

- [ ] **Step 4: Commit**

```bash
git add src/codemie_tools/core/vcs/github/github_client.py tests/codemie_tools/core/vcs/github/test_github_app_auth.py
git commit -m "EPMCDME-6577: Pass GHE base_url to GithubIntegration in github_client"
```

---

### Task 6: Accept optional `base_url` in `git_auth_utils.get_github_app_token`

**Files:**
- Modify: `src/codemie/datasource/loader/git_auth_utils.py:23-58`
- Test: `tests/codemie/datasource/loader/test_git_auth_utils.py` (extend)

**Interfaces:**
- Consumes: `_normalize_github_base_url` from the wrapper module (Task 1).
- Produces: `get_github_app_token(app_id, private_key, installation_id=None, base_url: Optional[str] = None) -> str`. Callers already pass positional args — new param is keyword-only with default `None`. Both `GithubIntegration()` call sites in the function receive `base_url=<normalized>` when non-None.

- [ ] **Step 1: Write failing tests**

Append to `tests/codemie/datasource/loader/test_git_auth_utils.py`:

```python
@patch('github.GithubIntegration')
def test_get_github_app_token_ghe_passes_base_url(mock_integration_class):
    mock_access_token = Mock()
    mock_access_token.token = "ghs_token"
    mock_integration = Mock()
    mock_integration.get_access_token.return_value = mock_access_token
    mock_integration_class.return_value = mock_integration

    from codemie.datasource.loader.git_auth_utils import get_github_app_token

    get_github_app_token(
        app_id=123456,
        private_key="key",
        installation_id=12345678,
        base_url="https://ghe.company.com",
    )
    assert mock_integration_class.call_args.kwargs.get("base_url") == "https://ghe.company.com/api/v3"


@patch('github.GithubIntegration')
def test_get_github_app_token_github_com_omits_base_url(mock_integration_class):
    mock_access_token = Mock()
    mock_access_token.token = "ghs_token"
    mock_integration = Mock()
    mock_integration.get_access_token.return_value = mock_access_token
    mock_integration_class.return_value = mock_integration

    from codemie.datasource.loader.git_auth_utils import get_github_app_token

    get_github_app_token(app_id=123456, private_key="key", installation_id=12345678)
    assert "base_url" not in mock_integration_class.call_args.kwargs
```

Add `from unittest.mock import Mock` and `from unittest.mock import patch` at the top if not already there.

Run: `pytest tests/codemie/datasource/loader/test_git_auth_utils.py -x -q -k "ghe or github_com_omits"`
Expected: FAIL.

- [ ] **Step 2: Update the function**

Replace `src/codemie/datasource/loader/git_auth_utils.py:23-58` with:

```python
def get_github_app_token(
    app_id: int,
    private_key: str,
    installation_id: Optional[int] = None,
    base_url: Optional[str] = None,
) -> str:
    """
    Generate GitHub App installation access token using PyGithub.

    Args:
        app_id: GitHub App ID
        private_key: Private key in PEM format
        installation_id: Installation ID (optional, will auto-detect)
        base_url: Hostname or full URL of a GHE Server (optional). When
            omitted or pointing at github.com, PyGithub's default endpoint
            is used.

    Returns:
        str: Installation access token

    Raises:
        ValueError: If token generation fails
    """
    try:
        from github import GithubIntegration
    except ImportError:
        raise ImportError("PyGithub is required for GitHub App authentication")

    # Local import — avoids a hard circular dep at module load if wrapper module ever changes.
    from codemie_tools.git.github.custom_github_api_wrapper import _normalize_github_base_url

    try:
        integration_kwargs = {"integration_id": app_id, "private_key": private_key}
        normalized = _normalize_github_base_url(base_url)
        if normalized:
            integration_kwargs["base_url"] = normalized
        integration = GithubIntegration(**integration_kwargs)

        if installation_id is None:
            installations = integration.get_installations()
            first_installation = next(iter(installations))
            installation_id = first_installation.id

        access_token = integration.get_access_token(installation_id)
        return access_token.token

    except Exception as e:
        logger.error(f"Failed to generate GitHub App token: {e}")
        raise ValueError(f"GitHub App authentication failed: {str(e)}")
```

- [ ] **Step 3: Run tests to verify pass**

Run: `pytest tests/codemie/datasource/loader/test_git_auth_utils.py -x -q`
Expected: PASS — new + existing tests.

- [ ] **Step 4: Commit**

```bash
git add src/codemie/datasource/loader/git_auth_utils.py tests/codemie/datasource/loader/test_git_auth_utils.py
git commit -m "EPMCDME-6577: Accept optional base_url in git_auth_utils.get_github_app_token"
```

---

### Task 7: Thread `base_url` through `git_loader.py` call sites of `get_github_app_token`

**Files:**
- Modify: `src/codemie/datasource/loader/git_loader.py:87` and `:118` (both call sites)
- Test: existing tests under `tests/codemie/datasource/loader/test_git_loader.py` (verify via targeted mock; add if none exist for these functions)

**Interfaces:**
- Consumes: new `base_url` kwarg on `get_github_app_token` (Task 6); `split_git_url` from `codemie_tools.git.utils`.
- Produces: Both `_build_clone_url(creds, repo)` and `_build_auth_header(creds)` extract the hostname from `repo.link` (for `_build_clone_url`) — for `_build_auth_header` the function does not currently see `repo`, so we adjust: extract hostname from `creds.repo_link` if present, otherwise `None`.

- [ ] **Step 1: Check existing tests**

Run: `test -f tests/codemie/datasource/loader/test_git_loader.py && grep -n "get_github_app_token\|_build_clone_url\|_build_auth_header" tests/codemie/datasource/loader/test_git_loader.py || echo NO_TEST_FILE`

- [ ] **Step 2: Write failing tests**

Add (or create the file with the SPDX header + imports if missing) to `tests/codemie/datasource/loader/test_git_loader.py`:

```python
from unittest.mock import Mock, patch


@patch('codemie.datasource.loader.git_loader.get_github_app_token')
def test_build_clone_url_ghe_passes_base_url(mock_get_token):
    from codemie.datasource.loader.git_loader import _build_clone_url

    mock_get_token.return_value = "ghs_token"

    creds = Mock()
    creds.is_github_app = True
    creds.app_id = 1
    creds.private_key = "k"
    creds.installation_id = 42
    creds.token = None
    creds.token_name = None

    repo = Mock()
    repo.link = "https://ghe.company.com/user/repo.git"

    _build_clone_url(creds, repo)
    assert mock_get_token.call_args.kwargs.get("base_url") == "https://ghe.company.com"


@patch('codemie.datasource.loader.git_loader.get_github_app_token')
def test_build_auth_header_ghe_passes_base_url(mock_get_token):
    from codemie.datasource.loader.git_loader import _build_auth_header

    mock_get_token.return_value = "ghs_token"

    creds = Mock()
    creds.is_github_app = True
    creds.app_id = 1
    creds.private_key = "k"
    creds.installation_id = 42
    creds.token = None
    creds.repo_link = "https://ghe.company.com/user/repo.git"

    _build_auth_header(creds)
    assert mock_get_token.call_args.kwargs.get("base_url") == "https://ghe.company.com"
```

Run: `pytest tests/codemie/datasource/loader/test_git_loader.py -x -q -k "ghe_passes_base_url"`
Expected: FAIL — call sites still pass positional args only.

- [ ] **Step 3: Update the two call sites**

In `src/codemie/datasource/loader/git_loader.py`, add at the top:

```python
from codemie_tools.git.utils import split_git_url
```

Replace line 87 (`_build_clone_url`):

```python
        base_url, _ = split_git_url(repo.link)
        token = get_github_app_token(
            creds.app_id,
            creds.private_key,
            creds.installation_id,
            base_url=base_url,
        )
```

Replace line 118 (`_build_auth_header`) — this function only has `creds`, not `repo`, so pull the URL from `creds.repo_link` when present:

```python
        base_url = None
        if getattr(creds, "repo_link", None):
            base_url, _ = split_git_url(creds.repo_link)
        token = get_github_app_token(
            creds.app_id,
            creds.private_key,
            creds.installation_id,
            base_url=base_url,
        )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/codemie/datasource/loader/test_git_loader.py -x -q`
Expected: PASS — new tests, and any existing `test_git_loader.py` tests must remain green.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/datasource/loader/git_loader.py tests/codemie/datasource/loader/test_git_loader.py
git commit -m "EPMCDME-6577: Forward GHE base_url from git_loader to get_github_app_token"
```

---

### Task 8: Full-suite regression check

**Files:** (none modified — validation only)

**Interfaces:**
- Consumes: everything from Tasks 1–7.
- Produces: green suite for git tests + wrapper tests.

- [ ] **Step 1: Run all affected test paths**

Run:

```bash
pytest \
  tests/codemie_tools/git/test_custom_git_api_wrapper.py \
  tests/codemie_tools/git/test_github_app_auth.py \
  tests/codemie_tools/core/vcs/github/test_github_app_auth.py \
  tests/codemie/datasource/loader/test_git_auth_utils.py \
  tests/codemie/datasource/loader/test_git_loader.py \
  tests/codemie/service/git_api/test_git_api_service.py \
  -x -q
```

Expected: PASS across the board. No skips introduced by the fix.

- [ ] **Step 2: Confirm no lingering unpatched `Github(auth=…)` or `GithubIntegration(integration_id=…, private_key=…)` (without `base_url`) in production paths**

Run:

```bash
grep -rn "Github(auth=" src/ | grep -v tests
grep -rn "GithubIntegration(" src/ | grep -v tests
```

Expected: every match either receives `base_url=` (conditionally or unconditionally) or is a comment.

- [ ] **Step 3: Commit (only if any docstring/comment fixup is needed; otherwise skip)**

No commit if nothing changed.

---

## Self-review checklist (author only — not a runtime step)

- AC1 (users can input GHE URL): satisfied — hostname is derived from the already-existing `repo_link` (main path) and `GithubConfig.url` (secondary path); no new user-facing field required.
- AC2 (system uses provided URL): satisfied by Tasks 2–7.
- AC3 (successful connectivity to GHE): assertions in wrapper/client tests confirm `base_url` reaches PyGithub with the exact `https://{host}/api/v3` format required by GHE REST.
- AC4 (fix at `custom_github_api_wrapper.py:33`): covered by Tasks 1 + 2 (line 33 is inside `_get_installation_id`; the fix additionally spans the sibling auth methods because they were also missing `base_url`).
- AC5 (no regressions for api.github.com): dedicated backward-compat tests in Tasks 2, 3, 4, 5, 6 assert `base_url not in call_kwargs` for the github.com paths; Task 8's full-suite run is the final regression gate.
- Placeholder scan: none.
- Type consistency: `github_base_url: Optional[str]` used identically across wrapper field, factory kwargs, and `get_github_app_token(base_url=…)`.
