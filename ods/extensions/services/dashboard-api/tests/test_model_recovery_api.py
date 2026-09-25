import json
from unittest.mock import Mock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers import models
from host_agent_client import AgentHTTPError, AgentTimeout

PENDING={'pending':True,'phase':'applied','transactionId':'a'*64}
DONE={'pending':False,'phase':'completed','transactionId':'a'*64,'outcome':'commit'}


@pytest.fixture
def client():
    app=FastAPI()
    app.include_router(models.router)
    with TestClient(app) as value:
        yield value


def test_recovery_requires_owner_auth_before_calling_host(client,monkeypatch):
    call=Mock()
    monkeypatch.setattr(models,'request_agent_json',call)
    assert client.get('/api/models/recovery').status_code==401
    assert client.post('/api/models/recovery',json={}).status_code==401
    call.assert_not_called()


def test_status_is_read_only_and_private_fields_are_not_projected(client,monkeypatch):
    call=Mock(return_value={**PENDING,'journal':{'secret':'private'}})
    monkeypatch.setattr(models,'request_agent_json',call)
    result=client.get('/api/models/recovery',headers={'Authorization':'Bearer test-key-12345'})
    assert result.status_code==200
    assert result.json()==PENDING
    call.assert_called_once_with('GET','/v1/model/recovery',payload=None,timeout=5)


def test_recovery_accepts_no_selection_or_paths_and_submits_once(client,monkeypatch):
    call=Mock(return_value=DONE)
    monkeypatch.setattr(models,'request_agent_json',call)
    headers={'Authorization':'Bearer test-key-12345'}
    for body in ({'model':'other'},{'transactionId':'b'*64},{'path':'/etc'},None):
        assert client.post('/api/models/recovery',headers=headers,json=body).status_code==400
    call.assert_not_called()
    result=client.post('/api/models/recovery',headers=headers,json={})
    assert result.status_code==200 and result.json()==DONE
    call.assert_called_once_with('POST','/v1/model/recover',payload={},timeout=400)


def test_unproved_recovery_remains_pending_without_replay(client,monkeypatch):
    payload={**PENDING,'reason':'model-recovery-proof-required','private':'hidden'}
    call=Mock(side_effect=AgentHTTPError(409,'unconfirmed',json.dumps(payload)))
    monkeypatch.setattr(models,'request_agent_json',call)
    result=client.post('/api/models/recovery',headers={'Authorization':'Bearer test-key-12345'},json={})
    assert result.status_code==409
    assert result.json()=={**PENDING,'reason':'model-recovery-proof-required'}
    assert call.call_count==1


@pytest.mark.parametrize('value',[{'pending':False,'phase':'applied','transactionId':'a'*64},
    {'pending':True,'phase':'held','transactionId':None},{**PENDING,'transactionId':'a'*64+'\n'}])
def test_inconsistent_receipts_are_never_success(value,client,monkeypatch):
    monkeypatch.setattr(models,'request_agent_json',Mock(return_value=value))
    assert client.get('/api/models/recovery',headers={'Authorization':'Bearer test-key-12345'}).status_code==503


def test_timeout_is_unconfirmed_not_retried(client,monkeypatch):
    call=Mock(side_effect=AgentTimeout('private detail'))
    monkeypatch.setattr(models,'request_agent_json',call)
    result=client.post('/api/models/recovery',headers={'Authorization':'Bearer test-key-12345'},json={})
    assert result.status_code==503 and 'private detail' not in result.text
    assert call.call_count==1
