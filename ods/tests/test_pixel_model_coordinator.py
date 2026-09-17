"""Contract tests for bin/pixel_model_coordinator.py.

The coordinator owns the model-contract transaction lifecycle
(`model-status`/`model-begin`/`model-apply`/`model-finish`) on top of the
SystemdAccessBridge: journaled phases, edge + native holds, verified config
projection, and commit/rollback release. A scripted FakeBridge drives the real
state machine — no production code is stubbed inside the coordinator itself.
"""
import contextlib
import hashlib
import json
import os
import stat
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import pixel_model_coordinator as mc
from pixel_access_bridge import AccessError
from pixel_model_contract import plan, projection

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX file custody')

HEX = 'a' * 64


def canonical(value):
    return (json.dumps(value, ensure_ascii=True, sort_keys=True,
                       separators=(',', ':'), allow_nan=False) + '\n').encode('ascii')


def openclaw_config(model='qwen3-4b', context=32768, output=4096):
    return {
        'agents': {'list': [{'id': 'pixel', 'model': 'ods-local/' + model,
                             'contextTokens': context}],
                   'defaults': {}},
        'models': {'providers': {
            'ods-local': {'models': [{'id': model, 'name': 'ODS Local ' + model,
                                      'contextWindow': context, 'maxTokens': output,
                                      'reasoning': False}]},
            'ods-gateway': {'models': [{'id': 'ods/current',
                                        'name': 'ODS Current (remote-70b)',
                                        'contextWindow': 131072, 'maxTokens': 8192,
                                        'reasoning': True}]}}},
        'plugins': {'entries': {'pixel-ods': {'enabled': True, 'config': {}}}},
    }


