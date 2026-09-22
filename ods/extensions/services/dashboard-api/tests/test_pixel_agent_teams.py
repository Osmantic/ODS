import asyncio
import copy
import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pixel_agent_teams import TeamManager, TeamStore, TeamConflict, questions_valid, project_receipts_valid
from routers import pixel_teams
from security import verify_api_key

OWNER = 'a' * 64


def finish(content='Actual result', status='none', questions=None):
    yield {'choices': [{'delta': {'content': content}}]}
    frame = {'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'pixel_outcome': {'schemaVersion': 1, 'status': status}}
    if questions:
        frame['pixel_questions'] = {'schemaVersion': 1, 'questions': questions}
    yield frame
    yield {'_done': True, '_state': 'complete'}


async def settle(manager):
    for _ in range(10):
        tasks = list(manager.tasks.values())
        if not tasks:
            return
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)


async def yes(*_):
    return True


@pytest.mark.asyncio
async def test_real_session_ids_order_handoff_and_idempotency(tmp_path):
    calls = []
    async def run(owner, agent):
        calls.append(copy.deepcopy(agent))
        for frame in finish(f'Observed by {agent["name"]}'):
            yield frame
    manager = TeamManager(TeamStore(tmp_path / 'teams'), run, yes)
    one = manager.start(OWNER, 'chat', 'attempt', 'Write a concise proposal', 3, 'Earlier owner context')
    assert manager.start(OWNER, 'chat', 'attempt', 'Write a concise proposal', 3, 'Earlier owner context')['id'] == one['id']
    with pytest.raises(TeamConflict):
        manager.start(OWNER, 'chat', 'attempt', 'Different task', 3, '')
    await settle(manager)
    assert [a['name'] for a in calls] == ['Explorer', 'Builder', 'Reviewer']
    assert len({a['chat_id'] for a in calls}) == 3
    assert 'Observed by Explorer' in calls[1]['messages'][0]['content']
    result = manager.list(OWNER, 'chat')[0]
    assert result['status'] == 'completed'
    assert all(a['status'] == 'completed' for a in result['agents'])
    assert all('chat_id' not in a and 'messages' not in a and 'context_messages' not in a and 'context_request_id' not in a for a in result['agents'])
    assert manager.list('b'*64, 'chat') == []
    assert manager.store.get('b'*64, one['id']) is None


@pytest.mark.asyncio
async def test_verified_project_receipts_survive_team_activity_persistence(tmp_path):
    stamp='2026-09-16T10:00:00.000Z'
    receipt={'schemaVersion':1,'kind':'ods-workspace-project','relativeDirectory':'Playground/http-method-smoke','observedAt':stamp}
    task={'schemaVersion':4,'runId':'chatcmpl_11111111-2222-4333-8444-555555555555','startedAt':stamp,'finishedAt':stamp,'state':'completed','calls':0,'failures':0,'blocked':0,'truncated':False,'activities':[], 'events':[], 'context':None,'goal':None,'projects':[receipt]}
    async def run(*_):
        yield {'pixel_task':task}
        for frame in finish('Created summary.json'): yield frame
    directory=tmp_path/'teams'
    manager=TeamManager(TeamStore(directory),run,yes)
    manager.start(OWNER,'chat','attempt','Create a summary file',1,'')
    await settle(manager)
    assert manager.list(OWNER,'chat')[0]['agents'][0]['activity']==task
    restored=TeamManager(TeamStore(directory),run,yes)
    assert restored.list(OWNER,'chat')[0]['agents'][0]['activity']['projects']==[receipt]
    assert restored.list('b'*64,'chat')==[]


@pytest.mark.parametrize('change', [
    {'relativeDirectory':'Playground/../escape'}, {'relativeDirectory':'Playground/CON'},
    {'relativeDirectory':'Playground/name.'}, {'relativeDirectory':'/private'}, {'schemaVersion':True},
    {'observedAt':'2026-02-30T00:00:00.000Z'}, {'observedAt':'2026-09-17T00:00:00.000Z'}, {'hostPath':'/private'},
])
def test_team_project_receipts_reject_untrusted_or_invalid_metadata(change):
    receipt={'schemaVersion':1,'kind':'ods-workspace-project','relativeDirectory':'Playground/tool','observedAt':'2026-09-16T10:00:00.000Z'}
    task={'projects':[{**receipt,**change}],'finishedAt':'2026-09-16T11:00:00.000Z'}
    assert not project_receipts_valid(task)
    assert not project_receipts_valid({'projects':[receipt,receipt]})


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['failed', 'pending'])
async def test_failed_or_pending_verification_never_runs_next_agent(tmp_path, status):
    calls = []
    async def run(owner, agent):
        calls.append(agent['id'])
        for frame in finish('The work could not be completed', status):
            yield frame
    manager = TeamManager(TeamStore(tmp_path/'teams'), run, yes)
    manager.start(OWNER, 'chat', 'a', 'Do work', 2, '')
    await settle(manager)
    assert calls == ['0']
    row = manager.list(OWNER, 'chat')[0]
    assert row['status'] == 'failed'
    assert [a['status'] for a in row['agents']] == ['failed', 'skipped']


