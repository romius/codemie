# Implementation Plan: EPMCDME-10701 — Display Deployment Dates

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record the deployment timestamp of each `(APP_VERSION, ENV)` pair on first service startup and expose it as `deployed_at` on the existing `GET /v1/info` endpoint, with no new external dependencies and full backward compatibility.

**Architecture:** One new Alembic migration creates the `deployment_versions` table with a unique constraint on `(version, environment)`. A new `DeploymentVersion` SQLModel in `src/codemie/rest_api/models/deployment_version.py` maps the table. A new `SQLDeploymentVersionRepository` with an ABC in `src/codemie/service/deployment/deployment_version_repository.py` exposes two operations: `get_by_version_and_env` and `insert`. A classmethod-only `DeploymentVersionService` in `src/codemie/service/deployment/deployment_version_service.py` implements `record_if_absent` (called at startup) and `get_deployed_at` (called per request). A new `_initialize_deployment_version()` helper in `main.py` calls `record_if_absent` inside `lifespan()`, wrapped in `try/except` so it is non-fatal. `InfoResponse` in `core/models.py` gains `deployed_at: Optional[datetime] = None` and the `app_info()` handler in `routers/common.py` populates it.

**Tech Stack:** Python 3.12, FastAPI, SQLModel, synchronous SQLAlchemy session (`get_session()`), PostgreSQL unique constraint + `IntegrityError` catch, Alembic, pytest + MagicMock.

---

## 7 Touch Points at a Glance

| # | Action | Path | Layer |
|---|--------|------|-------|
| 1 | Create | `src/external/alembic/versions/<rev>_add_deployment_versions_table.py` | Migration |
| 2 | Modify | `src/external/alembic/env.py` | Migration metadata |
| 3 | Create | `src/codemie/rest_api/models/deployment_version.py` | Data model |
| 4 | Create | `src/codemie/service/deployment/deployment_version_repository.py` | Repository |
| 5 | Create | `src/codemie/service/deployment/deployment_version_service.py` | Service |
| 6 | Modify | `src/codemie/rest_api/main.py` | Startup hook |
| 7a | Modify | `src/codemie/core/models.py` line 762 | Response model |
| 7b | Modify | `src/codemie/rest_api/routers/common.py` line 29 | Router |

---

## Task 1: Alembic Migration — `deployment_versions` table

**Files:**
- Create: `src/external/alembic/versions/<rev>_add_deployment_versions_table.py`

**Test-first: no** — Alembic migrations are not unit-tested; correctness is verified by `alembic check` (detects import errors and chain breaks) and integration (table present after `upgrade()`). There is no application code to exercise in isolation.

**Key constraints:**
- `down_revision = "i1n2t3e4r5a6"` — confirmed current head from `src/external/alembic/versions/i1n2t3e4r5a6_add_interactive_features_to_assistants.py` line 17.
- Unique constraint `uq_deployment_versions_version_env` on `(version, environment)` — this is the race-condition guard.
- `deployed_at` uses `sa.TIMESTAMP(timezone=True)` + `server_default=sa.text("now()")` — same pattern as `activity_events` migration (`src/external/alembic/versions/38255069bfab_create_activity_events_table.py` line 41–45).
- No `schema=` keyword needed (existing migrations in this repo do not set it explicitly in `op.create_table`; the schema is injected via `SET search_path` in `env.py` line 187).

- [ ] **Step 1: Generate a revision ID**

```bash
python -c "import uuid; print(uuid.uuid4().hex[:12])"
```

Note the output — use it as `<rev>` throughout this task.

- [ ] **Step 2: Create the migration file**

Replace `<rev>` with the value from step 1. The file name must be `<rev>_add_deployment_versions_table.py`.

```python
# src/external/alembic/versions/<rev>_add_deployment_versions_table.py
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

"""add_deployment_versions_table

Revision ID: <rev>
Revises: i1n2t3e4r5a6
Create Date: 2026-08-03 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "<rev>"
down_revision: Union[str, None] = "i1n2t3e4r5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create deployment_versions — one row per (version, environment), records first-start timestamp."""
    op.create_table(
        "deployment_versions",
        sa.Column("id", sa.VARCHAR(36), nullable=False),
        sa.Column("version", sa.VARCHAR(64), nullable=False),
        sa.Column("environment", sa.VARCHAR(64), nullable=False),
        sa.Column(
            "deployed_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version", "environment", name="uq_deployment_versions_version_env"),
    )
    op.create_index("ix_deployment_versions_version", "deployment_versions", ["version"])
    op.create_index("ix_deployment_versions_environment", "deployment_versions", ["environment"])


def downgrade() -> None:
    op.drop_index("ix_deployment_versions_environment", table_name="deployment_versions")
    op.drop_index("ix_deployment_versions_version", table_name="deployment_versions")
    op.drop_table("deployment_versions")
```

