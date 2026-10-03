"""Real private files and injected interruption between the two publications."""
import copy
import ast
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import types

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'bin'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
import access_release_transaction as migration
import pixel_access_mode as controller
import model_transaction
import provider_transaction
import settings_transaction
import pixel_access_protocol as protocol
from pixel_settings.contract import SettingsError


@pytest.fixture
def prepared(tmp_path):
    tmp_path.chmod(0o700)
    config = tmp_path / 'openclaw.json'
    candidate = tmp_path / 'candidate.json'
    state = tmp_path / 'state'
    state.mkdir(mode=0o700)
    base = {'agents': {'list': [{'id': 'pixel', 'model': 'previous'}]}}
    enabled, baseline = controller._helper.enable(base)
    config.write_text(json.dumps(enabled))
    config.chmod(0o600)
    next_config = copy.deepcopy(base)
    next_config['agents']['list'][0]['model'] = 'next'
    candidate.write_text(json.dumps(next_config))
    candidate.chmod(0o600)
    receipt = dict(version=controller.CONTROLLER_VERSION, status=controller.STATUS_ENABLED,
                   baseline=baseline, config_path=str(config),
                   config_sha256=controller._sha256_bytes(config.read_bytes()))
    controller._write_receipt(str(state), receipt)
    kwargs = dict(transaction_id='a' * 64, validate_config=lambda _: True,
                  check_no_active_run=lambda: False)
    migration.prepare(str(config), str(candidate), str(state),
        expected_receipt=controller._sha256_bytes(settings_transaction._encoded(receipt)), expected_current=receipt['config_sha256'],
        expected_candidate=controller._sha256_bytes(candidate.read_bytes()), **kwargs)
    return config, state, kwargs, config.read_bytes(), receipt, next_config


def test_preparation_does_not_publish_and_blocks_other_writers(prepared):
    config, state, _, before, receipt, _ = prepared
    assert config.read_bytes() == before
    assert controller._load_receipt(str(state)) == receipt
    for check in [controller._reject_pending_settings, model_transaction._guards,
                  provider_transaction._guards, settings_transaction._no_pending_access]:
        with pytest.raises(Exception, match='release|transition'):
            check(str(state))


@pytest.mark.parametrize('permission_changed', [False, True])
def test_next_release_uses_fresh_config_proof_after_runtime_overlay(prepared, permission_changed):
    config, state, kwargs, _, receipt, _ = prepared
    # Complete the original migration, then make a normal unrelated runtime
    # edit. The access controller intentionally keeps its restore receipt.
    migration.recover(str(config), str(state), outcome='apply', **kwargs)
    migration.finish(str(config), str(state), transaction_id=kwargs['transaction_id'],
                     outcome='apply', verify_runtime=lambda _: 'verified',
                     check_no_active_run=lambda: False)
    receipt = controller._load_receipt(str(state))
    current = json.loads(config.read_text())
    current['diagnostics'] = {'stuckSessionWarnMs': 123456}
    if permission_changed:
        current['agents']['list'][0]['tools']['exec']['host'] = 'sandbox'
    config.write_text(json.dumps(current))
    live_before = config.read_bytes()
    digest = controller._sha256_bytes(live_before)
    assert digest != receipt['config_sha256']
    candidate = config.with_name('candidate.json')
    next_kwargs = dict(kwargs, transaction_id='c' * 64)
    with pytest.raises(SettingsError, match='input-changed'):
        migration.prepare(str(config), str(candidate), str(state),
            expected_receipt=controller._sha256_bytes(settings_transaction._encoded(receipt)), expected_current=receipt['config_sha256'],
            expected_candidate=controller._sha256_bytes(candidate.read_bytes()), **next_kwargs)
    assert not (state / migration.JOURNAL).exists()
    def prepare_next():
        return migration.prepare(str(config), str(candidate), str(state),
            expected_receipt=controller._sha256_bytes(settings_transaction._encoded(receipt)), expected_current=digest,
            expected_candidate=controller._sha256_bytes(candidate.read_bytes()), **next_kwargs)
    if permission_changed:
        with pytest.raises(controller._helper.MigrationError, match='not fully enabled'):
            prepare_next()
        assert not (state / migration.JOURNAL).exists()
    else:
        assert prepare_next()['beforeSha'] == digest
        migration.recover(str(config), str(state), outcome='rollback', **next_kwargs)
    assert config.read_bytes() == live_before
    assert controller._load_receipt(str(state)) == receipt


