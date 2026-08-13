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

from datetime import datetime

from codemie.rest_api.models.conversation import ConversationListItem, ConversationResponse, SearchResultItem


def test_conversation_list_item_trims_folder():
    item = ConversationListItem(id="1", folder="  FAQ  ", date=datetime(2026, 1, 1))
    assert item.folder == "FAQ"


def test_conversation_list_item_none_folder_stays_none():
    item = ConversationListItem(id="1", folder=None, date=datetime(2026, 1, 1))
    assert item.folder is None


def test_conversation_response_trims_folder():
    response = ConversationResponse(conversation_id="1", folder="  FAQ  ")
    assert response.folder == "FAQ"


def test_search_result_item_trims_folder():
    item = SearchResultItem(id="1", name="chat", updated_at=datetime(2026, 1, 1), type="chat", folder="  FAQ  ")
    assert item.folder == "FAQ"