@pytest.mark.asyncio
async def test_missing_terminal_receipt_is_not_a_success(tmp_path):
    async def run(*_):
        yield {'choices': [{'delta': {'content': 'I did it'}}]}
        yield {'_done': True, '_state': 'complete'}
    manager = TeamManager(TeamStore(tmp_path/'teams'), run, yes)
    manager.start(OWNER, 'chat', 'a', 'Do work', 1, '')
    await settle(manager)
    assert manager.list(OWNER, 'chat')[0]['agents'][0]['status'] == 'failed'


@pytest.mark.asyncio
async def test_question_pauses_and_answer_continues_only_that_child(tmp_path):
    questions = [{'id':'style','question':'Qual estilo?', 'options':['Clean','Colorido']}]
    calls = []
    async def run(owner, agent):
        calls.append(copy.deepcopy(agent))
        frames = finish('Qual estilo?', 'pending', questions) if agent['id']=='0' and agent['turn']==0 else finish('Result based on the answer')
        for frame in frames:
            yield frame
    manager = TeamManager(TeamStore(tmp_path/'teams'), run, yes)
    initial = manager.start(OWNER, 'chat', 'a', 'Do work', 2, '')
    await settle(manager)
    row = manager.list(OWNER, 'chat')[0]
    assert row['status'] == 'waiting' and row['agents'][1]['status'] == 'queued'
    with pytest.raises(TeamConflict):
        manager.answer(OWNER, initial['id'], '0', {'wrong':'Clean'})
    manager.answer(OWNER, initial['id'], '0', {'style':'Clean'})
    with pytest.raises(TeamConflict):
        manager.answer(OWNER, initial['id'], '0', {'style':'Clean'})
    await settle(manager)
    assert len(calls) == 3
    assert calls[0]['chat_id'] == calls[1]['chat_id'] != calls[2]['chat_id']
    assert calls[0]['request_id'] != calls[1]['request_id']
    assert calls[1]['messages'][-1] == {'role':'user','content':'Qual estilo?\nClean'}
    assert calls[1]['context_messages'] == [
        calls[0]['messages'][0], {'role': 'assistant', 'content': 'Qual estilo?'},
        {'role': 'user', 'content': 'Qual estilo?\nClean'},
    ]
    saved = manager.store.get(OWNER, initial['id'])['agents'][0]
    assert saved['context_messages'][-1] == {'role': 'assistant', 'content': 'Result based on the answer'}
    assert saved['context_request_id'] == calls[1]['request_id']
    assert manager.list(OWNER, 'chat')[0]['status'] == 'completed'


@pytest.mark.asyncio
async def test_cancel_survives_activity_updates_and_does_not_start_next_agent(tmp_path):
    started, proceed = asyncio.Event(), asyncio.Event()
    calls = []
    async def run(owner, agent):
        calls.append(agent['id']); started.set()
        await proceed.wait()
        yield {'pixel_task':{'schemaVersion':1,'calls':1,'activities':[]}}
        yield {'error':{'message':'Stopped'}}
        yield {'_done':True,'_state':'cancelled'}
    async def cancel(*_):
        proceed.set()
        return True
    manager = TeamManager(TeamStore(tmp_path/'teams'), run, cancel)
    row = manager.start(OWNER,'chat','a','Do work',3,'')
    await started.wait()
    await manager.stop(OWNER,row['id'])
    await settle(manager)
    assert calls == ['0']
    result = manager.list(OWNER,'chat')[0]
    assert result['status'] == 'cancelled'
    assert all(a['status']=='cancelled' for a in result['agents'])


