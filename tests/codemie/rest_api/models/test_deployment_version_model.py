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

from datetime import datetime, timezone

from codemie.rest_api.models.deployment_version import DeploymentVersion


def test_deployment_version_instantiation():
    record = DeploymentVersion(
        id="some-uuid",
        version="1.2.3",
    )
    assert record.version == "1.2.3"
    assert record.id == "some-uuid"


def test_deployment_version_deployed_at_accepts_datetime():
    dt = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)
    record = DeploymentVersion(
        id="some-uuid",
        version="1.2.3",
        deployed_at=dt,
    )
    assert record.deployed_at == dt
