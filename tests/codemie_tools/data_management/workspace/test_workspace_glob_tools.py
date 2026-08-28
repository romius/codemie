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

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import json

from codemie.rest_api.models.agent_workspace import AgentWorkspaceFile
from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie_tools.data_management.workspace.tools import (
    GrepWorkspaceFilesTool,
    ListWorkspaceFilesTool,
)


def _user() -> User:
    return User(id="u-1")


def _workspace_file(path: str, *, mime_type: str = "text/plain") -> AgentWorkspaceFile:
    # AgentWorkspaceFile is an SQLModel model, but we only need the attribute bag here.
    return AgentWorkspaceFile(
        workspace_id="w-1",
        path=path,
        blob_owner="owner",
        blob_name=path,
        mime_type=mime_type,
        checksum="",
        size=0,
        version=1,
        deleted_at=None,
        date=datetime(2026, 1, 1),
        update_date=datetime(2026, 1, 1),
    )


class TestWorkspaceGlobTools:
    def test_workspace_list_with_glob(self):
        service = AgentWorkspaceService.__new__(AgentWorkspaceService)
        service.repository = MagicMock()
        service.file_repository = MagicMock()

        service.get_workspace = MagicMock(return_value=MagicMock(id="w-1", conversation_id="c-1"))
        service._get_conversation_uploaded_files = MagicMock(return_value=[])

        service.repository.list_files.return_value = [
            _workspace_file("src/main.py"),
            _workspace_file("README.md"),
            _workspace_file("src/utils/helpers.py"),
        ]

        files = service.list_files("w-1", _user(), glob="src/**/*.py")
        paths = [file.path for file in files]
        assert paths == ["src/main.py", "src/utils/helpers.py"]

    def test_workspace_grep_with_glob(self):
        service = AgentWorkspaceService.__new__(AgentWorkspaceService)
        service.repository = MagicMock()
        service.file_repository = MagicMock()

        service.get_workspace = MagicMock(return_value=MagicMock(id="w-1", conversation_id="c-1"))
        service._get_conversation_uploaded_files = MagicMock(return_value=[])

        service.repository.list_files.return_value = [
            _workspace_file("src/main.py", mime_type="text/x-python"),
            _workspace_file("README.md", mime_type="text/markdown"),
        ]

        def _read_file(*, file_name: str, owner: str, mime_type: str):
            content = "TODO: fix\n" if file_name.endswith(".py") else "TODO in docs\n"
            return MagicMock(content=content)

        service.file_repository.read_file.side_effect = _read_file

        matches = service.grep_files("w-1", query="todo", user=_user(), glob="src/**/*.py")
        assert len(matches) == 1
        assert matches[0].file_path == "src/main.py"
        assert "TODO" in matches[0].line

    def test_list_workspace_files_tool_forwards_glob(self):
        # CodeMieTool subclasses are Pydantic models; avoid __new__ which bypasses
        # Pydantic initialization and breaks internal invariants.
        tool = ListWorkspaceFilesTool.model_construct(
            user=_user(),
            conversation_id="c-1",
            workspace_id=None,
            workspace_service=MagicMock(),
        )
        tool._workspace_id = "w-1"
        tool.workspace_service.list_files.return_value = []

        result = tool.execute(glob="src/**/*.py")

        tool.workspace_service.list_files.assert_called_once_with("w-1", tool.user, glob="src/**/*.py")
        # Ensure it's valid JSON (tool always returns JSON strings).
        assert json.loads(result) == []

    def test_grep_workspace_files_tool_forwards_glob(self):
        tool = GrepWorkspaceFilesTool.model_construct(
            user=_user(),
            conversation_id="c-1",
            workspace_id=None,
            workspace_service=MagicMock(),
        )
        tool._workspace_id = "w-1"
        tool.workspace_service.grep_files.return_value = []

        result = tool.execute(query="todo", glob="src/**/*.py")

        tool.workspace_service.grep_files.assert_called_once_with("w-1", "todo", tool.user, glob="src/**/*.py")
        assert json.loads(result) == []
