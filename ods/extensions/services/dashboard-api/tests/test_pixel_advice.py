import copy
import hashlib
import uuid
from unittest.mock import AsyncMock,patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from routers import pixel_advice as api
from host_agent_client import AgentHTTPError

KEY={'Authorization':'Bearer test-key-12345'}
ID=str(uuid.uuid4())


def body():
    return dict(requestId=ID,expectedRevision=1,providerId='advisor',capsule='Only this capsule',
        allowCloud=False,acceptUnknownCost=False,maxOutputTokens=512,deadlineSeconds=10)


def result():
    return dict(jobId=ID,revision=1,providerId='advisor',providerLabel='My advisor',model='glm',kind='ods-peer',
        capsuleSha256=hashlib.sha256(body()['capsule'].encode()).hexdigest(),maxOutputTokens=512,deadlineSeconds=10,scope='reviewed-capsule-only',
        leaderChanged=False,toolsAllowed=False,costStatus='unknown',status='completed',error=None,
        result=dict(text='Untrusted advice',usage=dict(prompt_tokens=None,completion_tokens=None,total_tokens=None),
            costStatus='unknown',trusted=False))


@pytest.fixture
def client(monkeypatch):
    import security
    monkeypatch.setattr(security,'DASHBOARD_API_KEY','test-key-12345')
    app=FastAPI(); app.include_router(api.router)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def host():
    with patch.object(api,'request_agent_json',new_callable=AsyncMock) as mock:
        mock.return_value=result()
        yield mock


@pytest.mark.parametrize('action',['start','status','cancel'])
def test_every_action_requires_owner(client,host,action):
    assert client.post('/api/pixel/advice/'+action,json=body()).status_code==401
    host.assert_not_called()


def test_explicit_capsule_forwarded_once_and_response_not_cached(client,host):
    response=client.post('/api/pixel/advice/start',headers=KEY,json=body())
    assert response.status_code==200 and response.json()==result()
    assert response.headers['cache-control']=='no-store'
    host.assert_awaited_once_with('POST','/v1/pixel/advice/start',payload=body(),timeout=10)


@pytest.mark.parametrize('change',[dict(expectedRevision=True),dict(capsule='x'*16385),dict(allowCloud=1),
    dict(history=[]),dict(maxOutputTokens=0),dict(requestId='../secret')])
def test_bad_request_never_reaches_host(client,host,change):
    assert client.post('/api/pixel/advice/start',headers=KEY,json=dict(body(),**change)).status_code==400
    host.assert_not_called()


def test_duplicate_keys_and_large_body(client,host):
    response=client.post('/api/pixel/advice/status',headers=KEY,content='{"jobId":"a","jobId":"b"}')
    assert response.status_code==400
    assert client.post('/api/pixel/advice/status',headers=KEY,content=' '*131073).status_code==413
    host.assert_not_called()


@pytest.mark.parametrize('change',[dict(secret='private-sentinel'),dict(toolsAllowed=True),dict(leaderChanged=True),
    dict(jobId=str(uuid.uuid4())),dict(result={'text':'private-sentinel','trusted':True}),dict(error='advice-failed'),
    dict(providerId='Other'),dict(providerId='different'),dict(kind='cloud'),dict(revision=2),dict(capsuleSha256='b'*64)])
def test_bad_response_is_rejected_without_leak(client,host,change):
    host.return_value=dict(result(),**change)
    response=client.post('/api/pixel/advice/start',headers=KEY,json=body())
    assert response.status_code==502 and 'private-sentinel' not in response.text


@pytest.mark.parametrize('trusted',[None,0,'',True])
def test_only_explicit_untrusted_false_is_accepted(client,host,trusted):
    host.return_value=result(); host.return_value['result']['trusted']=trusted
    assert client.post('/api/pixel/advice/start',headers=KEY,json=body()).status_code==502


def test_ambiguous_host_error_is_not_replayed(client,host):
    host.side_effect=AgentHTTPError(500,'private-sentinel')
    response=client.post('/api/pixel/advice/start',headers=KEY,json=body())
    assert response.status_code==502 and 'private-sentinel' not in response.text
    assert host.call_count==1


def test_main_registers_advice(test_client):
    assert test_client.post('/api/pixel/advice/status',json={'jobId':ID}).status_code==401
