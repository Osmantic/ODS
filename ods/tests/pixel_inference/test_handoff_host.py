import hashlib
import http.client
import json
import os
from pathlib import Path
import sys
import uuid

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_pixel_provider_host_api as fixture
from pixel_provider.handoff_approvals import get_manager

pytestmark=pytest.mark.skipif(os.name!='posix',reason='POSIX handoff custody')


def checkpoint():
    value=dict(schemaVersion=1,runId=str(uuid.uuid4()),sessionId='session',agentId='pixel',workspaceDir='/tmp/workspace',
        recipient=dict(id='stronger',label='Stronger',kind='local',baseUrl='http://127.0.0.1:8090/v1',model='fixture',
            revision=1,scope='run',previousProviderId='leader'),dataScope='conversation-and-this-run-tool-results',
        returnAction='configured-leader-on-next-run',prompt='Continue',systemPrompt='System',messages=[])
    raw=json.dumps(value,separators=(',',':'),ensure_ascii=False)
    return value,raw,hashlib.sha256(raw.encode()).hexdigest()


@pytest.fixture
def host(tmp_path):
    fixture.HostHTTP.setUpClass(); fixture.HostHTTP.agent.DATA_DIR=tmp_path
    try: yield fixture.HostHTTP,tmp_path
    finally: fixture.HostHTTP.tearDownClass()


def request(host,action,body,token='synthetic-provider-test-key',raw=None):
    connection=http.client.HTTPConnection(*host[0].server.server_address,timeout=4)
    try:
        connection.request('POST','/v1/pixel/handoff/'+action,body=raw if raw is not None else json.dumps(body),
            headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        response=connection.getresponse()
        return response.status,json.loads(response.read()),response.getheader('Cache-Control')
    finally: connection.close()


@pytest.mark.parametrize('action',['list','status','decide'])
def test_owner_auth_before_any_state(host,action):
    assert request(host,action,{},token='wrong')[0]==403
    assert not list(host[1].iterdir())


def test_pristine_list_no_state_or_public_publish(host):
    assert request(host,'list',{})==(200,dict(items=[],unavailableCount=0),'no-store')
    assert not list(host[1].iterdir())
    assert request(host,'publish',{})[0]==404


def test_real_owner_preview_decision_and_conflict(host):
    (host[1]/'pixel-providers').mkdir(mode=0o700)
    manager=get_manager(host[1]); value,raw,digest=checkpoint()
    with manager.publish(raw,digest,60) as pending:
        assert request(host,'list',{})[1]['items'][0]['runId']==value['runId']
        status,result,cache=request(host,'status',{'runId':value['runId']})
        assert status==200 and result['checkpointJson']==raw and cache=='no-store'
        decision=dict(runId=value['runId'],checkpointDigest=digest,approved=True,allowCloud=False,acceptUnknownCost=False)
        assert request(host,'decide',decision)[1]['status']=='approved'
        assert pending.receipt()==dict(approved=True,checkpointDigest=digest)
        assert request(host,'decide',dict(decision,approved=False))[0]==409
        pending.finish('approved')
    assert request(host,'status',{'runId':value['runId']})[1]['status']=='approved'


@pytest.mark.parametrize('action,body',[('list',{'runId':'bad'}),('status',{'runId':'../bad'}),
    ('decide',{'runId':'../bad','command':['evil']}),('status',{'runId':'bad','secret':True})])
def test_invalid_requests_do_not_create_state(host,action,body):
    assert request(host,action,body)[0]==400
    assert not list(host[1].iterdir())


def test_strict_and_bounded_json(host):
    assert request(host,'list',{},raw='{"x":1,"x":2}')[0]==400
    assert request(host,'list',{},raw=' '*4097)[0]==413


def test_interrupted_cannot_be_approved(host):
    (host[1]/'pixel-providers').mkdir(mode=0o700)
    manager=get_manager(host[1]); value,raw,digest=checkpoint()
    with manager.publish(raw,digest,60): pass
    assert request(host,'status',{'runId':value['runId']})[1]['status']=='interrupted'
    decision=dict(runId=value['runId'],checkpointDigest=digest,approved=True,allowCloud=False,acceptUnknownCost=False)
    assert request(host,'decide',decision)[0]==409