- [ ] **Step 3: Verify the migration chain is intact**

```bash
source .venv/bin/activate
poetry run alembic -c src/external/alembic/alembic.ini check
```

Expected: no import errors (may warn that the migration is unapplied — that is expected at this stage).

- [ ] **Step 4: Commit**

```bash
git add "src/external/alembic/versions/<rev>_add_deployment_versions_table.py"
git commit -m "$(cat <<'EOF'
feat: add Alembic migration for deployment_versions table

Adds deployment_versions with unique constraint on (version, environment)
and server-side deployed_at timestamp. down_revision=i1n2t3e4r5a6.

Generated with AI

Co-Authored-By: codemie-ai <codemie.ai@gmail.com>
EOF
)"
```

---

## Task 2: Data Model — `DeploymentVersion` SQLModel

**Files:**
- Create: `src/codemie/rest_api/models/deployment_version.py`
- Create: `tests/codemie/rest_api/models/test_deployment_version_model.py` (new test file; create `tests/codemie/rest_api/models/__init__.py` if it does not exist)

**Test-first: yes**

Failing test (`ImportError` — file does not exist yet):

```python
# tests/codemie/rest_api/models/test_deployment_version_model.py
from datetime import datetime, timezone
from codemie.rest_api.models.deployment_version import DeploymentVersion


def test_deployment_version_instantiation():
    record = DeploymentVersion(
        id="some-uuid",
        version="1.2.3",
        environment="dev",
    )
    assert record.version == "1.2.3"
    assert record.environment == "dev"
    assert record.id == "some-uuid"


def test_deployment_version_deployed_at_accepts_datetime():
    dt = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)
    record = DeploymentVersion(
        id="some-uuid",
        version="1.2.3",
        environment="dev",
        deployed_at=dt,
    )
    assert record.deployed_at == dt
```

- [ ] **Step 1: Write the failing test**

Write the test file at `tests/codemie/rest_api/models/test_deployment_version_model.py` (create `tests/codemie/rest_api/models/__init__.py` as empty if the directory does not yet exist).

- [ ] **Step 2: Run to confirm failure**

```bash
poetry run pytest tests/codemie/rest_api/models/test_deployment_version_model.py -v
```

Expected: `ImportError` — `deployment_version` module does not exist yet.

- [ ] **Step 3: Create the model**

```python
# src/codemie/rest_api/models/deployment_version.py
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

"""SQLModel table class for the deployment_versions table.

One row per (version, environment) pair. Records the first-start
timestamp for the given APP_VERSION in the given ENV.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import TIMESTAMP, Column, UniqueConstraint, text
from sqlmodel import Field, SQLModel


class DeploymentVersion(SQLModel, table=True):
    """Append-only record of the first startup of a given version in a given environment."""

    __tablename__ = "deployment_versions"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    version: str = Field(sa_column_kwargs={"index": True}, max_length=64)
    environment: str = Field(sa_column_kwargs={"index": True}, max_length=64)
    deployed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(
            TIMESTAMP(timezone=True),
            server_default=text("now()"),
            nullable=False,
        ),
    )

    __table_args__ = (
        UniqueConstraint("version", "environment", name="uq_deployment_versions_version_env"),
    )
```

- [ ] **Step 4: Run to confirm the tests pass**

```bash
poetry run pytest tests/codemie/rest_api/models/test_deployment_version_model.py -v
```

Expected: both tests PASS.

- [ ] **Step 5: Update `alembic/env.py` import block**

In `src/external/alembic/env.py`, after the last existing model import (currently line 116, `from codemie.rest_api.a2a.types import Task`), add:

```python
from codemie.rest_api.models.deployment_version import DeploymentVersion
```

