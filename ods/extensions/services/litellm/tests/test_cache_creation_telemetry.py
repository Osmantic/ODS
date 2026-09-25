"""Successful callback events retain the pinned LiteLLM cache-write counter."""
import asyncio
import importlib.metadata
import json
import os

import httpx
import pytest

from test_token_spy_callback import load_callback


def send_event(monkeypatch, response):
    monkeypatch.setenv('TOKEN_SPY_URL', 'http://token-spy:8080')
    monkeypatch.setenv('TOKEN_SPY_API_KEY', 'telemetry-fixture')
    monkeypatch.setenv('ODS_MODEL_SWITCHBOARD', 'observe')
    callback = load_callback(monkeypatch)
    captured = []

    def receive(request):
        assert request.url.path == '/api/ingest/routed'
        assert request.headers['Authorization'] == 'Bearer telemetry-fixture'
        captured.append(json.loads(request.content))
        return httpx.Response(202)

    client_type = httpx.AsyncClient
    monkeypatch.setattr(callback.httpx, 'AsyncClient', lambda **kwargs: client_type(transport=httpx.MockTransport(receive), **kwargs))
    instance = callback.ODSTokenSpyCallback()

    async def scenario():
        await instance.async_log_success_event(
            {'messages': [{'role': 'user', 'content': 'private prompt'}]},
            response, 1, 2,
        )
        await asyncio.wait_for(instance.queue.join(), timeout=2)
        instance.worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await instance.worker

    asyncio.run(scenario())
    assert len(captured) == 1
    assert 'private prompt' not in json.dumps(captured)
    assert 'private answer' not in json.dumps(captured)
    return captured[0]


@pytest.mark.parametrize('details, legacy, expected', [
    ({'cache_creation_tokens': 4096}, 0, 4096),
    ({'cache_creation_tokens': 0}, 17, 0),
    ({}, 17, 17),
    ({'cache_creation_tokens': -1}, 0, 0),
    ({'cache_creation_tokens': True}, 0, 0),
    ({'cache_creation_tokens': 3_000_000_000}, 0, 4872),
])
def test_outgoing_ingest_uses_cache_creation_detail_without_changing_totals(monkeypatch, details, legacy, expected):
    event = send_event(monkeypatch, {
        'choices': [{'message': {'content': 'private answer'}}],
        'usage': {'prompt_tokens': 5000, 'completion_tokens': 42,
                  'prompt_tokens_details': {'cached_tokens': 128, **details},
                  'cache_write_tokens': legacy},
    })
    assert event['cache_write_tokens'] == expected
    assert event['cache_read_tokens'] == 128
    assert event['input_tokens'] == 5000 - 128 - expected
    assert sum(event[key] for key in ('input_tokens', 'cache_read_tokens', 'cache_write_tokens')) == 5000
    assert event['output_tokens'] == 42


@pytest.mark.skipif(os.environ.get('ODS_TEST_LITELLM_PIN') != '1', reason='Opt-in exact LiteLLM dependency contract')
def test_real_litellm_1907_usage_object_reaches_ingest(monkeypatch):
    assert importlib.metadata.version('litellm') == '1.90.7'
    from litellm import ModelResponse, Usage

    response = ModelResponse(model='fixture', usage=Usage(
        prompt_tokens=5000, completion_tokens=42,
        cache_creation_input_tokens=4096, cache_read_input_tokens=128,
    ))
    assert response.model_dump()['usage']['prompt_tokens_details']['cache_creation_tokens'] == 4096
    event = send_event(monkeypatch, response)
    assert event['cache_write_tokens'] == 4096
    assert event['cache_read_tokens'] == 128
