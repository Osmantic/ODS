"""Installer access proof keeps admission held and rejects stale runtime facts."""
import contextlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / 'bin'))
import pixel_access_bridge as bridge

pytestmark = pytest.mark.skipif(not hasattr(os, 'geteuid') or os.geteuid() != 0,
                                reason='root-owned journal fixtures require POSIX root')


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    adapter = bridge.SystemdAccessBridge(tmp_path, 'k' * 64, state=tmp_path)
    pending = dict(kind='model', transaction_id='a' * 64, token='b' * 64,
                   phase='held', edge_revision='c' * 64,
                   configured_mode='full-access', start_config_sha256='e' * 64)
    bridge.atomic_json(tmp_path / 'transition.json', pending)
    monkeypatch.setattr(adapter, 'locked', lambda: contextlib.nullcontext())
    monkeypatch.setattr(adapter, 'bounded', lambda _: contextlib.nullcontext())
    monkeypatch.setattr(adapter, 'discover', Mock())
    monkeypatch.setattr(adapter, 'edge', Mock(return_value=dict(
        phase='held', streams=0, revision='c' * 64)))
    monkeypatch.setattr(adapter, 'native', Mock(return_value=dict(
        phase='held', active=0, stopped=False, pid=123)))
    monkeypatch.setattr(adapter, 'worker', Mock(return_value=dict(
        configured_status='full-access', config_sha256='e' * 64)))
    monkeypatch.setattr(adapter, 'unit_boundary', Mock(return_value='boundary'))

    def prove(token, mode):
        assert token == 'b' * 64 and mode == 'full-access'
        bridge.atomic_json(tmp_path / 'verified.json', dict(
            pid=123, config_sha256='e' * 64, boundary='boundary'))
    monkeypatch.setattr(adapter, 'verify_held_mode', Mock(side_effect=prove))
    return adapter


def assert_not_released(runtime):
    assert all(call.args[0] != 'release' for call in runtime.native.call_args_list if call.args)
    assert all(call.args[0] != 'release' for call in runtime.edge.call_args_list if call.args)
    assert (runtime.state / 'transition.json').exists()
    assert not (runtime.state / 'model-promotion-completed.json').exists()


def test_fresh_proof_is_bounded_and_does_not_complete_transaction(runtime):
    result = runtime.verify_installer_model_access('a' * 64)
    assert result == dict(mode='full-access', pid=123, config_sha256='e' * 64)
    runtime.verify_held_mode.assert_called_once_with('b' * 64, 'full-access')
    runtime.discover.assert_called_once_with(allow_installing=True)
    assert json.loads((runtime.state / 'transition.json').read_text())['phase'] == 'held'
    assert_not_released(runtime)


@pytest.mark.parametrize('change', ['pid', 'configuration', 'boundary', 'native-open', 'edge-open', 'edge-revision'])
def test_change_after_probe_cannot_be_accepted(runtime, change):
    original = runtime.verify_held_mode.side_effect

    def prove(token, mode):
        original(token, mode)
        if change == 'pid':
            runtime.native.return_value['pid'] = 124
        elif change == 'configuration':
            runtime.worker.return_value = dict(configured_status='full-access', config_sha256='f' * 64)
        elif change == 'boundary':
            runtime.unit_boundary.return_value = 'changed'
        elif change == 'native-open':
            runtime.native.return_value['phase'] = 'idle'
        elif change == 'edge-open':
            runtime.edge.return_value['phase'] = 'idle'
        else:
            runtime.edge.return_value['revision'] = 'f' * 64
    runtime.verify_held_mode.side_effect = prove
    with pytest.raises(bridge.AccessError, match='runtime-proof-required'):
        runtime.verify_installer_model_access('a' * 64)
    assert json.loads((runtime.state / 'transition.json').read_text())['phase'] == 'error'
    assert_not_released(runtime)


@pytest.mark.parametrize('phase', ['idle', 'busy'])
def test_lost_external_hold_does_not_start_probe(runtime, phase):
    runtime.edge.return_value['phase'] = phase
    with pytest.raises(bridge.AccessError, match='model-lease-lost'):
        runtime.verify_installer_model_access('a' * 64)
    runtime.verify_held_mode.assert_not_called()
    runtime.native.assert_not_called()
    assert_not_released(runtime)


def test_other_transaction_rejected_before_runtime_access(runtime):
    with pytest.raises(bridge.AccessError, match='model-transaction-mismatch'):
        runtime.verify_installer_model_access('f' * 64)
    runtime.discover.assert_not_called()
    assert_not_released(runtime)


