# List Deployment Versions API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose all first-start deployment timestamps via authenticated `GET /v1/deployment-versions`, and remove `deployedAt` from `GET /v1/info`.

**Architecture:** Keep the existing write-once `deployment_versions` table and startup `record_if_absent`. Revert the info-endpoint date field. Add `list_all` / `list_deployments` and a new authenticated list route. Frontend is out of scope.

**Tech Stack:** FastAPI, SQLModel, Postgres, pytest, existing `authenticate` dependency, `ConfiguredModel` camelCase aliases.

**Spec:** `docs/superpowers/specs/2026-08-04-list-deployment-versions-api-design.md`

## Global Constraints

- Backend only — no `codemie-ui` changes
- Do not change Alembic migration / table schema
- Keep startup `record_if_absent` non-fatal
- Deployment dates must not appear on `GET /v1/info`
- List endpoint requires `Depends(authenticate)` from `codemie.rest_api.security.authentication`
- Sort list by `deployed_at DESC`
- Commit only when the user explicitly asks (do not auto-commit)

---

### Task 1: Revert `deployed_at` from `/v1/info`

**Files:**
- Modify: `src/codemie/core/models.py` (`InfoResponse`)
- Modify: `src/codemie/rest_api/routers/common.py` (`app_info`)
- Modify: `src/codemie/service/deployment/deployment_version_service.py` (remove `get_deployed_at`)
- Modify: `src/codemie/rest_api/main.py` (startup docstring only)
- Delete or rewrite: `tests/codemie/core/test_info_response_model.py` (remove deployed_at assertions; keep file only if other tests remain — otherwise delete file)
- Modify: `tests/codemie/rest_api/routers/test_common_router.py` (replace deployed_at tests with “info has no deployedAt”)
- Modify: `tests/codemie/service/deployment/test_deployment_version_service.py` (remove `get_deployed_at` tests)

**Interfaces:**
- Consumes: existing `InfoResponse`, `app_info`, `DeploymentVersionService`
- Produces: `InfoResponse` without `deployed_at`; service without `get_deployed_at`

- [ ] **Step 1: Rewrite failing/desired router tests for info without date**

Replace the two `deployed_at` router tests in `test_common_router.py` with:

```python
@pytest.mark.anyio
async def test_app_info_does_not_include_deployed_at():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/info")

    assert resp.status_code == 200
    data = resp.json()
    assert "deployedAt" not in data
    assert "deployed_at" not in data
    assert "version" in data
```

- [ ] **Step 2: Remove `deployed_at` from `InfoResponse` and `app_info()`**

`InfoResponse` must be:

```python
class InfoResponse(BaseResponse):
    version: str
    description: str
```

`app_info()` must be:

```python
@router.get("/info", status_code=status.HTTP_200_OK, response_model=InfoResponse)
def app_info():
    return InfoResponse(
        message="Codemie",
        version=config.APP_VERSION,
        description=APP_DESCRIPTION,
    )
```

- [ ] **Step 3: Remove `get_deployed_at` from service + its tests**

Delete `DeploymentVersionService.get_deployed_at` and the corresponding tests in `test_deployment_version_service.py`. Update the module docstring so it no longer mentions per-request `get_deployed_at`.

Update `_initialize_deployment_version` docstring in `main.py` to say recording failure is non-fatal and does not block startup (do not mention `/v1/info` returning `deployed_at`).

- [ ] **Step 4: Clean `InfoResponse` model tests**

Remove or rewrite `tests/codemie/core/test_info_response_model.py` so nothing asserts `deployed_at` / `deployedAt` on `InfoResponse`. If the file only tested that field, delete the file.

- [ ] **Step 5: Run tests**

Run:

```bash
pytest tests/codemie/rest_api/routers/test_common_router.py tests/codemie/service/deployment/test_deployment_version_service.py tests/codemie/core/test_info_response_model.py -v
```

Expected: PASS (or `test_info_response_model.py` absent and the other two PASS).

---

### Task 2: Repository `list_all`

