# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Resolution passes the acting user's id to the GitLab token manager."""

from unittest.mock import patch

from codemie_tools.core.vcs.gitlab.models import GitlabConfig
from codemie_tools.core.vcs.gitlab.tools import GitlabTool


def test_resolve_access_token_uses_acting_user():
    cfg = GitlabConfig(
        url="https://gitlab.com",
        auth_type="oauth",
        integration_id="s1",
        acting_user_id="u1",
    )
    tool = GitlabTool(config=cfg)
    with patch("codemie.service.gitlab_oauth.token_manager.GitLabOAuthTokenManager") as tm_cls:
        tm_cls.return_value.get_valid_access_token.return_value = "tok"
        assert tool._resolve_access_token() == "tok"
        tm_cls.return_value.get_valid_access_token.assert_called_once_with("s1", "u1")


def test_resolve_access_token_pat_ignores_user():
    cfg = GitlabConfig(url="https://gitlab.com", token="pat-123")
    tool = GitlabTool(config=cfg)
    assert tool._resolve_access_token() == "pat-123"
