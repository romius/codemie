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

from __future__ import annotations

from codemie.core.models import InfoResponse


def test_info_response_has_no_deployed_at_field():
    resp = InfoResponse(message="Codemie", version="1.0.0", description="desc")
    data = resp.model_dump(by_alias=True)
    assert "deployedAt" not in data
    assert "deployed_at" not in data
    assert "deployed_at" not in InfoResponse.model_fields