@pytest.mark.asyncio
async def test_unacknowledged_stop_and_restart_never_claim_cancelled_or_replay(tmp_path):
    started, proceed = asyncio.Event(), asyncio.Event()
    calls=[]
    async def run(*_):
        calls.append(1);started.set();await proceed.wait()
        for frame in finish(): yield frame
    async def no(*_): return False
    directory=tmp_path/'teams'
    manager=TeamManager(TeamStore(directory),run,no)
    row=manager.start(OWNER,'chat','a','Do work',2,'')
    await started.wait()
    stopped=await manager.stop(OWNER,row['id'])
    assert stopped['status']=='stopping'
    recovered=TeamManager(TeamStore(directory),run,no)
    assert recovered.list(OWNER,'chat')[0]['status']=='interrupted'
    assert len(calls)==1 and not recovered.tasks
    with pytest.raises(TeamConflict): recovered.start(OWNER,'chat','b','Do new work',1,'')
    proceed.set();await settle(manager)


@pytest.mark.asyncio
async def test_global_lane_and_immediate_cancellation_of_queued_team(tmp_path):
    started, proceed=asyncio.Event(),asyncio.Event()
    calls=[]
    async def run(owner, agent):
        calls.append(agent['chat_id']);started.set();await proceed.wait()
        for frame in finish():yield frame
    manager=TeamManager(TeamStore(tmp_path/'teams'),run,yes)
    manager.start(OWNER,'first','a','First task',1,'')
    await started.wait()
    second=manager.start(OWNER,'second','b','Second task',1,'')
    await asyncio.sleep(0)
    assert len(calls)==1
    result=await manager.stop(OWNER,second['id'])
    assert result['status']=='cancelled'
    proceed.set();await settle(manager)
    assert len(calls)==1


def test_questions_bounds_and_private_history_paths(tmp_path):
    assert questions_valid([{'id':'x','question':'Q'*300,'options':['A','B']}])
    assert not questions_valid([{'id':'x','question':'Q','options':['A','A']}])
    store=TeamStore(tmp_path/'teams')
    with pytest.raises(ValueError):store.get(OWNER,'../escape')
    outside=tmp_path/'other.json';outside.write_text('{}')
    if os.name=='posix':
        (store.directory/f'{OWNER}-{"b"*32}.json').symlink_to(outside)
        with pytest.raises(ValueError):store.get(OWNER,'b'*32)


def test_routes_require_owner_and_validate_count_and_answer_scope(tmp_path, monkeypatch):
    app=FastAPI();app.include_router(pixel_teams.router)
    with TestClient(app) as client:
        assert client.post('/api/pixel/agents/list',json={'chat_id':'chat'}).status_code in (401,403)
    app.dependency_overrides[verify_api_key]=lambda:'owner'
    monkeypatch.setattr(pixel_teams,'_manager',TeamManager(TeamStore(tmp_path/'teams'),None,None))
    with TestClient(app) as client:
        for count in [0,7,True,'2']:
            assert client.post('/api/pixel/agents/start',json={'chat_id':'chat','request_id':'a','task':'Work','count':count}).status_code==422
        assert client.post('/api/pixel/agents/stop',json={'team_id':'b'*32}).status_code==404
        assert client.post('/api/pixel/agents/answer',json={'team_id':'b'*32,'agent_id':'0','answers':{'x':'y'}}).status_code==404

@pytest.mark.asyncio
@pytest.mark.parametrize('plan,count', [('{"count":3}',3), ('```json\n{"count":1}\n```',1), ('{"count":500}',2), ('A simple request.\n{"count":1}',1)])
async def test_automatic_team_uses_model_plan_with_bounded_fallback(tmp_path, plan, count):
    calls=[]
    async def run(owner, agent):
        calls.append(copy.deepcopy(agent))
        for frame in finish(plan if agent['role']=='coordinator' else 'Done'):
            yield frame
    manager=TeamManager(TeamStore(tmp_path/'teams'),run,yes)
    manager.start(OWNER,'chat','auto','Help me write a short announcement',None,'')
    await settle(manager)
    row=manager.list(OWNER,'chat')[0]
    assert row['status']=='completed' and len(row['agents'])==count
    assert len(calls)==count+1
    assert len({a['chat_id'] for a in calls})==count+1
    assert row['planning']['role']=='coordinator' and 'messages' not in row['planning']
    assert bool(row['notice']) == (count==2)

