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

"""Unit tests for the d1c2b3a4e5f6 downgrade: it must restore the assistant toggle.

The downgrade runs against a database where ``interactive_enabled`` still exists
(revision e7f8a9b0c1d2 drops it only afterwards), so it is the last chance to move
the toggle back into the legacy JSONB column.
"""

import importlib


class _OpRecorder:
    """Minimal stand-in for alembic's `op`, recording the calls a migration makes."""

    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def add_column(self, table, column):
        self.calls.append(("add_column", (table, column.name)))

    def drop_column(self, table, column):
        self.calls.append(("drop_column", (table, column)))

    def execute(self, statement):
        self.calls.append(("execute", str(statement)))


def _module():
    return importlib.import_module("external.alembic.versions.d1c2b3a4e5f6_drop_interactive_features_from_assistants")


def _run_downgrade(monkeypatch) -> _OpRecorder:
    module = _module()
    recorder = _OpRecorder()
    monkeypatch.setattr(module, "op", recorder)
    module.downgrade()
    return recorder


def test_downgrade_re_adds_the_column_before_backfilling(monkeypatch):
    calls = _run_downgrade(monkeypatch).calls
    assert [name for name, _ in calls] == ["add_column", "execute"]
    assert calls[0][1] == ("assistants", "interactive_features")


def test_downgrade_restores_the_legacy_groups_for_enabled_assistants(monkeypatch):
    statement = _run_downgrade(monkeypatch).calls[1][1]
    assert "UPDATE assistants" in statement
    assert "interactive_enabled = true" in statement
    for group in ("action_buttons", "choice", "short_forms"):
        assert f'"{group}": true' in statement


def test_downgrade_leaves_disabled_assistants_null(monkeypatch):
    statement = _run_downgrade(monkeypatch).calls[1][1]
    # Only enabled rows are written; everything else keeps the NULL the add_column left.
    assert "WHERE interactive_enabled = true" in statement