**Files:**
- Modify: `src/codemie/service/deployment/deployment_version_repository.py`
- Modify: `tests/codemie/service/deployment/test_deployment_version_repository.py`

**Interfaces:**
- Consumes: `DeploymentVersion`, SQLModel `Session`, `select`
- Produces: `DeploymentVersionRepository.list_all(session) -> list[DeploymentVersion]` ordered by `deployed_at DESC`

- [ ] **Step 1: Write failing repository tests**

```python
def test_list_all_returns_empty_when_no_rows():
    session = MagicMock()
    session.exec.return_value.all.return_value = []
    result = _repo().list_all(session)
    assert result == []


def test_list_all_orders_by_deployed_at_desc():
    newer = DeploymentVersion(
        id="1",
        version="2.0.0",
        deployed_at=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )
    older = DeploymentVersion(
        id="2",
        version="1.0.0",
        deployed_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    session = MagicMock()
    session.exec.return_value.all.return_value = [newer, older]
    result = _repo().list_all(session)
    assert [r.version for r in result] == ["2.0.0", "1.0.0"]
    # Assert the select was built with order_by deployed_at desc — either by
    # inspecting session.exec.call_args[0][0] or trusting SQLModel order_by
    # if the project prefers a lighter mock (match existing repo test style).
```

Adapt assertions to the existing mock style in `test_deployment_version_repository.py`.

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/codemie/service/deployment/test_deployment_version_repository.py -v
```

Expected: FAIL — `list_all` missing.

- [ ] **Step 3: Implement `list_all`**

```python
def list_all(self, session: Session) -> list[DeploymentVersion]:
    stmt = select(DeploymentVersion).order_by(DeploymentVersion.deployed_at.desc())
    return list(session.exec(stmt).all())
```

Add the abstract method on `DeploymentVersionRepository` with the same signature.

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/codemie/service/deployment/test_deployment_version_repository.py -v
```

---

### Task 3: Service `list_deployments`

**Files:**
- Modify: `src/codemie/service/deployment/deployment_version_service.py`
- Modify: `tests/codemie/service/deployment/test_deployment_version_service.py`

**Interfaces:**
- Consumes: `deployment_version_repository.list_all`, `get_session`
- Produces: `DeploymentVersionService.list_deployments() -> list[DeploymentVersion]`

- [ ] **Step 1: Write failing service test**

```python
@patch("codemie.service.deployment.deployment_version_service.deployment_version_repository")
@patch("codemie.service.deployment.deployment_version_service.get_session")
def test_list_deployments_returns_repo_rows(mock_session_cm, mock_repo):
    session = MagicMock()
    mock_session_cm.return_value.__enter__.return_value = session
    rows = [_make_record("2.0.0"), _make_record("1.0.0")]
    mock_repo.list_all.return_value = rows

    result = DeploymentVersionService.list_deployments()

    assert result == rows
    mock_repo.list_all.assert_called_once_with(session)
```

- [ ] **Step 2: Run — expect FAIL**

```bash
pytest tests/codemie/service/deployment/test_deployment_version_service.py::test_list_deployments_returns_repo_rows -v
```

- [ ] **Step 3: Implement**

```python
@classmethod
def list_deployments(cls) -> list[DeploymentVersion]:
    """Return all deployment version records for this environment."""
    with get_session() as session:
        return deployment_version_repository.list_all(session)
```

Update module docstring: called at startup (`record_if_absent`) and for list API (`list_deployments`).

- [ ] **Step 4: Run — expect PASS**

```bash
pytest tests/codemie/service/deployment/test_deployment_version_service.py -v
```

---

### Task 4: Response models + authenticated `GET /v1/deployment-versions`

**Files:**
- Modify: `src/codemie/core/models.py` — add `DeploymentVersionItem`, `DeploymentVersionsResponse`
- Modify: `src/codemie/rest_api/routers/common.py` — add route
- Modify: `tests/codemie/rest_api/routers/test_common_router.py` — list endpoint tests
- Create (optional): `tests/codemie/core/test_deployment_versions_response_model.py` — camelCase serialization

