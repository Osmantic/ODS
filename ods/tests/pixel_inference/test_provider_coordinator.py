"""Real root/owner transaction files; explicitly simulated root, SDK and systemd.

These exercise production coordinator + owner projection + service participants.
They are not installed-runtime or model-inference acceptance.
"""
import sys
if sys.platform == "win32":
    from unittest import SkipTest
    raise SkipTest("Requires POSIX host ownership, file locks, or Unix sockets; run under Linux/WSL")

import contextlib
import json
import os
import sys
from pathlib import Path
from types import MethodType

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'bin'))
sys.path.insert(0, str(ROOT / 'extensions/services/pixel-agent/host'))

from pixel_access_bridge import AccessError, atomic_json, digest
from pixel_provider import coordinator as c
from pixel_provider import service_environment
from pixel_provider.config import default_config
from pixel_provider.managed_deployment import deployment, required_policy
from pixel_provider.store import StoreError
from test_service_activation import lifecycle, owner  # noqa: F401
from test_service_environment import participant  # noqa: F401


@pytest.fixture
def integrated(lifecycle, monkeypatch):  # noqa: F811 - imported pytest fixture
    p, b = lifecycle, lifecycle.bridge
    (b.state / 'transition.json').unlink()
    b.phase = 'idle'
    b.settings_data_dir = b.home / 'data'
    directory = b.settings_data_dir / 'pixel-providers'
    directory.mkdir(parents=True, mode=0o700)
    (directory / '.provider-config.lock').touch(mode=0o600)
    saved = default_config()
    saved.update(enabled=True, revision=1)
    saved['providers'] = [{'id': 'leader', 'label': 'Tower', 'kind': 'local', 'baseUrl': 'http://127.0.0.1:12001/v1',
        'model': 'qwen', 'contextTokens': 32768, 'maxOutputTokens': 4096, 'supportsTools': True,
        'supportsVision': False, 'reasoning': False, 'credentialRef': None, 'enabled': True}]
    saved['roles']['leader'] = 'leader'
    atomic_json(directory / 'provider-config.json', saved)
    b.pending = lambda: json.loads((b.state / 'transition.json').read_text()) if (b.state / 'transition.json').exists() else None
    b.locked = contextlib.nullcontext
    b.inspect = lambda: {'revision': digest([b.pid, b.pending(), b.phase]), 'busy': bool(b.active or b.streams),
        'configured_mode': 'sandboxed', 'runtime_verified': True, '_edge': {'revision': b.nrev}}
    original_read = c.settings._read
    monkeypatch.setattr(c.settings, '_read', lambda path, uid, maximum=1024*1024:
                        original_read(path, os.getuid() if uid == 0 else uid, maximum))
    monkeypatch.setattr(c, '_parents', service_environment._parents)
    monkeypatch.setattr(c, 'ServiceEnvironment', lambda bridge: p)

    class Runtime:
        def __init__(self, bridge): pass
        def qualify(self): return 'f' * 64
        def require_worker(self, directory, expected_custody):
            assert directory == b.directory and expected_custody == self.qualify()
            b.worker_checks += 1
            if not b.worker_ready:
                raise AccessError('provider-worker-runtime-not-ready')
        def verify_process(self):
            if b.failure == 'process': raise AccessError('provider-runtime-process-unqualified')
        def deployment(self, binding, directory):
            return deployment(binding, '/opt/ods/source', '/usr/bin/python3', str(directory), True), required_policy(), self.qualify()
    monkeypatch.setattr(c, 'RuntimeCustody', Runtime)
    native = b.native

    def native_lease(self, operation=None, token=None, **kwargs):
        if operation == 'acquire' and self.phase == 'idle': self.token, self.phase = token, 'held'
        if operation == 'release': self.phase = 'idle'
        return native(operation, token, **kwargs)

    def edge(self, operation=None, token=None, revision=None):
        if operation in ('acquire', 'recover'):
            if self.phase == 'idle': self.token, self.phase = token, 'held'
            if token != self.token: raise AccessError('foreign-edge-token')
        if operation == 'release':
            if self.failure == 'edge-release': raise AccessError('release-failed')
            self.phase = 'idle'
        return {'phase': self.phase, 'revision': self.nrev, 'streams': self.streams}

    def worker(self, operation, **kwargs):
        common = {'state_dir': str(b.home / '.openclaw/.ods-access-mode')}
        if operation == 'provider-status': return owner.provider_status(str(b.config), **common)
        common.update(transaction_id=kwargs['transaction_id'], expected_config_sha256=kwargs['config_hash'],
            validate_config=lambda path: bool(json.loads(Path(path).read_bytes())),
            check_no_active_run=kwargs['busy'], activate=kwargs['activate_provider'])
        if operation == 'provider-change':
            result = owner.change_provider(str(b.config), binding=kwargs['binding'],
                                           expected_projection=kwargs['expected_projection'], **common)
            if self.failure == 'lost-reply': raise AccessError('owner-worker-timeout')
            return result
        return owner.recover_provider(str(b.config), **common)
    b.native, b.edge, b.worker = MethodType(native_lease, b), MethodType(edge, b), MethodType(worker, b)
    b.saved, b.directory = saved, directory
    b.worker_ready, b.worker_checks = True, 0
    return p


