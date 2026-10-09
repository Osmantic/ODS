"""The model transaction never retries inference mutations or invents recovery."""
import copy
from types import SimpleNamespace
import json
import subprocess
import pytest
import test_model_activate as fixtures

host=fixtures._mod
_real_prove_pixel_model_contract=host._prove_pixel_model_contract
OLD={'model':'same-model','contextLength':65536,'maxTokens':2048,'reasoning':False,'routeFingerprint':'a'*64}
NEW={'model':'same-model','contextLength':65536,'maxTokens':8192,'reasoning':True,'routeFingerprint':'b'*64}


def test_local_contract_identity_accepts_only_exact_active_store_path(monkeypatch, tmp_path):
    monkeypatch.setattr(host, '_active_model_directory', lambda _: tmp_path)
    config = {'GGUF_FILE': 'model.gguf'}
    assert host._pixel_local_identity_matches(config, 'model.gguf', 'model.gguf')
    assert host._pixel_local_identity_matches(config, str(tmp_path / 'model.gguf'), 'model.gguf')
    assert not host._pixel_local_identity_matches(config, '/other/model.gguf', 'model.gguf')
    assert not host._pixel_local_identity_matches(config, str(tmp_path / 'model.gguf'), 'other.gguf')
    assert not host._pixel_local_identity_matches({}, str(tmp_path / 'model.gguf'), 'model.gguf')


def test_local_contract_identity_accepts_the_configured_logical_name(monkeypatch, tmp_path):
    # The installer's Portal contract names the model by LLM_MODEL, while
    # llama-server serves it under the GGUF file name (--alias GGUF_FILE).
    monkeypatch.setattr(host, '_active_model_directory', lambda _: tmp_path)
    config = {'GGUF_FILE': 'Qwen3.5-9B-Q4_K_M.gguf', 'LLM_MODEL': 'qwen3.5-9b'}
    assert host._pixel_local_identity_matches(config, 'Qwen3.5-9B-Q4_K_M.gguf', 'qwen3.5-9b')
    assert host._pixel_local_identity_matches(config, str(tmp_path / 'Qwen3.5-9B-Q4_K_M.gguf'), 'qwen3.5-9b')
    assert not host._pixel_local_identity_matches(config, 'Other-9B-Q4_K_M.gguf', 'qwen3.5-9b')
    assert not host._pixel_local_identity_matches(config, '/other/Qwen3.5-9B-Q4_K_M.gguf', 'qwen3.5-9b')
    assert not host._pixel_local_identity_matches(config, 'Qwen3.5-9B-Q4_K_M.gguf', 'qwen3.5-27b')
    assert not host._pixel_local_identity_matches(
        {'GGUF_FILE': 'Qwen3.5-9B-Q4_K_M.gguf'}, 'Qwen3.5-9B-Q4_K_M.gguf', 'qwen3.5-9b')


@pytest.mark.parametrize('status,body,detail', [
    (403, {'error': 'forbidden'}, '(model-status: HTTP 403 forbidden)'),
    (409, {'reason': 'busy'}, '(model-status: HTTP 409 busy)'),
    (400, {'error': 'bad value with spaces'}, '(model-status: HTTP 400)'),
])
def test_model_controller_refusal_names_operation_status_and_reason(monkeypatch, status, body, detail):
    # Fleet, Mac 2026-10-05: one generic sentence hid whether the controller
    # refused the agent's key or was busy.
    import pixel_access_relay
    monkeypatch.setattr(pixel_access_relay, 'request_runtime_model_control',
                        lambda operation, request=None, *, config: (status, body))
    with pytest.raises(host._PixelModelTransactionRejected) as refused:
        host._runtime_model_control('model-status', config={})
    assert str(refused.value) == ('Managed model controller refused the transition; '
                                  'its current state must be verified ' + detail)


READY = dict(schemaVersion=1, status='ready', revision='c'*64, contract=OLD, pending=False,
             transactionId=None, outcome=None)
BUSY = (409, {'error': 'transition-busy'})


