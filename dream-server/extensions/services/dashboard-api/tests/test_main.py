"""Tests for main.py — core endpoints and helper functions."""

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from main import (
    TTLCache,
    _build_api_status,
    _build_model_readiness_payload,
    _build_readiness_payload,
    _fallback_services,
    _read_installed_version,
    _serialize_services,
    get_allowed_origins,
)


# --- get_allowed_origins ---


def test_read_installed_version_parses_json_version_file(tmp_path, monkeypatch):
    version_file = tmp_path / ".version"
    version_file.write_text(json.dumps({"version": "3.1.4"}), encoding="utf-8")
    monkeypatch.setattr("main._resolve_install_root", lambda: tmp_path)

    assert _read_installed_version() == "3.1.4"


class TestGetAllowedOrigins:

    def test_returns_env_origins_when_set(self, monkeypatch):
        monkeypatch.setenv("DASHBOARD_ALLOWED_ORIGINS", "http://foo:3000,http://bar:3001")
        origins = get_allowed_origins()
        assert origins == ["http://foo:3000", "http://bar:3001"]

    def test_returns_defaults_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv("DASHBOARD_ALLOWED_ORIGINS", raising=False)
        origins = get_allowed_origins()
        assert "http://localhost:3001" in origins
        assert "http://127.0.0.1:3001" in origins

    def test_includes_lan_ips(self, monkeypatch):
        monkeypatch.delenv("DASHBOARD_ALLOWED_ORIGINS", raising=False)
        monkeypatch.setattr("main.socket.gethostname", lambda: "test-host")
        monkeypatch.setattr(
            "main.socket.gethostbyname_ex",
            lambda h: ("test-host", [], ["192.168.1.100"]),
        )
        origins = get_allowed_origins()
        assert "http://192.168.1.100:3001" in origins
        assert "http://192.168.1.100:3000" in origins

    def test_handles_socket_error(self, monkeypatch):
        import socket
        monkeypatch.delenv("DASHBOARD_ALLOWED_ORIGINS", raising=False)
        monkeypatch.setattr("main.socket.gethostname", lambda: "test-host")
        monkeypatch.setattr(
            "main.socket.gethostbyname_ex",
            MagicMock(side_effect=socket.gaierror("lookup failed")),
        )
        # Should not raise; just returns defaults without LAN IPs
        origins = get_allowed_origins()
        assert "http://localhost:3001" in origins


# --- /api/preflight/docker ---


