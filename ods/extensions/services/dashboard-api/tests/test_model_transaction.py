"""The model transaction never retries inference mutations or invents recovery."""
import copy
import json
import subprocess
import pytest
import test_model_activate as fixtures

host=fixtures._mod
OLD={'model':'same-model','contextLength':65536,'maxTokens':2048,'reasoning':False,'routeFingerprint':'a'*64}
NEW={'model':'same-model','contextLength':65536,'maxTokens':8192,'reasoning':True,'routeFingerprint':'b'*64}


@pytest.fixture
def controller(tmp_path,monkeypatch):
    monkeypatch.setattr(host,'INSTALL_DIR',tmp_path)
    config=tmp_path/'config.json';config.write_text('old')
    monkeypatch.setattr(host,'_pixel_model_config_paths',lambda:{'config':config})
    state=dict(schemaVersion=1,status='ready',revision='c'*64,contract=copy.deepcopy(OLD),pending=False,transactionId=None,outcome=None)
    calls=[]
    def call(operation,request=None,*,config):
        calls.append(operation)
        if operation=='model-begin':
            journal=json.loads(host._pixel_model_journal_path().read_text())
            assert journal['phase']=='prepared' and journal['transactionId']==request['transactionId']
            state.update(status='held',pending=True,transactionId=request['transactionId'])
        elif operation=='model-apply':state.update(status='applied',contract=copy.deepcopy(request['target']))
        elif operation=='model-finish':
            state.update(status='completed',pending=False,outcome=request['outcome'])
            if request['outcome']=='rollback':state['contract']=copy.deepcopy(OLD)
        return copy.deepcopy(state)
    monkeypatch.setattr(host,'_runtime_model_control',call)
    monkeypatch.setattr(host,'_prove_pixel_model_contract',lambda *_:True)
    return config,state,calls,call


def test_journal_is_private_and_precedes_begin_without_secrets(controller):
    _,_,calls,_=controller
    tx=host._begin_pixel_model_transaction({'PIXEL_OPENWEBUI_KEY':'never-store-key','DASHBOARD_API_KEY':'private'})
    text=host._pixel_model_journal_path().read_text()
    assert 'never-store-key' not in text and 'private' not in text
    assert host._pixel_model_recovery_status()=={'pending':True,'phase':'held','transactionId':tx.id}
    tx.apply(NEW);tx.finish('commit')
    assert calls==['model-status','model-begin','model-apply','model-finish']
    assert host._pixel_model_recovery_status()['pending'] is False


@pytest.mark.parametrize('partial',[False,True])
def test_begin_409_is_resolved_by_fresh_native_state_not_status_code(controller,monkeypatch,partial):
    _,state,calls,call=controller
    def reject(operation,request=None,*,config):
        if operation=='model-begin':
            if partial:call(operation,request,config=config)
            else:calls.append(operation)
            raise host._PixelModelTransactionRejected('409')
        return call(operation,request,config=config)
    monkeypatch.setattr(host,'_runtime_model_control',reject)
    if partial:
        tx=host._begin_pixel_model_transaction({'PIXEL_OPENWEBUI_KEY':'configured'})
        assert tx.id==state['transactionId'] and host._pixel_model_recovery_status()['pending']
    else:
        with pytest.raises(host._PixelModelTransactionRejected):host._begin_pixel_model_transaction({'PIXEL_OPENWEBUI_KEY':'configured'})
        assert not host._pixel_model_recovery_status()['pending']
    assert calls==['model-status','model-begin','model-status']


