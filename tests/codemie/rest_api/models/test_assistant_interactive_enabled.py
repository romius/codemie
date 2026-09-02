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

from codemie.rest_api.models.assistant import AssistantBase, AssistantRequest


def test_assistant_request_accepts_interactive_enabled():
    req = AssistantRequest(name="a", system_prompt="prompt", llm_model_type="gpt-4o", interactive_enabled=True)
    assert req.interactive_enabled is True
    assert "interactive_enabled" in req.model_fields_set
    assert req.model_dump()["interactive_enabled"] is True


def test_assistant_request_interactive_enabled_defaults_false():
    req = AssistantRequest(name="a", system_prompt="prompt", llm_model_type="gpt-4o")
    assert req.interactive_enabled is False
    # Not explicitly set -> partial updates must not clobber an existing True value.
    assert "interactive_enabled" not in req.model_fields_set


def test_assistant_base_declares_interactive_enabled_column():
    field = AssistantBase.model_fields["interactive_enabled"]
    assert field.default is False


def test_update_mapping_applies_interactive_enabled():
    from codemie.rest_api.models.assistant import Assistant

    assistant = Assistant(name="a", description="d", system_prompt="p")
    assert assistant.interactive_enabled is False
    request = AssistantRequest(name="a", system_prompt="p", llm_model_type="gpt-4o", interactive_enabled=True)
    assistant._map_assistant_request(request)
    assert assistant.interactive_enabled is True


def test_legacy_interactive_features_payload_is_ignored():
    """The removed legacy toggle payload must not resurrect a field or break parsing."""
    req = AssistantRequest(
        name="a", system_prompt="prompt", llm_model_type="gpt-4o", interactive_features={"choice": True}
    )
    assert not hasattr(req, "interactive_features")
    assert req.interactive_enabled is False
