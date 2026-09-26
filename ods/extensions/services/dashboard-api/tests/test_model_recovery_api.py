import importlib.util
import json
import re
from pathlib import Path
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
    call.assert_called_once_with('POST','/v1/model/recover',payload={},timeout=1020)


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


RESTORE={'model':'qwen3-coder-next-Q4_K_M.gguf','contextLength':131072}


def test_restore_offer_is_projected_only_when_exact_and_pending(client,monkeypatch):
    headers={'Authorization':'Bearer test-key-12345'}
    monkeypatch.setattr(models,'request_agent_json',Mock(return_value={**PENDING,'restore':{**RESTORE}}))
    assert client.get('/api/models/recovery',headers=headers).json()=={**PENDING,'restore':RESTORE}
    for restore in ({**RESTORE,'path':'/models/x'},{**RESTORE,'model':'x\n'},{**RESTORE,'contextLength':True},
                    {**RESTORE,'contextLength':2048},'qwen'):
        monkeypatch.setattr(models,'request_agent_json',Mock(return_value={**PENDING,'restore':restore}))
        assert client.get('/api/models/recovery',headers=headers).json()==PENDING
    monkeypatch.setattr(models,'request_agent_json',Mock(return_value={**DONE,'restore':RESTORE}))
    assert client.get('/api/models/recovery',headers=headers).json()==DONE


def test_restore_requires_owner_auth_and_only_the_pending_transaction(client,monkeypatch):
    call=Mock(return_value={**DONE,'outcome':'rollback'})
    monkeypatch.setattr(models,'request_agent_json',call)
    assert client.post('/api/models/recovery/restore',json={'transactionId':'a'*64}).status_code==401
    headers={'Authorization':'Bearer test-key-12345'}
    for body in ({},{'transactionId':'A'*64},{'transactionId':'a'*64,'model':'other'},
                 {'model_id':'qwen'},{'transactionId':'a'*64,'contextLength':4096},None):
        assert client.post('/api/models/recovery/restore',headers=headers,json=body).status_code==400,body
    call.assert_not_called()
    result=client.post('/api/models/recovery/restore',headers=headers,json={'transactionId':'a'*64})
    assert result.status_code==200 and result.json()=={**DONE,'outcome':'rollback'}
    call.assert_called_once_with('POST','/v1/model/recover/restore-previous',
                                 payload={'transactionId':'a'*64},timeout=3805)


def test_failed_restore_stays_pending_with_bounded_detail_and_retry_offer(client,monkeypatch):
    payload={**PENDING,'reason':'model-restore-failed','detail':'Model activation failed:\n'+'x'*400,
             'restore':RESTORE,'private':'hidden'}
    call=Mock(side_effect=AgentHTTPError(409,'restore failed',json.dumps(payload)))
    monkeypatch.setattr(models,'request_agent_json',call)
    result=client.post('/api/models/recovery/restore',headers={'Authorization':'Bearer test-key-12345'},
                       json={'transactionId':'a'*64})
    assert result.status_code==409
    body=result.json()
    assert body['reason']=='model-restore-failed' and body['restore']==RESTORE and 'private' not in body
    assert len(body['detail'])==300 and '\n' not in body['detail']
    assert call.call_count==1


def test_restore_that_kept_the_new_model_is_distinct_from_a_restore(client,monkeypatch):
    headers={'Authorization':'Bearer test-key-12345'}
    kept={**DONE,'reason':'model-restore-target-kept'}
    monkeypatch.setattr(models,'request_agent_json',Mock(return_value=kept))
    result=client.post('/api/models/recovery/restore',headers=headers,json={'transactionId':'a'*64})
    assert result.status_code==200 and result.json()==kept
    # The marker is projected only on a completed commit, never on a rollback.
    monkeypatch.setattr(models,'request_agent_json',
                        Mock(return_value={**DONE,'outcome':'rollback','reason':'model-restore-target-kept'}))
    result=client.post('/api/models/recovery/restore',headers=headers,json={'transactionId':'a'*64})
    assert result.json()=={**DONE,'outcome':'rollback'}


def test_repair_that_got_no_coordinator_answer_is_503_unavailable(client,monkeypatch):
    payload={**PENDING,'reason':'model-recovery-unavailable','restore':RESTORE}
    monkeypatch.setattr(models,'request_agent_json',
                        Mock(side_effect=AgentHTTPError(503,'unavailable',json.dumps(payload))))
    result=client.post('/api/models/recovery',headers={'Authorization':'Bearer test-key-12345'},json={})
    assert result.status_code==503 and result.json()==payload


def test_recovery_budgets_cover_the_relay_bound_host_worst_case():
    spec=importlib.util.spec_from_file_location(
        'pixel_access_relay_budget',Path(__file__).resolve().parents[4]/'bin'/'pixel_access_relay.py')
    relay=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay)
    inspect=relay._MODEL_CONTROL_EDGE_INSPECT_TIMEOUT_SECONDS
    status_read=inspect+22+3  # then model-status inside Edge
    mutation=inspect+310+3    # then model-finish inside Edge
    litellm_proof=12*30+11*2
    # Three status reads, one finish, and one live proof (docs/MODEL-MANAGEMENT.md).
    assert models.MODEL_RECOVERY_TIMEOUT_SECONDS>=3*status_read+mutation+litellm_proof
    assert models.MODEL_RESTORE_TIMEOUT_SECONDS>=models.MODEL_RECOVERY_TIMEOUT_SECONDS+status_read+2700


def test_nginx_outwaits_the_browser_deadline_for_recovery_and_restore():
    conf=(Path(__file__).resolve().parents[2]/'dashboard'/'nginx.conf').read_text(encoding='utf-8')
    for location,budget in (('/api/models/recovery',models.MODEL_RECOVERY_TIMEOUT_SECONDS),
                            ('/api/models/recovery/restore',models.MODEL_RESTORE_TIMEOUT_SECONDS)):
        block=re.search(rf'\n    location = {re.escape(location)} {{\n(.*?)\n    }}\n',conf,re.S).group(1)
        for directive in ('proxy_read_timeout','proxy_send_timeout'):
            seconds=int(re.search(rf'{directive} (\d+)s;',block).group(1))
            # The browser gives up 5 s after the API; nginx must not cut first.
            assert seconds>budget+5,(location,directive)
