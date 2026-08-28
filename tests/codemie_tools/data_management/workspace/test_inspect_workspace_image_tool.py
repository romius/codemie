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

import io
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.tools import ToolException
from PIL import Image

from codemie.rest_api.security.user import User
from codemie.service.agent_workspace_service import AgentWorkspaceService
from codemie_tools.data_management.workspace.inspect_workspace_image_tool import InspectWorkspaceImageTool


def _make_png_bytes(size: tuple[int, int] = (1, 1), color: str = "white") -> bytes:
    image = Image.new("RGB", size, color=color)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _build_workspace_service(*, content: bytes | None) -> AgentWorkspaceService:
    service = object.__new__(AgentWorkspaceService)
    service.get_workspace = MagicMock(return_value=MagicMock(id="workspace-1"))
    service.download_file = MagicMock(return_value=MagicMock(content=content))
    return service


def _build_tool(*, llm_model: str | None, metadata: dict | None, workspace_service: AgentWorkspaceService):
    tool = InspectWorkspaceImageTool(
        conversation_id="conversation-1",
        user=User(id="user-1", auth_token=None),
        workspace_service=workspace_service,
        workspace_id="workspace-1",
        llm_model=llm_model,
        request_uuid="request-1",
    )
    tool.metadata = metadata
    return tool


