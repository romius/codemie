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

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import IntegrityError

from codemie.rest_api.models.deployment_version import DeploymentVersion
from codemie.service.deployment.deployment_version_service import DeploymentVersionService


def _make_record(version="1.0.0") -> DeploymentVersion:
    return DeploymentVersion(
        id="uuid-1",
        version=version,
        deployed_at=datetime(2026, 8, 3, 10, 0, 0, tzinfo=timezone.utc),
    )


class TestRecordIfAbsent:
    @patch("codemie.service.deployment.deployment_version_service.deployment_version_repository")
    @patch("codemie.service.deployment.deployment_version_service.get_session")
    @patch("codemie.service.deployment.deployment_version_service.config")
    def test_inserts_when_no_record_exists(self, mock_cfg, mock_session_cm, mock_repo):
        mock_cfg.APP_VERSION = "1.0.0"
        session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)
        mock_repo.get_by_version.return_value = None
        mock_repo.insert.return_value = _make_record()

        result = DeploymentVersionService.record_if_absent()

        mock_repo.get_by_version.assert_called_once_with("1.0.0", session)
        mock_repo.insert.assert_called_once_with("1.0.0", session)
        assert result.version == "1.0.0"

    @patch("codemie.service.deployment.deployment_version_service.deployment_version_repository")
    @patch("codemie.service.deployment.deployment_version_service.get_session")
    @patch("codemie.service.deployment.deployment_version_service.config")
    def test_skips_insert_when_record_exists(self, mock_cfg, mock_session_cm, mock_repo):
        mock_cfg.APP_VERSION = "1.0.0"
        session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)
        existing = _make_record()
        mock_repo.get_by_version.return_value = existing

        result = DeploymentVersionService.record_if_absent()

        mock_repo.insert.assert_not_called()
        assert result is existing

    @patch("codemie.service.deployment.deployment_version_service.deployment_version_repository")
    @patch("codemie.service.deployment.deployment_version_service.get_session")
    @patch("codemie.service.deployment.deployment_version_service.config")
    def test_handles_integrity_error_from_concurrent_insert(self, mock_cfg, mock_session_cm, mock_repo):
        """Concurrent workers: second worker gets IntegrityError on insert — must return None gracefully."""
        mock_cfg.APP_VERSION = "1.0.0"
        session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)
        mock_repo.get_by_version.return_value = None
        mock_repo.insert.side_effect = IntegrityError("uq", {}, Exception())

        result = DeploymentVersionService.record_if_absent()

        assert result is None  # treated as non-error: another worker already inserted


class TestListDeployments:
    @patch("codemie.service.deployment.deployment_version_service.deployment_version_repository")
    @patch("codemie.service.deployment.deployment_version_service.get_session")
    def test_list_deployments_returns_repo_rows(self, mock_session_cm, mock_repo):
        session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)
        rows = [_make_record("2.0.0"), _make_record("1.0.0")]
        mock_repo.list_all.return_value = rows

        result = DeploymentVersionService.list_deployments()

        assert result == rows
        mock_repo.list_all.assert_called_once_with(session)
