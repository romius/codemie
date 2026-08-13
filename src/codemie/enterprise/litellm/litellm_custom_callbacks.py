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

"""Custom LiteLLM proxy callback: fix streaming cost model name.

The proxy's SSE streaming cost injection computes cost from the client-facing alias
(``request_data["model"]``, e.g. ``claude-sonnet-5``) instead of the router-resolved
deployment (e.g. ``bedrock/us.anthropic.claude-sonnet-5``). For Bedrock models this hits the
Anthropic-direct price table and diverges from the dashboard cost (which is computed from the
resolved model via ``logging_obj``).

This callback runs ``async_post_call_streaming_iterator_hook`` *before* the proxy's cost
injection and rewrites ``request_data["model"]`` to the resolved deployment so both paths use
the same (correct) pricing. Spend/dashboard logging keys off ``logging_obj``, not
``request_data["model"]``, so the change is scoped to the streaming cost calculation.
"""

from typing import Any, AsyncGenerator, Optional

from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy._types import UserAPIKeyAuth


class BedrockCostModelFixLogger(CustomLogger):
    """Point streaming cost injection at the router-resolved deployment model."""

    @staticmethod
    def _resolved_deployment(request_data: dict) -> Optional[str]:
        # New endpoints (e.g. /v1/messages) use "litellm_metadata"; others use "metadata".
        for key in ("litellm_metadata", "metadata"):
            md = request_data.get(key)
            if isinstance(md, dict):
                deployment = md.get("deployment")
                if isinstance(deployment, str) and deployment:
                    return deployment
        return None

    async def async_post_call_streaming_iterator_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        response: Any,
        request_data: dict,
    ) -> AsyncGenerator[Any, None]:
        deployment = self._resolved_deployment(request_data)
        if deployment:
            # Consumed downstream at common_request_processing.py -> cost injection.
            request_data["model"] = deployment
        async for chunk in response:
            yield chunk
