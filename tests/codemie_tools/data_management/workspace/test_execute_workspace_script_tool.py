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

from unittest.mock import MagicMock, patch

from codemie_tools.data_management.code_executor.models import CodeExecutorConfig
from codemie_tools.data_management.workspace.execute_workspace_script_tool import (
    WorkspaceScriptRunner,
)


class TestBuildScriptWrapperForwardsResourceLimits:
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
            assert kwargs["max_threads"] == 16

    def test_forwards_max_open_files(self):
        runner = self._runner_with_config(max_threads=16, max_open_files=128)
        with patch(
            "codemie_tools.data_management.workspace.execute_workspace_script_tool.build_guarded_workspace_script"
        ) as mock_build:
            mock_build.return_value = "script"
            runner._build_script_wrapper("script.py", "/workspace")
            _, kwargs = mock_build.call_args
            assert kwargs["max_open_files"] == 128

    def test_non_default_config_values_are_forwarded(self):
        runner = self._runner_with_config(max_threads=8, max_open_files=256)
        with patch(
            "codemie_tools.data_management.workspace.execute_workspace_script_tool.build_guarded_workspace_script"
        ) as mock_build:
            mock_build.return_value = "script"
            runner._build_script_wrapper("run.py", "/sandbox")
            _, kwargs = mock_build.call_args
            assert kwargs["max_threads"] == 8
            assert kwargs["max_open_files"] == 256
