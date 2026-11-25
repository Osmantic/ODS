import asyncio
import copy
import re
import pytest
from pixel_agent_teams import TeamManager, TeamStore, TeamConflict
from portal_goal import GOAL_STOPPED_ROUNDS, public_plan, goal_prompt, round_file_edits
from test_pixel_agent_teams import OWNER, finish, settle, yes

PLAN = {'status':'active','summary':'Working','steps':[{'id':'work','title':'Do the work','status':'running'},{'id':'check','title':'Check it','status':'pending'}]}


def goal_frame(status='active', changed=False):
    plan=copy.deepcopy(PLAN)
    plan['status']=status
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


def stopped_round(content, stop='tool-limit', status='failed', publication=None):
    # A round a Portal limit stopped: its reply arrived, its outcome is failed.
    yield {'choices': [{'delta': {'content': content}}]}
    yield {'choices': [{'delta': {}, 'finish_reason': 'stop'}],
           'pixel_outcome': {'schemaVersion': 1, 'status': status, **({'stop': stop} if stop else {})},
           **({'pixel': {'schemaVersion': 1, 'preview': copy.deepcopy(publication)}} if publication else {})}
    yield {'_done': True, '_state': 'complete'}


def edits_frame(status='active', edits=0, completed=()):
    frame=goal_frame(status)
    for step in frame['pixel_task']['goal']['steps']:
        if step['id'] in completed:step['status']='completed'
    if edits:frame['pixel_task']['activities']=[{'kind':'edit','calls':edits+1,'failures':1,'blocked':0}]
    return frame


def stop_frame(edits=0):
    # Pixel itself marks the plan of a stopped run blocked, with its own summary.
    frame=edits_frame('blocked',edits)
    frame['pixel_task']['goal']['summary']='Execution stopped; review the response before continuing.'
    return frame


PUBLICATION = {'schemaVersion': 1, 'kind': 'ods-pixel-workspace-preview',
               'relativeDirectory': 'Playground/elf', 'siteId': 'site-' + 'c' * 24,
               'port': 9437, 'url': 'http://site-' + 'c' * 24 + '.localhost:9437/site-' + 'c' * 24 + '/',
               'files': 3, 'bytes': 1000, 'sha256': 'c' * 64, 'entrySha256': 'd' * 64}


@pytest.mark.asyncio
@pytest.mark.parametrize('stop', ['tool-limit', 'run-deadline'])
async def test_a_round_a_portal_limit_stopped_continues_the_goal(tmp_path, stop):
    # Laptop, 2026-10-30: long changes stop at the tool limit; a goal used to end there.
    calls=[]
    async def run(owner,agent):
        calls.append(copy.deepcopy(agent))
        if len(calls)==1:
            yield stop_frame(edits=3)
            for frame in stopped_round('Cache written; the blit in ui.js is left.',stop,publication=PUBLICATION):yield frame
        else:
            yield goal_frame('completed')
            for frame in finish('Actual final result'):yield frame
    manager=TeamManager(TeamStore(tmp_path/'teams'),run,yes)
    manager.start(OWNER,'chat','goal','Make enemies draw from a sprite cache',None,'','goal')
    await settle(manager)
    row=manager.list(OWNER,'chat')[0]
    assert row['status']=='completed' and len(calls)==2
    prompt=calls[1]['messages'][-1]['content']
    note={'tool-limit':"The last round reached Portal's tool limit before it finished.",
          'run-deadline':"The last round reached Portal's time limit for one response before it finished."}[stop]
    # The note follows the sentence Pixel reads the owner's words up to.
    assert prompt.index('Continue this goal from the saved work.')<prompt.index(note)
    assert any(m['content']=='Cache written; the blit in ui.js is left.' for m in calls[1]['messages'])
    # The goal card shows the saved plan as still going, not Pixel's stop summary.
    assert calls[1]['goal_plan']=={**PLAN,'summary':'Continuing from the saved work'}
    # The stopped round's verified preview is kept for the finished goal.
    assert row['agents'][0]['publication']==PUBLICATION


# Pixel's stop for a round whose later attempt (a compaction retry or a
# revision) was still running at its 30-minute deadline, as the ingress
# delivers it (run_deadline_delivery.test.mjs). The ingress used to cut such a
# round off at 32 minutes with no reply, which ends the goal ('transport'
# above).
DEADLINE_STOP = ('**Portal stopped this response after 30 minutes, the most one response can run.** '
                 'This request needed more time. Files saved before the stop are kept, but the last change may be '
                 'unfinished, so the project may not work yet. Send "continue" to pick up where it left off. '
                 'If it keeps happening, [get help on Discord](https://discord.gg/4ntNp9MAwC).')


