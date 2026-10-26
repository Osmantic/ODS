"""Pixel discovery must not advertise a stopped host model server as ready."""
import asyncio
import json
from unittest.mock import patch

import pytest
from test_pixel import FakeClient, FakeResponse, pixel, pixel_env as pixel_env

pytestmark = pytest.mark.usefixtures("mock_edge_read_transport")


@pytest.fixture(autouse=True, params=['llama-server', 'lemonade'])
def host_runtime(monkeypatch, request):
    # A llama-server on the Windows host; an unmigrated .env still names Lemonade.
    monkeypatch.setattr(pixel, 'read_live_env_value', lambda key: {
        'LLM_BACKEND': request.param, 'AMD_INFERENCE_LOCATION': 'host',
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
    upstream = FakeResponse(chunks=[json.dumps({'data':[{'id':'portal/default'}]}).encode()])
    with patch.object(pixel.httpx, 'AsyncClient', return_value=FakeClient(upstream)):
        result = await pixel.pixel_status()
    assert result['available'] is False
    assert result['state'] == 'model_unavailable'
    assert ('GET', '/v1/llm/status') in calls


@pytest.mark.asyncio
@pytest.mark.parametrize(('management', 'recovery'), [
    # Exactly the proof for which Models offers Resume model.
    ({'managed': True, 'canActivate': False, 'canUnload': True, 'running': False, 'vision': False}, 'resume-model'),
    ({'managed': True, 'canActivate': True, 'canUnload': True, 'running': True, 'vision': False}, None),
    ({'managed': False, 'canActivate': False, 'canUnload': False, 'running': False, 'vision': False}, None),
    (pixel.AgentHTTPError(503, 'Windows runtime management could not be verified'), None),
], ids=['stopped', 'running', 'unmanaged', 'unverified'])
async def test_a_stopped_owned_runtime_offers_resume_model_in_portal(monkeypatch, management, recovery):
    async def host(method, path, **kwargs):
        if path == '/v1/model/management':
            if isinstance(management, BaseException):
                raise management
            return management
        return {'schema_version': 'ods.host-llm-status.v1', 'health': {'status': 'error'}} if path == '/v1/llm/status' else {'status': 'idle'}
    monkeypatch.setattr(pixel, 'request_agent_json', host)
    upstream = FakeResponse(chunks=[json.dumps({'data':[{'id':'portal/default'}]}).encode()])
    with patch.object(pixel.httpx, 'AsyncClient', return_value=FakeClient(upstream)):
        result = await pixel.pixel_status()
    assert (result['available'], result['state']) == (False, 'model_unavailable')
    assert result.get('recovery') == recovery
    assert result['detail'] == (pixel._RUNTIME_STOPPED_DETAIL if recovery else
                                'The local model runtime is unavailable. Restore it in Models before sending another task.')


@pytest.mark.asyncio
async def test_a_slow_runtime_proof_cannot_hold_the_status_read(monkeypatch):
    # The hint comes after the edge and telemetry reads, inside the page's deadline.
    async def host(method, path, **kwargs):
        if path == '/v1/model/management':
            await asyncio.sleep(5)
            return {'managed': True, 'canActivate': False, 'canUnload': True, 'running': False}
        return {'schema_version': 'ods.host-llm-status.v1', 'health': {'status': 'error'}} if path == '/v1/llm/status' else {'status': 'idle'}
    monkeypatch.setattr(pixel, 'request_agent_json', host)
    monkeypatch.setattr(pixel, '_RUNTIME_HINT_SECONDS', 0.05)
    upstream = FakeResponse(chunks=[json.dumps({'data':[{'id':'portal/default'}]}).encode()])
    with patch.object(pixel.httpx, 'AsyncClient', return_value=FakeClient(upstream)):
        result = await asyncio.wait_for(pixel.pixel_status(), timeout=2)
    assert (result['state'], result.get('recovery')) == ('model_unavailable', None)


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


@pytest.mark.asyncio
@pytest.mark.parametrize('code,available', [(501, True), (503, False)])
async def test_unsupported_host_telemetry_does_not_disable_discoverable_agent(monkeypatch, code, available):
    async def host(method, path, **kwargs):
        if path == '/v1/llm/status':
            raise pixel.AgentHTTPError(code, 'private-diagnostic')
        return {'status': 'idle'}
    monkeypatch.setattr(pixel, 'request_agent_json', host)
    upstream = FakeResponse(chunks=[json.dumps({'data': [{'id': 'portal/default'}]}).encode()])
    with patch.object(pixel.httpx, 'AsyncClient', return_value=FakeClient(upstream)):
        result = await pixel.pixel_status()
    assert result['available'] is available
    assert 'private-diagnostic' not in json.dumps(result)
    if not available:
        assert result['state'] == 'model_unavailable'