@pytest.mark.parametrize('change', [None, 'candidate', 'transaction', 'backup'])
def test_prepare_retry_preserves_exact_pending_plan(prepared, change):
    config, state, kwargs, before, receipt, _ = prepared
    candidate = config.with_name('candidate.json')
    journal_before = (state / migration.JOURNAL).read_bytes()
    if change == 'candidate':
        candidate.write_text('{"agents":{"list":[{"id":"pixel","model":"different"}]}}')
    elif change == 'transaction':
        kwargs = dict(kwargs, transaction_id='b' * 64)
    elif change == 'backup':
        (state / migration.FILES[1]).write_bytes(b'{}')
    def retry():
        return migration.prepare(str(config), str(candidate), str(state),
            expected_receipt=controller._sha256_bytes(settings_transaction._encoded(receipt)), expected_current=receipt['config_sha256'],
            expected_candidate=controller._sha256_bytes(candidate.read_bytes()), **kwargs)
    if change is None:
        result = retry()
        assert result['beforeSha'] == receipt['config_sha256']
    else:
        with pytest.raises(Exception, match='recovery-required'):
            retry()
    assert (state / migration.JOURNAL).read_bytes() == journal_before
    assert config.read_bytes() == before
    assert controller._load_receipt(str(state)) == receipt


@pytest.mark.parametrize('recovery', ['apply', 'rollback'])
def test_crash_after_config_before_receipt_is_recoverable(prepared, monkeypatch, recovery):
    config, state, kwargs, before, receipt, candidate = prepared
    original = controller._write_receipt
    def interrupted(*_, **__):
        raise OSError('fixture interruption')
    monkeypatch.setattr(controller, '_write_receipt', interrupted)
    with pytest.raises(OSError, match='fixture interruption'):
        migration.recover(str(config), str(state), outcome='apply', **kwargs)
    assert config.read_bytes() != before
    assert controller._load_receipt(str(state)) == receipt
    monkeypatch.setattr(controller, '_write_receipt', original)
    migration.recover(str(config), str(state), outcome=recovery, **kwargs)
    # Replay after both publications is also safe; root proof has not yet
    # removed the journal or reopened admission.
    migration.recover(str(config), str(state), outcome=recovery, **kwargs)
    updated = controller._load_receipt(str(state))
    if recovery == 'rollback':
        assert config.read_bytes() == before and updated == receipt
    else:
        restored, _ = controller._helper.restore(json.loads(config.read_bytes()), updated['baseline'])
        assert restored == candidate
        assert updated['config_sha256'] == controller._sha256_bytes(config.read_bytes())
    assert (state / migration.JOURNAL).exists()


def test_unrelated_config_change_is_not_overwritten(prepared):
    config, state, kwargs, _, _, _ = prepared
    changed = b'{"unrelated":"change"}'
    config.write_bytes(changed)
    with pytest.raises(Exception, match='state-changed'):
        migration.recover(str(config), str(state), outcome='rollback', **kwargs)
    assert config.read_bytes() == changed


def test_changed_backup_is_rejected(prepared):
    config, state, kwargs, before, _, _ = prepared
    (state / migration.FILES[1]).write_bytes(b'{}')
    with pytest.raises(Exception, match='backup-changed'):
        migration.recover(str(config), str(state), outcome='apply', **kwargs)
    assert config.read_bytes() == before


@pytest.mark.parametrize('outcome', ['apply', 'rollback'])
def test_finish_requires_proof_of_published_state(prepared, outcome):
    config, state, kwargs, _, _, _ = prepared
    migration.recover(str(config), str(state), outcome=outcome, **kwargs)
    verified = []
    def proof(digest):
        verified.append(digest)
        assert digest == controller._sha256_bytes(config.read_bytes())
        assert (state / migration.JOURNAL).exists()
        return 'verified'
    result = migration.finish(str(config), str(state),
        transaction_id=kwargs['transaction_id'], outcome=outcome,
        verify_runtime=proof, check_no_active_run=lambda: False)
    assert verified == [result['configSha256']]
    assert not (state / migration.JOURNAL).exists()
    assert json.loads((state / migration.COMPLETED).read_bytes()) == result
    controller._reject_pending_settings(str(state))


@pytest.mark.parametrize('proof_result', ['rejected', 'unavailable', None, True])
def test_failed_proof_keeps_recovery(prepared, proof_result):
    config, state, kwargs, _, _, _ = prepared
    migration.recover(str(config), str(state), outcome='apply', **kwargs)
    with pytest.raises(Exception, match='runtime-unverified'):
        migration.finish(str(config), str(state),
            transaction_id=kwargs['transaction_id'], outcome='apply',
            verify_runtime=lambda _: proof_result, check_no_active_run=lambda: False)
    assert (state / migration.JOURNAL).exists()
    assert not (state / migration.COMPLETED).exists()


def test_change_during_runtime_proof_keeps_recovery(prepared):
    config, state, kwargs, _, _, _ = prepared
    migration.recover(str(config), str(state), outcome='apply', **kwargs)
    def proof(_):
        config.write_bytes(b'{"changed":true}')
        return 'verified'
    with pytest.raises(Exception, match='state-changed'):
        migration.finish(str(config), str(state),
            transaction_id=kwargs['transaction_id'], outcome='apply',
            verify_runtime=proof, check_no_active_run=lambda: False)
    assert (state / migration.JOURNAL).exists()


