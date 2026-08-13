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

"""Leader election using PostgreSQL advisory locks.

This module provides a context manager for distributed leader election
using PostgreSQL advisory locks in a connection pooling environment.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlmodel import Session

from codemie.clients.postgres import PostgresClient
from codemie.configs import logger


class LeaderLockContext:
    """
    Context manager for PostgreSQL advisory lock-based leader election.

    Ensures the same database connection is used for lock acquisition,
    leader operations, and lock release. Prevents lock leakage in
    connection pooling environments.

    Advisory locks in PostgreSQL are connection-scoped, meaning they persist
    for the lifetime of the database connection. In a connection pooling
    environment, if we acquire a lock using one connection and try to release
    it using another, the release will fail and the lock will leak.

    This context manager solves this problem by keeping the same connection
    alive for the entire duration of the leader operation.

    Usage:
        with LeaderLockContext(lock_id=123) as lock:
            if not lock.acquired:
                return  # Another process is leader

            # Do leader work here
            # Lock held on same connection throughout

        # Lock automatically released here (even on exception)

    Example:
        with LeaderLockContext() as leader_lock:
            if not leader_lock.acquired:
                logger.info("Not the leader, skipping")
                return

            # This pod is the leader
            perform_leader_work()
            # Lock released automatically on exit
    """

    ADVISORY_LOCK_ID = 987654321  # Unique ID for conversation analysis lock

    def __init__(self, lock_id: int | None = None, *, lock_key: tuple[int, int] | None = None, engine=None):
        """
        Initialize leader lock context.

        Args:
            lock_id: PostgreSQL advisory lock ID for the single-argument lock form
                (defaults to class constant).
            lock_key: A (key1, key2) pair for the two-argument lock form. PostgreSQL
                keeps the one- and two-argument advisory lock spaces separate, so a
                lock_key can never collide with any lock_id. Both halves must fit in
                int4. Mutually exclusive with lock_id.
            engine: SQLAlchemy engine to take the lock connection from. Defaults to the
                shared application engine. Callers that hold a lock for the lifetime of
                a long task should pass a dedicated engine, since the connection stays
                checked out for as long as the lock is held.
        """
        if lock_key is not None and lock_id is not None:
            raise ValueError("Pass either lock_id or lock_key, not both")
        if lock_key is not None and not all(isinstance(part, int) for part in lock_key):
            raise TypeError("lock_key must be a pair of ints")
        if lock_id is not None and not isinstance(lock_id, int):
            raise TypeError("lock_id must be an int")

        self.lock_key = lock_key
        self.lock_id = None if lock_key is not None else (lock_id or self.ADVISORY_LOCK_ID)
        self.session: Session | None = None
        self.acquired: bool = False
        self._connection = None
        self._engine = engine

    @property
    def _label(self) -> str:
        """Human-readable lock identity for logs."""
        return str(self.lock_key) if self.lock_key is not None else str(self.lock_id)

    def _statement(self, function: str) -> str:
        """Build the lock/unlock statement.

        Both forms interpolate integers that are validated in __init__, never
        caller-supplied strings, so there is no injection surface here.
        """
        if self.lock_key is not None:
            return f"SELECT {function}({self.lock_key[0]}, {self.lock_key[1]})"
        return f"SELECT {function}({self.lock_id})"

    def __enter__(self) -> LeaderLockContext:
        """
        Acquire advisory lock on entry.

        Gets a connection from the pool and attempts to acquire the advisory lock.
        The connection is kept alive for the duration of the context.

        Returns:
            Self with `acquired` attribute set to True/False

        Raises:
            Exception: If lock acquisition fails due to database error
        """
        engine = self._engine if self._engine is not None else PostgresClient.get_engine()

        # Get connection from pool and keep it alive
        self._connection = engine.connect()

        # Create session bound to this specific connection
        self.session = Session(bind=self._connection)

        try:
            # Try to acquire advisory lock (non-blocking)
            result = self.session.execute(text(self._statement("pg_try_advisory_lock"))).scalar()

            self.acquired = bool(result)

            if self.acquired:
                logger.info(f"Advisory lock {self._label} acquired successfully (connection: {id(self._connection)})")
            else:
                logger.info(f"Advisory lock {self._label} already held by another process")

        except Exception as e:
            logger.error(f"Failed to acquire advisory lock {self._label}: {e}", exc_info=True)
            self.acquired = False
            # Clean up connection if acquisition fails
            self._cleanup()
            raise

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Release advisory lock on exit (even on exception).

        This method is called automatically when exiting the context,
        whether normally or due to an exception. It ensures the lock
        is released and the connection is returned to the pool.

        Args:
            exc_type: Exception type (if raised)
            exc_val: Exception value (if raised)
            exc_tb: Exception traceback (if raised)

        Returns:
            False to propagate exceptions from the with block
        """
        try:
            if self.acquired and self.session:
                # Release lock on same connection that acquired it
                result = self.session.execute(text(self._statement("pg_advisory_unlock"))).scalar()

                if result:
                    logger.info(
                        f"Advisory lock {self._label} released successfully (connection: {id(self._connection)})"
                    )
                else:
                    logger.warning(
                        f"Advisory lock {self._label} was not held during release "
                        f"(connection: {id(self._connection)}). This may indicate a bug."
                    )
        except Exception as e:
            logger.error(f"Failed to release advisory lock {self._label}: {e}", exc_info=True)
        finally:
            self._cleanup()

        # Don't suppress exceptions from the with block
        return False

    def is_alive(self) -> bool:
        """Report whether this context still holds a usable lock.

        PostgreSQL releases a session-level advisory lock only on explicit unlock or when
        the session ends, so the liveness of the connection is the whole question. A
        holder that parks on an idle connection for the lifetime of a long task cannot
        rely on ``pool_pre_ping``, which only validates connections as they are checked
        out of the pool — this connection was checked out once and never returned. The
        round trip forces the dead-connection case to surface.
        """
        if not self.acquired or self.session is None:
            return False

        try:
            self.session.execute(text("SELECT 1")).scalar()
            return True
        except Exception as e:
            logger.warning(f"Advisory lock {self._label} connection is no longer usable: {e}")
            return False

    def _cleanup(self):
        """
        Close session and return connection to pool.

        This method ensures resources are properly cleaned up even if
        errors occur during the cleanup process itself.
        """
        if self.session:
            try:
                self.session.close()
            except Exception as e:
                logger.error(f"Error closing session: {e}")
            finally:
                self.session = None

        if self._connection:
            try:
                self._connection.close()  # Returns connection to pool
            except Exception as e:
                logger.error(f"Error closing connection: {e}")
            finally:
                self._connection = None


