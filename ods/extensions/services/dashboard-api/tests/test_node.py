"""Tests for /api/node/capabilities — aggregated node diagnostics snapshot."""

from models import GPUInfo, ServiceStatus


def _gpu():
    return GPUInfo(name="RTX 4090", memory_used_mb=1000, memory_total_mb=24000,
                   memory_percent=4.2, utilization_percent=5, temperature_c=40,
                   gpu_backend="nvidia")


def _services():
    return [
        ServiceStatus(id="llama-server", name="Llama Server", port=8080,
                      external_port=8080, status="healthy", response_time_ms=12.0),
        ServiceStatus(id="open-webui", name="Open WebUI", port=3000,
                      external_port=3000, status="down", response_time_ms=None),
        ServiceStatus(id="qdrant", name="Qdrant", port=6333,
                      external_port=6333, status="unhealthy", response_time_ms=None),
    ]


class TestNodeCapabilitiesEndpoint:
    def test_full_payload(self, monkeypatch, tmp_path, test_client):
        import routers.node as node_mod

        async def _loaded():
            return "qwen2.5-7b-instruct"

        async def _svcs():
            return _services()

        monkeypatch.setattr(node_mod, "get_gpu_info", _gpu)
        monkeypatch.setattr(node_mod, "get_loaded_model", _loaded)
        monkeypatch.setattr(node_mod, "get_all_services", _svcs)
        (tmp_path / ".env").write_text("ODS_VERSION=9.9.9\n")
        monkeypatch.setattr(node_mod, "_install_root", lambda: tmp_path)

        r = test_client.get("/api/node/capabilities", headers=test_client.auth_headers)
        assert r.status_code == 200
        b = r.json()
        assert b["ods_version"] == "9.9.9"
        assert b["gpu"]["gpu_backend"] == "nvidia"
        assert b["gpu"]["memory_total_mb"] == 24000
        assert b["loaded_model"] == "qwen2.5-7b-instruct"
        assert b["service_count"] == 3
        assert b["running_service_count"] == 2  # healthy + unhealthy, not down
        assert len(b["services"]) == 3

    def test_gpu_and_model_absent(self, monkeypatch, tmp_path, test_client):
        import routers.node as node_mod

        async def _loaded():
            return None

        async def _svcs():
            return []

        monkeypatch.setattr(node_mod, "get_gpu_info", lambda: None)
        monkeypatch.setattr(node_mod, "get_loaded_model", _loaded)
        monkeypatch.setattr(node_mod, "get_all_services", _svcs)
        (tmp_path / ".env").write_text("ODS_VERSION=1.2.3\n")
        monkeypatch.setattr(node_mod, "_install_root", lambda: tmp_path)

        r = test_client.get("/api/node/capabilities", headers=test_client.auth_headers)
        assert r.status_code == 200
        b = r.json()
        assert b["gpu"] is None
        assert b["loaded_model"] is None
        assert b["service_count"] == 0
        assert b["running_service_count"] == 0

    def test_version_falls_back_to_app_version(self, monkeypatch, tmp_path, test_client):
        import routers.node as node_mod

        async def _loaded():
            return None

        async def _svcs():
            return []

        monkeypatch.setattr(node_mod, "get_gpu_info", lambda: None)
        monkeypatch.setattr(node_mod, "get_loaded_model", _loaded)
        monkeypatch.setattr(node_mod, "get_all_services", _svcs)
        monkeypatch.setattr(node_mod, "_install_root", lambda: tmp_path)  # empty dir

        r = test_client.get("/api/node/capabilities", headers=test_client.auth_headers)
        assert r.status_code == 200
        assert r.json()["ods_version"] == "2.5.3"  # app.version fallback

    def test_requires_auth(self, test_client):
        r = test_client.get("/api/node/capabilities")
        assert r.status_code in (401, 403)


class TestReadOdsVersion:
    def test_reads_env_ods_version(self, monkeypatch, tmp_path):
        import routers.node as node_mod
        (tmp_path / ".env").write_text("FOO=bar\nODS_VERSION=3.1.4\n")
        monkeypatch.setattr(node_mod, "_install_root", lambda: tmp_path)
        assert node_mod._read_ods_version("fallback") == "3.1.4"

    def test_reads_plain_version_file(self, monkeypatch, tmp_path):
        import routers.node as node_mod
        (tmp_path / ".version").write_text("7.7.7\n")
        monkeypatch.setattr(node_mod, "_install_root", lambda: tmp_path)
        assert node_mod._read_ods_version("fallback") == "7.7.7"

    def test_reads_json_version_file(self, monkeypatch, tmp_path):
        import routers.node as node_mod
        (tmp_path / ".version").write_text('{"version": "8.0.0"}')
        monkeypatch.setattr(node_mod, "_install_root", lambda: tmp_path)
        assert node_mod._read_ods_version("fallback") == "8.0.0"

    def test_env_takes_precedence_over_version_file(self, monkeypatch, tmp_path):
        import routers.node as node_mod
        (tmp_path / ".env").write_text("ODS_VERSION=env-wins\n")
        (tmp_path / ".version").write_text("file-loses")
        monkeypatch.setattr(node_mod, "_install_root", lambda: tmp_path)
        assert node_mod._read_ods_version("fallback") == "env-wins"

    def test_fallback_when_absent(self, monkeypatch, tmp_path):
        import routers.node as node_mod
        monkeypatch.setattr(node_mod, "_install_root", lambda: tmp_path)
        assert node_mod._read_ods_version("fb-1.0") == "fb-1.0"

    def test_reads_quoted_ods_version(self, monkeypatch, tmp_path):
        import routers.node as node_mod
        (tmp_path / ".env").write_text('ODS_VERSION="3.1.4"\n')
        monkeypatch.setattr(node_mod, "_install_root", lambda: tmp_path)
        assert node_mod._read_ods_version("fallback") == "3.1.4"
