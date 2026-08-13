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

"""Unit tests for MetricsRotationScheduler."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from codemie.service.metrics_rotation.scheduler import MetricsRotationScheduler

MODULE = "codemie.service.metrics_rotation.scheduler"


@patch(f"{MODULE}.CronTrigger")
def test_start_registers_fixed_utc_quarterly_trigger(mock_trigger_cls) -> None:
    """The metrics scheduler must always run at UTC calendar-quarter boundaries."""
    scheduler_backend = MagicMock()
    scheduler_backend.running = False
    scheduler = MetricsRotationScheduler(scheduler=scheduler_backend)

    scheduler.start()

    mock_trigger_cls.assert_called_once_with(
        minute="0",
        hour="0",
        day="1",
        month="1,4,7,10",
        day_of_week="*",
        timezone="UTC",
    )
    scheduler_backend.add_job.assert_called_once()
