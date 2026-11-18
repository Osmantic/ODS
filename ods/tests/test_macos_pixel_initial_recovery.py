"""Read-only eligibility inspection for interrupted initial activation."""
import copy
import importlib.util
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
SPEC = importlib.util.spec_from_file_location(
    'pixel_initial_recovery', ROOT / 'installers/macos/lib/pixel-initial-recovery.py')
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


class _Service:
    def __init__(self, target, events, fault):
        self.target = target
        self._events = events
        self._fault = fault

    def verify(self):
        self._events.append('verify:' + self.target)
        if self._fault == 'definition':
            raise recovery.RecoveryError('fixture-definition')


class _Adapter:
    def __init__(self, key, events, fault):
        self.key = key
        self._events = events
        self._fault = fault

    def process_identity(self):
        self._events.append('managed:' + self.key)
        if self._fault == 'managed-definition':
            raise recovery.RecoveryError('fixture-managed-definition')
        return (self.key, 1)


@pytest.fixture
def env(tmp_path, monkeypatch):
    import pixel_access_bridge
    import pixel_macos_custody

    monkeypatch.setattr(recovery.sys, 'platform', 'darwin')
    monkeypatch.setattr(recovery.os, 'geteuid', lambda: 0)

    state = tmp_path / 'protected'
    state.mkdir()
    events = []
    reads = {}
    owner = SimpleNamespace(pw_uid=501, pw_gid=20, pw_name='fixture')
    plan = dict(
        initial_install=True,
        owner=owner,
        source=Path('/owner/gateway.plist'),
        access_settings={'gateway_port': 18789, 'gateway_policy': {}},
        runtime_bundle={'digest': 'a' * 64},
        native_services={'expected_digest': 'b' * 64},
    )
    records = {
        'installation.json': {
            'schemaVersion': 1, 'phase': 'starting-access-relay',
            'operation': 'initial-install', 'owner': 501,
            'source': str(plan['source']),
        },
        'installation-edge.json': {
            'schemaVersion': 1, 'phase': 'held', 'container': 'e' * 64,
            'binding': {'token': 'f' * 64, 'revision': 'd' * 64},
            'status': {'capability': 'available', 'phase': 'held',
                       'admission_blocked': True, 'streams': 0, 'revision': 'd' * 64},
        },
        'service-installation.json': {
            'schemaVersion': 1, 'owner': 501, 'selection': plan['native_services'],
            'attempted': ['manager', 'promoter', 'operations'],
            'stopWitnesses': {}, 'progress': {'phase': 'services-active'},
            'recovery': {'manager': {}, 'promoter': {}, 'operations': {}},
        },
    }
    settings = json.dumps(plan['access_settings']).encode()

    def private_json(path, uid, maximum):
        assert uid == 0
        name = Path(path).name
        reads[name] = reads.get(name, 0) + 1
        return copy.deepcopy(records[name])

    def protected_bytes(path):
        return settings

    monkeypatch.setattr(pixel_access_bridge, 'private_json', private_json)
    monkeypatch.setattr(pixel_macos_custody, 'protected_bytes', protected_bytes)

    services = [_Service(name, events, None) for name in ('gateway', 'access', 'relay')]
    identities = []

    def identity(service):
        identities.append(service.target)
        return (service.target, 1)

    access_value = {'available': True, 'surface': 'darwin', 'pending': False,
                    'runtime_verified': False, 'effective_mode': 'unknown',
                    'reason': 'runtime-proof-required'}

    adapters = {key: _Adapter(key, events, None)
                for key in ('manager', 'promoter', 'operations')}

    def recovery_adapters(recovery_record, owner, save_stop, load_stop):
        assert owner == plan['owner'].pw_name
        assert save_stop is None and load_stop is None
        return adapters

    native_services = SimpleNamespace(recovery_adapters=recovery_adapters)

    api = SimpleNamespace(
        _native_services=native_services,
        _launchd=SimpleNamespace(ACCESS_STATE=state, ACCESS_CONFIG='/private/etc/ods/pixel-access.json'),
        _destination=lambda p: Path(p),
        _verify_bundle_selection=lambda plan: events.append('bundle'),
        _activation_services=lambda plan: [None, *services],
        _job_disabled=lambda target: False,
        _upgrade_service_identity=identity,
        _ready_gateway=lambda *a: events.append('gateway'),
        _ready_access=lambda service, timeout=None: dict(access_value),
        _ready_access_relay=lambda *a: events.append('relay'),
        _migration_edge_request=lambda plan, container: {
            'capability': 'available', 'phase': 'held',
            'admission_blocked': True, 'streams': 0, 'revision': 'd' * 64},
        _verify_new_services=lambda plan: events.append('services'),
    )

    def forbidden(*a, **k):
        raise AssertionError('inspection attempted a mutation')

    for name in ('install', '_activate', '_command', '_write_exact',
                 '_reprove_installed_access', 'verify_initial_install',
                 '_acquire_migration_hold', '_resume_migration_hold',
                 '_finish_migration_hold', 'bind_initial_services'):
        setattr(api, name, forbidden)

    return SimpleNamespace(plan=plan, api=api, records=records, events=events,
                           services=services, adapters=adapters,
                           access_value=access_value, state=state,
                           monkeypatch=monkeypatch, reads=reads)


