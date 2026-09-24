"""Configured trailing API slashes must not mark a reachable provider unhealthy."""
import httpx
import pytest


@pytest.mark.parametrize('api_path', ['/api/v1', '/api/v1/', '/api/v1///'])
def test_external_lemonade_status_normalizes_api_path(test_client, monkeypatch, api_path):
    import lemonade_client

    for key, value in {
        'GPU_BACKEND': 'amd', 'AMD_INFERENCE_RUNTIME': 'lemonade',
        'AMD_INFERENCE_BACKEND': 'vulkan', 'AMD_INFERENCE_LOCATION': 'host',
        'AMD_INFERENCE_RUNTIME_MODE': 'external-lemonade', 'AMD_INFERENCE_MANAGED': 'false',
        'AMD_INFERENCE_SUPPORTED_BACKENDS': 'vulkan', 'LLM_API_BASE_PATH': api_path,
        'LEMONADE_CONTAINER_BASE_URL': 'http://provider:13305',
    }.items():
        monkeypatch.setenv(key, value)
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == '/api/v1/health':
            return httpx.Response(200, json={'version': 'test', 'model_loaded': 'model-a'})
        if request.url.path == '/api/v1/models':
            return httpx.Response(200, json={'data': [{'id': 'model-a'}]})
        return httpx.Response(404, json={'error': 'wrong path'})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(lemonade_client.httpx, 'AsyncClient', lambda **kwargs: client)
    response = test_client.get('/api/gpu/amd-runtime', headers=test_client.auth_headers)
    assert response.status_code == 200
    assert response.json()['health'] == 'reachable'
    assert response.json()['modelCount'] == 1
    assert seen == ['/api/v1/health', '/api/v1/models']
    assert client.is_closed
