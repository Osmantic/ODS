"""Source-only retained-hold proof contracts; no Mac or deployment qualification."""
import ast
import asyncio
import copy
from contextlib import contextmanager
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'installers/macos/lib'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load('held_proof_repair_fixture', ROOT / 'tests/test_macos_pixel_controller_repair.py')
repair = base.repair
pure = load('held_proof', LIB / 'pixel-controller-held-proof.py')
live = load('held_proof_live', LIB / 'pixel-controller-held-proof-live.py')
child = load('held_proof_child', LIB / 'pixel-controller-proof-child.py')


def fixture():
    _, _, snapshots, repaired, _ = base.fixture()
    repaired['phase'] = 'repaired'
    authority = dict(current=base.CURRENT, candidate=base.CANDIDATE, owner=base.OWNER,
        snapshots=snapshots, repair_body=base.encoded(repaired), settings_hash='f' * 64,
        mode='sandboxed')
    value = pure.make_intent(repair, identities=repaired['identities'], **authority)
    return authority, value


def proof(value):
    return dict(mode=value['mode'], pid=value['identities']['gateway'][0],
                config_sha256='1' * 64, executed=True, at='2026-09-23T12:00:00.000Z')


def sample(value, *, held=False, ready=False):
    return dict(native=dict(available=True, phase='held' if held else 'idle', active=0,
        pid=value['identities']['gateway'][0], revision='2' * 64,
        proof=proof(value) if ready else None, runtime_version='2026.6.33', probe_failure=None),
        owned=held, ready=ready)


class FaultAdapter:
    def __init__(self, failure=None, after=False):
        self.authority, self.value = fixture()
        self.observed = sample(self.value)
        self.failure, self.after, self.events = failure, after, []
        self.edge_held, self.locked = True, True

    def event(self, name, action=lambda: None):
        assert self.locked
        self.events.append(name)
        fail = self.failure == len(self.events) - 1
        if fail and not self.after: raise OSError('interrupted before effect')
        result = action()
        if fail: raise OSError('interrupted after effect')
        return result

    def verify(self, value):
        def check():
            assert value == self.value
            pure.validate(repair, value, **self.authority)
            if not self.edge_held:
                assert value['phase'] in ('releasing-edge', 'complete')
        self.event('verify:' + value['phase'], check)

    def persist(self, before, after):
        assert before == self.value
        def write():
            pure.validate(repair, after, **self.authority)
            self.value = copy.deepcopy(after)
        self.event('persist:' + after['phase'], write)

    def snapshot(self, value):
        return self.event('snapshot:' + value['phase'], lambda: copy.deepcopy(self.observed))

    def native(self, value, operation):
        def effect():
            assert self.edge_held
            if operation == 'acquire':
                assert value['phase'] == 'acquiring-native'
                self.observed = sample(value, held=True)
            elif operation == 'probe':
                assert value['phase'] == 'probing'
                self.observed = sample(value, held=True, ready=True)
            else:
                assert value['phase'] == 'releasing-native'
                self.observed = sample(value, ready=True)
                self.observed['native']['revision'] = '3' * 64
            return copy.deepcopy(self.observed)
        return self.event('native:' + operation, effect)

    def release_edge(self, value):
        def effect():
            assert value['phase'] == 'releasing-edge'
            pure.check_native(self.observed, value, held=False, ready=True, exact=True)
            self.edge_held = False
            held = repair._hold(self.authority['snapshots']['hold'])
            self.authority['snapshots']['hold'] = json.dumps(dict(held, phase='released')).encode()
        self.event('edge:release', effect)

    def verify_released(self, value):
        self.event('edge:released', lambda: None)
        assert not self.edge_held