**Interfaces:**
- Consumes: `deployment_version_service.list_deployments`, `authenticate`, `User`
- Produces: `GET /v1/deployment-versions` → `{ "deployments": [ { "version", "deployedAt" } ] }`

- [ ] **Step 1: Add response models**

Near other response models in `core/models.py`:

```python
class DeploymentVersionItem(ConfiguredModel):
    version: str
    deployed_at: datetime


class DeploymentVersionsResponse(ConfiguredModel):
    deployments: list[DeploymentVersionItem]
```

- [ ] **Step 2: Write router tests**

Use `app.dependency_overrides` on `authenticate` imported into `common` (or patch the dependency the route uses). Pattern:

```python
from codemie.rest_api.security.authentication import authenticate
from codemie.core.models import User  # use whatever User type authenticate returns — match assistant router tests

# In fixture / test setup:
app.dependency_overrides[authenticate] = lambda: mock_user

@pytest.mark.anyio
async def test_list_deployment_versions_empty():
    with patch(
        "codemie.service.deployment.deployment_version_service.DeploymentVersionService.list_deployments",
        return_value=[],
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/v1/deployment-versions")
    assert resp.status_code == 200
    assert resp.json() == {"deployments": []}


@pytest.mark.anyio
async def test_list_deployment_versions_returns_camel_case_items():
    record = DeploymentVersion(
        id="1",
        version="2.40.0",
        deployed_at=datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc),
    )
    with patch(
        "codemie.service.deployment.deployment_version_service.DeploymentVersionService.list_deployments",
        return_value=[record],
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/v1/deployment-versions")
    data = resp.json()
    assert data["deployments"][0]["version"] == "2.40.0"
    assert "deployedAt" in data["deployments"][0]
    assert "deployed_at" not in data["deployments"][0]


@pytest.mark.anyio
async def test_list_deployment_versions_requires_auth():
    # Clear overrides so authenticate runs; expect 401/403 without credentials
    app.dependency_overrides.pop(authenticate, None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/deployment-versions")
    assert resp.status_code in (401, 403)
```

Exact status code: match whatever `authenticate` returns in this test harness (inspect a similar router test if needed).

- [ ] **Step 3: Implement route in `common.py`**

```python
from fastapi import Depends
from codemie.core.models import DeploymentVersionItem, DeploymentVersionsResponse, InfoResponse
from codemie.rest_api.security.authentication import authenticate
from codemie.rest_api.models.user import User  # confirm actual User import path used by authenticate

@router.get(
    "/deployment-versions",
    status_code=status.HTTP_200_OK,
    response_model=DeploymentVersionsResponse,
)
def list_deployment_versions(user: User = Depends(authenticate)):
    from codemie.service.deployment.deployment_version_service import deployment_version_service

    records = deployment_version_service.list_deployments()
    return DeploymentVersionsResponse(
        deployments=[
            DeploymentVersionItem(version=r.version, deployed_at=r.deployed_at)
            for r in records
        ]
    )
```

Resolve the exact `User` type from `authenticate`’s signature in `authentication.py` and use that import (do not invent a new user type).

- [ ] **Step 4: Run focused + related tests**

```bash
pytest tests/codemie/rest_api/routers/test_common_router.py tests/codemie/service/deployment/ tests/codemie/core/test_info_response_model.py -v
```

Expected: all PASS; `/v1/info` has no `deployedAt`; list endpoint returns camelCase list.

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| Remove `deployedAt` from `/v1/info` | Task 1 |
| Remove `get_deployed_at` | Task 1 |
| `list_all` ordered DESC | Task 2 |
| `list_deployments` | Task 3 |
| `GET /v1/deployment-versions` + auth | Task 4 |
| Response camelCase `deployedAt` | Task 4 |
| Empty list `[]` | Task 4 |
| No UI / no schema change / keep startup write | Global + Tasks 1–4 leave write path intact |