def test_unprivileged_invocation_rejected(runtime, monkeypatch):
    monkeypatch.setattr(bridge.os, 'geteuid', lambda: 1000)
    with pytest.raises(bridge.AccessError, match='root-service-required'):
        runtime.verify_installer_model_access('a' * 64)
    runtime.discover.assert_not_called()
    assert_not_released(runtime)


def test_failed_probe_leaves_recoverable_error(runtime):
    runtime.verify_held_mode.side_effect = bridge.AccessError('runtime-proof-failed')
    with pytest.raises(bridge.AccessError, match='runtime-proof-failed'):
        runtime.verify_installer_model_access('a' * 64)
    journal = json.loads((runtime.state / 'transition.json').read_text())
    assert journal['phase'] == 'error' and journal['error'] == 'runtime-proof-failed'
    assert_not_released(runtime)


def test_interrupted_edge_uses_same_transaction_recovery(runtime):
    runtime.edge.side_effect = [dict(phase='interrupted', streams=0, revision='f' * 64),
                               dict(phase='held', streams=0, revision='c' * 64),
                               dict(phase='held', streams=0, revision='c' * 64)]
    runtime.verify_installer_model_access('a' * 64)
    assert runtime.edge.call_args_list[1].args == ('recover', 'b' * 64, 'f' * 64)
    assert_not_released(runtime)


@pytest.mark.parametrize('phase', ['acquiring', 'draining', 'finishing', 'releasing', 'native-released'])
def test_verification_cannot_adopt_other_transition_phases(runtime, phase):
    path = runtime.state / 'transition.json'
    journal = json.loads(path.read_text())
    journal['phase'] = phase
    bridge.atomic_json(path, journal)
    with pytest.raises(bridge.AccessError, match='model-lease-lost'):
        runtime.verify_installer_model_access('a' * 64)
    runtime.discover.assert_not_called()
    assert json.loads(path.read_text())['phase'] == phase


def test_configured_mode_change_before_probe_is_rejected(runtime):
    runtime.worker.return_value['configured_status'] = 'sandboxed'
    with pytest.raises(bridge.AccessError, match='configured-mode-changed'):
        runtime.verify_installer_model_access('a' * 64)
    runtime.verify_held_mode.assert_not_called()
    assert_not_released(runtime)


def release_worker(runtime, monkeypatch, change=None):
    bridge.atomic_json(runtime.state / 'release-prepared.json', dict(
        transactionId='a' * 64, candidateSha256='f' * 64, beforeSha='e' * 64, afterSha='e' * 64))
    monkeypatch.setattr(runtime, 'probe_held_mode', Mock(return_value=(
        {'pid': 123, 'proof': {'mode': 'full-access'}}, 'boundary')))
    def complete(operation='status', **kwargs):
        assert operation == 'release-finish', 'nested owner worker during migration'
        assert kwargs['config_hash'] == 'e' * 64
        assert kwargs['transaction_id'] == 'a' * 64
        assert kwargs['busy']() is False
        if change != 'missing-proof':
            assert kwargs['verify_release']() == 'verified'
        if change == 'pid':
            runtime.native.return_value['pid'] = 124
        elif change == 'boundary':
            runtime.unit_boundary.return_value = 'changed'
        elif change == 'edge':
            runtime.edge.return_value['phase'] = 'idle'
        elif change == 'busy':
            runtime.native.return_value['active'] = 1
        return {'configSha256': ('f' if change == 'digest' else 'e') * 64}
    runtime.worker.side_effect = complete


@pytest.mark.parametrize('outcome', ['apply', 'rollback'])
def test_release_completion_keeps_model_admission_held(runtime, monkeypatch, outcome):
    release_worker(runtime, monkeypatch)
    assert runtime.finish_release_access('a' * 64, 'e' * 64, outcome) == {'configSha256': 'e' * 64}
    assert runtime.worker.call_args.kwargs['release_outcome'] == outcome
    runtime.worker.assert_called_once()
    runtime.verify_held_mode.assert_not_called()
    assert json.loads((runtime.state / 'verified.json').read_text())['pid'] == 123
    assert_not_released(runtime)
    assert json.loads((runtime.state / 'release-completed.json').read_text()) == dict(
        transactionId='a' * 64, configSha256='e' * 64, outcome=outcome)


