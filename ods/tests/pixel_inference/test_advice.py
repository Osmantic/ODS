import asyncio
import copy
import json
import os
from pathlib import Path
import sys
import uuid

import httpx
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'bin'))
from pixel_provider.advice import AdvisoryCall,INSTRUCTION,validate_request
from pixel_provider.config import public_config
from pixel_provider.store import StoreError
from pixel_provider.vault import CredentialStore
from test_provider_runtime import configuration

pytestmark = pytest.mark.skipif(os.name != 'posix',reason='POSIX private store')


def request(**changes):
    return dict(dict(requestId=str(uuid.uuid4()),expectedRevision=1,providerId='backup',
        capsule='Review only this fictional plan.',allowCloud=False,acceptUnknownCost=False,
        maxOutputTokens=512,deadlineSeconds=10),**changes)


@pytest.fixture
def saved(tmp_path):
    root = tmp_path/'providers'; root.mkdir(mode=0o700)
    config = configuration(); config['revision']=0; config['roles']['advisor']='backup'
    result = CredentialStore(root).save_public(dict(expectedRevision=0,document=public_config(config),
        credentialChanges={'backup':dict(action='set',value='fixture-advisor-key')}))
    return root,result


def run(call,handler,**kwargs):
    return asyncio.run(call.execute(client_factory=lambda:httpx.AsyncClient(
        transport=httpx.MockTransport(handler),trust_env=False),**kwargs))


def response(content='Check the assumption.',**message):
    return httpx.Response(200,json=dict(choices=[dict(message=dict(role='assistant',content=content,**message))]))


def test_only_exact_capsule_and_instruction_reach_advisor(saved):
    root,original = saved
    body = request(); call = AdvisoryCall(root,body)
    requests = []
    def handler(req):
        requests.append(req)
        return response()
    body['capsule']='mutated after approval'
    result = run(call,handler)
    assert len(requests)==1
    sent = json.loads(requests[0].content)
    assert sent['messages']==[dict(role='system',content=INSTRUCTION),dict(role='user',content='Review only this fictional plan.')]
    assert sent['model']=='backup' and 'tools' not in sent
    assert requests[0].headers['Authorization']=='Bearer fixture-advisor-key'
    assert result['text']=='Check the assumption.' and result['trusted'] is False
    assert all(value is None for value in result['usage'].values())
    assert result['costStatus']=='unknown'
    assert public_config(CredentialStore(root).load())==original
    assert call.metadata()['leaderChanged'] is False and call.metadata()['toolsAllowed'] is False


@pytest.mark.parametrize('status',[401,403,429,500,503])
def test_never_retries_or_uses_leader_when_advisor_fails(saved,status):
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(status)
    with pytest.raises(StoreError,match='advice-provider-failed'):
        run(AdvisoryCall(saved[0],request()),handler)
    assert len(calls)==1


@pytest.mark.parametrize('message',[dict(tool_calls=[{'id':'bad'}]),dict(function_call={'name':'exec'})])
def test_advisor_cannot_return_executable_tools(saved,message):
    with pytest.raises(StoreError,match='invalid-advice-response'):
        run(AdvisoryCall(saved[0],request()),lambda _:response(**message))


def test_cloud_needs_both_transfer_and_unknown_cost_agreement(saved):
    root,config = saved
    config['providers'][1]['kind']='cloud'; config['policy']['allowCloud']=True
    CredentialStore(root).save_public(dict(expectedRevision=1,document=config,
        credentialChanges={'backup':dict(action='set',value='fictional-not-real-cloud-key')}))
    with pytest.raises(StoreError,match='cloud-transfer-confirmation-required'):
        AdvisoryCall(root,request(expectedRevision=2))
    with pytest.raises(StoreError,match='unknown-cost-confirmation-required'):
        AdvisoryCall(root,request(expectedRevision=2,allowCloud=True))
    assert AdvisoryCall(root,request(expectedRevision=2,allowCloud=True,acceptUnknownCost=True))


def test_revision_target_and_budget_are_bound(saved):
    for changes,error in [(dict(expectedRevision=0),'stale-revision'),(dict(providerId='primary'),'advisor-not-selected'),
            (dict(maxOutputTokens=4096,capsule='x'*16384),'unused')]:
        if error=='unused':
            continue
        with pytest.raises(StoreError,match=error):
            AdvisoryCall(saved[0],request(**changes))
    root,config = saved
    config['providers'][1]['contextTokens']=1024; config['providers'][1]['maxOutputTokens']=512
    CredentialStore(root).save_public(dict(expectedRevision=1,document=config))
    with pytest.raises(StoreError,match='advice-context-limit'):
        AdvisoryCall(root,request(expectedRevision=2,capsule='a'*700))


@pytest.mark.parametrize('change',[dict(requestId='bad'),dict(expectedRevision=True),dict(allowCloud=1),
    dict(capsule=''),dict(capsule='x'*16385),dict(capsule='bad\x00'),dict(maxOutputTokens=0),
    dict(deadlineSeconds=181),dict(unknown='field')])
def test_strict_request(change):
    with pytest.raises(StoreError,match='invalid-advice-request'):
        validate_request(request(**change))


def test_snapshot_survives_settings_edit_and_cancel_releases_upstream(saved):
    root,config = saved
    call = AdvisoryCall(root,request())
    config['roles']['advisor']='primary'
    CredentialStore(root).save_public(dict(expectedRevision=1,document=config))
    state = dict(cancel=False,closed=False)
    async def handler(_):
        state['cancel']=True
        try:
            await asyncio.sleep(20)
        finally:
            state['closed']=True
    with pytest.raises(asyncio.CancelledError):
        run(call,handler,cancelled=lambda:state['cancel'])
    assert state['closed']
    assert call.config['roles']['leader']=='backup'


def test_cancel_before_send_makes_no_call(saved):
    calls = []
    with pytest.raises(asyncio.CancelledError):
        run(AdvisoryCall(saved[0],request()),lambda req:calls.append(req),cancelled=lambda:True)
    assert not calls