@pytest.mark.asyncio
async def test_a_retried_round_stopped_at_its_deadline_continues_the_goal(tmp_path):
    calls=[]
    async def run(owner,agent):
        calls.append(copy.deepcopy(agent))
        if len(calls)==1:
            yield stop_frame(edits=2)
            for frame in stopped_round(DEADLINE_STOP,'run-deadline'):yield frame
        else:
            yield goal_frame('completed')
            for frame in finish('Actual final result'):yield frame
    manager=TeamManager(TeamStore(tmp_path/'teams'),run,yes)
    manager.start(OWNER,'chat','goal','Make enemies draw from a sprite cache',None,'','goal')
    await settle(manager)
    row=manager.list(OWNER,'chat')[0]
    assert row['status']=='completed' and len(calls)==2
    prompt=calls[1]['messages'][-1]['content']
    assert "The last round reached Portal's time limit for one response before it finished." in prompt
    assert any(m['content']==DEADLINE_STOP for m in calls[1]['messages'])


@pytest.mark.asyncio
async def test_a_stopped_round_continues_long_work_up_to_a_bound(tmp_path):
    # Changed files count as progress in a stopped round, three stopped rounds
    # in a row without a newly completed step; a completed step resets that.
    calls=[]
    async def run(owner,agent):
        calls.append(agent['request_id'])
        n=len(calls)
        if n<6:
            yield edits_frame(edits=2,completed=('work',) if n>=4 else ())
            for frame in stopped_round('More files changed; still working.'):yield frame
        else:
            yield goal_frame('completed')
            for frame in finish('Actual final result'):yield frame
    manager=TeamManager(TeamStore(tmp_path/'teams'),run,yes)
    manager.start(OWNER,'chat','goal','Build the long feature',None,'','goal')
    await settle(manager)
    assert manager.list(OWNER,'chat')[0]['status']=='completed' and len(calls)==6
    # Without a newly completed step, the fourth stopped round in a row pauses.
    endless=[]
    async def churn(owner,agent):
        endless.append(agent['request_id'])
        yield edits_frame(edits=2)
        for frame in stopped_round('More files changed; still working.'):yield frame
    manager=TeamManager(TeamStore(tmp_path/'endless'),churn,yes)
    manager.start(OWNER,'chat','goal','Build the long feature',None,'','goal')
    await settle(manager)
    row=manager.list(OWNER,'chat')[0]
    assert row['status']=='failed' and len(endless)==GOAL_STOPPED_ROUNDS+1
    assert row['agents'][0]['error'].startswith('The goal is incomplete.')


@pytest.mark.asyncio
@pytest.mark.parametrize('stopped', [True, False])
async def test_rounds_without_progress_still_pause(tmp_path, stopped):
    # A stopped round that changed no file, or an ordinary round that changed
    # files but finished no step, is a stall: four in a row pause the goal.
    idle=[]
    async def stuck(owner,agent):
        idle.append(agent['request_id'])
        if stopped:
            yield edits_frame()
            for frame in stopped_round('Still reading.'):yield frame
        else:
            yield edits_frame(edits=2)
            for frame in finish('Changed a file; the step is not done.'):yield frame
    manager=TeamManager(TeamStore(tmp_path/'idle'),stuck,yes)
    manager.start(OWNER,'chat','goal','Build the long feature',None,'','goal')
    await settle(manager)
    row=manager.list(OWNER,'chat')[0]
    assert row['status']=='failed' and len(idle)==4
    assert row['agents'][0]['error'].startswith('The goal is incomplete.')


@pytest.mark.asyncio
async def test_a_stopped_rounds_completion_report_is_checked_by_the_next_round(tmp_path):
    calls=[]
    async def run(owner,agent):
        calls.append(copy.deepcopy(agent))
        yield goal_frame('completed')
        frames=stopped_round('All steps done; checking was cut off.') if len(calls)==1 else finish('Actual final result')
        for frame in frames:yield frame
    manager=TeamManager(TeamStore(tmp_path/'teams'),run,yes)
    manager.start(OWNER,'chat','goal','Make enemies draw from a sprite cache',None,'','goal')
    await settle(manager)
    assert manager.list(OWNER,'chat')[0]['status']=='completed' and len(calls)==2
    assert 'record completion via tool_call' in calls[1]['messages'][-1]['content']


