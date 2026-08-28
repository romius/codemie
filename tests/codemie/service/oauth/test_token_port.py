# Copyright 2026 EPAM Systems, Inc. ("EPAM")
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

"""ToolOAuthTokenPort error mapping - TMS failures are sanitized, never leaked."""

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.oauth.token_port import map_tms_error_to_http

tms = pytest.importorskip("codemie_enterprise.mcp_auth")


def test_unavailable_maps_to_503():
    exc = map_tms_error_to_http("GitLab", tms.TMSUnavailable("down"))
    assert isinstance(exc, ExtendedHTTPException)
    assert exc.code == 503
    assert "GitLab" in exc.message


def test_persistence_error_maps_to_502():
    exc = map_tms_error_to_http("Jira", tms.TMSPersistenceError("boom"))
    assert exc.code == 502


def test_refresh_error_maps_to_502():
    exc = map_tms_error_to_http("Confluence", tms.TokenRefreshError("nope"))
    assert exc.code == 502


def test_unknown_error_maps_to_generic_500():
    exc = map_tms_error_to_http("GitLab", RuntimeError("unexpected"))
    assert exc.code == 500
    # The raw exception text must never be surfaced to the caller.
    assert "unexpected" not in exc.message
