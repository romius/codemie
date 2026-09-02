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

from codemie_tools.base.models import ToolSet
from codemie_tools.qa.toolkit import QualityAssuranceToolkit, QualityAssuranceToolkitUI
from codemie_tools.qa.zephyr_squad.tools_vars import ZEPHYR_SQUAD_TOOL


class TestQualityAssuranceToolkit:
    def test_get_definition(self):
        toolkit_ui = QualityAssuranceToolkit.get_definition()
        assert isinstance(toolkit_ui, QualityAssuranceToolkitUI)
        assert toolkit_ui.toolkit == ToolSet.QUALITY_ASSURANCE
        assert len(toolkit_ui.tools) == 5
        assert toolkit_ui.label == ToolSet.QUALITY_ASSURANCE.value


class TestQualityAssuranceToolkitUI:
    def test_toolkit_property(self):
        toolkit_ui = QualityAssuranceToolkitUI()
        assert toolkit_ui.toolkit == ToolSet.QUALITY_ASSURANCE

    def test_tools_property(self):
        toolkit_ui = QualityAssuranceToolkitUI()
        assert len(toolkit_ui.tools) == 5

        # Check that the tools are correctly defined
        tool_names = [tool.name for tool in toolkit_ui.tools]
        assert "ZephyrScale" in tool_names
        assert "ZephyrSquad" in tool_names
        assert "XrayGetTests" in tool_names
        assert "XrayCreateTest" in tool_names
        assert "XrayExecuteGraphQL" in tool_names

    def test_zephyr_squad_marked_deprecated(self):
        toolkit_ui = QualityAssuranceToolkitUI()
        by_name = {tool.name: tool for tool in toolkit_ui.tools}
        assert by_name["ZephyrSquad"].deprecated is True
        for name in ("ZephyrScale", "XrayGetTests", "XrayCreateTest", "XrayExecuteGraphQL"):
            assert by_name[name].deprecated is False, f"{name} should not be deprecated"

    def test_zephyr_squad_metadata_deprecated_flag(self):
        assert ZEPHYR_SQUAD_TOOL.deprecated is True
