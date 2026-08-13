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

"""Tests for the assistant user mapping model scope column."""

from codemie.rest_api.models.usage.assistant_user_mapping import (
    ASSISTANT_SCOPE,
    AssistantUserMappingSQL,
)


def test_new_mapping_defaults_to_assistant_scope():
    mapping = AssistantUserMappingSQL(assistant_id="a1", user_id="u1", tools_config=[])

    assert ASSISTANT_SCOPE == ""
    assert mapping.workflow_id == ASSISTANT_SCOPE


def test_mapping_accepts_an_explicit_workflow_scope():
    mapping = AssistantUserMappingSQL(assistant_id="a1", user_id="u1", workflow_id="w1", tools_config=[])

    assert mapping.workflow_id == "w1"


def test_unique_constraint_covers_the_scope_column():
    constraint = next(
        arg
        for arg in AssistantUserMappingSQL.__table_args__
        if getattr(arg, "name", None) == "uix_assistant_user_mapping_scope"
    )

    assert [column.name for column in constraint.columns] == ["assistant_id", "user_id", "workflow_id"]