@pytest.mark.asyncio
async def test_retry_reviewer_preserves_builder_and_uses_new_attempt(tmp_path):
    calls=[]
    recovery_ids=[]
    async def run(owner,agent):
        calls.append((agent['id'],agent['request_id']))
        if agent['request_id']=='retry-1': recovery_ids.extend(agent.get('recovery_request_ids', []))
        if agent['role']=='reviewer' and agent['request_id']!='retry-1':
            yield {'error':{'message':'runtime unavailable'}}
            yield {'_done':True,'_state':'interrupted'}
        else:
            for frame in finish('Completed report'):yield frame
    manager=TeamManager(TeamStore(tmp_path/'teams'),run,yes)
    row=manager.start(OWNER,'chat','retry-test','Research and review',2,'')
    await settle(manager)
    failed=manager.list(OWNER,'chat')[0]
    assert failed['agents'][1]['retryable'] and not failed['agents'][0]['retryable']
    assert 'incomplete' in failed['agents'][1]['error']
    with pytest.raises(TeamConflict):manager.retry(OWNER,row['id'],'0')
    manager.retry(OWNER,row['id'],'1')
    with pytest.raises(TeamConflict):manager.retry(OWNER,row['id'],'1')
    await settle(manager)
    assert calls==[('0','turn-0'),('1','turn-0'),('1','recovery-1-turn-0'),('1','retry-1')]
    assert recovery_ids==['turn-0','recovery-1-turn-0']
    assert all('recovery_request_ids' not in agent for agent in manager.list(OWNER,'chat')[0]['agents'])
    assert manager.list(OWNER,'chat')[0]['status']=='completed'

@pytest.mark.asyncio
async def test_readonly_transport_recovery_is_bounded_and_never_replays_builder(tmp_path):
    calls=[]
    async def run(owner,agent):
        calls.append((agent['role'],agent['request_id']))
        if agent['role']=='reviewer' and agent['request_id']=='turn-0':
            yield {'error':{'code':'runtime_unavailable'}}
            yield {'_done':True,'_state':'interrupted'}
        else:
            for frame in finish('Verified result'):yield frame
    manager=TeamManager(TeamStore(tmp_path/'teams'),run,yes)
    manager.start(OWNER,'chat','recover','Research and review',2,'')
    await settle(manager)
    assert calls==[('builder','turn-0'),('reviewer','turn-0'),('reviewer','recovery-1-turn-0')]
    assert manager.list(OWNER,'chat')[0]['status']=='completed'


@pytest.mark.asyncio
async def test_unavailable_runtime_never_dispatches_a_worker(monkeypatch):
    from unittest.mock import AsyncMock
    host=AsyncMock(return_value={})
    health=AsyncMock(return_value='Unavailable')
    dispatch=AsyncMock()
    monkeypatch.setattr(pixel_teams.pixel,'_host_model_status',host)
    monkeypatch.setattr(pixel_teams.pixel,'_local_inference_issue',health)
    monkeypatch.setattr(pixel_teams.pixel,'_retained_chat_stream',dispatch)
    frames=[x async for x in pixel_teams._run(OWNER,{})]
    assert health.await_count==3
    assert frames[-2]=={'error':{'code':'model_unavailable'}}
    dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_stop_during_health_check_never_starts_inference(monkeypatch):
    from unittest.mock import AsyncMock
    agent={}
    async def health(_):agent['stop_requested']=True;return None
    dispatch=AsyncMock()
    monkeypatch.setattr(pixel_teams.pixel,'_host_model_status',AsyncMock(return_value={}))
    monkeypatch.setattr(pixel_teams.pixel,'_local_inference_issue',health)
    monkeypatch.setattr(pixel_teams.pixel,'_retained_chat_stream',dispatch)
    frames=[x async for x in pixel_teams._run(OWNER,agent)]
    assert frames[-1]=={'_done':True,'_state':'cancelled'}
    dispatch.assert_not_called()

@pytest.mark.asyncio
async def test_empty_model_answer_is_never_completed(tmp_path):
    async def run(*_):
        for frame in finish(''):yield frame
    manager=TeamManager(TeamStore(tmp_path/'teams'),run,yes)
    manager.start(OWNER,'chat','empty','Do work',1,'')
    await settle(manager)
    assert manager.list(OWNER,'chat')[0]['status']=='failed'


@pytest.mark.parametrize('mode', ['team', 'goal'])
@pytest.mark.parametrize('command', ['/extensions https://github.com/o/r install', '/goal /extensions @demo install'])
def test_extension_commands_cannot_create_unbound_worker_requests(tmp_path, mode, command):
    async def unexpected(*_):
        raise AssertionError('No model worker should start')
    manager = TeamManager(TeamStore(tmp_path / 'teams'), unexpected, yes)
    with pytest.raises(TeamConflict, match='chat installation coordinator'):
        manager.start(OWNER, 'chat', 'attempt', command, 1, '', mode)
    assert manager.list(OWNER, 'chat') == []