@pytest.mark.parametrize('outcome',['commit','rollback'])
def test_restart_recovery_finishes_only_exact_saved_state_and_current_proof(controller,monkeypatch,outcome):
    config,state,calls,call=controller
    env={'PIXEL_OPENWEBUI_KEY':'configured'}
    tx=host._begin_pixel_model_transaction(env)
    if outcome=='commit':
        config.write_text('new');tx.apply(NEW)
    def lose_finish(operation,request=None,*,config):
        if operation=='model-finish':calls.append(operation);raise TimeoutError()
        return call(operation,request,config=config)
    monkeypatch.setattr(host,'_runtime_model_control',lose_finish)
    with pytest.raises(host._PixelModelTransactionUncertain):tx.finish(outcome)
    assert state['pending'] is True
    monkeypatch.setattr(host,'_runtime_model_control',call)
    monkeypatch.setattr(host,'_prove_pixel_model_contract',lambda *_:False)
    before=list(calls)
    assert host._recover_pixel_model_transaction(env)['pending'] is True
    assert calls[len(before):]==['model-status']
    monkeypatch.setattr(host,'_prove_pixel_model_contract',lambda *_:True)
    result=host._recover_pixel_model_transaction(env)
    assert result['pending'] is False and result['outcome']==outcome
    assert state['contract']==(NEW if outcome=='commit' else OLD)
    assert calls.count('model-begin')==1 and calls.count('model-apply')==(outcome=='commit')


def test_partial_host_mutation_cannot_be_recovered_by_a_generic_reset(controller):
    config,state,calls,_=controller
    env={'PIXEL_OPENWEBUI_KEY':'configured'}
    tx=host._begin_pixel_model_transaction(env)
    config.write_text('half-written')
    result=host._recover_pixel_model_transaction(env)
    assert result=={'pending':True,'phase':'held','transactionId':tx.id,'reason':'model-recovery-proof-required'}
    assert state['pending'] and 'model-finish' not in calls
    with pytest.raises(host._PixelModelTransactionUncertain):host._begin_pixel_model_transaction(env)
    assert calls.count('model-begin')==1


def test_explicit_recovery_commits_exact_applied_target_without_replaying_apply(controller):
    config,state,calls,_=controller
    env={'PIXEL_OPENWEBUI_KEY':'configured'}
    transaction=host._begin_pixel_model_transaction(env)
    transaction.target=copy.deepcopy(NEW)
    transaction._save('applying')
    config.write_text('new')
    state.update(status='applied',contract=copy.deepcopy(NEW),pending=True,
                 transactionId=transaction.id,outcome=None)

    result=host._recover_pixel_model_transaction(env)

    assert result=={'pending':False,'phase':'completed',
                    'transactionId':transaction.id,'outcome':'commit'}
    assert calls.count('model-apply')==0 and calls.count('model-finish')==1
    assert host._read_pixel_model_journal()['phase']=='completed'


@pytest.mark.parametrize('proof,change_during_proof',[(False,False),(True,True)])
def test_explicit_recovery_leaves_unproved_applied_target_pending(
        controller,monkeypatch,proof,change_during_proof):
    config,state,calls,_=controller
    env={'PIXEL_OPENWEBUI_KEY':'configured'}
    transaction=host._begin_pixel_model_transaction(env)
    transaction.target=copy.deepcopy(NEW)
    transaction._save('applying')
    config.write_text('new')
    state.update(status='applied',contract=copy.deepcopy(NEW),pending=True,
                 transactionId=transaction.id,outcome=None)
    def prove(*_args):
        if change_during_proof:config.write_text('changed-during-proof')
        return proof
    monkeypatch.setattr(host,'_prove_pixel_model_contract',prove)

    result=host._recover_pixel_model_transaction(env)

    assert result['pending'] is True and result['phase']=='applying'
    assert calls.count('model-apply')==0 and calls.count('model-finish')==0


def test_unreceived_begin_cannot_clear_hold_when_external_model_changed(controller,monkeypatch):
    _,state,calls,_=controller
    env={'PIXEL_OPENWEBUI_KEY':'configured'}
    tx=host._PixelModelTransaction(env)
    tx.previous=copy.deepcopy(OLD)
    tx._save('prepared')
    monkeypatch.setattr(host,'_prove_pixel_model_contract',lambda *_:False)
    assert host._recover_pixel_model_transaction(env)['pending'] is True
    assert host._pixel_model_recovery_status()['pending'] is True
    assert state['transactionId'] is None
    assert 'model-finish' not in calls
    monkeypatch.setattr(host,'_prove_pixel_model_contract',lambda *_:True)
    assert host._recover_pixel_model_transaction(env)['outcome']=='rollback'


