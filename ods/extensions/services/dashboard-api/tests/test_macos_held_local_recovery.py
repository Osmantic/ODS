"""Regression evidence for the reported bootstrap 2B / live 9B split."""
import copy
import json

import pytest

import test_model_activate as fixtures
import test_model_transaction as transactions

host = transactions.host
controller = transactions.controller


OLD = {'model': 'Qwen3.5-2B-Q4_K_M.gguf', 'contextLength': 65536,
       'maxTokens': 8192, 'reasoning': False, 'imageInput': 'unknown'}
NEW = {**OLD, 'model': 'Qwen3.5-9B-Q4_K_M.gguf'}


@pytest.fixture
def split_route(controller, monkeypatch):
    config_file, state, calls, call = controller
    state['contract'] = copy.deepcopy(OLD)
    env = {'PIXEL_OPENWEBUI_KEY': 'fixture', 'GPU_BACKEND': 'apple',
           'ODS_MODE': 'local', 'GGUF_FILE': NEW['model'],
           'LLM_MODEL': 'qwen3.5-9b', 'CTX_SIZE': '65536', 'MAX_CONTEXT': '65536'}
    monkeypatch.setattr(host.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(host, 'load_env', lambda _: dict(env))
    model_dir = host.INSTALL_DIR / 'models'
    model_dir.mkdir()
    (model_dir / NEW['model']).write_bytes(b'fixture model artifact')
    monkeypatch.setattr(host, '_active_model_directory', lambda _: model_dir)
    monkeypatch.setattr(host, '_prove_pixel_model_contract', lambda _, contract: contract == NEW)
    monkeypatch.setattr(host, '_verify_litellm_route', lambda _: None)
    monkeypatch.setattr(host, '_pixel_model_reasoning_capable', lambda *_: False)
    tx = host._begin_pixel_model_transaction(env)
    bootstrap = host.INSTALL_DIR / 'data/bootstrap-status.json'
    bootstrap.write_text(json.dumps({'status': 'complete', 'model': NEW['model'], 'percent': 100}))
    return env, tx, config_file, state, calls, call


def test_reported_held_bootstrap_split_is_not_recovered_by_current_proof(split_route):
    env, tx, _, state, calls, _ = split_route
    before = host._pixel_model_journal_path().read_bytes()

    result = host._recover_pixel_model_transaction(env)

    assert result['pending'] is True
    assert result['phase'] == 'held'
    assert state['contract'] == OLD and tx.target is None
    assert host._pixel_model_journal_path().read_bytes() == before
    assert 'model-apply' not in calls and 'model-finish' not in calls


def test_repair_endpoint_requires_authentication(monkeypatch):
    monkeypatch.setattr(host, 'check_auth', lambda _: False)
    monkeypatch.setattr(host, 'read_json_body', lambda _: pytest.fail('unauthenticated body'))
    host.AgentHandler._handle_model_repair_current_local(fixtures._ResponseHandler())


def test_repair_endpoint_cannot_interrupt_an_existing_lifecycle(monkeypatch):
    monkeypatch.setattr(host, 'check_auth', lambda _: True)
    monkeypatch.setattr(host, 'read_json_body', lambda _: {'transactionId': 'a' * 64})
    monkeypatch.setattr(host, '_begin_model_lifecycle', lambda _: (False, {'operation': 'model_activation'}))
    monkeypatch.setattr(host, '_end_model_lifecycle', lambda _: pytest.fail('released another owner'))
    monkeypatch.setattr(host, '_repair_held_pixel_local_route', lambda *_args, **_kwargs: pytest.fail('repair dispatched'))
    handler = fixtures._ResponseHandler()
    host.AgentHandler._handle_model_repair_current_local(handler)
    assert handler.response_code == 409


@pytest.mark.parametrize('failure', [OSError, ValueError, RuntimeError, TypeError])
def test_initial_proof_defers_when_durable_custody_is_unreadable(monkeypatch, failure):
    def unreadable():
        raise failure('invalid saved state')
    monkeypatch.setattr(host, '_pixel_model_recovery_status', unreadable)
    assert host._pixel_model_transition_pending_for_route_proof() is True


@pytest.mark.parametrize('body', [{}, {'transactionId': 'x'}, {'transactionId': True},
                                 {'transactionId': 'a' * 64, 'model': NEW['model']}])
def test_repair_endpoint_rejects_selection_and_invalid_ids(monkeypatch, body):
    monkeypatch.setattr(host, 'check_auth', lambda _: True)
    monkeypatch.setattr(host, 'read_json_body', lambda _: body)
    monkeypatch.setattr(host, '_begin_model_lifecycle', lambda _: pytest.fail('invalid admission'))
    handler = fixtures._ResponseHandler()
    host.AgentHandler._handle_model_repair_current_local(handler)
    assert handler.response_code == 400


@pytest.mark.parametrize('fail', [False, True])
def test_repair_endpoint_owns_lifecycle_and_releases_it_on_failure(monkeypatch, fail):
    actions = []
    monkeypatch.setattr(host, 'check_auth', lambda _: True)
    monkeypatch.setattr(host, 'read_json_body', lambda _: {'transactionId': 'a' * 64})
    monkeypatch.setattr(host, 'load_env', lambda _: {'fixture': 'env'})
    monkeypatch.setattr(host, '_begin_model_lifecycle', lambda kind: (actions.append('begin') or True, None))
    monkeypatch.setattr(host, '_end_model_lifecycle', lambda kind: actions.append('end'))
    from threading import Event
    monkeypatch.setattr(host, '_switchboard_initial_verify_cancel', Event())
    def repair(env, *, transaction_id):
        assert env == {'fixture': 'env'} and transaction_id == 'a' * 64
        actions.append('repair')
        if fail:
            raise RuntimeError('proof failed')
        return {'pending': False, 'phase': 'completed', 'transactionId': transaction_id, 'outcome': 'commit'}
    monkeypatch.setattr(host, '_repair_held_pixel_local_route', repair)
    handler = fixtures._ResponseHandler()
    host.AgentHandler._handle_model_repair_current_local(handler)
    assert handler.response_code == (409 if fail else 200)
    assert actions == ['begin', 'repair', 'end']
    assert host._switchboard_initial_verify_cancel.is_set()


@pytest.mark.parametrize('phase', ['applying', 'applied', 'committing'])
def test_interrupted_repair_finishes_proven_target_without_reapplying(split_route, phase):
    env, tx, _, state, calls, _ = split_route
    tx.apply(NEW)
    tx._save(phase)
    result = host._repair_held_pixel_local_route(env, transaction_id=tx.id)
    assert result['outcome'] == 'commit' and not result['pending']
    assert state['contract'] == NEW
    assert calls.count('model-apply') == 1
    assert calls.count('model-finish') == 1


def test_unconfirmed_apply_is_never_replayed(split_route):
    env, tx, _, _, calls, _ = split_route
    tx.target = copy.deepcopy(NEW)
    tx._save('applying')
    before = host._pixel_model_journal_path().read_bytes()
    with pytest.raises(RuntimeError, match='unconfirmed'):
        host._repair_held_pixel_local_route(env, transaction_id=tx.id)
    assert host._pixel_model_journal_path().read_bytes() == before
    assert 'model-apply' not in calls and 'model-finish' not in calls


def test_completed_native_reply_can_be_acknowledged_without_replaying_finish(split_route):
    env, tx, _, state, calls, call = split_route
    tx.apply(NEW)
    tx._save('committing')
    call('model-finish', {'outcome': 'commit'}, config=env)
    result = host._repair_held_pixel_local_route(env, transaction_id=tx.id)
    assert result['outcome'] == 'commit' and not result['pending']
    assert state['contract'] == NEW
    assert calls.count('model-apply') == 1 and calls.count('model-finish') == 1


def test_failed_post_apply_proof_keeps_gates_and_can_be_recovered(split_route, monkeypatch):
    env, tx, _, state, calls, _ = split_route
    proofs = iter([True, False])
    monkeypatch.setattr(host, '_prove_pixel_model_contract', lambda *_: next(proofs))
    with pytest.raises(RuntimeError, match='changed during native repair'):
        host._repair_held_pixel_local_route(env, transaction_id=tx.id)
    assert state['pending'] is True and state['contract'] == NEW
    assert host._read_pixel_model_journal()['phase'] == 'applied'
    assert 'model-finish' not in calls
    monkeypatch.setattr(host, '_prove_pixel_model_contract', lambda *_: True)
    assert host._repair_held_pixel_local_route(env, transaction_id=tx.id)['pending'] is False
    assert calls.count('model-apply') == 1 and calls.count('model-finish') == 1


def test_explicit_repair_proves_current_route_then_completes_same_transaction(split_route):
    env, tx, _, state, calls, _ = split_route

    result = host._repair_held_pixel_local_route(env, transaction_id=tx.id)

    assert result == {'pending': False, 'phase': 'completed',
                      'transactionId': tx.id, 'outcome': 'commit'}
    journal = host._read_pixel_model_journal()
    assert journal['previous'] == OLD and journal['target'] == NEW
    assert journal['transactionId'] == tx.id and journal['outcome'] == 'commit'
    assert state['contract'] == NEW and state['pending'] is False
    assert calls.count('model-begin') == 1
    assert calls.count('model-apply') == 1
    assert calls.count('model-finish') == 1


def test_native_state_change_during_completion_probe_refuses_apply(split_route, monkeypatch):
    env, tx, _, state, calls, _ = split_route
    before = host._pixel_model_journal_path().read_bytes()
    def concurrent_change(_):
        state['revision'] = 'd' * 64
    monkeypatch.setattr(host, '_verify_litellm_route', concurrent_change)
    with pytest.raises(RuntimeError, match='state changed during proof'):
        host._repair_held_pixel_local_route(env, transaction_id=tx.id)
    assert host._pixel_model_journal_path().read_bytes() == before
    assert 'model-apply' not in calls and 'model-finish' not in calls


def test_changed_journal_is_never_overwritten_by_repair(split_route, monkeypatch):
    env, tx, _, _, calls, _ = split_route
    foreign = host._read_pixel_model_journal()
    foreign['transactionId'] = 'f' * 64
    def concurrent_change(_):
        host._pixel_model_journal_path().write_text(json.dumps(foreign))
    monkeypatch.setattr(host, '_verify_litellm_route', concurrent_change)
    with pytest.raises(RuntimeError, match='evidence changed before repair'):
        host._repair_held_pixel_local_route(env, transaction_id=tx.id)
    assert host._read_pixel_model_journal() == foreign
    assert 'model-apply' not in calls and 'model-finish' not in calls


@pytest.mark.parametrize('fault', ['wrong-id', 'foreign-owner', 'changed-config', 'unproved',
                                  'proof-drift', 'consumer-failure', 'remote', 'linux',
                                  'context-disagreement', 'changed-contract', 'missing-model',
                                  'symlink-model'])
def test_explicit_repair_refuses_without_mutation(split_route, monkeypatch, fault):
    env, tx, config_file, state, calls, _ = split_route
    requested_id = tx.id
    if fault == 'wrong-id':
        requested_id = 'f' * 64
    elif fault == 'foreign-owner':
        state['transactionId'] = 'f' * 64
    elif fault == 'changed-config':
        config_file.write_text('concurrent change')
    elif fault == 'unproved':
        monkeypatch.setattr(host, '_prove_pixel_model_contract', lambda *_: False)
    elif fault == 'proof-drift':
        def proof(*_):
            config_file.write_text('changed during inference')
            return True
        monkeypatch.setattr(host, '_prove_pixel_model_contract', proof)
    elif fault == 'consumer-failure':
        def consumer(_):
            raise RuntimeError('consumer not ready')
        monkeypatch.setattr(host, '_verify_litellm_route', consumer)
    elif fault == 'remote':
        env['ODS_MODE'] = 'cloud'
    elif fault == 'linux':
        monkeypatch.setattr(host.platform, 'system', lambda: 'Linux')
    elif fault == 'context-disagreement':
        env['CTX_SIZE'] = '32768'
    elif fault == 'changed-contract':
        state['contract'] = copy.deepcopy(NEW)
    elif fault in {'missing-model', 'symlink-model'}:
        artifact = host._active_model_directory(env) / NEW['model']
        artifact.unlink()
        if fault == 'symlink-model':
            artifact.symlink_to(config_file)
    before = host._pixel_model_journal_path().read_bytes()
    with pytest.raises(RuntimeError):
        host._repair_held_pixel_local_route(env, transaction_id=requested_id)
    assert host._pixel_model_journal_path().read_bytes() == before
    assert 'model-apply' not in calls and 'model-finish' not in calls