def test_positive_inspection_returns_only_public_fields(env):
    result = recovery.inspect(env.plan, env.api)
    assert result == {
        'status': 'interrupted-initial-inspection-passed',
        'runtimeDigest': 'a' * 64,
        'serviceDigest': 'b' * 64,
        'installationPhase': 'starting-access-relay',
        'runtimeVerified': False,
    }
    assert 'token' not in json.dumps(result)
    assert 'f' * 64 not in json.dumps(result)
    assert 'access_settings' not in result
    assert 'container' not in result
    assert 'revision' not in result
    assert env.events.count('bundle') == 2
    assert env.events.count('services') == 2
    assert env.events.count('managed:manager') >= 2
    assert env.events.count('managed:promoter') >= 2
    assert env.events.count('managed:operations') >= 2


@pytest.mark.parametrize('mode,ready,accepted', [
    ('restricted', True, True), ('full', True, False), ('restricted', False, False),
])
def test_verified_access_must_match_policy(env, mode, ready, accepted):
    env.api._repair = SimpleNamespace(access_ready=lambda value: ready)
    env.api._policy = SimpleNamespace(policy_state=lambda policy: {'activeMode': 'restricted'})
    env.api._ready_access = lambda service, timeout=None: dict(
        env.access_value, runtime_verified=True, effective_mode=mode, reason=None)
    if accepted:
        assert recovery.inspect(env.plan, env.api)['runtimeVerified'] is True
    else:
        with pytest.raises(recovery.RecoveryError, match='native-initial-recovery-access-invalid'):
            recovery.inspect(env.plan, env.api)


@pytest.mark.parametrize('schema', [None, True, 2])
def test_service_journal_schema_is_exact(env, schema):
    env.records['service-installation.json']['schemaVersion'] = schema
    with pytest.raises(recovery.RecoveryError, match='native-initial-recovery-services-invalid'):
        recovery.inspect(env.plan, env.api)


