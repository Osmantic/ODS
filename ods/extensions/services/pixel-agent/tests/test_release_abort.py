"""Real owner files and root coordinator; no installed services or credentials."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from test_installer_model_access import runtime as runtime_fixture  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'host'))
import access_release_transaction as owner
import pixel_access_mode as controller
import pixel_access_bridge as bridge
import settings_transaction as storage

pytestmark = pytest.mark.skipif(os.geteuid() != 0, reason='root-private temporary coordinator receipts')


@pytest.fixture
def recovery(runtime_fixture, tmp_path, monkeypatch):  # noqa: F811
    runtime = runtime_fixture
    config = tmp_path / 'openclaw.json'
    candidate = tmp_path / 'candidate.json'
    state = tmp_path / 'owner-state'
    state.mkdir(mode=0o700)
    enabled, baseline = controller._helper.enable({'agents': {'list': [{'id': 'pixel', 'model': 'old'}]}})
    config.write_text(json.dumps(enabled))
    config.chmod(0o600)
    candidate.write_bytes(config.read_bytes())  # Already-enabled renderer candidate is invalid.
    candidate.chmod(0o600)
    digest = lambda: controller._sha256_bytes(config.read_bytes())
    receipt = dict(version=1, status=controller.STATUS_ENABLED, baseline=baseline,
                   config_path=str(config), config_sha256=digest())
    controller._write_receipt(str(state), receipt, durable=True)
    pending = json.loads((runtime.state / 'transition.json').read_text())
    pending['start_config_sha256'] = digest()
    bridge.atomic_json(runtime.state / 'transition.json', pending)
    phases = dict(native='held', edge='held')

    def native(operation=None, *args, **kwargs):
        if operation == 'acquire': phases['native'] = 'held'
        if operation == 'release': phases['native'] = 'idle'
        return dict(phase=phases['native'], active=0, stopped=False, pid=123)

    def edge(operation=None, *args, **kwargs):
        if operation in ('acquire', 'recover'): phases['edge'] = 'held'
        if operation == 'release': phases['edge'] = 'idle'
        return dict(phase=phases['edge'], streams=0, revision='c' * 64)

    def prove(*_):
        bridge.atomic_json(runtime.state / 'verified.json', dict(pid=123,
            config_sha256=digest(), boundary='boundary', proof={'mode': 'full-access', 'executed': True}))

    def worker(operation='status', **kwargs):
        if operation == 'status':
            return dict(configured_status='full-access', config_sha256=digest())
        common = dict(transaction_id=kwargs['transaction_id'], expected_current=kwargs['config_hash'],
                      check_no_active_run=kwargs['busy'])
        if operation == 'release-baseline':
            return owner.baseline(str(config), str(state), **common)
        if operation == 'release-prepare':
            return owner.prepare(str(config), kwargs['candidate_path'], str(state),
                expected_candidate=kwargs['candidate_sha256'], expected_receipt=kwargs['receipt_sha256'],
                validate_config=lambda _: True, **common)
        assert operation == 'release-abort'
        return owner.abort_unprepared(str(config), str(state), expected_receipt=kwargs['receipt_sha256'],
            verify_runtime=lambda _: kwargs['verify_release'](), **common)

    runtime.native = Mock(side_effect=native)
    runtime.edge = Mock(side_effect=edge)
    runtime.worker = Mock(side_effect=worker)
    runtime.verify_held_mode = Mock(side_effect=prove)
    runtime.probe_held_mode = Mock(return_value=(dict(pid=123,
        proof={'mode': 'full-access', 'executed': True}), 'boundary'))
    return runtime, config, candidate, state, receipt, phases


def reject(recovery):
    runtime, _, candidate, _, _, _ = recovery
    with pytest.raises(bridge.AccessError, match='release-preparation-failed'):
        runtime.prepare_release_access('a' * 64, str(candidate), controller._sha256_bytes(candidate.read_bytes()))
    assert not (runtime.state / 'release-prepared.json').exists()


def test_rejected_candidate_safe_abort_then_corrected_candidate_is_accepted(recovery):
    runtime, config, candidate, state, receipt, phases = recovery
    reject(recovery)
    original = config.read_bytes()
    baseline = json.loads((runtime.state / 'release-baseline.json').read_text())
    assert baseline['receiptSha256'] == controller._sha256_bytes(storage._encoded(receipt))
    assert not (state / owner.JOURNAL).exists()
    assert runtime.abort_release_access('a' * 64) == {'configSha256': controller._sha256_bytes(original)}
    assert phases == {'native': 'idle', 'edge': 'idle'}
    assert config.read_bytes() == original
    assert controller._load_receipt(str(state)) == receipt
    assert not (runtime.state / 'transition.json').exists()
    # The next attempt is a new normal admission transaction, not a widened retry.
    bridge.atomic_json(runtime.state / 'transition.json', dict(kind='model', transaction_id='b' * 64,
        token='2' * 64, phase='held', edge_revision='c' * 64, configured_mode='full-access',
        start_config_sha256=controller._sha256_bytes(original)))
    phases.update(native='held', edge='held')
    candidate.write_text('{"agents":{"list":[{"id":"pixel","model":"new"}]}}')
    result = runtime.prepare_release_access('b' * 64, str(candidate), controller._sha256_bytes(candidate.read_bytes()))
    assert result['beforeSha'] == controller._sha256_bytes(original)
    assert (state / owner.JOURNAL).exists()


@pytest.mark.parametrize('fault', ['receipt', 'config', 'journal', 'journal-symlink', 'legacy',
                                  'baseline-symlink', 'prepared-symlink', 'proof', 'active'])
def test_abort_refuses_changed_or_prepared_or_unproved_state(recovery, fault):
    runtime, config, _, state, receipt, phases = recovery
    reject(recovery)
    if fault == 'receipt':
        controller._write_receipt(str(state), dict(receipt, config_sha256='8' * 64))
    elif fault == 'config':
        config.write_bytes(config.read_bytes() + b' ')
    elif fault == 'journal':
        (state / owner.JOURNAL).write_text('{}')
    elif fault == 'journal-symlink':
        (state / owner.JOURNAL).symlink_to(state / 'absent')
    elif fault == 'legacy':
        (runtime.state / 'release-baseline.json').unlink()
    elif fault == 'baseline-symlink':
        path = runtime.state / 'release-baseline.json'
        path.rename(runtime.state / 'saved-baseline.json')
        path.symlink_to(runtime.state / 'saved-baseline.json')
    elif fault == 'prepared-symlink':
        (runtime.state / 'release-prepared.json').symlink_to(state / 'absent')
    elif fault == 'proof':
        runtime.probe_held_mode.side_effect = bridge.AccessError('runtime-proof-failed')
    else:
        runtime.native.side_effect = None
        runtime.native.return_value = dict(phase='held', active=1, stopped=False, pid=123)
    with pytest.raises(bridge.AccessError):
        runtime.abort_release_access('a' * 64)
    assert phases == {'native': 'held', 'edge': 'held'}
    assert (runtime.state / 'transition.json').exists()
    assert not (runtime.state / 'release-completed.json').exists()


@pytest.mark.parametrize('fault', ['receipt', 'config', 'journal'])
def test_owner_rechecks_original_state_after_fresh_proof(recovery, fault):
    runtime, config, _, state, receipt, phases = recovery
    reject(recovery)
    def race(*_):
        if fault == 'receipt':
            controller._write_receipt(str(state), dict(receipt, config_sha256='7' * 64))
        elif fault == 'config':
            config.write_bytes(config.read_bytes() + b' ')
        else:
            (state / owner.JOURNAL).write_text('{}')
        return dict(pid=123, proof={'mode': 'full-access', 'executed': True}), 'boundary'
    runtime.probe_held_mode.side_effect = race
    with pytest.raises(bridge.AccessError):
        runtime.abort_release_access('a' * 64)
    assert phases == {'native': 'held', 'edge': 'held'}
    assert not (runtime.state / 'release-completed.json').exists()


def test_mutation_between_baseline_and_prepare_cannot_be_aborted(recovery):
    runtime, _, candidate, state, receipt, phases = recovery
    original_worker = runtime.worker.side_effect

    def race(operation='status', **kwargs):
        result = original_worker(operation, **kwargs)
        if operation == 'release-baseline':
            controller._write_receipt(str(state), dict(receipt, config_sha256='9' * 64))
        return result

    runtime.worker.side_effect = race
    candidate.write_text('{"agents":{"list":[{"id":"pixel"}]}}')
    reject(recovery)
    assert not (state / owner.JOURNAL).exists()
    with pytest.raises(bridge.AccessError):
        runtime.abort_release_access('a' * 64)
    assert phases == {'native': 'held', 'edge': 'held'}


def test_lost_prepare_reply_retains_owner_journal_and_allows_only_same_candidate(recovery):
    runtime, _, candidate, state, _, phases = recovery
    candidate.write_text('{"agents":{"list":[{"id":"pixel"}]}}')
    original_worker = runtime.worker.side_effect
    lost = True

    def lose(operation='status', **kwargs):
        nonlocal lost
        result = original_worker(operation, **kwargs)
        if operation == 'release-prepare' and lost:
            lost = False
            raise OSError('lost reply after durable journal')
        return result

    runtime.worker.side_effect = lose
    reject(recovery)
    journal = (state / owner.JOURNAL).read_bytes()
    with pytest.raises(bridge.AccessError):
        runtime.abort_release_access('a' * 64)
    assert (state / owner.JOURNAL).read_bytes() == journal
    assert phases == {'native': 'held', 'edge': 'held'}
    runtime.prepare_release_access('a' * 64, str(candidate), controller._sha256_bytes(candidate.read_bytes()))
    assert (state / owner.JOURNAL).read_bytes() == journal


def test_abort_reply_loss_reproves_and_releases_same_transaction(recovery, monkeypatch):
    runtime, _, _, _, _, phases = recovery
    reject(recovery)
    original = bridge.atomic_json
    fail = True

    def lost(path, value):
        nonlocal fail
        original(path, value)
        if path.name == 'release-completed.json' and fail:
            fail = False
            raise OSError('lost root completion reply')

    monkeypatch.setattr(bridge, 'atomic_json', lost)
    with pytest.raises(bridge.AccessError):
        runtime.abort_release_access('a' * 64)
    assert phases == {'native': 'held', 'edge': 'held'}
    runtime.abort_release_access('a' * 64)
    assert runtime.probe_held_mode.call_count == 2
    assert phases == {'native': 'idle', 'edge': 'idle'}
    # An already released exact completion is safe to repeat, with no new hold.
    runtime.abort_release_access('a' * 64)
    assert phases == {'native': 'idle', 'edge': 'idle'}
