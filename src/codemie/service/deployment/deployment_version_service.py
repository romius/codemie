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

"""Service for recording and querying deployment version timestamps.

Classmethod-only pattern following AssistantVersionService.
Called at startup (record_if_absent) and for list API (list_deployments).
"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from codemie.clients.postgres import get_session
from codemie.configs import config
from codemie.rest_api.models.deployment_version import DeploymentVersion
from codemie.service.deployment.deployment_version_repository import deployment_version_repository


class DeploymentVersionService:
    @classmethod
    def record_if_absent(cls) -> DeploymentVersion | None:
        """Insert a deployment record for APP_VERSION if none exists.

        Safe for concurrent workers: an IntegrityError from the unique constraint
        means another worker already inserted the row — treated as success (returns None).
        Non-fatal: all exceptions are caught; caller must handle None gracefully.
        """
        with get_session() as session:
            existing = deployment_version_repository.get_by_version(config.APP_VERSION, session)
            if existing:
                return existing
            try:
                record = deployment_version_repository.insert(config.APP_VERSION, session)
                session.commit()
                return record
            except IntegrityError:
                session.rollback()
                return None

    @classmethod
    def list_deployments(cls) -> list[DeploymentVersion]:
        """Return all deployment version records for this environment."""
        with get_session() as session:
            return deployment_version_repository.list_all(session)


deployment_version_service = DeploymentVersionService()
