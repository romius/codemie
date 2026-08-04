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

"""Tests for the configurable skill content-length limit (EPMCDME-13541)."""

import pytest
from pydantic import ValidationError

import codemie.rest_api.models.skill as skill_models
from codemie.rest_api.models.skill import (
    DEFAULT_CONTENT_LENGTH,
    MIN_CONTENT_LENGTH,
    SkillCreateRequest,
    SkillInstructionsGenerateRequest,
    SkillInstructionsGenerateResponse,
    SkillUpdateRequest,
    get_skill_max_content_length,
)


def _create_payload(content: str) -> dict:
    return {
        "name": "test-skill",
        "description": "A valid skill description.",
        "content": content,
        "project": "demo",
    }


class TestEffectiveLimitAccessor:
    def test_default_constant_is_30000(self):
        assert DEFAULT_CONTENT_LENGTH == 30000

    def test_min_content_length_constant_is_100(self):
        assert MIN_CONTENT_LENGTH == 100

    def test_accessor_returns_default_when_unset(self):
        # No DB / key unset -> get_typed_value_safe falls back to the default.
        assert get_skill_max_content_length() == 30000

    def test_accessor_reads_configured_value(self, monkeypatch):
        monkeypatch.setattr(
            skill_models.DynamicConfigService,
            "get_typed_value_safe",
            classmethod(lambda cls, key, expected_type, default=None: 50000),
        )
        assert get_skill_max_content_length() == 50000

    def test_accessor_falls_back_on_error(self, monkeypatch):
        def _boom(cls, key, expected_type, default=None):
            return default

        monkeypatch.setattr(skill_models.DynamicConfigService, "get_typed_value_safe", classmethod(_boom))
        assert get_skill_max_content_length() == 30000

    def test_accessor_clamps_zero_to_default(self, monkeypatch):
        # CR-001: limit=0 must never DoS all write endpoints.
        monkeypatch.setattr(
            skill_models.DynamicConfigService,
            "get_typed_value_safe",
            classmethod(lambda cls, key, expected_type, default=None: 0),
        )
        assert get_skill_max_content_length() == DEFAULT_CONTENT_LENGTH

    def test_accessor_clamps_negative_to_default(self, monkeypatch):
        # CR-001: negative limits are nonsensical — fall back to the safe default.
        monkeypatch.setattr(
            skill_models.DynamicConfigService,
            "get_typed_value_safe",
            classmethod(lambda cls, key, expected_type, default=None: -500),
        )
        assert get_skill_max_content_length() == DEFAULT_CONTENT_LENGTH

    def test_accessor_clamps_below_min_to_default(self, monkeypatch):
        # CR-002: limit < MIN_CONTENT_LENGTH creates an impossible constraint
        # (max < min); clamp to the safe default instead.
        monkeypatch.setattr(
            skill_models.DynamicConfigService,
            "get_typed_value_safe",
            classmethod(lambda cls, key, expected_type, default=None: 50),
        )
        assert get_skill_max_content_length() == DEFAULT_CONTENT_LENGTH

    def test_accessor_guards_bool_true(self, monkeypatch):
        # CR-004: bool is a subclass of int; True (==1) stored as BOOL type must
        # not silently become the effective limit.
        monkeypatch.setattr(
            skill_models.DynamicConfigService,
            "get_typed_value_safe",
            classmethod(lambda cls, key, expected_type, default=None: True),
        )
        assert get_skill_max_content_length() == DEFAULT_CONTENT_LENGTH

    def test_accessor_guards_bool_false(self, monkeypatch):
        # CR-004: False (==0) is equally invalid as an effective content limit.
        monkeypatch.setattr(
            skill_models.DynamicConfigService,
            "get_typed_value_safe",
            classmethod(lambda cls, key, expected_type, default=None: False),
        )
        assert get_skill_max_content_length() == DEFAULT_CONTENT_LENGTH


class TestCreateValidation:
    def test_content_at_default_limit_ok(self):
        SkillCreateRequest(**_create_payload("x" * 30000))

    def test_content_over_default_limit_rejected(self):
        with pytest.raises(ValidationError) as exc:
            SkillCreateRequest(**_create_payload("x" * 30001))
        assert "must not exceed 30000" in str(exc.value)

    def test_increased_limit_allows_larger_content(self, monkeypatch):
        monkeypatch.setattr(skill_models, "get_skill_max_content_length", lambda: 50000)
        SkillCreateRequest(**_create_payload("x" * 40000))  # would fail under a static 30000 cap

    def test_below_min_length_still_rejected(self):
        with pytest.raises(ValidationError):
            SkillCreateRequest(**_create_payload("x" * 10))


class TestUpdateValidation:
    def test_update_omitting_content_passes_even_when_limit_low(self, monkeypatch):
        monkeypatch.setattr(skill_models, "get_skill_max_content_length", lambda: 100)
        # No content provided -> limit must not be enforced.
        SkillUpdateRequest(name="renamed-skill")

    def test_update_over_limit_content_rejected(self):
        with pytest.raises(ValidationError) as exc:
            SkillUpdateRequest(content="x" * 30001)
        assert "must not exceed 30000" in str(exc.value)

    def test_update_within_limit_ok(self):
        SkillUpdateRequest(content="x" * 30000)


class TestGenerateModelsValidation:
    def test_generate_request_existing_instructions_over_limit_accepted(self):
        # CR-002 (round 2): the generate/refine endpoint reads existing skill content back from
        # storage; enforcing the current effective limit here would strand users whose skills
        # exceed a newly-lowered limit — the exact case where AI-assisted trimming is most useful.
        req = SkillInstructionsGenerateRequest(existing_instructions="x" * 50000)
        assert len(req.existing_instructions or "") == 50000

    def test_generate_response_no_length_constraint(self):
        # CR-005: the response model carries no field_validator so over-limit LLM
        # output does not produce an HTTP 500; limit checks belong in the service layer.
        resp = SkillInstructionsGenerateResponse(instructions="x" * 50000)
        assert len(resp.instructions) == 50000


class TestReadSafety:
    def test_detail_response_allows_oversized_stored_content(self):
        # A pre-existing skill stored above a (possibly lowered) limit must remain readable.
        from datetime import UTC, datetime

        from codemie.rest_api.models.skill import SkillDetailResponse, SkillVisibility

        resp = SkillDetailResponse(
            id="s1",
            name="big-skill",
            description="A large pre-existing skill.",
            content="x" * 40000,
            project="demo",
            visibility=SkillVisibility.PRIVATE,
            categories=[],
            created_date=datetime.now(UTC),
        )
        assert len(resp.content) == 40000
