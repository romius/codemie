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

"""Repository for deployment version records.

Follows the ABC + concrete implementation pattern established by
ActivityEventRepository (src/codemie/service/activity/activity_repository.py).
All methods accept a caller-provided sync session.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from sqlmodel import Session, select

from codemie.rest_api.models.deployment_version import DeploymentVersion


class DeploymentVersionRepository(ABC):
    """Abstract interface for deployment version persistence."""

    @abstractmethod
    def get_by_version(self, version: str, session: Session) -> DeploymentVersion | None:
        """Return the record for version, or None if absent."""

    @abstractmethod
    def insert(self, version: str, session: Session) -> DeploymentVersion:
        """Insert a new record for version. Caller is responsible for commit."""

    @abstractmethod
    def list_all(self, session: Session) -> list[DeploymentVersion]:
        """Return all rows ordered by deployed_at DESC."""


class SQLDeploymentVersionRepository(DeploymentVersionRepository):
    """Postgres-backed implementation."""

    def get_by_version(self, version: str, session: Session) -> DeploymentVersion | None:
        stmt = select(DeploymentVersion).where(DeploymentVersion.version == version)
        return session.exec(stmt).first()

    def insert(self, version: str, session: Session) -> DeploymentVersion:
        record = DeploymentVersion(version=version)
        session.add(record)
        session.flush()
        return record

    def list_all(self, session: Session) -> list[DeploymentVersion]:
        stmt = select(DeploymentVersion).order_by(DeploymentVersion.deployed_at.desc())
        return list(session.exec(stmt).all())


deployment_version_repository: DeploymentVersionRepository = SQLDeploymentVersionRepository()