def test_external_adoption_reuses_only_same_confirmed_held_transaction(controller):
    _,state,calls,_=controller
    env={'PIXEL_OPENWEBUI_KEY':'configured'}
    target={'model':'loaded-B','contextLength':65536,'maxTokens':8192,'reasoning':False}
    transaction=host._begin_or_resume_external_pixel_transaction(env,target)
    journal=host._read_pixel_model_journal()
    assert journal['phase']=='held' and journal['target']==target
    resumed=host._begin_or_resume_external_pixel_transaction(env,target)
    assert resumed.id==transaction.id==state['transactionId']
    assert calls.count('model-begin')==1
    with pytest.raises(host._PixelModelTransactionUncertain):
        host._begin_or_resume_external_pixel_transaction(env,{**target,'model':'other-C'})
    assert calls.count('model-begin')==1


@pytest.mark.parametrize('journal_phase', ['applying', 'applied'])
def test_external_adoption_resumes_proved_apply_without_replaying_it(controller, journal_phase):
    _,state,calls,_=controller
    env={'PIXEL_OPENWEBUI_KEY':'configured'}
    target={'model':'loaded-B','contextLength':65536,'maxTokens':8192,'reasoning':False}
    transaction=host._begin_or_resume_external_pixel_transaction(env,target)
    transaction.apply(target)
    if journal_phase=='applying':
        transaction._save('applying')
    resumed=host._begin_or_resume_external_pixel_transaction(env,target)
    assert resumed.id==transaction.id==state['transactionId']
    assert resumed.journal['phase']=='applied'
    assert calls.count('model-begin')==1
    assert calls.count('model-apply')==1
    resumed.finish('commit')
    assert calls.count('model-finish')==1


def test_external_adoption_does_not_replay_unproved_apply(controller):
    _,_,calls,_=controller
    env={'PIXEL_OPENWEBUI_KEY':'configured'}
    target={'model':'loaded-B','contextLength':65536,'maxTokens':8192,'reasoning':False}
    transaction=host._begin_or_resume_external_pixel_transaction(env,target)
    transaction._save('applying')
    with pytest.raises(host._PixelModelTransactionUncertain):
        host._begin_or_resume_external_pixel_transaction(env,target)
    assert calls.count('model-apply')==0
    assert calls.count('model-finish')==0


@pytest.mark.parametrize('outcome',['commit','rollback'])
def test_finish_recovery_qualifies_exact_state_when_one_gate_was_already_released(controller,monkeypatch,outcome):
    config,state,calls,call=controller
    env={'PIXEL_OPENWEBUI_KEY':'configured'}
    tx=host._begin_pixel_model_transaction(env)
    if outcome=='commit':
        config.write_text('new');tx.apply(NEW)
    tx._save('committing' if outcome=='commit' else 'rolling-back')
    def partly_released(operation,request=None,*,config):
        if operation=='model-status':
            calls.append(operation);raise RuntimeError('model-hold-unconfirmed')
        assert operation=='model-finish' and request=={'transactionId':tx.id,'outcome':outcome}
        return call(operation,request,config=config)
    monkeypatch.setattr(host,'_runtime_model_control',partly_released)
    monkeypatch.setattr(host,'_prove_pixel_model_contract',lambda *_:False)
    assert host._recover_pixel_model_transaction(env)['pending']
    assert 'model-finish' not in calls
    monkeypatch.setattr(host,'_prove_pixel_model_contract',lambda *_:True)
    assert host._recover_pixel_model_transaction(env)['outcome']==outcome
    assert state['pending'] is False and calls.count('model-finish')==1
    assert calls.count('model-begin')==1


