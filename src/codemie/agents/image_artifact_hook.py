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

from langchain_core.messages import HumanMessage, ImageContentBlock, TextContentBlock, ToolMessage


def _extract_image_blocks(artifact: list) -> tuple[list[ImageContentBlock], str | None]:
    blocks = []
    text = None
    for item in artifact:
        if isinstance(item, dict) and "data" in item and "mime_type" in item:
            blocks.append(ImageContentBlock(type="image", base64=item["data"], mime_type=item["mime_type"]))
            if not text:
                text = item.get("text")
    return blocks, text


def make_image_message(blocks: list[ImageContentBlock], text: str | None = None) -> HumanMessage:
    return HumanMessage(
        content=[
            TextContentBlock(type="text", text=text or "[Attached images from the tool response above]"),
            *blocks,
        ]
    )


def accumulate_artifact(
    msg: ToolMessage, pending: list[ImageContentBlock], pending_text: str | None
) -> tuple[list[ImageContentBlock], str | None]:
    artifact = getattr(msg, "artifact", None)
    if not isinstance(artifact, list):
        return pending, pending_text
    blocks, text = _extract_image_blocks(artifact)
    return [*pending, *blocks], pending_text if pending_text else text


def image_artifact_pre_model_hook(state: dict) -> dict:
    """Inject images from ToolMessage.artifact right after the tool group that produced them."""
    messages = state.get("messages", [])
    result: list = []
    pending: list[ImageContentBlock] = []
    pending_text: str | None = None

    for msg in messages:
        if isinstance(msg, ToolMessage):
            pending, pending_text = accumulate_artifact(msg, pending, pending_text)
        else:
            if pending:
                result.append(make_image_message(pending, pending_text))
                pending = []
                pending_text = None
        result.append(msg)

    if pending:
        result.append(make_image_message(pending, pending_text))

    return {"llm_input_messages": result}