def test_effect_order_retains_original_hold_through_real_proof_and_native_release():
    adapter = FaultAdapter()
    originals = copy.deepcopy(adapter.authority)
    assert pure.execute(adapter.value, adapter)['phase'] == 'complete'
    effects = [event for event in adapter.events if event.startswith(('persist:', 'native:', 'edge:release'))]
    assert effects == ['persist:acquiring-native', 'native:acquire', 'persist:probing', 'native:probe',
        'persist:verified', 'persist:releasing-native', 'native:release', 'persist:native-released',
        'persist:releasing-edge', 'edge:release', 'persist:complete', 'edge:released']
    assert all(adapter.authority['snapshots'][key] == originals['snapshots'][key]
               for key in ('archive', 'context', 'service'))
    assert adapter.authority['repair_body'] == originals['repair_body']
    previous = len(adapter.events)
    assert pure.execute(adapter.value, adapter)['phase'] == 'complete'
    assert not any(event.startswith(('persist:', 'native:')) for event in adapter.events[previous:])


@pytest.mark.parametrize('after', [False, True])
def test_interruption_at_every_durable_write_observation_and_effect_resumes_exactly(after):
    reference = FaultAdapter()
    pure.execute(reference.value, reference)
    for index in range(len(reference.events)):
        adapter = FaultAdapter(index, after)
        with pytest.raises(OSError): pure.execute(adapter.value, adapter)
        if not adapter.edge_held:
            assert adapter.value['phase'] in ('releasing-edge', 'complete')
            assert adapter.observed['ready'] and adapter.observed['native']['proof'] == adapter.value['proof']
        adapter.failure = None
        assert pure.execute(adapter.value, adapter)['phase'] == 'complete'
        assert adapter.events.count('native:probe') <= 2


@pytest.mark.parametrize('field', ['archive', 'context', 'service', 'hold'])
def test_authority_byte_drift_is_not_adopted(field):
    authority, value = fixture()
    authority['snapshots'][field] += b' '
    with pytest.raises((pure.ProofError, repair.RepairError)):
        pure.validate(repair, value, **authority)


@pytest.mark.parametrize('change', [
    lambda a: a.update(repair_body=a['repair_body'] + b' '),
    lambda a: a.update(settings_hash='0' * 64), lambda a: a.update(mode='full-access'),
    lambda a: a.update(candidate='0' * 64), lambda a: a['owner'].update(uid=502),
])
def test_repair_settings_owner_and_selection_are_exact(change):
    authority, value = fixture()
    authority = copy.deepcopy(authority)
    change(authority)
    with pytest.raises((pure.ProofError, repair.RepairError)):
        pure.validate(repair, value, **authority)


@pytest.mark.parametrize('change', [
    lambda v: v.update(extra=True), lambda v: v.update(schemaVersion=True),
    lambda v: v.update(phase='ready'), lambda v: v.update(leaseRevision='1' * 64),
    lambda v: v.update(proof={}), lambda v: v['identities']['gateway'].pop(),
    lambda v: v['identities']['access'].__setitem__(2, 1000000),
])
def test_closed_journal_schema_and_phase_contract(change):
    authority, value = fixture()
    change(value)
    with pytest.raises((pure.ProofError, repair.RepairError)):
        pure.validate(repair, value, **authority)


@pytest.mark.parametrize('phase', ['acquiring-native', 'probing', 'verified', 'releasing-native',
                                    'native-released', 'releasing-edge'])
@pytest.mark.parametrize('fault', ['other-token', 'process', 'busy', 'proof', 'revision', 'version'])
def test_unknown_native_state_never_releases_edge(phase, fault):
    adapter = FaultAdapter()
    original = adapter.persist
    def stop(before, after):
        original(before, after)
        if after['phase'] == phase: raise OSError('checkpoint')
    adapter.persist = stop
    with pytest.raises(OSError): pure.execute(adapter.value, adapter)
    adapter.persist = original
    native = adapter.observed['native']
    if fault == 'other-token':
        native['phase'] = 'held'
        adapter.observed['owned'] = False
    elif fault == 'process': native['pid'] += 1
    elif fault == 'busy': native['active'] = 1
    elif fault == 'proof':
        # Before acquiring, arbitrary old proof is not authority; acquisition
        # deliberately clears it. All subsequent phases reject unknown proof.
        if phase == 'acquiring-native':
            native['phase'] = 'held'
            adapter.observed['owned'] = True
        native['proof'] = {'executed': True}
        adapter.observed['ready'] = True
    elif fault == 'revision':
        native['revision'] = 'invalid'
    else: native['runtime_version'] = 'other'
    with pytest.raises(pure.ProofError): pure.execute(adapter.value, adapter)
    assert adapter.edge_held and 'edge:release' not in adapter.events


