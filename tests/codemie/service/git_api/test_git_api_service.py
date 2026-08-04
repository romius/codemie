# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
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
def test_init_github_api_wrapper_github_com_forwards_raw_base_url(mock_wrapper_class):
    """Seam contract: callsite forwards raw base_url regardless of host; the wrapper's
    _normalize_github_base_url (unit-tested separately, see
    tests/codemie_tools/git/test_github_app_auth.py) is the sole place that decides
    github.com → default PyGithub endpoint. See seam-tests section in
    .ai-run/guides/testing/testing-patterns.md.
    """
    mock_wrapper_class.return_value = Mock()
    GitApiService.init_github_api_wrapper(
        github_access_token="ghp_test",
        repo_link="https://github.com/user/repo.git",
        base_branch="main",
    )
    call_kwargs = mock_wrapper_class.call_args[1]
    assert call_kwargs["github_base_url"] == "https://github.com"


@patch('codemie.service.git_api.git_api_service.CustomGitHubAPIWrapper')
def test_init_github_api_wrapper_no_repo_link(mock_wrapper_class):
    """No repo_link → wrapper called with token only, no base_url."""
    mock_wrapper_class.return_value = Mock()
    GitApiService.init_github_api_wrapper(github_access_token="ghp_test")
    call_kwargs = mock_wrapper_class.call_args[1]
    assert "github_base_url" not in call_kwargs
    assert call_kwargs["github_access_token"] == "ghp_test"
