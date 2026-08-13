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

"""Module for triggers core service"""

import asyncio
import platform

from codemie.configs import logger
from codemie.triggers.bindings.cron import Cron
from codemie.utils.leader_lock import async_leader_lock_context

# Unique advisory lock ID for the trigger engine leader election.
# Must not collide with other pg_advisory_lock IDs in the codebase.
TRIGGER_ENGINE_LOCK_ID = 1_357_924_680


class NodeController:
    """Trigger engine active node controller.

    Uses a PostgreSQL session-level advisory lock for leader election. Only the
    pod that holds the lock runs the Cron binding. When that pod dies the TCP
    connection closes and PostgreSQL automatically releases the lock, so a standby
    pod acquires it within RETRY_INTERVAL_SECONDS seconds — no heartbeat renewal
    or stale-document cleanup required.

    The leader does verify that its lock connection is still alive on each tick. That
    is the one guarantee an advisory lock does not give for free: a connection killed
    while idle releases the lock server-side without the holder noticing.
    """

    RETRY_INTERVAL_SECONDS = 10

    def __init__(self):
        self.cron_instance = Cron()

    async def start(self):
        """Compete for the advisory lock and run the trigger engine while holding it."""
        node = platform.uname().node
        while True:
            async with async_leader_lock_context(TRIGGER_ENGINE_LOCK_ID) as lock:
                if lock.acquired:
                    await self._lead(lock, node)
                else:
                    logger.debug(
                        "Trigger engine lock held by another node, retrying in %ss",
                        self.RETRY_INTERVAL_SECONDS,
                    )
            await asyncio.sleep(self.RETRY_INTERVAL_SECONDS)

    async def _lead(self, lock, node: str) -> None:
        """Run the engine for as long as this node holds the lock, then shut it down.

        The shutdown belongs here rather than at the call site: the lock is released when
        the caller's context manager exits, so the scheduler has to be stopped first or a
        standby could start a second engine while this one is still running.
        """
        logger.info("Trigger engine leader lock acquired on node: %s", node)
        try:
            await self.cron_instance.start_async()
            await self._hold_while_running(lock, node)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Trigger engine crashed on node %s: %r", node, exc)
        finally:
            self.cron_instance.shutdown()

    async def _hold_while_running(self, lock, node: str) -> None:
        """Park for the scheduler's lifetime, stepping down if the lock is lost.

        start_async() starts APScheduler and returns immediately; the scheduler then runs
        in the background. Without this loop the caller's finally block tears the scheduler
        down microseconds after it starts. Returning releases the lock, so a standby pod
        can take over whenever the scheduler stops.
        """
        while self.cron_instance.scheduler and self.cron_instance.scheduler.running:
            await asyncio.sleep(self.RETRY_INTERVAL_SECONDS)

            # scheduler.running is a local flag: it stays true even after PostgreSQL has
            # dropped our lock along with a connection that was killed while sitting idle
            # (failover, pgbouncer or load balancer idle reaping). Without this probe a
            # standby would take the lock and start a second engine while this one kept
            # running, and nothing would end that state short of a pod restart.
            if not await asyncio.to_thread(lock.is_alive):
                logger.warning(
                    "Trigger engine lock lost on node %s - stepping down so a standby can take over",
                    node,
                )
                return