class FakeBridge:
    """Scripted stand-in for SystemdAccessBridge (edge/native holds, worker)."""

    def __init__(self, root, mode='sandboxed', busy=False):
        self.state = root / 'state'
        self.state.mkdir(mode=0o700)
        self.home = root / 'home'
        (self.home / '.openclaw').mkdir(parents=True, mode=0o700)
        self.owner = SimpleNamespace(pw_uid=os.getuid())
        self.native_origin = 'http://127.0.0.1:9'
        self.native_key = 'key'
        self._mode = mode
        self._busy = busy
        self._edge_seq = 0
        self._edge = {'phase': 'idle', 'revision': self._edge_revision()}
        self._native_rev = 0
        self._native = {'phase': 'idle', 'revision': 0, 'stopped': False}
        self._held_token = None
        self._pid = 4242
        self._started = 777
        self._pending = None
        self._completion = None
        self.calls = []
        # Fault-injection knobs for recovery/mismatch scenarios.
        self.fail_worker_once = set()
        self.apply_result_sha = None
        self.probe_mode = None
        self.tamper_readback_contract = None
        self.native_stopped = False
        self._write_config(openclaw_config())

    # --- file-backed config -------------------------------------------------
    @property
    def _config_path(self):
        return self.home / '.openclaw' / 'openclaw.json'

    def _write_config(self, document):
        self._config_path.write_bytes(canonical(document))
        os.chmod(self._config_path, 0o600)

    def _config(self):
        raw = self._config_path.read_bytes()
        return json.loads(raw), hashlib.sha256(raw).hexdigest()

    # --- bridge surface -----------------------------------------------------
    def bounded(self, _seconds):
        return contextlib.nullcontext()

    def locked(self):
        return contextlib.nullcontext()

    def discover(self):
        return None

    def pending(self):
        path = self.state / 'transition.json'
        return json.loads(path.read_text()) if path.exists() else None

    def inspect(self):
        return {'busy': self._busy, 'configured_mode': self._mode,
                '_edge': dict(self._edge)}

    def unit_boundary(self):
        return 'boundary-v1'

    def settings_source(self):
        return None

    def command(self, args, timeout=20):
        self.calls.append(('command', list(args)))
        if args[:2] == ['systemctl', 'restart']:
            self._pid += 1
            self._started += 1
            return ''
        return (f'MainPID={self._pid}\nActiveState=active\n'
                f'ExecMainStartTimestampMonotonic={self._started}\n')

    def _edge_revision(self):
        self._edge_seq += 1
        return hashlib.sha256(f'edge-{self._edge_seq}'.encode()).hexdigest()

    def edge(self, operation=None, token=None, revision=None):
        self.calls.append(('edge', operation))
        if operation in ('acquire', 'recover'):
            self._held_token = token
            self._edge = {'phase': 'held', 'revision': self._edge_revision(),
                          'capability': 'available', 'streams': 0}
        elif operation == 'release':
            self._edge = {'phase': 'idle', 'revision': self._edge_revision()}
        return dict(self._edge)

    def native(self, operation=None, token=None):
        self.calls.append(('native', operation))
        if self.native_stopped:
            return {'stopped': True}
        if operation == 'acquire':
            self._native_rev += 1
            self._native = {'phase': 'held', 'revision': self._native_rev,
                            'stopped': False, 'active': False}
        elif operation == 'release':
            self._native = {'phase': 'idle', 'revision': self._native_rev + 1,
                            'stopped': False}
        elif operation == 'probe':
            return {'pid': self._pid,
                    'proof': {'mode': self.probe_mode or self._mode}}
        return dict(self._native)

    def owns_native_hold(self, snapshot, token):
        return snapshot.get('phase') == 'held' and token == self._held_token

    def stopped_native(self, token):
        return {'stopped': False}

    def provision_probe(self):
        return None

    def http(self, origin, path, key, body=None, timeout=20):
        self.calls.append(('http', body and body.get('operation')))
        current, _sha = self._config()
        projected = projection(current)
        return {'schemaVersion': 1, 'pid': self._pid,
                'revision': self._native['revision'], 'source': 'current-model-contract',
                'observedAt': '2026-09-17T00:00:00Z',
                'contract': self.tamper_readback_contract or projected['contract'],
                'limits': projected['limits']}

    def worker(self, operation='status', *, confirmed=False, config_hash=None,
               transaction_id=None, busy=None, restart=None, **kwargs):
        self.calls.append(('worker', operation))
        if operation in self.fail_worker_once:
            self.fail_worker_once.discard(operation)
            raise AccessError('worker-lost-reply')
        _config, config_sha = self._config()
        if operation == 'model-status':
            return {'configSha256': config_sha, 'pending': self._pending is not None,
                    'transactionId': self._pending, 'completion': self._completion}
        if operation == 'model-begin':
            self._pending = transaction_id
            return {}
        if operation == 'model-apply':
            before = json.loads((self.state / 'model-before.json').read_text())
            proposed = plan(before, kwargs['model_target'])
            self._write_config(proposed)
            return {'configSha256': self.apply_result_sha or mc._sha(proposed)}
        if operation == 'model-rollback':
            before = json.loads((self.state / 'model-before.json').read_text())
            self._write_config(before)
            self._pending = None
            return {}
        if operation == 'model-finish':
            self._completion = {'transactionId': transaction_id,
                                'outcome': kwargs['model_outcome'],
                                'configSha256': config_sha}
            self._pending = None
            return {}
        raise AssertionError(f'unexpected worker op {operation}')


@pytest.fixture
def bridge(tmp_path):
    return FakeBridge(tmp_path)


class TestRequestValidation:
    def test_unknown_operation(self, bridge):
        with pytest.raises(AccessError) as exc:
            mc.control(bridge, 'model-restart')
        assert exc.value.code == 'invalid-model-operation'

    @pytest.mark.parametrize('req', [
        None, {}, {'revision': HEX}, {'transactionId': HEX, 'extra': 1},
        {'transactionId': 'not-hex', 'revision': HEX},
    ])
    def test_begin_request_shape(self, bridge, req):
        with pytest.raises(AccessError) as exc:
            mc.control(bridge, 'model-begin', req)
        assert exc.value.code == 'invalid-model-request'

    def test_apply_requires_valid_target(self, bridge):
        # target() raises ModelError, not AccessError — the contract validator's
        # own exception type crosses the boundary unchanged.
        from pixel_model_contract import ModelError
        with pytest.raises(ModelError, match='invalid-model-contract'):
            mc.control(bridge, 'model-apply',
                       {'transactionId': HEX, 'target': {'model': 'x'}})

    def test_finish_outcome_allowlist(self, bridge):
        for outcome in ('commit', 'rollback'):
            with pytest.raises(AccessError) as exc:
                mc.control(bridge, 'model-finish',
                           {'transactionId': HEX, 'outcome': outcome})
            assert exc.value.code == 'model-transaction-missing'
        with pytest.raises(AccessError) as exc:
            mc.control(bridge, 'model-finish',
                       {'transactionId': HEX, 'outcome': 'maybe'})
        assert exc.value.code == 'invalid-model-request'


