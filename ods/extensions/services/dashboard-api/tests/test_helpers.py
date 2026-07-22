"""Tests for helpers.py — model info, bootstrap status, token tracking, system metrics."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import httpx
import pytest

from helpers import (
    get_model_info, get_bootstrap_status, _update_lifetime_tokens,
    get_uptime, get_cpu_metrics, get_ram_metrics,
    check_service_health, get_all_services,
    get_llama_metrics, get_loaded_model, get_llama_context_size,
    get_disk_usage, dir_size_gb, invalidate_dir_size_cache, clear_dir_size_cache,
    _get_aio_session, set_services_cache, get_cached_services,
    _get_httpx_client, _get_lifetime_tokens,
)
from models import BootstrapStatus, ServiceStatus, DiskUsage


# --- get_model_info ---


class TestGetModelInfo:

    def test_parses_32b_awq_model(self, install_dir):
        env_file = install_dir / ".env"
        env_file.write_text('LLM_MODEL=Qwen2.5-32B-Instruct-AWQ\n')

        info = get_model_info()
        assert info is not None
        assert info.name == "Qwen2.5-32B-Instruct-AWQ"
        assert info.size_gb == 16.0
        assert info.quantization == "AWQ"

    def test_strips_only_matched_quote_pairs(self, install_dir):
        # A double-quoted value keeps its inner single quotes, and a value
        # that legitimately ends with a quote character is not truncated.
        env_file = install_dir / ".env"
        env_file.write_text(
            "LLM_MODEL=\"Qwen2.5-7B-Instruct\"\n"
            "GGUF_FILE=model'v2.gguf\n"
        )

        info = get_model_info()
        assert info is not None
        assert info.name == "Qwen2.5-7B-Instruct"

    def test_keeps_mismatched_quotes_verbatim(self, install_dir):
        env_file = install_dir / ".env"
        env_file.write_text("LLM_MODEL=\"Qwen2.5-7B-Instruct'\n")

        info = get_model_info()
        assert info is not None
        assert info.name == "\"Qwen2.5-7B-Instruct'"

    def test_parses_7b_model(self, install_dir):
        env_file = install_dir / ".env"
        env_file.write_text('LLM_MODEL=Qwen2.5-7B-Instruct\n')

        info = get_model_info()
        assert info is not None
        assert info.size_gb == 4.0
        assert info.quantization is None

    def test_parses_14b_gptq_model(self, install_dir):
        env_file = install_dir / ".env"
        env_file.write_text('LLM_MODEL=Qwen2.5-14B-Instruct-GPTQ\n')

        info = get_model_info()
        assert info is not None
        assert info.size_gb == 8.0
        assert info.quantization == "GPTQ"

    def test_parses_70b_model(self, install_dir):
        env_file = install_dir / ".env"
        env_file.write_text('LLM_MODEL=Llama-3-70B-GGUF\n')

        info = get_model_info()
        assert info is not None
        assert info.size_gb == 35.0
        assert info.quantization == "GGUF"

    def test_parses_numeric_context(self, install_dir):
        env_file = install_dir / ".env"
        env_file.write_text('LLM_MODEL=Qwen2.5-7B-Instruct\nCTX_SIZE=8192\n')

        info = get_model_info()
        assert info is not None
        assert info.context_length == 8192

    def test_non_numeric_context_falls_back_to_default(self, install_dir):
        # A non-numeric CTX_SIZE/MAX_CONTEXT (e.g. "auto") must not 500 every
        # caller of get_model_info(); it falls back to the default context.
        env_file = install_dir / ".env"
        env_file.write_text('LLM_MODEL=Qwen2.5-7B-Instruct\nCTX_SIZE=auto\n')

        info = get_model_info()
        assert info is not None
        assert info.context_length == 32768

    def test_returns_none_when_no_env(self, install_dir):
        # No .env file created
        assert get_model_info() is None

    def test_returns_none_when_no_llm_model_line(self, install_dir):
        env_file = install_dir / ".env"
        env_file.write_text('SOME_OTHER_VAR=foo\n')

        assert get_model_info() is None

    def test_handles_quoted_value(self, install_dir):
        env_file = install_dir / ".env"
        env_file.write_text('LLM_MODEL="Qwen2.5-7B-Instruct"\n')

        info = get_model_info()
        assert info is not None
        assert info.name == "Qwen2.5-7B-Instruct"


# --- get_bootstrap_status ---


class TestGetBootstrapStatus:

    def test_inactive_when_no_file(self, data_dir):
        status = get_bootstrap_status()
        assert isinstance(status, BootstrapStatus)
        assert status.active is False

    def test_inactive_when_complete(self, data_dir):
        status_file = data_dir / "bootstrap-status.json"
        status_file.write_text(json.dumps({"status": "complete"}))

        status = get_bootstrap_status()
        assert status.active is False

    def test_inactive_when_empty_status(self, data_dir):
        status_file = data_dir / "bootstrap-status.json"
        status_file.write_text(json.dumps({"status": ""}))

        status = get_bootstrap_status()
        assert status.active is False

    def test_active_download(self, data_dir):
        status_file = data_dir / "bootstrap-status.json"
        status_file.write_text(json.dumps({
            "status": "downloading",
            "model": "Qwen2.5-32B",
            "percent": 42.5,
            "bytesDownloaded": 5 * 1024**3,
            "bytesTotal": 12 * 1024**3,
            "speedBytesPerSec": 50 * 1024**2,
            "eta": "3m 20s",
        }))

        status = get_bootstrap_status()
        assert status.active is True
        assert status.model_name == "Qwen2.5-32B"
        assert status.percent == 42.5
        assert status.eta_seconds == 200  # 3*60 + 20

    def test_eta_calculating(self, data_dir):
        status_file = data_dir / "bootstrap-status.json"
        status_file.write_text(json.dumps({
            "status": "downloading",
            "percent": 1.0,
            "eta": "calculating...",
        }))

        status = get_bootstrap_status()
        assert status.active is True
        assert status.eta_seconds is None

    def test_handles_malformed_json(self, data_dir):
        status_file = data_dir / "bootstrap-status.json"
        status_file.write_text("not json!")

        status = get_bootstrap_status()
        assert status.active is False

    def test_inactive_when_failed(self, data_dir):
        status_file = data_dir / "bootstrap-status.json"
        status_file.write_text(json.dumps({"status": "failed", "model": "test.gguf"}))

        status = get_bootstrap_status()
        assert status.active is False

    def test_active_when_downloading_model_file_on_disk(self, data_dir):
        models_dir = data_dir / "models"
        models_dir.mkdir(exist_ok=True)
        (models_dir / "present.gguf").write_bytes(b"\x00" * 1024)

        status_file = data_dir / "bootstrap-status.json"
        status_file.write_text(json.dumps({
            "status": "downloading", "model": "present.gguf",
            "percent": 50, "bytesDownloaded": 500, "bytesTotal": 1024,
        }))

        status = get_bootstrap_status()
        assert status.active is True

    def test_inactive_when_non_active_status_model_file_on_disk(self, data_dir):
        models_dir = data_dir / "models"
        models_dir.mkdir(exist_ok=True)
        (models_dir / "present.gguf").write_bytes(b"\x00" * 1024)

        status_file = data_dir / "bootstrap-status.json"
        status_file.write_text(json.dumps({
            "status": "stale", "model": "present.gguf",
            "percent": 50, "bytesDownloaded": 500, "bytesTotal": 1024,
        }))

        status = get_bootstrap_status()
        assert status.active is False

    def test_active_during_verifying_even_if_file_exists(self, data_dir):
        models_dir = data_dir / "models"
        models_dir.mkdir(exist_ok=True)
        (models_dir / "verifying.gguf").write_bytes(b"\x00" * 1024)

        status_file = data_dir / "bootstrap-status.json"
        status_file.write_text(json.dumps({
            "status": "verifying", "model": "verifying.gguf",
            "percent": 100, "bytesDownloaded": 1024, "bytesTotal": 1024,
        }))

        status = get_bootstrap_status()
        assert status.active is True

    def test_active_during_swapping_even_if_file_exists(self, data_dir):
        models_dir = data_dir / "models"
        models_dir.mkdir(exist_ok=True)
        (models_dir / "swapping.gguf").write_bytes(b"\x00" * 1024)

        status_file = data_dir / "bootstrap-status.json"
        status_file.write_text(json.dumps({
            "status": "swapping", "model": "swapping.gguf",
            "percent": 100, "bytesDownloaded": 1024, "bytesTotal": 1024,
        }))

        status = get_bootstrap_status()
        assert status.active is True

    def test_path_traversal_rejected(self, data_dir):
        status_file = data_dir / "bootstrap-status.json"
        status_file.write_text(json.dumps({
            "status": "downloading", "model": "../../etc/passwd",
            "percent": 50, "bytesDownloaded": 500, "bytesTotal": 1000,
        }))

        status = get_bootstrap_status()
        assert status.active is True


# --- _update_lifetime_tokens ---


class TestUpdateLifetimeTokens:

    def test_fresh_start(self, data_dir):
        result = _update_lifetime_tokens(100.0)
        assert result == 100

    def test_accumulates_across_calls(self, data_dir):
        _update_lifetime_tokens(100.0)
        result = _update_lifetime_tokens(250.0)
        assert result == 250  # 100 + (250 - 100)

    def test_handles_server_restart(self, data_dir):
        """When server_counter < prev, the counter has reset."""
        _update_lifetime_tokens(500.0)
        # Server restarted, counter back to 50
        result = _update_lifetime_tokens(50.0)
        # Should add 50 (treats reset counter as fresh delta)
        assert result == 550  # 500 + 50

    def test_handles_corrupted_token_file(self, data_dir):
        """Corrupted JSON should log a warning and start fresh."""
        token_file = data_dir / "token_counter.json"
        token_file.write_text("not valid json{{{")
        result = _update_lifetime_tokens(100.0)
        assert result == 100

    @pytest.mark.parametrize("payload", [
        [],
        {"lifetime": "not-a-number", "last_server_counter": "bad"},
        {"lifetime": -50, "last_server_counter": float("inf")},
    ])
    def test_normalizes_valid_json_with_invalid_counter_types(self, data_dir, payload):
        token_file = data_dir / "token_counter.json"
        token_file.write_text(json.dumps(payload))

        assert _update_lifetime_tokens(25.0) == 25
        assert _get_lifetime_tokens() == 25

    def test_retries_transient_windows_replace_failure(self, data_dir, monkeypatch):
        import helpers

        real_replace = helpers.os.replace
        calls = {"count": 0}

        def transient_replace(source, destination):
            calls["count"] += 1
            if calls["count"] < 3:
                raise PermissionError("temporarily locked")
            return real_replace(source, destination)

        monkeypatch.setattr(helpers.os, "replace", transient_replace)
        monkeypatch.setattr(helpers.time, "sleep", lambda _seconds: None)

        assert _update_lifetime_tokens(12.0) == 12
        assert calls["count"] == 3
        assert _get_lifetime_tokens() == 12

    def test_handles_unwritable_token_file(self, data_dir, monkeypatch):
        """When the token file cannot be written, should not raise."""
        import helpers
        monkeypatch.setattr(helpers, "_TOKEN_FILE", data_dir / "readonly" / "token.json")
        # Parent dir doesn't exist, so write will fail
        result = _update_lifetime_tokens(50.0)
        assert result == 50


# --- System metrics (cross-platform) ---


class TestGetUptime:

    def test_returns_int(self):
        result = get_uptime()
        assert isinstance(result, int)
        assert result >= 0

    def test_returns_zero_on_unsupported_platform(self, monkeypatch):
        monkeypatch.setattr("helpers.platform.system", lambda: "UnknownOS")
        assert get_uptime() == 0


class TestGetCpuMetrics:

    def test_returns_expected_keys(self):
        result = get_cpu_metrics()
        assert "percent" in result
        assert "temp_c" in result
        assert isinstance(result["percent"], (int, float))

    def test_returns_defaults_on_unsupported_platform(self, monkeypatch):
        monkeypatch.setattr("helpers.platform.system", lambda: "UnknownOS")
        result = get_cpu_metrics()
        assert result == {"percent": 0, "temp_c": None}


class TestGetRamMetrics:

    def test_returns_expected_keys(self):
        result = get_ram_metrics()
        assert "used_gb" in result
        assert "total_gb" in result
        assert "percent" in result

    def test_returns_defaults_on_unsupported_platform(self, monkeypatch):
        monkeypatch.setattr("helpers.platform.system", lambda: "UnknownOS")
        result = get_ram_metrics()
        assert result == {"used_gb": 0, "total_gb": 0, "percent": 0}


# --- check_service_health ---


class TestCheckServiceHealth:

    _CONFIG = {
        "name": "test-svc",
        "port": 8080,
        "external_port": 8080,
        "health": "/health",
        "host": "localhost",
    }

    @pytest.mark.asyncio
    async def test_healthy_on_200(self, mock_aiohttp_session, monkeypatch):
        session = mock_aiohttp_session(status=200)
        monkeypatch.setattr("helpers._get_aio_session", AsyncMock(return_value=session))

        result = await check_service_health("test-svc", self._CONFIG)
        assert result.status == "healthy"
        assert result.id == "test-svc"
        assert result.port == 8080

    @pytest.mark.asyncio
    async def test_sends_host_localhost_header(self, mock_aiohttp_session, monkeypatch):
        session = mock_aiohttp_session(status=200)
        monkeypatch.setattr("helpers._get_aio_session", AsyncMock(return_value=session))

        await check_service_health("test-svc", self._CONFIG)
        session.get.assert_called_once()
        _, kwargs = session.get.call_args
        assert kwargs.get("headers", {}).get("Host") == "localhost"

    @pytest.mark.asyncio
    async def test_unhealthy_on_500(self, mock_aiohttp_session, monkeypatch):
        session = mock_aiohttp_session(status=500)
        monkeypatch.setattr("helpers._get_aio_session", AsyncMock(return_value=session))

        result = await check_service_health("test-svc", self._CONFIG)
        assert result.status == "unhealthy"

    @pytest.mark.asyncio
    async def test_degraded_on_timeout(self, monkeypatch):
        session = MagicMock()
        session.get = MagicMock(side_effect=asyncio.TimeoutError())
        monkeypatch.setattr("helpers._get_aio_session", AsyncMock(return_value=session))

        result = await check_service_health("test-svc", self._CONFIG)
        assert result.status == "degraded"

    @pytest.mark.asyncio
    async def test_not_deployed_on_dns_failure(self, monkeypatch):
        from collections import namedtuple
        ConnKey = namedtuple('ConnectionKey', ['host', 'port', 'is_ssl', 'ssl', 'proxy', 'proxy_auth', 'proxy_headers_hash'])
        conn_key = ConnKey('test-svc', 8080, False, None, None, None, None)
        os_err = OSError("Name or service not known")
        os_err.strerror = "Name or service not known"
        exc = aiohttp.ClientConnectorError(conn_key, os_err)
        session = MagicMock()
        session.get = MagicMock(side_effect=exc)
        monkeypatch.setattr("helpers._get_aio_session", AsyncMock(return_value=session))

        result = await check_service_health("test-svc", self._CONFIG)
        assert result.status == "not_deployed"

    @pytest.mark.asyncio
    async def test_down_on_connection_refused(self, monkeypatch):
        conn_key = MagicMock()
        exc = aiohttp.ClientConnectorError(conn_key, OSError("Connection refused"))
        session = MagicMock()
        session.get = MagicMock(side_effect=exc)
        monkeypatch.setattr("helpers._get_aio_session", AsyncMock(return_value=session))

        result = await check_service_health("test-svc", self._CONFIG)
        assert result.status == "down"

    @pytest.mark.asyncio
    async def test_down_on_os_error(self, monkeypatch):
        session = MagicMock()
        session.get = MagicMock(side_effect=OSError("connection refused"))
        monkeypatch.setattr("helpers._get_aio_session", AsyncMock(return_value=session))

        result = await check_service_health("test-svc", self._CONFIG)
        assert result.status == "down"

    @pytest.mark.asyncio
    async def test_host_network_portless_service_is_not_deployed(self):
        config = {
            "name": "Portless",
            "port": 0,
            "external_port": 0,
            "health": "/health",
            "host": "localhost",
            "host_network": True,
        }

        result = await check_service_health("portless", config)
        assert result.status == "not_deployed"

    @pytest.mark.asyncio
    async def test_tailscale_not_running_is_not_deployed(self, monkeypatch):
        config = {
            "name": "Tailscale",
            "port": 0,
            "external_port": 0,
            "health": "/health",
            "host": "localhost",
            "host_network": True,
        }
        monkeypatch.setattr(
            "helpers.request_agent_json",
            AsyncMock(return_value={"running": False}),
        )

        result = await check_service_health("tailscale", config)
        assert result.status == "not_deployed"

    @pytest.mark.asyncio
    async def test_tailscale_authenticated_is_healthy(self, monkeypatch):
        config = {
            "name": "Tailscale",
            "port": 0,
            "external_port": 0,
            "health": "/health",
            "host": "localhost",
            "host_network": True,
        }
        monkeypatch.setattr(
            "helpers.request_agent_json",
            AsyncMock(return_value={"running": True, "authenticated": True}),
        )

        result = await check_service_health("tailscale", config)
        assert result.status == "healthy"


# --- get_all_services ---


class TestGetAllServices:

    @pytest.mark.asyncio
    async def test_returns_all_statuses(self, monkeypatch):
        fake_services = {
            "svc-a": {"name": "Service A", "port": 8001, "external_port": 8001, "health": "/health", "host": "localhost"},
            "svc-b": {"name": "Service B", "port": 8002, "external_port": 8002, "health": "/health", "host": "localhost"},
        }
        monkeypatch.setattr("helpers.SERVICES", fake_services)

        async def fake_health(sid, cfg):
            return ServiceStatus(id=sid, name=cfg["name"], port=cfg["port"],
                                 external_port=cfg["external_port"], status="healthy")

        monkeypatch.setattr("helpers.check_service_health", fake_health)

        result = await get_all_services()
        assert len(result) == 2
        ids = {s.id for s in result}
        assert ids == {"svc-a", "svc-b"}

    @pytest.mark.asyncio
    async def test_exception_in_one_service_returns_down(self, monkeypatch):
        fake_services = {
            "ok-svc": {"name": "OK", "port": 8001, "external_port": 8001, "health": "/health", "host": "localhost"},
            "bad-svc": {"name": "Bad", "port": 8002, "external_port": 8002, "health": "/health", "host": "localhost"},
        }
        monkeypatch.setattr("helpers.SERVICES", fake_services)

        async def fake_health(sid, cfg):
            if sid == "bad-svc":
                raise RuntimeError("unexpected failure")
            return ServiceStatus(id=sid, name=cfg["name"], port=cfg["port"],
                                 external_port=cfg["external_port"], status="healthy")

        monkeypatch.setattr("helpers.check_service_health", fake_health)

        result = await get_all_services()
        assert len(result) == 2
        bad = next(s for s in result if s.id == "bad-svc")
        assert bad.status == "down"
        ok = next(s for s in result if s.id == "ok-svc")
        assert ok.status == "healthy"

    @pytest.mark.asyncio
    async def test_empty_services_returns_empty(self, monkeypatch):
        monkeypatch.setattr("helpers.SERVICES", {})
        result = await get_all_services()
        assert result == []


# --- get_llama_metrics ---


class TestGetLlamaMetrics:

    @pytest.mark.asyncio
    async def test_parses_prometheus_metrics(self, monkeypatch):
        from conftest import load_golden_fixture
        prom_text = load_golden_fixture("prometheus_metrics.txt")

        fake_services = {
            "llama-server": {"host": "localhost", "port": 8080, "health": "/health", "name": "llama-server"},
        }
        monkeypatch.setattr("helpers.SERVICES", fake_services)

        # Reset the previous token state so TPS calculation is fresh
        import helpers
        helpers._prev_tokens.update({"count": 0, "time": 0.0, "tps": 0.0})

        mock_response = MagicMock()
        mock_response.text = prom_text
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        monkeypatch.setattr("helpers.httpx.AsyncClient", lambda **kw: mock_client)

        result = await get_llama_metrics(model_hint="test-model")
        assert "tokens_per_second" in result
        assert "lifetime_tokens" in result
        assert isinstance(result["tokens_per_second"], (int, float))

    @pytest.mark.asyncio
    async def test_returns_zero_on_failure(self, monkeypatch):
        fake_services = {
            "llama-server": {"host": "localhost", "port": 8080, "health": "/health", "name": "llama-server"},
        }
        monkeypatch.setattr("helpers.SERVICES", fake_services)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=OSError("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        monkeypatch.setattr("helpers.httpx.AsyncClient", lambda **kw: mock_client)

        result = await get_llama_metrics(model_hint="test-model")
        assert result["tokens_per_second"] == 0

    @pytest.mark.asyncio
    async def test_invalid_success_payload_does_not_reset_persistent_counter(
        self, monkeypatch, tmp_path,
    ):
        import helpers

        monkeypatch.setattr(
            "helpers.SERVICES",
            {"llama-server": {"host": "localhost", "port": 8080}},
        )
        monkeypatch.setattr(helpers, "_TOKEN_FILE", tmp_path / "token_counter.json")
        helpers._prev_tokens.update(
            {"count": 100, "time": helpers.time.time() - 1, "tps": 20.0, "gen_secs": 5.0},
        )
        assert helpers._update_lifetime_tokens(100) == 100

        mock_response = MagicMock()
        mock_response.text = "<html>proxy is starting</html>"
        mock_response.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        monkeypatch.setattr("helpers._get_httpx_client", AsyncMock(return_value=mock_client))

        result = await get_llama_metrics(model_hint="test-model")

        assert result == {
            "tokens_per_second": 0,
            "lifetime_tokens": 100,
            "token_count_mode": "cumulative",
        }
        assert helpers._get_lifetime_tokens() == 100
        assert json.loads(helpers._TOKEN_FILE.read_text())["last_server_counter"] == 100


# --- get_loaded_model ---


class TestGetLoadedModel:

    @pytest.mark.asyncio
    async def test_returns_model_with_loaded_status(self, monkeypatch):
        fake_services = {
            "llama-server": {"host": "localhost", "port": 8080, "health": "/health", "name": "llama-server"},
        }
        monkeypatch.setattr("helpers.SERVICES", fake_services)

        mock_response = MagicMock()
        mock_response.json = MagicMock(return_value={
            "data": [
                {"id": "idle-model", "status": {"value": "idle"}},
                {"id": "loaded-model", "status": {"value": "loaded"}},
            ]
        })

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        monkeypatch.setattr("helpers.httpx.AsyncClient", lambda **kw: mock_client)

        result = await get_loaded_model()
        assert result == "loaded-model"

    @pytest.mark.asyncio
    async def test_returns_first_model_when_no_loaded(self, monkeypatch):
        fake_services = {
            "llama-server": {"host": "localhost", "port": 8080, "health": "/health", "name": "llama-server"},
        }
        monkeypatch.setattr("helpers.SERVICES", fake_services)

        mock_response = MagicMock()
        mock_response.json = MagicMock(return_value={
            "data": [
                {"id": "only-model", "status": {"value": "idle"}},
            ]
        })

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        monkeypatch.setattr("helpers.httpx.AsyncClient", lambda **kw: mock_client)

        result = await get_loaded_model()
        assert result == "only-model"

    @pytest.mark.asyncio
    async def test_returns_none_on_failure(self, monkeypatch):
        fake_services = {
            "llama-server": {"host": "localhost", "port": 8080, "health": "/health", "name": "llama-server"},
        }
        monkeypatch.setattr("helpers.SERVICES", fake_services)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("unreachable"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        monkeypatch.setattr("helpers.httpx.AsyncClient", lambda **kw: mock_client)

        result = await get_loaded_model()
        assert result is None


# --- get_llama_context_size ---


class TestGetLlamaContextSize:

    @pytest.mark.asyncio
    async def test_returns_n_ctx(self, monkeypatch):
        fake_services = {
            "llama-server": {"host": "localhost", "port": 8080, "health": "/health", "name": "llama-server"},
        }
        monkeypatch.setattr("helpers.SERVICES", fake_services)

        mock_response = MagicMock()
        mock_response.json = MagicMock(return_value={
            "default_generation_settings": {"n_ctx": 32768}
        })

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        monkeypatch.setattr("helpers.httpx.AsyncClient", lambda **kw: mock_client)

        result = await get_llama_context_size(model_hint="test-model")
        assert result == 32768

    @pytest.mark.asyncio
    async def test_returns_none_on_failure(self, monkeypatch):
        fake_services = {
            "llama-server": {"host": "localhost", "port": 8080, "health": "/health", "name": "llama-server"},
        }
        monkeypatch.setattr("helpers.SERVICES", fake_services)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("unreachable"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        monkeypatch.setattr("helpers.httpx.AsyncClient", lambda **kw: mock_client)

        result = await get_llama_context_size(model_hint="test-model")
        assert result is None


# --- get_disk_usage ---


class TestGetDiskUsage:

    def test_returns_disk_usage(self, monkeypatch):
        monkeypatch.setattr("helpers.INSTALL_DIR", "/tmp")

        result = get_disk_usage()
        assert isinstance(result, DiskUsage)
        assert result.total_gb > 0
        assert result.used_gb >= 0
        assert 0 <= result.percent <= 100

    def test_falls_back_to_home_dir(self, monkeypatch):
        monkeypatch.setattr("helpers.INSTALL_DIR", "/nonexistent/path/that/does/not/exist")

        import os
        result = get_disk_usage()
        assert isinstance(result, DiskUsage)
        assert result.path == os.path.expanduser("~")
        assert result.total_gb > 0


# --- _get_aio_session ---


class TestGetAioSession:

    @pytest.mark.asyncio
    async def test_creates_session(self, monkeypatch):
        import helpers
        monkeypatch.setattr(helpers, "_aio_session", None)
        monkeypatch.setattr(helpers, "_aio_session_lock", None)
        session = await _get_aio_session()
        assert session is not None
        await session.close()

    @pytest.mark.asyncio
    async def test_reuses_session(self, monkeypatch):
        import helpers
        monkeypatch.setattr(helpers, "_aio_session", None)
        monkeypatch.setattr(helpers, "_aio_session_lock", None)
        s1 = await _get_aio_session()
        s2 = await _get_aio_session()
        assert s1 is s2
        await s1.close()

    @pytest.mark.asyncio
    async def test_waits_for_singleton_lock_before_creating_session(self, monkeypatch):
        import helpers
        lock = asyncio.Lock()
        await lock.acquire()
        monkeypatch.setattr(helpers, "_aio_session", None)
        monkeypatch.setattr(helpers, "_aio_session_lock", lock)

        task = asyncio.create_task(_get_aio_session())
        await asyncio.sleep(0)

        assert not task.done()
        lock.release()
        session = await task
        assert session is not None
        await session.close()


# --- _get_httpx_client ---


class TestGetHttpxClient:

    @pytest.mark.asyncio
    async def test_reuses_client(self, monkeypatch):
        import helpers
        monkeypatch.setattr(helpers, "_httpx_client", None)
        monkeypatch.setattr(helpers, "_httpx_client_lock", None)
        c1 = await _get_httpx_client()
        c2 = await _get_httpx_client()
        assert c1 is c2
        await c1.aclose()

    @pytest.mark.asyncio
    async def test_waits_for_singleton_lock_before_creating_client(self, monkeypatch):
        import helpers
        lock = asyncio.Lock()
        await lock.acquire()
        monkeypatch.setattr(helpers, "_httpx_client", None)
        monkeypatch.setattr(helpers, "_httpx_client_lock", lock)

        task = asyncio.create_task(_get_httpx_client())
        await asyncio.sleep(0)

        assert not task.done()
        lock.release()
        client = await task
        assert client is not None
        await client.aclose()


# --- set_services_cache / get_cached_services ---


class TestServicesCache:

    def test_set_and_get(self, monkeypatch):
        import helpers
        monkeypatch.setattr(helpers, "_services_cache", None)
        assert get_cached_services() is None
        fake = [ServiceStatus(id="s", name="S", port=80, external_port=80, status="healthy")]
        set_services_cache(fake)
        assert get_cached_services() == fake

    def test_optional_host_systemd_down_is_cached_as_not_deployed(self, monkeypatch):
        import helpers
        monkeypatch.setattr(helpers, "_services_cache", None)
        monkeypatch.setattr(helpers, "SERVICES", {
            "opencode": {
                "name": "OpenCode (IDE)",
                "port": 3003,
                "external_port": 3003,
                "health": "/",
                "host": "localhost",
                "type": "host-systemd",
                "category": "optional",
            },
            "dashboard-api": {
                "name": "Dashboard API",
                "port": 3002,
                "external_port": 3002,
                "health": "/health",
                "host": "localhost",
            },
        })

        set_services_cache([
            ServiceStatus(
                id="opencode",
                name="OpenCode (IDE)",
                port=3003,
                external_port=3003,
                status="down",
            ),
            ServiceStatus(
                id="dashboard-api",
                name="Dashboard API",
                port=3002,
                external_port=3002,
                status="down",
            ),
        ])

        cached = {service.id: service for service in get_cached_services()}
        assert cached["opencode"].status == "not_deployed"
        assert cached["dashboard-api"].status == "down"


# --- _get_lifetime_tokens ---


class TestGetLifetimeTokens:

    def test_returns_zero_when_no_file(self, data_dir):
        assert _get_lifetime_tokens() == 0

    def test_returns_lifetime_from_file(self, data_dir):
        token_file = data_dir / "token_counter.json"
        token_file.write_text(json.dumps({"lifetime": 42}))
        assert _get_lifetime_tokens() == 42


# --- check_service_health host-systemd ---


class TestCheckServiceHealthSystemd:

    @pytest.mark.asyncio
    async def test_host_systemd_returns_healthy_when_host_agent_proves_port(self, monkeypatch):
        async def fake_request(method, path, *, params, timeout):
            assert method == "GET"
            assert path == "/v1/host/port"
            assert params == {"host": "127.0.0.1", "port": 3003}
            assert timeout == 5
            return {"reachable": True, "response_time_ms": 12.3}

        monkeypatch.setattr("helpers.request_agent_json", fake_request)

        config = {
            "name": "opencode", "port": 3003, "external_port": 3003,
            "health": "/health", "host": "localhost", "type": "host-systemd",
        }
        result = await check_service_health("opencode", config)
        assert result.status == "healthy"
        assert result.response_time_ms == 12.3

    @pytest.mark.asyncio
    async def test_host_systemd_returns_not_deployed_when_host_port_closed(self, monkeypatch):
        monkeypatch.setattr(
            "helpers.request_agent_json",
            AsyncMock(return_value={"reachable": False, "response_time_ms": 2.0}),
        )

        config = {
            "name": "opencode", "port": 3003, "external_port": 3003,
            "health": "/health", "host": "localhost", "type": "host-systemd",
        }
        result = await check_service_health("opencode", config)
        assert result.status == "not_deployed"
        assert result.response_time_ms == 2.0


# --- get_model_info error branch ---


class TestGetModelInfoErrors:

    def test_returns_none_on_os_error(self, install_dir, monkeypatch):
        env_file = install_dir / ".env"
        env_file.write_text('LLM_MODEL=test\n')
        # Make the open fail after exists() returns True
        import builtins
        orig_open = builtins.open
        def failing_open(path, *a, **kw):
            if str(path).endswith(".env"):
                raise OSError("permission denied")
            return orig_open(path, *a, **kw)
        monkeypatch.setattr(builtins, "open", failing_open)
        assert get_model_info() is None


# --- get_bootstrap_status eta/percent branches ---


class TestGetBootstrapStatusEdgeCases:

    def test_eta_single_seconds_value(self, data_dir):
        status_file = data_dir / "bootstrap-status.json"
        status_file.write_text(json.dumps({
            "status": "downloading", "percent": 90, "eta": "45s",
        }))
        status = get_bootstrap_status()
        assert status.active is True
        assert status.eta_seconds == 45

    def test_invalid_percent_type(self, data_dir):
        status_file = data_dir / "bootstrap-status.json"
        status_file.write_text(json.dumps({
            "status": "downloading", "percent": "not-a-number",
            "bytesDownloaded": 100,
        }))
        status = get_bootstrap_status()
        assert status.active is True
        assert status.percent is None

    def test_speed_and_sizes(self, data_dir):
        status_file = data_dir / "bootstrap-status.json"
        status_file.write_text(json.dumps({
            "status": "downloading",
            "bytesDownloaded": 2 * 1024**3,
            "bytesTotal": 10 * 1024**3,
            "speedBytesPerSec": 100 * 1024**2,
        }))
        status = get_bootstrap_status()
        assert status.active is True
        assert status.downloaded_gb is not None
        assert abs(status.downloaded_gb - 2.0) < 0.01
        assert status.speed_mbps is not None

    def test_oversized_progress_is_clamped_for_active_download(self, data_dir):
        status_file = data_dir / "bootstrap-status.json"
        status_file.write_text(json.dumps({
            "status": "downloading",
            "model": "Full.gguf",
            "percent": 143.2,
            "bytesDownloaded": 150,
            "bytesTotal": 100,
        }))
        status = get_bootstrap_status()
        assert status.active is True
        assert status.percent == 100.0
        assert status.downloaded_gb == 100 / (1024**3)
        assert status.total_gb == 100 / (1024**3)


# --- get_uptime platform branches ---


class TestGetUptimePlatforms:

    def test_linux_reads_proc_uptime(self, monkeypatch):
        monkeypatch.setattr("helpers.platform.system", lambda: "Linux")
        import builtins
        orig_open = builtins.open
        def fake_open(path, *a, **kw):
            if str(path) == "/proc/uptime":
                from io import StringIO
                return StringIO("12345.67 9876.54")
            return orig_open(path, *a, **kw)
        monkeypatch.setattr(builtins, "open", fake_open)
        assert get_uptime() == 12345

    def test_darwin_branch(self, monkeypatch):
        monkeypatch.setattr("helpers.platform.system", lambda: "Darwin")
        import time
        mock_result = MagicMock()
        mock_result.returncode = 0
        boot_time = int(time.time()) - 600
        mock_result.stdout = f"{{ sec = {boot_time}, usec = 0 }} Mon Jan 1 00:00:00 2026"
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: mock_result)
        result = get_uptime()
        assert 595 <= result <= 610


# --- get_llama_metrics TPS calculation branch ---


class TestGetLlamaMetricsTPS:

    @pytest.mark.asyncio
    async def test_tps_calculated_on_second_call(self, monkeypatch):
        """TPS is calculated when previous token count and gen_secs are set."""
        import helpers
        import time as _time

        fake_services = {
            "llama-server": {"host": "localhost", "port": 8080, "health": "/health", "name": "llama-server"},
        }
        monkeypatch.setattr("helpers.SERVICES", fake_services)

        # Set up previous state
        helpers._prev_tokens.update({"count": 100, "time": _time.time() - 1, "tps": 0.0, "gen_secs": 5.0})

        # Mock response with updated token counts
        mock_response = MagicMock()
        mock_response.text = (
            "# HELP tokens_predicted_total\n"
            "tokens_predicted_total 200\n"
            "# HELP tokens_predicted_seconds_total\n"
            "tokens_predicted_seconds_total 10.0\n"
        )
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        monkeypatch.setattr("helpers.httpx.AsyncClient", lambda **kw: mock_client)

        result = await get_llama_metrics(model_hint="test")
        # 100 tokens / 5 seconds = 20.0 tps
        assert result["tokens_per_second"] == 20.0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("previous_count", "current_count"),
        [(200, 200), (200, 25)],
        ids=["idle", "server-counter-reset"],
    )
    async def test_idle_or_reset_counter_clears_stale_throughput(
        self, monkeypatch, tmp_path, previous_count, current_count,
    ):
        import helpers

        monkeypatch.setattr(
            "helpers.SERVICES",
            {"llama-server": {"host": "localhost", "port": 8080}},
        )
        monkeypatch.setattr(helpers, "_TOKEN_FILE", tmp_path / "token_counter.json")
        helpers._prev_tokens.update(
            {
                "count": previous_count,
                "time": helpers.time.time() - 1,
                "tps": 42.0,
                "gen_secs": 10.0,
            },
        )

        mock_response = MagicMock()
        mock_response.text = (
            f"llamacpp_tokens_predicted_total {current_count}\n"
            "llamacpp_tokens_predicted_seconds_total 10.0\n"
        )
        mock_response.raise_for_status.return_value = None
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        monkeypatch.setattr("helpers._get_httpx_client", AsyncMock(return_value=mock_client))

        result = await get_llama_metrics(model_hint="test")

        assert result["tokens_per_second"] == 0.0


class TestLemonadeMetrics:
    @pytest.mark.asyncio
    async def test_host_stats_report_real_tps_and_latest_completion_tokens(
        self, monkeypatch, tmp_path,
    ):
        import helpers

        token_file = tmp_path / "token_counter.json"
        monkeypatch.setattr(helpers, "_TOKEN_FILE", token_file)
        monkeypatch.setattr(helpers, "LLM_BACKEND", "lemonade")
        monkeypatch.setattr(helpers, "read_live_env_value", lambda key: "host")
        stats = {
            "input_tokens": 24,
            "output_tokens": 7,
            "prompt_tokens": 24,
            "decode_token_times": [0.01, 0.02],
            "time_to_first_token": 0.04,
            "tokens_per_second": 188.49,
        }
        request = AsyncMock(return_value={
            "schema_version": "ods.host-llm-status.v1",
            "health": {"status": "ok"},
            "stats": stats,
        })
        monkeypatch.setattr(helpers, "request_agent_json", request)

        result = await helpers.get_llama_metrics()

        assert result == {
            "tokens_per_second": 188.5,
            "lifetime_tokens": 7,
            "token_count_mode": "latest_completion",
        }
        assert not token_file.exists()

    @pytest.mark.asyncio
    async def test_unavailable_host_stats_do_not_relabel_stale_llama_total(
        self, monkeypatch, tmp_path,
    ):
        import helpers

        token_file = tmp_path / "token_counter.json"
        token_file.write_text(json.dumps({"lifetime": 42}))
        monkeypatch.setattr(helpers, "_TOKEN_FILE", token_file)
        monkeypatch.setattr(helpers, "LLM_BACKEND", "lemonade")
        monkeypatch.setattr(helpers, "read_live_env_value", lambda key: "host")
        monkeypatch.setattr(
            helpers, "request_agent_json",
            AsyncMock(return_value={"health": {"status": "ok"}, "stats": None}),
        )

        result = await helpers.get_llama_metrics()

        assert result == {
            "tokens_per_second": 0,
            "lifetime_tokens": 0,
            "token_count_mode": "unavailable",
        }

    @pytest.mark.asyncio
    async def test_container_runtime_accepts_new_v1_stats_route(self, monkeypatch, tmp_path):
        import helpers

        monkeypatch.setattr(helpers, "_TOKEN_FILE", tmp_path / "token_counter.json")
        monkeypatch.setattr(helpers, "LLM_BACKEND", "lemonade")
        monkeypatch.setattr(
            helpers, "read_live_env_value",
            lambda key: "container" if key == "AMD_INFERENCE_LOCATION" else "test-key",
        )
        monkeypatch.setattr(helpers, "SERVICES", {
            "llama-server": {"host": "llama-server", "port": 8080},
        })
        legacy = MagicMock()
        legacy.raise_for_status.side_effect = httpx.HTTPStatusError(
            "not found", request=MagicMock(), response=MagicMock(status_code=404),
        )
        current = MagicMock()
        current.raise_for_status.return_value = None
        current.json.return_value = {
            "output_tokens": 5,
            "tokens_per_second": 33.33,
            "time_to_first_token": 0.2,
        }
        client = AsyncMock()
        client.get = AsyncMock(side_effect=[legacy, current])
        monkeypatch.setattr(helpers, "_get_httpx_client", AsyncMock(return_value=client))

        result = await helpers.get_llama_metrics()

        assert result == {
            "tokens_per_second": 33.3,
            "lifetime_tokens": 5,
            "token_count_mode": "latest_completion",
        }
        assert [call.args[0] for call in client.get.await_args_list] == [
            "http://llama-server:8080/api/v1/stats",
            "http://llama-server:8080/v1/stats",
        ]
        assert all(
            call.kwargs["headers"] == {"Authorization": "Bearer test-key"}
            for call in client.get.await_args_list
        )


class TestServiceHealthReconciliation:

    @pytest.mark.asyncio
    async def test_docker_health_repairs_only_transient_timeout(self, monkeypatch):
        import helpers

        services = {
            "dashboard": {
                "name": "Dashboard", "port": 3001, "external_port": 3001,
                "type": "docker", "container_name": "ods-dashboard",
            },
            "open-webui": {
                "name": "Open WebUI", "port": 3000, "external_port": 3000,
                "type": "docker", "container_name": "ods-open-webui",
            },
        }
        monkeypatch.setattr(helpers, "SERVICES", services)

        async def probe(service_id, config):
            return ServiceStatus(
                id=service_id, name=config["name"], port=config["port"],
                external_port=config["external_port"],
                status="degraded" if service_id == "dashboard" else "unhealthy",
            )

        monkeypatch.setattr(helpers, "check_service_health", probe)
        monkeypatch.setattr(helpers, "LLM_BACKEND", "llama")
        monkeypatch.setattr(helpers, "request_agent_json", AsyncMock(return_value={
            "schema_version": "ods.host-service-health.v1",
            "containers": [
                {"service_id": "dashboard", "container_name": "ods-dashboard", "state": "running", "health": "healthy"},
                {"service_id": "open-webui", "container_name": "ods-open-webui", "state": "running", "health": "healthy"},
            ],
        }))

        statuses = {status.id: status.status for status in await helpers.get_all_services()}

        assert statuses == {"dashboard": "healthy", "open-webui": "unhealthy"}

    @pytest.mark.asyncio
    async def test_all_healthy_path_does_not_call_host_agent(self, monkeypatch):
        import helpers

        services = {
            "dashboard": {"name": "Dashboard", "port": 3001, "external_port": 3001},
        }
        monkeypatch.setattr(helpers, "SERVICES", services)

        async def probe(service_id, config):
            return ServiceStatus(
                id=service_id, name=config["name"], port=config["port"],
                external_port=config["external_port"], status="healthy",
            )

        request = AsyncMock()
        monkeypatch.setattr(helpers, "check_service_health", probe)
        monkeypatch.setattr(helpers, "request_agent_json", request)

        statuses = await helpers.get_all_services()

        assert statuses[0].status == "healthy"
        request.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "runtime_health,expected",
        [
            ({"status": "ok", "model_loaded": "model.gguf"}, "healthy"),
            ({"status": "error", "model_loaded": "model.gguf"}, "down"),
        ],
    )
    async def test_host_lemonade_requires_explicit_ok_status(
        self, monkeypatch, runtime_health, expected,
    ):
        import helpers

        services = {
            "llama-server": {
                "name": "LLM", "port": 8080, "external_port": 8080,
                "type": "docker", "container_name": "ods-llama-server",
            },
        }
        monkeypatch.setattr(helpers, "SERVICES", services)
        monkeypatch.setattr(helpers, "LLM_BACKEND", "lemonade")
        monkeypatch.setattr(
            helpers, "read_live_env_value",
            lambda key: "host" if key == "AMD_INFERENCE_LOCATION" else "",
        )

        async def probe(service_id, config):
            return ServiceStatus(
                id=service_id, name=config["name"], port=config["port"],
                external_port=config["external_port"], status="down",
            )

        request = AsyncMock(side_effect=[
            {"schema_version": "ods.host-service-health.v1", "containers": []},
            {"schema_version": "ods.host-llm-status.v1", "health": runtime_health},
        ])
        monkeypatch.setattr(helpers, "check_service_health", probe)
        monkeypatch.setattr(helpers, "request_agent_json", request)

        statuses = await helpers.get_all_services()

        assert statuses[0].status == expected


# --- bootstrap status ETA edge cases ---


class TestBootstrapStatusEtaEdge:

    def test_invalid_eta_string(self, data_dir):
        """ETA with unparseable content → eta_seconds is None."""
        status_file = data_dir / "bootstrap-status.json"
        status_file.write_text(json.dumps({
            "status": "downloading", "percent": 50,
            "eta": "not a number at all",
        }))
        status = get_bootstrap_status()
        assert status.active is True
        assert status.eta_seconds is None


# --- dir_size_gb ---


class TestDirSizeGb:

    @staticmethod
    def _symlink_or_skip(link: Path, target: Path):
        try:
            link.symlink_to(target)
        except OSError as exc:
            pytest.skip(f"symlink creation unavailable in this test environment: {exc}")

    def test_nonexistent_path_returns_zero(self, tmp_path):
        clear_dir_size_cache()
        assert dir_size_gb(tmp_path / "does-not-exist") == 0.0

    def test_empty_directory_returns_zero(self, tmp_path):
        clear_dir_size_cache()
        empty = tmp_path / "empty"
        empty.mkdir()
        assert dir_size_gb(empty) == 0.0

    def test_directory_with_files(self, tmp_path):
        clear_dir_size_cache()
        d = tmp_path / "data"
        d.mkdir()
        # Write 100 MiB (avoids allocating 1 GiB in CI)
        size = 1024 * 1024 * 100
        (d / "bigfile.bin").write_bytes(b"\x00" * size)
        assert dir_size_gb(d) == 0.1

    def test_symlinks_are_skipped(self, tmp_path):
        clear_dir_size_cache()
        d = tmp_path / "withlinks"
        d.mkdir()
        real = d / "real.bin"
        real.write_bytes(b"\x00" * 1024)
        link = d / "link.bin"
        self._symlink_or_skip(link, real)
        # Only real.bin should be counted (1024 B ≈ 0.0 GB when rounded to 2dp)
        result = dir_size_gb(d)
        assert result == 0.0  # 1024 bytes rounds to 0.0 GB

    def test_checks_symlink_before_is_file(self, tmp_path, monkeypatch):
        clear_dir_size_cache()
        d = tmp_path / "withlinks"
        d.mkdir()
        outside = tmp_path / "outside.bin"
        outside.write_bytes(b"\x00" * 1024)
        link = d / "outside-link.bin"
        self._symlink_or_skip(link, outside)

        original_is_file = Path.is_file

        def guarded_is_file(self):
            if self == link:
                raise AssertionError("dir_size_gb called is_file before skipping symlink")
            return original_is_file(self)

        monkeypatch.setattr(Path, "is_file", guarded_is_file)
        assert dir_size_gb(d) == 0.0

    def test_uses_cached_value_until_invalidated(self, tmp_path, monkeypatch):
        clear_dir_size_cache()
        d = tmp_path / "cached"
        d.mkdir()
        (d / "data.bin").write_bytes(b"\x00" * 1024)

        assert dir_size_gb(d) == 0.0

        def _unexpected_rglob(self, pattern):
            raise AssertionError("dir_size_gb unexpectedly walked the filesystem")

        monkeypatch.setattr(Path, "rglob", _unexpected_rglob)
        assert dir_size_gb(d) == 0.0

    def test_invalidate_dir_size_cache_forces_refresh(self, tmp_path, monkeypatch):
        clear_dir_size_cache()
        d = tmp_path / "refresh"
        d.mkdir()
        (d / "data.bin").write_bytes(b"\x00" * 1024)

        assert dir_size_gb(d) == 0.0

        original_rglob = Path.rglob
        calls = {"count": 0}

        def _tracking_rglob(self, pattern):
            calls["count"] += 1
            return original_rglob(self, pattern)

        monkeypatch.setattr(Path, "rglob", _tracking_rglob)
        assert dir_size_gb(d) == 0.0
        assert calls["count"] == 0

        invalidate_dir_size_cache(d)
        assert dir_size_gb(d) == 0.0
        assert calls["count"] == 1

    def test_dir_size_cache_bound(self, tmp_path):
        from helpers import _dir_size_cache
        _dir_size_cache.clear()

        # Fill cache with 1005 items
        for i in range(1005):
            path = tmp_path / f"test_dir_{i}"
            _dir_size_cache.set(path, 1.0)

        assert len(_dir_size_cache._store) == 1000

        # Verify older items were evicted
        first_path = tmp_path / "test_dir_0"
        assert _dir_size_cache.get(first_path) is None