This ensures `SQLModel.metadata` (line 118 of `env.py`) includes the new table for autogenerate comparisons. The explicit `op.create_table()` in the migration is what actually creates the table; this import only affects `alembic check` correctness.

- [ ] **Step 6: Verify alembic check still clean**

```bash
poetry run alembic -c src/external/alembic/alembic.ini check
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/codemie/rest_api/models/deployment_version.py \
        src/external/alembic/env.py \
        tests/codemie/rest_api/models/__init__.py \
        tests/codemie/rest_api/models/test_deployment_version_model.py
git commit -m "$(cat <<'EOF'
feat: add DeploymentVersion SQLModel and register in alembic env

Introduces the deployment_versions table class following the
ActivityEvent append-only pattern. Adds env.py import so
alembic autogenerate detects the table correctly.

Generated with AI

Co-Authored-By: codemie-ai <codemie.ai@gmail.com>
EOF
)"
```

---

## Task 3: Repository — `DeploymentVersionRepository`

**Files:**
- Create: `src/codemie/service/deployment/__init__.py`
- Create: `src/codemie/service/deployment/deployment_version_repository.py`
- Create: `tests/codemie/service/deployment/__init__.py`
- Create: `tests/codemie/service/deployment/test_deployment_version_repository.py`

**Test-first: yes**

Failing tests (`ImportError` — neither directory nor file exist yet):

```python
# tests/codemie/service/deployment/test_deployment_version_repository.py
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from codemie.rest_api.models.deployment_version import DeploymentVersion
from codemie.service.deployment.deployment_version_repository import (
    SQLDeploymentVersionRepository,
)


def _repo() -> SQLDeploymentVersionRepository:
    return SQLDeploymentVersionRepository()


def test_get_by_version_and_env_returns_record_when_found():
    session = MagicMock()
    record = DeploymentVersion(
        id="uuid-1",
        version="1.0.0",
        environment="dev",
        deployed_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )
    session.exec.return_value.first.return_value = record

    result = _repo().get_by_version_and_env("1.0.0", "dev", session)

    session.exec.assert_called_once()
    assert result is record


def test_get_by_version_and_env_returns_none_when_absent():
    session = MagicMock()
    session.exec.return_value.first.return_value = None

    result = _repo().get_by_version_and_env("1.0.0", "dev", session)

    assert result is None


def test_insert_adds_and_flushes_record():
    session = MagicMock()

    result = _repo().insert("1.0.0", "dev", session)

    session.add.assert_called_once()
    session.flush.assert_called_once()
    assert result.version == "1.0.0"
    assert result.environment == "dev"
    assert isinstance(result.id, str)
```

- [ ] **Step 1: Write the failing tests**

Create `src/codemie/service/deployment/__init__.py` (empty), `tests/codemie/service/deployment/__init__.py` (empty), and `tests/codemie/service/deployment/test_deployment_version_repository.py` with the content above.

- [ ] **Step 2: Run to confirm failure**

```bash
poetry run pytest tests/codemie/service/deployment/test_deployment_version_repository.py -v
```

Expected: `ImportError` — repository module does not exist yet.

- [ ] **Step 3: Create the repository**

```python
# src/codemie/service/deployment/deployment_version_repository.py
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
    def get_by_version_and_env(
        self, version: str, environment: str, session: Session
    ) -> DeploymentVersion | None:
        """Return the record for (version, environment), or None if absent."""

    @abstractmethod
    def insert(
        self, version: str, environment: str, session: Session
    ) -> DeploymentVersion:
        """Insert a new record for (version, environment). Caller is responsible for commit."""


class SQLDeploymentVersionRepository(DeploymentVersionRepository):
    """Postgres-backed implementation."""

    def get_by_version_and_env(
        self, version: str, environment: str, session: Session
    ) -> DeploymentVersion | None:
        stmt = (
            select(DeploymentVersion)
            .where(DeploymentVersion.version == version)
            .where(DeploymentVersion.environment == environment)
        )
        return session.exec(stmt).first()

    def insert(
        self, version: str, environment: str, session: Session
    ) -> DeploymentVersion:
        record = DeploymentVersion(version=version, environment=environment)
        session.add(record)
        session.flush()
        return record


deployment_version_repository: DeploymentVersionRepository = SQLDeploymentVersionRepository()
```

- [ ] **Step 4: Run to confirm tests pass**

