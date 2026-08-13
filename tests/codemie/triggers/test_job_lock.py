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

"""Cross-process locking for scheduled datasource reindex jobs (EPMCDME-13171)."""

from __future__ import annotations

import subprocess
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from codemie.triggers.job_lock import (
    datasource_lock_key,
    with_datasource_job_lock,
)
from codemie.utils.leader_lock import LeaderLockContext

INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1


@contextmanager
def _patched_lock(acquired: bool):
    """Patch the job lock so the decorator sees the given acquisition outcome."""

    @contextmanager
    def _fake_lock(_datasource_id):
        yield acquired

    with patch("codemie.triggers.job_lock.datasource_job_lock", new=_fake_lock):
        yield


def _payload(datasource_id="ds-1"):
    payload = MagicMock()
    payload.index_info.id = datasource_id
    return payload


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------


def test_lock_key_halves_fit_in_int4():
    """PostgreSQL's two-argument advisory lock takes int4, not bigint."""
    for datasource_id in ("ds-1", "a" * 200, "0f8c1b1e-1111-2222-3333-444455556666"):
        key1, key2 = datasource_lock_key(datasource_id)
        assert INT32_MIN <= key1 <= INT32_MAX
        assert INT32_MIN <= key2 <= INT32_MAX


def test_lock_key_is_stable_across_processes():
    """A per-process salt would mean two pods never contend for the same datasource.

    This is why the key is a BLAKE2b digest and not Python's built-in hash(), which is
    randomized per interpreter unless PYTHONHASHSEED is pinned. Running in a real
    subprocess is the only way to actually prove it.
    """
    code = "from codemie.triggers.job_lock import datasource_lock_key; print(datasource_lock_key('ds-stable'))"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(datasource_lock_key("ds-stable"))


def test_different_datasources_get_different_keys():
    assert datasource_lock_key("ds-1") != datasource_lock_key("ds-2")


# ---------------------------------------------------------------------------
# Decorator behaviour
# ---------------------------------------------------------------------------


def test_actor_runs_when_lock_acquired():
    calls = []

    @with_datasource_job_lock
    def reindex(payload):
        calls.append(payload)
        return "done"

    with _patched_lock(acquired=True):
        result = reindex(payload=_payload())

    assert len(calls) == 1
    assert result == "done"


def test_actor_skipped_when_lock_held_elsewhere():
    """Lock acquisition failure must prevent duplicate execution."""
    calls = []

    @with_datasource_job_lock
    def reindex(payload):
        calls.append(payload)

    with _patched_lock(acquired=False):
        result = reindex(payload=_payload())

    assert calls == [], "actor must not run when another process holds the lock"
    assert result is None


def test_actor_accepts_keyword_dispatch():
    """APScheduler dispatches actors as kwargs={"payload": payload}.

    A wrapper that only accepted a positional argument would raise TypeError on every
    scheduled reindex, which is a far worse failure than the one being guarded against.
    """
    seen = {}

    @with_datasource_job_lock
    def reindex(payload):
        seen["payload"] = payload

    payload = _payload()
    with _patched_lock(acquired=True):
        reindex(payload=payload)

    assert seen["payload"] is payload


def test_actor_accepts_positional_dispatch():
    """The stale-indexing watchdog calls resume_stale_datasource positionally."""
    from codemie.rest_api.models.index import IndexInfo

    seen = {}

    @with_datasource_job_lock
    def resume(index_info):
        seen["index_info"] = index_info

    index_info = MagicMock(spec=IndexInfo)
    index_info.id = "ds-7"

    with _patched_lock(acquired=True):
        resume(index_info)

    assert seen["index_info"] is index_info


def test_actor_runs_unlocked_when_datasource_id_missing():
    """Fail open on a malformed payload: a skipped reindex is worse than a rare duplicate."""
    calls = []

    @with_datasource_job_lock
    def reindex(payload):
        calls.append(payload)

    payload = MagicMock()
    payload.index_info = None

    with patch("codemie.triggers.job_lock.logger"):
        reindex(payload=payload)

    assert len(calls) == 1


