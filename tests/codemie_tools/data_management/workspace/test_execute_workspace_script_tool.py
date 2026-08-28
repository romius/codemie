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

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import json
import unittest

from codemie.rest_api.models.agent_workspace import (
    ExecuteWorkspaceScriptResponse,
    WorkspaceFileItemResponse,
)
from codemie_tools.data_management.code_executor.models import CodeExecutorConfig
from codemie_tools.data_management.workspace.execute_workspace_script_tool import (
    ExecuteWorkspaceScriptTool,
    WorkspaceScriptRunner,
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


class TestBuildScriptWrapperForwardsResourceLimits(unittest.TestCase):
    """_build_script_wrapper must pass config-controlled limits to the guard builder."""

    def _runner_with_config(self, max_threads: int, max_open_files: int) -> WorkspaceScriptRunner:
        runner = MagicMock(spec=WorkspaceScriptRunner)
        runner.config = CodeExecutorConfig(max_threads=max_threads, max_open_files=max_open_files)
        runner._build_script_wrapper = WorkspaceScriptRunner._build_script_wrapper.__get__(runner)
        return runner

    def test_forwards_max_threads(self):
        runner = self._runner_with_config(max_threads=16, max_open_files=128)
        with patch(
            "codemie_tools.data_management.workspace.execute_workspace_script_tool.build_guarded_workspace_script"
        ) as mock_build:
            mock_build.return_value = "script"
            runner._build_script_wrapper("script.py", "/workspace")
            _, kwargs = mock_build.call_args
            self.assertEqual(kwargs["max_threads"], 16)

    def test_forwards_max_open_files(self):
        runner = self._runner_with_config(max_threads=16, max_open_files=128)
        with patch(
            "codemie_tools.data_management.workspace.execute_workspace_script_tool.build_guarded_workspace_script"
        ) as mock_build:
            mock_build.return_value = "script"
            runner._build_script_wrapper("script.py", "/workspace")
            _, kwargs = mock_build.call_args
            self.assertEqual(kwargs["max_open_files"], 128)

    def test_non_default_config_values_are_forwarded(self):
        runner = self._runner_with_config(max_threads=8, max_open_files=256)
        with patch(
            "codemie_tools.data_management.workspace.execute_workspace_script_tool.build_guarded_workspace_script"
        ) as mock_build:
            mock_build.return_value = "script"
            runner._build_script_wrapper("run.py", "/sandbox")
            _, kwargs = mock_build.call_args
            self.assertEqual(kwargs["max_threads"], 8)
            self.assertEqual(kwargs["max_open_files"], 256)