class TestPreflightDocker:

    def test_docker_available(self, test_client, monkeypatch):
        import asyncio
        import os.path as _ospath
        monkeypatch.setattr(_ospath, "exists", lambda p: False)

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Docker version 24.0.7, build afdd53b", b""))

        monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=mock_proc))

        resp = test_client.get("/api/preflight/docker", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True
        assert "24.0.7" in data["version"]

    def test_docker_not_installed(self, test_client, monkeypatch):
        import asyncio
        import os.path as _ospath
        monkeypatch.setattr(_ospath, "exists", lambda p: False)
        monkeypatch.setattr(
            asyncio, "create_subprocess_exec",
            AsyncMock(side_effect=FileNotFoundError("docker not found")),
        )

        resp = test_client.get("/api/preflight/docker", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False
        assert "not installed" in data["error"]

    def test_docker_timeout(self, test_client, monkeypatch):
        import asyncio
        import os.path as _ospath
        monkeypatch.setattr(_ospath, "exists", lambda p: False)

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())

        monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=mock_proc))
        monkeypatch.setattr(asyncio, "wait_for", AsyncMock(side_effect=asyncio.TimeoutError()))

        resp = test_client.get("/api/preflight/docker", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False
        assert "timed out" in data["error"]


# --- /api/preflight/gpu ---


class TestPreflightGpu:

    def test_gpu_available(self, test_client, monkeypatch):
        from models import GPUInfo
        gpu = GPUInfo(
            name="RTX 4090", memory_used_mb=2048, memory_total_mb=24576,
            memory_percent=8.3, utilization_percent=35, temperature_c=62,
            gpu_backend="nvidia",
        )
        monkeypatch.setattr("main.get_gpu_info", lambda: gpu)

        resp = test_client.get("/api/preflight/gpu", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True
        assert data["name"] == "RTX 4090"
        assert data["backend"] == "nvidia"

    def test_gpu_unavailable_amd(self, test_client, monkeypatch):
        monkeypatch.setattr("main.get_gpu_info", lambda: None)
        monkeypatch.setenv("GPU_BACKEND", "amd")

        resp = test_client.get("/api/preflight/gpu", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False
        assert "AMD" in data["error"]

    def test_unified_memory_label(self, test_client, monkeypatch):
        from models import GPUInfo
        gpu = GPUInfo(
            name="AMD Strix Halo", memory_used_mb=10240, memory_total_mb=98304,
            memory_percent=10.4, utilization_percent=15, temperature_c=55,
            memory_type="unified", gpu_backend="amd",
        )
        monkeypatch.setattr("main.get_gpu_info", lambda: gpu)

        resp = test_client.get("/api/preflight/gpu", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True
        assert data["memory_type"] == "unified"
        assert "Unified" in data["memory_label"]


# --- /api/preflight/disk ---


class TestPreflightDisk:

    def test_returns_disk_info(self, test_client, monkeypatch):
        from collections import namedtuple
        DiskUsageTuple = namedtuple('usage', ['total', 'used', 'free'])
        monkeypatch.setattr("main.os.path.exists", lambda p: True)
        monkeypatch.setattr("main.shutil.disk_usage", lambda p: DiskUsageTuple(500 * 1024**3, 200 * 1024**3, 300 * 1024**3))

        resp = test_client.get("/api/preflight/disk", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 500 * 1024**3
        assert data["used"] == 200 * 1024**3
        assert data["free"] == 300 * 1024**3

    def test_handles_exception(self, test_client, monkeypatch):
        monkeypatch.setattr("main.os.path.exists", lambda p: True)
        monkeypatch.setattr("main.shutil.disk_usage", MagicMock(side_effect=OSError("disk error")))

        resp = test_client.get("/api/preflight/disk", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data


# --- _build_api_status ---


class TestBuildApiStatus:

    @pytest.mark.asyncio
    async def test_returns_full_structure(self, monkeypatch):
        from models import GPUInfo, BootstrapStatus, ModelInfo

        gpu = GPUInfo(
            name="RTX 4090", memory_used_mb=2048, memory_total_mb=24576,
            memory_percent=8.3, utilization_percent=35, temperature_c=62,
            gpu_backend="nvidia",
        )
        monkeypatch.setattr("main.get_gpu_info", lambda: gpu)
        monkeypatch.setattr("main.get_all_services", AsyncMock(return_value=[]))
        monkeypatch.setattr("main.get_model_info", lambda: ModelInfo(name="Test-32B", size_gb=16.0, context_length=32768))
        monkeypatch.setattr("main.get_bootstrap_status", lambda: BootstrapStatus(active=False))
        monkeypatch.setattr("main.get_loaded_model", AsyncMock(return_value="Test-32B"))
        monkeypatch.setattr("main.get_llama_metrics", AsyncMock(return_value={"tokens_per_second": 25.5, "lifetime_tokens": 10000}))
        monkeypatch.setattr("main.get_llama_context_size", AsyncMock(return_value=32768))
        monkeypatch.setattr("main.get_uptime", lambda: 3600)
        monkeypatch.setattr("main.get_cpu_metrics", lambda: {"percent": 15.0, "temp_c": 55})
        monkeypatch.setattr("main.get_ram_metrics", lambda: {"used_gb": 16.0, "total_gb": 64.0, "percent": 25.0})

        result = await _build_api_status()
        assert result["gpu"] is not None
        assert result["gpu"]["name"] == "RTX 4090"
        assert result["tier"] == "Prosumer"
        assert result["uptime"] == 3600
        assert result["inference"]["tokensPerSecond"] == 25.5
        assert result["inference"]["loadedModel"] == "Test-32B"

    @pytest.mark.asyncio
    async def test_tier_professional(self, monkeypatch):
        from models import GPUInfo, BootstrapStatus

        gpu = GPUInfo(
            name="H100", memory_used_mb=4096, memory_total_mb=81920,
            memory_percent=5.0, utilization_percent=10, temperature_c=45,
            gpu_backend="nvidia",
        )
        monkeypatch.setattr("main.get_gpu_info", lambda: gpu)
        monkeypatch.setattr("main.get_all_services", AsyncMock(return_value=[]))
        monkeypatch.setattr("main.get_model_info", lambda: None)
        monkeypatch.setattr("main.get_bootstrap_status", lambda: BootstrapStatus(active=False))
        monkeypatch.setattr("main.get_loaded_model", AsyncMock(return_value=None))
        monkeypatch.setattr("main.get_llama_metrics", AsyncMock(return_value={"tokens_per_second": 0, "lifetime_tokens": 0}))
        monkeypatch.setattr("main.get_llama_context_size", AsyncMock(return_value=None))
        monkeypatch.setattr("main.get_uptime", lambda: 0)
        monkeypatch.setattr("main.get_cpu_metrics", lambda: {"percent": 0, "temp_c": None})
        monkeypatch.setattr("main.get_ram_metrics", lambda: {"used_gb": 0, "total_gb": 0, "percent": 0})

        result = await _build_api_status()
        assert result["tier"] == "Professional"

    @pytest.mark.asyncio
    async def test_tier_strix_halo(self, monkeypatch):
        from models import GPUInfo, BootstrapStatus

        gpu = GPUInfo(
            name="Strix Halo", memory_used_mb=10240, memory_total_mb=98304,
            memory_percent=10.4, utilization_percent=15, temperature_c=55,
            memory_type="unified", gpu_backend="amd",
        )
        monkeypatch.setattr("main.get_gpu_info", lambda: gpu)
        monkeypatch.setattr("main.get_all_services", AsyncMock(return_value=[]))
        monkeypatch.setattr("main.get_model_info", lambda: None)
        monkeypatch.setattr("main.get_bootstrap_status", lambda: BootstrapStatus(active=False))
        monkeypatch.setattr("main.get_loaded_model", AsyncMock(return_value=None))
        monkeypatch.setattr("main.get_llama_metrics", AsyncMock(return_value={"tokens_per_second": 0, "lifetime_tokens": 0}))
        monkeypatch.setattr("main.get_llama_context_size", AsyncMock(return_value=None))
        monkeypatch.setattr("main.get_uptime", lambda: 0)
        monkeypatch.setattr("main.get_cpu_metrics", lambda: {"percent": 0, "temp_c": None})
        monkeypatch.setattr("main.get_ram_metrics", lambda: {"used_gb": 0, "total_gb": 0, "percent": 0})

        result = await _build_api_status()
        assert result["tier"] == "Strix Halo 90+"


# --- Readiness ---


class TestReadinessPayload:

    def test_core_ready_with_disabled_voice(self):
        from models import BootstrapStatus, ServiceStatus

        statuses = [
            ServiceStatus(id="llama-server", name="LLM", port=8080, external_port=8080, status="healthy"),
            ServiceStatus(id="open-webui", name="Open WebUI", port=3000, external_port=3000, status="healthy"),
        ]

        result = _build_readiness_payload(
            service_statuses=statuses,
            loaded_model="Test-32B",
            context_size=32768,
            bootstrap_info=BootstrapStatus(active=False),
            host_agent={"available": True},
            stt_model_cached=None,
            stt_model_name="Systran/faster-whisper-base",
        )

        assert result["ready"] is True
        assert result["status"] == "ready"
        assert result["canChat"] is True
        assert result["canUseVoice"] is False
        assert any(check["id"] == "voice" and check["status"] == "disabled" for check in result["checks"])

    def test_missing_model_blocks_chat(self):
        from models import BootstrapStatus, ServiceStatus

        statuses = [
            ServiceStatus(id="llama-server", name="LLM", port=8080, external_port=8080, status="healthy"),
            ServiceStatus(id="open-webui", name="Open WebUI", port=3000, external_port=3000, status="healthy"),
        ]

        result = _build_readiness_payload(
            service_statuses=statuses,
            loaded_model=None,
            context_size=32768,
            bootstrap_info=BootstrapStatus(active=False),
            host_agent={"available": True},
            stt_model_cached=None,
            stt_model_name="Systran/faster-whisper-base",
        )

        assert result["ready"] is False
        assert result["status"] == "blocked"
        assert result["canChat"] is False
        assert "dream restart llama-server" in result["repairHints"]

    def test_voice_ready_requires_services_and_cached_stt_model(self):
        from models import BootstrapStatus, ServiceStatus

        statuses = [
            ServiceStatus(id="llama-server", name="LLM", port=8080, external_port=8080, status="healthy"),
            ServiceStatus(id="open-webui", name="Open WebUI", port=3000, external_port=3000, status="healthy"),
            ServiceStatus(id="whisper", name="Whisper", port=8000, external_port=9000, status="healthy"),
            ServiceStatus(id="tts", name="TTS", port=8880, external_port=8880, status="healthy"),
        ]

        result = _build_readiness_payload(
            service_statuses=statuses,
            loaded_model="Test-32B",
            context_size=32768,
            bootstrap_info=BootstrapStatus(active=False),
            host_agent={"available": True},
            stt_model_cached=True,
            stt_model_name="deepdml/faster-whisper-large-v3-turbo-ct2",
        )

        assert result["canUseVoice"] is True
        assert any(check["id"] == "voice" and check["ready"] is True for check in result["checks"])

    def test_uncached_stt_model_degrades_optional_voice(self):
        from models import BootstrapStatus, ServiceStatus

        statuses = [
            ServiceStatus(id="llama-server", name="LLM", port=8080, external_port=8080, status="healthy"),
            ServiceStatus(id="open-webui", name="Open WebUI", port=3000, external_port=3000, status="healthy"),
            ServiceStatus(id="whisper", name="Whisper", port=8000, external_port=9000, status="healthy"),
            ServiceStatus(id="tts", name="TTS", port=8880, external_port=8880, status="healthy"),
        ]

        result = _build_readiness_payload(
            service_statuses=statuses,
            loaded_model="Test-32B",
            context_size=32768,
            bootstrap_info=BootstrapStatus(active=False),
            host_agent={"available": True},
            stt_model_cached=False,
            stt_model_name="deepdml/faster-whisper-large-v3-turbo-ct2",
        )

        assert result["ready"] is True
        assert result["status"] == "degraded"
        assert result["canUseVoice"] is False
        assert "dream repair voice" in result["repairHints"]

    def test_api_readiness_endpoint(self, test_client, monkeypatch):
        from models import BootstrapStatus, ServiceStatus

        statuses = [
            ServiceStatus(id="llama-server", name="LLM", port=8080, external_port=8080, status="healthy"),
            ServiceStatus(id="open-webui", name="Open WebUI", port=3000, external_port=3000, status="healthy"),
        ]
        monkeypatch.setattr("main._get_services", AsyncMock(return_value=statuses))
        monkeypatch.setattr("main.get_loaded_model", AsyncMock(return_value="Test-32B"))
        monkeypatch.setattr("main.get_llama_context_size", AsyncMock(return_value=32768))
        monkeypatch.setattr("main.get_bootstrap_status", lambda: BootstrapStatus(active=False))
        monkeypatch.setattr("main._probe_host_agent_health", lambda: {"available": True})
        monkeypatch.setattr("main._check_stt_model_cached", AsyncMock(return_value=(None, "Systran/faster-whisper-base")))

        resp = test_client.get("/api/readiness", headers=test_client.auth_headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is True
        assert data["canChat"] is True
        assert "checks" in data


# --- /api/service-tokens ---


class TestServiceTokens:

    def test_returns_token_from_env(self, test_client, monkeypatch):
        monkeypatch.setenv("OPENCLAW_TOKEN", "my-secret-token")

        resp = test_client.get("/api/service-tokens", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("openclaw") == "my-secret-token"

    def test_returns_empty_when_no_token(self, test_client, monkeypatch):
        monkeypatch.delenv("OPENCLAW_TOKEN", raising=False)
        # The file-based fallback paths (/data/openclaw/..., /dream-server/.env)
        # won't exist in test environment, so all fallbacks fail gracefully.

        resp = test_client.get("/api/service-tokens", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        # Either empty dict or no openclaw key
        assert "openclaw" not in data


# --- /api/external-links ---


class TestExternalLinks:

    def test_returns_links_for_services(self, test_client, monkeypatch):
        import config
        monkeypatch.setattr(config, "SERVICES", {
            "open-webui": {"name": "Open WebUI", "port": 3000, "external_port": 3000, "health": "/health", "host": "localhost"},
            "n8n": {"name": "n8n", "port": 5678, "external_port": 5678, "health": "/healthz", "host": "localhost"},
            "dashboard-api": {"name": "Dashboard API", "port": 3002, "external_port": 3002, "health": "/health", "host": "localhost"},
        })
        # Also patch the SERVICES imported in main module
        monkeypatch.setattr("main.SERVICES", config.SERVICES)

        resp = test_client.get("/api/external-links", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        link_ids = [link["id"] for link in data]
        assert "open-webui" in link_ids
        assert "n8n" in link_ids

    def test_excludes_dashboard_api(self, test_client, monkeypatch):
        import config
        monkeypatch.setattr(config, "SERVICES", {
            "dashboard-api": {"name": "Dashboard API", "port": 3002, "external_port": 3002, "health": "/health", "host": "localhost"},
        })
        monkeypatch.setattr("main.SERVICES", config.SERVICES)

        resp = test_client.get("/api/external-links", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 0


# --- /api/storage ---


class TestApiStorage:

    def test_returns_storage_breakdown(self, test_client, monkeypatch):
        from models import DiskUsage
        monkeypatch.setattr("main.get_disk_usage", lambda: DiskUsage(
            path="/tmp", used_gb=100.0, total_gb=500.0, percent=20.0,
        ))
        monkeypatch.setattr("main.DATA_DIR", "/tmp/dream-test-nonexistent-data")

        resp = test_client.get("/api/storage", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert "vector_db" in data
        assert "total_data" in data
        assert "disk" in data
        assert data["disk"]["total_gb"] == 500.0


# --- TTLCache ---


class TestTTLCache:

    def test_set_and_get(self):
        cache = TTLCache()
        cache.set("k", "v", ttl=10)
        assert cache.get("k") == "v"

    def test_expired_key_returns_none(self):
        cache = TTLCache()
        cache.set("k", "v", ttl=0.01)
        time.sleep(0.02)
        assert cache.get("k") is None

    def test_missing_key_returns_none(self):
        cache = TTLCache()
        assert cache.get("nope") is None


# --- /api/preflight/docker edge cases ---


class TestPreflightDockerEdge:

    def test_docker_inside_container(self, test_client, monkeypatch):
        import os.path as _ospath
        monkeypatch.setattr(_ospath, "exists", lambda p: p == "/.dockerenv")

        resp = test_client.get("/api/preflight/docker", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is True
        assert "host" in data["version"]

    def test_docker_command_failed(self, test_client, monkeypatch):
        import asyncio
        import os.path as _ospath
        monkeypatch.setattr(_ospath, "exists", lambda p: False)

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))
        monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(return_value=mock_proc))

        resp = test_client.get("/api/preflight/docker", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False
        assert "failed" in data["error"]

    def test_docker_os_error(self, test_client, monkeypatch):
        import asyncio
        import os.path as _ospath
        monkeypatch.setattr(_ospath, "exists", lambda p: False)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("broken")))

        resp = test_client.get("/api/preflight/docker", headers=test_client.auth_headers)
        assert resp.status_code == 200
        assert resp.json()["available"] is False


# --- /api/preflight/gpu no-gpu fallback ---


class TestPreflightGpuNoGpu:

    def test_gpu_unavailable_generic(self, test_client, monkeypatch):
        monkeypatch.setattr("main.get_gpu_info", lambda: None)
        monkeypatch.setenv("GPU_BACKEND", "nvidia")

        resp = test_client.get("/api/preflight/gpu", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["available"] is False
        assert "No GPU detected" in data["error"]


# --- /api/preflight/required-ports ---


class TestPreflightRequiredPorts:

    def test_returns_ports(self, test_client, monkeypatch):
        monkeypatch.setattr("main.SERVICES", {
            "svc-a": {"name": "A", "port": 8000, "external_port": 8000},
        })
        resp = test_client.get("/api/preflight/required-ports")
        assert resp.status_code == 200
        data = resp.json()
        assert any(p["port"] == 8000 for p in data["ports"])


# --- /api/preflight/ports ---


class TestPreflightPorts:

    def test_port_in_use(self, test_client, monkeypatch):
        import socket as _socket
        monkeypatch.setattr("main.SERVICES", {"svc": {"name": "S", "port": 8123, "external_port": 8123}})

        # Bind a port to make it "in use"
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", 18765))
        try:
            resp = test_client.post(
                "/api/preflight/ports",
                json={"ports": [18765]},
                headers=test_client.auth_headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["available"] is False
            assert len(data["conflicts"]) == 1
        finally:
            sock.close()

    @pytest.mark.parametrize("port", [0, -1, 65536, "not-a-port"])
    def test_rejects_invalid_port_values(self, test_client, port):
        resp = test_client.post(
            "/api/preflight/ports",
            json={"ports": [port]},
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 422

    def test_accepts_valid_port_range_edges(self, test_client):
        resp = test_client.post(
            "/api/preflight/ports",
            json={"ports": [1, 65535]},
            headers=test_client.auth_headers,
        )
        assert resp.status_code == 200


# --- /gpu endpoint (cached paths) ---


class TestGpuEndpoint:

    def test_gpu_cached_hit(self, test_client, monkeypatch):
        from models import GPUInfo
        import main
        gpu = GPUInfo(
            name="RTX 4090", memory_used_mb=2048, memory_total_mb=24576,
            memory_percent=8.3, utilization_percent=35, temperature_c=62,
            gpu_backend="nvidia",
        )
        main._cache.set("gpu_info", gpu, 60)
        resp = test_client.get("/gpu", headers=test_client.auth_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "RTX 4090"

    def test_gpu_cached_falsy(self, test_client, monkeypatch):
        import main
        main._cache.set("gpu_info", None, 60)
        resp = test_client.get("/gpu", headers=test_client.auth_headers)
        assert resp.status_code == 503

    def test_gpu_no_cache_no_gpu(self, test_client, monkeypatch):
        import main
        main._cache._store.pop("gpu_info", None)
        monkeypatch.setattr("main.get_gpu_info", lambda: None)
        resp = test_client.get("/gpu", headers=test_client.auth_headers)
        assert resp.status_code == 503


# --- /services, /disk, /model, /bootstrap endpoints ---


class TestCoreEndpoints:

    def test_services_returns_list(self, test_client, monkeypatch):
        from models import ServiceStatus
        statuses = [ServiceStatus(id="s", name="S", port=80, external_port=80, status="healthy")]
        monkeypatch.setattr("main.get_cached_services", lambda: statuses)
        resp = test_client.get("/services", headers=test_client.auth_headers)
        assert resp.status_code == 200

    def test_services_fallback_live(self, test_client, monkeypatch):
        monkeypatch.setattr("main.get_cached_services", lambda: None)
        monkeypatch.setattr("main.get_all_services", AsyncMock(return_value=[]))
        resp = test_client.get("/services", headers=test_client.auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_disk_endpoint(self, test_client, monkeypatch):
        from models import DiskUsage
        monkeypatch.setattr("main.get_disk_usage", lambda: DiskUsage(path="/", used_gb=50, total_gb=500, percent=10))
        resp = test_client.get("/disk", headers=test_client.auth_headers)
        assert resp.status_code == 200
        assert resp.json()["total_gb"] == 500.0

    def test_model_endpoint(self, test_client, monkeypatch):
        from models import ModelInfo
        monkeypatch.setattr("main.get_model_info", lambda: ModelInfo(name="T", size_gb=1.0, context_length=4096))
        resp = test_client.get("/model", headers=test_client.auth_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "T"

    def test_bootstrap_endpoint(self, test_client, monkeypatch):
        from models import BootstrapStatus
        monkeypatch.setattr("main.get_bootstrap_status", lambda: BootstrapStatus(active=False))
        resp = test_client.get("/bootstrap", headers=test_client.auth_headers)
        assert resp.status_code == 200
        assert resp.json()["active"] is False


# --- model readiness ---


class TestModelReadiness:

    def test_ready_when_loaded_context_meets_hermes_minimum(self):
        from models import BootstrapStatus, ModelInfo

        result = _build_model_readiness_payload(
            model_info=ModelInfo(name="qwen", size_gb=6.0, context_length=131072, quantization="GGUF"),
            bootstrap_info=BootstrapStatus(active=False),
            loaded_model="qwen",
            runtime_context=131072,
        )

        assert result["ready"] is True
        assert result["status"] == "ready"
        assert result["context"]["meetsHermesMinimum"] is True
        assert result["context"]["meetsHermesTarget"] is True
        assert result["hermes"]["compatible"] is True

    def test_bootstrap_can_be_hermes_compatible_before_full_model_ready(self):
        from models import BootstrapStatus, ModelInfo

        result = _build_model_readiness_payload(
            model_info=ModelInfo(name="qwen3.5-2b", size_gb=1.5, context_length=65536, quantization="GGUF"),
            bootstrap_info=BootstrapStatus(active=True, model_name="full-model.gguf", percent=42.0),
            loaded_model="qwen3.5-2b",
            runtime_context=65536,
        )

        assert result["ready"] is True
        assert result["status"] == "bootstrap"
        assert result["context"]["meetsHermesMinimum"] is True
        assert result["context"]["meetsHermesTarget"] is False
        assert any("Full model is still downloading" in issue for issue in result["issues"])

    def test_context_below_hermes_minimum_blocks_readiness(self):
        from models import BootstrapStatus, ModelInfo

        result = _build_model_readiness_payload(
            model_info=ModelInfo(name="small", size_gb=1.0, context_length=8192),
            bootstrap_info=BootstrapStatus(active=False),
            loaded_model="small",
            runtime_context=8192,
        )

        assert result["ready"] is False
        assert result["status"] == "blocked"
        assert result["hermes"]["compatible"] is False
        assert any("Context is below Hermes minimum" in issue for issue in result["issues"])

    def test_model_readiness_endpoint(self, test_client, monkeypatch):
        from models import BootstrapStatus, ModelInfo

        monkeypatch.setattr("main.get_model_info", lambda: ModelInfo(name="qwen", size_gb=6.0, context_length=131072))
        monkeypatch.setattr("main.get_bootstrap_status", lambda: BootstrapStatus(active=False))
        monkeypatch.setattr("main.get_loaded_model", AsyncMock(return_value="qwen"))
        monkeypatch.setattr("main.get_llama_context_size", AsyncMock(return_value=131072))

        resp = test_client.get("/api/model-readiness", headers=test_client.auth_headers)

        assert resp.status_code == 200
        data = resp.json()
        assert data["ready"] is True
        assert data["activeModel"] == "qwen"
        assert data["hermes"]["targetReady"] is True


# --- /status endpoint (FullStatus) ---


class TestStatusEndpoint:

    def test_returns_full_status(self, test_client, monkeypatch):
        from models import GPUInfo, DiskUsage, ModelInfo, BootstrapStatus
        monkeypatch.setattr("main.get_gpu_info", lambda: GPUInfo(
            name="RTX 4090", memory_used_mb=2048, memory_total_mb=24576,
            memory_percent=8.3, utilization_percent=35, temperature_c=62,
            gpu_backend="nvidia",
        ))
        monkeypatch.setattr("main.get_disk_usage", lambda: DiskUsage(path="/", used_gb=50, total_gb=500, percent=10))
        monkeypatch.setattr("main.get_model_info", lambda: ModelInfo(name="T", size_gb=1.0, context_length=4096))
        monkeypatch.setattr("main.get_bootstrap_status", lambda: BootstrapStatus(active=False))
        monkeypatch.setattr("main.get_uptime", lambda: 3600)
        monkeypatch.setattr("main.get_cached_services", lambda: [])
        monkeypatch.setattr("main.get_all_services", AsyncMock(return_value=[]))

        resp = test_client.get("/status", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["gpu"]["name"] == "RTX 4090"
        assert data["uptime_seconds"] == 3600


# --- /api/status service serialization ---


class TestApiStatusServiceSerialization:

    def test_serialize_services_includes_service_id_and_semantics(self, monkeypatch):
        from models import ServiceStatus
        monkeypatch.setattr("main.SERVICES", {
            "llama-server": {"category": "core"},
        })
        services = [
            ServiceStatus(
                id="llama-server",
                name="llama-server (LLM Inference)",
                port=8080,
                external_port=11434,
                status="healthy",
            )
        ]

        serialized = _serialize_services(services, uptime=42)

        assert serialized == [{
            "id": "llama-server",
            "name": "llama-server (LLM Inference)",
            "status": "healthy",
            "port": 11434,
            "uptime": 42,
            "category": "core",
            "required": True,
            "impact": "core",
            "state": "ready",
            "severity": "ok",
            "countsAsIssue": False,
        }]

    def test_serialize_optional_not_deployed_is_disabled(self, monkeypatch):
        from models import ServiceStatus
        monkeypatch.setattr("main.SERVICES", {
            "tailscale": {"category": "optional"},
        })
        services = [
            ServiceStatus(
                id="tailscale",
                name="Tailscale",
                port=0,
                external_port=0,
                status="not_deployed",
            )
        ]

        serialized = _serialize_services(services, uptime=42)

        assert serialized[0]["required"] is False
        assert serialized[0]["impact"] == "optional"
        assert serialized[0]["state"] == "disabled"
        assert serialized[0]["severity"] == "disabled"
        assert serialized[0]["countsAsIssue"] is False

    def test_optional_unknown_does_not_count_as_issue(self, monkeypatch):
        from models import ServiceStatus
        monkeypatch.setattr("main.SERVICES", {
            "optional-tool": {"category": "optional"},
        })
        services = [
            ServiceStatus(
                id="optional-tool",
                name="Optional Tool",
                port=8080,
                external_port=8080,
                status="unknown",
            )
        ]

        serialized = _serialize_services(services, uptime=42)

        assert serialized[0]["state"] == "unknown"
        assert serialized[0]["severity"] == "unknown"
        assert serialized[0]["countsAsIssue"] is False

    def test_fallback_services_include_service_ids_and_semantics(self, monkeypatch):
        monkeypatch.setattr("main.SERVICES", {
            "dashboard-api": {
                "name": "Dashboard API (System Status)",
                "port": 3002,
                "external_port": 3002,
                "category": "core",
            }
        })

        serialized = _fallback_services()

        assert serialized == [{
            "id": "dashboard-api",
            "name": "Dashboard API (System Status)",
            "status": "unknown",
            "port": 3002,
            "uptime": None,
            "category": "core",
            "required": True,
            "impact": "core",
            "state": "blocked",
            "severity": "critical",
            "countsAsIssue": True,
        }]


# --- /api/status fallback on exception ---


class TestApiStatusFallback:

    def test_fallback_on_oserror(self, test_client, monkeypatch):
        """Narrow exception class (OSError) falls through to the safe-fallback dict."""
        monkeypatch.setattr("main._build_api_status", AsyncMock(side_effect=OSError("network down")))

        resp = test_client.get("/api/status", headers=test_client.auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["gpu"] is None
        assert data["tier"] == "Unknown"
        assert data["services"] == []

    def test_runtime_error_propagates_as_500(self, test_client, monkeypatch):
        """Programming errors (RuntimeError) inside _build_api_status must
        propagate so they surface in tests / monitoring rather than being
        silently masked as 200-with-zeros (CLAUDE.md "Let It Crash")."""
        from fastapi.testclient import TestClient
        from main import app

        monkeypatch.setattr(
            "main._build_api_status",
            AsyncMock(side_effect=RuntimeError("boom")),
        )
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/status", headers=test_client.auth_headers)
        assert resp.status_code == 500


# --- _build_api_status tier branches ---


class TestBuildApiStatusTiers:

    @pytest.mark.asyncio
    async def test_tier_entry(self, monkeypatch):
        from models import GPUInfo, BootstrapStatus
        gpu = GPUInfo(
            name="RTX 3060", memory_used_mb=1024, memory_total_mb=12288,
            memory_percent=8.3, utilization_percent=10, temperature_c=55,
            gpu_backend="nvidia",
        )
        monkeypatch.setattr("main.get_gpu_info", lambda: gpu)
        monkeypatch.setattr("main.get_all_services", AsyncMock(return_value=[]))
        monkeypatch.setattr("main.get_model_info", lambda: None)
        monkeypatch.setattr("main.get_bootstrap_status", lambda: BootstrapStatus(active=False))
        monkeypatch.setattr("main.get_loaded_model", AsyncMock(return_value=None))
        monkeypatch.setattr("main.get_llama_metrics", AsyncMock(return_value={"tokens_per_second": 0, "lifetime_tokens": 0}))
        monkeypatch.setattr("main.get_llama_context_size", AsyncMock(return_value=None))
        monkeypatch.setattr("main.get_uptime", lambda: 0)
        monkeypatch.setattr("main.get_cpu_metrics", lambda: {"percent": 0, "temp_c": None})
        monkeypatch.setattr("main.get_ram_metrics", lambda: {"used_gb": 0, "total_gb": 0, "percent": 0})

        result = await _build_api_status()
        assert result["tier"] == "Entry"

    @pytest.mark.asyncio
    async def test_tier_standard(self, monkeypatch):
        from models import GPUInfo, BootstrapStatus
        gpu = GPUInfo(
            name="RTX 4080", memory_used_mb=2048, memory_total_mb=16384,
            memory_percent=12.5, utilization_percent=20, temperature_c=55,
            gpu_backend="nvidia",
        )
        monkeypatch.setattr("main.get_gpu_info", lambda: gpu)
        monkeypatch.setattr("main.get_all_services", AsyncMock(return_value=[]))
        monkeypatch.setattr("main.get_model_info", lambda: None)
        monkeypatch.setattr("main.get_bootstrap_status", lambda: BootstrapStatus(active=False))
        monkeypatch.setattr("main.get_loaded_model", AsyncMock(return_value=None))
        monkeypatch.setattr("main.get_llama_metrics", AsyncMock(return_value={"tokens_per_second": 0, "lifetime_tokens": 0}))
        monkeypatch.setattr("main.get_llama_context_size", AsyncMock(return_value=None))
        monkeypatch.setattr("main.get_uptime", lambda: 0)
        monkeypatch.setattr("main.get_cpu_metrics", lambda: {"percent": 0, "temp_c": None})
        monkeypatch.setattr("main.get_ram_metrics", lambda: {"used_gb": 0, "total_gb": 0, "percent": 0})

        result = await _build_api_status()
        assert result["tier"] == "Standard"

    @pytest.mark.asyncio
    async def test_tier_minimal(self, monkeypatch):
        from models import GPUInfo, BootstrapStatus
        gpu = GPUInfo(
            name="GT 1030", memory_used_mb=256, memory_total_mb=2048,
            memory_percent=12.5, utilization_percent=5, temperature_c=40,
            gpu_backend="nvidia",
        )
        monkeypatch.setattr("main.get_gpu_info", lambda: gpu)
        monkeypatch.setattr("main.get_all_services", AsyncMock(return_value=[]))
        monkeypatch.setattr("main.get_model_info", lambda: None)
        monkeypatch.setattr("main.get_bootstrap_status", lambda: BootstrapStatus(active=False))
        monkeypatch.setattr("main.get_loaded_model", AsyncMock(return_value=None))
        monkeypatch.setattr("main.get_llama_metrics", AsyncMock(return_value={"tokens_per_second": 0, "lifetime_tokens": 0}))
        monkeypatch.setattr("main.get_llama_context_size", AsyncMock(return_value=None))
        monkeypatch.setattr("main.get_uptime", lambda: 0)
        monkeypatch.setattr("main.get_cpu_metrics", lambda: {"percent": 0, "temp_c": None})
        monkeypatch.setattr("main.get_ram_metrics", lambda: {"used_gb": 0, "total_gb": 0, "percent": 0})

        result = await _build_api_status()
        assert result["tier"] == "Minimal"

    @pytest.mark.asyncio
    async def test_no_gpu_returns_unknown_tier(self, monkeypatch):
        from models import BootstrapStatus
        monkeypatch.setattr("main.get_gpu_info", lambda: None)
        monkeypatch.setattr("main.get_all_services", AsyncMock(return_value=[]))
        monkeypatch.setattr("main.get_model_info", lambda: None)
        monkeypatch.setattr("main.get_bootstrap_status", lambda: BootstrapStatus(active=False))
        monkeypatch.setattr("main.get_loaded_model", AsyncMock(return_value=None))
        monkeypatch.setattr("main.get_llama_metrics", AsyncMock(return_value={"tokens_per_second": 0, "lifetime_tokens": 0}))
        monkeypatch.setattr("main.get_llama_context_size", AsyncMock(return_value=None))
        monkeypatch.setattr("main.get_uptime", lambda: 0)
        monkeypatch.setattr("main.get_cpu_metrics", lambda: {"percent": 0, "temp_c": None})
        monkeypatch.setattr("main.get_ram_metrics", lambda: {"used_gb": 0, "total_gb": 0, "percent": 0})

        result = await _build_api_status()
        assert result["tier"] == "Unknown"
        assert result["gpu"] is None

    @pytest.mark.asyncio
    async def test_gpu_with_power_draw(self, monkeypatch):
        from models import GPUInfo, BootstrapStatus
        gpu = GPUInfo(
            name="RTX 4090", memory_used_mb=2048, memory_total_mb=24576,
            memory_percent=8.3, utilization_percent=35, temperature_c=62,
            gpu_backend="nvidia", power_w=320.0,
        )
        monkeypatch.setattr("main.get_gpu_info", lambda: gpu)
        monkeypatch.setattr("main.get_all_services", AsyncMock(return_value=[]))
        monkeypatch.setattr("main.get_model_info", lambda: None)
        monkeypatch.setattr("main.get_bootstrap_status", lambda: BootstrapStatus(active=False))
        monkeypatch.setattr("main.get_loaded_model", AsyncMock(return_value=None))
        monkeypatch.setattr("main.get_llama_metrics", AsyncMock(return_value={"tokens_per_second": 0, "lifetime_tokens": 0}))
        monkeypatch.setattr("main.get_llama_context_size", AsyncMock(return_value=None))
        monkeypatch.setattr("main.get_uptime", lambda: 0)
        monkeypatch.setattr("main.get_cpu_metrics", lambda: {"percent": 0, "temp_c": None})
        monkeypatch.setattr("main.get_ram_metrics", lambda: {"used_gb": 0, "total_gb": 0, "percent": 0})

        result = await _build_api_status()
        assert result["gpu"]["powerDraw"] == 320.0

    @pytest.mark.asyncio
    async def test_active_bootstrap(self, monkeypatch):
        from models import GPUInfo, BootstrapStatus
        gpu = GPUInfo(
            name="RTX 4090", memory_used_mb=2048, memory_total_mb=24576,
            memory_percent=8.3, utilization_percent=35, temperature_c=62,
            gpu_backend="nvidia",
        )
        bs = BootstrapStatus(
            active=True, model_name="Qwen-32B", percent=50.0,
            downloaded_gb=8.0, total_gb=16.0, eta_seconds=120, speed_mbps=100.0,
        )
        monkeypatch.setattr("main.get_gpu_info", lambda: gpu)
        monkeypatch.setattr("main.get_all_services", AsyncMock(return_value=[]))
        monkeypatch.setattr("main.get_model_info", lambda: None)
        monkeypatch.setattr("main.get_bootstrap_status", lambda: bs)
        monkeypatch.setattr("main.get_loaded_model", AsyncMock(return_value=None))
        monkeypatch.setattr("main.get_llama_metrics", AsyncMock(return_value={"tokens_per_second": 0, "lifetime_tokens": 0}))
        monkeypatch.setattr("main.get_llama_context_size", AsyncMock(return_value=None))
        monkeypatch.setattr("main.get_uptime", lambda: 0)
        monkeypatch.setattr("main.get_cpu_metrics", lambda: {"percent": 0, "temp_c": None})
        monkeypatch.setattr("main.get_ram_metrics", lambda: {"used_gb": 0, "total_gb": 0, "percent": 0})

        result = await _build_api_status()
        assert result["bootstrap"]["active"] is True
        assert result["bootstrap"]["model"] == "Qwen-32B"
        assert result["bootstrap"]["percent"] == 50.0