def request():
    _, value = fixture()
    hashes = {name: 'a' * 64 for name in child.MODULES}
    hashes['pixel_access_bridge.py'] = repair.AFTER
    return dict(action='snapshot', owner=value['owner'], settingsSha256='f' * 64,
        modules=hashes, token='d' * 64, mode='sandboxed', gatewayIdentity=value['identities']['gateway'])


def child_namespace():
    namespace = {'__name__': 'proof_child_test'}
    exec(compile(child.PROGRAM, '<actual fixed child>', 'exec'), namespace)
    return namespace


def test_fixed_child_command_and_actual_closed_protocol():
    assert child.command() == ['/usr/bin/python3', '-I', '-B', '-c', child.PROGRAM]
    for action in ('snapshot', 'acquire', 'probe', 'release'):
        value = dict(request(), action=action)
        assert json.loads(child.request_bytes(value)) == value
    tree = ast.parse((LIB / 'pixel-macos-access-install.py').read_text())
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in
                ('HOST_FILES', 'SETTINGS_FILES', 'PROVIDER_FILES') for t in node.targets):
            constants[node.targets[0].id] = ast.literal_eval(node.value)
    assert set(child.MODULES) == set(constants['HOST_FILES']) | {
        'pixel_settings/' + name for name in constants['SETTINGS_FILES']} | {
        'pixel_provider/' + name for name in constants['PROVIDER_FILES']}
    run = [n for n in ast.walk(ast.parse(child.PROGRAM)) if isinstance(n, ast.FunctionDef) and n.name == 'run'][0]
    forbidden = {'locked', 'recovery_locked', 'change', 'reconcile', 'create_connection'}
    assert not any(isinstance(n, ast.Attribute) and n.attr in forbidden for n in ast.walk(run))


@pytest.mark.parametrize('change', [
    lambda v: v.update(action='change'), lambda v: v.update(command='/bin/sh'),
    lambda v: v.update(path='/tmp/import'), lambda v: v.update(token='x' * 64),
    lambda v: v.update(token='d' * 65), lambda v: v.update(mode='off'),
    lambda v: v.update(settingsSha256='x' * 64), lambda v: v['modules'].update(extra='a' * 64),
    lambda v: v['modules'].update({'pixel_access_bridge.py': repair.BEFORE}),
    lambda v: v['owner'].update(uid=0), lambda v: v['owner'].update(uid=True),
    lambda v: v['owner'].update(name='../owner'), lambda v: v['gatewayIdentity'].__setitem__(0, 0),
    lambda v: v['gatewayIdentity'].__setitem__(3, 0), lambda v: v['gatewayIdentity'].__setitem__(2, 1000000),
])
def test_actual_child_builder_rejects_command_path_owner_and_scope_injection(change):
    value = request()
    change(value)
    with pytest.raises(ValueError): child.request_bytes(value)


@pytest.mark.parametrize('body', [b'{}', b'[]', b'{"action":"probe","action":"snapshot"}', b'x' * 16385])
def test_actual_child_parser_rejects_missing_duplicate_and_oversized_input(body):
    with pytest.raises(ValueError): child_namespace()['decode'](body)