def busy_relay(monkeypatch, replies):
    import pixel_access_relay
    calls: list[str] = []
    def relay(operation, request=None, *, config):
        calls.append(operation)
        return replies.pop(0) if len(replies) > 1 else replies[0]
    monkeypatch.setattr(pixel_access_relay, 'request_runtime_model_control', relay)
    return calls


def test_status_read_waits_while_another_operation_holds_the_controller(monkeypatch):
    # Fleet, Mac 2026-10-05: right after a committed change the controller
    # answered model-status with 409 transition-busy for over a minute, and
    # the next enable failed within a second.
    calls = busy_relay(monkeypatch, [BUSY, BUSY, (200, copy.deepcopy(READY))])
    sleeps: list[float] = []
    monkeypatch.setattr(host, 'time', SimpleNamespace(monotonic=lambda: 0, sleep=sleeps.append))
    assert host._runtime_model_control('model-status', config={}) == READY
    assert calls == ['model-status'] * 3 and sleeps == [3, 3]


def test_status_read_stops_waiting_with_a_clear_message(monkeypatch):
    calls = busy_relay(monkeypatch, [BUSY])
    clock = iter(range(0, 6000, 60))
    monkeypatch.setattr(host, 'time', SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda _: None))
    with pytest.raises(host._PixelModelTransactionRejected) as refused:
        host._runtime_model_control('model-status', config={})
    assert str(refused.value) == ('ODS is still finishing the last model change. '
                                  'Wait a minute, then try again.')
    # The clock reads 60 s per call: asked at 0 s and 60 s, then 120 s is up.
    assert refused.value.code == 'transition-busy' and calls == ['model-status'] * 2


def test_busy_mutation_is_refused_at_once_and_never_sent_again(monkeypatch):
    calls = busy_relay(monkeypatch, [BUSY])
    monkeypatch.setattr(host, 'time', SimpleNamespace(
        monotonic=lambda: 0, sleep=lambda _: pytest.fail('a mutation must not wait and resend')))
    with pytest.raises(host._PixelModelTransactionRejected) as refused:
        host._runtime_model_control('model-finish', {'transactionId': 'a'*64, 'outcome': 'rollback'}, config={})
    assert '(model-finish: HTTP 409 transition-busy)' in str(refused.value)
    assert refused.value.code == 'transition-busy' and calls == ['model-finish']


def test_installer_contract_with_logical_name_is_proven_and_mismatches_are_logged(monkeypatch, caplog):
    # Fleet, 2026-10-05: leaving a remote route on a fresh install failed every
    # time, because the saved Portal contract named qwen3.5-9b and the server
    # serves Qwen3.5-9B-Q4_K_M.gguf.
    config = {'GGUF_FILE': 'Qwen3.5-9B-Q4_K_M.gguf', 'LLM_MODEL': 'qwen3.5-9b', 'CTX_SIZE': '65536'}
    contract = {'model': 'qwen3.5-9b', 'contextLength': 65536, 'maxTokens': 8192, 'reasoning': False}
    monkeypatch.setattr(host, '_managed_wsl_runtime', lambda _config: {'managed': False})
    proof = {'identity': 'Qwen3.5-9B-Q4_K_M.gguf', 'contextLength': 65536, 'contextVerified': True}
    monkeypatch.setattr(host, '_wait_for_model_readiness', lambda *_args, **_kwargs: dict(proof))
    assert _real_prove_pixel_model_contract(config, contract) is True

    proof['contextLength'] = 32768
    with caplog.at_level('WARNING'):
        assert _real_prove_pixel_model_contract(config, contract) is False
    assert 'the runtime context is 32768, the contract needs 65536' in caplog.text

    proof.update(identity='Other-9B-Q4_K_M.gguf', contextLength=65536)
    caplog.clear()
    with caplog.at_level('WARNING'):
        assert _real_prove_pixel_model_contract(config, contract) is False
    assert 'the runtime serves Other-9B-Q4_K_M.gguf, the contract names qwen3.5-9b' in caplog.text


