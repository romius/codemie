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

from typing import Any

from langchain_core.messages import ImageContentBlock, ToolMessage

from codemie.agents.image_artifact_hook import accumulate_artifact, make_image_message


def _image_artifact_pre_model_hook(state: dict) -> dict:
    """Inject images from ToolMessage.artifact inline after each tool group that produced them."""
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

    if result == list(messages):
        return {"llm_input_messages": messages}
    return {"llm_input_messages": result}


def _compose_pre_model_hooks(*hooks) -> Any | None:
    active_hooks = [hook for hook in hooks if hook is not None]
    if not active_hooks:
        return None

    def composed_pre_model_hook(state: dict[str, Any]) -> dict[str, Any]:
        working_state = dict(state)
        combined_updates: dict[str, Any] = {}

        for hook in active_hooks:
            hook_result = hook(working_state)
            if not hook_result:
                continue

            combined_updates.update(hook_result)

            if "llm_input_messages" in hook_result:
                llm_input_messages = hook_result["llm_input_messages"]
                working_state["messages"] = llm_input_messages
                working_state["llm_input_messages"] = llm_input_messages
            else:
                working_state.update(hook_result)

        return combined_updates

    return composed_pre_model_hook
