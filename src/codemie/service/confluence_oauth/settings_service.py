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

"""Bridge between the Confluence OAuth flow and TMS-backed per-user token storage."""

from codemie.service.oauth.settings_base import AtlassianOAuthSettingsService


class ConfluenceOAuthSettingsService(AtlassianOAuthSettingsService):
    """Extract credentials from a completed Confluence OAuth flow and persist per-user tokens."""

    provider_label = "Confluence"