@pytest.fixture
def controller(tmp_path,monkeypatch):
    monkeypatch.setattr(host,'INSTALL_DIR',tmp_path)
    config=tmp_path/'config.json'
    config.write_text('old')
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
    tx.apply(NEW)
    tx.finish('commit')
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
        config.write_text('new')
        tx.apply(NEW)
    def lose_finish(operation,request=None,*,config):
        if operation=='model-finish':
            calls.append(operation)
            raise TimeoutError()
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


@pytest.mark.parametrize('proof,other_drift,proof_drift', [
    (True, False, False), (False, False, False),
    (True, True, False), (True, False, True),
])
def test_commit_recovery_accepts_only_proven_stable_env_only_drift(
        controller,monkeypatch,proof,other_drift,proof_drift):
    _,_,calls,_=controller
    env_file=host.INSTALL_DIR/'.env'
    env_file.write_text('MODEL=old\n')
    other_file=host.INSTALL_DIR/'model-config'
    other_file.write_text('old')
    monkeypatch.setattr(host,'_pixel_model_config_paths',lambda:{
        '.env':env_file,'model-config':other_file,
    })
    env={'PIXEL_OPENWEBUI_KEY':'configured'}
    transaction=host._begin_pixel_model_transaction(env)
    env_file.write_text('MODEL=new\n')
    other_file.write_text('new')
    transaction.apply(NEW)
    transaction._save('committing')
    env_file.write_text('MODEL=new\nUNRELATED_SETTING=changed\n')
    if other_drift:
        other_file.write_text('changed-after-commit')
    def prove(*_args):
        if proof_drift:
            env_file.write_text('MODEL=new\nCHANGED_DURING_PROOF=true\n')
        return proof
    monkeypatch.setattr(host,'_prove_pixel_model_contract',prove)

    result=host._recover_pixel_model_transaction(env)

    expected_pending=not proof or other_drift or proof_drift
    assert result['pending'] is expected_pending
    assert calls.count('model-finish')==(0 if expected_pending else 1)


def test_partial_host_mutation_cannot_be_recovered_by_a_generic_reset(controller):
    config,state,calls,_=controller
    env={'PIXEL_OPENWEBUI_KEY':'configured'}
    tx=host._begin_pixel_model_transaction(env)
    config.write_text('half-written')
    result=host._recover_pixel_model_transaction(env)
    assert result=={'pending':True,'phase':'held','transactionId':tx.id,'reason':'model-recovery-proof-required',
                    'releasable':False}
    # A host changed mid-switch is never released without the proof either.
    assert host._recover_pixel_model_transaction(env,release_unverified=True)['pending'] is True
    assert state['pending'] and 'model-finish' not in calls
    with pytest.raises(host._PixelModelTransactionUncertain):host._begin_pixel_model_transaction(env)
    assert calls.count('model-begin')==1


def test_legacy_macos_promotion_with_held_2b_contract_keeps_recovery_pending(controller, monkeypatch):
    config, state, calls, _ = controller
    previous = {'model': 'Qwen3.5-2B-Q4_K_M.gguf', 'contextLength': 65536,
                'maxTokens': 8192, 'reasoning': False}
    state['contract'] = previous
    env = {'PIXEL_OPENWEBUI_KEY': 'configured', 'GPU_BACKEND': 'apple',
           'GGUF_FILE': 'Qwen3.5-9B-Q4_K_M.gguf', 'LLM_MODEL': 'qwen3.5-9b'}
    transaction = host._begin_pixel_model_transaction(env)
    config.write_text('changed by interrupted activation')
    status = host.INSTALL_DIR / 'data/bootstrap-status.json'
    status.write_text(json.dumps({'status': 'complete', 'percent': 100,
                                 'model': env['GGUF_FILE']}))
    monkeypatch.setattr(host, '_managed_wsl_runtime', lambda _: {'managed': False})
    monkeypatch.setattr(host, '_wait_for_model_readiness', lambda *a, **k: {
        'identity': env['GGUF_FILE'], 'contextLength': 65536, 'contextVerified': True})
    monkeypatch.setattr(host, '_prove_pixel_model_contract', _real_prove_pixel_model_contract)
    assert not host._prove_pixel_model_contract(env, previous)
    before = host._pixel_model_journal_path().read_bytes()
    for explicit_release in (False, True):
        result = host._recover_pixel_model_transaction(env, release_unverified=explicit_release)
        assert result['pending'] and result['phase'] == 'held'
        assert result['releasable'] is False
    assert transaction.target is None
    assert host._pixel_model_journal_path().read_bytes() == before
    assert 'model-apply' not in calls and 'model-finish' not in calls


