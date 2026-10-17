"""Portal availability uses fresh owned health, not completion of optional metrics."""
import asyncio
import json
import subprocess
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import httpx
import pytest
from fastapi import FastAPI

import host_agent_client
from security import verify_api_key
from test_host_agent import _mod as agent
from test_pixel import FakeClient, FakeResponse, pixel, pixel_env as pixel_env


KEY = "isolated-host-health-key"
MODEL = "fixture-model.gguf"
ENV = {
    "GPU_BACKEND": "cpu", "LLM_BACKEND": "llama-server",
    "ODS_HOST_LLM_TRANSPORT": "model-router", "AMD_INFERENCE_LOCATION": "host",
    "NATIVE_LLM_CONTAINER_BASE_URL": "http://fixture-runtime:8080",
    "LLAMA_SERVER_API_KEY": "a" * 64,
}


@pytest.fixture
def owned_host(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(agent, "AGENT_API_KEY", KEY)
    monkeypatch.setattr(agent, "load_env", lambda _path: dict(ENV))
    monkeypatch.setattr(agent._wsl_runtime, "candidate", lambda env: env.get("ODS_HOST_LLM_TRANSPORT") == "model-router")
    monkeypatch.setattr(agent, "_host_llm_status_cache", (0.0, None))
    calls = []
    state = {"health": "ok", "delay_metrics": False}
    metrics_started = threading.Event()
    release_metrics = threading.Event()

    def transport(root, origin, path, **kwargs):
        # Substitute only the isolated runtime I/O boundary. Host health,
        # telemetry aggregation, authenticated HTTP handler and Portal use
        # their actual implementations, including the three-second timeout.
        assert root == tmp_path and origin == ENV["NATIVE_LLM_CONTAINER_BASE_URL"]
        assert kwargs["api_key"] == ENV["LLAMA_SERVER_API_KEY"]
        assert kwargs.get("payload") is None
        calls.append(path)
        if path == "/health":
            if isinstance(state["health"], Exception):
                raise state["health"]
            if isinstance(state["health"], dict):
                return json.dumps(state["health"])
            return json.dumps({"status": state["health"]})
        if path == "/v1/models":
            return json.dumps({"data": [{"id": MODEL}]})
        if path == "/props":
            return json.dumps({"default_generation_settings": {"n_ctx": 32768}})
        assert path == "/metrics"
        if state["delay_metrics"]:
            metrics_started.set()
            assert release_metrics.wait(8), "Test did not release optional metrics"
        return "llamacpp:prompt_tokens_total 0\n"

    monkeypatch.setattr(agent, "_router_transport_request", transport)
    server = ThreadingHTTPServer(("127.0.0.1", 0), agent.AgentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state, calls, metrics_started, release_metrics, f"http://127.0.0.1:{server.server_port}"
    release_metrics.set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


async def portal_status(monkeypatch, origin, *, lifecycle=None):
    monkeypatch.setattr(pixel, "read_live_env_value", lambda key: ENV.get(key, ""))

    async def idle():
        return lifecycle if lifecycle is not None else {"status": "idle", "modelTransactionPending": False}

    async def identity(*_args):
        return pixel.unknown_runtime_identity()

    async def access():
        return None, "access-probe-unavailable"

    monkeypatch.setattr(pixel, "_host_model_status", idle)
    monkeypatch.setattr(pixel, "_current_runtime_identity", identity)
    monkeypatch.setattr(pixel, "_current_access_readiness", access)
    monkeypatch.setattr(pixel, "request_agent_json", host_agent_client.async_request_json)
    edge = FakeClient(FakeResponse(chunks=[json.dumps({"data": [{"id": "portal/default"}]}).encode()]))
    monkeypatch.setattr(pixel, "get_edge_read_client", lambda: edge)
    app = FastAPI()
    app.include_router(pixel.router)
    app.dependency_overrides[verify_api_key] = lambda: None
    async with httpx.AsyncClient(base_url=origin, headers={"Authorization": "Bearer " + KEY}) as host:
        monkeypatch.setattr(host_agent_client, "_async_client", host)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://portal-test") as client:
            response = await client.get("/api/pixel/status")
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_slow_optional_metrics_cannot_turn_fresh_healthy_runtime_into_restore(monkeypatch, owned_host):
    state, calls, started, release, origin = owned_host
    state["delay_metrics"] = True
    telemetry = asyncio.create_task(asyncio.to_thread(agent._host_llm_status))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        assert not telemetry.done() and not release.is_set()
        # An ordinary metrics poll already owns the full telemetry lock. The
        # runtime's actual fresh /health is still healthy and independently readable.
        result = await portal_status(monkeypatch, origin)
        assert result["available"] is True, result
        assert "Restore" not in result["detail"]
        assert calls.count("/health") == 2
        assert calls.count("/metrics") == 1
        assert not telemetry.done() and not release.is_set()
    finally:
        release.set()
        await telemetry


@pytest.mark.asyncio
async def test_confirmed_unhealthy_runtime_stays_blocked(monkeypatch, owned_host):
    state, calls, _started, _release, origin = owned_host
    state["health"] = "error"
    result = await portal_status(monkeypatch, origin)
    assert result["available"] is False
    assert result["state"] == "model_unavailable"
    assert calls == ["/health"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [OSError("owned router proof unavailable"), subprocess.TimeoutExpired("private-command", 2)])
async def test_unverified_runtime_is_blocked_without_claiming_it_needs_restore(monkeypatch, owned_host, failure):
    state, calls, _started, _release, origin = owned_host
    state["health"] = failure
    result = await portal_status(monkeypatch, origin)
    assert result["available"] is False
    assert result["state"] == "model_unverified", result
    assert "Restore" not in result["detail"]
    assert "private" not in result["detail"]
    assert calls == ["/health"]


@pytest.mark.asyncio
@pytest.mark.parametrize("health,state", [
    ("loading", "model_loading"),
    ({"error": {"code": 503}}, "model_loading"),
    ({"status": {}}, "model_unverified"),
    ({"unrecognized": True}, "model_unverified"),
])
async def test_loading_and_malformed_health_never_become_available(monkeypatch, owned_host, health, state):
    config, calls, _started, _release, origin = owned_host
    config["health"] = health
    result = await portal_status(monkeypatch, origin)
    assert result["available"] is False
    assert result["state"] == state
    assert "Restore" not in result["detail"]
    assert calls == ["/health"]


def test_health_route_requires_auth_before_runtime_transport(owned_host):
    _state, calls, _started, _release, origin = owned_host
    with pytest.raises(urllib.error.HTTPError) as denied:
        urllib.request.urlopen(origin + "/v1/llm/health", timeout=2)
    assert denied.value.code == 401
    assert calls == []


@pytest.mark.asyncio
async def test_unsupported_host_keeps_existing_discovery_without_native_probe(monkeypatch, owned_host):
    _state, calls, _started, _release, origin = owned_host
    monkeypatch.setattr(agent, "_host_llm_runtime", lambda _env: "")
    result = await portal_status(monkeypatch, origin)
    assert result["available"] is True
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("lifecycle", [
    {"modelTransactionPending": True},
    {"lifecycleActive": True, "activeOperation": "model_activation"},
])
async def test_held_model_transition_precedes_even_healthy_native_runtime(monkeypatch, owned_host, lifecycle):
    _state, calls, _started, _release, origin = owned_host
    result = await portal_status(monkeypatch, origin, lifecycle=lifecycle)
    assert result["available"] is False
    assert result["state"] == "model_switching"
    assert calls == []


@pytest.mark.asyncio
async def test_previous_healthy_sample_does_not_mask_current_failure(monkeypatch, owned_host):
    state, calls, _started, _release, origin = owned_host
    assert (await portal_status(monkeypatch, origin))["available"] is True
    state["health"] = "error"
    result = await portal_status(monkeypatch, origin)
    assert result["available"] is False
    assert result["state"] == "model_unavailable"
    assert calls == ["/health", "/health"]
