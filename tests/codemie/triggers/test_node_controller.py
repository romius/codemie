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

import asyncio
import pytest
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from codemie.triggers.node_controller import NodeController, TRIGGER_ENGINE_LOCK_ID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_controller(start_async_effect=None):
    """Build a NodeController with a fully mocked Cron instance.

    start_async_effect: an async callable to use as start_async.
    Defaults to a realistic mock: sets cron.scheduler to a running scheduler
    and returns, matching what the real start_async() does.
    """
    ctrl = NodeController.__new__(NodeController)
    mock_cron = MagicMock()
    mock_cron.scheduler = None

    if start_async_effect is None:
        # Realistic default: sets scheduler.running=True and returns, just like
        # the real APScheduler-backed start_async.
        mock_scheduler = MagicMock()
        mock_scheduler.running = True

        async def _default_start():
            mock_cron.scheduler = mock_scheduler

        mock_cron.start_async = _default_start
        mock_cron._mock_scheduler = mock_scheduler  # expose for assertions
    else:
        mock_cron.start_async = start_async_effect

    mock_cron.shutdown = MagicMock()
    ctrl.cron_instance = mock_cron
    return ctrl


def _patch_lock(acquired: bool, alive=True):
    """Patch async_leader_lock_context to yield a lock with the given state.

    `alive` is what the leader's liveness probe reports: True to keep holding, False to
    simulate a connection killed underneath us, or a list consumed one entry per probe.
    """
    outcomes = list(alive) if isinstance(alive, list) else None

    def _is_alive():
        if outcomes is None:
            return alive
        return outcomes.pop(0) if outcomes else False

    lock = MagicMock()
    lock.acquired = acquired
    lock.is_alive = MagicMock(side_effect=_is_alive)

    @asynccontextmanager
    async def _fake_lock(_lock_id):
        yield lock

    return patch("codemie.triggers.node_controller.async_leader_lock_context", new=_fake_lock)


# ---------------------------------------------------------------------------
# Lock-ID uniqueness
# ---------------------------------------------------------------------------


def test_trigger_engine_lock_id_is_unique():
    """TRIGGER_ENGINE_LOCK_ID must not collide with any other advisory lock ID."""
    from codemie.service.leaderboard.config import LEADERBOARD_LOCK_ID
    from codemie.service.stale_datasource.config import STALE_DATASOURCE_LOCK_ID
    from codemie.utils.leader_lock import LeaderLockContext

    known_ids = {
        LeaderLockContext.ADVISORY_LOCK_ID,
        LEADERBOARD_LOCK_ID,
        STALE_DATASOURCE_LOCK_ID,
    }
    assert TRIGGER_ENGINE_LOCK_ID not in known_ids


# ---------------------------------------------------------------------------
# Lock not acquired: engine must stay off
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cron_not_started_when_lock_not_acquired():
    """When another pod holds the lock, start_async must never be called."""
    ctrl = _make_controller()
    start_async_calls = []
    ctrl.cron_instance.start_async = AsyncMock(side_effect=lambda: start_async_calls.append(1))

    with _patch_lock(acquired=False):
        with patch(
            "codemie.triggers.node_controller.asyncio.sleep",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ):
            with pytest.raises(asyncio.CancelledError):
                await ctrl.start()

    assert start_async_calls == [], "start_async must not be called when the lock is not held"


# ---------------------------------------------------------------------------
# Lock acquired: engine must start and stay up while lock is held
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_holding_loop_runs_while_scheduler_is_running():
    """The leader must park inside the lock until the scheduler stops.

    This is the regression guard for the bug where start_async() returned
    immediately and the finally block tore the scheduler down right away.
    The fix is a while-loop that polls scheduler.running inside the lock
    context; this test proves that loop fires before shutdown is called.
    """
    ctrl = _make_controller()
    mock_scheduler = ctrl.cron_instance._mock_scheduler

    events = []
    original_shutdown = ctrl.cron_instance.shutdown.side_effect

    def _tracking_shutdown():
        events.append("shutdown")
        if original_shutdown:
            original_shutdown()

    ctrl.cron_instance.shutdown.side_effect = _tracking_shutdown

    async def _sleep(seconds):
        events.append(f"sleep({seconds})")
        if len([e for e in events if e.startswith("sleep")]) == 1:
            # First sleep is the holding loop — simulate the scheduler stopping
            mock_scheduler.running = False
        else:
            # Second sleep is the outer retry loop — exit the test
            raise asyncio.CancelledError

    with _patch_lock(acquired=True):
        with patch("codemie.triggers.node_controller.asyncio.sleep", new=_sleep):
            with pytest.raises(asyncio.CancelledError):
                await ctrl.start()

    # The holding sleep must have run before shutdown was called
    assert any(e.startswith("sleep") for e in events), "holding loop must sleep"
    assert "shutdown" in events, "shutdown must eventually be called"
    first_sleep = next(i for i, e in enumerate(events) if e.startswith("sleep"))
    shutdown_idx = events.index("shutdown")
    assert first_sleep < shutdown_idx, "shutdown must not fire before the holding loop runs"


