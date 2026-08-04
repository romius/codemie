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

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import text

from codemie.clients.postgres import PostgresClient
from codemie.configs import config, logger
from codemie.enterprise.litellm import (
    backfill_project_budget_assignments_from_settings,
    backfill_user_budget_assignments,
    ensure_predefined_budgets,
    sync_budgets_from_litellm,
)

_BUDGET_RECONCILIATION_LOCK_ID = 7734269313


@dataclass
class ReconciliationStepResult:
    step_name: str
    duration_seconds: float


class BudgetStartupReconciliationService:
    """Runs the post-readiness budget reconciliation sequence once per startup."""

    @asynccontextmanager
    async def advisory_lock(self):
        """Acquire the advisory lock for the lifetime of one reconciliation attempt."""
        engine = PostgresClient.get_async_engine()
        async with engine.connect() as lock_conn:
            result = await lock_conn.execute(
                text("SELECT pg_try_advisory_lock(:id)"),
                {"id": _BUDGET_RECONCILIATION_LOCK_ID},
            )
            acquired = bool(result.scalar())
            try:
                yield acquired
            finally:
                if acquired:
                    await lock_conn.execute(
                        text("SELECT pg_advisory_unlock(:id)"),
                        {"id": _BUDGET_RECONCILIATION_LOCK_ID},
                    )

    async def _run_steps(self) -> list[ReconciliationStepResult]:
        results: list[ReconciliationStepResult] = []

        for step_name, step in (
            ("ensure_predefined_budgets", ensure_predefined_budgets),
            ("sync_budgets_from_litellm", sync_budgets_from_litellm),
            ("backfill_user_budget_assignments", backfill_user_budget_assignments),
            (
                "backfill_project_budget_assignments_from_settings",
                backfill_project_budget_assignments_from_settings,
            ),
        ):
            started_at = time.perf_counter()
            logger.info(f"budget_reconciliation_step_started: step_name={step_name}")
            await step()
            duration_seconds = time.perf_counter() - started_at
            logger.info(
                f"budget_reconciliation_step_succeeded: step_name={step_name}, duration_seconds={duration_seconds:.3f}"
            )
            results.append(
                ReconciliationStepResult(
                    step_name=step_name,
                    duration_seconds=duration_seconds,
                )
            )

        return results

    async def run(self) -> None:
        """Run reconciliation once if enabled and not already owned by another instance."""
        if not config.LLM_PROXY_BUDGET_RECONCILIATION_ENABLED:
            logger.info("Budget reconciliation disabled by config, skipping startup reconciliation task")
            return

        async with self.advisory_lock() as acquired:
            if not acquired:
                logger.info("Budget reconciliation lock held by another instance, skipping startup reconciliation")
                return

            logger.info("Budget startup reconciliation started")
            started_at = time.perf_counter()
            try:
                step_results = await asyncio.wait_for(
                    self._run_steps(),
                    timeout=config.LLM_PROXY_BUDGET_RECONCILIATION_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                duration_seconds = time.perf_counter() - started_at
                logger.error(
                    f"Budget startup reconciliation failed: duration_seconds={duration_seconds:.3f}, error={exc}",
                    exc_info=True,
                )
                return

            duration_seconds = time.perf_counter() - started_at
            logger.info(
                f"Budget startup reconciliation finished: "
                f"steps_completed={len(step_results)}, duration_seconds={duration_seconds:.3f}"
            )


budget_startup_reconciliation_service = BudgetStartupReconciliationService()
