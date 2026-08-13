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

import pytest
from pydantic import ValidationError

from codemie.core.models import (
    CreateConversationRequest,
    UpdateConversationFolderRequest,
    UpdateConversationRequest,
)


def test_update_conversation_folder_request_trims_whitespace():
    request = UpdateConversationFolderRequest(folder="  FAQ  ")
    assert request.folder == "FAQ"


def test_update_conversation_folder_request_rejects_empty_after_trim():
    with pytest.raises(ValidationError):
        UpdateConversationFolderRequest(folder="   ")


def test_create_conversation_request_trims_optional_folder():
    request = CreateConversationRequest(folder="  FAQ  ")
    assert request.folder == "FAQ"


def test_create_conversation_request_normalizes_empty_after_trim_to_none():
    request = CreateConversationRequest(folder="   ")
    assert request.folder is None


def test_create_conversation_request_allows_none_folder():
    request = CreateConversationRequest(folder=None)
    assert request.folder is None


def test_update_conversation_request_trims_optional_folder():
    request = UpdateConversationRequest(folder="  FAQ  ")
    assert request.folder == "FAQ"


def test_update_conversation_request_normalizes_empty_after_trim_to_none():
    request = UpdateConversationRequest(folder="   ")
    assert request.folder is None