class TestLifecycle:
    def test_status_ready_on_clean_state(self, bridge):
        result = mc.control(bridge, 'model-status')
        assert result['schemaVersion'] == 1 and result['status'] == 'ready'
        assert result['pending'] is False and result['transactionId'] is None
        assert result['contract']['model'] == 'qwen3-4b'
        assert 'token' not in json.dumps(result)

    def test_begin_holds_then_apply_then_commit(self, bridge):
        status = mc.control(bridge, 'model-status')
        tid = 'b' * 64
        held = mc.control(bridge, 'model-begin',
                          {'revision': status['revision'], 'transactionId': tid})
        assert held['status'] == 'held' and held['pending'] is True
        assert held['transactionId'] == tid
        target = {'model': 'swapped-8b', 'contextLength': 65536,
                  'maxTokens': 8192, 'reasoning': True}
        applied = mc.control(bridge, 'model-apply',
                             {'transactionId': tid, 'target': target})
        assert applied['status'] == 'applied'
        assert applied['contract'] == target
        done = mc.control(bridge, 'model-finish',
                        {'transactionId': tid, 'outcome': 'commit'})
        assert done['status'] == 'completed' and done['outcome'] == 'commit'
        assert done['transactionId'] == tid
        assert not (bridge.state / 'transition.json').exists()
        assert json.loads((bridge.state / 'model-completed.json').read_text()) == {
            'transactionId': tid, 'outcome': 'commit',
            'configSha256': mc._sha(bridge._config()[0])}
        # The applied config is durable and projects the target.
        assert projection(bridge._config()[0])['contract'] == target
        # Follow-up status reports the completed transaction.
        status = mc.control(bridge, 'model-status')
        assert status['status'] == 'completed' and status['transactionId'] == tid

    def test_rollback_restores_before_config(self, bridge):
        status = mc.control(bridge, 'model-status')
        tid = 'c' * 64
        before_sha = bridge._config()[1]
        mc.control(bridge, 'model-begin',
                   {'revision': status['revision'], 'transactionId': tid})
        mc.control(bridge, 'model-apply',
                   {'transactionId': tid, 'target': {'model': 'other-1b',
                                                     'contextLength': 16384,
                                                     'maxTokens': 2048,
                                                     'reasoning': False}})
        done = mc.control(bridge, 'model-finish',
                          {'transactionId': tid, 'outcome': 'rollback'})
        assert done['outcome'] == 'rollback'
        assert bridge._config()[1] == before_sha
        assert projection(bridge._config()[0])['contract']['model'] == 'qwen3-4b'

    def test_conflicting_transaction_rejected(self, bridge):
        status = mc.control(bridge, 'model-status')
        mc.control(bridge, 'model-begin',
                   {'revision': status['revision'], 'transactionId': 'd' * 64})
        with pytest.raises(AccessError) as exc:
            mc.control(bridge, 'model-apply',
                       {'transactionId': 'e' * 64,
                        'target': {'model': 'm', 'contextLength': 4096,
                                   'maxTokens': 1, 'reasoning': False}})
        assert exc.value.code == 'model-transaction-conflict'

    def test_begin_replay_is_idempotent_status(self, bridge):
        status = mc.control(bridge, 'model-status')
        tid = 'f' * 64
        mc.control(bridge, 'model-begin',
                   {'revision': status['revision'], 'transactionId': tid})
        again = mc.control(bridge, 'model-begin',
                           {'revision': status['revision'], 'transactionId': tid})
        assert again['status'] == 'held'

    def test_busy_runtime_refuses_begin(self, tmp_path):
        bridge = FakeBridge(tmp_path, busy=True)
        status = mc.control(bridge, 'model-status')
        with pytest.raises(AccessError) as exc:
            mc.control(bridge, 'model-begin',
                       {'revision': status['revision'], 'transactionId': HEX})
        assert exc.value.code == 'runtime-busy'

    def test_completed_transaction_rejects_replay(self, bridge):
        status = mc.control(bridge, 'model-status')
        tid = '1' * 64
        mc.control(bridge, 'model-begin',
                   {'revision': status['revision'], 'transactionId': tid})
        mc.control(bridge, 'model-finish', {'transactionId': tid, 'outcome': 'rollback'})
        with pytest.raises(AccessError) as exc:
            mc.control(bridge, 'model-begin',
                       {'revision': status['revision'], 'transactionId': tid})
        assert exc.value.code == 'model-transaction-completed'

    def test_finish_commit_idempotent_replay(self, bridge):
        status = mc.control(bridge, 'model-status')
        tid = '2' * 64
        mc.control(bridge, 'model-begin',
                   {'revision': status['revision'], 'transactionId': tid})
        mc.control(bridge, 'model-finish', {'transactionId': tid, 'outcome': 'rollback'})
        # Replayed finish with the same outcome is idempotent; a conflicting
        # outcome against the completion record is rejected.
        result = mc.control(bridge, 'model-finish',
                            {'transactionId': tid, 'outcome': 'rollback'})
        assert result['status'] == 'completed' and result['outcome'] == 'rollback'
        with pytest.raises(AccessError) as exc:
            mc.control(bridge, 'model-finish',
                       {'transactionId': tid, 'outcome': 'commit'})
        assert exc.value.code == 'model-transaction-completed'