```bash
poetry run pytest tests/codemie/service/deployment/test_deployment_version_repository.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/deployment/__init__.py \
        src/codemie/service/deployment/deployment_version_repository.py \
        tests/codemie/service/deployment/__init__.py \
        tests/codemie/service/deployment/test_deployment_version_repository.py
git commit -m "$(cat <<'EOF'
feat: add DeploymentVersionRepository with ABC and SQL implementation

Follows ActivityEventRepository pattern: ABC + concrete SQL impl.
Two operations: get_by_version_and_env (lookup) and insert (first-start record).

Generated with AI

Co-Authored-By: codemie-ai <codemie.ai@gmail.com>
EOF
)"
```

---

## Task 4: Service — `DeploymentVersionService`

**Files:**
- Create: `src/codemie/service/deployment/deployment_version_service.py`
- Create: `tests/codemie/service/deployment/test_deployment_version_service.py`

**Test-first: yes**

Failing tests (`ImportError` — service file does not exist yet):

```python
# tests/codemie/service/deployment/test_deployment_version_service.py
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from codemie.rest_api.models.deployment_version import DeploymentVersion
from codemie.service.deployment.deployment_version_service import DeploymentVersionService


def _make_record(version="1.0.0", env="dev") -> DeploymentVersion:
    return DeploymentVersion(
        id="uuid-1",
        version=version,
        environment=env,
        deployed_at=datetime(2026, 8, 3, 10, 0, 0, tzinfo=timezone.utc),
    )


class TestRecordIfAbsent:
    @patch("codemie.service.deployment.deployment_version_service.deployment_version_repository")
    @patch("codemie.service.deployment.deployment_version_service.get_session")
    @patch("codemie.service.deployment.deployment_version_service.config")
    def test_inserts_when_no_record_exists(self, mock_cfg, mock_session_cm, mock_repo):
        mock_cfg.APP_VERSION = "1.0.0"
        mock_cfg.ENV = "dev"
        session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)
        mock_repo.get_by_version_and_env.return_value = None
        mock_repo.insert.return_value = _make_record()

        result = DeploymentVersionService.record_if_absent()

        mock_repo.get_by_version_and_env.assert_called_once_with("1.0.0", "dev", session)
        mock_repo.insert.assert_called_once_with("1.0.0", "dev", session)
        assert result.version == "1.0.0"

    @patch("codemie.service.deployment.deployment_version_service.deployment_version_repository")
    @patch("codemie.service.deployment.deployment_version_service.get_session")
    @patch("codemie.service.deployment.deployment_version_service.config")
    def test_skips_insert_when_record_exists(self, mock_cfg, mock_session_cm, mock_repo):
        mock_cfg.APP_VERSION = "1.0.0"
        mock_cfg.ENV = "dev"
        session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)
        existing = _make_record()
        mock_repo.get_by_version_and_env.return_value = existing

        result = DeploymentVersionService.record_if_absent()

        mock_repo.insert.assert_not_called()
        assert result is existing

    @patch("codemie.service.deployment.deployment_version_service.deployment_version_repository")
    @patch("codemie.service.deployment.deployment_version_service.get_session")
    @patch("codemie.service.deployment.deployment_version_service.config")
    def test_handles_integrity_error_from_concurrent_insert(self, mock_cfg, mock_session_cm, mock_repo):
        """Concurrent workers: second worker gets IntegrityError on insert — must return None gracefully."""
        mock_cfg.APP_VERSION = "1.0.0"
        mock_cfg.ENV = "dev"
        session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)
        mock_repo.get_by_version_and_env.return_value = None
        mock_repo.insert.side_effect = IntegrityError("uq", {}, Exception())

        result = DeploymentVersionService.record_if_absent()

        assert result is None  # treated as non-error: another worker already inserted


class TestGetDeployedAt:
    @patch("codemie.service.deployment.deployment_version_service.deployment_version_repository")
    @patch("codemie.service.deployment.deployment_version_service.get_session")
    @patch("codemie.service.deployment.deployment_version_service.config")
    def test_returns_deployed_at_when_record_exists(self, mock_cfg, mock_session_cm, mock_repo):
        mock_cfg.APP_VERSION = "1.0.0"
        mock_cfg.ENV = "dev"
        session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)
        mock_repo.get_by_version_and_env.return_value = _make_record()

        result = DeploymentVersionService.get_deployed_at()

        assert result == datetime(2026, 8, 3, 10, 0, 0, tzinfo=timezone.utc)

    @patch("codemie.service.deployment.deployment_version_service.deployment_version_repository")
    @patch("codemie.service.deployment.deployment_version_service.get_session")
    @patch("codemie.service.deployment.deployment_version_service.config")
    def test_returns_none_when_no_record(self, mock_cfg, mock_session_cm, mock_repo):
        mock_cfg.APP_VERSION = "1.0.0"
        mock_cfg.ENV = "dev"
        session = MagicMock()
        mock_session_cm.return_value.__enter__ = MagicMock(return_value=session)
        mock_session_cm.return_value.__exit__ = MagicMock(return_value=False)
        mock_repo.get_by_version_and_env.return_value = None

        result = DeploymentVersionService.get_deployed_at()

        assert result is None
```

