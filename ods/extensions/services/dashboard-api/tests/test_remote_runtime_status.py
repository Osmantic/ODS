"""The selected cloud provider must not inherit local model telemetry."""

import asyncio
from unittest.mock import AsyncMock

import pytest

import main
from host_agent_client import AgentHTTPError, AgentProtocolError, AgentTimeout, AgentUnavailable
from models import BootstrapStatus, DiskUsage, ModelInfo


REMOTE = {
    "source": "remote-provider", "model": "deepseek-v4.1-flash",
    "contextLength": 131072, "maxTokens": 8192, "reasoning": False,
    "routeFingerprint": "a" * 64,
}


@pytest.fixture
def status_helpers(monkeypatch):
    monkeypatch.setattr(main, 'get_cloud_throughput', AsyncMock(return_value={}))
    for name, value in {
        "get_gpu_info": None, "get_bootstrap_status": BootstrapStatus(active=False),
        "get_model_info": ModelInfo(name="Stale-Claude", size_gb=0, context_length=200000),
        "get_uptime": 0, "get_cpu_metrics": {}, "get_ram_metrics": {},
        "get_disk_usage": DiskUsage(path="/fixture", used_gb=1, total_gb=10, percent=10),
    }.items():
        monkeypatch.setattr(main, name, lambda value=value: value)
    monkeypatch.setattr(main, "_get_services", AsyncMock(return_value=[]))
    mocks = {
        "get_loaded_model": AsyncMock(return_value="Stray-Local"),
        "get_llama_metrics": AsyncMock(return_value={"tokens_per_second": 99, "throughput_model": "Stray-Local"}),
        "get_llama_context_size": AsyncMock(return_value=4096),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(main, name, mock)
    return mocks


@pytest.mark.asyncio
async def test_remote_provider_uses_only_its_own_completion_measurement(monkeypatch, status_helpers):
    monkeypatch.setattr(main, 'async_request_agent_json', AsyncMock(return_value={'activeRuntime': REMOTE}))
    monkeypatch.setattr(main, 'get_cloud_throughput', AsyncMock(return_value={
        'tokens_per_second': 12.5, 'throughput_mode': 'cloud_request_average',
        'throughput_state': 'retained', 'throughput_model': REMOTE['model']}))
    result = await main._build_api_status()
    assert result['model']['tokensPerSecond'] == result['inference']['tokensPerSecond'] == 12.5
    assert result['inference']['loadedModel'] is None
    assert result['inference']['throughputMode'] == 'cloud_request_average'
    for mock in status_helpers.values():
        mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [True, False])
async def test_remote_provider_overrides_stale_config_and_local_residency(monkeypatch, status_helpers, configured):
    monkeypatch.setattr(main, "async_request_agent_json", AsyncMock(return_value={"activeRuntime": REMOTE}))
    if not configured:
        monkeypatch.setattr(main, "get_model_info", lambda: None)
    result = await main._build_api_status()
    assert result["currentModel"] == result["model"]["currentModel"] == REMOTE["model"]
    assert result["model"]["name"] == REMOTE["model"]
    assert result["loadedModel"] is result["model"]["loadedModel"] is result["inference"]["loadedModel"] is None
    assert result["model"]["contextLength"] == result["inference"]["contextSize"] == 131072
    assert result["configuredModel"] == ("Stale-Claude" if configured else None)
    assert result["model"]["tokensPerSecond"] is result["inference"]["tokensPerSecond"] is None
    assert result["inference"]["lifetimeTokens"] is None
    assert result["inference"]["throughputMode"] == "unavailable"
    for mock in status_helpers.values():
        mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [AgentUnavailable("fixture"), AgentTimeout("fixture"), AgentProtocolError("fixture"), AgentHTTPError(503, "fixture")])
async def test_local_telemetry_survives_unavailable_host(monkeypatch, status_helpers, error):
    monkeypatch.setattr(main, "async_request_agent_json", AsyncMock(side_effect=error))
    result = await main._build_api_status()
    assert result["currentModel"] == result["loadedModel"] == "Stray-Local"
    assert result["model"]["contextLength"] == 4096
    assert result["model"]["tokensPerSecond"] == 99
    for mock in status_helpers.values():
        mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_remote_projection_uses_async_bounded_host_endpoint(monkeypatch):
    rpc = AsyncMock(return_value={"activeRuntime": REMOTE})
    monkeypatch.setattr(main, "async_request_agent_json", rpc)
    assert await main._get_dashboard_remote_runtime() == REMOTE
    rpc.assert_awaited_once_with("GET", "/v1/model/status", timeout=2.0)


@pytest.mark.asyncio
async def test_cold_host_cache_is_repolled_without_using_stale_identity(monkeypatch):
    rpc = AsyncMock(side_effect=[{"status": "complete", "model": "Old-Local"}, {"activeRuntime": REMOTE}])
    monkeypatch.setattr(main, "async_request_agent_json", rpc)
    assert await main._get_dashboard_remote_runtime() == REMOTE
    assert rpc.await_count == 2


@pytest.mark.asyncio
async def test_unconfirmed_cloud_route_does_not_inherit_local_or_configured_identity(monkeypatch, status_helpers):
    monkeypatch.setattr(main, "read_live_env_value", lambda _key: "cloud")
    monkeypatch.setattr(main, "async_request_agent_json", AsyncMock(return_value={"status": "complete"}))
    result = await main._build_api_status()
    assert result["model"] is result["currentModel"] is result["loadedModel"] is None
    assert result["configuredModel"] == "Stale-Claude"
    assert result["inference"]["contextSize"] is result["inference"]["tokensPerSecond"] is None
    for mock in status_helpers.values():
        mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_repoll_shares_the_absolute_deadline(monkeypatch):
    cancelled = asyncio.Event()
    calls = 0

    async def rpc(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(1.5)
            return {"status": "complete"}
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(main, "async_request_agent_json", rpc)
    start = asyncio.get_running_loop().time()
    assert await asyncio.wait_for(main._get_dashboard_remote_runtime(), timeout=2.5) is None
    assert asyncio.get_running_loop().time() - start < 2.5
    assert calls == 2 and cancelled.is_set()


@pytest.mark.asyncio
async def test_remote_probe_cancels_transport_that_exceeds_total_deadline(monkeypatch):
    cancelled = asyncio.Event()

    async def stuck(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(main, "async_request_agent_json", stuck)
    assert await asyncio.wait_for(main._get_dashboard_remote_runtime(), timeout=3.0) is None
    assert cancelled.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [
    None, {"models": [REMOTE["model"]]},
    {"activeRuntime": {**REMOTE, "contextLength": True}},
    {"activeRuntime": {**REMOTE, "contextLength": 2048}},
    {"activeRuntime": {**REMOTE, "apiKey": "must-not-project"}},
    {"activeRuntime": {**REMOTE, "routeFingerprint": "invalid"}},
    {"activeRuntime": {"source": "local-switchboard", "model": "Local", "contextLength": 4096}},
    {"activeRuntime": {"source": "external-host", "model": "External", "contextLength": 8192}},
])
async def test_catalog_malformed_and_other_sources_do_not_select_remote(monkeypatch, status):
    monkeypatch.setattr(main, "async_request_agent_json", AsyncMock(return_value=status))
    assert await main._get_dashboard_remote_runtime() is None
