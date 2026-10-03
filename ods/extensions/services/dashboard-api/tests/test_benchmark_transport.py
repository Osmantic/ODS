"""Benchmark transport follows the installed chat route and bound credentials."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import config
from setup_chat_route import KEYS


@pytest.fixture
def benchmark_transport(test_client, monkeypatch, tmp_path):
    import routers.models as router
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(router, "LLM_BACKEND", "llama-server")
    monkeypatch.setattr(router, "SERVICES", {"llama-server": {"host": "llama-server", "port": 8080}})
    monkeypatch.setattr(router, "get_loaded_model", AsyncMock(return_value="fixture"))
    monkeypatch.setattr(router, "get_gpu_info", lambda: None)
    monkeypatch.setattr(router, "get_llama_context_size", AsyncMock(return_value=32768))
    monkeypatch.setattr(router, "get_llama_metrics", AsyncMock(return_value={}))
    monkeypatch.setattr(router, "_load_library", lambda: [])
    monkeypatch.setattr(router, "_installed_model_paths", lambda: {})
    monkeypatch.setattr(router, "build_models_payload", lambda *a, **k: {"models": [{"id": "fixture", "status": "loaded"}]})
    ticks = iter([100.0, 104.0])
    monkeypatch.setattr(router, "time", SimpleNamespace(perf_counter=lambda: next(ticks)))
    calls = []
    original_client = httpx.AsyncClient

    def configure(values, transport=None):
        (tmp_path / ".env").write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
        if transport is not None:
            monkeypatch.setattr(router.httpx, "AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))

    def call():
        return test_client.post("/api/models/fixture/benchmark", headers=test_client.auth_headers, json={"max_tokens": 64})

    return configure, call, calls


@pytest.mark.parametrize("values,endpoint,model,key", [
    ({}, "http://llama-server:8080/v1/chat/completions", "fixture", None),
    ({"LLM_API_URL": "http://litellm:4000", "LLM_BACKEND": "lemonade", "LITELLM_KEY": "gateway-key", "LEMONADE_API_KEY": "wrong-destination-key", "OPEN_WEBUI_LLM_BASE_URL": "http://litellm:4000/v1", "OPEN_WEBUI_TASK_MODEL": "cloud"}, "http://litellm:4000/v1/chat/completions", "ods/current", "gateway-key"),
    ({"LLM_API_URL": "http://llama-server:8080/v1/", "GGUF_FILE": "stale-checkpoint"}, "http://llama-server:8080/v1/chat/completions", "fixture", None),
    ({"LLM_API_URL": "https://lemonade.example:9443/api/v1/", "LLM_BACKEND": "lemonade", "LEMONADE_CONTAINER_BASE_URL": "https://lemonade.example:9443", "LEMONADE_API_KEY": "native-key", "LEMONADE_MODEL": "stale-model"}, "https://lemonade.example:9443/api/v1/chat/completions", "fixture", "native-key"),
    ({"LLM_API_URL": "http://host.docker.internal:13305", "LLM_BACKEND": "lemonade", "AMD_INFERENCE_PORT": "13305", "LITELLM_LEMONADE_API_KEY": "installed-key", "LEMONADE_MODEL": "stale-model"}, "http://host.docker.internal:13305/api/v1/chat/completions", "fixture", "installed-key"),
    ({"LLM_API_URL": "http://litellm:4000", "LITELLM_KEY": "gateway-key", "ODS_MODEL_SWITCHBOARD": "enabled", "LLM_API_BASE_PATH": "/api/v1"}, "http://litellm:4000/v1/chat/completions", "ods/current", "gateway-key"),
    ({"LLM_API_URL": "http://litellm:4000/v1", "LLM_BACKEND": "external", "LITELLM_KEY": "gateway-key"}, "http://litellm:4000/v1/chat/completions", "ods/current", "gateway-key"),
    ({"LLM_API_URL": "https://external.example:9443/prefix/v1/", "LLM_BACKEND": "external", "OPEN_WEBUI_LLM_BASE_URL": "https://external.example:9443/prefix/v1", "OPEN_WEBUI_LLM_API_KEY": "external-key", "OPEN_WEBUI_TASK_MODEL": "stale-external-model"}, "https://external.example:9443/prefix/v1/chat/completions", "fixture", "external-key"),
    ({"LLM_API_URL": "https://other.example/v1", "LLM_MODEL": "fixture", "LITELLM_KEY": "must-not-leak", "LEMONADE_API_KEY": "must-not-leak", "LEMONADE_CONTAINER_BASE_URL": "https://lemonade.example", "OPEN_WEBUI_LLM_API_KEY": "must-not-leak", "OPEN_WEBUI_LLM_BASE_URL": "https://external.example/v1"}, "https://other.example/v1/chat/completions", "fixture", None),
])
def test_benchmark_uses_destination_bound_route(benchmark_transport, values, endpoint, model, key):
    configure, call, calls = benchmark_transport
    def respond(request):
        calls.append(request)
        if str(request.url) != endpoint or request.headers.get("Authorization") != (f"Bearer {key}" if key else None):
            return httpx.Response(401, json={"error": "wrong endpoint or credential"})
        return httpx.Response(200, json={"usage": {"completion_tokens": 64}})
    configure(values, httpx.MockTransport(respond))
    result = call()
    assert result.status_code == 200, result.text
    assert json.loads(calls[0].content)["model"] == model
    assert result.json()["generatedTokens"] == 64


def test_benchmark_real_authenticated_http_runtime(benchmark_transport, monkeypatch):
    configure, call, calls = benchmark_transport
    class Runtime(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append((self.path, self.headers.get("Authorization")))
            self.rfile.read(int(self.headers["Content-Length"]))
            ok = self.path == "/api/v1/chat/completions" and self.headers.get("Authorization") == "Bearer runtime-key"
            body = json.dumps({"usage": {"completion_tokens": 64}} if ok else {"error": "unauthorized"}).encode()
            self.send_response(200 if ok else 401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Runtime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_port
        import routers.models as router
        monkeypatch.setattr(router, "LLM_BACKEND", "lemonade")
        monkeypatch.setattr(router, "SERVICES", {"llama-server": {"host": "127.0.0.1", "port": port}})
        base = f"http://127.0.0.1:{port}"
        configure({"LLM_API_URL": base + "/api/v1/", "LLM_BACKEND": "lemonade", "LEMONADE_CONTAINER_BASE_URL": base, "LEMONADE_API_KEY": "runtime-key", "LEMONADE_MODEL": "stale-model"})
        result = call()
        assert result.status_code == 200, result.text
        assert calls == [("/api/v1/chat/completions", "Bearer runtime-key")]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_benchmark_invalid_route_makes_no_request_or_saved_sample(benchmark_transport, monkeypatch):
    configure, call, calls = benchmark_transport
    import routers.models as router
    saved = Mock()
    monkeypatch.setattr(router, "record_model_performance", saved)
    def unexpected_request(request):
        calls.append(request)
        raise AssertionError("Invalid configuration must not make an inference request")
    configure({"LLM_API_URL": "https://user:password@external.example/v1"}, httpx.MockTransport(unexpected_request))
    result = call()
    assert result.status_code == 503
    assert result.json()["detail"] == "The configured benchmark inference route is invalid"
    assert calls == []
    saved.assert_not_called()


@pytest.mark.parametrize("health_available", [True, False])
def test_benchmark_unloaded_runtime_never_loads_persisted_model(
    benchmark_transport, monkeypatch, tmp_path, health_available,
):
    configure, call, _ = benchmark_transport
    import routers.models as router
    from performance_oracle import build_models_payload

    completions = []
    runtime = {"loaded": None, "available": health_available}

    class UnloadedRuntime(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"status": "ok", "model_loaded": runtime["loaded"]}).encode()
            self.send_response(200 if runtime["available"] else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            completions.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            # A direct Lemonade completion can auto-load the requested model.
            body = json.dumps({"usage": {"completion_tokens": 64}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), UnloadedRuntime)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    saved = Mock()
    monkeypatch.setattr(router, "record_model_performance", saved)
    monkeypatch.setattr(router, "get_loaded_model", AsyncMock(return_value=None))
    monkeypatch.setattr(router, "LLM_BACKEND", "lemonade")
    monkeypatch.setattr(router, "SERVICES", {"llama-server": {"host": "127.0.0.1", "port": server.server_port}})
    monkeypatch.setattr(router, "INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(router, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(router, "_ENV_PATH", tmp_path / ".env")
    artifact = tmp_path / "data" / "models" / "fixture.gguf"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"downloaded model")
    monkeypatch.setattr(router, "_installed_model_paths", lambda: {artifact.name: artifact})
    monkeypatch.setattr(router, "_load_library", lambda: [{
        "id": "fixture", "name": "Fixture", "gguf_file": artifact.name,
        "llm_model_name": "fixture", "size_mb": 1, "context_length": 32768,
    }])
    monkeypatch.setattr(router, "build_models_payload", build_models_payload)
    try:
        configure({
            "LLM_API_URL": f"http://127.0.0.1:{server.server_port}/api/v1",
            "LLM_BACKEND": "lemonade", "LLM_MODEL": "fixture", "GGUF_FILE": artifact.name,
        })
        result = call()
        assert result.status_code == 503, result.text
        assert completions == []
        saved.assert_not_called()
        assert artifact.read_bytes() == b"downloaded model"

        # Once the owner loads a model, a fresh observation permits a benchmark.
        runtime.update(loaded="fixture", available=True)
        result = call()
        assert result.status_code == 200, result.text
        assert [item["model"] for item in completions] == ["fixture"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