@pytest.mark.parametrize('completion', [None, 'other-config', 'other-outcome', 'other-transaction'])
def test_generic_finish_cannot_skip_release_completion(runtime, completion):
    bridge.atomic_json(runtime.state / 'release-intent.json', dict(
        transactionId='a' * 64, candidateSha256='f' * 64))
    if completion is not None:
        record = dict(transactionId='a' * 64, configSha256='e' * 64, outcome='apply')
        key = {'other-config': 'configSha256', 'other-outcome': 'outcome',
               'other-transaction': 'transactionId'}[completion]
        record[key] = 'rollback' if key == 'outcome' else 'f' * 64
        bridge.atomic_json(runtime.state / 'release-completed.json', record)
    with pytest.raises(bridge.AccessError, match='release-completion-required'):
        runtime.model_finish(dict(transaction_id='a' * 64, outcome='applied'))
    runtime.verify_held_mode.assert_not_called()
    assert_not_released(runtime)


def test_prepare_lost_worker_reply_blocks_generic_finish(runtime):
    def worker(operation='status', **kwargs):
        if operation == 'release-baseline':
            return dict(configSha256='e' * 64, receiptSha256='9' * 64)
        if operation == 'status':
            return dict(configured_status='full-access', config_sha256='e' * 64)
        assert operation == 'release-prepare'
        assert (runtime.state / 'release-intent.json').exists()
        raise OSError('lost worker reply')
    runtime.worker.side_effect = worker
    with pytest.raises(bridge.AccessError, match='release-preparation-failed'):
        runtime.prepare_release_access('a' * 64, '/home/fixture/candidate.json', 'f' * 64)
    with pytest.raises(bridge.AccessError, match='release-completion-required'):
        runtime.model_finish(dict(transaction_id='a' * 64, outcome='applied'))
    assert_not_released(runtime)


def test_release_finish_reproves_after_lost_root_completion_reply(runtime, monkeypatch):
    release_worker(runtime, monkeypatch)
    original = bridge.atomic_json
    interrupted = False
    def persist(path, value):
        nonlocal interrupted
        original(path, value)
        if path.name == 'release-completed.json' and not interrupted:
            interrupted = True
            raise OSError('reply lost after durable completion')
    monkeypatch.setattr(bridge, 'atomic_json', persist)
    with pytest.raises(bridge.AccessError, match='release-verification-failed'):
        runtime.finish_release_access('a' * 64, 'e' * 64, 'apply')
    assert json.loads((runtime.state / 'transition.json').read_text())['phase'] == 'error'
    assert_not_released(runtime)
    assert runtime.finish_release_access('a' * 64, 'e' * 64, 'apply') == {'configSha256': 'e' * 64}
    assert runtime.probe_held_mode.call_count == 2
    assert json.loads((runtime.state / 'transition.json').read_text())['phase'] == 'held'
    assert_not_released(runtime)


@pytest.mark.parametrize('case', ['verified', 'other-transaction', 'missing-intent', 'invalid'])
def test_status_exposes_only_matching_root_release_completion(runtime, case):
    if case != 'missing-intent':
        bridge.atomic_json(runtime.state / 'release-intent.json', dict(
            transactionId='a' * 64, candidateSha256='f' * 64))
    completion = dict(transactionId=('b' if case == 'other-transaction' else 'a') * 64,
                      configSha256='e' * 64, outcome='apply')
    if case == 'invalid':
        completion['extra'] = 'not allowed'
    bridge.atomic_json(runtime.state / 'release-completed.json', completion)
    if case == 'invalid':
        with pytest.raises(bridge.AccessError, match='release-recovery-required'):
            runtime.model_status()
    else:
        status = runtime.model_status()
        if case == 'verified':
            assert status['release_completion'] == dict(outcome='apply', config_sha256='e' * 64)
        else:
            assert 'release_completion' not in status
    assert_not_released(runtime)


@pytest.mark.parametrize('change', ['missing-proof', 'pid', 'boundary', 'edge', 'busy', 'digest'])
def test_release_completion_drift_keeps_root_recovery(runtime, monkeypatch, change):
    release_worker(runtime, monkeypatch, change)
    with pytest.raises(bridge.AccessError):
        runtime.finish_release_access('a' * 64, 'e' * 64, 'apply')
    assert json.loads((runtime.state / 'transition.json').read_text())['phase'] == 'error'
    assert not (runtime.state / 'verified.json').exists()
    assert_not_released(runtime)


def test_release_completion_rejects_other_transaction(runtime):
    with pytest.raises(bridge.AccessError, match='model-transaction-mismatch'):
        runtime.finish_release_access('f' * 64, 'e' * 64, 'apply')
    runtime.worker.assert_not_called()


