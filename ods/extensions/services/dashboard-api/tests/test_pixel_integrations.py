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


def test_server_tests_restarts_and_clearing_activity_relay(client, agent):
    agent.return_value = {'settings': {'revision': 3}, 'approvals': [], 'activity': [], 'notice': 'Echo answered with 1 tools.'}
    assert client.post('/api/pixel/integrations/test', headers=AUTH, json={'serverId': 'echo'}).json()['notice'] == 'Echo answered with 1 tools.'
    agent.assert_awaited_with('POST', '/v1/pixel/integrations/test', payload={'serverId': 'echo'}, timeout=140)
    client.post('/api/pixel/integrations/restart', headers=AUTH, json={'serverId': 'echo'})
    agent.assert_awaited_with('POST', '/v1/pixel/integrations/restart', payload={'serverId': 'echo'}, timeout=30)
    client.post('/api/pixel/integrations/clear-activity', headers=AUTH, json={})
    agent.assert_awaited_with('POST', '/v1/pixel/integrations/clear-activity', payload={}, timeout=30)
    assert client.post('/api/pixel/integrations/restart', headers=AUTH, json={}).status_code == 400
    assert client.post('/api/pixel/integrations/clear-activity', headers=AUTH, json={'all': True}).status_code == 400


def test_app_tests_relay_with_the_app_name(client, agent):
    agent.return_value = {'settings': {'revision': 3}, 'approvals': [], 'activity': [], 'notice': 'FreshRSS answered: 2 feeds.'}
    reply = client.post('/api/pixel/integrations/test-app', headers=AUTH, json={'app': 'news'})
    assert reply.json()['notice'] == 'FreshRSS answered: 2 feeds.'
    agent.assert_awaited_with('POST', '/v1/pixel/integrations/test-app', payload={'app': 'news'}, timeout=140)
    assert client.post('/api/pixel/integrations/test-app', headers=AUTH, json={}).status_code == 400
    assert client.post('/api/pixel/integrations/test-app', headers=AUTH, json={'app': 'news', 'key': 'x'}).status_code == 400


def test_watch_list_and_send_digest_relay_without_fields(client, agent):
    agent.return_value = {'settings': {'revision': 3}, 'approvals': [], 'activity': [], 'watches': {'watches': [], 'total': 0}}
    assert client.post('/api/pixel/integrations/watch-list', headers=AUTH, json={}).json()['watches'] == {'watches': [], 'total': 0}
    agent.assert_awaited_with('POST', '/v1/pixel/integrations/watch-list', payload={}, timeout=140)
    agent.return_value = {'settings': {'revision': 3}, 'approvals': [], 'activity': [], 'notice': 'Digest sent with 2 stories.'}
    assert client.post('/api/pixel/integrations/send-digest', headers=AUTH, json={}).json()['notice'] == 'Digest sent with 2 stories.'
    agent.assert_awaited_with('POST', '/v1/pixel/integrations/send-digest', payload={}, timeout=140)
    for action in ('watch-list', 'send-digest'):
        assert client.post(f'/api/pixel/integrations/{action}', headers=AUTH, json={'app': 'news'}).status_code == 400


def test_macos_says_integrations_are_not_available_yet(client, agent):
    refusal = json.dumps({'error': 'unsupported', 'message': "Portal integrations aren't available on macOS yet."})
    agent.side_effect = AgentHTTPError(400, 'unsupported', refusal)
    response = client.get('/api/pixel/integrations', headers=AUTH)
    assert response.status_code == 400
    assert response.json()['detail'] == "Portal integrations aren't available on macOS yet."
