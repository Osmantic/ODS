"""Tests for routers/resources.py — per-service resource metrics."""

import json
import urllib.error
from unittest.mock import MagicMock, patch

from routers.resources import _scan_service_disk, _fetch_container_stats


# ---------------------------------------------------------------------------
# _scan_service_disk
# ---------------------------------------------------------------------------


class TestScanServiceDisk:

    def test_nonexistent_data_dir(self, monkeypatch):
        """Nonexistent DATA_DIR returns empty dict."""
        monkeypatch.setattr("routers.resources.DATA_DIR", "/nonexistent/path")
        result = _scan_service_disk()
        assert result == {}

    def test_empty_data_dir(self, tmp_path, monkeypatch):
        """Empty DATA_DIR returns empty dict."""
        monkeypatch.setattr("routers.resources.DATA_DIR", str(tmp_path))
        result = _scan_service_disk()
        assert result == {}

    def test_known_dir_mapped_to_service(self, tmp_path, monkeypatch):
        """Known dir name 'models' is mapped to 'llama-server'."""
        monkeypatch.setattr("routers.resources.DATA_DIR", str(tmp_path))
        (tmp_path / "models").mkdir()
        monkeypatch.setattr("routers.resources.dir_size_gb", lambda p: 5.2)

        result = _scan_service_disk()
        assert "llama-server" in result
        assert result["llama-server"]["data_gb"] == 5.2
        assert result["llama-server"]["path"] == "data/models"

    def test_unknown_dir_passes_through(self, tmp_path, monkeypatch):
        """Unknown dir name passes through as-is for service_id."""
        monkeypatch.setattr("routers.resources.DATA_DIR", str(tmp_path))
        (tmp_path / "custom-service").mkdir()
        monkeypatch.setattr("routers.resources.dir_size_gb", lambda p: 1.5)

        result = _scan_service_disk()
        assert "custom-service" in result
        assert result["custom-service"]["data_gb"] == 1.5
        assert result["custom-service"]["path"] == "data/custom-service"

    def test_zero_size_dir_excluded(self, tmp_path, monkeypatch):
        """Directories with size_gb == 0 are excluded from results."""
        monkeypatch.setattr("routers.resources.DATA_DIR", str(tmp_path))
        (tmp_path / "models").mkdir()
        monkeypatch.setattr("routers.resources.dir_size_gb", lambda p: 0)

        result = _scan_service_disk()
        assert result == {}

    def test_files_in_data_dir_skipped(self, tmp_path, monkeypatch):
        """Regular files in DATA_DIR are skipped, only directories scanned."""
        monkeypatch.setattr("routers.resources.DATA_DIR", str(tmp_path))
        (tmp_path / "somefile.txt").write_text("not a directory")

        result = _scan_service_disk()
        assert result == {}


# ---------------------------------------------------------------------------
# _fetch_container_stats
# ---------------------------------------------------------------------------