class TestInspectWorkspaceImageTool:
    def test_execute_prefers_tool_llm_model_over_metadata(self):
        workspace_service = _build_workspace_service(content=_make_png_bytes())
        tool = _build_tool(
            llm_model="vision-a",
            metadata={"llm_model": "vision-b"},
            workspace_service=workspace_service,
        )

        model_details = MagicMock(multimodal=True)
        fake_chat = MagicMock()
        fake_chat.invoke.return_value = MagicMock(content="ok")

        with (
            patch(
                "codemie_tools.data_management.workspace.inspect_workspace_image_tool.llm_service.get_model_details",
                return_value=model_details,
            ) as mock_details,
            patch(
                "codemie_tools.data_management.workspace.inspect_workspace_image_tool.get_llm_by_credentials",
                return_value=fake_chat,
            ) as mock_get_llm,
        ):
            assert tool.execute(file_path="image.png", inspect_request="describe") == "ok"

        mock_details.assert_called_once_with("vision-a")
        mock_get_llm.assert_called_once()
        assert mock_get_llm.call_args.kwargs["llm_model"] == "vision-a"

    def test_execute_falls_back_to_metadata_model_name(self):
        workspace_service = _build_workspace_service(content=_make_png_bytes())
        tool = _build_tool(
            llm_model=None,
            metadata={"llm_model": "vision-metadata"},
            workspace_service=workspace_service,
        )

        model_details = MagicMock(multimodal=True)
        fake_chat = MagicMock()
        fake_chat.invoke.return_value = MagicMock(content="ok")

        with (
            patch(
                "codemie_tools.data_management.workspace.inspect_workspace_image_tool.llm_service.get_model_details",
                return_value=model_details,
            ),
            patch(
                "codemie_tools.data_management.workspace.inspect_workspace_image_tool.get_llm_by_credentials",
                return_value=fake_chat,
            ) as mock_get_llm,
        ):
            assert tool.execute(file_path="image.png", inspect_request="describe") == "ok"

        # Regression: must pass the resolved model_name, not self.llm_model (None).
        assert mock_get_llm.call_args.kwargs["llm_model"] == "vision-metadata"

        # Regression coverage for image-attachment chat flows: ensure we pass both text and image_url blocks.
        messages = fake_chat.invoke.call_args.args[0]
        assert len(messages) == 1
        message = messages[0]
        assert any(
            isinstance(block, dict) and block.get("type") == "text" and block.get("text") == "describe"
            for block in message.content
        )
        assert any(
            isinstance(block, dict)
            and block.get("type") == "image_url"
            and isinstance(block.get("image_url"), dict)
            and str(block["image_url"].get("url", "")).startswith("data:image/png;base64,")
            for block in message.content
        )

    def test_execute_raises_when_model_missing(self):
        workspace_service = _build_workspace_service(content=_make_png_bytes())
        tool = _build_tool(llm_model=None, metadata={}, workspace_service=workspace_service)

        with pytest.raises(ToolException, match="no model name was provided"):
            tool.execute(file_path="image.png", inspect_request="describe")

    def test_execute_raises_for_non_multimodal_model(self):
        workspace_service = _build_workspace_service(content=_make_png_bytes())
        tool = _build_tool(
            llm_model="text-only",
            metadata={},
            workspace_service=workspace_service,
        )

        model_details = MagicMock(multimodal=False)
        with patch(
            "codemie_tools.data_management.workspace.inspect_workspace_image_tool.llm_service.get_model_details",
            return_value=model_details,
        ):
            with pytest.raises(ToolException, match="vision-capable"):
                tool.execute(file_path="image.png", inspect_request="describe")

    def test_execute_raises_when_model_lookup_fails(self):
        workspace_service = _build_workspace_service(content=_make_png_bytes())
        tool = _build_tool(
            llm_model="vision-a",
            metadata={},
            workspace_service=workspace_service,
        )

        with patch(
            "codemie_tools.data_management.workspace.inspect_workspace_image_tool.llm_service.get_model_details",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(ToolException, match="Failed to look up model details"):
                tool.execute(file_path="image.png", inspect_request="describe")

    def test_execute_raises_when_llm_init_fails(self):
        workspace_service = _build_workspace_service(content=_make_png_bytes())
        tool = _build_tool(
            llm_model="vision-a",
            metadata={},
            workspace_service=workspace_service,
        )

        model_details = MagicMock(multimodal=True)
        with (
            patch(
                "codemie_tools.data_management.workspace.inspect_workspace_image_tool.llm_service.get_model_details",
                return_value=model_details,
            ),
            patch(
                "codemie_tools.data_management.workspace.inspect_workspace_image_tool.get_llm_by_credentials",
                side_effect=RuntimeError("init failed"),
            ),
        ):
            with pytest.raises(ToolException, match="Failed to initialize vision model"):
                tool.execute(file_path="image.png", inspect_request="describe")

    def test_execute_raises_when_invoke_fails(self):
        workspace_service = _build_workspace_service(content=_make_png_bytes())
        tool = _build_tool(
            llm_model="vision-a",
            metadata={},
            workspace_service=workspace_service,
        )

        model_details = MagicMock(multimodal=True)
        fake_chat = MagicMock()
        fake_chat.invoke.side_effect = RuntimeError("provider error")

        with (
            patch(
                "codemie_tools.data_management.workspace.inspect_workspace_image_tool.llm_service.get_model_details",
                return_value=model_details,
            ),
            patch(
                "codemie_tools.data_management.workspace.inspect_workspace_image_tool.get_llm_by_credentials",
                return_value=fake_chat,
            ),
        ):
            with pytest.raises(ToolException, match="Vision model invocation failed"):
                tool.execute(file_path="image.png", inspect_request="describe")

    def test_execute_raises_when_response_is_none(self):
        workspace_service = _build_workspace_service(content=_make_png_bytes())
        tool = _build_tool(
            llm_model="vision-a",
            metadata={},
            workspace_service=workspace_service,
        )

        model_details = MagicMock(multimodal=True)
        fake_chat = MagicMock()
        fake_chat.invoke.return_value = None

        with (
            patch(
                "codemie_tools.data_management.workspace.inspect_workspace_image_tool.llm_service.get_model_details",
                return_value=model_details,
            ),
            patch(
                "codemie_tools.data_management.workspace.inspect_workspace_image_tool.get_llm_by_credentials",
                return_value=fake_chat,
            ),
        ):
            with pytest.raises(ToolException, match="Vision model returned no response"):
                tool.execute(file_path="image.png", inspect_request="describe")

    def test_execute_raises_when_file_is_not_image(self):
        workspace_service = _build_workspace_service(content=b"hello, not an image")
        tool = _build_tool(
            llm_model="vision-a",
            metadata={},
            workspace_service=workspace_service,
        )

        with pytest.raises(ToolException, match="does not appear to be an image"):
            tool.execute(file_path="not-image.txt", inspect_request="describe")

    def test_execute_raises_when_file_content_empty(self):
        workspace_service = _build_workspace_service(content=None)
        tool = _build_tool(
            llm_model="vision-a",
            metadata={},
            workspace_service=workspace_service,
        )

        with pytest.raises(ToolException, match="could not be read"):
            tool.execute(file_path="image.png", inspect_request="describe")