def child_runtime(monkeypatch, *, action='snapshot', fault=None):
    """Execute the actual fixed child, mocking only OS custody and installed IO."""
    ns = child_namespace()
    value = request()
    value['action'] = action
    _, intent = fixture()
    observed = sample(intent, held=action in ('acquire', 'probe'), ready=action != 'acquire')
    events = []
    settings = dict(install_dir='/fixed-install', gateway_target='fixed-target', gateway_plist='fixed-plist',
        gateway_process='node', gateway_binding={}, openclaw_bin='fixed-binary', gateway_policy={})
    owner = SimpleNamespace(pw_name='fixture', pw_uid=501, pw_gid=20)
    ns['sys'] = SimpleNamespace(platform='darwin', path=[])
    ns['os'] = SimpleNamespace(geteuid=lambda: 0)
    ns['bootstrap_custody'] = lambda r: events.append('bootstrap')
    def authority(*args):
        events.append('authority')
        if fault == 'authority' and events.count('authority') == 2: raise ValueError('private')
        if fault == 'late-proof' and events.count('authority') == 2:
            observed['native']['proof'] = None
            observed['ready'] = False
        return settings
    ns['authority'] = authority
    ns['key_bytes'] = lambda *a: events.append('key') or 'private-fixture-key'
    def identity():
        events.append('identity')
        result = list(value['gatewayIdentity'])
        if fault == 'late-process' and events.count('identity') > 1: result[1] += 1
        return result
    @contextmanager
    def bounded(seconds):
        assert seconds == 140
        events.append('bound')
        yield
    def native(operation=None, token=None, **kwargs):
        events.append('native:' + str(operation))
        if operation: assert token == value['token']
        if fault == 'late-sample' and events.count('native:None') == 2:
            observed['native']['revision'] = '0' * 64
        return copy.deepcopy(observed['native'])
    def status():
        events.append('status')
        return dict(available=True, scope='owner-host', surface='darwin', configured_mode='sandboxed',
            effective_mode='sandboxed', runtime_verified=observed['ready'], busy=False, pending=False, reason=None)
    def probe(token, mode):
        assert token == value['token'] and mode == value['mode']
        events.append('probe')
    bridge = SimpleNamespace(bounded=bounded, discover=lambda: events.append('discover'),
        gateway_service=SimpleNamespace(process_identity=identity), _policy_state=lambda: {'activeMode': 'sandboxed'},
        verify_held_mode=probe, native=native, status=status,
        owns_native_hold=lambda *a: events.append('owns') or observed['owned'])
    monkeypatch.setitem(sys.modules, 'pwd', SimpleNamespace(getpwnam=lambda name: owner))
    monkeypatch.setitem(sys.modules, 'pixel_access_bridge', SimpleNamespace(LaunchdAccessBridge=lambda *a, **k: bridge))
    return ns, value, events


@pytest.mark.parametrize('action', ['snapshot', 'acquire', 'probe', 'release'])
def test_actual_child_uses_direct_installed_primitive_without_controller_recursion(monkeypatch, action):
    ns, value, events = child_runtime(monkeypatch, action=action)
    result = ns['run'](value)
    assert result['ready'] is (action != 'acquire')
    assert events[-7:] == ['authority', 'key', 'native:None', 'status', 'native:None', 'owns', 'identity']
    assert events.count('probe') == (action == 'probe')
    assert events.count('native:' + action) == (action in ('acquire', 'release'))


@pytest.mark.parametrize('fault', ['authority', 'late-process', 'late-sample', 'late-proof'])
def test_actual_child_never_returns_stale_ready_observation(monkeypatch, fault):
    ns, value, events = child_runtime(monkeypatch, fault=fault)
    if fault == 'late-proof':
        result = ns['run'](value)
        assert result['ready'] is False and result['native']['proof'] is None
    else:
        with pytest.raises(ValueError): ns['run'](value)


