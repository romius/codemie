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

"""APScheduler-based quarterly metrics index rotation scheduler."""

from __future__ import annotations

from apscheduler.triggers.cron import CronTrigger

from codemie.configs import logger
from codemie.service.metrics_rotation.es_index_rotation_service import MetricsIndexRotationService
from codemie.utils.leader_lock import async_leader_lock

_ROTATION_LOCK_ID = 987654327  # sequence: CA=987654321, Spend=987654322/323/324, LB=987654325, Activity=987654326
_QUARTERLY_TRIGGER = {
    "minute": "0",
    "hour": "0",
    "day": "1",
    "month": "1,4,7,10",
    "day_of_week": "*",
    "timezone": "UTC",
}


class MetricsRotationScheduler:
    """Quarterly scheduler for codemie_metrics_logs index rotation.

    Uses LeaderLockContext advisory-lock pattern to ensure only one pod
    performs the rotation in a multi-replica deployment.
    """

    def __init__(self, scheduler) -> None:
        self.scheduler = scheduler

    def start(self) -> None:
        trigger = CronTrigger(**_QUARTERLY_TRIGGER)
        self.scheduler.add_job(
            self._run_rotation,
            trigger=trigger,
            id="metrics_index_rotation",
            replace_existing=True,
            name="Metrics Index Rotation",
        )

        if not self.scheduler.running:
            self.scheduler.start()

        logger.info("Registered metrics index rotation job for UTC calendar-quarter boundaries")

    async def _run_rotation(self) -> None:
        """Quarterly job with leader lock for multi-pod safety."""
        async with async_leader_lock(_ROTATION_LOCK_ID) as acquired:
            if not acquired:
                logger.info("Metrics index rotation: not the leader, skipping")
                return

            try:
                service = MetricsIndexRotationService()
                await service.rotate()
            except Exception as e:
                logger.error(f"Metrics index rotation failed: {e}", exc_info=True)

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            logger.info("MetricsRotationScheduler stopped")
