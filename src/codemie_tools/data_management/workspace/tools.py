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

import base64
from typing import Optional, Type

from pydantic import BaseModel, Field, PrivateAttr

from codemie.rest_api.models.agent_workspace import CreateAgentWorkspaceRequest
from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie_tools.base.codemie_tool import CodeMieTool
from codemie_tools.data_management.workspace.execute_workspace_script_tool import (
    ExecuteWorkspaceScriptTool,
)
from codemie_tools.data_management.workspace.tools_vars import (
    DELETE_WORKSPACE_FILE_TOOL,
    EDIT_WORKSPACE_FILE_TOOL,
    EXECUTE_WORKSPACE_SCRIPT_TOOL,
    GREP_WORKSPACE_FILES_TOOL,
    LIST_WORKSPACE_FILES_TOOL,
    READ_WORKSPACE_FILE_TOOL,
    WRITE_WORKSPACE_FILE_TOOL,
)


class ListWorkspaceFilesInput(BaseModel):
    glob: str | None = Field(
        default=None,
        description="Optional glob pattern to filter files (e.g. '*.py', 'src/**/*.ts'). If omitted, all files are returned.",
    )


class ReadWorkspaceFileInput(BaseModel):
    file_path: str = Field(description="Workspace-relative file path to read.")


class WriteWorkspaceFileInput(BaseModel):
    file_path: str = Field(description="Workspace-relative file path to create or overwrite.")
    content: str = Field(description="UTF-8 text content to write into the workspace file.")


class EditWorkspaceFileInput(BaseModel):
    file_path: str = Field(description="Workspace-relative file path to edit.")
    old_string: str = Field(description="String to replace. Must be unique unless replace_all is true.")
    new_string: str = Field(description="Replacement string.")
    replace_all: bool = Field(
        default=False,
        description="Replace all occurrences instead of the first occurrence.",
    )


class DeleteWorkspaceFileInput(BaseModel):
    file_path: str = Field(description="Workspace-relative file path to delete.")


class GrepWorkspaceFilesInput(BaseModel):
    query: str = Field(description="Case-insensitive search string to find in workspace text files.")
    glob: str | None = Field(
        default=None,
        description="Optional glob pattern to limit the search scope (e.g. '*.py', 'src/**'). If omitted, all text files are searched.",
    )


class BaseWorkspaceTool(CodeMieTool):
    conversation_id: str = Field(exclude=True)
    user: User = Field(exclude=True)
    workspace_service: AgentWorkspaceService = Field(default_factory=AgentWorkspaceService, exclude=True)
    workspace_id: str | None = Field(default=None, exclude=True)
    _workspace_id: str | None = PrivateAttr(default=None)

    def _get_workspace_id(self) -> str:
        if self._workspace_id is None:
            if self.workspace_id:
                self.workspace_service.get_workspace(self.workspace_id, self.user)
                self._workspace_id = self.workspace_id
            else:
                workspace = self.workspace_service.create_workspace(
                    CreateAgentWorkspaceRequest(conversation_id=self.conversation_id),
                    self.user,
                )
                self._workspace_id = workspace.id
        return self._workspace_id

    @staticmethod
    def _dump_json(payload) -> str:
        return ExecuteWorkspaceScriptTool._dump_json(payload)


class ListWorkspaceFilesTool(BaseWorkspaceTool):
    name: str = LIST_WORKSPACE_FILES_TOOL.name
    description: str = LIST_WORKSPACE_FILES_TOOL.description
    args_schema: Type[BaseModel] = ListWorkspaceFilesInput

    def execute(self, glob: str | None = None) -> str:
        workspace_id = self._get_workspace_id()
        files = self.workspace_service.list_files(workspace_id, self.user, glob=glob)
        return self._dump_json(files)


class ReadWorkspaceFileTool(BaseWorkspaceTool):
    name: str = READ_WORKSPACE_FILE_TOOL.name
    description: str = READ_WORKSPACE_FILE_TOOL.description
    args_schema: Type[BaseModel] = ReadWorkspaceFileInput

    def execute(self, file_path: str) -> str:
        workspace_id = self._get_workspace_id()
        file_content = self.workspace_service.get_file_content(workspace_id, file_path, self.user)

        if getattr(file_content, "is_binary", False):
            raw = getattr(file_content, "content", b"") or b""
            if not isinstance(raw, bytes):
                raw = str(raw).encode("utf-8", errors="replace")
            b64 = base64.b64encode(raw).decode("ascii")

            payload = {
                "path": getattr(file_content, "path", file_path),
                "mime_type": getattr(file_content, "mime_type", None),
                "checksum": getattr(file_content, "checksum", ""),
                "size": getattr(file_content, "size", 0),
                "version": getattr(file_content, "version", 0),
                "is_binary": True,
                "content": b64,
                "content_encoding": "base64",
            }
            return self._dump_json(payload)

        # `AgentWorkspaceService.get_file_content()` returns an internal object for both
        # text and binary. For text files, `content` is always a string here, so dumping
        # the internal object is safe and keeps fields consistent.
        payload = {
            "path": getattr(file_content, "path", file_path),
            "mime_type": getattr(file_content, "mime_type", None),
            "checksum": getattr(file_content, "checksum", ""),
            "size": getattr(file_content, "size", 0),
            "version": getattr(file_content, "version", 0),
            "is_binary": bool(getattr(file_content, "is_binary", False)),
            "content": getattr(file_content, "content", None),
        }
        return self._dump_json(payload)


class WriteWorkspaceFileTool(BaseWorkspaceTool):
    name: str = WRITE_WORKSPACE_FILE_TOOL.name
    description: str = WRITE_WORKSPACE_FILE_TOOL.description
    args_schema: Type[BaseModel] = WriteWorkspaceFileInput

    def execute(self, file_path: str, content: str) -> str:
        workspace_id = self._get_workspace_id()
        workspace_file = self.workspace_service.upsert_text_file(workspace_id, file_path, content, self.user)
        return self._dump_json(workspace_file)


class EditWorkspaceFileTool(BaseWorkspaceTool):
    name: str = EDIT_WORKSPACE_FILE_TOOL.name
    description: str = EDIT_WORKSPACE_FILE_TOOL.description
    args_schema: Type[BaseModel] = EditWorkspaceFileInput

    def execute(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> str:
        workspace_id = self._get_workspace_id()
        response = self.workspace_service.edit_file(
            workspace_id=workspace_id,
            file_path=file_path,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
            user=self.user,
        )
        return self._dump_json(response)


class DeleteWorkspaceFileTool(BaseWorkspaceTool):
    name: str = DELETE_WORKSPACE_FILE_TOOL.name
    description: str = DELETE_WORKSPACE_FILE_TOOL.description
    args_schema: Type[BaseModel] = DeleteWorkspaceFileInput

    def execute(self, file_path: str) -> str:
        workspace_id = self._get_workspace_id()
        response = self.workspace_service.delete_file(workspace_id, file_path, self.user)
        return self._dump_json(response)


class GrepWorkspaceFilesTool(BaseWorkspaceTool):
    name: str = GREP_WORKSPACE_FILES_TOOL.name
    description: str = GREP_WORKSPACE_FILES_TOOL.description
    args_schema: Type[BaseModel] = GrepWorkspaceFilesInput

    def execute(self, query: str, glob: str | None = None) -> str:
        workspace_id = self._get_workspace_id()
        matches = self.workspace_service.grep_files(workspace_id, query, self.user, glob=glob)
        return self._dump_json(matches)