@pytest.mark.parametrize('fault', [
    'phase', 'operation', 'owner', 'source', 'schema', 'extra-key',
    'edge-phase', 'edge-container', 'edge-binding', 'edge-status',
    'services-attempted', 'services-stops', 'services-recovery',
    'services-progress', 'services-owner', 'services-selection',
    'settings', 'pending', 'disabled', 'definition', 'hold',
    'record-change', 'edge-change', 'services-change', 'settings-change',
    'process-change', 'access-unavailable', 'access-surface', 'access-pending',
    'schema-bool', 'streams-bool', 'upgrade-plan', 'migration-plan',
    'missing-runtime-verified', 'unexpected-unverified-reason',
    'late-private-journal', 'late-private-config', 'late-core-pid',
    'late-managed-pid', 'late-hold-stream', 'late-hold-revision',
    'managed-definition',
])
def test_adversarial_inspection_fails_closed(env, fault):
    records = env.records
    if fault == 'phase':
        records['installation.json']['phase'] = 'active'
    elif fault == 'operation':
        records['installation.json']['operation'] = 'migration'
    elif fault == 'owner':
        records['installation.json']['owner'] = 502
    elif fault == 'source':
        records['installation.json']['source'] = '/other'
    elif fault == 'schema':
        records['installation.json']['schemaVersion'] = 2
    elif fault == 'schema-bool':
        records['installation.json']['schemaVersion'] = True
    elif fault == 'extra-key':
        records['installation.json']['extra'] = True
    elif fault == 'edge-phase':
        records['installation-edge.json']['phase'] = 'released'
    elif fault == 'edge-container':
        records['installation-edge.json']['container'] = 'not-hex'
    elif fault == 'edge-binding':
        records['installation-edge.json']['binding'] = {'token': 'f' * 64}
    elif fault == 'edge-status':
        records['installation-edge.json']['status']['streams'] = 1
    elif fault == 'streams-bool':
        records['installation-edge.json']['status']['streams'] = False
    elif fault == 'services-attempted':
        records['service-installation.json']['attempted'] = ['manager']
    elif fault == 'services-stops':
        records['service-installation.json']['stopWitnesses'] = {'manager': {}}
    elif fault == 'services-recovery':
        records['service-installation.json']['requiresRecovery'] = True
    elif fault == 'services-progress':
        records['service-installation.json']['progress']['phase'] = 'starting'
    elif fault == 'services-owner':
        records['service-installation.json']['owner'] = 502
    elif fault == 'services-selection':
        records['service-installation.json']['selection'] = {'expected_digest': 'c' * 64}
    elif fault == 'settings':
        env.monkeypatch.setattr(
            sys.modules['pixel_macos_custody'], 'protected_bytes',
            lambda p: json.dumps({'changed': True}).encode())
    elif fault == 'pending':
        (env.state / 'transition.json').write_text('pending')
    elif fault == 'disabled':
        env.api._job_disabled = lambda target: True
    elif fault == 'definition':
        env.services[0]._fault = 'definition'
    elif fault == 'managed-definition':
        env.adapters['manager']._fault = 'managed-definition'
    elif fault == 'hold':
        env.api._migration_edge_request = lambda plan, container: {
            'capability': 'available', 'phase': 'idle',
            'admission_blocked': False, 'streams': 0, 'revision': 'd' * 64}
    elif fault == 'record-change':
        original = sys.modules['pixel_access_bridge'].private_json
        calls = {'n': 0}
        def changed(path, uid, maximum):
            value = original(path, uid, maximum)
            if Path(path).name == 'installation.json':
                calls['n'] += 1
                if calls['n'] > 1:
                    value['phase'] = 'active'
            return value
        env.monkeypatch.setattr(sys.modules['pixel_access_bridge'], 'private_json', changed)
    elif fault == 'edge-change':
        original = sys.modules['pixel_access_bridge'].private_json
        calls = {'n': 0}
        def changed(path, uid, maximum):
            value = original(path, uid, maximum)
            if Path(path).name == 'installation-edge.json':
                calls['n'] += 1
                if calls['n'] > 1:
                    value['phase'] = 'released'
            return value
        env.monkeypatch.setattr(sys.modules['pixel_access_bridge'], 'private_json', changed)
    elif fault == 'services-change':
        original = sys.modules['pixel_access_bridge'].private_json
        calls = {'n': 0}
        def changed(path, uid, maximum):
            value = original(path, uid, maximum)
            if Path(path).name == 'service-installation.json':
                calls['n'] += 1
                if calls['n'] > 1:
                    value['progress']['phase'] = 'starting'
            return value
        env.monkeypatch.setattr(sys.modules['pixel_access_bridge'], 'private_json', changed)
    elif fault == 'settings-change':
        calls = {'n': 0}
        def changed(path):
            calls['n'] += 1
            if calls['n'] > 1:
                return json.dumps({'changed': True}).encode()
            return json.dumps(env.plan['access_settings']).encode()
        env.monkeypatch.setattr(sys.modules['pixel_macos_custody'], 'protected_bytes', changed)
    elif fault == 'process-change':
        calls = {'n': 0}
        def identity(service):
            calls['n'] += 1
            return (service.target, 1 if calls['n'] <= 3 else 2)
        env.api._upgrade_service_identity = identity
    elif fault == 'access-unavailable':
        env.api._ready_access = lambda service, timeout=None: dict(env.access_value, available=False)
    elif fault == 'access-surface':
        env.api._ready_access = lambda service, timeout=None: dict(env.access_value, surface='linux')
    elif fault == 'access-pending':
        env.api._ready_access = lambda service, timeout=None: dict(env.access_value, pending=True)
    elif fault == 'upgrade-plan':
        env.plan['upgrade_qualification'] = {'candidateDigest': 'a' * 64}
    elif fault == 'migration-plan':
        env.plan['migration'] = {'candidateDigest': 'a' * 64}
    elif fault == 'missing-runtime-verified':
        value = dict(env.access_value)
        value.pop('runtime_verified')
        env.api._ready_access = lambda service, timeout=None: dict(value)
    elif fault == 'unexpected-unverified-reason':
        env.api._ready_access = lambda service, timeout=None: dict(
            env.access_value, reason='other')
    elif fault == 'late-private-journal':
        original = sys.modules['pixel_access_bridge'].private_json
        calls = {'n': 0}
        def changed(path, uid, maximum):
            value = original(path, uid, maximum)
            if Path(path).name == 'installation.json':
                calls['n'] += 1
                if calls['n'] >= 3:
                    value['phase'] = 'active'
            return value
        env.monkeypatch.setattr(sys.modules['pixel_access_bridge'], 'private_json', changed)
    elif fault == 'late-private-config':
        calls = {'n': 0}
        def changed(path):
            calls['n'] += 1
            if calls['n'] >= 3:
                return json.dumps({'changed': True}).encode()
            return json.dumps(env.plan['access_settings']).encode()
        env.monkeypatch.setattr(sys.modules['pixel_macos_custody'], 'protected_bytes', changed)
    elif fault == 'late-core-pid':
        calls = {'n': 0}
        def identity(service):
            calls['n'] += 1
            return (service.target, 1 if calls['n'] <= 6 else 2)
        env.api._upgrade_service_identity = identity
    elif fault == 'late-managed-pid':
        calls = {'n': 0}
        def process_identity(self):
            calls['n'] += 1
            return (self.key, 1 if calls['n'] <= 6 else 2)
        for adapter in env.adapters.values():
            adapter.process_identity = process_identity.__get__(adapter, _Adapter)
    elif fault == 'late-hold-stream':
        calls = {'n': 0}
        def request(plan, container):
            calls['n'] += 1
            streams = 0 if calls['n'] <= 2 else 1
            return {'capability': 'available', 'phase': 'held',
                    'admission_blocked': True, 'streams': streams,
                    'revision': 'd' * 64}
        env.api._migration_edge_request = request
    elif fault == 'late-hold-revision':
        calls = {'n': 0}
        def request(plan, container):
            calls['n'] += 1
            revision = 'd' * 64 if calls['n'] <= 2 else 'c' * 64
            return {'capability': 'available', 'phase': 'held',
                    'admission_blocked': True, 'streams': 0,
                    'revision': revision}
        env.api._migration_edge_request = request
    with pytest.raises(recovery.RecoveryError):
        recovery.inspect(env.plan, env.api)


