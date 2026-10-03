"""Every measured ODS container must remain inspectable in the resource response."""

from pathlib import Path

import pytest
import yaml


@pytest.fixture
def librechat_resources(monkeypatch):
    from main import _cache
    from routers import resources

    library = Path(__file__).resolve().parents[3] / "library/services/librechat"
    manifest = yaml.safe_load((library / "manifest.yaml").read_text())
    compose = yaml.safe_load((library / "compose.yaml").read_text())
    # The installed extension has one catalog manifest but three containers.
    monkeypatch.setattr(resources, "SERVICES", {"librechat": manifest["service"]})
    samples = [{
        "service_id": service["container_name"].removeprefix("ods-"),
        "container_name": service["container_name"],
        "cpu_percent": 2.0 * index,
        "memory_used_mb": 128 * index,
        "memory_limit_mb": 1024,
        "pids": index,
    } for index, service in enumerate(compose["services"].values(), 1)]
    monkeypatch.setattr(resources, "_fetch_container_stats", lambda: samples)
    for key in ("service_resources_containers", "service_resources_disk"):
        _cache.invalidate(key)
    yield resources, samples
    for key in ("service_resources_containers", "service_resources_disk"):
        _cache.invalidate(key)


@pytest.mark.parametrize("with_disk", [False, True])
def test_shipped_auxiliary_containers_are_visible_once_without_restart_authority(
    test_client, monkeypatch, librechat_resources, with_disk,
):
    resources, samples = librechat_resources
    disk = {
        "librechat-mongodb": {"data_gb": 0.5, "path": "data/librechat-mongodb"},
        "retired-service": {"data_gb": 1.0, "path": "data/retired-service"},
    } if with_disk else {}
    monkeypatch.setattr(resources, "_scan_service_disk", lambda: disk)

    response = test_client.get("/api/services/resources", headers=test_client.auth_headers)
    assert response.status_code == 200
    payload = response.json()
    rows = {row["id"]: row for row in payload["services"]}
    expected = {sample["service_id"] for sample in samples} | set(disk)
    assert rows.keys() == expected
    assert len(rows) == len(payload["services"])
    assert rows["librechat"]["restartable"] is True
    for sample in samples:
        row = rows[sample["service_id"]]
        assert row["container"] == sample
        if row["id"] != "librechat":
            assert row["type"] == "docker"
            assert row["restartable"] is False
            assert "not declared" in row["restart_unavailable_reason"]
            refused = test_client.post(
                f"/api/services/{row['id']}/restart", headers=test_client.auth_headers,
            )
            assert refused.status_code == 404

    if with_disk:
        assert rows["librechat-mongodb"]["disk"] == disk["librechat-mongodb"]
        assert rows["retired-service"]["container"] is None
        assert rows["retired-service"]["type"] == "unknown"
    measured = [row["container"] for row in rows.values() if row["container"]]
    assert sum(row["memory_used_mb"] for row in measured) == payload["totals"]["memory_used_mb"]
    assert sum(row["cpu_percent"] for row in measured) == payload["totals"]["cpu_percent"]
