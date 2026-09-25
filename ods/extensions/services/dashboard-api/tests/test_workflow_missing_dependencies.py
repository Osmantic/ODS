"""Unknown workflow dependencies cannot pass the public enable prerequisite."""
import json
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.fixture
def workflow_catalog(monkeypatch, tmp_path):
    import routers.workflows as workflows
    catalog = {"categories": {}, "workflows": [{
        "id": "example", "name": "Example", "description": "Example workflow",
        "file": "example.json", "dependencies": ["missing-extension"],
    }]}
    (tmp_path / "example.json").write_text(json.dumps({"name": "Example", "nodes": []}))
    monkeypatch.setattr(workflows, "WORKFLOW_DIR", tmp_path)
    monkeypatch.setattr(workflows, "load_workflow_catalog", lambda: catalog)
    monkeypatch.setattr(workflows, "SERVICES", {
        "llama-server": {"name": "LLM", "port": 8080},
    })
    monkeypatch.setattr(workflows, "get_n8n_workflows", AsyncMock(return_value=[]))
    monkeypatch.setattr(workflows, "check_n8n_available", AsyncMock(return_value=True))
    return workflows, catalog


def test_catalog_does_not_claim_unknown_dependencies_are_ready(test_client, workflow_catalog):
    response = test_client.get("/api/workflows", headers=test_client.auth_headers)
    assert response.status_code == 200
    entry = response.json()["workflows"][0]
    assert entry["dependencyStatus"] == {"missing-extension": False}
    assert entry["allDependenciesMet"] is False


def test_enable_stops_before_n8n_mutation_for_unknown_dependency(
    test_client, workflow_catalog, monkeypatch,
):
    workflows, _ = workflow_catalog
    upstream = Mock(side_effect=AssertionError("n8n must not be contacted"))
    monkeypatch.setattr(workflows.aiohttp, "ClientSession", upstream)
    response = test_client.post("/api/workflows/example/enable", headers=test_client.auth_headers)
    assert response.status_code == 400
    assert "Missing dependencies: missing-extension" in response.json()["detail"]
    upstream.assert_not_called()


@pytest.mark.parametrize("status,expected", [("healthy", True), ("down", False)])
def test_known_legacy_alias_still_uses_the_real_health_probe(
    test_client, workflow_catalog, monkeypatch, status, expected,
):
    from models import ServiceStatus
    _, catalog = workflow_catalog
    catalog["workflows"][0]["dependencies"] = ["ollama", "llama-server"]
    health = AsyncMock(return_value=ServiceStatus(
        id="llama-server", name="LLM", port=8080, external_port=8080, status=status))
    monkeypatch.setattr("helpers.check_service_health", health)
    response = test_client.get("/api/workflows", headers=test_client.auth_headers)
    entry = response.json()["workflows"][0]
    assert entry["dependencyStatus"] == {"ollama": expected, "llama-server": expected}
    assert entry["allDependenciesMet"] is expected
    health.assert_awaited_once()