@pytest.mark.parametrize('drift', ['GGUF_FILE=other-model.gguf', 'CTX_SIZE=8192'])
def test_commit_recovery_reads_current_env_before_releasing_native_hold(controller,monkeypatch,drift):
    _,state,calls,_=controller
    env_file=host.INSTALL_DIR/'.env'
    text=('PIXEL_OPENWEBUI_KEY=configured\nGGUF_FILE=same-model.gguf\n'
          'CTX_SIZE=65536\nMAX_CONTEXT=65536\n')
    env_file.write_text(text)
    monkeypatch.setattr(host,'_pixel_model_config_paths',lambda:{'.env':env_file})
    monkeypatch.setattr(host,'_managed_wsl_runtime',lambda _env:{'managed':False})

    def serving(config,**kwargs):
        # The runtime serves same-model.gguf at 65536 cells; an exact proof
        # holds only while .env still names that model and context.
        assert kwargs['attempts']==1 and kwargs['require_exact_context'] is True
        if kwargs['gguf_file']!='same-model.gguf' or config.get('CTX_SIZE')!='65536':
            return {}
        return {'identity':'same-model.gguf','contextLength':65536,'contextVerified':True}

    monkeypatch.setattr(host,'_wait_for_model_readiness',serving)
    env=host.load_env(env_file)
    transaction=host._begin_pixel_model_transaction(env)
    target={key:value for key,value in NEW.items() if key!='routeFingerprint'}
    target['model']='same-model.gguf'
    transaction.apply(target)
    transaction._save('committing')
    key=drift.split('=',1)[0]
    env_file.write_text('\n'.join(drift if line.startswith(key+'=') else line
                                  for line in text.splitlines())+'\n')
    monkeypatch.setattr(host,'_prove_pixel_model_contract',_real_prove_pixel_model_contract)
    assert _real_prove_pixel_model_contract(env,target) is True
    assert _real_prove_pixel_model_contract(host.load_env(env_file),target) is False

    result=host._recover_pixel_model_transaction(env)

    assert result['pending'] is True
    assert state['pending'] is True
    assert 'model-finish' not in calls


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


def test_external_adoption_transaction_helper_is_gone():
    assert not hasattr(host, '_begin_or_resume_external_pixel_transaction')


@pytest.mark.parametrize('outcome',['commit','rollback'])
def test_finish_recovery_qualifies_exact_state_when_one_gate_was_already_released(controller,monkeypatch,outcome):
    config,state,calls,call=controller
    env={'PIXEL_OPENWEBUI_KEY':'configured'}
    tx=host._begin_pixel_model_transaction(env)
    if outcome=='commit':
        config.write_text('new')
        tx.apply(NEW)
    tx._save('committing' if outcome=='commit' else 'rolling-back')
    def partly_released(operation,request=None,*,config):
        if operation=='model-status':
            calls.append(operation)
            raise RuntimeError('model-hold-unconfirmed')
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
    config.write_text('new')
    tx.apply(NEW)
    tx._save('committing')
    if not status_available:
        def unavailable(*_args,**_kwargs):raise RuntimeError('model-hold-unconfirmed')
        monkeypatch.setattr(host,'_runtime_model_control',unavailable)
    def changed(*_args):
        config.write_text('changed-during-proof')
        return True
    monkeypatch.setattr(host,'_prove_pixel_model_contract',changed)
    assert host._recover_pixel_model_transaction(env)['pending']
    assert state['pending'] and 'model-finish' not in calls


