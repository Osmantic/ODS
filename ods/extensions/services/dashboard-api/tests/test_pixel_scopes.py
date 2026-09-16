import uuid
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from routers import pixel_scopes as api
from host_agent_client import AgentHTTPError

KEY = {'Authorization': 'Bearer test-key-12345'}


def result():
    return dict(schemaVersion=1, revision=0, chatId='Chat_A', taskId=None, taskSelection=None,
        conversationSelection=None, defaultSnapshot=None, defaultSelection=None,
        effectiveScope=None, effectiveSelection=None, runtimeStatus='preference-only', checkpointApproval='required-each-handoff-run')


@pytest.fixture
def client(monkeypatch):
    import security
    monkeypatch.setattr(security, 'DASHBOARD_API_KEY', 'test-key-12345')
    app = FastAPI(); app.include_router(api.router)
    with TestClient(app) as client: yield client


@pytest.fixture
def host():
    with patch.object(api, 'request_agent_json', new_callable=AsyncMock) as mock:
        mock.return_value = result()
        yield mock


@pytest.mark.parametrize('action', list(api.FIELDS))
def test_owner_required_before_forwarding(client, host, action):
    assert client.post('/api/pixel/provider-scopes/'+action, json={}).status_code == 401
    host.assert_not_called()


def test_status_projection_and_host_path(client, host):
    response = client.post('/api/pixel/provider-scopes/status', json={'chatId': 'Chat_A'}, headers=KEY)
    assert response.status_code == 200 and response.json() == result()
    assert response.headers['cache-control'] == 'no-store'
    assert host.call_args.args == ('POST', '/v1/pixel/provider-scopes/status')


@pytest.mark.parametrize('field,value', [('secret', 'do-not-reflect'), ('runtimeStatus', 'active'),
    ('chatId', 'other'), ('effectiveScope', 'task'), ('revision', True)])
def test_invalid_host_projection_rejected(client, host, field, value):
    host.return_value[field] = value
    response = client.post('/api/pixel/provider-scopes/status', json={'chatId': 'Chat_A'}, headers=KEY)
    assert response.status_code == 502 and 'do-not-reflect' not in response.text


def test_preserves_exact_case_and_task_identity(client, host):
    task = str(uuid.uuid4()); host.return_value.update(taskId=task, revision=1)
    body = dict(chatId='Chat_A', taskId=task, expectedRevision=0)
    assert client.post('/api/pixel/provider-scopes/begin', json=body, headers=KEY).status_code == 200
    assert host.call_args.kwargs['payload'] == body


@pytest.mark.parametrize('action,body', [('status', {'chatId': '../secret'}),
    ('begin', {'chatId': 'Chat_A', 'taskId': None, 'expectedRevision': 0}),
    ('return', {'chatId': 'Chat_A', 'taskId': None, 'expectedRevision': True, 'scope': 'default'}),
    ('select', {'chatId': 'Chat_A', 'command': ['bad']})])
def test_invalid_owner_request_not_forwarded(client, host, action, body):
    assert client.post('/api/pixel/provider-scopes/'+action, json=body, headers=KEY).status_code == 400
    host.assert_not_called()


def test_duplicate_keys_and_oversize(client, host):
    for raw, status in [(' {"chatId":"a","chatId":"b"}', 400), (' '*4097, 413)]:
        assert client.post('/api/pixel/provider-scopes/status', content=raw, headers=KEY).status_code == status
    host.assert_not_called()


def test_ambiguous_write_is_never_automatically_retried(client, host):
    host.side_effect = AgentHTTPError(409, 'private detail')
    body = dict(chatId='Chat_A', taskId=str(uuid.uuid4()), expectedRevision=0)
    response = client.post('/api/pixel/provider-scopes/begin', json=body, headers=KEY)
    assert response.status_code == 409 and 'private detail' not in response.text
    assert host.call_count == 1
