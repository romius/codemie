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

from typing import List

from codemie_tools.base.base_toolkit import DiscoverableToolkit
from codemie_tools.base.models import Tool, ToolKit, ToolSet
from codemie_tools.sharepoint.tools import SharePointTool
from codemie_tools.sharepoint.tools_vars import SHAREPOINT_TOOL


class SharePointToolkitUI(ToolKit):
    toolkit: ToolSet = ToolSet.SHAREPOINT
    tools: List[Tool] = [
        Tool.from_metadata(SHAREPOINT_TOOL, tool_class=SharePointTool),
    ]
    label: str = ToolSet.SHAREPOINT.value


class SharePointToolkit(DiscoverableToolkit):
    @classmethod
    def get_definition(cls):
        return SharePointToolkitUI()
