import json
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_agent_client import AgentHTTPError, AgentTimeout
from routers import models


HEADERS = {'Authorization': 'Bearer test-key-12345'}
OBSERVED = {
    'status': 'verified', 'modelId': 'Qwen3.5-2B-Q4_K_M',
    'contextLength': 65536, 'backend': 'vulkan',
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(models, 'pixel_stream_active', lambda: False)
    app = FastAPI()
    app.include_router(models.router)
    with TestClient(app) as value:
        yield value


def test_external_observation_and_adoption_require_owner_auth(client, monkeypatch):
    call = Mock()
    monkeypatch.setattr(models, 'request_agent_json', call)
    assert client.get('/api/models/external-observation').status_code == 401
    assert client.post('/api/models/external-adopt', json={'model_id': OBSERVED['modelId']}).status_code == 401
    call.assert_not_called()


def test_external_observation_projects_only_verified_nonsecret_identity(client, monkeypatch):
    call = Mock(return_value={**OBSERVED, 'checkpoint': 'C:/private/model.gguf', 'secret': 'hidden'})
    monkeypatch.setattr(models, 'request_agent_json', call)
    result = client.get('/api/models/external-observation', headers=HEADERS)
    assert result.status_code == 200
    assert result.json() == OBSERVED
    assert result.headers['cache-control'] == 'no-store'
    call.assert_called_once_with('GET', '/v1/model/external-observation', timeout=20)


@pytest.mark.parametrize('value', [
    {**OBSERVED, 'status': 'guessed'},
    {**OBSERVED, 'contextLength': 0},
    {**OBSERVED, 'modelId': 'model\nsecret'},
])
def test_external_observation_rejects_unproved_agent_response(client, monkeypatch, value):
    monkeypatch.setattr(models, 'request_agent_json', Mock(return_value=value))
    assert client.get('/api/models/external-observation', headers=HEADERS).status_code == 503


@pytest.mark.parametrize('body', [None, {}, {'model_id': 'bad\nvalue'},
    {'model_id': OBSERVED['modelId'], 'checkpoint': '/private/file'}])
def test_external_adoption_accepts_only_exact_model_identity(client, monkeypatch, body):
    call = Mock()
    monkeypatch.setattr(models, 'request_agent_json', call)
    assert client.post('/api/models/external-adopt', headers=HEADERS, json=body).status_code == 400
    call.assert_not_called()


def test_external_adoption_submits_once_and_projects_receipt(client, monkeypatch):
    call = Mock(return_value={
        'status': 'adopted', 'modelId': OBSERVED['modelId'],
        'contextLength': 65536, 'modelTransactionId': 'a' * 64,
        'private': 'hidden',
    })
    monkeypatch.setattr(models, 'request_agent_json', call)
    result = client.post('/api/models/external-adopt', headers=HEADERS, json={'model_id': OBSERVED['modelId']})
    assert result.status_code == 200
    assert result.json() == {
        'status': 'adopted', 'modelId': OBSERVED['modelId'],
        'contextLength': 65536, 'modelTransactionId': 'a' * 64,
    }
    assert result.headers['cache-control'] == 'no-store'
    call.assert_called_once_with('POST', '/v1/model/external-adopt',
                                 payload={'model_id': OBSERVED['modelId']}, timeout=600)


@pytest.mark.parametrize('damage', [
    {'contextLength': 0}, {'modelTransactionId': 'unproved'}, {'modelId': 'other-model'},
])
def test_external_adoption_rejects_unproved_agent_receipt(client, monkeypatch, damage):
    value = {'status': 'adopted', 'modelId': OBSERVED['modelId'],
             'contextLength': 65536, 'modelTransactionId': 'a' * 64}
    value.update(damage)
    monkeypatch.setattr(models, 'request_agent_json', Mock(return_value=value))
    result = client.post('/api/models/external-adopt', headers=HEADERS, json={'model_id': OBSERVED['modelId']})
    assert result.status_code == 502


def test_external_adoption_preserves_pending_without_replay(client, monkeypatch):
    pending = {'error': 'repair required', 'code': 'managed_model_recovery_required',
               'pending': True, 'secret': 'hidden'}
    call = Mock(side_effect=AgentHTTPError(503, 'unconfirmed', json.dumps(pending)))
    monkeypatch.setattr(models, 'request_agent_json', call)
    result = client.post('/api/models/external-adopt', headers=HEADERS, json={'model_id': OBSERVED['modelId']})
    assert result.status_code == 503
    assert result.json()['detail'] == {
        'error': 'repair required', 'code': 'managed_model_recovery_required', 'pending': True,
    }
    assert call.call_count == 1


def test_external_adoption_timeout_is_not_retried(client, monkeypatch):
    call = Mock(side_effect=AgentTimeout('private detail'))
    monkeypatch.setattr(models, 'request_agent_json', call)
    result = client.post('/api/models/external-adopt', headers=HEADERS, json={'model_id': OBSERVED['modelId']})
    assert result.status_code == 503
    assert 'private detail' not in result.text
    assert call.call_count == 1


def test_external_adoption_refuses_active_pixel_stream(client, monkeypatch):
    call = Mock()
    monkeypatch.setattr(models, 'request_agent_json', call)
    monkeypatch.setattr(models, 'pixel_stream_active', lambda: True)
    result = client.post('/api/models/external-adopt', headers=HEADERS, json={'model_id': OBSERVED['modelId']})
    assert result.status_code == 409
    assert result.json()['detail']['code'] == 'pixel_chat_active'
    call.assert_not_called()
