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

"""Custom LiteLLM proxy callbacks.

``async_pre_call_deployment_hook`` fires AFTER deployment selection and the
``{**litellm_params, **kwargs}`` merge in the router, so ``store=False`` from a model
entry's ``litellm_params`` is visible here.  ``async_pre_call_hook`` fires earlier — before
that merge — and would only see ``store=False`` when the client sends it explicitly.
"""

from typing import Any, AsyncGenerator, Dict, Optional

from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy._types import UserAPIKeyAuth
from litellm.utils import CallTypes


class BedrockCostModelFixLogger(CustomLogger):
    # ------------------------------------------------------------------ #
    # pre-call (post-deployment): strip reasoning items when store=False  #
    # ------------------------------------------------------------------ #

    async def async_pre_call_deployment_hook(
        self,
        kwargs: Dict[str, Any],
        call_type: Optional[CallTypes],
    ) -> Optional[dict]:
        """Strip encrypted reasoning items when store=False.

        When a model entry has ``litellm_params.store: false``, LiteLLM merges
        it into the request kwargs at this deployment-selection hook. Reasoning
        items with ``encrypted_content`` reference server-side state that was
        never persisted (``store=False`` on turn 1), so Azure rejects them with
        400 on turn 2+. This hook strips such items to make the turn stateless.
        """
        if kwargs.get("store") is not False:
            return kwargs

        input_items = kwargs.get("input")
        if not isinstance(input_items, list):
            return kwargs

        filtered = [
            item
            for item in input_items
            if not (isinstance(item, dict) and item.get("type") == "reasoning" and item.get("encrypted_content"))
        ]

        if 0 < len(filtered) < len(input_items):
            kwargs["input"] = filtered

        return kwargs

    # ------------------------------------------------------------------ #
    # post-call streaming: fix Bedrock cost model name                    #
    # ------------------------------------------------------------------ #

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
