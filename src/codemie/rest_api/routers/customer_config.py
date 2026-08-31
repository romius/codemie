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

from fastapi import APIRouter, Depends, status
from fastapi.encoders import jsonable_encoder
from codemie.configs.customer_config import Component
from typing import List
from codemie.rest_api.models.application import Application
from codemie.rest_api.models.customer_config import (
    SettingDeclarationResponse,
    SettingUpdateRequest,
    SettingUpdateResponse,
)
from codemie.rest_api.security.authentication import (
    authenticate,
    require_customer_config_write,
)
from codemie.rest_api.security.user import User
from codemie.service.customer_config_service import (
    list_settings,
    reset_setting,
    resolve_components,
    save_setting,
)

_SETTING_PATH = "/config/declarations/{component_id}"

router = APIRouter(
    tags=["customer_config"],
    prefix="/v1",
    dependencies=[],
)


@router.get("/config", response_model=List[Component], response_model_exclude_none=True)
async def get_config():
    return jsonable_encoder(await resolve_components())


@router.get(
    "/config/declarations",
    response_model=List[SettingDeclarationResponse],
    dependencies=[Depends(authenticate), Depends(require_customer_config_write)],
)
async def get_declarations():
    """Settings declared as dynamic, with their current value and override marker."""
    return jsonable_encoder(await list_settings())


@router.put(
    _SETTING_PATH,
    response_model=SettingUpdateResponse,
    dependencies=[Depends(authenticate), Depends(require_customer_config_write)],
)
async def update_setting(
    component_id: str,
    request: SettingUpdateRequest,
    current_user: User = Depends(authenticate),
):
    """Override one declared setting. A payload failing its declaration stores nothing."""
    settings = await save_setting(component_id, request.settings, current_user)
    return SettingUpdateResponse(component_id=component_id, settings=settings)


@router.delete(
    _SETTING_PATH,
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(authenticate), Depends(require_customer_config_write)],
)
async def delete_setting(component_id: str, current_user: User = Depends(authenticate)):
    """Drop the override so the value comes from the deployment default again."""
    await reset_setting(component_id, current_user)


@router.get("/applications", response_model=List[Application])
async def get_applications():
    components = await resolve_components()

    # Filter for components with IDs starting with 'applications:'
    application_components = [component for component in components if component.id.startswith('applications:')]

    applications = []
    for app in application_components:
        slug = app.id.replace('applications:', '', 1)

        applications.append(
            Application(
                name=app.settings.name,
                description=app.settings.description or '',
                slug=slug,
                entry=app.settings.url,
                type=app.settings.type,
                created_by=app.settings.created_by,
                icon_url=app.settings.icon_url,
                arguments=getattr(app.settings, "arguments", None),
            )
        )

    return jsonable_encoder(applications)
