import json
from unittest.mock import AsyncMock, patch

import pytest
import security
from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_agent_client import AgentHTTPError, AgentUnavailable
from routers import pixel_integrations as api

AUTH = {'Authorization': 'Bearer test-key-12345'}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(security, 'DASHBOARD_API_KEY', 'test-key-12345')
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def agent():
    with patch.object(api, 'request_agent_json', new_callable=AsyncMock) as mock:
        yield mock


def test_requires_the_owner_key(client, agent):
    assert client.get('/api/pixel/integrations').status_code == 401
    agent.assert_not_called()


def test_get_and_actions_relay_exact_requests(client, agent):
    agent.return_value = {'settings': {'revision': 3}, 'approvals': [], 'activity': []}
    assert client.get('/api/pixel/integrations', headers=AUTH).json()['settings']['revision'] == 3
    agent.assert_awaited_with('GET', '/v1/pixel/integrations', payload=None, timeout=30)
    client.post('/api/pixel/integrations/discover', headers=AUTH, json={'serverId': 'home'})
    agent.assert_awaited_with('POST', '/v1/pixel/integrations/discover', payload={'serverId': 'home'}, timeout=140)
    client.post('/api/pixel/integrations/decide', headers=AUTH, json={'approvalId': 'abc', 'decision': 'approve'})
    agent.assert_awaited_with('POST', '/v1/pixel/integrations/decide', payload={'approvalId': 'abc', 'decision': 'approve'}, timeout=30)
    response = client.post('/api/pixel/integrations/save', headers=AUTH, json={'expectedRevision': 3, 'settings': {}})
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'


def test_unknown_actions_and_fields_are_refused(client, agent):
    assert client.post('/api/pixel/integrations/delete', headers=AUTH, json={}).status_code == 404
    assert client.post('/api/pixel/integrations/discover', headers=AUTH, json={'serverId': 'home', 'url': 'x'}).status_code == 400
    assert client.post('/api/pixel/integrations/decide', headers=AUTH, json={'approvalId': 'abc'}).status_code == 400
    assert client.post('/api/pixel/integrations/save', headers=AUTH, content=b'not json').status_code == 400
    assert client.post('/api/pixel/integrations/save', headers=AUTH, content=b'{"x":"' + b'a' * 70000 + b'"}').status_code == 413
    agent.assert_not_called()


def test_a_refusal_keeps_the_sentence_for_the_owner(client, agent):
    refusal = json.dumps({'error': 'stale-revision', 'message': 'These settings changed in another window.'})
    agent.side_effect = AgentHTTPError(409, 'conflict', refusal)
    response = client.post('/api/pixel/integrations/save', headers=AUTH, json={'expectedRevision': 1, 'settings': {}, 'secrets': {}})
    assert response.status_code == 409
    assert response.json() == {'detail': 'These settings changed in another window.', 'code': 'stale-revision'}


def test_an_absent_service_is_unavailable(client, agent):
    agent.side_effect = AgentHTTPError(503, 'unavailable', '{"error":"unavailable","message":"not running"}')
    assert client.get('/api/pixel/integrations', headers=AUTH).status_code == 503
    agent.side_effect = AgentUnavailable('down')
    assert client.get('/api/pixel/integrations', headers=AUTH).status_code == 503
