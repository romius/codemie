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

"""GitLabAuthRequiredException payload and propagation contract."""

from codemie.core.exceptions import GitLabAuthRequiredException, MCPAuthenticationRequiredException


def test_payload_shape_matches_client_contract():
    exc = GitLabAuthRequiredException("s1", "Team GitLab")
    assert exc.payload == {
        "error": "gitlab_auth_required",
        "setting_id": "s1",
        "integration_name": "Team GitLab",
    }


def test_defaults_integration_name_when_missing():
    exc = GitLabAuthRequiredException("s1")
    assert exc.payload["integration_name"] == "GitLab integration"


def test_subclasses_mcp_auth_so_existing_propagation_catches_it():
    # The agent/stream layers catch MCPAuthenticationRequiredException; subclassing means the
    # GitLab gate rides the same propagation and the shared HTTP handler serializes the payload.
    assert issubclass(GitLabAuthRequiredException, MCPAuthenticationRequiredException)
    assert isinstance(GitLabAuthRequiredException("s1"), MCPAuthenticationRequiredException)
