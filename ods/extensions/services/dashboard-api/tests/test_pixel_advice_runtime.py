from unittest.mock import AsyncMock,patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from routers import pixel_advice_runtime as api

ID='c2ac7cba-198d-4cb6-b4ca-722eb29d778f'
KEY={'Authorization':'Bearer test-key-12345'}

def state():
    return dict(status='missing',revision=0,runtimeId=None,sourceSha256='a'*64,host='Tower2',job=None,
        candidates=[dict(id='b'*64,path='/usr/bin/python3.12',version='3.12.3',canPrepare=True)])
def body():
    return dict(requestId=ID,expectedRevision=0,sourceSha256='a'*64,candidateId='b'*64,confirmed=True)
def job():
    return dict(jobId=ID,expectedRevision=0,runtimeId='runtime-'+ID.replace('-',''),candidateId='b'*64,status='running',error=None)

@pytest.fixture
def client(monkeypatch):
    import security
    monkeypatch.setattr(security,'DASHBOARD_API_KEY','test-key-12345')
    app=FastAPI(); app.include_router(api.router)
    with TestClient(app) as client: yield client

@pytest.fixture
def host():
    with patch.object(api,'request_agent_json',new_callable=AsyncMock) as mock:
        mock.return_value=state(); yield mock

def test_auth_before_host_and_no_cache(client,host):
    assert client.get('/api/pixel/advice-runtime').status_code==401; host.assert_not_called()
    result=client.get('/api/pixel/advice-runtime',headers=KEY)
    assert result.status_code==200 and result.json()==state() and result.headers['cache-control']=='no-store'

@pytest.mark.parametrize('extra',[dict(path='/tmp/evil'),dict(python='/tmp/evil'),dict(command=['evil']),
    dict(confirmed=False),dict(confirmed=1),dict(expectedRevision=True),dict(candidateId='../secret')])
def test_invalid_setup_never_reaches_host(client,host,extra):
    assert client.post('/api/pixel/advice-runtime/prepare',headers=KEY,json=dict(body(),**extra)).status_code==400
    host.assert_not_called()

def test_candidate_id_only_exact_job_bound_and_no_retry(client,host):
    host.return_value=job()
    result=client.post('/api/pixel/advice-runtime/prepare',headers=KEY,json=body())
    assert result.status_code==200 and result.json()==job()
    host.assert_awaited_once_with('POST','/v1/pixel/advice-runtime/prepare',payload=body(),timeout=10)

@pytest.mark.parametrize('extra',[dict(secret='private-sentinel'),dict(host=[]),dict(revision=True),
    dict(status='ready'),dict(candidates=[{'path':'private-sentinel'}])])
def test_invalid_host_projection_does_not_leak(client,host,extra):
    host.return_value=dict(state(),**extra)
    result=client.get('/api/pixel/advice-runtime',headers=KEY)
    assert result.status_code==502 and 'private-sentinel' not in result.text

@pytest.mark.parametrize('extra',[dict(jobId='bad'),dict(candidateId='c'*64),dict(expectedRevision=1),dict(error='secret')])
def test_wrong_job_response_rejected(client,host,extra):
    host.return_value=dict(job(),**extra)
    assert client.post('/api/pixel/advice-runtime/prepare',headers=KEY,json=body()).status_code==502

def test_strict_json_and_request_limit(client,host):
    assert client.post('/api/pixel/advice-runtime/status',headers=KEY,content='{"jobId":"x","jobId":"y"}').status_code==400
    assert client.post('/api/pixel/advice-runtime/status',headers=KEY,content=' '*8193).status_code==413
    host.assert_not_called()

def test_main_registers_runtime_router(test_client):
    assert test_client.get('/api/pixel/advice-runtime').status_code==401