@pytest.mark.parametrize('changed',[False,True])
def test_partial_begin_recovery_only_rolls_back_exact_unchanged_host_state(controller,monkeypatch,changed):
    config,state,calls,call=controller
    env={'PIXEL_OPENWEBUI_KEY':'configured'}
    tx=host._PixelModelTransaction(env)
    tx.previous=copy.deepcopy(OLD)
    tx._save('prepared')
    state.update(pending=True,transactionId=tx.id,status='held')
    if changed:config.write_text('unconfirmed-other-change')
    def partial(operation,request=None,*,config):
        if operation=='model-status':
            calls.append(operation)
            raise RuntimeError('model-begin-unconfirmed')
        assert operation=='model-finish' and request=={'transactionId':tx.id,'outcome':'rollback'}
        return call(operation,request,config=config)
    monkeypatch.setattr(host,'_runtime_model_control',partial)
    result=host._recover_pixel_model_transaction(env)
    assert result['pending'] is changed
    assert calls.count('model-finish')==(not changed)
    assert 'model-begin' not in calls and 'model-apply' not in calls


def test_recovery_proof_is_one_exact_probe(monkeypatch):
    def prove(_env,**kwargs):
        assert kwargs['attempts']==1 and kwargs['require_exact_context'] is True
        assert 'allow_model_warmup' not in kwargs
        return {'identity':'local.gguf','contextLength':65536,'contextVerified':True}
    monkeypatch.setattr(host,'_wait_for_model_readiness',prove)
    contract={key:value for key,value in dict(OLD,model='local.gguf').items() if key!='routeFingerprint'}
    assert host._prove_pixel_model_contract({'GGUF_FILE':'local.gguf'},contract)


def test_readiness_never_completes_against_a_model_the_runtime_does_not_list(monkeypatch):
    def run(cmd,**_kwargs):
        url=cmd[-1]
        body={'status':'ok'} if url.endswith('/health') else {'object':'list','data':[]}
        return subprocess.CompletedProcess(cmd,0,stdout=json.dumps(body))
    monkeypatch.setattr(host.subprocess,'run',run)
    monkeypatch.setattr(host,'_chat_completion_ready',lambda *a,**k:pytest.fail('no confirmed loaded identity'))
    assert host._wait_for_model_readiness({},model_id='local',gguf_file='local.gguf',
        llm_model_name='local',attempts=1,initial_delay=0,return_proof=True)=={}


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


@pytest.mark.parametrize('body,pending,status',[
    ({},False,200),({},True,409),({'releaseUnverified':True},False,200),
    ({'transactionId':'a'*64},True,400),({'releaseUnverified':False},True,400),({'releaseUnverified':'yes'},True,400)])
def test_recovery_endpoint_uses_only_owned_journal_and_releases_lifecycle_lock(monkeypatch,body,pending,status):
    actions=[]
    monkeypatch.setattr(host,'check_auth',lambda _:True)
    monkeypatch.setattr(host,'read_json_body',lambda _:body)
    monkeypatch.setattr(host,'_begin_model_lifecycle',lambda kind:(actions.append(('begin',kind)) or True,None))
    monkeypatch.setattr(host,'_end_model_lifecycle',lambda kind:actions.append(('end',kind)))
    monkeypatch.setattr(host,'load_env',lambda _: {'fixture':'env'})
    def recover(env,*,release_unverified=False):
        assert env=={'fixture':'env'}
        actions.append(('recover',release_unverified))
        return {'pending':pending,'phase':'held' if pending else 'completed','transactionId':'a'*64}
    monkeypatch.setattr(host,'_recover_pixel_model_transaction',recover)
    handler=fixtures._ResponseHandler()
    host.AgentHandler._handle_model_recover(handler)
    assert handler.response_code==status
    accepted=body in ({},{'releaseUnverified':True})
    assert actions==([('begin','model_recovery'),('recover',body=={'releaseUnverified':True}),
                      ('end','model_recovery')] if accepted else [])


