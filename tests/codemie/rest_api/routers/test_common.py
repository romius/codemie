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

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from codemie.rest_api.routers.common import router

app = FastAPI()
app.include_router(router)


@pytest.mark.asyncio
async def test_app_info_advertises_default_file_datasource_max_upload_count():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/info")

    assert response.status_code == 200
    assert response.json()["fileDatasourceMaxUploadCount"] == 10


@pytest.mark.asyncio
async def test_app_info_advertises_configured_file_datasource_max_upload_count():
    with patch("codemie.rest_api.routers.common.config.FILE_DATASOURCE_MAX_UPLOAD_COUNT", 50):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/v1/info")

    assert response.status_code == 200
    assert response.json()["fileDatasourceMaxUploadCount"] == 50