def test_failed_journal_removal_restores_recovery(prepared, monkeypatch):
    config, state, kwargs, _, _, _ = prepared
    migration.recover(str(config), str(state), outcome='apply', **kwargs)
    def interrupted(sd, name):
        (Path(sd) / name).unlink()
        raise OSError('directory sync failed')
    monkeypatch.setattr(settings_transaction, '_remove', interrupted)
    with pytest.raises(OSError, match='directory sync failed'):
        migration.finish(str(config), str(state),
            transaction_id=kwargs['transaction_id'], outcome='apply',
            verify_runtime=lambda _: 'verified', check_no_active_run=lambda: False)
    assert (state / migration.JOURNAL).exists()
    migration.recover(str(config), str(state), outcome='rollback', **kwargs)


def test_lost_completion_reply_is_reproved_on_retry(prepared):
    config, state, kwargs, _, _, _ = prepared
    migration.recover(str(config), str(state), outcome='apply', **kwargs)
    proofs = []
    def verify(digest):
        proofs.append(digest)
        return 'verified'
    finish_args = dict(transaction_id=kwargs['transaction_id'], outcome='apply',
                       verify_runtime=verify, check_no_active_run=lambda: False)
    first = migration.finish(str(config), str(state), **finish_args)
    assert migration.finish(str(config), str(state), **finish_args) == first
    assert proofs == [first['configSha256']] * 2
    with pytest.raises(Exception, match='runtime-unverified'):
        migration.finish(str(config), str(state),
                         **dict(finish_args, verify_runtime=lambda _: 'unavailable'))


@pytest.mark.parametrize('mutation', ['transaction', 'outcome', 'receipt', 'config'])
def test_completion_retry_rejects_different_state(prepared, mutation):
    config, state, kwargs, _, _, _ = prepared
    migration.recover(str(config), str(state), outcome='apply', **kwargs)
    finish_args = dict(transaction_id=kwargs['transaction_id'], outcome='apply',
                       verify_runtime=lambda _: 'verified', check_no_active_run=lambda: False)
    migration.finish(str(config), str(state), **finish_args)
    if mutation == 'transaction':
        finish_args['transaction_id'] = 'b' * 64
    elif mutation == 'outcome':
        finish_args['outcome'] = 'rollback'
    elif mutation == 'config':
        config.write_bytes(b'{"changed":true}')
    else:
        receipt = controller._load_receipt(str(state))
        receipt['config_sha256'] = 'b' * 64
        controller._write_receipt(str(state), receipt)
    def forbidden(_):
        pytest.fail('proof called for an unrelated completion')
    finish_args['verify_runtime'] = forbidden
    with pytest.raises(Exception, match='completion-invalid'):
        migration.finish(str(config), str(state), **finish_args)


def test_worker_dispatch_publishes_and_verifies_exact_migration(prepared, monkeypatch):
    config, state, kwargs, _, _, _ = prepared
    worker = Path(migration.__file__).with_name('access_mode_worker.py')
    main = next(node for node in ast.parse(worker.read_text()).body
                if isinstance(node, ast.FunctionDef) and node.name == 'main')
    monkeypatch.setattr(controller, '_default_state_dir', lambda: str(state))
    def run(operation, replies):
        events = []
        request = dict(operation=operation, openclaw='/bin/true',
                       config_sha256=controller._sha256_bytes(config.read_bytes()), confirmed=False,
                       transaction_id=kwargs['transaction_id'], release_outcome='apply')
        scope = dict(protocol=protocol, controller=controller,
                     access_release_transaction=migration, SettingsError=SettingsError,
                     StoreError=ValueError, ModelError=ValueError, subprocess=subprocess,
                     emit=events.append,
                     os=types.SimpleNamespace(path=os.path, environ={'HOME': str(config.parent),
                                                                    'OPENCLAW_CONFIG_PATH': str(config)}),
                     sys=types.SimpleNamespace(stdin=io.StringIO(json.dumps(request) + '\n' + replies)))
        exec(compile(ast.Module(body=[main], type_ignores=[]), str(worker), 'exec'), scope)
        scope['main']()
        return events
    applied = run('release-recover', 'false\n')
    assert applied == [{'hook': 'busy'}, {'result': {
        'configSha256': controller._sha256_bytes(config.read_bytes())}}]
    denied = run('release-finish', 'false\n"unavailable"\n')
    assert denied[-1] == {'error': 'access-release-runtime-unverified'}
    assert (state / migration.JOURNAL).exists()
    finished = run('release-finish', 'false\n"verified"\nfalse\n')
    assert finished == [{'hook': 'busy'}, {'hook': 'release-verify'}, {'hook': 'busy'},
                        {'result': applied[-1]['result']}]
    assert not (state / migration.JOURNAL).exists()