def held_unproven_switch(controller,monkeypatch):
    """A switch that applied nothing and whose previous model cannot be proven."""
    env={'PIXEL_OPENWEBUI_KEY':'configured'}
    tx=host._begin_pixel_model_transaction(env)
    monkeypatch.setattr(host,'_prove_pixel_model_contract',lambda *_:False)
    return env,tx


def test_owner_can_release_a_switch_that_changed_nothing_without_the_live_proof(controller,monkeypatch):
    # Fleet row 27 (laptop): the previous contract was a cloud default with no
    # local model, so recovery waited forever for a proof that cannot pass.
    _,state,calls,_=controller
    env,tx=held_unproven_switch(controller,monkeypatch)
    result=host._recover_pixel_model_transaction(env)
    assert result['pending'] is True and result['releasable'] is True
    before=list(calls)
    result=host._recover_pixel_model_transaction(env,release_unverified=True)
    assert result=={'pending':False,'phase':'completed','transactionId':tx.id,'outcome':'rollback'}
    assert calls[len(before):]==['model-status','model-status','model-finish']
    assert state['status']=='completed' and state['contract']==OLD and not host._pixel_model_recovery_status()['pending']


def test_release_without_proof_refuses_a_controller_on_another_contract(controller,monkeypatch):
    _,state,calls,_=controller
    env,_=held_unproven_switch(controller,monkeypatch)
    state['contract']=copy.deepcopy(NEW)
    assert host._recover_pixel_model_transaction(env)['releasable'] is False
    assert host._recover_pixel_model_transaction(env,release_unverified=True)['pending'] is True
    assert 'model-finish' not in calls


def test_release_without_proof_refuses_an_applied_target(controller,monkeypatch):
    config,_,calls,_=controller
    env={'PIXEL_OPENWEBUI_KEY':'configured'}
    tx=host._begin_pixel_model_transaction(env)
    config.write_text('new')
    tx.apply(NEW)
    monkeypatch.setattr(host,'_prove_pixel_model_contract',lambda *_:False)
    assert host._recover_pixel_model_transaction(env,release_unverified=True)['pending'] is True
    assert 'model-finish' not in calls


def test_interrupted_release_without_proof_completes_on_rerun(controller,monkeypatch):
    _,state,calls,call=controller
    env,_=held_unproven_switch(controller,monkeypatch)
    def lose_finish(operation,request=None,*,config):
        if operation=='model-finish':
            calls.append(operation)
            raise TimeoutError()
        return call(operation,request,config=config)
    monkeypatch.setattr(host,'_runtime_model_control',lose_finish)
    assert host._recover_pixel_model_transaction(env,release_unverified=True)['pending'] is True
    assert host._pixel_model_recovery_status()['phase']=='rolling-back'
    monkeypatch.setattr(host,'_runtime_model_control',call)
    result=host._recover_pixel_model_transaction(env,release_unverified=True)
    assert result['pending'] is False and result['outcome']=='rollback' and state['status']=='completed'