- [ ] **Step 1: Write the failing tests**

Write `tests/codemie/service/deployment/test_deployment_version_service.py` with the content above.

- [ ] **Step 2: Run to confirm failure**

```bash
poetry run pytest tests/codemie/service/deployment/test_deployment_version_service.py -v
```

Expected: `ImportError` — service module does not exist yet.

- [ ] **Step 3: Create the service**

```python
# src/codemie/service/deployment/deployment_version_service.py
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
Called at startup (record_if_absent) and per request (get_deployed_at).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.exc import IntegrityError

from codemie.clients.postgres import get_session
from codemie.configs import config
from codemie.rest_api.models.deployment_version import DeploymentVersion
from codemie.service.deployment.deployment_version_repository import deployment_version_repository


class DeploymentVersionService:
    @classmethod
    def record_if_absent(cls) -> DeploymentVersion | None:
        """Insert a deployment record for (APP_VERSION, ENV) if none exists.

        Safe for concurrent workers: an IntegrityError from the unique constraint
        means another worker already inserted the row — treated as success (returns None).
        Non-fatal: all exceptions are caught; caller must handle None gracefully.
        """
        with get_session() as session:
            existing = deployment_version_repository.get_by_version_and_env(
                config.APP_VERSION, config.ENV, session
            )
            if existing:
                return existing
            try:
                return deployment_version_repository.insert(config.APP_VERSION, config.ENV, session)
            except IntegrityError:
                # Another worker inserted concurrently — not an error.
                return None

    @classmethod
    def get_deployed_at(cls) -> datetime | None:
        """Return deployed_at for (APP_VERSION, ENV), or None if no record exists."""
        with get_session() as session:
            record = deployment_version_repository.get_by_version_and_env(
                config.APP_VERSION, config.ENV, session
            )
            return record.deployed_at if record else None


deployment_version_service = DeploymentVersionService()
```

- [ ] **Step 4: Run to confirm tests pass**

```bash
poetry run pytest tests/codemie/service/deployment/ -v
```

Expected: all 8 tests across both service and repository test files PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/service/deployment/deployment_version_service.py \
        tests/codemie/service/deployment/test_deployment_version_service.py
git commit -m "$(cat <<'EOF'
feat: add DeploymentVersionService with record_if_absent and get_deployed_at

Classmethod-only service. record_if_absent catches IntegrityError for
concurrent-worker safety. get_deployed_at used by /v1/info handler.

Generated with AI

Co-Authored-By: codemie-ai <codemie.ai@gmail.com>
EOF
)"
```

---

## Task 5: Startup Hook — `_initialize_deployment_version()` in `main.py`

**Files:**
- Modify: `src/codemie/rest_api/main.py`

**Test-first: no** — startup hooks in `main.py` are not unit-tested directly (they are integration-level). The service-level behavior is fully covered by Task 4 tests. The correctness of the hook is verified by the `_check_sharepoint_pkce_redis()` precedent pattern at line 360–370 and by confirming the server starts without error (Task 7 smoke test).

**Change description:**

At `src/codemie/rest_api/main.py`:

1. Add the `_initialize_deployment_version()` helper function. Insert it after `_check_sharepoint_pkce_redis()` (currently ending around line 370), before `_setup_conversation_analysis_scheduler`. The function follows the exact same non-fatal warning pattern established by `_check_sharepoint_pkce_redis()` (lines 360–370):

```python
def _initialize_deployment_version() -> None:
    """Record first-start timestamp for (APP_VERSION, ENV) if not already stored.

    Non-fatal: if the database is unreachable or the insert fails for any reason,
    log a warning and continue. /v1/info will return deployed_at=None until the
    next successful startup.
    """
    try:
        from codemie.service.deployment.deployment_version_service import deployment_version_service

        deployment_version_service.record_if_absent()
        from codemie.configs import logger

        logger.info(f"Deployment version recorded for version={__import__('codemie.configs', fromlist=['config']).config.APP_VERSION!r} env={__import__('codemie.configs', fromlist=['config']).config.ENV!r}")
    except Exception as exc:
        from codemie.configs import logger

        logger.warning(f"Failed to record deployment version at startup: {exc}")