@pytest.mark.parametrize('status_available',[True,False])
def test_recovery_does_not_release_if_config_changes_during_current_proof(controller,monkeypatch,status_available):
    config,state,calls,call=controller
    env={'PIXEL_OPENWEBUI_KEY':'configured'}
    tx=host._begin_pixel_model_transaction(env)
    config.write_text('new');tx.apply(NEW);tx._save('committing')
    if not status_available:
        def unavailable(*_args,**_kwargs):raise RuntimeError('model-hold-unconfirmed')
        monkeypatch.setattr(host,'_runtime_model_control',unavailable)
    def changed(*_args):config.write_text('changed-during-proof');return True
    monkeypatch.setattr(host,'_prove_pixel_model_contract',changed)
    assert host._recover_pixel_model_transaction(env)['pending']
    assert state['pending'] and 'model-finish' not in calls


@pytest.mark.parametrize('changed',[False,True])
def test_partial_begin_recovery_only_rolls_back_exact_unchanged_host_state(controller,monkeypatch,changed):
    config,state,calls,call=controller
    env={'PIXEL_OPENWEBUI_KEY':'configured'}
    tx=host._PixelModelTransaction(env);tx.previous=copy.deepcopy(OLD);tx._save('prepared')
    state.update(pending=True,transactionId=tx.id,status='held')
    if changed:config.write_text('unconfirmed-other-change')
    def partial(operation,request=None,*,config):
        if operation=='model-status':
            calls.append(operation);raise RuntimeError('model-begin-unconfirmed')
        assert operation=='model-finish' and request=={'transactionId':tx.id,'outcome':'rollback'}
        return call(operation,request,config=config)
    monkeypatch.setattr(host,'_runtime_model_control',partial)
    result=host._recover_pixel_model_transaction(env)
    assert result['pending'] is changed
    assert calls.count('model-finish')==(not changed)
    assert 'model-begin' not in calls and 'model-apply' not in calls


def test_recovery_proof_disables_model_load_warmup(monkeypatch):
    def prove(_env,**kwargs):
        assert kwargs['allow_model_warmup'] is False and kwargs['attempts']==1
        return {'identity':'local','contextLength':65536,'contextVerified':True}
    monkeypatch.setattr(host,'_wait_for_model_readiness',prove)
    contract={key:value for key,value in dict(OLD,model='local').items() if key!='routeFingerprint'}
    assert host._prove_pixel_model_contract({'GGUF_FILE':'local.gguf'},contract)


def test_readiness_without_warmup_never_loads_an_unloaded_model(monkeypatch):
    monkeypatch.setattr(host,'_uses_lemonade_runtime',lambda _:True)
    monkeypatch.setattr(host,'_is_windows_host_llama_server',lambda _:False)
    monkeypatch.setattr(host,'_lemonade_runtime_base_url',lambda _:'http://127.0.0.1:8080')
    monkeypatch.setattr(host.subprocess,'run',lambda cmd,**kwargs:subprocess.CompletedProcess(cmd,0,
        stdout=json.dumps({'status':'ok','all_models_loaded':[]})))
    monkeypatch.setattr(host,'_send_lemonade_warmup',lambda *a,**k:pytest.fail('recovery must not load'))
    monkeypatch.setattr(host,'_chat_completion_ready',lambda *a,**k:pytest.fail('no confirmed loaded identity'))
    assert host._wait_for_model_readiness({},model_id='local',gguf_file='local.gguf',
        llm_model_name='local',lemonade_model_id='Local',attempts=1,initial_delay=0,
        return_proof=True,allow_model_warmup=False)=={}


def test_get_recovery_is_cheap_and_does_not_query_native_or_inference(controller,monkeypatch):
    tx=host._begin_pixel_model_transaction({'PIXEL_OPENWEBUI_KEY':'configured'})
    monkeypatch.setattr(host,'_runtime_model_control',lambda *a,**k:pytest.fail('no native call'))
    monkeypatch.setattr(host,'_pixel_model_config_digests',lambda:pytest.fail('no full config read'))
    assert host._pixel_model_recovery_status()['transactionId']==tx.id