def begin(bridge, tid='9' * 64):
    status = mc.control(bridge, 'model-status')
    mc.control(bridge, 'model-begin',
               {'revision': status['revision'], 'transactionId': tid})
    return tid


def apply(bridge, tid, model='swapped-8b'):
    target = {'model': model, 'contextLength': 65536,
              'maxTokens': 8192, 'reasoning': True}
    return mc.control(bridge, 'model-apply',
                      {'transactionId': tid, 'target': target}), target


class TestRecovery:
    def test_stale_revision_rejected(self, bridge):
        with pytest.raises(AccessError) as exc:
            mc.control(bridge, 'model-begin',
                       {'revision': '0' * 64, 'transactionId': HEX})
        assert exc.value.code == 'model-inspection-changed'

    def test_commit_without_apply_unverified(self, bridge):
        tid = begin(bridge)
        with pytest.raises(AccessError) as exc:
            mc.control(bridge, 'model-finish',
                       {'transactionId': tid, 'outcome': 'commit'})
        assert exc.value.code == 'model-apply-unverified'

    def test_apply_projection_mismatch(self, bridge):
        tid = begin(bridge)
        bridge.apply_result_sha = 'f' * 64
        with pytest.raises(AccessError) as exc:
            apply(bridge, tid)
        assert exc.value.code == 'model-projection-mismatch'

    def test_apply_target_cannot_change_midflight(self, bridge):
        tid = begin(bridge)
        target = {'model': 'mid-4b', 'contextLength': 16384,
                  'maxTokens': 2048, 'reasoning': False}
        bridge.fail_worker_once.add('model-apply')
        with pytest.raises(AccessError):
            mc.control(bridge, 'model-apply',
                       {'transactionId': tid, 'target': target})
        # Journal now pins the first target; a different one must not replace it.
        with pytest.raises(AccessError) as exc:
            mc.control(bridge, 'model-apply',
                       {'transactionId': tid,
                        'target': {'model': 'other-8b', 'contextLength': 32768,
                                   'maxTokens': 4096, 'reasoning': True}})
        assert exc.value.code == 'model-target-changed'

    def test_lost_begin_reply_resumes_to_held(self, bridge):
        status = mc.control(bridge, 'model-status')
        tid = '7' * 64
        bridge.fail_worker_once.add('model-begin')
        with pytest.raises(AccessError):
            mc.control(bridge, 'model-begin',
                       {'revision': status['revision'], 'transactionId': tid})
        # Journal is parked in 'acquiring'; a replayed begin resumes, not restarts.
        result = mc.control(bridge, 'model-begin',
                            {'revision': status['revision'], 'transactionId': tid})
        assert result['status'] == 'held' and result['transactionId'] == tid

    def test_lost_apply_reply_commits_without_reapply(self, bridge):
        tid = begin(bridge)
        target = {'model': 'lost-4b', 'contextLength': 16384,
                  'maxTokens': 2048, 'reasoning': False}
        bridge.fail_worker_once.add('model-apply')
        with pytest.raises(AccessError):
            mc.control(bridge, 'model-apply',
                       {'transactionId': tid, 'target': target})
        # Worker applied the config before its reply was lost.
        before = json.loads((bridge.state / 'model-before.json').read_text())
        bridge._write_config(plan(before, target))
        calls_before = len(bridge.calls)
        done = mc.control(bridge, 'model-finish',
                          {'transactionId': tid, 'outcome': 'commit'})
        assert done['status'] == 'completed' and done['outcome'] == 'commit'
        assert ('worker', 'model-apply') not in bridge.calls[calls_before:]

    def test_worker_pending_without_journal_fails_closed(self, bridge):
        bridge._pending = 'z' * 64
        with pytest.raises(AccessError) as exc:
            mc.control(bridge, 'model-status')
        assert exc.value.code == 'model-recovery-journal-missing'


