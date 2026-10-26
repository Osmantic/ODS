"""Authenticated task proxy: exact contracts and no replay after uncertainty."""
import copy
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from host_agent_client import AgentHTTPError, AgentTimeout
from routers import pixel_schedules as api

PAGE = {'kind': 'tasks', 'items': [], 'offset': 0, 'hasMore': False, 'nextOffset': None,
        'pendingCreates': [], 'pendingCount': 0, 'pendingRemovals': []}
HEADERS = {'Authorization': 'Bearer test-key-12345'}


@pytest.fixture
def route(monkeypatch):
    import security
    monkeypatch.setattr(security, 'DASHBOARD_API_KEY', 'test-key-12345')
    call = AsyncMock(return_value=copy.deepcopy(PAGE))
    monkeypatch.setattr(api, 'request_agent_json', call)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        yield client, call


def test_owner_auth_precedes_parsing_or_dispatch(route):
    client, call = route
    response = client.post('/api/pixel/schedules', content='not json')
    assert response.status_code == 401
    call.assert_not_called()


def test_current_list_uses_only_the_fixed_host_route_and_disables_caching(route):
    client, call = route
    response = client.post('/api/pixel/schedules', headers=HEADERS, json={'action': 'list', 'offset': 0})
    assert response.status_code == 200 and response.json() == PAGE
    assert response.headers['cache-control'] == 'no-store'
    call.assert_awaited_once_with('POST', '/v1/pixel/schedules', payload={'action': 'list', 'offset': 0}, timeout=200)


@pytest.mark.parametrize('body', [
    '{"action":"list","action":"create","offset":0}', '{"action":"list","offset":NaN}',
    json.dumps({'action': 'list', 'offset': 0, 'packageDir': '/tmp/other'}),
    json.dumps({'action': 'list', 'offset': True}), '[1]', '[]',
])
def test_invalid_authority_or_ambiguous_json_never_reaches_host(route, body):
    client, call = route
    response = client.post('/api/pixel/schedules', headers={**HEADERS, 'Content-Type': 'application/json'}, content=body)
    assert response.status_code == 400
    call.assert_not_called()


def test_size_limit_and_query_are_refused(route):
    client, call = route
    assert client.post('/api/pixel/schedules', headers={**HEADERS, 'Content-Type': 'application/json'}, content=' ' * 16385).status_code == 413
    assert client.post('/api/pixel/schedules?owner=other', headers=HEADERS, json={'action': 'list', 'offset': 0}).status_code == 400
    call.assert_not_called()


@pytest.mark.parametrize('reply', [dict(PAGE, token='private'), dict(PAGE, offset=1), {'kind': 'change', 'outcome': 'unconfirmed', 'task': None}])
def test_private_or_crossed_reply_is_never_a_success(route, reply):
    client, call = route
    call.return_value = reply
    response = client.post('/api/pixel/schedules', headers=HEADERS, json={'action': 'list', 'offset': 0})
    assert response.status_code == 503
    assert 'private' not in response.text


def test_timeout_after_create_is_not_retried(route):
    client, call = route
    call.side_effect = AgentTimeout('private diagnostic')
    proposal = {'action': 'create', 'requestId': 'd971df9f-c297-4cb0-96b1-18e2488ea69c', 'name': 'Briefing',
                'task': 'Summarize my day.', 'schedule': {'kind': 'at', 'at': '2030-01-01T09:00:00Z'}}
    response = client.post('/api/pixel/schedules', headers=HEADERS, json=proposal)
    assert response.status_code == 503
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['detail']['code'] == 'scheduler-unavailable'
    assert call.await_count == 1


def test_old_host_agent_has_actionable_update_status_without_raw_diagnostics(route):
    client, call = route
    call.side_effect = AgentHTTPError(404, 'private diagnostic', 'private path')
    response = client.post('/api/pixel/schedules', headers=HEADERS, json={'action': 'list', 'offset': 0})
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'scheduler-update-required'