@pytest.mark.parametrize('method',['_handle_model_recovery_status','_handle_model_recover'])
def test_recovery_routes_require_owner_authentication(monkeypatch,method):
    monkeypatch.setattr(host,'check_auth',lambda _:False)
    monkeypatch.setattr(host,'_pixel_model_recovery_status',lambda:pytest.fail('unauthenticated journal read'))
    monkeypatch.setattr(host,'_recover_pixel_model_transaction',lambda *_:pytest.fail('unauthenticated recovery'))
    getattr(host.AgentHandler,method)(fixtures._ResponseHandler())


@pytest.mark.parametrize('body,pending,status',[({},False,200),({},True,409),({'transactionId':'a'*64},True,400)])
def test_recovery_endpoint_uses_only_owned_journal_and_releases_lifecycle_lock(monkeypatch,body,pending,status):
    actions=[]
    monkeypatch.setattr(host,'check_auth',lambda _:True)
    monkeypatch.setattr(host,'read_json_body',lambda _:body)
    monkeypatch.setattr(host,'_begin_model_lifecycle',lambda kind:(actions.append(('begin',kind)) or True,None))
    monkeypatch.setattr(host,'_end_model_lifecycle',lambda kind:actions.append(('end',kind)))
    monkeypatch.setattr(host,'load_env',lambda _: {'fixture':'env'})
    def recover(env):
        assert env=={'fixture':'env'}
        actions.append(('recover',None))
        return {'pending':pending,'phase':'held' if pending else 'completed','transactionId':'a'*64}
    monkeypatch.setattr(host,'_recover_pixel_model_transaction',recover)
    handler=fixtures._ResponseHandler()
    host.AgentHandler._handle_model_recover(handler)
    assert handler.response_code==status
    assert actions==([('begin','model_recovery'),('recover',None),('end','model_recovery')] if body=={} else [])


def test_background_model_readback_is_unknown_until_confirmed_and_invalidates_after_config_change(tmp_path,monkeypatch):
    monkeypatch.setattr(host,'INSTALL_DIR',tmp_path)
    monkeypatch.setattr(host,'_remote_provider_route_state_path',lambda:tmp_path/'route.json')
    env=tmp_path/'.env';env.write_text('one')
    jobs=[]
    class Thread:
        def __init__(self,target,**_):self.target=target
        def start(self):jobs.append(self.target)
    monkeypatch.setattr(host.threading,'Thread',Thread)
    monkeypatch.setattr(host,'_managed_pixel_runtime_contract',lambda:copy.deepcopy(OLD))
    monkeypatch.setattr(host,'_pixel_model_read_cache',{})
    assert host._cached_managed_pixel_runtime_contract() is None
    assert host._cached_managed_pixel_runtime_contract() is None and len(jobs)==1
    jobs.pop()()
    assert host._cached_managed_pixel_runtime_contract()==OLD
    env.write_text('two-new-provider')
    assert host._cached_managed_pixel_runtime_contract() is None
    monkeypatch.setattr(host,'_managed_pixel_runtime_contract',lambda:copy.deepcopy(NEW))
    jobs.pop()()
    assert host._cached_managed_pixel_runtime_contract()==NEW


@pytest.mark.parametrize('completed', [False, True])
def test_legacy_route_digest_only_accepted_for_completed_journal(controller, completed, monkeypatch, tmp_path):
    paths = host._pixel_model_config_paths()
    monkeypatch.setattr(host, '_pixel_model_config_paths', lambda: {**paths, 'data/model-state.json':tmp_path/'state.json'})
    tx = host._begin_pixel_model_transaction({'PIXEL_OPENWEBUI_KEY':'configured'})
    if completed:
        tx.finish('rollback')
    path = host._pixel_model_journal_path()
    value = json.loads(path.read_text())
    for key in ('before', 'after'):
        if value[key] is not None:
            value[key].pop('data/model-state.json')
    path.write_text(json.dumps(value))
    if completed:
        assert host._read_pixel_model_journal()['phase'] == 'completed'
    else:
        with pytest.raises(RuntimeError, match='evidence'):
            host._read_pixel_model_journal()