```

A cleaner version using the module-level `logger` and `config` already imported at the top of `main.py`:

```python
def _initialize_deployment_version() -> None:
    """Record first-start timestamp for (APP_VERSION, ENV) if not already stored.

    Non-fatal: a warning is logged and the application continues if the DB is
    unavailable at startup. The /v1/info endpoint will return deployed_at=None.
    """
    try:
        from codemie.service.deployment.deployment_version_service import deployment_version_service

        deployment_version_service.record_if_absent()
        logger.info(
            f"Deployment version recorded: version={config.APP_VERSION!r} env={config.ENV!r}"
        )
    except Exception as exc:
        logger.warning(f"Failed to record deployment version at startup: {exc}")
```

2. Call `_initialize_deployment_version()` from `lifespan()`. Insert the call after `_check_sharepoint_pkce_redis()` at line 713 (which currently reads `_check_sharepoint_pkce_redis()`), so the sequence reads:

```python
    # Initialize optional features
    _initialize_optional_features()
    _check_sharepoint_pkce_redis()
    _initialize_deployment_version()
```

- [ ] **Step 1: Add `_initialize_deployment_version()` helper to `main.py`**

Insert the function body (shown above) after `_check_sharepoint_pkce_redis()` (line 360–370 in the reference read).

- [ ] **Step 2: Add call in `lifespan()` after `_check_sharepoint_pkce_redis()`**

Locate the call at `main.py` line 713 and add `_initialize_deployment_version()` immediately after it.

- [ ] **Step 3: Verify no import errors**

```bash
poetry run python -c "from codemie.rest_api import main; print('OK')"
```

Expected: `OK` printed, no traceback.

- [ ] **Step 4: Commit**

```bash
git add src/codemie/rest_api/main.py
git commit -m "$(cat <<'EOF'
feat: call _initialize_deployment_version() in lifespan startup hook

Inserts (APP_VERSION, ENV) deployment record on first start.
Non-fatal: wrapped in try/except so DB unavailability at startup
only logs a warning and does not prevent app startup.

Generated with AI

Co-Authored-By: codemie-ai <codemie.ai@gmail.com>
EOF
)"
```

---

## Task 6: Response Model — `deployed_at` field on `InfoResponse`

**Files:**
- Modify: `src/codemie/core/models.py` lines 762–764
- Create: `tests/codemie/core/test_info_response_model.py` (new test file; create `tests/codemie/core/__init__.py` if it does not exist)

**Test-first: yes**

Failing test (fails before the change because `InfoResponse` does not have `deployed_at` yet):

```python
# tests/codemie/core/test_info_response_model.py
from __future__ import annotations

from datetime import datetime, timezone

from codemie.core.models import InfoResponse


def test_info_response_deployed_at_defaults_to_none():
    resp = InfoResponse(message="Codemie", version="1.0.0", description="desc")
    assert resp.deployed_at is None


def test_info_response_deployed_at_accepts_datetime():
    dt = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)
    resp = InfoResponse(message="Codemie", version="1.0.0", description="desc", deployed_at=dt)
    assert resp.deployed_at == dt


def test_info_response_deployed_at_serializes_as_camel_case():
    """InfoResponse inherits ConfiguredModel which has alias_generator=to_camel.
    deployed_at must serialize to deployedAt in JSON output.
    """
    dt = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)
    resp = InfoResponse(message="Codemie", version="1.0.0", description="desc", deployed_at=dt)
    data = resp.model_dump(by_alias=True)
    assert "deployedAt" in data
    assert "deployed_at" not in data
    assert data["deployedAt"] == dt