@pytest.fixture
def model_readback(tmp_path,monkeypatch):
    monkeypatch.setattr(host,'INSTALL_DIR',tmp_path)
    monkeypatch.setattr(host,'_remote_provider_route_state_path',lambda:tmp_path/'route.json')
    (tmp_path/'.env').write_text('one')
    clock=[100.0]
    jobs=[]
    class Thread:
        def __init__(self,target,**_):self.target=target
        def start(self):jobs.append(self.target)
    monkeypatch.setattr(host.threading,'Thread',Thread)
    monkeypatch.setattr(host.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(host,'_managed_pixel_runtime_contract',lambda:copy.deepcopy(OLD))
    monkeypatch.setattr(host,'_pixel_model_read_cache',{})
    return clock,jobs,tmp_path/'.env'


def test_model_readback_refreshes_before_expiry_with_one_worker(model_readback):
    clock,jobs,_=model_readback
    read=host._cached_managed_pixel_runtime_contract
    assert read() is None
    jobs.pop()()
    # Keep crossing the original TTL with newly confirmed, never stale proof.
    for _ in range(4):
        clock[0]+=5
        assert read()==OLD
        assert len(jobs)==1
        assert all(read()==OLD for _ in range(8)) and len(jobs)==1
        jobs.pop()()
        assert read()==OLD and not jobs


def test_model_readback_never_serves_expired_value_during_refresh(model_readback):
    clock,jobs,_=model_readback
    read=host._cached_managed_pixel_runtime_contract
    assert read() is None
    jobs.pop()()
    clock[0]+=10
    assert read()==OLD and len(jobs)==1
    clock[0]+=5
    assert read() is None and len(jobs)==1
    jobs.pop()()
    assert read()==OLD


@pytest.mark.parametrize('failure',[None,RuntimeError('denied')])
def test_model_readback_failed_refresh_revokes_still_fresh_value(model_readback,monkeypatch,failure):
    clock,jobs,_=model_readback
    read=host._cached_managed_pixel_runtime_contract
    read()
    jobs.pop()()
    clock[0]+=10
    assert read()==OLD and len(jobs)==1
    def failed():
        if failure:raise failure
        return None
    monkeypatch.setattr(host,'_managed_pixel_runtime_contract',failed)
    jobs.pop()()
    assert read() is None


@pytest.mark.parametrize('poll_changed_key',[True,False])
def test_model_readback_rejects_old_worker_after_config_change(model_readback,monkeypatch,poll_changed_key):
    clock,jobs,env=model_readback
    read=host._cached_managed_pixel_runtime_contract
    read()
    jobs.pop()()
    clock[0]+=10
    assert read()==OLD and len(jobs)==1
    env.write_text('two-new-provider')
    if poll_changed_key:
        assert read() is None and len(jobs)==1
    jobs.pop()()
    # Old worker must neither qualify the new generation nor prevent its readback.
    assert read() is None and len(jobs)==1
    monkeypatch.setattr(host,'_managed_pixel_runtime_contract',lambda:copy.deepcopy(NEW))
    jobs.pop()()
    assert read()==NEW


def test_model_readback_does_not_return_old_value_after_immediate_failed_refresh(model_readback,monkeypatch):
    clock,jobs,_=model_readback
    read=host._cached_managed_pixel_runtime_contract
    read()
    jobs.pop()()
    clock[0]+=10
    class ImmediateThread:
        def __init__(self,target,**_):self.target=target
        def start(self):self.target()
    monkeypatch.setattr(host.threading,'Thread',ImmediateThread)
    monkeypatch.setattr(host,'_managed_pixel_runtime_contract',lambda:None)
    assert read() is None


def test_model_readback_old_worker_cannot_publish_after_key_changes_back(model_readback,monkeypatch):
    _,jobs,_=model_readback
    key=['original']
    monkeypatch.setattr(host,'_managed_pixel_readback_key',lambda:key[0])
    read=host._cached_managed_pixel_runtime_contract
    assert read() is None
    key[0]='changed'
    assert read() is None and len(jobs)==1
    key[0]='original'
    assert read() is None and len(jobs)==1
    jobs.pop()()
    assert read() is None and len(jobs)==1
    jobs.pop()()
    assert read()==OLD


def test_model_readback_retries_after_worker_start_failure(model_readback,monkeypatch):
    clock,jobs,_=model_readback
    read=host._cached_managed_pixel_runtime_contract
    read()
    jobs.pop()()
    clock[0]+=10
    regular_thread=host.threading.Thread
    class FailedThread:
        def __init__(self,**_):pass
        def start(self):raise RuntimeError('cannot start thread')
    monkeypatch.setattr(host.threading,'Thread',FailedThread)
    assert read() is None
    monkeypatch.setattr(host.threading,'Thread',regular_thread)
    assert read()==OLD and len(jobs)==1
    jobs.pop()()
    assert read()==OLD


def test_background_model_readback_is_unknown_until_confirmed_and_invalidates_after_config_change(tmp_path,monkeypatch):
    monkeypatch.setattr(host,'INSTALL_DIR',tmp_path)
    monkeypatch.setattr(host,'_remote_provider_route_state_path',lambda:tmp_path/'route.json')
    env=tmp_path/'.env'
    env.write_text('one')
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
