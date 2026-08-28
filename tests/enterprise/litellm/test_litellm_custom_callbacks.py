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

"""Tests for BedrockCostModelFixLogger (litellm_custom_callbacks)."""

import pytest


def _make_reasoning_item(encrypted_content="ZmFrZQ==", rs_id="rs_abc123"):
    return {"type": "reasoning", "id": rs_id, "encrypted_content": encrypted_content}


def _make_user_item(content="follow up"):
    return {"role": "user", "content": content}


@pytest.fixture()
def handler():
    from codemie.enterprise.litellm.litellm_custom_callbacks import BedrockCostModelFixLogger

    return BedrockCostModelFixLogger()


class TestAsyncPreCallDeploymentHookStoreFalse:
    """async_pre_call_deployment_hook strips reasoning items when store=False.

    This hook fires after deployment selection and the litellm_params merge, so
    store=False from a model entry's litellm_params is visible here.
    """

    @pytest.mark.asyncio
    async def test_strips_reasoning_items_when_store_false(self, handler):
        kwargs = {
            "store": False,
            "input": [_make_reasoning_item(), _make_user_item()],
        }
        result = await handler.async_pre_call_deployment_hook(kwargs, "responses")

        assert result["input"] == [_make_user_item()]

    @pytest.mark.asyncio
    async def test_strips_multiple_reasoning_items(self, handler):
        kwargs = {
            "store": False,
            "input": [
                _make_reasoning_item(rs_id="rs_1"),
                _make_user_item("first"),
                _make_reasoning_item(rs_id="rs_2"),
                _make_user_item("second"),
            ],
        }
        result = await handler.async_pre_call_deployment_hook(kwargs, "responses")

        assert result["input"] == [_make_user_item("first"), _make_user_item("second")]

    @pytest.mark.asyncio
    async def test_preserves_reasoning_items_without_encrypted_content(self, handler):
        """Items typed as reasoning but lacking encrypted_content are not stripped."""
        bare_reasoning = {"type": "reasoning", "id": "rs_bare"}
        kwargs = {
            "store": False,
            "input": [bare_reasoning, _make_user_item()],
        }
        result = await handler.async_pre_call_deployment_hook(kwargs, "responses")

        assert bare_reasoning in result["input"]

    @pytest.mark.asyncio
    async def test_noop_when_store_true(self, handler):
        original_input = [_make_reasoning_item(), _make_user_item()]
        kwargs = {"store": True, "input": list(original_input)}
        result = await handler.async_pre_call_deployment_hook(kwargs, "responses")

        assert result["input"] == original_input

    @pytest.mark.asyncio
    async def test_noop_when_store_absent(self, handler):
        original_input = [_make_reasoning_item(), _make_user_item()]
        kwargs = {"input": list(original_input)}
        result = await handler.async_pre_call_deployment_hook(kwargs, "responses")

        assert result["input"] == original_input

    @pytest.mark.asyncio
    async def test_noop_when_input_absent(self, handler):
        kwargs = {"store": False}
        result = await handler.async_pre_call_deployment_hook(kwargs, "responses")

        assert "input" not in result

    @pytest.mark.asyncio
    async def test_noop_when_input_empty(self, handler):
        kwargs = {"store": False, "input": []}
        result = await handler.async_pre_call_deployment_hook(kwargs, "responses")

        assert result["input"] == []

    @pytest.mark.asyncio
    async def test_noop_when_no_reasoning_items_present(self, handler):
        user_items = [_make_user_item("hello"), _make_user_item("world")]
        kwargs = {"store": False, "input": list(user_items)}
        result = await handler.async_pre_call_deployment_hook(kwargs, "responses")

        assert result["input"] == user_items

    @pytest.mark.asyncio
    async def test_call_type_none_also_strips(self, handler):
        """Hook is call-type-agnostic: stripping applies regardless of call_type."""
        kwargs = {
            "store": False,
            "input": [_make_reasoning_item(), _make_user_item()],
        }
        result = await handler.async_pre_call_deployment_hook(kwargs, None)

        assert result["input"] == [_make_user_item()]

    @pytest.mark.asyncio
    async def test_noop_when_all_items_are_reasoning(self, handler):
        """When stripping would empty the input list, leave it unchanged."""
        all_reasoning = [
            _make_reasoning_item(rs_id="rs_1"),
            _make_reasoning_item(rs_id="rs_2"),
        ]
        kwargs = {"store": False, "input": list(all_reasoning)}
        result = await handler.async_pre_call_deployment_hook(kwargs, "responses")

        assert result["input"] == all_reasoning

    @pytest.mark.asyncio
    async def test_store_false_from_litellm_params_is_visible(self, handler):
        """Simulate the primary use case: store=False merged from litellm_params."""
        litellm_params = {"store": False, "model": "azure/gpt-5.1-codex"}
        request_kwargs = {"input": [_make_reasoning_item(), _make_user_item()]}
        merged = {**litellm_params, **request_kwargs}

        result = await handler.async_pre_call_deployment_hook(merged, "responses")

        assert result["input"] == [_make_user_item()]


class TestResolvedDeployment:
    """_resolved_deployment extracts the router-resolved model from metadata."""

    def test_reads_from_litellm_metadata(self, handler):
        request_data = {"litellm_metadata": {"deployment": "bedrock/us.anthropic.claude-sonnet-5"}}
        assert handler._resolved_deployment(request_data) == "bedrock/us.anthropic.claude-sonnet-5"

    def test_reads_from_metadata_fallback(self, handler):
        request_data = {"metadata": {"deployment": "bedrock/eu.anthropic.claude-sonnet-5"}}
        assert handler._resolved_deployment(request_data) == "bedrock/eu.anthropic.claude-sonnet-5"

    def test_prefers_litellm_metadata_over_metadata(self, handler):
        request_data = {
            "litellm_metadata": {"deployment": "bedrock/first"},
            "metadata": {"deployment": "bedrock/second"},
        }
        assert handler._resolved_deployment(request_data) == "bedrock/first"

    def test_returns_none_when_absent(self, handler):
        assert handler._resolved_deployment({}) is None

    def test_returns_none_when_deployment_empty_string(self, handler):
        request_data = {"litellm_metadata": {"deployment": ""}}
        assert handler._resolved_deployment(request_data) is None
