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
import mimetypes
from typing import Type

from langchain_core.tools import ToolException
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from codemie.configs.logger import logger
from codemie.core.constants import LLM_MODEL
from codemie.core.dependecies import get_llm_by_credentials
from codemie.service.llm_service.llm_service import llm_service
from codemie_tools.data_management.workspace.tools import BaseWorkspaceTool
from codemie_tools.data_management.workspace.tools_vars import INSPECT_WORKSPACE_IMAGE_TOOL

_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB — conservative limit across vision providers
_MAX_TOKENS = 3000

# Magic-byte signatures for supported image formats.
# Checked against raw file content so a misnamed file is rejected correctly.
_IMAGE_MAGIC: list[tuple[bytes, int | None, bytes | None, str]] = [
    (b"\xff\xd8\xff", None, None, "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", None, None, "image/png"),
    (b"GIF87a", None, None, "image/gif"),
    (b"GIF89a", None, None, "image/gif"),
    (b"RIFF", 8, b"WEBP", "image/webp"),
    (b"BM", None, None, "image/bmp"),
    (b"II*\x00", None, None, "image/tiff"),
    (b"MM\x00*", None, None, "image/tiff"),
]


def _detect_mime_from_content(raw: bytes) -> str | None:
    for magic, secondary_offset, secondary_check, mime in _IMAGE_MAGIC:
        if not raw.startswith(magic):
            continue
        if secondary_offset is None:
            return mime
        end = secondary_offset + len(secondary_check)  # type: ignore[arg-type]
        if len(raw) >= end and raw[secondary_offset:end] == secondary_check:
            return mime
    return None


class InspectWorkspaceImageInput(BaseModel):
    file_path: str = Field(description="Path to the image file to inspect.")
    inspect_request: str = Field(description="What exactly to analyze/inspect in the image.")


class InspectWorkspaceImageTool(BaseWorkspaceTool):
    name: str = INSPECT_WORKSPACE_IMAGE_TOOL.name
    description: str = INSPECT_WORKSPACE_IMAGE_TOOL.description
    args_schema: Type[BaseModel] = InspectWorkspaceImageInput
    response_format: str = "content"
    llm_model: str | None = Field(default=None, exclude=True)
    request_uuid: str | None = Field(default=None, exclude=True)

    def execute(self, file_path: str, inspect_request: str) -> str:
        workspace_id = self._get_workspace_id()
        file_obj = self.workspace_service.download_file(workspace_id, file_path, self.user)
        if file_obj.content is None:
            raise ToolException(f"File '{file_path}' could not be read (empty or unreadable).")
        raw = file_obj.content if isinstance(file_obj.content, bytes) else file_obj.content.encode()
        if len(raw) > _MAX_IMAGE_BYTES:
            raise ToolException(
                f"Image '{file_path}' is {len(raw) // 1024} KB, which exceeds the "
                f"{_MAX_IMAGE_BYTES // (1024 * 1024)} MB limit for visual inspection."
            )
        mime_type = _detect_mime_from_content(raw) or mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        if not mime_type.startswith("image/"):
            raise ToolException(
                f"File '{file_path}' does not appear to be an image (detected MIME type: {mime_type}). "
                "Only image files are supported."
            )

        meta_model = (self.metadata if isinstance(self.metadata, dict) else {}).get(LLM_MODEL)
        model_name = (self.llm_model or "").strip() or (str(meta_model).strip() if meta_model is not None else "")
        if not model_name:
            raise ToolException(
                "inspect_workspace_image requires a vision-capable model but no model name was provided "
                "(expected tool.llm_model or metadata['llm_model'])."
            )

        try:
            model_details = llm_service.get_model_details(model_name)
        except Exception as exc:
            raise ToolException(f"Failed to look up model details for '{model_name}': {exc}") from exc
        if model_details is None or not getattr(model_details, "multimodal", False):
            raise ToolException(
                f"inspect_workspace_image requires a vision-capable (multimodal) model. Current model: {model_name}."
            )

        try:
            chat_model = get_llm_by_credentials(
                llm_model=model_name,
                request_id=self.request_uuid,
                streaming=False,
            )
        except Exception as exc:
            raise ToolException(f"Failed to initialize vision model '{model_name}': {exc}") from exc

        b64 = base64.b64encode(raw).decode("ascii")
        message = HumanMessage(
            content=[
                {"type": "text", "text": inspect_request},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
            ]
        )
        try:
            logger.info(
                "InspectWorkspaceImageTool: invoking vision-capable model. "
                f"model={model_name} request_id={self.request_uuid}"
            )
            response = chat_model.invoke([message], max_tokens=_MAX_TOKENS)
            logger.debug(
                "InspectWorkspaceImageTool: vision-capable model invocation completed. "
                f"model={model_name} request_id={self.request_uuid}"
            )
        except Exception as exc:
            raise ToolException(f"Vision model invocation failed: {exc}") from exc
        if response is None:
            raise ToolException("Vision model returned no response.")

        content = response.content
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                block["text"] if isinstance(block, dict) and block.get("type") == "text" else str(block)
                for block in content
            )
        return str(content)