@pytest.mark.asyncio
async def test_the_stop_note_is_not_repeated_after_a_question(tmp_path):
    calls=[]
    questions=[{'id':'style','question':'Which style?','options':['Clean','Bright']}]
    async def run(owner,agent):
        calls.append(copy.deepcopy(agent))
        if len(calls)==1:
            yield stop_frame(edits=2)
            for frame in stopped_round('Cache written; one question left.'):yield frame
        elif len(calls)==2:
            yield goal_frame('waiting')
            for frame in finish('Need your preference','pending',questions):yield frame
        else:
            yield goal_frame('completed')
            for frame in finish('Actual final result'):yield frame
    manager=TeamManager(TeamStore(tmp_path/'teams'),run,yes)
    row=manager.start(OWNER,'chat','goal','Make enemies draw from a sprite cache',None,'','goal')
    await settle(manager)
    assert "reached Portal's tool limit" in calls[1]['messages'][-1]['content']
    manager.answer(OWNER,row['id'],'0',{'style':'Clean'})
    await settle(manager)
    assert len(calls)==3 and 'Clean' in calls[2]['messages'][-1]['content']
    assert "reached Portal's tool limit" not in calls[2]['messages'][-1]['content']


def test_an_edit_a_stop_cut_off_is_not_counted():
    activity={'activities':[{'kind':'edit','calls':3,'failures':1,'blocked':0}],
              'events':[{'sequence':1,'kind':'edit','state':'completed'},{'sequence':2,'kind':'edit','state':'failed'},
                        {'sequence':3,'kind':'edit','state':'running'}]}
    assert round_file_edits(activity)==1
    assert round_file_edits({**activity,'events':activity['events'][:2]})==2
    assert round_file_edits(None)==0 and round_file_edits({'activities':[{'kind':'read','calls':4,'failures':0}]})==0


@pytest.mark.asyncio
@pytest.mark.parametrize('stop', [None, 'output-limit', ['tool-limit']])
async def test_other_failed_rounds_still_end_the_goal(tmp_path, stop):
    calls=[]
    async def run(owner,agent):
        calls.append(agent['request_id'])
        yield edits_frame(edits=2)
        for frame in stopped_round('Tests failed.',stop):yield frame
    manager=TeamManager(TeamStore(tmp_path/'teams'),run,yes)
    manager.start(OWNER,'chat','goal','Fix the parser',None,'','goal')
    await settle(manager)
    row=manager.list(OWNER,'chat')[0]
    assert row['status']=='failed' and len(calls)==1
    assert row['agents'][0]['error']=='The agent could not verify completion. Its response is not a verified result.'


@pytest.mark.asyncio
async def test_stop_partial_goal_never_starts_a_continuation_or_claims_completed(tmp_path):
    started,release=asyncio.Event(),asyncio.Event()
    calls=[]
    async def run(owner,agent):
        calls.append(agent['request_id'])
        started.set()
        await release.wait()
        yield goal_frame()
        for frame in finish('Partial saved work'):yield frame
    async def cancel(*_):
        release.set()
        return True
    manager=TeamManager(TeamStore(tmp_path/'teams'),run,cancel)
    row=manager.start(OWNER,'chat','goal','Do work',None,'','goal')
    await started.wait()
    await manager.stop(OWNER,row['id'])
    await settle(manager)
    assert len(calls)==1 and manager.list(OWNER,'chat')[0]['status']=='cancelled'


@pytest.mark.asyncio
async def test_goal_question_waits_for_answer_and_keeps_goal_mode(tmp_path):
    calls=[]
    questions=[{'id':'style','question':'Qual estilo?','options':['Clean','Colorido']}]
    async def run(owner,agent):
        calls.append(copy.deepcopy(agent))
        yield goal_frame('waiting' if len(calls)==1 else 'active' if len(calls)==2 else 'completed')
        frames=finish('Need your preference','pending',questions) if len(calls)==1 else finish('Actual result')
        for frame in frames:yield frame
    manager=TeamManager(TeamStore(tmp_path/'teams'),run,yes)
    row=manager.start(OWNER,'chat','goal','Make a proposal',None,'','goal')
    await settle(manager)
    assert manager.list(OWNER,'chat')[0]['status']=='waiting' and len(calls)==1
    manager.answer(OWNER,row['id'],'0',{'style':'Clean'})
    await settle(manager)
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


def test_goal_prompt_leaves_the_reply_language_to_pixel():
    # Pixel's response-language rule decides, from the owner's words after /goal.
    row={'goal':'Crie um site simples sobre café e publique.','context':'Conversa anterior sobre o site.'}
    owner_text=[row['goal'],row['context'],'Use azul.']
    for agent in [{}, {'goal_plan':PLAN,'goal_answers':'Use azul.'}]:
        prompt=goal_prompt(row,agent)
        assert row['goal'] in prompt
        fixed=prompt
        for text in owner_text:
            fixed=fixed.replace(text,'')
        assert not re.search(r"language|English|Portuguese",fixed,re.I),fixed
