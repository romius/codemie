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

"""Spot-check that always-safe tools return True from is_safe()."""

import pytest


def _make_search_kb():
    from codemie.agents.tools.kb.search_kb import SearchKBTool

    return SearchKBTool.__new__(SearchKBTool)


def _make_read_file():
    from codemie_tools.data_management.file_system.tools import ReadFileTool

    return ReadFileTool.__new__(ReadFileTool)


def _make_get_wiki_page_by_path():
    from codemie_tools.azure_devops.wiki.tools import GetWikiPageByPathTool

    return GetWikiPageByPathTool.__new__(GetWikiPageByPathTool)


def _make_get_page_xwiki():
    from codemie_tools.core.project_management.xwiki.tools import GetPageTool

    return GetPageTool.__new__(GetPageTool)


def _make_list_directory():
    from codemie_tools.data_management.file_system.tools import ListDirectoryTool

    return ListDirectoryTool.__new__(ListDirectoryTool)


@pytest.mark.parametrize(
    "make_tool",
    [
        _make_search_kb,
        _make_read_file,
        _make_get_wiki_page_by_path,
        _make_get_page_xwiki,
        _make_list_directory,
    ],
)
def test_always_safe_tool_returns_true(make_tool):
    tool = make_tool()
    assert tool.is_safe({}) is True
    assert tool.is_safe({"method": "POST"}) is True