class TestFetchContainerStats:

    def test_valid_json_response(self):
        """Valid JSON with containers key returns the list."""
        containers = [{"container_name": "dream-llama", "cpu_percent": 45.0}]
        body = json.dumps({"containers": containers}).encode()

        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("routers.resources.urllib.request.urlopen", return_value=mock_resp):
            result = _fetch_container_stats()
        assert result == containers

    def test_host_agent_unreachable(self):
        """URLError (host agent unreachable) returns empty list."""
        with patch("routers.resources.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("Connection refused")):
            result = _fetch_container_stats()
        assert result == []

    def test_http_500_error(self):
        """HTTPError (server error) returns empty list."""
        with patch("routers.resources.urllib.request.urlopen",
                   side_effect=urllib.error.HTTPError(
                       url="http://test", code=500, msg="Internal Server Error",
                       hdrs=None, fp=None)):
            result = _fetch_container_stats()
        assert result == []

    def test_os_error(self):
        """OSError (network unreachable) returns empty list."""
        with patch("routers.resources.urllib.request.urlopen",
                   side_effect=OSError("Network is unreachable")):
            result = _fetch_container_stats()
        assert result == []


# ---------------------------------------------------------------------------
# service_resources endpoint
# ---------------------------------------------------------------------------


class TestServiceResources:

    @staticmethod
    def _clear_resource_cache():
        """Remove cached resource entries so each test gets fresh data."""
        from main import _cache
        _cache.invalidate("service_resources_containers")
        _cache.invalidate("service_resources_disk")

    def test_requires_auth(self, test_client):
        """GET /api/services/resources without auth header returns 401."""
        resp = test_client.get("/api/services/resources")
        assert resp.status_code == 401

    def test_full_response_with_stats_and_disk(self, test_client, monkeypatch):
        """Merges container stats and disk data into per-service entries."""
        self._clear_resource_cache()

        fake_services = {
            "llama-server": {"name": "Llama Server", "container_name": "dream-llama"},
            "open-webui": {"name": "Open WebUI", "container_name": "dream-webui"},
        }
        monkeypatch.setattr("routers.resources.SERVICES", fake_services)
        monkeypatch.setattr("routers.resources.GPU_BACKEND", "nvidia")

        fake_stats = [
            {"container_name": "dream-llama", "cpu_percent": 45.0, "memory_used_mb": 1024},
            {"container_name": "dream-webui", "cpu_percent": 10.0, "memory_used_mb": 256},
        ]
        fake_disk = {
            "llama-server": {"data_gb": 16.5, "path": "data/models"},
        }

        with patch("routers.resources._fetch_container_stats", return_value=fake_stats), \
             patch("routers.resources._scan_service_disk", return_value=fake_disk):
            resp = test_client.get(
                "/api/services/resources",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "services" in data
        assert "totals" in data
        assert "caveats" in data

        ids = [s["id"] for s in data["services"]]
        assert "llama-server" in ids
        assert "open-webui" in ids

        llama = next(s for s in data["services"] if s["id"] == "llama-server")
        assert llama["type"] == "docker"
        assert llama["restartable"] is True
        assert llama["restart_unavailable_reason"] is None
        assert llama["container"]["cpu_percent"] == 45.0
        assert llama["disk"]["data_gb"] == 16.5

        webui = next(s for s in data["services"] if s["id"] == "open-webui")
        assert webui["restartable"] is True
        assert webui["container"]["cpu_percent"] == 10.0
        assert webui["disk"] is None

        assert data["totals"]["cpu_percent"] == 55.0
        assert data["totals"]["memory_used_mb"] == 1280
        assert data["totals"]["disk_data_gb"] == 16.5
        assert data["caveats"]["docker_desktop_memory"] is False

    def test_host_agent_down_disk_only(self, test_client, monkeypatch):
        """When host agent returns no stats, response has disk data only."""
        self._clear_resource_cache()

        fake_services = {
            "llama-server": {"name": "Llama Server", "container_name": "dream-llama"},
        }
        monkeypatch.setattr("routers.resources.SERVICES", fake_services)
        monkeypatch.setattr("routers.resources.GPU_BACKEND", "nvidia")

        fake_disk = {"llama-server": {"data_gb": 8.0, "path": "data/models"}}

        with patch("routers.resources._fetch_container_stats", return_value=[]), \
             patch("routers.resources._scan_service_disk", return_value=fake_disk):
            resp = test_client.get(
                "/api/services/resources",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        llama = next(s for s in data["services"] if s["id"] == "llama-server")
        assert llama["container"] is None
        assert llama["disk"]["data_gb"] == 8.0
        assert data["totals"]["cpu_percent"] == 0
        assert data["totals"]["memory_used_mb"] == 0

    def test_apple_backend_sets_caveat(self, test_client, monkeypatch):
        """GPU_BACKEND=apple sets docker_desktop_memory caveat to True."""
        self._clear_resource_cache()

        monkeypatch.setattr("routers.resources.SERVICES", {})
        monkeypatch.setattr("routers.resources.GPU_BACKEND", "apple")

        with patch("routers.resources._fetch_container_stats", return_value=[]), \
             patch("routers.resources._scan_service_disk", return_value={}):
            resp = test_client.get(
                "/api/services/resources",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        assert resp.json()["caveats"]["docker_desktop_memory"] is True

    def test_orphaned_disk_data_included(self, test_client, monkeypatch):
        """Disk data for services not in SERVICES dict appears as orphaned entries."""
        self._clear_resource_cache()

        monkeypatch.setattr("routers.resources.SERVICES", {})
        monkeypatch.setattr("routers.resources.GPU_BACKEND", "nvidia")

        fake_disk = {"orphaned-svc": {"data_gb": 2.0, "path": "data/orphaned-svc"}}

        with patch("routers.resources._fetch_container_stats", return_value=[]), \
             patch("routers.resources._scan_service_disk", return_value=fake_disk):
            resp = test_client.get(
                "/api/services/resources",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        data = resp.json()
        orphaned = next(s for s in data["services"] if s["id"] == "orphaned-svc")
        assert orphaned["name"] == "orphaned-svc"
        assert orphaned["type"] == "unknown"
        assert orphaned["restartable"] is False
        assert "not declared" in orphaned["restart_unavailable_reason"]
        assert orphaned["container"] is None
        assert orphaned["disk"]["data_gb"] == 2.0

    def test_host_systemd_service_is_not_restartable(self, test_client, monkeypatch):
        """Host-level services are reported but excluded from Docker restart."""
        self._clear_resource_cache()

        monkeypatch.setattr("routers.resources.SERVICES", {
            "opencode": {
                "name": "OpenCode",
                "type": "host-systemd",
                "container_name": "",
            },
        })
        monkeypatch.setattr("routers.resources.GPU_BACKEND", "nvidia")

        with patch("routers.resources._fetch_container_stats", return_value=[]), \
             patch("routers.resources._scan_service_disk", return_value={}):
            resp = test_client.get(
                "/api/services/resources",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        service = resp.json()["services"][0]
        assert service["id"] == "opencode"
        assert service["type"] == "host-systemd"
        assert service["restartable"] is False
        assert "Host-level" in service["restart_unavailable_reason"]

    def test_restart_service_calls_host_agent(self, test_client, monkeypatch):
        """POST /api/services/{id}/restart validates the service and proxies to host agent."""
        monkeypatch.setattr("routers.resources.SERVICES", {
            "ape": {"name": "APE", "container_name": "dream-ape"},
        })

        with patch("routers.resources._post_agent_json", return_value={
            "status": "ok",
            "service_id": "ape",
            "action": "restart",
        }) as post_agent:
            resp = test_client.post(
                "/api/services/ape/restart",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        assert resp.json()["action"] == "restart"
        post_agent.assert_called_once_with(
            "/v1/service/restart",
            {"service_id": "ape"},
        )

    def test_restart_service_rejects_unknown_service(self, test_client, monkeypatch):
        """Restart endpoint only allows services known to DreamServer config."""
        monkeypatch.setattr("routers.resources.SERVICES", {})

        resp = test_client.post(
            "/api/services/not-installed/restart",
            headers=test_client.auth_headers,
        )

        assert resp.status_code == 404

    def test_restart_service_rejects_non_docker_service(self, test_client, monkeypatch):
        """Restart endpoint rejects known services that do not map to Docker."""
        monkeypatch.setattr("routers.resources.SERVICES", {
            "opencode": {
                "name": "OpenCode",
                "type": "host-systemd",
                "container_name": "",
            },
        })

        with patch("routers.resources._post_agent_json") as post_agent:
            resp = test_client.post(
                "/api/services/opencode/restart",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 400
        assert "Host-level" in resp.json()["detail"]
        post_agent.assert_not_called()

    def test_restart_dashboard_uses_delayed_host_agent_restart(self, test_client, monkeypatch):
        """Restarting dashboard/API is delayed so the initiating request can return."""
        monkeypatch.setattr("routers.resources.SERVICES", {
            "dashboard-api": {"name": "Dashboard API", "container_name": "dream-dashboard-api"},
        })

        with patch("routers.resources._post_agent_json", return_value={
            "status": "accepted",
            "service_id": "dashboard-api",
            "action": "restart",
        }) as post_agent:
            resp = test_client.post(
                "/api/services/dashboard-api/restart",
                headers=test_client.auth_headers,
            )

        assert resp.status_code == 200
        post_agent.assert_called_once_with(
            "/v1/service/restart",
            {"service_id": "dashboard-api", "delay_seconds": 1},
        )
