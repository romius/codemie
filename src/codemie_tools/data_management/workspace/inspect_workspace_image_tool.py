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
from dataclasses import dataclass, field
from typing import Any, Type

from langchain_core.tools import ToolException
from pydantic import BaseModel, Field

from codemie_tools.data_management.workspace.tools import BaseWorkspaceTool
from codemie_tools.data_management.workspace.tools_vars import INSPECT_WORKSPACE_IMAGE_TOOL

_MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB — conservative limit across vision providers

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


@dataclass
class InspectWorkspaceImageResponse:
    text: str
    image_artifact: list = field(default_factory=list)

    def __str__(self) -> str:
        return self.text


class InspectWorkspaceImageInput(BaseModel):
    file_path: str = Field(description="Path to the image file to inspect.")
    inspect_request: str = Field(description="What exactly to analyze/inspect in the image.")


class InspectWorkspaceImageTool(BaseWorkspaceTool):
    name: str = INSPECT_WORKSPACE_IMAGE_TOOL.name
    description: str = INSPECT_WORKSPACE_IMAGE_TOOL.description
    args_schema: Type[BaseModel] = InspectWorkspaceImageInput
    response_format: str = "content_and_artifact"

    def execute(self, file_path: str, inspect_request: str) -> InspectWorkspaceImageResponse:
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
        b64 = base64.b64encode(raw).decode()
        artifact = [{"data": b64, "mime_type": mime_type, "filename": file_path, "text": inspect_request}]
        text = (
            f"Image '{file_path}' is now attached for visual analysis. Respond with your analysis of: {inspect_request}"
        )
        return InspectWorkspaceImageResponse(text=text, image_artifact=artifact)

    def _limit_output_content(self, output: Any) -> Any:
        if not isinstance(output, InspectWorkspaceImageResponse):
            return super()._limit_output_content(output)
        limited_text, token_count = super()._limit_output_content(output.text)
        if limited_text is output.text:
            return output, token_count
        return InspectWorkspaceImageResponse(text=limited_text, image_artifact=output.image_artifact), token_count

    def _post_process_output_content(self, output, *args, **kwargs) -> tuple[str, list]:
        if not isinstance(output, InspectWorkspaceImageResponse):
            return str(output), []
        return output.text, output.image_artifact
