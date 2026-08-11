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

import pytest
from fastapi import HTTPException, status

from codemie.service.workflow_service import WorkflowService
from codemie.triggers.bindings.webhook import WebhookService


@pytest.fixture
def mock_background_tasks():
    return MagicMock()


@pytest.fixture
def setting_proj_a():
    setting = MagicMock()
    setting.project_name = "project-a"
    setting.user_id = "test_user"
    return setting


def test_handle_datasource_rejects_when_datasource_project_differs_from_webhook_project(
    mock_background_tasks, setting_proj_a
):
    datasource = MagicMock()
    datasource.project_name = "project-b"
    datasource.created_by = MagicMock()

    with patch('codemie.triggers.bindings.webhook.validate_datasource', return_value=datasource):
        with pytest.raises(HTTPException) as exc_info:
            WebhookService.handle_datasource("ds-1", mock_background_tasks, setting_proj_a)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_handle_assistant_rejects_when_assistant_project_differs_from_webhook_project(
    mock_background_tasks, setting_proj_a
):
    assistant = MagicMock()
    assistant.project = "project-b"

    with patch('codemie.triggers.bindings.webhook.validate_assistant', return_value=assistant):
        with pytest.raises(HTTPException) as exc_info:
            WebhookService.handle_assistant("asst-1", b'{}', mock_background_tasks, setting_proj_a)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_handle_workflow_rejects_when_workflow_project_differs_from_webhook_project(
    mock_background_tasks, setting_proj_a
):
    workflow = MagicMock()
    workflow.project = "project-b"

    with patch.object(WorkflowService, 'get_workflow', return_value=workflow):
        with pytest.raises(HTTPException) as exc_info:
            WebhookService.handle_workflow("wf-1", b'{}', mock_background_tasks, setting_proj_a)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