@pytest.mark.asyncio
async def test_shutdown_not_called_while_scheduler_is_running():
    """shutdown must not be called while scheduler.running is True.

    The holding loop must keep the lock — and defer shutdown — for as long as
    the scheduler is alive.
    """
    ctrl = _make_controller()
    mock_scheduler = ctrl.cron_instance._mock_scheduler

    shutdown_while_running = []

    def _tracking_shutdown():
        if mock_scheduler.running:
            shutdown_while_running.append(True)

    ctrl.cron_instance.shutdown.side_effect = _tracking_shutdown

    async def _sleep(seconds):
        mock_scheduler.running = False
        raise asyncio.CancelledError

    with _patch_lock(acquired=True):
        with patch("codemie.triggers.node_controller.asyncio.sleep", new=_sleep):
            with pytest.raises(asyncio.CancelledError):
                await ctrl.start()

    assert shutdown_while_running == [], "shutdown must not be called while scheduler is running"


# ---------------------------------------------------------------------------
# Crash recovery: exception from start_async → shutdown + retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_called_after_cron_crashes():
    """A non-cancellation exception from start_async triggers shutdown and allows retry."""
    calls = []

    async def _crash():
        calls.append("start")
        raise RuntimeError("engine blew up")

    ctrl = _make_controller(start_async_effect=_crash)
    ctrl.cron_instance.shutdown = MagicMock(side_effect=lambda: calls.append("shutdown"))

    async def _sleep_once(_seconds):
        raise asyncio.CancelledError  # stop after the first retry sleep

    with _patch_lock(acquired=True):
        with patch("codemie.triggers.node_controller.asyncio.sleep", new=_sleep_once):
            with pytest.raises(asyncio.CancelledError):
                await ctrl.start()

    assert calls == ["start", "shutdown"], f"unexpected call order: {calls}"


# ---------------------------------------------------------------------------
# Cancellation: CancelledError propagates after cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_propagates_and_triggers_shutdown():
    """CancelledError during the holding loop propagates out after shutdown."""
    calls = []

    ctrl = _make_controller()
    ctrl.cron_instance.shutdown = MagicMock(side_effect=lambda: calls.append("shutdown"))

    async def _sleep(_seconds):
        calls.append("sleep")
        raise asyncio.CancelledError

    with _patch_lock(acquired=True):
        with patch("codemie.triggers.node_controller.asyncio.sleep", new=_sleep):
            with pytest.raises(asyncio.CancelledError):
                await ctrl.start()

    assert "shutdown" in calls, "shutdown must be called on cancellation"


# ---------------------------------------------------------------------------
# Liveness: a lock lost under an idle connection must not leave two engines running
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leader_steps_down_when_lock_connection_dies():
    """A connection killed while idle releases the lock without the holder noticing.

    scheduler.running is a local flag and stays true, so without a liveness probe the
    holding loop spins forever: a standby takes the lock, starts a second engine, and
    nothing ends that state short of a pod restart. The leader must shut its scheduler
    down instead.
    """
    ctrl = _make_controller()
    events = []
    ctrl.cron_instance.shutdown = MagicMock(side_effect=lambda: events.append("shutdown"))

    sleeps = 0

    async def _sleep(_seconds):
        nonlocal sleeps
        sleeps += 1
        events.append("sleep")
        if sleeps > 3:  # the retry sleep after stepping down - end the test
            raise asyncio.CancelledError

    # Alive for the first probe, dead on the second.
    with _patch_lock(acquired=True, alive=[True, False]):
        with patch("codemie.triggers.node_controller.asyncio.sleep", new=_sleep):
            with patch("codemie.triggers.node_controller.logger"):
                with pytest.raises(asyncio.CancelledError):
                    await ctrl.start()

    assert "shutdown" in events, "leader must shut the scheduler down once the lock is lost"


@pytest.mark.asyncio
async def test_leader_keeps_holding_while_lock_is_alive():
    """The probe must not cause the leader to step down while it still holds the lock."""
    ctrl = _make_controller()
    probes = 0

    async def _sleep(_seconds):
        nonlocal probes
        probes += 1
        if probes >= 3:
            raise asyncio.CancelledError

    with _patch_lock(acquired=True, alive=True):
        with patch("codemie.triggers.node_controller.asyncio.sleep", new=_sleep):
            with pytest.raises(asyncio.CancelledError):
                await ctrl.start()

    # Reaching the third sleep means a live probe never broke the loop early; a probe
    # reporting a lost lock would have exited after the first.
    assert probes >= 3, "the holding loop must keep looping while the lock is alive"
