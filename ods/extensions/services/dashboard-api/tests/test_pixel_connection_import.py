"""Owner auth and strict credential-free proxy; upstream host is simulated here."""
import json
import time
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import pixel_providers as api

KEY = 'ods_infer_' + 'b' * 64


def fixture():
    bundle = {'schemaVersion': 1, 'kind': 'ods-inference-connection', 'label': 'Tower',
              'baseUrl': 'http://127.0.0.1:40345/v1', 'model': 'ods/shared',
              'deviceId': 'device-' + 'a' * 16, 'expiresAt': int(time.time()) + 3600,
              'expected': {'catalogId': 'glm', 'runtimeModelId': 'GLM'},
              'credential': {'apiKey': KEY}, 'execution': 'client-owned'}
    result = {'schemaVersion': 1, 'endpoint': bundle['baseUrl'], 'deviceId': bundle['deviceId'],
              'expiresAt': bundle['expiresAt'], 'expected': bundle['expected'], 'metadata': {
                  'catalogId': 'glm', 'routedModel': 'GLM', 'identitySource': 'ods-verified-route',
                  'routeSeq': 23, 'contextLength': 65536, 'maxOutputTokens': 4096,
                  'capabilities': {'chat': True, 'tools': False, 'vision': False, 'agentViable': True},
                  'expiresAt': bundle['expiresAt'], 'execution': 'client-owned'}}
    return {'bundle': json.dumps(bundle), 'confirmedEndpoint': bundle['baseUrl']}, result


@pytest.fixture
def owner(monkeypatch):
    import security
    monkeypatch.setattr(security, 'DASHBOARD_API_KEY', 'synthetic-owner-key')
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client, patch.object(api, 'request_agent_json', new_callable=AsyncMock) as host:
        yield client, host


def post(owner, **kwargs):
    return owner[0].post('/api/pixel/providers/connection-probe',
                         headers={'Authorization': 'Bearer synthetic-owner-key'}, **kwargs)


def test_owner_auth_before_host_or_validation(owner):
    assert owner[0].post('/api/pixel/providers/connection-probe', content='invalid').status_code == 401
    owner[1].assert_not_called()


def test_one_request_no_save_no_key_response_preserves_tools_false(owner):
    body, result = fixture()
    owner[1].return_value = result
    response = post(owner, json=body)
    assert response.status_code == 200
    assert response.json() == result
    assert response.headers['cache-control'] == 'no-store'
    assert KEY not in response.text
    owner[1].assert_awaited_once_with('POST', '/v1/pixel/providers/connection-probe', payload=body, timeout=25)


@pytest.mark.parametrize('change', [
    lambda body: body.update(credential=KEY), lambda body: body.update(bundle=1),
    lambda body: body.update(bundle='{"x":1,"x":2}'),
    lambda body: body.update(bundle='[' * 40 + '0' + ']' * 40),
    lambda body: body.update(bundle='x' * 32769), lambda body: body.update(confirmedEndpoint=[]),
])
def test_bad_requests_do_not_reach_host(owner, change):
    body, _ = fixture()
    change(body)
    response = post(owner, json=body)
    assert response.status_code == 400 and KEY not in response.text
    owner[1].assert_not_called()


def test_body_cap_before_host(owner):
    assert post(owner, content=b' ' * 65537).status_code == 413
    owner[1].assert_not_called()


@pytest.mark.parametrize('change', [
    lambda value: value.update(credential=KEY), lambda value: value['metadata'].update(secret=KEY),
    lambda value: value['metadata']['capabilities'].update(tools='true'),
    lambda value: value.update(endpoint='https://wrong.example/v1'),
    lambda value: value.update(deviceId='device-' + 'c' * 16),
    lambda value: value['expected'].update(runtimeModelId=KEY),
    lambda value: value['metadata'].update(maxOutputTokens=True),
])
def test_bad_host_results_are_not_forwarded(owner, change):
    body, value = fixture()
    value = deepcopy(value)
    change(value)
    owner[1].return_value = value
    response = post(owner, json=body)
    assert response.status_code == 502 and KEY not in response.text
    assert owner[1].await_count == 1


def test_host_error_not_echoed_or_retried(owner):
    from host_agent_client import AgentHTTPError
    owner[1].side_effect = AgentHTTPError(503, KEY, KEY)
    response = post(owner, json=fixture()[0])
    assert response.status_code == 503 and KEY not in response.text
    assert owner[1].await_count == 1
