"""Benchmark HTTP receipts must describe the request, not other server traffic."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest


@pytest.fixture
def benchmark(test_client, monkeypatch):
    import routers.models as router

    target = {"id": "fixture", "status": "loaded", "gguf": "fixture.gguf"}
    monkeypatch.setattr(router, "SERVICES", {"llama-server": {"host": "llama-server", "port": 8080}})
    monkeypatch.setattr(router, "get_loaded_model", AsyncMock(return_value="fixture"))
    monkeypatch.setattr(router, "get_gpu_info", lambda: SimpleNamespace(name="fixture GPU", gpu_backend="nvidia"))
    monkeypatch.setattr(router, "get_llama_context_size", AsyncMock(return_value=32768))
    monkeypatch.setattr(router, "get_llama_metrics", AsyncMock(return_value={"tokens_per_second": 0}))
    monkeypatch.setattr(router, "_load_library", lambda: [])
    monkeypatch.setattr(router, "_scan_downloaded_models", lambda: [])
    monkeypatch.setattr(router, "build_models_payload", lambda *a, **k: {"models": [target]})
    monkeypatch.setattr(router, "build_sample_signature", lambda *a: {})
    saved = Mock()
    monkeypatch.setattr(router, "record_model_performance", saved)
    counters = AsyncMock(side_effect=[
        httpx.Response(200, text="llamacpp:tokens_predicted_total 100\nllamacpp:tokens_predicted_seconds_total 10", request=httpx.Request("GET", "http://llama-server/metrics")),
        httpx.Response(200, text="llamacpp:tokens_predicted_total 500\nllamacpp:tokens_predicted_seconds_total 12", request=httpx.Request("GET", "http://llama-server/metrics")),
    ])
    clock = iter([100.0, 104.0])
    monkeypatch.setattr(router, "time", SimpleNamespace(perf_counter=lambda: next(clock)))

    def call(payload):
        response = httpx.Response(200, json=payload, request=httpx.Request("POST", "http://llama-server/v1/chat/completions"))
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = response
        client.get = counters
        monkeypatch.setattr(router.httpx, "AsyncClient", lambda **kwargs: client)
        return test_client.post("/api/models/fixture/benchmark", headers=test_client.auth_headers, json={"max_tokens": 64})

    return call, saved, counters


def test_request_timings_win_over_concurrent_server_counters(benchmark):
    call, saved, counters = benchmark
    response = call({"timings": {"predicted_n": 64, "predicted_ms": 2000}, "usage": {"completion_tokens": 64}})
    assert response.status_code == 200
    result = response.json()
    assert result["generatedTokens"] == 64
    assert result["generateSeconds"] == 2
    assert result["tokensPerSecond"] == 32
    assert result["method"] == "request timings"
    assert saved.call_args.args[3] == 32
    counters.assert_not_awaited()


@pytest.mark.parametrize("timings", [None, {}, {"predicted_n": 200}, {"predicted_ms": 1000}])
def test_usage_pairs_with_request_wall_time_when_decode_timing_is_unavailable(benchmark, timings):
    call, saved, counters = benchmark
    response = call({"timings": timings, "usage": {"completion_tokens": 64}})
    assert response.status_code == 200
    assert response.json()["tokensPerSecond"] == 16
    assert response.json()["generatedTokens"] == 64
    assert response.json()["generateSeconds"] == 4
    assert response.json()["method"] == "request usage / wall time"
    assert saved.call_args.args[3] == 16
    counters.assert_not_awaited()


@pytest.mark.parametrize("payload", [
    {"choices": [{"message": {"content": "These words are not tokenizer counts"}}]},
    {"usage": {"completion_tokens": True}},
    {"usage": {"completion_tokens": 1.5}},
    {"timings": {"predicted_n": 64, "predicted_ms": -1}},
    {"usage": []},
    [],
])
def test_unmeasurable_completion_is_not_saved_as_performance(benchmark, payload):
    call, saved, _ = benchmark
    response = call(payload)
    assert response.status_code == 502
    assert "request token count" in response.json()["detail"]
    saved.assert_not_called()
