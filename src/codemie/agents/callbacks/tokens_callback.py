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

import contextlib
import uuid
from typing import Any, Optional
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import LLMResult

from codemie.configs import config, logger
from codemie.core.llm_cache import is_litellm_proxy_cache_hit, is_llm_cache_hit
from codemie.core.utils import calculate_token_cost
from codemie.service.request_summary_manager import request_summary_manager, LLMRun
from codemie.service.llm_service.llm_service import llm_service


class TokensCalculationCallback(AsyncCallbackHandler):
    def __init__(self, request_id: str, llm_model: str):
        super().__init__()
        self.internal_run_id = str(uuid.uuid4())
        self.request_id = request_id
        self.llm_model = llm_model
        self.input_tokens = 0
        self.output_tokens = 0

    @staticmethod
    def _extract_proxy_cost(generation_info: dict) -> Optional[float]:
        """Return the pre-calculated cost from LiteLLM proxy generation_info, or None."""
        streaming_cost = generation_info.get("litellm_cost")
        if streaming_cost is not None:
            with contextlib.suppress(ValueError, TypeError):
                return float(streaming_cost)
        cost_str = generation_info.get("headers", {}).get("x-litellm-response-cost")
        if cost_str:
            with contextlib.suppress(ValueError, TypeError):
                return float(cost_str)
        return None

    @staticmethod
    def _iter_gen_results(response: LLMResult):
        for gen in response.generations:
            yield from gen

    def _calculate_cost(
        self,
        proxy_cost: Optional[float],
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int,
        cache_creation_tokens: int,
    ) -> tuple[float, float, float]:
        if proxy_cost is not None:
            return proxy_cost, 0.0, 0.0
        model_costs = llm_service.get_model_cost(self.llm_model)
        return calculate_token_cost(
            llm_model=self.llm_model,
            cost_config=model_costs,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            cache_creation_tokens=cache_creation_tokens,
        )

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Run when LLM ends running."""
        try:
            if is_llm_cache_hit(response):
                logger.debug(
                    "Skipping LangGraph usage tracking for LiteLLM cache hit: "
                    f"request_id={self.request_id} model={self.llm_model} estimated_spend_skipped=unknown"
                )
                return
            input_tokens = 0
            output_tokens = 0
            cached_tokens = 0
            cache_creation_tokens = 0
            proxy_cost: Optional[float] = None
            for gen_result in self._iter_gen_results(response):
                if gen_result.generation_info and is_litellm_proxy_cache_hit(gen_result.generation_info):
                    logger.debug(
                        "Skipping LangGraph usage tracking for LiteLLM proxy cache hit (x-litellm-cache-key): "
                        f"request_id={self.request_id} model={self.llm_model}"
                    )
                    continue
                if gen_result.message and gen_result.message.usage_metadata:
                    usage_metadata: UsageMetadata = gen_result.message.usage_metadata
                    input_tokens += usage_metadata.get("input_tokens", 0)
                    output_tokens += usage_metadata.get("output_tokens", 0)
                    cached_tokens += usage_metadata.get("input_token_details", {}).get("cache_read", 0)
                    cache_creation_tokens += usage_metadata.get("input_token_details", {}).get("cache_creation", 0)
                    logger.debug(f"On LLM End. Usage metadata: {usage_metadata}")
                if (
                    proxy_cost is None
                    and gen_result.generation_info
                    and config.LLM_PROXY_ENABLED
                    and config.LLM_PROXY_TRACK_USAGE
                ):
                    proxy_cost = self._extract_proxy_cost(gen_result.generation_info)

            if not (input_tokens or output_tokens) and proxy_cost is None:
                return

            money_spent, cached_tokens_money_spent, cached_tokens_creation_cost = self._calculate_cost(
                proxy_cost, input_tokens, output_tokens, cached_tokens, cache_creation_tokens
            )

            llm_run = LLMRun(
                run_id=str(run_id),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                money_spent=money_spent,
                cached_tokens_money_spent=cached_tokens_money_spent,
                cached_tokens_creation_cost=cached_tokens_creation_cost,
                llm_model=self.llm_model,
            )

            request_summary_manager.update_llm_run(request_id=self.request_id, llm_run=llm_run)
        except Exception as e:
            logger.error(f"Error while calculating tokens: {str(e)}")

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        """Run when LLM errors. Capture partial token usage if the provider returns it."""
        try:
            logger.warning(f"LLM error for run {run_id}, model {self.llm_model}: {error}")
            response: Optional[LLMResult] = kwargs.get("response")
            if response is None:
                return
            if is_llm_cache_hit(response):
                logger.debug(
                    "Skipping LangGraph error usage tracking for LiteLLM cache hit: "
                    f"request_id={self.request_id} model={self.llm_model} estimated_spend_skipped=unknown"
                )
                return

            input_tokens = 0
            output_tokens = 0
            cached_tokens = 0
            cache_creation_tokens = 0
            for gen_result in self._iter_gen_results(response):
                if gen_result.generation_info and is_litellm_proxy_cache_hit(gen_result.generation_info):
                    logger.debug(
                        "Skipping LangGraph error usage tracking for LiteLLM proxy cache hit"
                        f" (x-litellm-cache-key): request_id={self.request_id} model={self.llm_model}"
                    )
                    continue
                if gen_result.message and gen_result.message.usage_metadata:
                    usage_metadata: UsageMetadata = gen_result.message.usage_metadata
                    input_tokens += usage_metadata.get("input_tokens", 0)
                    output_tokens += usage_metadata.get("output_tokens", 0)
                    cached_tokens += usage_metadata.get("input_token_details", {}).get("cache_read", 0)
                    cache_creation_tokens += usage_metadata.get("input_token_details", {}).get("cache_creation", 0)

            if not (input_tokens or output_tokens):
                return

            model_costs = llm_service.get_model_cost(self.llm_model)
            money_spent, cached_tokens_money_spent, cached_tokens_creation_cost = calculate_token_cost(
                llm_model=self.llm_model,
                cost_config=model_costs,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                cache_creation_tokens=cache_creation_tokens,
            )

            llm_run = LLMRun(
                run_id=str(run_id),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                money_spent=money_spent,
                cached_tokens_money_spent=cached_tokens_money_spent,
                cached_tokens_creation_cost=cached_tokens_creation_cost,
                llm_model=self.llm_model,
            )
            request_summary_manager.update_llm_run(request_id=self.request_id, llm_run=llm_run)
        except Exception as e:
            logger.error(f"Error while handling LLM error token calculation: {str(e)}")

    def on_chat_model_start(self, serialized: dict[str, Any], messages: list[list[BaseMessage]], **kwargs: Any) -> None:
        """Run when LLM starts running.

        Args:
            serialized (Dict[str, Any]): The serialized LLM.
            messages (List[List[BaseMessage]]): The messages to run.
            **kwargs (Any): Additional keyword arguments.
        """