@pytest.mark.parametrize('fault', [None, 'wrong-config', 'wrong-module', 'pending', 'wrong-path'])
def test_actual_child_authority_checks_every_fixed_module_and_settings(monkeypatch, fault):
    ns, value = child_namespace(), request()
    bodies = {name: name.encode() for name in child.MODULES}
    value['modules'] = {name: repair.sha(body) for name, body in bodies.items()}
    settings = dict(owner='fixture', state_dir=str(ns['STATE']), gateway_target='system/com.ods.pixel-native-gateway',
        gateway_plist='/Library/LaunchDaemons/com.ods.pixel-native-gateway.plist',
        openclaw_bin=str(ns['ROOT'] / 'openclaw-gateway-launcher'), edge_owner_key_sha256='f' * 64)
    config = json.dumps(settings).encode()
    value['settingsSha256'] = repair.sha(config)
    reads = []
    def read(path, **kwargs):
        reads.append(path)
        if path == ns['CONFIG']: return config + (b' ' if fault == 'wrong-config' else b'')
        name = path.relative_to(ns['ROOT']).as_posix()
        return bodies[name] + (b' ' if fault == 'wrong-module' and name == child.MODULES[-1] else b'')
    ns['private_metadata'] = lambda path, mode: None
    ns['os'] = SimpleNamespace(path=SimpleNamespace(lexists=lambda path: fault == 'pending'))
    if fault == 'wrong-path':
        settings['openclaw_bin'] = '/tmp/arbitrary'
        config = json.dumps(settings).encode()
        value['settingsSha256'] = repair.sha(config)
    custody = SimpleNamespace(protected_tree_metadata=lambda p: None, protected_bytes=read)
    if fault:
        with pytest.raises(ValueError): ns['authority'](custody, value)
    else:
        assert ns['authority'](custody, value) == settings
        assert len(reads) == len(child.MODULES) + 1


def source(name, namespace):
    return base.source_function('installers/macos/lib/pixel-macos-access-install.py', name, namespace)


@pytest.mark.parametrize('phase', ['held', 'releasing', 'released'])
@pytest.mark.parametrize('fault', [None, 'late-proof', 'late-process'])
def test_real_adapter_and_hold_helper_resample_after_slow_checks_before_release(monkeypatch, phase, fault):
    authority, value = fixture()
    value.update(phase='releasing-edge', leaseRevision='2' * 64, proof=proof(value))
    hold = repair._hold(authority['snapshots']['hold'])
    hold['phase'] = phase
    disk = [copy.deepcopy(hold)]
    events = []
    observed = sample(value, ready=True)
    def acquire_release(plan, container, operation, binding):
        events.append(operation)
        if operation == 'acquire':
            if fault == 'late-proof': observed['ready'] = False
            if fault == 'late-process': observed['native']['pid'] += 1
            return hold['status']
        assert events[-2] == 'fresh-child'
        return {'capability': 'available', 'phase': 'idle', 'streams': 0, 'admission_blocked': False}
    def write(path, record):
        events.append('journal:' + record['phase'])
        disk[0] = record
    monkeypatch.setitem(sys.modules, 'pixel_access_bridge', SimpleNamespace(
        private_json=lambda *a: disk[0], atomic_json=write))
    finish = source('_finish_migration_hold', {'_edge_hold_journal': lambda p: 'hold',
        '_migration_edge_request': acquire_release, 'InstallError': ValueError})
    adapter = object.__new__(live.Adapter)
    adapter.i = SimpleNamespace(_finish_migration_hold=finish, _migration_edge_request=acquire_release, InstallError=ValueError)
    adapter.pure, adapter.plan = pure, {}
    def verify(value):
        events.append('slow-verify')
        if phase != 'held':
            if fault == 'late-proof': observed['ready'] = False
            if fault == 'late-process': observed['native']['pid'] += 1
    adapter.verify = verify
    adapter.edge = lambda v: events.append('slow-edge') or hold
    adapter.budget = lambda: 1
    adapter.child_sample = lambda *a: events.append('fresh-child') or copy.deepcopy(observed)
    if fault:
        with pytest.raises(pure.ProofError): adapter.release_edge(value)
        assert 'release' not in events
        assert disk[0]['phase'] != 'released' or phase == 'released'
    else:
        adapter.release_edge(value)
        assert events.index('release') > events.index('fresh-child') > events.index('slow-edge')
        assert disk[0]['phase'] == 'released'


