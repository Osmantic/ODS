"""Local OpenAI-compatible URLs retain local cost provenance at ingest."""
import asyncio
import importlib.util
import json

import httpx
import pytest
from test_token_spy_callback import CALLBACK_PATH, load_callback


@pytest.mark.parametrize("base,provider", [
    ("http://[::1]:8080/v1", "local"),
    ("http://[0:0:0:0:0:0:0:1]:8080/v1", "local"),
    ("http://127.0.0.2:8080/v1", "local"),
    ("http://[::ffff:127.0.0.1]:8080/v1", "local"),
    ("http://127.0.0.1:8080/v1", "local"),
    ("http://localhost:8080/v1", "local"),
    ("https://api.openai.com/v1", "openai"),
    ("http://192.168.1.9:8080/v1", "openai"),
    ("https://localhost.example.com/v1", "openai"),
])
def test_callback_transport_preserves_loopback_cost_provenance(monkeypatch, base, provider):
    monkeypatch.setenv("TOKEN_SPY_URL", "http://token-spy:8080")
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "fixture-key")
    monkeypatch.setenv("ODS_MODEL_SWITCHBOARD", "observe")
    callback = load_callback(monkeypatch)
    spec = importlib.util.spec_from_file_location("loopback_routed", CALLBACK_PATH.parent.parent / "token-spy/routed_telemetry.py")
    routed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(routed)
    captured = []

    def ingest(request):
        assert request.url.path == "/api/ingest/routed"
        event = routed.validate_routed_event(json.loads(request.content))
        captured.append(routed.routed_event_to_usage(event))
        return httpx.Response(202)

    client_type = httpx.AsyncClient
    monkeypatch.setattr(callback.httpx, "AsyncClient", lambda **kwargs: client_type(transport=httpx.MockTransport(ingest), **kwargs))
    instance = callback.ODSTokenSpyCallback()

    async def scenario():
        try:
            await instance.async_log_success_event(
                {"litellm_params":{"api_base":base,"custom_llm_provider":"openai"}},
                {"model":"fixture", "usage":{"prompt_tokens":10,"completion_tokens":3}}, 1, 2,
            )
            await asyncio.wait_for(instance.queue.join(), 2)
        finally:
            instance.worker.cancel()
            with pytest.raises(asyncio.CancelledError):
                await instance.worker

    asyncio.run(scenario())
    assert len(captured) == 1
    assert captured[0]["provider_name"] == provider
    assert captured[0]["cost_source"] == ("local_zero_cost" if provider == "local" else "untracked")
    assert captured[0]["input_tokens"] + captured[0]["output_tokens"] == 13
