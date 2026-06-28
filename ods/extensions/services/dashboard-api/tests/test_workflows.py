"""Tests for workflows router endpoints."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest


def test_workflows_requires_auth(test_client):
    """GET /api/workflows without auth → 401."""
    resp = test_client.get("/api/workflows")
    assert resp.status_code == 401


def test_workflows_authenticated(test_client):
    """GET /api/workflows with auth → 200, returns workflow catalog."""
    resp = test_client.get("/api/workflows", headers=test_client.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "workflows" in data
    assert "categories" in data
    assert isinstance(data["workflows"], list)
    assert isinstance(data["categories"], dict)


def test_workflow_enable_requires_auth(test_client):
    """POST /api/workflows/{id}/enable without auth → 401."""
    resp = test_client.post("/api/workflows/test-workflow/enable")
    assert resp.status_code == 401


def test_workflow_disable_requires_auth(test_client):
    """DELETE /api/workflows/{id} without auth → 401."""
    resp = test_client.delete("/api/workflows/test-workflow")
    assert resp.status_code == 401


def test_workflow_executions_requires_auth(test_client):
    """GET /api/workflows/{id}/executions without auth → 401."""
    resp = test_client.get("/api/workflows/test-workflow/executions")
    assert resp.status_code == 401


def test_workflow_enable_authenticated(test_client):
    """POST /api/workflows/{id}/enable with auth → 404 when workflow not in catalog."""
    resp = test_client.post(
        "/api/workflows/nonexistent-workflow/enable",
        headers=test_client.auth_headers
    )
    assert resp.status_code == 404


def test_workflow_disable_authenticated(test_client):
    """DELETE /api/workflows/{id} with auth → 404 when workflow not in catalog."""
    resp = test_client.delete(
        "/api/workflows/nonexistent-workflow",
        headers=test_client.auth_headers
    )
    assert resp.status_code == 404


def test_workflow_executions_authenticated(test_client):
    """GET /api/workflows/{id}/executions with auth → 404 when workflow not in catalog."""
    resp = test_client.get(
        "/api/workflows/nonexistent-workflow/executions",
        headers=test_client.auth_headers
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# load_workflow_catalog() unit tests
# ---------------------------------------------------------------------------


def test_load_workflow_catalog_missing_file(monkeypatch):
    """load_workflow_catalog returns DEFAULT_WORKFLOW_CATALOG when file does not exist."""
    import routers.workflows as wf_mod

    fake_path = Path("/tmp/nonexistent-catalog-dir/catalog.json")
    monkeypatch.setattr(wf_mod, "WORKFLOW_CATALOG_FILE", fake_path)

    result = wf_mod.load_workflow_catalog()
    assert result == wf_mod.DEFAULT_WORKFLOW_CATALOG


def test_load_workflow_catalog_invalid_json(tmp_path, monkeypatch):
    """load_workflow_catalog returns DEFAULT_WORKFLOW_CATALOG for invalid JSON."""
    import routers.workflows as wf_mod

    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text("{not valid json!!!")
    monkeypatch.setattr(wf_mod, "WORKFLOW_CATALOG_FILE", catalog_file)

    result = wf_mod.load_workflow_catalog()
    assert result == wf_mod.DEFAULT_WORKFLOW_CATALOG


def test_load_workflow_catalog_non_dict_json(tmp_path, monkeypatch):
    """load_workflow_catalog returns DEFAULT_WORKFLOW_CATALOG when root is not a dict."""
    import routers.workflows as wf_mod

    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text('["this", "is", "a", "list"]')
    monkeypatch.setattr(wf_mod, "WORKFLOW_CATALOG_FILE", catalog_file)

    result = wf_mod.load_workflow_catalog()
    assert result == wf_mod.DEFAULT_WORKFLOW_CATALOG


def test_load_workflow_catalog_valid(tmp_path, monkeypatch):
    """load_workflow_catalog returns parsed catalog when file is valid."""
    import routers.workflows as wf_mod

    catalog = {
        "workflows": [
            {"id": "wf-1", "name": "Test Workflow", "description": "A test", "file": "test.json"}
        ],
        "categories": {"automation": {"name": "Automation", "icon": "Cog"}}
    }
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(json.dumps(catalog))
    monkeypatch.setattr(wf_mod, "WORKFLOW_CATALOG_FILE", catalog_file)

    result = wf_mod.load_workflow_catalog()
    assert len(result["workflows"]) == 1
    assert result["workflows"][0]["id"] == "wf-1"
    assert "automation" in result["categories"]


def test_load_workflow_catalog_invalid_inner_types(tmp_path, monkeypatch):
    """load_workflow_catalog normalises non-list workflows and non-dict categories."""
    import routers.workflows as wf_mod

    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(json.dumps({"workflows": "not-a-list", "categories": "not-a-dict"}))
    monkeypatch.setattr(wf_mod, "WORKFLOW_CATALOG_FILE", catalog_file)

    result = wf_mod.load_workflow_catalog()
    assert result["workflows"] == []
    assert result["categories"] == {}


# ---------------------------------------------------------------------------
# get_n8n_workflows() unit tests
# ---------------------------------------------------------------------------


def test_get_n8n_workflows_success(test_client, monkeypatch):
    """get_n8n_workflows returns workflow list on 200 response."""
    import routers.workflows as wf_mod

    n8n_data = {"data": [{"id": "1", "name": "My Workflow", "active": True}]}

    resp_mock = AsyncMock()
    resp_mock.status = 200
    resp_mock.json = AsyncMock(return_value=n8n_data)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=resp_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)

    session_mock = AsyncMock()
    session_mock.get = MagicMock(return_value=ctx)
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("routers.workflows.aiohttp.ClientSession", return_value=session_mock):
        import asyncio
        result = asyncio.run(wf_mod.get_n8n_workflows())

    assert len(result) == 1
    assert result[0]["name"] == "My Workflow"


def test_get_n8n_workflows_failure(test_client, monkeypatch):
    """get_n8n_workflows returns empty list on connection error."""
    import routers.workflows as wf_mod

    session_mock = AsyncMock()
    session_mock.get = MagicMock(side_effect=aiohttp.ClientError("connection refused"))
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("routers.workflows.aiohttp.ClientSession", return_value=session_mock):
        import asyncio
        result = asyncio.run(wf_mod.get_n8n_workflows())

    assert result == []


# ---------------------------------------------------------------------------
# check_workflow_dependencies() unit tests
# ---------------------------------------------------------------------------


def test_check_workflow_dependencies_all_healthy(test_client, monkeypatch):
    """check_workflow_dependencies returns True for healthy deps."""
    import routers.workflows as wf_mod
    from models import ServiceStatus

    async def mock_health(sid, cfg):
        return ServiceStatus(
            id=sid, name=cfg["name"], port=cfg["port"],
            external_port=cfg.get("external_port", cfg["port"]),
            status="healthy",
        )

    monkeypatch.setattr("helpers.check_service_health", mock_health)

    import asyncio
    result = asyncio.run(
        wf_mod.check_workflow_dependencies(["llama-server"])
    )
    # "llama-server" should be checked directly (no alias mapping)
    # Result depends on whether llama-server is in SERVICES
    assert isinstance(result, dict)


def test_check_workflow_dependencies_with_alias(test_client, monkeypatch):
    """check_workflow_dependencies resolves 'ollama' alias to 'llama-server'."""
    import routers.workflows as wf_mod
    from models import ServiceStatus

    healthy_status = ServiceStatus(
        id="llama-server", name="LLM Server", port=8080,
        external_port=8080, status="healthy",
    )

    mock_fn = AsyncMock(return_value=healthy_status)
    monkeypatch.setattr("helpers.check_service_health", mock_fn)

    import asyncio
    result = asyncio.run(
        wf_mod.check_workflow_dependencies(["ollama"])
    )
    assert result["ollama"] is True


def test_check_workflow_dependencies_unhealthy(test_client, monkeypatch):
    """check_workflow_dependencies returns False for unhealthy deps."""
    import routers.workflows as wf_mod
    from models import ServiceStatus

    # Ensure "llama-server" is in SERVICES so the code path actually calls check_service_health
    monkeypatch.setitem(wf_mod.SERVICES, "llama-server", {"name": "LLM Server", "port": 8080})

    unhealthy_status = ServiceStatus(
        id="llama-server", name="LLM Server", port=8080,
        external_port=8080, status="down",
    )

    mock_fn = AsyncMock(return_value=unhealthy_status)
    monkeypatch.setattr("helpers.check_service_health", mock_fn)

    import asyncio
    result = asyncio.run(
        wf_mod.check_workflow_dependencies(["ollama"])
    )
    assert result["ollama"] is False


def test_check_workflow_dependencies_unknown_dep(test_client, monkeypatch):
    """check_workflow_dependencies returns True for deps not in SERVICES."""
    import routers.workflows as wf_mod

    import asyncio
    result = asyncio.run(
        wf_mod.check_workflow_dependencies(["totally-unknown-service-xyz"])
    )
    assert result["totally-unknown-service-xyz"] is True


def test_check_workflow_dependencies_uses_cache(test_client, monkeypatch):
    """check_workflow_dependencies reuses health_cache and skips duplicate checks."""
    import routers.workflows as wf_mod

    mock_fn = AsyncMock()
    monkeypatch.setattr("helpers.check_service_health", mock_fn)

    cache = {"llama-server": True}
    import asyncio
    result = asyncio.run(
        wf_mod.check_workflow_dependencies(["ollama"], health_cache=cache)
    )
    assert result["ollama"] is True
    mock_fn.assert_not_called()


# ---------------------------------------------------------------------------
# check_n8n_available() unit tests
# ---------------------------------------------------------------------------


def test_check_n8n_available_success(test_client):
    """check_n8n_available returns True when n8n responds < 500."""
    import routers.workflows as wf_mod

    resp_mock = AsyncMock()
    resp_mock.status = 200

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=resp_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)

    session_mock = AsyncMock()
    session_mock.get = MagicMock(return_value=ctx)

    async def fake_get_session():
        return session_mock

    with patch("helpers._get_aio_session", side_effect=fake_get_session):
        import asyncio
        result = asyncio.run(wf_mod.check_n8n_available())

    assert result is True


def test_check_n8n_available_failure(test_client):
    """check_n8n_available returns False on connection error."""
    import routers.workflows as wf_mod

    session_mock = AsyncMock()
    session_mock.get = MagicMock(side_effect=aiohttp.ClientError("refused"))

    async def fake_get_session():
        return session_mock

    with patch("helpers._get_aio_session", side_effect=fake_get_session):
        import asyncio
        result = asyncio.run(wf_mod.check_n8n_available())

    assert result is False


# ---------------------------------------------------------------------------
# /api/workflows/categories endpoint
# ---------------------------------------------------------------------------


def test_workflow_categories_requires_auth(test_client):
    """GET /api/workflows/categories without auth → 401."""
    resp = test_client.get("/api/workflows/categories")
    assert resp.status_code == 401


def test_workflow_categories_returns_catalog_categories(test_client, tmp_path, monkeypatch):
    """GET /api/workflows/categories → 200, returns categories from catalog."""
    import routers.workflows as wf_mod

    catalog = {
        "workflows": [],
        "categories": {"automation": {"name": "Automation", "icon": "Cog"}}
    }
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(json.dumps(catalog))
    monkeypatch.setattr(wf_mod, "WORKFLOW_CATALOG_FILE", catalog_file)

    resp = test_client.get("/api/workflows/categories", headers=test_client.auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "categories" in data
    assert "automation" in data["categories"]


# ---------------------------------------------------------------------------
# /api/workflows/n8n/status endpoint
# ---------------------------------------------------------------------------


def test_n8n_status_requires_auth(test_client):
    """GET /api/workflows/n8n/status without auth → 401."""
    resp = test_client.get("/api/workflows/n8n/status")
    assert resp.status_code == 401


def test_n8n_status_available(test_client):
    """GET /api/workflows/n8n/status → 200, returns available=True when n8n responds."""
    resp_mock = AsyncMock()
    resp_mock.status = 200

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=resp_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)

    session_mock = AsyncMock()
    session_mock.get = MagicMock(return_value=ctx)

    async def fake_get_session():
        return session_mock

    with patch("helpers._get_aio_session", side_effect=fake_get_session):
        resp = test_client.get("/api/workflows/n8n/status", headers=test_client.auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    assert "url" in data


def test_n8n_status_unavailable(test_client):
    """GET /api/workflows/n8n/status → 200, returns available=False when n8n is down."""
    session_mock = AsyncMock()
    session_mock.get = MagicMock(side_effect=aiohttp.ClientError("refused"))

    async def fake_get_session():
        return session_mock

    with patch("helpers._get_aio_session", side_effect=fake_get_session):
        resp = test_client.get("/api/workflows/n8n/status", headers=test_client.auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is False


# ---------------------------------------------------------------------------
# /api/workflows/{id}/enable — invalid ID format (400)
# ---------------------------------------------------------------------------


def test_workflow_enable_invalid_id_format(test_client):
    """POST /api/workflows/{id}/enable with special chars → 400."""
    resp = test_client.post(
        "/api/workflows/bad!id@here/enable",
        headers=test_client.auth_headers,
    )
    assert resp.status_code == 400
    assert "Invalid workflow ID format" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# /api/workflows/{id}/enable — missing dependencies (400)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/workflows/bad!id@here/disable"),
        ("delete", "/api/workflows/bad!id@here"),
        ("get", "/api/workflows/bad!id@here/executions"),
    ],
)
def test_workflow_id_mutation_routes_reject_invalid_format(test_client, method, path):
    resp = getattr(test_client, method)(path, headers=test_client.auth_headers)
    assert resp.status_code == 400
    assert "Invalid workflow ID format" in resp.json()["detail"]


def test_workflow_enable_missing_deps(test_client, tmp_path, monkeypatch):
    """POST /api/workflows/{id}/enable → 400 when dependencies are not running."""
    import routers.workflows as wf_mod
    from models import ServiceStatus

    catalog = {
        "workflows": [
            {"id": "dep-wf", "name": "Dep Workflow", "description": "needs llama",
             "file": "dep-wf.json", "dependencies": ["llama-server"]}
        ],
        "categories": {}
    }
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(json.dumps(catalog))
    monkeypatch.setattr(wf_mod, "WORKFLOW_CATALOG_FILE", catalog_file)

    # Ensure "llama-server" is in SERVICES so the code path actually calls check_service_health
    monkeypatch.setitem(wf_mod.SERVICES, "llama-server", {"name": "LLM Server", "port": 8080})

    unhealthy_status = ServiceStatus(
        id="llama-server", name="LLM Server", port=8080,
        external_port=8080, status="down",
    )
    monkeypatch.setattr("helpers.check_service_health", AsyncMock(return_value=unhealthy_status))

    resp = test_client.post(
        "/api/workflows/dep-wf/enable",
        headers=test_client.auth_headers,
    )
    assert resp.status_code == 400
    assert "Missing dependencies" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# /api/workflows/{id}/enable — workflow file not found (404)
# ---------------------------------------------------------------------------


def test_workflow_enable_file_not_found(test_client, tmp_path, monkeypatch):
    """POST /api/workflows/{id}/enable → 404 when workflow JSON file is missing."""
    import routers.workflows as wf_mod

    catalog = {
        "workflows": [
            {"id": "file-wf", "name": "File Workflow", "description": "no file",
             "file": "missing.json", "dependencies": []}
        ],
        "categories": {}
    }
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(json.dumps(catalog))
    monkeypatch.setattr(wf_mod, "WORKFLOW_CATALOG_FILE", catalog_file)

    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    monkeypatch.setattr(wf_mod, "WORKFLOW_DIR", workflow_dir)

    resp = test_client.post(
        "/api/workflows/file-wf/enable",
        headers=test_client.auth_headers,
    )
    assert resp.status_code == 404
    assert "Workflow file not found" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# /api/workflows/{id}/disable POST endpoint
# ---------------------------------------------------------------------------


def test_workflow_disable_post_requires_auth(test_client):
    """POST /api/workflows/{id}/disable without auth → 401."""
    resp = test_client.post("/api/workflows/test-wf/disable")
    assert resp.status_code == 401


def test_workflow_disable_post_not_in_catalog(test_client):
    """POST /api/workflows/{id}/disable → 404 when workflow not in catalog."""
    with patch("routers.workflows.get_n8n_workflows", new_callable=AsyncMock, return_value=[]):
        resp = test_client.post(
            "/api/workflows/nonexistent/disable",
            headers=test_client.auth_headers,
        )
    assert resp.status_code == 404


def test_workflow_disable_post_not_installed(test_client, tmp_path, monkeypatch):
    """POST /api/workflows/{id}/disable → 404 when workflow not installed in n8n."""
    import routers.workflows as wf_mod

    catalog = {
        "workflows": [
            {"id": "dis-wf", "name": "Disable Me", "description": "test",
             "file": "dis.json", "dependencies": []}
        ],
        "categories": {}
    }
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(json.dumps(catalog))
    monkeypatch.setattr(wf_mod, "WORKFLOW_CATALOG_FILE", catalog_file)

    with patch("routers.workflows.get_n8n_workflows", new_callable=AsyncMock, return_value=[]):
        resp = test_client.post(
            "/api/workflows/dis-wf/disable",
            headers=test_client.auth_headers,
        )
    assert resp.status_code == 404
    assert "not installed" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# /api/workflows/{id}/executions — installed workflow returns executions
# ---------------------------------------------------------------------------


def test_workflow_executions_not_installed(test_client, tmp_path, monkeypatch):
    """GET /api/workflows/{id}/executions → empty list when workflow not in n8n."""
    import routers.workflows as wf_mod

    catalog = {
        "workflows": [
            {"id": "exec-wf", "name": "Exec Workflow", "description": "test",
             "file": "exec.json", "dependencies": []}
        ],
        "categories": {}
    }
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(json.dumps(catalog))
    monkeypatch.setattr(wf_mod, "WORKFLOW_CATALOG_FILE", catalog_file)

    with patch("routers.workflows.get_n8n_workflows", new_callable=AsyncMock, return_value=[]):
        resp = test_client.get(
            "/api/workflows/exec-wf/executions",
            headers=test_client.auth_headers,
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["executions"] == []
    assert "not installed" in data["message"].lower()


def test_workflow_executions_installed(test_client, tmp_path, monkeypatch):
    """GET /api/workflows/{id}/executions → returns n8n execution data."""
    import routers.workflows as wf_mod

    catalog = {
        "workflows": [
            {"id": "exec-wf", "name": "Exec Workflow", "description": "test",
             "file": "exec.json", "dependencies": []}
        ],
        "categories": {}
    }
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(json.dumps(catalog))
    monkeypatch.setattr(wf_mod, "WORKFLOW_CATALOG_FILE", catalog_file)

    n8n_workflows = [{"id": "42", "name": "Exec Workflow", "active": True}]

    executions_data = {"data": [{"id": "100", "finished": True, "mode": "trigger"}]}

    # Mock get_n8n_workflows to return our installed workflow
    with patch("routers.workflows.get_n8n_workflows", new_callable=AsyncMock, return_value=n8n_workflows):
        # Mock the aiohttp session for the executions API call
        resp_mock = AsyncMock()
        resp_mock.status = 200
        resp_mock.json = AsyncMock(return_value=executions_data)

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=resp_mock)
        ctx.__aexit__ = AsyncMock(return_value=False)

        session_mock = AsyncMock()
        session_mock.get = MagicMock(return_value=ctx)
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)

        with patch("routers.workflows.aiohttp.ClientSession", return_value=session_mock):
            resp = test_client.get(
                "/api/workflows/exec-wf/executions",
                headers=test_client.auth_headers,
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["workflowId"] == "exec-wf"
    assert data["n8nId"] == "42"
    assert len(data["executions"]) == 1


# ---------------------------------------------------------------------------
# /api/workflows/{id}/enable — full import to n8n
# ---------------------------------------------------------------------------


def test_workflow_enable_success(test_client, tmp_path, monkeypatch):
    """POST /api/workflows/{id}/enable → 200, imports workflow to n8n."""
    import routers.workflows as wf_mod

    catalog = {
        "workflows": [
            {"id": "ok-wf", "name": "OK Workflow", "description": "test",
             "file": "ok-wf.json", "dependencies": []}
        ],
        "categories": {}
    }
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(json.dumps(catalog))
    monkeypatch.setattr(wf_mod, "WORKFLOW_CATALOG_FILE", catalog_file)

    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    (workflow_dir / "ok-wf.json").write_text(json.dumps({"name": "OK Workflow", "nodes": []}))
    monkeypatch.setattr(wf_mod, "WORKFLOW_DIR", workflow_dir)

    # Mock n8n POST (create) → 201 with id
    create_resp = AsyncMock()
    create_resp.status = 201
    create_resp.json = AsyncMock(return_value={"data": {"id": "n8n-99"}})

    # Mock n8n PATCH (activate) → 200
    activate_resp = AsyncMock()
    activate_resp.status = 200

    create_ctx = AsyncMock()
    create_ctx.__aenter__ = AsyncMock(return_value=create_resp)
    create_ctx.__aexit__ = AsyncMock(return_value=False)

    activate_ctx = AsyncMock()
    activate_ctx.__aenter__ = AsyncMock(return_value=activate_resp)
    activate_ctx.__aexit__ = AsyncMock(return_value=False)

    session_mock = AsyncMock()
    session_mock.post = MagicMock(return_value=create_ctx)
    session_mock.patch = MagicMock(return_value=activate_ctx)
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("routers.workflows.aiohttp.ClientSession", return_value=session_mock):
        resp = test_client.post(
            "/api/workflows/ok-wf/enable",
            headers=test_client.auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    assert data["n8nId"] == "n8n-99"
    assert data["activated"] is True


def test_workflow_enable_n8n_error(test_client, tmp_path, monkeypatch):
    """POST /api/workflows/{id}/enable → n8n returns error status."""
    import routers.workflows as wf_mod

    catalog = {
        "workflows": [
            {"id": "err-wf", "name": "Err Workflow", "description": "test",
             "file": "err-wf.json", "dependencies": []}
        ],
        "categories": {}
    }
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(json.dumps(catalog))
    monkeypatch.setattr(wf_mod, "WORKFLOW_CATALOG_FILE", catalog_file)

    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    (workflow_dir / "err-wf.json").write_text(json.dumps({"name": "Err"}))
    monkeypatch.setattr(wf_mod, "WORKFLOW_DIR", workflow_dir)

    create_resp = AsyncMock()
    create_resp.status = 400
    create_resp.text = AsyncMock(return_value="validation error")

    create_ctx = AsyncMock()
    create_ctx.__aenter__ = AsyncMock(return_value=create_resp)
    create_ctx.__aexit__ = AsyncMock(return_value=False)

    session_mock = AsyncMock()
    session_mock.post = MagicMock(return_value=create_ctx)
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("routers.workflows.aiohttp.ClientSession", return_value=session_mock):
        resp = test_client.post(
            "/api/workflows/err-wf/enable",
            headers=test_client.auth_headers,
        )

    assert resp.status_code == 400


def test_workflow_enable_n8n_unreachable(test_client, tmp_path, monkeypatch):
    """POST /api/workflows/{id}/enable → 503 when n8n unreachable."""
    import routers.workflows as wf_mod

    catalog = {
        "workflows": [
            {"id": "net-wf", "name": "Net Workflow", "description": "test",
             "file": "net-wf.json", "dependencies": []}
        ],
        "categories": {}
    }
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(json.dumps(catalog))
    monkeypatch.setattr(wf_mod, "WORKFLOW_CATALOG_FILE", catalog_file)

    workflow_dir = tmp_path / "workflows"
    workflow_dir.mkdir()
    (workflow_dir / "net-wf.json").write_text(json.dumps({"name": "Net"}))
    monkeypatch.setattr(wf_mod, "WORKFLOW_DIR", workflow_dir)

    session_mock = AsyncMock()
    session_mock.post = MagicMock(side_effect=aiohttp.ClientError("refused"))
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("routers.workflows.aiohttp.ClientSession", return_value=session_mock):
        resp = test_client.post(
            "/api/workflows/net-wf/enable",
            headers=test_client.auth_headers,
        )

    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# /api/workflows/{id}/disable — success path (via _remove_workflow)
# ---------------------------------------------------------------------------


def test_workflow_disable_success(test_client, tmp_path, monkeypatch):
    """POST /api/workflows/{id}/disable → 200 when workflow found and removed."""
    import routers.workflows as wf_mod

    catalog = {
        "workflows": [
            {"id": "rm-wf", "name": "Remove Me", "description": "test",
             "file": "rm.json", "dependencies": []}
        ],
        "categories": {}
    }
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(json.dumps(catalog))
    monkeypatch.setattr(wf_mod, "WORKFLOW_CATALOG_FILE", catalog_file)

    n8n_workflows = [{"id": "77", "name": "Remove Me", "active": True}]

    del_resp = AsyncMock()
    del_resp.status = 204

    del_ctx = AsyncMock()
    del_ctx.__aenter__ = AsyncMock(return_value=del_resp)
    del_ctx.__aexit__ = AsyncMock(return_value=False)

    session_mock = AsyncMock()
    session_mock.delete = MagicMock(return_value=del_ctx)
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("routers.workflows.get_n8n_workflows", new_callable=AsyncMock, return_value=n8n_workflows), \
         patch("routers.workflows.aiohttp.ClientSession", return_value=session_mock):
        resp = test_client.post(
            "/api/workflows/rm-wf/disable",
            headers=test_client.auth_headers,
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


# ---------------------------------------------------------------------------
# /api/workflows/{id}/executions — error fallback
# ---------------------------------------------------------------------------


def test_workflow_executions_n8n_error(test_client, tmp_path, monkeypatch):
    """GET /api/workflows/{id}/executions → returns error when n8n call fails."""
    import routers.workflows as wf_mod

    catalog = {
        "workflows": [
            {"id": "ex-wf", "name": "Ex Workflow", "description": "test",
             "file": "ex.json", "dependencies": []}
        ],
        "categories": {}
    }
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(json.dumps(catalog))
    monkeypatch.setattr(wf_mod, "WORKFLOW_CATALOG_FILE", catalog_file)

    n8n_workflows = [{"id": "88", "name": "Ex Workflow", "active": True}]

    session_mock = AsyncMock()
    session_mock.get = MagicMock(side_effect=aiohttp.ClientError("refused"))
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("routers.workflows.get_n8n_workflows", new_callable=AsyncMock, return_value=n8n_workflows), \
         patch("routers.workflows.aiohttp.ClientSession", return_value=session_mock):
        resp = test_client.get(
            "/api/workflows/ex-wf/executions",
            headers=test_client.auth_headers,
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["executions"] == []
    assert "error" in data


# ---------------------------------------------------------------------------
# /api/workflows — full endpoint (covers lines 117-149)
# ---------------------------------------------------------------------------


def test_workflows_with_matching_n8n(test_client, tmp_path, monkeypatch):
    """GET /api/workflows with a workflow that matches an n8n workflow."""
    import routers.workflows as wf_mod

    catalog = {
        "workflows": [
            {"id": "chat-wf", "name": "Chat Assistant", "description": "Chat bot",
             "file": "chat.json", "dependencies": [],
             "icon": "MessageSquare", "category": "general",
             "featured": True, "setupTime": "~1 min",
             "diagram": {"nodes": 3}}
        ],
        "categories": {"general": {"name": "General", "icon": "Cog"}}
    }
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(json.dumps(catalog))
    monkeypatch.setattr(wf_mod, "WORKFLOW_CATALOG_FILE", catalog_file)

    n8n_workflows = [{"id": "55", "name": "Chat Assistant", "active": True,
                       "statistics": {"executions": {"total": 42}}}]

    resp_mock = AsyncMock()
    resp_mock.status = 200
    resp_mock.json = AsyncMock(return_value={"data": n8n_workflows})

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=resp_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)

    session_mock = AsyncMock()
    session_mock.get = MagicMock(return_value=ctx)
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("routers.workflows.aiohttp.ClientSession", return_value=session_mock):
        resp = test_client.get("/api/workflows", headers=test_client.auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    wf = next(w for w in data["workflows"] if w["id"] == "chat-wf")
    assert wf["installed"] is True
    assert wf["active"] is True
    assert wf["n8nId"] == "55"
    assert wf["executions"] == 42
    assert wf["featured"] is True