@pytest.mark.parametrize('fault', [None, 'timeout', 'exit', 'large', 'duplicate', 'shape'])
def test_real_adapter_child_transport_is_fixed_bounded_and_private(monkeypatch, fault):
    authority, value = fixture()
    events = []
    adapter = object.__new__(live.Adapter)
    adapter.i, adapter.repair, adapter.child = SimpleNamespace(InstallError=ValueError), repair, child
    adapter.settings_hash, adapter.mode = authority['settings_hash'], authority['mode']
    adapter.modules = request()['modules']
    adapter.authority = lambda: authority
    adapter.budget = lambda: 42
    adapter.verify = lambda v: events.append('authority')
    def run(command, **kwargs):
        events.append('child')
        assert command == child.command()
        assert kwargs['env'] == {'PATH': '/usr/bin:/bin'} and kwargs['cwd'] == '/'
        assert kwargs['timeout'] == 42 and kwargs['stderr'] == live.subprocess.DEVNULL
        assert set(json.loads(kwargs['input'])) == set(request())
        assert b'credential' not in kwargs['input'] and b'private-fixture-key' not in kwargs['input']
        if fault == 'timeout': raise live.subprocess.TimeoutExpired(command, 42)
        output = base.encoded(sample(value))
        if fault == 'large': output = b'x' * 8193
        if fault == 'duplicate': output = b'{"ready":true,"ready":false}'
        if fault == 'shape': output = b'{"error":"private"}'
        return SimpleNamespace(returncode=1 if fault == 'exit' else 0, stdout=output)
    monkeypatch.setattr(live.subprocess, 'run', run)
    if fault:
        with pytest.raises((ValueError, live.subprocess.TimeoutExpired)): adapter.invoke(value, 'snapshot')
    else:
        assert adapter.invoke(value, 'snapshot') == sample(value)
        assert events == ['authority', 'child', 'authority']


@pytest.mark.parametrize('reproved', [False, True])
def test_cli_only_skips_startup_reconcile_after_completed_retained_hold_proof(monkeypatch, capsys, reproved):
    import argparse
    events = []
    monkeypatch.setitem(sys.modules, 'pixel_access_bridge', SimpleNamespace(AccessError=RuntimeError))
    def recover(*a, **kwargs):
        events.append('recover')
        if reproved: kwargs['on_reproved']()
        return 'active'
    namespace = dict(argparse=argparse, json=json, subprocess=live.subprocess,
        sys=SimpleNamespace(platform='darwin', stderr=sys.stderr), os=SimpleNamespace(geteuid=lambda: 0),
        InstallError=type('Error', (ValueError,), {}),
        _upgrade=SimpleNamespace(UpgradeError=RuntimeError), _recovery_bridge=lambda **k: None,
        recover_install=recover, _reprove_recovered_access=lambda owner: events.append('startup-reconcile'))
    main = source('_recovery_main', namespace)
    assert main(['--owner', 'fixture', '--current-bundle-digest', 'a' * 64, '--bundle-digest', 'b' * 64]) == 0
    assert events == (['recover'] if reproved else ['recover', 'startup-reconcile'])
    assert json.loads(capsys.readouterr().out)['status'] == 'active'