def request(p, operation='apply'):
    state = c.status(p.bridge)
    return {'operation': operation, 'revision': state['revision'], 'providerRevision': state['providerRevision']}


def test_actual_owner_apply_update_deactivate_preserves_original_route(integrated):
    p, b = integrated, integrated.bridge
    original = json.loads(b.before)
    first = c.change(b, request(p))
    assert first['registrationVerified'] is True and first['transportVerified'] is False
    assert c.status(b)['status'] == 'applied' and b.phase == 'idle' and b.pending() is None
    b.saved['revision'] = 2
    atomic_json(b.directory / 'provider-config.json', b.saved)
    assert c.status(b)['status'] == 'saved-changes'
    second = c.change(b, request(p))
    assert first['binding']['activationId'] != second['binding']['activationId']
    assert second['binding']['revision'] == 2
    result = c.change(b, request(p, 'deactivate'))
    assert result['binding'] is None and json.loads(b.config.read_bytes()) == original
    assert p.snapshot() == {'environment': None, 'dropin': None}
    assert c.status(b)['status'] == 'inactive' and b.restarts == 3


def test_unready_worker_refuses_before_any_root_record_or_restart(integrated):
    p, b = integrated, integrated.bridge
    change = request(p)
    before = {str(path): path.read_bytes() for path in b.state.iterdir() if path.is_file()}
    b.worker_ready = False
    with pytest.raises(AccessError, match='provider-worker-runtime-not-ready'):
        c.change(b, change)
    assert b.worker_checks == 1 and b.restarts == 0 and b.pending() is None
    assert b.phase == 'idle' and b.config.read_bytes() == b.before
    assert before == {str(path): path.read_bytes() for path in b.state.iterdir() if path.is_file()}


def test_worker_drift_does_not_block_deactivation(integrated):
    p, b = integrated, integrated.bridge
    c.change(b, request(p))
    b.worker_ready = False
    assert c.change(b, request(p, 'deactivate'))['binding'] is None
    assert b.worker_checks == 1 and json.loads(b.config.read_bytes()) == json.loads(b.before)


def test_worker_drift_does_not_block_interrupted_recovery(integrated):
    p, b = integrated, integrated.bridge
    b.failure = 'reload'
    with pytest.raises(AccessError):
        c.change(b, request(p))
    b.failure, b.worker_ready = None, False
    assert c.change(b, request(p, 'recover'))['outcome'] == 'rolled-back'
    assert b.worker_checks == 1 and b.pending() is None
    assert b.config.read_bytes() == b.before


@pytest.mark.parametrize('failure', ['reload', 'restart'])
def test_real_owner_pending_recovery_rolls_back_exact_bytes(integrated, failure):
    p, b = integrated, integrated.bridge
    b.failure = failure
    with pytest.raises(AccessError): c.change(b, request(p))
    assert b.pending() and b.phase == 'held'
    b.failure = None
    result = c.change(b, request(p, 'recover'))
    assert result['outcome'] == 'rolled-back' and b.config.read_bytes() == b.before
    assert b.phase == 'idle' and b.pending() is None
    assert p.snapshot() == {'environment': None, 'dropin': None}


@pytest.mark.parametrize('failure', ['lost-reply', 'edge-release'])
def test_durable_completion_is_reconciled_without_repeating_restart(integrated, failure):
    p, b = integrated, integrated.bridge
    b.failure = failure
    with pytest.raises(AccessError): c.change(b, request(p))
    assert b.pending() and b.restarts == 1
    b.failure = None
    assert c.change(b, request(p, 'recover'))['outcome'] == 'applied'
    assert b.restarts == 1 and b.pending() is None and c.status(b)['status'] == 'applied'