```

- [ ] **Step 1: Write the failing test**

Write `tests/codemie/core/test_info_response_model.py` with the content above. Create `tests/codemie/core/__init__.py` as empty if the directory does not exist.

- [ ] **Step 2: Run to confirm failure**

```bash
poetry run pytest tests/codemie/core/test_info_response_model.py -v
```

Expected: `test_info_response_deployed_at_defaults_to_none` fails with `TypeError` (unexpected keyword argument or wrong field count) and `test_info_response_deployed_at_serializes_as_camel_case` fails similarly.

- [ ] **Step 3: Add `deployed_at` to `InfoResponse`**

In `src/codemie/core/models.py` lines 762–764, change:

```python
class InfoResponse(BaseResponse):
    version: str
    description: str
```

to:

```python
class InfoResponse(BaseResponse):
    version: str
    description: str
    deployed_at: Optional[datetime] = None
```

The `Optional` and `datetime` imports are already present at `models.py` lines 20 and 18 respectively (`from typing import ... Optional` and `from datetime import datetime`). No new imports are needed.

- [ ] **Step 4: Run to confirm tests pass**

```bash
poetry run pytest tests/codemie/core/test_info_response_model.py -v
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/codemie/core/models.py \
        tests/codemie/core/__init__.py \
        tests/codemie/core/test_info_response_model.py
git commit -m "$(cat <<'EOF'
feat: add deployed_at field to InfoResponse

Optional[datetime] = None keeps the change backward-compatible.
Serializes as deployedAt (camelCase) via ConfiguredModel alias_generator.

Generated with AI

Co-Authored-By: codemie-ai <codemie.ai@gmail.com>
EOF
)"
```

---

## Task 7: Router — populate `deployed_at` in `app_info()`

**Files:**
- Modify: `src/codemie/rest_api/routers/common.py` lines 28–34
- Create: `tests/codemie/rest_api/routers/test_common_router.py`

**Test-first: yes**

Failing tests (the router handler currently does not call `deployment_version_service` at all, so `deployed_at` will be missing from the response):

```python
# tests/codemie/rest_api/routers/test_common_router.py
# Copyright 2026 EPAM Systems, Inc. ("EPAM")
#
# Licensed under the Apache License, Version 2.0 (the "License");
# ...

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from codemie.rest_api.routers import common as common_router

app = FastAPI()
app.include_router(common_router.router)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_app_info_returns_deployed_at_when_service_provides_value():
    dt = datetime(2026, 8, 3, 10, 0, 0, tzinfo=timezone.utc)
    with patch(
        "codemie.rest_api.routers.common.deployment_version_service"
    ) as mock_svc:
        mock_svc.get_deployed_at.return_value = dt
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/v1/info")

    assert resp.status_code == 200
    data = resp.json()
    assert "deployedAt" in data
    assert data["deployedAt"] is not None


@pytest.mark.anyio
async def test_app_info_returns_null_deployed_at_when_service_returns_none():
    with patch(
        "codemie.rest_api.routers.common.deployment_version_service"
    ) as mock_svc:
        mock_svc.get_deployed_at.return_value = None
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/v1/info")

    assert resp.status_code == 200
    data = resp.json()
    assert "deployedAt" in data
    assert data["deployedAt"] is None
```

- [ ] **Step 1: Write the failing tests**

Write `tests/codemie/rest_api/routers/test_common_router.py` with the content above.

- [ ] **Step 2: Run to confirm failure**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_common_router.py -v
```

Expected: both tests fail — `deployedAt` key is absent because `app_info()` does not yet import or call `deployment_version_service`.

- [ ] **Step 3: Update `app_info()` in `common.py`**

In `src/codemie/rest_api/routers/common.py`, replace the current `app_info()` body (lines 29–34):

```python
@router.get("/info", status_code=status.HTTP_200_OK, response_model=InfoResponse)
def app_info():
    return InfoResponse(
        message="Codemie",
        version=config.APP_VERSION,
        description=APP_DESCRIPTION,
    )
```

with:

```python
@router.get("/info", status_code=status.HTTP_200_OK, response_model=InfoResponse)
def app_info():
    from codemie.service.deployment.deployment_version_service import deployment_version_service

    return InfoResponse(
        message="Codemie",
        version=config.APP_VERSION,
        description=APP_DESCRIPTION,
        deployed_at=deployment_version_service.get_deployed_at(),
    )
```