@pytest.mark.parametrize('interference', [None, 'new-turn', 'different-token'])
def test_actual_transition_gate_lost_release_reply_retries_only_original_receipt(monkeypatch, interference):
    gate_module = load('held_proof_actual_transition_gate', ROOT / 'extensions/services/pixel-edge/transition_gate.py')
    gate = object.__new__(gate_module.TransitionGate)
    gate.mutex, gate.active, gate.closing = asyncio.Lock(), set(), False
    gate.path, gate.failure = 'in-memory-custody-fixture', None
    gate.state = gate_module._empty_state()
    # Only persistence/custody IO is replaced; all admission/lease transitions,
    # token comparisons, revision changes and release receipts are actual code.
    gate._check = lambda: None
    gate._write = lambda state: setattr(gate, 'state', state)
    token, revision = 'd' * 64, gate.state['revision']
    status = asyncio.run(gate.acquire(token, revision))
    _, value = fixture()
    value.update(phase='releasing-edge', leaseRevision='2' * 64, proof=proof(value))
    disk = [dict(schemaVersion=1, phase='held', container='c' * 64,
                 binding=dict(token=token, revision=revision), status=status)]
    events, lose = [], [True]
    def request_edge(plan, container, operation, binding):
        events.append(operation)
        result = asyncio.run(getattr(gate, operation)(binding['token'], binding['revision']))
        if operation == 'release' and lose and lose.pop():
            raise TimeoutError('lost successful release reply')
        return result
    monkeypatch.setitem(sys.modules, 'pixel_access_bridge', SimpleNamespace(
        private_json=lambda *a: disk[0], atomic_json=lambda path, value: disk.__setitem__(0, value)))
    finish = source('_finish_migration_hold', dict(_edge_hold_journal=lambda p: 'hold',
        _migration_edge_request=request_edge, InstallError=ValueError))
    adapter = object.__new__(live.Adapter)
    adapter.i = SimpleNamespace(_finish_migration_hold=finish, _migration_edge_request=request_edge, InstallError=ValueError)
    adapter.pure, adapter.plan, adapter.budget = pure, {}, lambda: 1
    adapter.verify = lambda v: None
    adapter.edge = lambda v: disk[0]
    adapter.child_sample = lambda *a: sample(value, ready=True)
    with pytest.raises(TimeoutError): adapter.release_edge(value)
    assert gate.state['phase'] == 'idle' and disk[0]['phase'] == 'releasing'
    assert gate.state['revision'] != revision and gate.state['released']['revision'] == revision
    if interference == 'new-turn':
        asyncio.run(gate.admit('turn'))
        asyncio.run(gate.finish('turn'))
    elif interference == 'different-token':
        asyncio.run(gate.acquire('f' * 64, gate.state['revision']))
    before = copy.deepcopy(gate.state)
    if interference:
        with pytest.raises(gate_module.GateError): adapter.release_edge(value)
        assert gate.state == before and disk[0]['phase'] == 'releasing'
    else:
        adapter.release_edge(value)
        assert disk[0]['phase'] == 'released' and gate.state == before
    assert events == ['acquire', 'release', 'release']