def test_stale_request_busy_and_public_path_fields_refuse_before_change(integrated):
    p, b = integrated, integrated.bridge
    stale = request(p)
    b.saved['revision'] = 2
    atomic_json(b.directory / 'provider-config.json', b.saved)
    with pytest.raises(AccessError, match='inspection-changed'): c.change(b, stale)
    bad = dict(request(p), unit='evil.service')
    with pytest.raises(AccessError, match='invalid-provider-request'): c.change(b, bad)
    b.active = 1
    with pytest.raises(AccessError, match='busy'): c.change(b, request(p))
    assert b.pending() is None and b.config.read_bytes() == b.before and b.restarts == 0


def test_unknown_configuration_retains_journals_and_never_overwrites(integrated):
    p, b = integrated, integrated.bridge
    b.failure = 'reload'
    with pytest.raises(AccessError): c.change(b, request(p))
    b.config.write_bytes(b.before + b' ')
    foreign = b.config.read_bytes()
    b.failure = None
    with pytest.raises(StoreError, match='rollback-conflict'): c.change(b, request(p, 'recover'))
    assert b.config.read_bytes() == foreign and b.pending() and b.phase == 'held'


def test_owner_projection_mismatch_refuses_before_any_config_write(integrated):
    p, b = integrated, integrated.bridge
    command = b.worker
    def wrong_projection(operation, **kwargs):
        if operation == 'provider-change': kwargs['expected_projection']['afterSha'] = '0' * 64
        return command(operation, **kwargs)
    b.worker = wrong_projection
    with pytest.raises(StoreError, match='root-projection-mismatch'): c.change(b, request(p))
    assert b.config.read_bytes() == b.before and b.restarts == 0 and b.pending()
    b.worker = command
    assert c.change(b, request(p, 'recover'))['outcome'] == 'rolled-back'


def test_process_identity_failure_is_not_reported_as_applied(integrated):
    p, b = integrated, integrated.bridge
    b.failure = 'process'
    with pytest.raises(AccessError, match='process-unqualified'): c.change(b, request(p))
    assert c.status(b)['registrationVerified'] is False and b.pending()


def test_owner_change_cannot_trigger_a_live_partial_configuration_reload(integrated):
    p, b = integrated, integrated.bridge
    worker = b.worker
    def guarded_worker(operation, **kwargs):
        if operation == 'provider-change':
            assert b.stopped is True
            assert b.pending()['restartIdentity']['pid'] == b.pid
            assert b.config.read_bytes() == b.before
        return worker(operation, **kwargs)
    b.worker = guarded_worker
    assert c.change(b, request(p))['outcome'] == 'applied'
    assert b.stops == 1 and b.restarts == 1 and b.stopped is False


@pytest.mark.parametrize('failure', ['stop', 'stopped-before-owner'])
def test_interrupted_quiesce_restores_original_without_adopting_owner_state(integrated, failure):
    p, b = integrated, integrated.bridge
    worker = b.worker
    def interrupted(operation, **kwargs):
        if operation == 'provider-change': raise AccessError('simulated-owner-unavailable')
        return worker(operation, **kwargs)
    if failure == 'stop': b.failure = failure
    else: b.worker = interrupted
    with pytest.raises(AccessError): c.change(b, request(p))
    assert b.config.read_bytes() == b.before and b.pending()['phase'] == 'invoking'
    assert b.pending()['restartIdentity']['pid'] == b.pid
    b.failure, b.worker = None, worker
    assert c.change(b, request(p, 'recover'))['outcome'] == 'rolled-back'
    assert b.config.read_bytes() == b.before and b.pending() is None and b.phase == 'idle'
    assert b.stopped is False and b.restarts == (0 if failure == 'stop' else 1)


def test_owner_write_without_callback_recovers_old_config_before_start(integrated):
    p, b = integrated, integrated.bridge
    worker = b.worker
    def crash_before_callback():
        assert b.stopped and b.config.read_bytes() != b.before
        assert p.snapshot() == {'environment': None, 'dropin': None}
        raise AccessError('simulated-callback-loss')
    def interrupted(operation, **kwargs):
        if operation == 'provider-change': kwargs['activate_provider'] = crash_before_callback
        return worker(operation, **kwargs)
    b.worker = interrupted
    with pytest.raises(AccessError, match='callback-loss'): c.change(b, request(p))
    assert b.stopped and b.pending()['phase'] == 'invoking' and b.restarts == 0
    assert worker('provider-status')['pending'] is True
    b.worker = worker
    assert c.change(b, request(p, 'recover'))['outcome'] == 'rolled-back'
    assert b.config.read_bytes() == b.before and b.live_binding is None
    assert b.restarts == 1 and not b.stopped and b.phase == 'idle'
