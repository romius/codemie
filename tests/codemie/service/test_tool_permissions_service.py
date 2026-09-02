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

import pytest
from unittest.mock import MagicMock, patch
from codemie.core.models import ToolCallPolicy
from codemie.service.tool_permissions_service import ToolPermissionsService
from codemie.rest_api.models.assistant import ToolPermissionsConfig


@pytest.fixture
def svc():
    return ToolPermissionsService()


def _assistant(policy=ToolCallPolicy.ASK_FOR_APPROVAL, allow_override=True):
    assistant = MagicMock()
    assistant.tool_permissions = ToolPermissionsConfig(
        tool_call_policy=policy,
        allow_override=allow_override,
    )
    return assistant


def _feature_disabled():
    """Master gate off: tool-call confirmation is unavailable for the customer."""
    mock = MagicMock()
    mock.is_feature_enabled.return_value = False
    return patch("codemie.service.tool_permissions_service.customer_config", mock)


def _enabled_no_floor():
    """Feature enabled but no customer floor configured; override chain applies."""
    mock = MagicMock()
    mock.is_feature_enabled.return_value = True
    mock.get_feature_setting.return_value = None
    return patch("codemie.service.tool_permissions_service.customer_config", mock)


def _with_floor(policy: ToolCallPolicy):
    mock = MagicMock()
    mock.is_feature_enabled.return_value = True
    mock.get_feature_setting.return_value = policy
    return patch("codemie.service.tool_permissions_service.customer_config", mock)


# --- master gate: feature disabled forces auto-approve ---


def test_disabled_feature_forces_auto_approve(svc):
    """With the feature off, even a strict assistant/request policy auto-approves."""
    with _feature_disabled():
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.ASK_FOR_APPROVAL),
            tool_call_policy_override=ToolCallPolicy.ASK_FOR_APPROVAL,
        )
    assert result.tool_call_policy == ToolCallPolicy.AUTO_APPROVE


def test_disabled_feature_preserves_allow_override(svc):
    with _feature_disabled():
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.ASK_FOR_APPROVAL, allow_override=False),
        )
    assert result.tool_call_policy == ToolCallPolicy.AUTO_APPROVE
    assert result.allow_override is False


# --- override chain (feature enabled, no floor) ---


def test_returns_assistant_policy_when_no_overrides(svc):
    with _enabled_no_floor():
        result = svc.get_effective_permissions(_assistant(ToolCallPolicy.ASK_FOR_APPROVAL))
    assert result.tool_call_policy == ToolCallPolicy.ASK_FOR_APPROVAL


def test_request_override_raises_above_assistant(svc):
    """A stricter request override may raise the effective policy above the assistant's."""
    with _enabled_no_floor():
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.AUTO_APPROVE),
            tool_call_policy_override=ToolCallPolicy.ASK_FOR_APPROVAL,
        )
    assert result.tool_call_policy == ToolCallPolicy.ASK_FOR_APPROVAL


def test_request_override_cannot_lower_assistant_ceiling(svc):
    """The assistant's confirmation requirement is a hard ceiling a weaker request override cannot disable."""
    with _enabled_no_floor():
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.ASK_FOR_APPROVAL),
            tool_call_policy_override=ToolCallPolicy.AUTO_APPROVE,
        )
    assert result.tool_call_policy == ToolCallPolicy.ASK_FOR_APPROVAL


def test_conversation_policy_raises_above_assistant(svc):
    """A stricter conversation policy may raise the effective policy above the assistant's."""
    with _enabled_no_floor():
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.AUTO_APPROVE),
            conversation_policy=ToolCallPolicy.ASK_FOR_APPROVAL,
        )
    assert result.tool_call_policy == ToolCallPolicy.ASK_FOR_APPROVAL


def test_conversation_policy_cannot_lower_assistant_ceiling(svc):
    """The assistant's confirmation requirement is a hard ceiling a weaker conversation policy cannot disable."""
    with _enabled_no_floor():
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.ASK_FOR_APPROVAL),
            conversation_policy=ToolCallPolicy.AUTO_APPROVE,
        )
    assert result.tool_call_policy == ToolCallPolicy.ASK_FOR_APPROVAL


def test_request_beats_conversation(svc):
    with _enabled_no_floor():
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.AUTO_APPROVE),
            tool_call_policy_override=ToolCallPolicy.ASK_FOR_APPROVAL,
            conversation_policy=ToolCallPolicy.AUTO_APPROVE,
        )
    assert result.tool_call_policy == ToolCallPolicy.ASK_FOR_APPROVAL


def test_allow_override_false_ignores_request(svc):
    with _enabled_no_floor():
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.ASK_FOR_APPROVAL, allow_override=False),
            tool_call_policy_override=ToolCallPolicy.AUTO_APPROVE,
        )
    assert result.tool_call_policy == ToolCallPolicy.ASK_FOR_APPROVAL


def test_none_tool_permissions_uses_defaults(svc):
    assistant = MagicMock()
    assistant.tool_permissions = None
    with _enabled_no_floor():
        result = svc.get_effective_permissions(assistant)
    assert result.tool_call_policy == ToolCallPolicy.AUTO_APPROVE


# --- customer enforcement floor ---


def test_floor_clamps_weaker_request(svc):
    """customer floor=ask_for_approval, request=auto_approve → ask_for_approval"""
    with _with_floor(ToolCallPolicy.ASK_FOR_APPROVAL):
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.AUTO_APPROVE),
            tool_call_policy_override=ToolCallPolicy.AUTO_APPROVE,
        )
    assert result.tool_call_policy == ToolCallPolicy.ASK_FOR_APPROVAL


def test_floor_does_not_override_stricter_request(svc):
    """customer floor=approve_for_me, request=ask_for_approval → ask_for_approval"""
    with _with_floor(ToolCallPolicy.APPROVE_FOR_ME):
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.AUTO_APPROVE),
            tool_call_policy_override=ToolCallPolicy.ASK_FOR_APPROVAL,
        )
    assert result.tool_call_policy == ToolCallPolicy.ASK_FOR_APPROVAL


def test_floor_approve_for_me_clamps_auto_approve(svc):
    with _with_floor(ToolCallPolicy.APPROVE_FOR_ME):
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.AUTO_APPROVE),
            tool_call_policy_override=ToolCallPolicy.AUTO_APPROVE,
        )
    assert result.tool_call_policy == ToolCallPolicy.APPROVE_FOR_ME


def test_floor_applies_even_when_allow_override_false(svc):
    """customer floor beats allow_override=False — floor is a tenant-level control"""
    with _with_floor(ToolCallPolicy.ASK_FOR_APPROVAL):
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.AUTO_APPROVE, allow_override=False),
        )
    assert result.tool_call_policy == ToolCallPolicy.ASK_FOR_APPROVAL


def test_enforcement_enabled_but_no_policy_does_not_clamp(svc):
    with _enabled_no_floor():
        result = svc.get_effective_permissions(
            _assistant(ToolCallPolicy.AUTO_APPROVE),
            tool_call_policy_override=ToolCallPolicy.AUTO_APPROVE,
        )
    assert result.tool_call_policy == ToolCallPolicy.AUTO_APPROVE