@pytest.mark.parametrize('kind', ['legacy', 'repaired', 'rolled-back', 'incomplete-proof', 'failed-proof'])
@pytest.mark.parametrize('loading', ['portable-function', 'native-module'])
def test_archived_repaired_route_holds_lock_through_proof_and_skips_legacy_release(monkeypatch, tmp_path, kind, loading):
    if loading == 'native-module' and os.name == 'nt':
        pytest.skip('Full POSIX installer import runs in Linux/macOS CI')
    _, _, snapshots, repaired, _ = base.fixture()
    repaired['phase'] = 'rolled-back' if kind == 'rolled-back' else 'repaired'
    hold = repair._hold(snapshots['hold'])
    events, held = [], []
    plan = dict(access_settings={'gateway_port': 18789})
    journal, records = SimpleNamespace(value={'phase': 'active'}), []
    services = {name: name for name in repaired['identities']}
    hold_path, repair_path = tmp_path / 'hold', tmp_path / 'repair'
    hold_path.touch()
    if kind != 'legacy': repair_path.touch()
    @contextmanager
    def locked(**kwargs):
        assert kwargs == {'completed_digest': base.CANDIDATE}
        held.append(True)
        events.append('lock')
        try: yield
        finally:
            held.pop()
            events.append('unlock')
    def run_locked(installer, **kwargs):
        assert held and kwargs['services'] == services and kwargs['records'] == records
        assert kwargs['selection'] == dict(current_digest=base.CURRENT, candidate_digest=base.CANDIDATE, owner_name='fixture')
        events.append('retained-proof')
        if kind == 'failed-proof': raise ValueError('proof failed')
        return {'phase': 'verified' if kind == 'incomplete-proof' else 'complete'}
    namespace = dict(os=os, json=json, re=base.re, HERE=LIB, _repair=repair, InstallError=ValueError,
        ACCESS_FILES={'config': '/fixed-config'}, _destination=lambda p: p,
        _load_upgrade_recovery=lambda **k: (plan, journal, records),
        _verify_recovery_bindings=lambda *a: None, _upgrade_services=lambda *a: (services, services),
        _verify_bundle_selection=lambda p: events.append('selection'),
        _upgrade_service_identity=lambda s: (1, 2, 3), _policy=SimpleNamespace(policy_state=lambda p: {}),
        _ready_gateway=lambda *a: None, _ready_access=lambda *a: None, _ready_access_relay=lambda *a: None,
        _clear_candidate_stop_witnesses=lambda p: None, _edge_hold_journal=lambda p: hold_path,
        _controller_repair_path=lambda d: repair_path, _controller_private_bytes=lambda *a: base.encoded(repaired),
        _finish_migration_hold=lambda *a: events.append('legacy-release'))
    monkeypatch.setitem(sys.modules, 'pixel_access_bridge', SimpleNamespace(private_json=lambda *a: hold))
    monkeypatch.setitem(sys.modules, 'pixel_macos_custody', SimpleNamespace(protected_inspection_bytes=lambda *a, **k: b'{"gateway_policy":{}}'))
    if loading == 'native-module':
        # The shared native installer fixture already owns its POSIX imports.
        test_module = load('held_proof_native_installer_fixture', ROOT / 'tests/test_macos_pixel_access_install.py')
        installer = test_module.installer
        for name, item in namespace.items(): monkeypatch.setattr(installer, name, item, raising=False)
        function = installer._finish_archived_upgrade
    else:
        function = source('_finish_archived_upgrade', namespace)
    fake_util = SimpleNamespace(spec_from_file_location=lambda *a: SimpleNamespace(loader=SimpleNamespace(exec_module=lambda m: None)),
                                module_from_spec=lambda s: SimpleNamespace(run_locked=run_locked))
    if loading == 'native-module': monkeypatch.setattr(installer, 'importlib', SimpleNamespace(util=fake_util))
    else: namespace['importlib'] = SimpleNamespace(util=fake_util)
    invoke = lambda: function(SimpleNamespace(recovery_locked=locked), current_digest=base.CURRENT,
        candidate_digest=base.CANDIDATE, owner_name='fixture', on_reproved=lambda: events.append('proved-callback'))
    if kind in ('incomplete-proof', 'failed-proof'):
        with pytest.raises(ValueError): invoke()
        assert 'proved-callback' not in events and 'legacy-release' not in events
    else:
        assert invoke() == 'active'
        if kind == 'repaired': assert events[-3:] == ['retained-proof', 'proved-callback', 'unlock']
        else: assert events[-2:] == ['legacy-release', 'unlock'] and 'retained-proof' not in events
    assert not held


@pytest.mark.parametrize('fault', [None, 'mode', 'owner', 'gid', 'hardlink', 'symlink'])
def test_actual_child_private_metadata_denies_unsafe_file(fault):
    import stat
    ns = child_namespace()
    info = dict(st_mode=stat.S_IFREG | 0o644, st_uid=0, st_gid=0, st_nlink=1)
    if fault == 'mode': info['st_mode'] |= 0o022
    if fault == 'owner': info['st_uid'] = 501
    if fault == 'gid': info['st_gid'] = 20
    if fault == 'hardlink': info['st_nlink'] = 2
    if fault == 'symlink': info['st_mode'] = stat.S_IFLNK | 0o644
    path = SimpleNamespace(lstat=lambda: SimpleNamespace(**info))
    if fault:
        with pytest.raises(ValueError): ns['private_metadata'](path, 0o644)
    else: ns['private_metadata'](path, 0o644)


@pytest.mark.parametrize('fault', ['private-error', 'large-result', 'oversized-input'])
def test_actual_child_main_keeps_errors_private_and_output_bounded(capsys, fault):
    ns = child_namespace()
    raw = b' ' * 16385 if fault == 'oversized-input' else child.request_bytes(request())
    ns['sys'] = SimpleNamespace(stdin=SimpleNamespace(buffer=io.BytesIO(raw)))
    def run(value):
        if fault == 'private-error': raise RuntimeError('SECRET-PRIVATE-PATH-CREDENTIAL')
        return {'value': 'x' * 9000}
    ns['run'] = run
    assert ns['main']() == 1
    output = capsys.readouterr()
    assert output.out == '{"error":"controller-held-proof-child-failed"}\n'
    assert not output.err