def test_inspection_requires_root_and_darwin(env):
    env.monkeypatch.setattr(recovery.sys, 'platform', 'linux')
    with pytest.raises(recovery.RecoveryError):
        recovery.inspect(env.plan, env.api)
    env.monkeypatch.setattr(recovery.sys, 'platform', 'darwin')
    env.monkeypatch.setattr(recovery.os, 'geteuid', lambda: 501)
    with pytest.raises(recovery.RecoveryError):
        recovery.inspect(env.plan, env.api)


def test_inspection_requires_complete_plan(env):
    for key in ('initial_install', 'runtime_bundle', 'native_services'):
        plan = dict(env.plan)
        plan.pop(key)
        with pytest.raises(recovery.RecoveryError):
            recovery.inspect(plan, env.api)


@pytest.fixture
def completion_env(env):
    env.locked = False
    env.live_edge = 'held'
    env.before_release = lambda: None
    env.lose_release_reply = False
    env.fail_reproof = False
    env.api.pwd = SimpleNamespace(getpwnam=lambda name: env.plan['owner'])

    @contextmanager
    def locked():
        assert not env.locked
        env.locked = True
        env.events.append('lock')
        try:
            yield
        finally:
            env.locked = False
            env.events.append('unlock')

    env.api._bridge_from_recovery_plan = lambda plan, owner_name: SimpleNamespace(locked=locked)

    def phase(plan, path, value):
        assert env.locked
        env.events.append('phase:' + value)
        env.records['installation.json']['phase'] = value
    env.api._migration_phase = phase

    def edge_status(plan, container):
        return {'capability': 'available', 'phase': env.live_edge, 'streams': 0,
                'admission_blocked': env.live_edge == 'held',
                'revision': ('d' if env.live_edge == 'held' else 'c') * 64}
    env.api._migration_edge_request = edge_status

    def finish_hold(plan, record, *, verify_before_release):
        assert env.locked
        assert record == env.records['installation-edge.json']
        assert env.records['installation.json']['phase'] == 'active'
        edge = env.records['installation-edge.json']
        edge['phase'] = 'releasing'
        env.before_release()
        verify_before_release()
        env.events.append('release')
        env.live_edge = 'idle'
        if env.lose_release_reply:
            env.lose_release_reply = False
            raise OSError('lost release reply')
        edge['phase'] = 'released'
    env.api._finish_migration_hold = finish_hold

    def reprove(plan):
        assert not env.locked, 'reproof must not deadlock on the controller lock'
        assert env.records['installation-edge.json']['phase'] == 'released'
        env.events.append('reprove')
        if env.fail_reproof:
            raise OSError('reproof unavailable')
    env.api._reprove_installed_access = reprove

    def verify(plan):
        assert not env.locked
        assert env.records['installation.json']['phase'] == 'active'
        assert env.records['installation-edge.json']['phase'] == 'released'
        env.events.append('final-proof')
        return {'status': 'active', 'runtimeDigest': 'a' * 64, 'serviceDigest': 'b' * 64}
    env.api.verify_initial_install = verify
    return env


