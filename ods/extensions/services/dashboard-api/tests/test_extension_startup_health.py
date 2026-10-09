"""Follow real health reconciliation through the cache and Library endpoints."""
import asyncio
from unittest.mock import AsyncMock

import pytest

import helpers
from models import ServiceStatus
from routers import extensions
from test_extensions import _make_catalog_ext, _patch_extensions_config


@pytest.fixture
def startup_fixture(monkeypatch, tmp_path):
    catalog = [{**_make_catalog_ext("n8n", "n8n"), "catalog_source": "builtin"}]
    _patch_extensions_config(monkeypatch, catalog, tmp_path=tmp_path)
    builtin = tmp_path / "builtin" / "n8n"
    builtin.mkdir(parents=True)
    (builtin / "compose.yaml").write_text("services: {n8n: {image: n8n}}\n")
    monkeypatch.setattr(extensions, "EXTENSIONS_DIR", builtin.parent)
    monkeypatch.setattr(extensions, "_read_progress", lambda _: None)
    config = {"name": "n8n", "port": 5678, "external_port": 5678,
              "type": "docker", "container_name": "ods-n8n"}
    monkeypatch.setattr(helpers, "SERVICES", {"n8n": config})
    monkeypatch.setattr(helpers, "load_extension_manifests", lambda *a, **k: ({"n8n": config}, [], []))
    monkeypatch.setattr(helpers, "LLM_BACKEND", "llama")
    monkeypatch.setattr(helpers, "_services_cache", None)

    def sample(probe="down", state="running", health="starting", *, rows=None, schema="ods.host-service-health.v1"):
        monkeypatch.setattr(helpers, "check_service_health", AsyncMock(return_value=ServiceStatus(
            id="n8n", name="n8n", port=5678, external_port=5678, status=probe,
        )))
        containers = rows if rows is not None else [{
            "service_id": "n8n", "container_name": "ods-n8n", "state": state, "health": health,
        }]
        monkeypatch.setattr(helpers, "request_agent_json", AsyncMock(return_value={
            "schema_version": schema, "containers": containers,
        }))
        statuses = asyncio.run(helpers.get_all_services())
        helpers.set_services_cache(statuses)
        return statuses[0]

    return sample, builtin


def read_library(test_client):
    catalog = test_client.get("/api/extensions/catalog", headers=test_client.auth_headers)
    detail = test_client.get("/api/extensions/n8n", headers=test_client.auth_headers)
    assert catalog.status_code == detail.status_code == 200
    return [next(row for row in catalog.json()["extensions"] if row["id"] == "n8n"), detail.json()]


@pytest.mark.parametrize("probe", ["down", "degraded"])
def test_declared_startup_survives_cache_then_clears_on_health_or_failure(startup_fixture, test_client, probe):
    sample, _ = startup_fixture
    status = sample(probe)
    for row in read_library(test_client):
        assert row["status"] == "installing"
        assert row["runtime_starting"] is True
    assert status.startup_pending is True
    assert "startup_pending" not in status.model_dump()  # Internal provenance, no general API schema change.

    sample("healthy", health="healthy")
    for row in read_library(test_client):
        assert row["status"] == "enabled"
        assert row["runtime_starting"] is False

    sample("down", health="unhealthy")
    for row in read_library(test_client):
        assert row["status"] == "unhealthy"
        assert row["runtime_starting"] is False


@pytest.mark.parametrize("sample_args,expected", [
    ({"probe": "unhealthy"}, "unhealthy"),  # HTTP error must never become startup.
    ({"probe": "degraded", "health": "none"}, "unhealthy"),
    ({"probe": "degraded", "rows": []}, "unhealthy"),
    ({"probe": "down", "health": "unhealthy"}, "unhealthy"),
    ({"probe": "down", "state": "exited"}, "stopped"),
    ({"probe": "down", "schema": "wrong"}, "stopped"),
    ({"probe": "degraded", "rows": [
        {"service_id": "n8n", "state": "running", "health": "starting"},
        {"service_id": "n8n", "state": "running", "health": "starting"},
    ]}, "unhealthy"),
    ({"probe": "degraded", "rows": [
        {"service_id": "n8n", "state": "running", "health": "starting"},
        {"service_id": "other", "container_name": "ods-n8n", "state": "running", "health": "unhealthy"},
    ]}, "unhealthy"),
])
def test_startup_requires_unambiguous_evidence_and_never_hides_failure(startup_fixture, test_client, sample_args, expected):
    sample, _ = startup_fixture
    sample(**sample_args)
    for row in read_library(test_client):
        assert row["status"] == expected
        assert row["runtime_starting"] is False


def test_disabled_selection_and_explicit_install_error_override_startup(startup_fixture, test_client, monkeypatch):
    sample, builtin = startup_fixture
    sample()
    (builtin / "compose.yaml").rename(builtin / "compose.yaml.disabled")
    for row in read_library(test_client):
        assert row["status"] == "disabled"
        assert row["runtime_starting"] is False
    (builtin / "compose.yaml.disabled").rename(builtin / "compose.yaml")
    monkeypatch.setattr(extensions, "_read_progress", lambda _: {"status": "error", "error": "Setup failed"})
    for row in read_library(test_client):
        assert row["status"] == "error"
        assert row["runtime_starting"] is False
