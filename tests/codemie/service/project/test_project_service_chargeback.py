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

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from codemie.service.project.project_service import ProjectService


class TestProjectServiceChargebackEnabled:
    @patch("codemie.service.project.project_service.application_repository")
    @patch("codemie.service.project.project_service.get_session")
    def test_update_project_passes_chargeback_enabled_to_repository(self, mock_get_session, mock_app_repo):
        mock_session = MagicMock()

        @contextmanager
        def _ctx():
            yield mock_session

        mock_get_session.return_value = _ctx()

        mock_project = SimpleNamespace(
            id="my-project",
            name="my-project",
            display_name=None,
            description="desc",
            cost_center_id=None,
            chargeback_enabled=False,
        )
        mock_app_repo.update_project.return_value = mock_project

        with (
            patch(
                "codemie.service.project.project_service.ProjectService._get_project_for_update",
                return_value=mock_project,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_name",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._validate_project_description",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_display_name",
                return_value=None,
            ),
            patch(
                "codemie.service.project.project_service.ProjectService._resolve_updated_cost_center_id",
                return_value=None,
            ),
            patch("codemie.service.project.project_service.SettingsService"),
        ):
            ProjectService.update_project(
                user=MagicMock(id="admin-1", is_admin=True),
                project_name="my-project",
                chargeback_enabled=True,
            )

        call_kwargs = mock_app_repo.update_project.call_args.kwargs
        assert call_kwargs.get("chargeback_enabled") is True
