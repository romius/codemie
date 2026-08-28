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

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from codemie.core.exceptions import ExtendedHTTPException
from codemie.service.cost_center_service import CostCenterService


def _admin_user():
    user = MagicMock()
    user.is_admin_or_maintainer = True
    user.id = "actor-1"
    return user


def _cost_center(name="eng-123"):
    cc = MagicMock()
    cc.id = uuid4()
    cc.name = name
    return cc


class TestCostCenterService:
    @pytest.mark.parametrize(
        "name",
        [
            "eng-123",
            "aa-bb2",
            "1a-2b",
        ],
    )
    def test_validate_name_accepts_single_separator_pattern(self, name: str):
        assert CostCenterService.validate_name(name) == name

    @pytest.mark.parametrize(
        "name",
        [
            "eng--123",
            "eng-123-extra",
            "eng_123",
            "-eng123",
            "eng123-",
        ],
    )
    def test_validate_name_rejects_invalid_separator_pattern(self, name: str):
        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.validate_name(name)

        assert exc_info.value.code == 400
        assert exc_info.value.message == "Invalid cost center name"


class TestCostCenterServiceLogging:
    @patch("codemie.service.cost_center_service.logger")
    @patch("codemie.service.cost_center_service.cost_center_repository")
    def test_create_logs_on_success(self, mock_repo, mock_logger):
        """create emits a logger.info containing 'cost_center_created'."""
        mock_repo.get_by_name_case_insensitive.return_value = None
        mock_repo.create.return_value = _cost_center()

        CostCenterService.create(MagicMock(), user=_admin_user(), name="eng-123", description="desc")

        mock_logger.info.assert_called_once()
        assert "cost_center_created" in mock_logger.info.call_args[0][0]

    @patch("codemie.service.cost_center_service.logger")
    @patch("codemie.service.cost_center_service.cost_center_repository")
    def test_update_logs_on_success(self, mock_repo, mock_logger):
        """update emits a logger.info containing 'cost_center_updated'."""
        existing = _cost_center()
        mock_repo.get_active_by_id.return_value = existing
        mock_repo.update.return_value = existing

        CostCenterService.update(MagicMock(), user=_admin_user(), cost_center_id=existing.id, description="new")

        mock_logger.info.assert_called_once()
        assert "cost_center_updated" in mock_logger.info.call_args[0][0]

    @patch("codemie.service.cost_center_service.logger")
    @patch("codemie.service.cost_center_service.application_repository")
    @patch("codemie.service.cost_center_service.cost_center_repository")
    def test_delete_logs_on_success(self, mock_repo, mock_app_repo, mock_logger):
        """delete emits a logger.info containing 'cost_center_deleted'."""
        existing = _cost_center()
        mock_repo.get_active_by_id.return_value = existing
        mock_app_repo.count_active_projects_by_cost_center_id.return_value = 0

        CostCenterService.delete(MagicMock(), user=_admin_user(), cost_center_id=existing.id)

        mock_logger.info.assert_called_once()
        assert "cost_center_deleted" in mock_logger.info.call_args[0][0]

    @patch("codemie.service.cost_center_service.logger")
    @patch("codemie.service.cost_center_service.application_repository")
    @patch("codemie.service.cost_center_service.cost_center_repository")
    def test_delete_does_not_log_when_projects_linked(self, mock_repo, mock_app_repo, mock_logger):
        """delete must not log when it aborts on linked active projects."""
        existing = _cost_center()
        mock_repo.get_active_by_id.return_value = existing
        mock_app_repo.count_active_projects_by_cost_center_id.return_value = 3

        with pytest.raises(ExtendedHTTPException):
            CostCenterService.delete(MagicMock(), user=_admin_user(), cost_center_id=existing.id)

        mock_logger.info.assert_not_called()


class TestCostCenterServiceFeatureGate:
    @patch("codemie.service.cost_center_service.customer_config", create=True)
    def test_create_rejects_when_feature_disabled(self, mock_customer_config):
        mock_customer_config.is_feature_enabled.return_value = False

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.create(MagicMock(), user=_admin_user(), name="eng-123", description=None)

        assert exc_info.value.code == 403
        mock_customer_config.is_feature_enabled.assert_called_once_with("costCenters")

    @patch("codemie.service.cost_center_service.customer_config", create=True)
    def test_list_paginated_rejects_when_feature_disabled(self, mock_customer_config):
        mock_customer_config.is_feature_enabled.return_value = False

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.list_paginated(MagicMock(), user=_admin_user(), search=None, page=1, per_page=10)

        assert exc_info.value.code == 403

    @patch("codemie.service.cost_center_service.customer_config", create=True)
    def test_get_or_404_rejects_when_feature_disabled(self, mock_customer_config):
        mock_customer_config.is_feature_enabled.return_value = False

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.get_or_404(MagicMock(), user=_admin_user(), cost_center_id=uuid4())

        assert exc_info.value.code == 403

    @patch("codemie.service.cost_center_service.customer_config", create=True)
    def test_update_rejects_when_feature_disabled(self, mock_customer_config):
        mock_customer_config.is_feature_enabled.return_value = False

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.update(MagicMock(), user=_admin_user(), cost_center_id=uuid4(), description="x")

        assert exc_info.value.code == 403

    @patch("codemie.service.cost_center_service.customer_config", create=True)
    def test_delete_rejects_when_feature_disabled(self, mock_customer_config):
        mock_customer_config.is_feature_enabled.return_value = False

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.delete(MagicMock(), user=_admin_user(), cost_center_id=uuid4())

        assert exc_info.value.code == 403

    @patch("codemie.service.cost_center_service.cost_center_repository")
    @patch("codemie.service.cost_center_service.customer_config", create=True)
    def test_ensure_exists_for_project_rejects_when_feature_disabled(self, mock_customer_config, mock_repo):
        mock_customer_config.is_feature_enabled.return_value = False

        with pytest.raises(ExtendedHTTPException) as exc_info:
            CostCenterService.ensure_exists_for_project(MagicMock(), uuid4())

        assert exc_info.value.code == 403
        mock_repo.get_active_by_id.assert_not_called()

    @patch("codemie.service.cost_center_service.cost_center_repository")
    @patch("codemie.service.cost_center_service.customer_config", create=True)
    def test_ensure_exists_for_project_skips_feature_check_when_cost_center_id_is_none(
        self, mock_customer_config, mock_repo
    ):
        mock_customer_config.is_feature_enabled.return_value = False

        result = CostCenterService.ensure_exists_for_project(MagicMock(), None)

        assert result is None
        mock_customer_config.is_feature_enabled.assert_not_called()
        mock_repo.get_active_by_id.assert_not_called()

    @patch("codemie.service.cost_center_service.cost_center_repository")
    @patch("codemie.service.cost_center_service.customer_config", create=True)
    def test_ensure_exists_for_project_succeeds_when_feature_enabled(self, mock_customer_config, mock_repo):
        mock_customer_config.is_feature_enabled.return_value = True
        cost_center = _cost_center()
        mock_repo.get_active_by_id.return_value = cost_center

        result = CostCenterService.ensure_exists_for_project(MagicMock(), cost_center.id)

        assert result is cost_center
