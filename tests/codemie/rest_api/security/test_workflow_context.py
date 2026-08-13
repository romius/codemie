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

"""Tests for the workflow id context variable."""

from codemie.rest_api.security.workflow_context import (
    get_current_workflow_id,
    set_current_workflow_id,
)


def test_workflow_id_is_readable_until_it_is_cleared():
    assert get_current_workflow_id() is None

    set_current_workflow_id("workflow-1")
    try:
        assert get_current_workflow_id() == "workflow-1"
    finally:
        # Clearing is an unconditional set, never a restore of whatever ran before: a pooled thread
        # must not inherit the previous execution's workflow.
        set_current_workflow_id(None)

    assert get_current_workflow_id() is None


def test_setting_none_keeps_resolution_outside_a_workflow():
    set_current_workflow_id(None)

    assert get_current_workflow_id() is None
