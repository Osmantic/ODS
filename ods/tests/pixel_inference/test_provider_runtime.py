import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

import httpx
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'bin'))
from pixel_provider.config import default_config
from pixel_provider.runtime_gateway import create_app


def configuration():
    config = default_config()
    config.update(enabled=True,revision=4)
    config['providers'] = [dict(id=name,label=name,kind='local',baseUrl=f'http://127.0.0.1:{port}/v1',
        model=name,contextTokens=32768,maxOutputTokens=4096,supportsTools=True,supportsVision=False,
        reasoning=False,credentialRef=None,enabled=True) for name,port in [('primary',12001),('backup',12002)]]
    config['roles'].update(leader='primary',backups=['backup'])
    return config


def payload(stream=False):
    return dict(model='ods/pixel',stream=stream,messages=[{'role':'user','content':'Continue the task'},
        {'role':'assistant','content':None,'tool_calls':[{'id':'done-1','type':'function',
            'function':{'name':'write','arguments':'{"path":"done.txt","content":"saved"}'}}]},
        {'role':'tool','tool_call_id':'done-1','content':'Successfully wrote saved'}],max_tokens=512)


def answer(request):
    return httpx.Response(200,json={'id':'fixture','object':'chat.completion','model':json.loads(request.content)['model'],
        'choices':[{'index':0,'message':{'role':'assistant','content':'verified'},'finish_reason':'stop'}]})


async def request_to(app,body,**kwargs):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://client') as client:
        return await client.post('/v1/chat/completions',headers={'Authorization':'Bearer test'},json=body,**kwargs)


def make_app(handler,config=None,events=None):
    return create_app(config or configuration(),{'primary':'primary-key','backup':'backup-key'},'test',events=events,
        client_factory=lambda:httpx.AsyncClient(transport=httpx.MockTransport(handler),trust_env=False))


def test_backup_receives_exact_completed_tool_history_and_own_key():
    calls,events = [],[]
    def handler(request):
        calls.append((json.loads(request.content),dict(request.headers)))
        return httpx.Response(503) if len(calls)==1 else answer(request)
    app = make_app(handler,events=events)
    original = payload()
    response = asyncio.run(request_to(app,original))
    assert response.status_code == 200
    assert [body['model'] for body,_ in calls] == ['primary','backup']
    assert calls[0][0]['messages'] == calls[1][0]['messages'] == original['messages']
    assert [headers['authorization'] for _,headers in calls] == ['Bearer primary-key','Bearer backup-key']
    assert response.headers['x-ods-provider'] == 'backup'
    assert [e['result'] for e in events] == ['attempt','transient-failure','attempt','completed']


@pytest.mark.parametrize('status',[400,401,403,404,422,301])
def test_auth_config_and_redirect_never_fall_back(status):
    calls = []
    def handler(request):
        calls.append(request); return httpx.Response(status)
    app = make_app(handler)
    async def check():
        first = await request_to(app,payload())
        second = await request_to(app,payload())
        assert first.status_code == 400 and second.status_code == 409
    asyncio.run(check())
    assert len(calls) == 1


def test_refusal_is_a_response_not_a_failure():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','refusal':'policy refusal'}}]})
    response = asyncio.run(request_to(make_app(handler),payload()))
    assert response.status_code == 200 and len(calls)==1
    assert response.json()['choices'][0]['message']['refusal'] == 'policy refusal'


def test_total_attempt_budget_and_terminal_lease():
    calls = []
    def handler(request):
        calls.append(request); return httpx.Response(503)
    app = make_app(handler)
    async def check():
        assert (await request_to(app,payload())).json()['error']['code'] == 'provider-attempts-exhausted'
        assert (await request_to(app,payload())).status_code == 409
    asyncio.run(check())
    assert len(calls)==2


def test_smaller_backup_is_not_sent_tool_history():
    config = configuration(); config['providers'][1]['contextTokens'] = 16384
    calls = []
    def handler(request):
        calls.append(request); return httpx.Response(503)
    response = asyncio.run(request_to(make_app(handler,config),payload()))
    assert response.status_code == 400 and len(calls)==1


def test_missing_output_budget_is_bounded():
    calls = []
    def handler(request):
        calls.append(json.loads(request.content)); return answer(request)
    body = payload(); del body['max_tokens']
    asyncio.run(request_to(make_app(handler),body))
    assert calls[0]['max_tokens']==1024


def test_policy_copy_pins_revision():
    config,events = configuration(),[]
    app = make_app(answer,config,events)
    config['revision'] = 100; config['roles']['leader']='backup'
    result = asyncio.run(request_to(app,payload()))
    assert result.headers['x-ods-provider-revision']=='4'
    assert result.headers['x-ods-provider']=='primary'


SSE = b'data: {"id":"fixture","choices":[{"delta":{"content":"ok"},"finish_reason":null}]}\n\ndata: [DONE]\n\n'


def test_stream_can_fallback_before_commit():
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls)==1:
            raise httpx.ConnectError('fixture')
        return httpx.Response(200,headers={'content-type':'text/event-stream'},content=SSE)
    response = asyncio.run(request_to(make_app(handler),payload(True)))
    assert response.content==SSE and len(calls)==2


def test_partial_stream_never_splices_backup():
    calls,events = [],[]
    class Broken(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            raise httpx.ReadError('fixture')
    def handler(request):
        calls.append(request)
        return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=Broken())
    app = make_app(handler,events=events)
    async def check():
        with pytest.raises(Exception):
            await request_to(app,payload(True))
        assert (await request_to(app,payload())).status_code==409
    asyncio.run(check())
    assert len(calls)==1
    assert events[-1]['result']=='stream-interrupted'


def test_one_deadline_cancels_waiting_headers():
    config = configuration(); config['policy']['deadlineSeconds']=1
    cancelled = []
    async def handler(request):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)
    started = time.monotonic()
    response = asyncio.run(request_to(make_app(handler,config),payload()))
    assert response.json()['error']['code']=='provider-deadline'
    assert time.monotonic()-started<2 and cancelled==[True]


def test_cancel_waiting_headers_releases_and_closes_lease():
    async def check():
        started,cancelled = asyncio.Event(),asyncio.Event()
        async def handler(request):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        app = make_app(handler)
        task = asyncio.create_task(request_to(app,payload()))
        await asyncio.wait_for(started.wait(),1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set()
        assert (await request_to(app,payload())).status_code==409
    asyncio.run(check())
