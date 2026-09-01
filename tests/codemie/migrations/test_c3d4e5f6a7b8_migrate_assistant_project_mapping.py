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

"""Unit tests for c3d4e5f6a7b8 migration transform logic."""


def _import_transforms():
    from external.alembic.versions.c3d4e5f6a7b8_migrate_assistant_project_mapping_to_settings import (
        _group_assistant_ids,
        _merge_assistant_ids,
    )

    return _group_assistant_ids, _merge_assistant_ids


def test_group_assistant_ids_dedups_within_project():
    _group_assistant_ids, _ = _import_transforms()
    rows = [("proj1", "a1"), ("proj1", "a2"), ("proj1", "a1")]
    result = _group_assistant_ids(rows)
    assert result == {"proj1": ["a1", "a2"]}


def test_group_assistant_ids_splits_by_project_preserving_order():
    _group_assistant_ids, _ = _import_transforms()
    rows = [("proj1", "a1"), ("proj2", "b1"), ("proj1", "a2"), ("proj2", "b2")]
    result = _group_assistant_ids(rows)
    assert result == {"proj1": ["a1", "a2"], "proj2": ["b1", "b2"]}


def test_group_assistant_ids_empty_rows():
    _group_assistant_ids, _ = _import_transforms()
    assert _group_assistant_ids([]) == {}


def test_merge_assistant_ids_appends_new_and_dedupes():
    _, _merge_assistant_ids = _import_transforms()
    existing = [{"key": "assistant_ids", "value": ["a1", "a2"]}]
    result = _merge_assistant_ids(existing, ["a2", "a3"])
    assert result == [{"key": "assistant_ids", "value": ["a1", "a2", "a3"]}]


def test_merge_assistant_ids_preserves_other_credential_entries():
    _, _merge_assistant_ids = _import_transforms()
    existing = [
        {"key": "webhook_url", "value": "https://example.test"},
        {"key": "assistant_ids", "value": ["a1"]},
    ]
    result = _merge_assistant_ids(existing, ["a2"])
    assert result[0] == {"key": "webhook_url", "value": "https://example.test"}
    assert result[1] == {"key": "assistant_ids", "value": ["a1", "a2"]}


def test_merge_assistant_ids_adds_entry_when_missing():
    _, _merge_assistant_ids = _import_transforms()
    existing = [{"key": "webhook_url", "value": "https://example.test"}]
    result = _merge_assistant_ids(existing, ["a1"])
    assert {"key": "assistant_ids", "value": ["a1"]} in result
    assert len(result) == 2


def test_merge_assistant_ids_does_not_mutate_input():
    _, _merge_assistant_ids = _import_transforms()
    existing = [{"key": "assistant_ids", "value": ["a1"]}]
    _merge_assistant_ids(existing, ["a2"])
    assert existing == [{"key": "assistant_ids", "value": ["a1"]}]