def test_release_completion_requires_root(runtime, monkeypatch):
    monkeypatch.setattr(bridge.os, 'geteuid', lambda: 1000)
    with pytest.raises(bridge.AccessError, match='root-service-required'):
        runtime.finish_release_access('a' * 64, 'e' * 64, 'apply')
    runtime.worker.assert_not_called()


def test_release_prepare_binds_candidate_without_releasing(runtime):
    def worker(operation='status', **kwargs):
        if operation == 'release-baseline':
            return dict(configSha256='e' * 64, receiptSha256='9' * 64)
        if operation == 'status':
            return dict(configured_status='full-access', config_sha256='e' * 64)
        assert operation == 'release-prepare'
        assert kwargs['candidate_path'] == '/home/fixture/dist/openclaw.json'
        assert kwargs['candidate_sha256'] == 'f' * 64
        assert kwargs['config_hash'] == 'e' * 64
        assert kwargs['busy']() is False
        return {'beforeSha': 'e' * 64, 'afterSha': '1' * 64}
    runtime.worker.side_effect = worker
    result = runtime.prepare_release_access('a' * 64, '/home/fixture/dist/openclaw.json', 'f' * 64)
    assert result == {'beforeSha': 'e' * 64, 'afterSha': '1' * 64}
    assert json.loads((runtime.state / 'release-prepared.json').read_text()) == {
        'transactionId': 'a' * 64, 'candidateSha256': 'f' * 64, **result}
    assert_not_released(runtime)


@pytest.mark.parametrize('path,digest', [('relative', 'f' * 64), ('/a/../b', 'f' * 64), ('/a', 'bad')])
def test_release_prepare_rejects_bad_candidate_before_runtime(runtime, path, digest):
    with pytest.raises(bridge.AccessError, match='invalid-release-candidate'):
        runtime.prepare_release_access('a' * 64, path, digest)
    runtime.discover.assert_not_called()
    runtime.native.assert_not_called()
    assert_not_released(runtime)


def test_release_prepare_lost_worker_reply_leaves_recovery(runtime):
    def worker(operation='status', **kwargs):
        if operation == 'release-baseline':
            return dict(configSha256='e' * 64, receiptSha256='9' * 64)
        if operation == 'status':
            return dict(configured_status='full-access', config_sha256='e' * 64)
        raise bridge.AccessError('owner-worker-timeout')
    runtime.worker.side_effect = worker
    with pytest.raises(bridge.AccessError, match='owner-worker-timeout'):
        runtime.prepare_release_access('a' * 64, '/home/fixture/dist/openclaw.json', 'f' * 64)
    assert json.loads((runtime.state / 'transition.json').read_text())['phase'] == 'error'
    assert not (runtime.state / 'release-prepared.json').exists()
    assert_not_released(runtime)


@pytest.mark.parametrize('outcome', ['apply', 'rollback'])
def test_release_publication_requires_stopped_gateway(runtime, outcome):
    bridge.atomic_json(runtime.state / 'release-prepared.json', dict(
        transactionId='a' * 64, candidateSha256='f' * 64, beforeSha='e' * 64, afterSha='1' * 64))
    runtime.gateway_service = Mock(spec=['assert_stopped'])
    expected = ('1' if outcome == 'apply' else 'e') * 64
    def worker(operation='status', **kwargs):
        if operation == 'release-baseline':
            return dict(configSha256='e' * 64, receiptSha256='9' * 64)
        if operation == 'status':
            return {'config_sha256': 'e' * 64}
        assert operation == 'release-recover'
        assert kwargs['busy']() is False
        assert kwargs['release_outcome'] == outcome
        return {'configSha256': expected}
    runtime.worker.side_effect = worker
    assert runtime.publish_release_access('a' * 64, outcome) == {'configSha256': expected}
    assert runtime.gateway_service.assert_stopped.call_count >= 3
    runtime.native.assert_not_called()
    assert_not_released(runtime)


def test_running_gateway_prevents_release_publication(runtime):
    bridge.atomic_json(runtime.state / 'release-prepared.json', dict(
        transactionId='a' * 64, candidateSha256='f' * 64, beforeSha='e' * 64, afterSha='1' * 64))
    runtime.gateway_service = Mock(spec=['assert_stopped'])
    runtime.gateway_service.assert_stopped.side_effect = bridge.AccessError('service-not-stopped')
    with pytest.raises(bridge.AccessError, match='service-not-stopped'):
        runtime.publish_release_access('a' * 64, 'apply')
    runtime.worker.assert_not_called()
    assert_not_released(runtime)
