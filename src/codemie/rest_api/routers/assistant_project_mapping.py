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

"""Router for assistant project feature mapping endpoints."""

from fastapi import APIRouter, Depends, Query, status

from codemie.configs import logger
from codemie.configs.customer_config import customer_config
from codemie.core.exceptions import ExtendedHTTPException
from codemie.core.models import BaseResponse
from codemie.rest_api.models.base import PaginatedListResponse
from codemie.rest_api.models.usage.assistant_project_mapping import (
    AssistantProjectFeature,
    AssistantProjectMappingRequest,
)
from codemie.rest_api.routers.assistant import _get_assistant_by_id_or_raise
from codemie.rest_api.routers.utils import raise_forbidden, raise_not_found
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.security.user import User
from codemie.service.assistant.assistant_project_mapping_service import (
    AssistantProjectMappingForbidden,
    AssistantProjectMappingNotFound,
    AssistantProjectMappingService,
)

_TEAMS_BOT_FEATURE = "teamsBotIntegration"
_TRY_AGAIN = "Try again later."


def _require_teams_bot_feature() -> None:
    if not customer_config.is_feature_enabled(_TEAMS_BOT_FEATURE):
        raise ExtendedHTTPException(
            code=status.HTTP_403_FORBIDDEN,
            message="Feature not available",
            details="Teams Bot Integration is not enabled for this customer.",
            help="Contact your system administrator to enable the 'features:teamsBotIntegration' component.",
        )


router = APIRouter(
    tags=["Assistant Project Mappings"],
    prefix="/v1",
    dependencies=[],
)


@router.get(
    "/assistants/projects/mapping",
    status_code=status.HTTP_200_OK,
    response_model=PaginatedListResponse,
    response_model_by_alias=True,
)
def list_project_assistants(
    feature: AssistantProjectFeature = Query(...),
    project: str = Query(...),
    page: int = Query(0, ge=0),
    per_page: int = Query(12, ge=1, le=10000),
    user: User = Depends(authenticate),
):
    """List assistants enabled for a project feature. Accessible to project members."""
    _require_teams_bot_feature()

    try:
        return AssistantProjectMappingService().list(
            project_name=project,
            feature=feature.value,
            user=user,
            page=page,
            per_page=per_page,
        )
    except AssistantProjectMappingNotFound as e:
        raise_not_found(e.resource_id, e.resource_type)
    except AssistantProjectMappingForbidden as e:
        raise_forbidden(e.action)
    except Exception as e:
        logger.error(f"Error listing project feature assistants: {e}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to list assistants",
            details=str(e),
            help=_TRY_AGAIN,
        ) from e


@router.post(
    "/assistants/{assistant_id}/projects/mapping",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
    response_model_by_alias=True,
)
def enable_assistant_for_project(
    assistant_id: str,
    request: AssistantProjectMappingRequest,
    user: User = Depends(authenticate),
):
    """Enable an assistant for a project feature. Requires project admin."""
    _require_teams_bot_feature()
    _get_assistant_by_id_or_raise(assistant_id)

    try:
        AssistantProjectMappingService().enable(
            assistant_id=assistant_id,
            project_name=request.project_name,
            feature=request.feature.value,
            user=user,
        )
        logger.info(
            f"assistant_project_mapping_enabled: project={request.project_name}, "
            f"assistant_id={assistant_id}, feature={request.feature.value}, "
            f"by={user.id}, domain=project_management"
        )
        return BaseResponse(message="Assistant enabled for project feature successfully")
    except AssistantProjectMappingNotFound as e:
        raise_not_found(e.resource_id, e.resource_type)
    except AssistantProjectMappingForbidden as e:
        raise_forbidden(e.action)
    except Exception as e:
        logger.error(f"Error enabling assistant for project feature: {e}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to enable assistant",
            details=str(e),
            help=_TRY_AGAIN,
        ) from e


@router.delete(
    "/assistants/{assistant_id}/projects/mapping",
    status_code=status.HTTP_200_OK,
    response_model=BaseResponse,
    response_model_by_alias=True,
)
def disable_assistant_for_project(
    assistant_id: str,
    project: str = Query(...),
    feature: AssistantProjectFeature = Query(...),
    user: User = Depends(authenticate),
):
    """Disable an assistant for a project feature. Requires project admin."""
    _require_teams_bot_feature()
    _get_assistant_by_id_or_raise(assistant_id)

    try:
        AssistantProjectMappingService().disable(
            assistant_id=assistant_id,
            project_name=project,
            feature=feature.value,
            user=user,
        )
        logger.info(
            f"assistant_project_mapping_disabled: project={project}, "
            f"assistant_id={assistant_id}, feature={feature.value}, "
            f"by={user.id}, domain=project_management"
        )
        return BaseResponse(message="Assistant disabled for project feature successfully")
    except AssistantProjectMappingNotFound as e:
        raise_not_found(e.resource_id, e.resource_type)
    except AssistantProjectMappingForbidden as e:
        raise_forbidden(e.action)
    except Exception as e:
        logger.error(f"Error disabling assistant for project feature: {e}", exc_info=True)
        raise ExtendedHTTPException(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Failed to disable assistant",
            details=str(e),
            help=_TRY_AGAIN,
        ) from e
