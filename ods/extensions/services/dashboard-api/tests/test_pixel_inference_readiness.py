"""Pixel discovery must not advertise a stopped host model server as ready."""
import json
from unittest.mock import patch

import pytest
from test_pixel import FakeClient, FakeResponse, pixel, pixel_env


@pytest.fixture(autouse=True)
def host_runtime(monkeypatch):
    monkeypatch.setattr(pixel, 'read_live_env_value', lambda key: {
        'LLM_BACKEND': 'lemonade', 'AMD_INFERENCE_LOCATION': 'host',
    }.get(key, ''))


@pytest.mark.asyncio
@pytest.mark.parametrize('telemetry', [None, {}, {'health': {'status': 'ok'}},
    {'schema_version': 'ods.host-llm-status.v1', 'health': {'status': 'error'}}])
async def test_agent_catalog_does_not_mask_unavailable_inference(monkeypatch, telemetry):
    calls = []
    async def host(method, path, **kwargs):
        calls.append((method, path))
        return telemetry if path == '/v1/llm/status' else {'status': 'idle'}
    monkeypatch.setattr(pixel, 'request_agent_json', host)
    upstream = FakeResponse(chunks=[json.dumps({'data':[{'id':'pixel/default'}]}).encode()])
    with patch.object(pixel.httpx, 'AsyncClient', return_value=FakeClient(upstream)):
        result = await pixel.pixel_status()
    assert result['available'] is False
    assert result['state'] == 'model_unavailable'
    assert ('GET', '/v1/llm/status') in calls


@pytest.mark.asyncio
async def test_live_health_can_recover_without_generating_tokens(monkeypatch):
    async def host(method, path, **kwargs):
        assert method == 'GET' and path == '/v1/llm/status'
        return {'schema_version':'ods.host-llm-status.v1','health':{'status':'ok'}}
    monkeypatch.setattr(pixel, 'request_agent_json', host)
    assert await pixel._local_inference_issue(None) is None


@pytest.mark.asyncio
async def test_transport_failure_is_sanitized(monkeypatch):
    async def host(*args, **kwargs):
        raise pixel.AgentClientError('private-host-error')
    monkeypatch.setattr(pixel, 'request_agent_json', host)
    issue = await pixel._local_inference_issue(None)
    assert 'unavailable' in issue
    assert 'private-host-error' not in issue


@pytest.mark.asyncio
async def test_remote_model_does_not_depend_on_local_server(monkeypatch):
    async def host(*args, **kwargs):
        pytest.fail('Must not probe an unused local model')
    monkeypatch.setattr(pixel, 'request_agent_json', host)
    runtime={'source':'remote-provider','model':'remote','contextLength':8192,'maxTokens':4096,'reasoning':False}
    assert await pixel._local_inference_issue({'activeRuntime':runtime}) is None