@asynccontextmanager
async def async_leader_lock_context(lock_id: int):
    """Async wrapper around LeaderLockContext yielding the context itself.

    Use this instead of `async_leader_lock` when the leader holds the lock across a long
    running task and needs to keep checking that it still holds it — see
    `LeaderLockContext.is_alive`.

    Usage:
        async with async_leader_lock_context(MY_LOCK_ID) as lock:
            if not lock.acquired:
                return
            while working:
                if not await asyncio.to_thread(lock.is_alive):
                    break  # step down, a standby can take over
    """
    lock = LeaderLockContext(lock_id=lock_id)
    await asyncio.to_thread(lock.__enter__)
    try:
        yield lock
    finally:
        await asyncio.to_thread(lock.__exit__, None, None, None)


@asynccontextmanager
async def async_leader_lock(lock_id: int):
    """Async wrapper around LeaderLockContext for use in asyncio code.

    Yields True if this process acquired the advisory lock, False otherwise.
    The sync acquire/release calls are dispatched via asyncio.to_thread so
    the event loop is never blocked on database I/O.

    Usage:
        async with async_leader_lock(MY_LOCK_ID) as acquired:
            if not acquired:
                return
            # Do leader work here
    """
    async with async_leader_lock_context(lock_id) as lock:
        yield lock.acquired