class TestStateGuards:
    def test_releasing_journal_rejects_apply(self, bridge):
        tid = begin(bridge)
        journal = json.loads((bridge.state / 'transition.json').read_text())
        journal['phase'] = 'releasing'
        (bridge.state / 'transition.json').write_text(json.dumps(journal))
        with pytest.raises(AccessError) as exc:
            apply(bridge, tid)
        assert exc.value.code == 'model-transaction-finishing'

    def test_outcome_conflict_on_flipped_finish(self, bridge):
        tid = begin(bridge)
        journal = json.loads((bridge.state / 'transition.json').read_text())
        journal.update(phase='releasing', outcome='commit')
        (bridge.state / 'transition.json').write_text(json.dumps(journal))
        with pytest.raises(AccessError) as exc:
            mc.control(bridge, 'model-finish',
                       {'transactionId': tid, 'outcome': 'rollback'})
        assert exc.value.code == 'model-outcome-conflict'

    def test_corrupt_journal_fails_closed(self, bridge):
        tid = begin(bridge)
        journal = json.loads((bridge.state / 'transition.json').read_text())
        journal['kind'] = 'settings'
        (bridge.state / 'transition.json').write_text(json.dumps(journal))
        for op, req in [('model-status', None),
                        ('model-apply', {'transactionId': tid, 'target': {
                            'model': 'm', 'contextLength': 4096,
                            'maxTokens': 1, 'reasoning': False}})]:
            with pytest.raises(AccessError) as exc:
                mc.control(bridge, op, req)
            assert exc.value.code == 'invalid-model-transition'

    def test_dropped_edge_hold_detected(self, bridge):
        begin(bridge)
        bridge.edge('release')
        with pytest.raises(AccessError) as exc:
            mc.control(bridge, 'model-status')
        assert exc.value.code == 'model-hold-unconfirmed'

    def test_runtime_mismatch_on_tampered_readback(self, bridge):
        bridge.tamper_readback_contract = {
            'model': 'injected-1b', 'contextLength': 4096,
            'maxTokens': 1, 'reasoning': False}
        with pytest.raises(AccessError) as exc:
            mc.control(bridge, 'model-status')
        assert exc.value.code == 'model-runtime-mismatch'

    def test_stopped_native_runtime_unavailable(self, bridge):
        bridge.native_stopped = True
        with pytest.raises(AccessError) as exc:
            mc.control(bridge, 'model-status')
        assert exc.value.code == 'model-runtime-unavailable'

    def test_tampered_config_between_apply_and_commit(self, bridge):
        tid = begin(bridge)
        apply(bridge, tid)
        tampered = openclaw_config(model='evil-1b')
        bridge._write_config(tampered)
        with pytest.raises(AccessError) as exc:
            mc.control(bridge, 'model-finish',
                       {'transactionId': tid, 'outcome': 'commit'})
        assert exc.value.code == 'model-config-changed'

    def test_probe_proof_mismatch_blocks_completion(self, bridge):
        tid = begin(bridge)
        apply(bridge, tid)
        bridge.probe_mode = 'full-access'
        with pytest.raises(AccessError) as exc:
            mc.control(bridge, 'model-finish',
                       {'transactionId': tid, 'outcome': 'commit'})
        assert exc.value.code == 'model-access-proof-failed'
        # Fail-closed: transaction journal remains, holds are not released.
        assert (bridge.state / 'transition.json').exists()

    def test_invalid_completion_record_detected(self, bridge):
        done = bridge.state / 'model-completed.json'
        done.write_text(json.dumps({'transactionId': 'not-hex',
                                    'outcome': 'commit',
                                    'configSha256': '0' * 64}))
        os.chmod(done, 0o600)
        with pytest.raises(AccessError) as exc:
            mc.control(bridge, 'model-status')
        assert exc.value.code == 'invalid-model-completion'
