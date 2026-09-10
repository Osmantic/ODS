import hashlib
import json
import uuid
from unittest.mock import AsyncMock,patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from routers import pixel_handoff as api
from host_agent_client import AgentHTTPError

KEY={'Authorization':'Bearer test-key-12345'}
ID=str(uuid.uuid4())


def result(status='pending'):
    recipient=dict(id='stronger',label='Stronger',kind='local',baseUrl='http://127.0.0.1:8090/v1',model='fixture',
        revision=1,scope='run',previousProviderId='leader')
    checkpoint=dict(schemaVersion=1,runId=ID,sessionId='session',agentId='pixel',workspaceDir='/tmp/workspace',
        recipient=recipient,dataScope='conversation-and-this-run-tool-results',returnAction='configured-leader-on-next-run',
        prompt='Continue',systemPrompt='System',messages=[])
    raw=json.dumps(checkpoint)
    return dict(runId=ID,checkpointDigest=hashlib.sha256(raw.encode()).hexdigest(),checkpointBytes=len(raw.encode()),
        recipient=recipient,expiresAt=1800000000,status=status,checkpointJson=raw)


def decision():
    return dict(runId=ID,checkpointDigest=result()['checkpointDigest'],approved=True,allowCloud=False,acceptUnknownCost=False)


@pytest.fixture
def client(monkeypatch):
    import security
    monkeypatch.setattr(security,'DASHBOARD_API_KEY','test-key-12345')
    app=FastAPI(); app.include_router(api.router)
    with TestClient(app) as client: yield client


@pytest.fixture
def host():
    with patch.object(api,'request_agent_json',new_callable=AsyncMock) as mock:
        mock.return_value=result()
        yield mock


@pytest.mark.parametrize('action',['list','status','decide'])
def test_every_action_requires_owner(client,host,action):
    assert client.post('/api/pixel/handoff/'+action,json={}).status_code==401
    host.assert_not_called()


def test_exact_checkpoint_forwarded_without_rewrite(client,host):
    response=client.post('/api/pixel/handoff/status',headers=KEY,json={'runId':ID})
    assert response.status_code==200 and response.json()==result()
    assert response.headers['cache-control']=='no-store'
    host.assert_awaited_once_with('POST','/v1/pixel/handoff/status',payload={'runId':ID},timeout=10)


def test_decision_exact_once(client,host):
    host.return_value=result('approved')
    assert client.post('/api/pixel/handoff/decide',headers=KEY,json=decision()).status_code==200
    host.assert_awaited_once_with('POST','/v1/pixel/handoff/decide',payload=decision(),timeout=10)


def test_list_never_contains_checkpoint(client,host):
    metadata=result(); metadata.pop('checkpointJson')
    host.return_value=dict(items=[metadata],unavailableCount=0)
    assert client.post('/api/pixel/handoff/list',headers=KEY,json={}).json()==host.return_value
    host.return_value=dict(items=[result()],unavailableCount=0)
    assert client.post('/api/pixel/handoff/list',headers=KEY,json={}).status_code==502


@pytest.mark.parametrize('change',[dict(secret='private-sentinel'),dict(checkpointDigest='b'*64),dict(checkpointBytes=2),
    dict(status='executed'),dict(expiresAt=True),dict(runId=str(uuid.uuid4())),dict(checkpointJson='private-sentinel')])
def test_corrupt_response_is_not_leaked(client,host,change):
    host.return_value=dict(result(),**change)
    response=client.post('/api/pixel/handoff/status',headers=KEY,json={'runId':ID})
    assert response.status_code==502 and 'private-sentinel' not in response.text


@pytest.mark.parametrize('change',[dict(approved=1),dict(runId='../bad'),dict(checkpointDigest='invalid'),dict(command=['evil'])])
def test_invalid_decision_does_not_reach_host(client,host,change):
    assert client.post('/api/pixel/handoff/decide',headers=KEY,json=dict(decision(),**change)).status_code==400
    host.assert_not_called()


def test_framing_and_no_publication(client,host):
    assert client.post('/api/pixel/handoff/list',headers=KEY,content='{"x":1,"x":2}').status_code==400
    assert client.post('/api/pixel/handoff/list',headers=KEY,content=' '*4097).status_code==413
    assert client.post('/api/pixel/handoff/publish',headers=KEY,json={}).status_code==404
    host.assert_not_called()


def test_ambiguous_write_is_not_replayed(client,host):
    host.side_effect=AgentHTTPError(500,'private-sentinel')
    response=client.post('/api/pixel/handoff/decide',headers=KEY,json=decision())
    assert response.status_code==502 and 'private-sentinel' not in response.text
    assert host.call_count==1


def test_main_registers_owner_route(test_client):
    assert test_client.post('/api/pixel/handoff/list',json={}).status_code==401
