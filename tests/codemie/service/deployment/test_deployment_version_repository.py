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

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from codemie.rest_api.models.deployment_version import DeploymentVersion
from codemie.service.deployment.deployment_version_repository import (
    SQLDeploymentVersionRepository,
)


def _repo() -> SQLDeploymentVersionRepository:
    return SQLDeploymentVersionRepository()


def test_get_by_version_returns_record_when_found():
    session = MagicMock()
    record = DeploymentVersion(
        id="uuid-1",
        version="1.0.0",
        deployed_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )
    session.exec.return_value.first.return_value = record

    result = _repo().get_by_version("1.0.0", session)

    session.exec.assert_called_once()
    assert result is record


def test_get_by_version_returns_none_when_absent():
    session = MagicMock()
    session.exec.return_value.first.return_value = None

    result = _repo().get_by_version("1.0.0", session)

    assert result is None


def test_insert_adds_and_flushes_record():
    session = MagicMock()

    result = _repo().insert("1.0.0", session)

    session.add.assert_called_once()
    session.flush.assert_called_once()
    assert result.version == "1.0.0"
    assert isinstance(result.id, str)


def test_list_all_returns_empty_when_no_rows():
    session = MagicMock()
    session.exec.return_value.all.return_value = []

    result = _repo().list_all(session)

    assert result == []
    session.exec.assert_called_once()


def test_list_all_orders_by_deployed_at_desc():
    newer = DeploymentVersion(
        id="1",
        version="2.0.0",
        deployed_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )
    older = DeploymentVersion(
        id="2",
        version="1.0.0",
        deployed_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    session = MagicMock()
    session.exec.return_value.all.return_value = [newer, older]

    result = _repo().list_all(session)

    assert [r.version for r in result] == ["2.0.0", "1.0.0"]
    session.exec.assert_called_once()
