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

from typing import ClassVar, Optional

from codemie.configs.customer_config import customer_config
from codemie.configs.logger import logger
from codemie.core.models import ToolCallPolicy
from codemie.rest_api.models.assistant import AssistantBase, ToolPermissionsConfig


class ToolPermissionsService:
    """Resolves effective tool permission settings for an assistant.

    Tool-call confirmation is gated behind the customer ``tool_permissions``
    feature. When the feature is disabled, tools auto-approve regardless of the
    assistant/request/conversation policy.

    When the feature is enabled the base policy is selected first:
      - allow_override=True:  request override or conversation-level policy may only raise
        strictness above the assistant config, never lower it
      - allow_override=False: assistant config only

    The customer floor is then applied as a strictness clamp on top: if the
    customer configures a stricter tool_call_policy, it overrides the resolved
    policy regardless of allow_override.
    """

    _TOOL_PERMISSIONS_FEATURE: ClassVar[str] = "tool_permissions"
    _TOOL_CALL_POLICY_SETTING: ClassVar[str] = "min_tool_call_policy"

    def get_effective_permissions(
        self,
        assistant: AssistantBase,
        tool_call_policy_override: Optional[ToolCallPolicy] = None,
        conversation_policy: Optional[ToolCallPolicy] = None,
    ) -> ToolPermissionsConfig:
        base = assistant.tool_permissions or ToolPermissionsConfig()

        if not customer_config.is_feature_enabled(self._TOOL_PERMISSIONS_FEATURE):
            logger.info("Tool permissions feature disabled; auto-approving tool calls")

            return ToolPermissionsConfig(
                tool_call_policy=ToolCallPolicy.AUTO_APPROVE,
                allow_override=base.allow_override,
            )

        if base.allow_override:
            override = tool_call_policy_override or conversation_policy
            effective_policy = (
                ToolCallPolicy.stricter(override, base.tool_call_policy) if override else base.tool_call_policy
            )
        else:
            effective_policy = base.tool_call_policy

        floor_policy = customer_config.get_feature_setting(
            self._TOOL_PERMISSIONS_FEATURE, self._TOOL_CALL_POLICY_SETTING
        )

        if floor_policy:
            clamped = ToolCallPolicy.stricter(ToolCallPolicy(floor_policy), effective_policy)

            if clamped != effective_policy:
                logger.info(
                    "Customer floor '%s' clamped tool_call_policy from '%s' to '%s'",
                    floor_policy,
                    effective_policy,
                    clamped,
                )

            effective_policy = clamped

        logger.info(
            "Resolved effective tool_call_policy '%s' (allow_override=%s)", effective_policy, base.allow_override
        )

        return ToolPermissionsConfig(
            tool_call_policy=effective_policy,
            allow_override=base.allow_override,
        )
