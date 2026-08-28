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

import pytest

from codemie.core.exceptions import ValidationException
from codemie.service.agent_workspace_service import AgentWorkspaceService

_match = AgentWorkspaceService._path_matches_glob


class TestPathMatchesGlob:
    def test_none_glob_matches_everything(self):
        assert _match("src/foo.py", None) is True
        assert _match("config.py", None) is True

    def test_bare_star_matches_only_root_level(self):
        assert _match("config.py", "*.py") is True
        assert _match("src/config.py", "*.py") is False
        assert _match("src/sub/deep.py", "*.py") is False

    def test_directory_star_matches_direct_children_only(self):
        assert _match("src/foo.py", "src/*.py") is True
        assert _match("src/sub/foo.py", "src/*.py") is False
        # must be left-anchored — project/src/foo.py must NOT match
        assert _match("project/src/foo.py", "src/*.py") is False

    def test_double_star_slash_matches_zero_or_more_segments(self):
        # zero segments: root-level file
        assert _match("foo.py", "**/foo.py") is True
        # one segment
        assert _match("src/foo.py", "**/foo.py") is True
        # two segments
        assert _match("a/b/foo.py", "**/foo.py") is True

    def test_recursive_glob_with_prefix(self):
        assert _match("src/sub/a.ts", "src/**/*.ts") is True
        assert _match("src/a/b/c/a.ts", "src/**/*.ts") is True
        assert _match("src/a.ts", "src/**/*.ts") is True
        # must NOT match outside src/
        assert _match("other/src/a.ts", "src/**/*.ts") is False

    def test_double_star_alone_matches_all(self):
        assert _match("foo.py", "**") is True
        assert _match("src/sub/deep.ts", "**") is True

    def test_src_double_star_matches_all_under_src(self):
        assert _match("src/foo.py", "src/**") is True
        assert _match("src/sub/deep.ts", "src/**") is True
        assert _match("other/foo.py", "src/**") is False

    def test_exact_name_matches(self):
        assert _match("config.py", "config.py") is True
        assert _match("src/config.py", "config.py") is False

    def test_question_mark_matches_single_non_separator_char(self):
        assert _match("foo.py", "fo?.py") is True
        assert _match("fooo.py", "fo?.py") is False
        assert _match("src/foo.py", "fo?.py") is False


class TestValidateGlob:
    def test_none_is_accepted(self):
        AgentWorkspaceService._validate_glob(None)  # must not raise

    def test_valid_patterns_accepted(self):
        for pattern in ["*.py", "src/*.py", "src/**/*.ts", "**/foo.py", "src/**", "**"]:
            AgentWorkspaceService._validate_glob(pattern)  # must not raise

    def test_absolute_path_rejected(self):
        with pytest.raises(ValidationException):
            AgentWorkspaceService._validate_glob("/etc/**")

    def test_traversal_rejected(self):
        with pytest.raises(ValidationException):
            AgentWorkspaceService._validate_glob("../../*")

    def test_traversal_in_middle_rejected(self):
        with pytest.raises(ValidationException):
            AgentWorkspaceService._validate_glob("src/../other/*.py")

    def test_empty_string_rejected(self):
        with pytest.raises(ValidationException):
            AgentWorkspaceService._validate_glob("")

    def test_whitespace_only_rejected(self):
        with pytest.raises(ValidationException):
            AgentWorkspaceService._validate_glob("   ")


class TestPathLengthGuards:
    def test_path_matches_glob_rejects_excessively_long_paths(self):
        # Guard against attacker-controlled very long file names causing expensive matching.
        long_path = "a" * 5000
        assert AgentWorkspaceService._path_matches_glob(long_path, "*.py") is False

    def test_normalize_path_rejects_excessively_long_paths(self):
        with pytest.raises(ValidationException):
            AgentWorkspaceService._normalize_path("a" * 5000)