@pytest.mark.parametrize('phase,edge,live,releases', [
    ('starting-access-relay', 'held', 'held', 1),
    ('active', 'held', 'held', 1),
    ('active', 'releasing', 'held', 1),
    ('active', 'releasing', 'idle', 1),
    ('active', 'released', 'idle', 0),
])
def test_completion_reproves_each_durable_retry_state(completion_env, phase, edge, live, releases):
    env = completion_env
    env.records['installation.json']['phase'] = phase
    env.records['installation-edge.json']['phase'] = edge
    env.live_edge = live
    result = recovery.complete(env.plan, env.api)
    assert result == {'status': 'active', 'runtimeDigest': 'a' * 64, 'serviceDigest': 'b' * 64}
    assert env.events.count('release') == releases
    assert env.events[-3:] == ['unlock', 'reprove', 'final-proof']
    assert env.events.count('phase:active') == int(phase == 'starting-access-relay')


@pytest.mark.parametrize('phase,edge,live', [
    ('starting-access-relay', 'releasing', 'held'),
    ('starting-access-relay', 'released', 'idle'),
    ('initial-stopped', 'held', 'held'),
    ('active', 'held', 'idle'),
    ('active', 'released', 'held'),
])
def test_completion_rejects_unknown_or_inconsistent_states(completion_env, phase, edge, live):
    env = completion_env
    env.records['installation.json']['phase'] = phase
    env.records['installation-edge.json']['phase'] = edge
    env.live_edge = live
    before = copy.deepcopy(env.records)
    with pytest.raises(recovery.RecoveryError):
        recovery.complete(env.plan, env.api)
    assert env.records == before
    assert 'release' not in env.events and 'reprove' not in env.events


def test_completion_retries_lost_release_reply_with_original_binding(completion_env):
    env = completion_env
    binding = copy.deepcopy(env.records['installation-edge.json']['binding'])
    env.lose_release_reply = True
    with pytest.raises(OSError, match='lost release reply'):
        recovery.complete(env.plan, env.api)
    assert env.records['installation.json']['phase'] == 'active'
    assert env.records['installation-edge.json']['phase'] == 'releasing'
    assert env.live_edge == 'idle'
    assert recovery.complete(env.plan, env.api)['status'] == 'active'
    assert env.records['installation-edge.json']['binding'] == binding
    assert env.events.count('release') == 2


def test_completion_retry_after_reproof_failure_does_not_release_again(completion_env):
    env = completion_env
    env.fail_reproof = True
    with pytest.raises(OSError, match='reproof unavailable'):
        recovery.complete(env.plan, env.api)
    assert env.records['installation.json']['phase'] == 'active'
    assert env.records['installation-edge.json']['phase'] == 'released'
    env.fail_reproof = False
    assert recovery.complete(env.plan, env.api)['status'] == 'active'
    assert env.events.count('release') == 1


def test_completion_checks_custody_before_release(completion_env):
    env = completion_env
    def change_custody():
        env.records['service-installation.json']['owner'] = 502
    env.before_release = change_custody
    with pytest.raises(recovery.RecoveryError, match='native-initial-recovery-services-invalid'):
        recovery.complete(env.plan, env.api)
    assert env.records['installation-edge.json']['phase'] == 'releasing'
    assert env.live_edge == 'held'
    assert 'release' not in env.events and 'reprove' not in env.events


def test_completion_rejects_pending_retirement_without_writes(completion_env):
    env = completion_env
    (env.state / 'retirement.json').write_text('{}')
    before = copy.deepcopy(env.records)
    with pytest.raises(recovery.RecoveryError, match='transition-pending'):
        recovery.complete(env.plan, env.api)
    assert env.records == before
    assert 'release' not in env.events


def test_default_inspection_does_not_admit_completion_retry_states(completion_env):
    env = completion_env
    env.records['installation.json']['phase'] = 'active'
    with pytest.raises(recovery.RecoveryError, match='phase-not-interrupted'):
        recovery.inspect(env.plan, env.api)