The deferred import (`from ... import deployment_version_service` inside the function body) follows the same pattern used by other handlers in `main.py` for avoiding circular imports and keeping module-level overhead minimal.

- [ ] **Step 4: Run to confirm tests pass**

```bash
poetry run pytest tests/codemie/rest_api/routers/test_common_router.py -v
```

Expected: both tests PASS.

- [ ] **Step 5: Run the full test suite to check for regressions**

```bash
poetry run pytest tests/ -v --tb=short -q
```

Expected: all pre-existing tests still pass; new tests pass.

- [ ] **Step 6: Lint**

```bash
poetry run ruff check src/codemie/rest_api/routers/common.py \
                      src/codemie/service/deployment/ \
                      src/codemie/core/models.py
poetry run ruff format --check src/
```

Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/codemie/rest_api/routers/common.py \
        tests/codemie/rest_api/routers/test_common_router.py
git commit -m "$(cat <<'EOF'
feat: populate deployed_at in GET /v1/info response

Calls DeploymentVersionService.get_deployed_at() to fill the new
InfoResponse.deployed_at field. Returns null when no record exists.

Generated with AI

Co-Authored-By: codemie-ai <codemie.ai@gmail.com>
EOF
)"
```

---

## Spec Coverage Check

| Requirement | Task |
|---|---|
| Store deployment date scoped to current env (no multi-env write) | Task 1 — unique constraint on (version, environment); Task 5 — record uses `config.ENV` |
| Timestamp recorded automatically on service startup (first start of version in env) | Tasks 4–5 — `record_if_absent()` + `_initialize_deployment_version()` in `lifespan()` |
| `deployed_at` returned in `GET /v1/info` response | Tasks 6–7 — `InfoResponse.deployed_at` + `app_info()` update |
| Race condition handling (concurrent startup) | Task 4 — `IntegrityError` catch in `record_if_absent()` + test `test_handles_integrity_error_from_concurrent_insert` |
| Non-fatal startup failure | Task 5 — `try/except Exception` in `_initialize_deployment_version()`, following `_check_sharepoint_pkce_redis()` pattern at `main.py` line 360–370 |
| One commit per layer | Tasks 1–7 each specify an isolated `git commit` |
| `down_revision = "i1n2t3e4r5a6"` | Task 1 — confirmed from `i1n2t3e4r5a6_add_interactive_features_to_assistants.py` line 17 |
| `alembic/env.py` import for new model | Task 2 step 5 |
| `deployed_at` serializes as `deployedAt` (camelCase) | Task 6 test `test_info_response_deployed_at_serializes_as_camel_case` |

---

## Risk Mitigations Summary

| Risk | Mitigation in this plan |
|---|---|
| Race condition (concurrent workers, rolling restarts) | `IntegrityError` catch in `record_if_absent()` — Task 4, explicitly tested |
| Non-fatal DB unavailability at startup | `try/except Exception` + `logger.warning` in `_initialize_deployment_version()` — Task 5 |
| camelCase alias surprise for frontend | Task 6 test asserts `"deployedAt"` in `model_dump(by_alias=True)` |
| `APP_VERSION` not pipeline-injected | Operational gap — noted in risk section of technical analysis; no code change needed, but deployment pipeline must inject `APP_VERSION=<semver>` |
| Alembic chain break | Task 1 step 3 explicitly runs `alembic check`; `down_revision` pinned to confirmed head `i1n2t3e4r5a6` |
| `env.py` missing import | Task 2 step 5 adds the import; step 6 runs `alembic check` to confirm |

---

### Critical Files for Implementation

- `C:\Users\KonstantinShnyrkov\Work\codemie-dev\codemie\src\codemie\rest_api\main.py`
- `C:\Users\KonstantinShnyrkov\Work\codemie-dev\codemie\src\codemie\core\models.py`
- `C:\Users\KonstantinShnyrkov\Work\codemie-dev\codemie\src\codemie\rest_api\routers\common.py`
- `C:\Users\KonstantinShnyrkov\Work\codemie-dev\codemie\src\external\alembic\env.py`
- `C:\Users\KonstantinShnyrkov\Work\codemie-dev\codemie\src\codemie\service\activity\activity_repository.py`
