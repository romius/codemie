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

import json
import unittest
from datetime import datetime, timezone

from codemie.rest_api.models.agent_workspace import (
    ExecuteWorkspaceScriptResponse,
    WorkspaceFileItemResponse,
)
from codemie_tools.data_management.workspace.execute_workspace_script_tool import (
    ExecuteWorkspaceScriptTool,
)


def _make_file_item() -> WorkspaceFileItemResponse:
    return WorkspaceFileItemResponse(
        path="src/foo.py",
        mime_type="text/x-python",
        checksum="abc123",
        size=42,
        version=1,
        update_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class TestExecuteWorkspaceScriptToolDumpJson(unittest.TestCase):
    def _make_tool(self) -> ExecuteWorkspaceScriptTool:
        return ExecuteWorkspaceScriptTool.__new__(ExecuteWorkspaceScriptTool)

    def test_checksum_excluded_from_workspace_files_in_response(self):
        tool = self._make_tool()
        response = ExecuteWorkspaceScriptResponse(
            message="ok",
            output="done",
            workspace_files=[_make_file_item()],
        )
        result = json.loads(tool._dump_json(response))
        self.assertNotIn("checksum", result["workspace_files"][0])

    def test_output_field_still_present(self):
        tool = self._make_tool()
        response = ExecuteWorkspaceScriptResponse(
            message="ok",
            output="script output",
            workspace_files=[],
        )
        result = json.loads(tool._dump_json(response))
        self.assertEqual(result["output"], "script output")

    def test_path_and_size_present_in_workspace_files(self):
        tool = self._make_tool()
        response = ExecuteWorkspaceScriptResponse(
            message="ok",
            output="",
            workspace_files=[_make_file_item()],
        )
        result = json.loads(tool._dump_json(response))
        file_entry = result["workspace_files"][0]
        self.assertEqual(file_entry["path"], "src/foo.py")
        self.assertEqual(file_entry["size"], 42)