def test_lock_released_when_actor_raises():
    """AC: locks are released after failure handling, not only on the happy path."""
    released = []

    class _Lock:
        def __init__(self, *_args, **_kwargs):
            self.acquired = True

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            released.append(True)
            return False

    @with_datasource_job_lock
    def reindex(payload):
        raise RuntimeError("indexing blew up")

    with patch("codemie.triggers.job_lock.LeaderLockContext", _Lock):
        with patch("codemie.triggers.job_lock._get_lock_engine", return_value=MagicMock()):
            with pytest.raises(RuntimeError):
                reindex(payload=_payload())

    assert released == [True], "lock must be released when the actor raises"


# ---------------------------------------------------------------------------
# LeaderLockContext: existing consumers must be unaffected
# ---------------------------------------------------------------------------


@contextmanager
def _mocked_session(acquired=True):
    with patch("codemie.utils.leader_lock.PostgresClient") as client:
        with patch("codemie.utils.leader_lock.Session") as session_class:
            session = MagicMock()
            session.execute.return_value.scalar.return_value = acquired
            session_class.return_value = session
            client.get_engine.return_value = MagicMock()
            yield session, client


def test_default_form_is_unchanged_for_existing_callers():
    """Regression guard for the eight subsystems already using this class.

    With lock_key and engine unset the emitted SQL and the engine must be exactly what
    they were before per-job locking existed.
    """
    with _mocked_session() as (session, client):
        with LeaderLockContext(lock_id=123456) as lock:
            assert lock.acquired is True

    client.get_engine.assert_called_once()
    acquire_sql = str(session.execute.call_args_list[0][0][0])
    release_sql = str(session.execute.call_args_list[1][0][0])
    assert acquire_sql == "SELECT pg_try_advisory_lock(123456)"
    assert release_sql == "SELECT pg_advisory_unlock(123456)"


def test_two_argument_form_used_for_lock_key():
    with _mocked_session() as (session, _client):
        with LeaderLockContext(lock_key=(11, -22)) as lock:
            assert lock.acquired is True

    acquire_sql = str(session.execute.call_args_list[0][0][0])
    release_sql = str(session.execute.call_args_list[1][0][0])
    assert acquire_sql == "SELECT pg_try_advisory_lock(11, -22)"
    assert release_sql == "SELECT pg_advisory_unlock(11, -22)"


def test_dedicated_engine_is_used_when_provided():
    """Job locks must never draw from the shared application pool."""
    dedicated = MagicMock()

    with _mocked_session() as (_session, client):
        with LeaderLockContext(lock_key=(1, 2), engine=dedicated):
            pass

    dedicated.connect.assert_called_once()
    client.get_engine.assert_not_called()


def test_lock_id_and_lock_key_are_mutually_exclusive():
    with pytest.raises(ValueError):
        LeaderLockContext(lock_id=1, lock_key=(1, 2))


# ---------------------------------------------------------------------------
# Lock liveness
# ---------------------------------------------------------------------------


def test_is_alive_true_while_connection_usable():
    with _mocked_session() as (session, _client):
        with LeaderLockContext(lock_id=1) as lock:
            session.execute.reset_mock()
            assert lock.is_alive() is True
            assert "SELECT 1" in str(session.execute.call_args[0][0])


def test_is_alive_false_when_connection_dead():
    """A connection killed while idle releases the lock without the holder noticing."""
    with _mocked_session() as (session, _client):
        with LeaderLockContext(lock_id=1) as lock:
            session.execute.side_effect = RuntimeError("server closed the connection unexpectedly")
            with patch("codemie.utils.leader_lock.logger"):
                assert lock.is_alive() is False


def test_is_alive_false_when_lock_was_never_acquired():
    with _mocked_session(acquired=False) as (_session, _client):
        with LeaderLockContext(lock_id=1) as lock:
            assert lock.acquired is False
            assert lock.is_alive() is False
