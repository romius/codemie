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

from unittest.mock import MagicMock, patch

from codemie.repository.assistants.assistant_clone_event_repository import (
    AssistantCloneEventRepository,
)


@patch("codemie.repository.assistants.assistant_clone_event_repository.Session")
def test_log_clone_event_inserts_row_without_dedup_check(mock_session_class):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session

    AssistantCloneEventRepository().log_clone_event("assistant-1", "user-1")

    assert mock_session.add.called
    assert mock_session.commit.called


@patch("codemie.repository.assistants.assistant_clone_event_repository.Session")
def test_log_clone_event_allows_repeat_events_from_same_user(mock_session_class):
    """Two clone events from the same user must both be logged — no dedup."""
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session

    repo = AssistantCloneEventRepository()
    repo.log_clone_event("assistant-1", "user-1")
    repo.log_clone_event("assistant-1", "user-1")

    assert mock_session.add.call_count == 2
    assert mock_session.commit.call_count == 2


@patch("codemie.repository.assistants.assistant_clone_event_repository.Session")
def test_get_clone_count_returns_row_count(mock_session_class):
    mock_session = MagicMock()
    mock_session_class.return_value.__enter__.return_value = mock_session
    mock_session.exec.return_value.one.return_value = 3

    result = AssistantCloneEventRepository().get_clone_count("assistant-1")

    assert result == 3
