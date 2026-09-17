import asyncio
import copy
import pytest
from pixel_agent_teams import TeamManager, TeamStore, TeamConflict
from portal_goal import public_plan, goal_prompt
from test_pixel_agent_teams import OWNER, finish, settle, yes

PLAN = {'status':'active','summary':'Working','steps':[{'id':'work','title':'Do the work','status':'running'},{'id':'check','title':'Check it','status':'pending'}]}


def goal_frame(status='active', changed=False):
    plan=copy.deepcopy(PLAN);plan['status']=status
    if status=='completed':
        for step in plan['steps']:step['status']='completed'
    if changed:plan['steps'][0]['title']='A different task'
    return {'pixel_task':{'schemaVersion':2,'state':'finished','calls':1,'failures':0,'blocked':0,'activities':[], 'goal':plan}}


@pytest.mark.asyncio
@pytest.mark.parametrize('schema_version',[2,3])
async def test_goal_continues_in_new_durable_turn_then_completes(tmp_path,schema_version):
    calls=[]
    async def run(owner,agent):
        calls.append(copy.deepcopy(agent))
        frame=goal_frame('completed' if len(calls)==2 else 'active')
        frame['pixel_task']['schemaVersion']=schema_version
        yield frame
        for frame in finish('Actual final result' if len(calls)==2 else 'Partial work saved'):yield frame
    manager=TeamManager(TeamStore(tmp_path/'teams'),run,yes)
    initial=manager.start(OWNER,'chat','goal','Produce and check the result',None,'','goal')
    with pytest.raises(TeamConflict):manager.start(OWNER,'chat','goal','Produce and check the result',1,'','team')
    await settle(manager)
    row=manager.list(OWNER,'chat')[0]
    assert row['status']=='completed' and row['mode']=='goal'
    assert len(calls)==2 and calls[0]['chat_id']==calls[1]['chat_id']
    assert calls[0]['request_id']!=calls[1]['request_id']
    assert calls[1]['messages'][-1]['content'].startswith('/goal Produce and check')
    assert 'Saved public plan' in calls[1]['messages'][-1]['content']
    assert any(m['content']=='Partial work saved' for m in calls[1]['messages'])
    assert row['agents'][0]['activity']['goal']['status']=='completed'
    assert row['agents'][0]['activity']['schemaVersion']==schema_version
    assert manager.start(OWNER,'chat','goal','Produce and check the result',None,'','goal')['id']==initial['id']


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['stalled','transport','missing_receipt','changed_plan','blocked'])
async def test_goal_never_replays_ambiguous_effects_or_reports_false_completion(tmp_path,failure):
    calls=[]
    async def run(owner,agent):
        calls.append(agent['request_id'])
        yield goal_frame('blocked' if failure=='blocked' else 'active',failure=='changed_plan' and len(calls)>1)
        if failure=='transport':
            yield {'error':{'message':'Connection lost after writing'}}
            yield {'_done':True,'_state':'interrupted'}
        elif failure=='missing_receipt':
            yield {'choices':[{'delta':{'content':'Done'}}]}
            yield {'_done':True,'_state':'complete'}
        else:
            for frame in finish('Saved partial work'):yield frame
    manager=TeamManager(TeamStore(tmp_path/'teams'),run,yes)
    manager.start(OWNER,'chat','goal','Do the work',None,'','goal')
    await settle(manager)
    row=manager.list(OWNER,'chat')[0]
    assert row['status']=='failed'
    assert len(calls)==({'stalled':4,'changed_plan':2}.get(failure,1))
    assert len(calls)==len(set(calls)) and not row['agents'][0]['retryable']


@pytest.mark.asyncio
async def test_stop_partial_goal_never_starts_a_continuation_or_claims_completed(tmp_path):
    started,release=asyncio.Event(),asyncio.Event();calls=[]
    async def run(owner,agent):
        calls.append(agent['request_id']);started.set();await release.wait()
        yield goal_frame()
        for frame in finish('Partial saved work'):yield frame
    async def cancel(*_):release.set();return True
    manager=TeamManager(TeamStore(tmp_path/'teams'),run,cancel)
    row=manager.start(OWNER,'chat','goal','Do work',None,'','goal')
    await started.wait();await manager.stop(OWNER,row['id']);await settle(manager)
    assert len(calls)==1 and manager.list(OWNER,'chat')[0]['status']=='cancelled'


@pytest.mark.asyncio
async def test_goal_question_waits_for_answer_and_keeps_goal_mode(tmp_path):
    calls=[];questions=[{'id':'style','question':'Qual estilo?','options':['Clean','Colorido']}]
    async def run(owner,agent):
        calls.append(copy.deepcopy(agent))
        yield goal_frame('waiting' if len(calls)==1 else 'active' if len(calls)==2 else 'completed')
        frames=finish('Need your preference','pending',questions) if len(calls)==1 else finish('Actual result')
        for frame in frames:yield frame
    manager=TeamManager(TeamStore(tmp_path/'teams'),run,yes)
    row=manager.start(OWNER,'chat','goal','Make a proposal',None,'','goal')
    await settle(manager)
    assert manager.list(OWNER,'chat')[0]['status']=='waiting' and len(calls)==1
    manager.answer(OWNER,row['id'],'0',{'style':'Clean'});await settle(manager)
    assert calls[1]['messages'][-1]['content'].startswith('/goal Make a proposal')
    assert "Owner's answers:" in calls[1]['messages'][-1]['content']
    assert len(calls)==3 and 'Clean' in calls[2]['messages'][-1]['content']
    assert manager.list(OWNER,'chat')[0]['status']=='completed'


def test_public_plan_is_bounded_and_does_not_accept_hidden_fields():
    assert public_plan(PLAN)==PLAN
    for invalid in [{**PLAN,'private':'secret'}, {**PLAN,'status':'completed'}, {**PLAN,'steps':[PLAN['steps'][0]]*9},
                    {**PLAN,'status':[]}, {**PLAN,'steps':[{**PLAN['steps'][0],'status':{}}]}]:
        assert public_plan(invalid) is None


def test_goal_prompt_preserves_maximum_objective_and_answers_within_transport_limit():
    row={'goal':'x'*8000,'context':'c'*1800}
    plan={**PLAN,'steps':[{'id':'step'+str(i),'title':'"'*160,'status':'pending'} for i in range(8)]}
    prompt=goal_prompt(row,{'goal_plan':plan,'goal_answers':'a'*4000})
    assert len(prompt)<=16384 and row['goal'] in prompt and 'a'*4000 in prompt
    assert all(step['id'] in prompt for step in plan['steps'])
