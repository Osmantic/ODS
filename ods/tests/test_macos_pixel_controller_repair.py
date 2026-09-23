"""Portable authority/state-machine tests; these do not qualify a live Mac."""
import ast
import base64
import copy
from contextlib import contextmanager
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
import stat
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('controller_repair',
    ROOT / 'installers/macos/lib/pixel-controller-repair.py')
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)
LIVE_SPEC = importlib.util.spec_from_file_location('controller_repair_live',
    ROOT / 'installers/macos/lib/pixel-controller-repair-live.py')
live = importlib.util.module_from_spec(LIVE_SPEC)
LIVE_SPEC.loader.exec_module(live)
CURRENT, CANDIDATE = 'a' * 64, 'b' * 64
OWNER = {'name': 'fixture', 'uid': 501, 'gid': 20}


def encoded(value):
    return (json.dumps(value, sort_keys=True) + '\n').encode()


def fixture():
    # Use the actual reviewed 6410 source bytes, not a placeholder Python file.
    after = (ROOT / 'bin/pixel_access_bridge.py').read_bytes().replace(b'\r\n', b'\n')
    assert repair.sha(after) == repair.AFTER
    before = after.replace(repair.NEW_HUNK, repair.OLD_HUNK, 1)
    assert repair.sha(before) == repair.BEFORE
    item = {'path': repair.TARGET, 'mode': 0o644, 'gid': 0,
            'before': base64.b64encode(before).decode(), 'after': base64.b64encode(before).decode(),
            'beforeSha256': repair.BEFORE, 'afterSha256': repair.BEFORE}
    archive = {'schemaVersion': 1, 'currentDigest': CURRENT, 'candidateDigest': CANDIDATE,
               'phase': 'active', 'files': [item]}
    held = {'schemaVersion': 1, 'container': 'c' * 64,
            'binding': {'token': 'd' * 64, 'revision': 'e' * 64}, 'phase': 'held',
            'status': {'capability': 'available', 'phase': 'held', 'revision': 'e' * 64,
                       'streams': 0, 'admission_blocked': True}}
    snapshots = {'archive': encoded(archive), 'context': b'{"approved":"context"}',
                 'service': b'{"approved":"services"}', 'hold': encoded(held)}
    identities = {role: [n + 1, 1234, 55] + ([] if role == 'access' else [501, 20, 501, 20, 501, 20, '/verified/executable']) for n, role in enumerate(
        ('gateway', 'access', 'relay', 'operations', 'manager', 'promoter'))}
    intent = repair.make_intent(current=CURRENT, candidate=CANDIDATE,
        owner=OWNER, snapshots=snapshots, identities=identities)
    records = [dict(path=repair.TARGET, mode=0o644, gid=0, before=before, after=before),
               dict(path='/unrelated', mode=0o600, gid=0, before=b'old', after=b'keep')]
    return before, after, snapshots, intent, records


def projection(records, intent, snapshots):
    return repair.effective_records(records, intent, current=CURRENT, candidate=CANDIDATE,
                                    owner=OWNER, snapshots=snapshots)


def test_exact_reviewed_hunk_only():
    before, after, *_ = fixture()
    assert repair.replacement(before) == after
    for changed in (before + b'\n', before.replace(b"'extra_groups'", b"'other_groups'"), after):
        with pytest.raises(repair.RepairError):
            repair.replacement(changed)


@pytest.mark.parametrize('field', ['archive', 'context', 'service', 'hold'])
def test_every_authority_snapshot_is_bound(field):
    _, _, snapshots, intent, _ = fixture()
    snapshots[field] += b' '
    with pytest.raises(repair.RepairError):
        repair.validate_intent(intent, current=CURRENT, candidate=CANDIDATE, owner=OWNER, snapshots=snapshots)


@pytest.mark.parametrize('change', [
    lambda i: i.update(extra=True), lambda i: i.update(repair='arbitrary-patch'),
    lambda i: i.update(candidateDigest='f' * 64), lambda i: i.update(currentDigest='f' * 64),
    lambda i: i['owner'].update(uid=0), lambda i: i['target'].update(path='/elsewhere'),
    lambda i: i['target'].update(mode=0o777), lambda i: i['target'].update(gid=20),
    lambda i: i['target'].update(afterSha256='f' * 64), lambda i: i.update(phase='ready'),
    lambda i: i.update(schemaVersion=True), lambda i: i['target'].update(uid=False),
    lambda i: i['identities']['gateway'].pop(), lambda i: i['identities']['access'].append('/wrong'),
    lambda i: i['identities']['access'].__setitem__(2, 1000000),
    lambda i: i.update(heldRecord=base64.b64encode(b'{}').decode()),
])
def test_intent_rejects_changed_scope_or_metadata(change):
    _, _, snapshots, intent, _ = fixture(); change(intent)
    with pytest.raises(repair.RepairError):
        repair.validate_intent(intent, current=CURRENT, candidate=CANDIDATE, owner=OWNER, snapshots=snapshots)


@pytest.mark.parametrize('phase', sorted(repair.PHASES - {'repaired', 'rolled-back'}))
def test_incomplete_sidecar_blocks_recovery_and_finalization(phase):
    _, _, snapshots, intent, records = fixture(); intent['phase'] = phase
    with pytest.raises(repair.RepairError, match='incomplete'):
        projection(records, intent, snapshots)


@pytest.mark.parametrize('phase', ['held', 'releasing', 'released'])
def test_completed_overlay_survives_only_the_genuine_hold_release_progression(phase):
    _, after, snapshots, intent, records = fixture(); intent['phase'] = 'repaired'
    original = copy.deepcopy((snapshots, records))
    held = json.loads(snapshots['hold']); held['phase'] = phase
    snapshots['hold'] = snapshots['hold'] if phase == 'held' else json.dumps(held).encode()
    result = projection(records, intent, snapshots)
    assert result[0]['after'] == after and result[0]['before'] == records[0]['before']
    assert result[1] == records[1]
    assert records == original[1] and snapshots['archive'] == original[0]['archive']
    held['binding']['revision'] = 'f' * 64; snapshots['hold'] = encoded(held)
    with pytest.raises(repair.RepairError):
        projection(records, intent, snapshots)


def test_no_sidecar_or_verified_rollback_gives_no_exception():
    _, _, snapshots, intent, records = fixture()
    assert projection(records, None, snapshots) is records
    intent['phase'] = 'rolled-back'
    assert projection(records, intent, snapshots) is records
    held = json.loads(snapshots['hold']); held['phase'] = 'released'
    snapshots['hold'] = encoded(held)
    with pytest.raises(repair.RepairError):
        projection(records, intent, snapshots)


@pytest.mark.parametrize('change', [
    lambda r: r[0].update(after=b'unknown'), lambda r: r[0].update(mode=0o600),
    lambda r: r.pop(0), lambda r: r.append(copy.deepcopy(r[0])),
])
def test_overlay_is_exactly_one_original_record(change):
    _, _, snapshots, intent, records = fixture(); intent['phase'] = 'repaired'; change(records)
    with pytest.raises(repair.RepairError):
        projection(records, intent, snapshots)


class FaultAdapter:
    def __init__(self, *, failure=None, after=False):
        self.before, self.after, self.snapshots, self.value, self.records = fixture()
        self.body, self.running, self.new_process = self.before, True, False
        self.failure, self.fail_after, self.events = failure, after, []
        self.hold = True

    def event(self, name, action=lambda: None):
        self.events.append(name)
        fail = len(self.events) - 1 == self.failure
        if fail and not self.fail_after:
            raise OSError('injected before effect')
        action()
        if fail:
            raise OSError('injected after effect')

    def verify(self, value, allowed, *, terminal=False):
        def action():
            assert value == self.value and self.hold
            repair.validate_intent(value, current=CURRENT, candidate=CANDIDATE,
                owner=OWNER, snapshots=self.snapshots)
            if repair.sha(self.body) not in allowed:
                raise repair.RepairError('unknown/phase-inconsistent bytes')
            if terminal:
                assert self.running and self.new_process
        self.event('verify:' + value['phase'], action)

    def persist(self, previous, updated):
        assert previous == self.value
        self.event('persist:' + updated['phase'], lambda: setattr(self, 'value', copy.deepcopy(updated)))

    def stop(self, value):
        self.event('stop', lambda: setattr(self, 'running', False))

    def replace(self, value, *, rollback):
        assert not self.running
        self.event('cas', lambda: setattr(self, 'body', self.before if rollback else self.after))

    def start(self, value):
        def action():
            self.running = self.new_process = True
        self.event('start', action)


def trace(rollback=False):
    adapter = FaultAdapter()
    repair.execute(adapter.value, adapter, rollback=rollback)
    return adapter.events


@pytest.mark.parametrize('rollback', [False, True])
@pytest.mark.parametrize('after', [False, True])
def test_every_durable_phase_and_effect_resumes_under_the_original_hold(rollback, after):
    for index, event in enumerate(trace(rollback)):
        adapter = FaultAdapter(failure=index, after=after)
        original = copy.deepcopy(adapter.snapshots)
        with pytest.raises(OSError):
            repair.execute(adapter.value, adapter, rollback=rollback)
        assert adapter.hold and adapter.snapshots == original, (event, after)
        adapter.failure = None
        result = repair.execute(adapter.value, adapter, rollback=rollback)
        assert result['phase'] == ('rolled-back' if rollback else 'repaired')
        assert adapter.body == (adapter.before if rollback else adapter.after)
        assert adapter.hold and adapter.snapshots == original
        count = adapter.events.count('stop') + adapter.events.count('cas') + adapter.events.count('start')
        repair.execute(adapter.value, adapter, rollback=rollback)
        assert adapter.events.count('stop') + adapter.events.count('cas') + adapter.events.count('start') == count


@pytest.mark.parametrize('phase', sorted(repair.PHASES))
def test_unknown_bytes_never_authorize_resume_or_rollback(phase):
    adapter = FaultAdapter(); adapter.value['phase'] = phase; adapter.body = b'unknown'
    for rollback in [False, True]:
        with pytest.raises(repair.RepairError):
            repair.execute(adapter.value, adapter, rollback=rollback)
    assert adapter.hold and not {'stop', 'cas', 'start'} & set(adapter.events)


def test_stopped_label_without_a_valid_new_generation_witness_stays_held():
    adapter = FaultAdapter(); adapter.value['phase'] = 'starting'; adapter.body = adapter.after
    adapter.running = False
    def ambiguous(_value):
        raise repair.RepairError('native-stop-witness-unavailable')
    adapter.start = ambiguous
    with pytest.raises(repair.RepairError, match='stop-witness'):
        repair.execute(adapter.value, adapter)
    assert adapter.hold and adapter.value['phase'] == 'starting'


def test_archive_json_duplicate_fields_do_not_supply_repair_authority():
    _, _, snapshots, intent, _ = fixture()
    snapshots['archive'] = snapshots['archive'].replace(b'{', b'{"phase":"forged",', 1)
    with pytest.raises(repair.RepairError):
        repair.validate_intent(intent, current=CURRENT, candidate=CANDIDATE, owner=OWNER, snapshots=snapshots)


@pytest.mark.parametrize('after', [False, True])
def test_explicit_rollback_from_each_interrupted_forward_step(after):
    for index in range(len(trace())):
        adapter = FaultAdapter(failure=index, after=after)
        with pytest.raises(OSError):
            repair.execute(adapter.value, adapter)
        adapter.failure = None
        result = repair.execute(adapter.value, adapter, rollback=True)
        assert result['phase'] == 'rolled-back' and adapter.body == adapter.before and adapter.hold


@pytest.mark.parametrize('phase', ['held', 'releasing', 'released'])
def test_finalization_requires_ordinary_recovery_to_release_the_bound_hold(phase):
    _, _, snapshots, intent, records = fixture(); intent['phase'] = 'repaired'
    hold = json.loads(snapshots['hold']); hold['phase'] = phase
    if phase != 'held':
        snapshots['hold'] = json.dumps(hold).encode()
    projection(records, intent, snapshots)
    if phase == 'released':
        repair.require_finalizable(intent, snapshots)
    else:
        with pytest.raises(repair.RepairError, match='runtime-recovery-required'):
            repair.require_finalizable(intent, snapshots)


def source_function(relative, name, namespace):
    # Exercise the real function body portably without pretending Windows can
    # import the installer's POSIX dependencies or execute privileged adapters.
    path = ROOT / relative
    node, = [node for node in ast.walk(ast.parse(path.read_text()))
             if isinstance(node, ast.FunctionDef) and node.name == name]
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]


def test_reviewed_replacement_launch_identity_clears_seventeen_owner_groups(monkeypatch):
    owner = SimpleNamespace(pw_name='fixture', pw_uid=501, pw_gid=20)
    queried = []
    fake_os = SimpleNamespace(geteuid=lambda: 0,
        getgrouplist=lambda *args: queried.append(args) or list(range(17)))
    monkeypatch.setitem(sys.modules, 'pwd', SimpleNamespace(getpwnam=lambda name: owner))
    identity = source_function('bin/pixel_access_bridge.py', '_native_owner_identity',
        {'os': fake_os, 'AccessError': ValueError})
    assert identity(SimpleNamespace(owner=owner)) == {'user': 501, 'group': 20, 'extra_groups': []}
    assert queried == []


@pytest.mark.parametrize('fault', [None, 'held', 'releasing', 'incomplete', 'unknown-bytes',
    'no-proof', 'wrong-policy', 'pending', 'busy', 'unavailable', 'hold-changed-during-proof',
    'late-proof-loss', 'late-access-birth', 'late-gateway-birth', 'late-transition', 'late-policy',
    'late-launchctl-proof-loss', 'final-status-birth-change', 'late-revision', 'lock-busy',
    'repair-removed-before-lock', 'archive-changed-before-lock'])
@pytest.mark.parametrize('loading', ['portable-function', 'native-module'])
def test_real_finalizer_path_never_promotes_repair_to_access_readiness(monkeypatch, fault, loading):
    if loading == 'native-module' and os.name == 'nt':
        pytest.skip('Full POSIX module import runs in Linux/macOS CI')
    before, after, snapshots, intent, records = fixture()
    intent['phase'] = 'verifying' if fault == 'incomplete' else 'repaired'
    hold = json.loads(snapshots['hold'])
    hold['phase'] = fault if fault in ('held', 'releasing') else 'released'
    if hold['phase'] != 'held':
        snapshots['hold'] = json.dumps(hold).encode()
    raw = encoded(intent)
    archive = json.loads(snapshots['archive'])
    state = Path('/private/var/lib/ods-pixel-access')
    repair_path = state / ('runtime-controller-repair-' + CANDIDATE + '.json')
    selection = dict(expected_digest='f' * 64, expected_ref='a' * 40)
    plan = {'owner': SimpleNamespace(pw_name='fixture', pw_uid=501, pw_gid=20),
            'access_settings': {'gateway_policy': {}}}
    status = dict(available=True, surface='darwin', scope='owner-host', configured_mode='sandboxed',
        effective_mode='sandboxed', runtime_verified=True, pending=False, busy=False, reason=None)
    if fault == 'no-proof': status.update(effective_mode='unknown', runtime_verified=False, reason='runtime-proof-required')
    if fault == 'wrong-policy': status.update(effective_mode='full-access', configured_mode='full-access')
    if fault in ('pending', 'busy'): status[fault] = True
    if fault == 'unavailable': status.update(available=False)
    checks, transitions = [], set()
    process_identities = {'access': [123, 456, 0], 'gateway': [789, 456, 0], 'relay': [900, 456, 0]}
    locked, repair_present, status_reads, launchctl_reads = False, True, 0, 0
    @contextmanager
    def recovery_locked(*, completed_digest):
        nonlocal locked, repair_present
        assert completed_digest == CANDIDATE and not locked
        if fault == 'lock-busy': raise ValueError('transition-busy')
        locked = True
        checks.append('lock')
        if fault == 'repair-removed-before-lock': repair_present = False
        if fault == 'archive-changed-before-lock': archive['phase'] = 'restored'
        try:
            yield
        finally:
            locked = False
            checks.append('unlock')
    def recovery_bridge(**kwargs):
        assert kwargs == dict(current_digest=CURRENT, candidate_digest=CANDIDATE, owner_name='fixture')
        return SimpleNamespace(recovery_locked=recovery_locked)
    def load(**kwargs):
        assert locked
        checks.append('effective-records')
        assert kwargs == dict(current_digest=CURRENT, candidate_digest=CANDIDATE,
                              owner_name='fixture', completed=True)
        return plan, SimpleNamespace(value=archive), projection(records, intent, snapshots)
    def live_status(*args, **kwargs):
        nonlocal status_reads
        assert locked
        status_reads += 1
        assert kwargs == {'owner_gid': OWNER['gid']}
        checks.append('read-only-access-proof')
        if fault == 'hold-changed-during-proof': snapshots['hold'] += b' '
        if fault == 'final-status-birth-change' and status_reads == 2: process_identities['access'][0] += 1
        return dict(status)
    def verify_services(value):
        assert locked
        checks.append('services-ready')
        if fault == 'late-proof-loss': status.update(available=False, runtime_verified=False, reason='inspection-failed')
        if fault == 'late-access-birth': process_identities['access'][0] += 1
        if fault == 'late-gateway-birth': process_identities['gateway'][0] += 1
        if fault == 'late-transition': transitions.add('transition.json')
        if fault == 'late-policy': transitions.add('policy-activation.json')
        if fault == 'late-revision': status['revision'] = 'different-proof'
    def launchctl(*args, **kwargs):
        nonlocal launchctl_reads
        assert locked
        launchctl_reads += 1
        if fault == 'late-launchctl-proof-loss' and launchctl_reads == 3:
            status.update(available=False, runtime_verified=False, reason='inspection-failed')
        return SimpleNamespace(stdout='state = running\n')
    installer = SimpleNamespace(_controller_repair_path=lambda value: repair_path,
        _controller_private_bytes=lambda *args: raw, _repair=repair,
        _load_upgrade_recovery=load, _verify_recovery_bindings=lambda *args: None,
        _controller_repair_snapshots=lambda value: dict(snapshots),
        _upgrade_services=lambda *args: (None, {name: name for name in process_identities}),
        _upgrade_service_identity=lambda service: tuple(process_identities[service]),
        _recovery_bridge=recovery_bridge,
        _policy=SimpleNamespace(policy_state=lambda value: {'activeMode': 'sandboxed'}),
        _verify_new_services=verify_services)
    monkeypatch.setitem(sys.modules, 'pixel_access_bridge', SimpleNamespace(private_json=lambda path, *args:
        {'selection': selection} if path.name == 'service-installation.json' else archive))
    monkeypatch.setitem(sys.modules, 'pixel_macos_custody', SimpleNamespace(protected_bytes=lambda path, **kwargs:
        (b'unknown' if fault == 'unknown-bytes' else after) if path == repair.TARGET else b'keep'))
    namespace = dict(sys=SimpleNamespace(platform='darwin', path=[]),
        os=SimpleNamespace(geteuid=lambda: 0, path=SimpleNamespace(lexists=lambda path:
            path == repair_path and repair_present or path.name in transitions)),
        re=re, Path=Path, HERE=ROOT / 'installers/macos/lib', base64=base64,
        hashlib=__import__('hashlib'), pwd=SimpleNamespace(getpwnam=lambda name: plan['owner']),
        helper=lambda name: installer if name == 'pixel-macos-access-install' else SimpleNamespace(controller_status=live_status),
        subprocess=SimpleNamespace(run=launchctl))
    if loading == 'native-module':
        spec = importlib.util.spec_from_file_location('repair_native_finalize',
            ROOT / 'installers/macos/lib/pixel-native-finalize.py')
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        for name, value in namespace.items():
            monkeypatch.setattr(native, name, value)
        proof = native.protected_proof
    else:
        source_function('installers/macos/lib/pixel-native-finalize.py', '_protected_selection_proof', namespace)
        proof = source_function('installers/macos/lib/pixel-native-finalize.py', 'protected_proof', namespace)
    if fault:
        with pytest.raises(ValueError):
            proof('fixture', CANDIDATE, 'f' * 64, 'a' * 40)
    else:
        assert proof('fixture', CANDIDATE, 'f' * 64, 'a' * 40) == dict(
            status='active', runtimeDigest=CANDIDATE, serviceDigest='f' * 64)
        assert checks == ['lock', 'effective-records', 'read-only-access-proof', 'services-ready',
                          'read-only-access-proof', 'unlock']
    assert not locked


@pytest.mark.parametrize('pending', [False, True])
def test_unrepaired_finalization_keeps_the_existing_unlocked_contract(monkeypatch, pending):
    body = b'activated'
    archive = {'phase': 'active', 'candidateDigest': CANDIDATE, 'files': [{
        'path': '/protected/file', 'after': base64.b64encode(body).decode(), 'afterSha256': repair.sha(body)}]}
    selection = dict(expected_digest='f' * 64, expected_ref='a' * 40)
    checked = []
    # Deliberately no recovery bridge, repair verifier, or access-status API:
    # an ordinary archive still follows its original public proof contract.
    installer = SimpleNamespace(_controller_repair_path=lambda value: Path('/protected/repair.json'),
        _verify_new_services=lambda plan: checked.append(plan))
    monkeypatch.setitem(sys.modules, 'pixel_access_bridge', SimpleNamespace(private_json=lambda path, *args:
        {'selection': selection} if path.name == 'service-installation.json' else archive))
    monkeypatch.setitem(sys.modules, 'pixel_macos_custody', SimpleNamespace(protected_bytes=lambda *args, **kwargs: body))
    namespace = dict(sys=SimpleNamespace(platform='darwin', path=[]),
        os=SimpleNamespace(geteuid=lambda: 0, path=SimpleNamespace(lexists=lambda path: pending and path.name == 'runtime-upgrade.json')),
        re=re, Path=Path, HERE=ROOT / 'installers/macos/lib', base64=base64, hashlib=__import__('hashlib'),
        pwd=SimpleNamespace(getpwnam=lambda owner: SimpleNamespace(pw_uid=501)), helper=lambda name: installer,
        subprocess=SimpleNamespace(run=lambda *args, **kwargs: SimpleNamespace(stdout='state = running\n')))
    source_function('installers/macos/lib/pixel-native-finalize.py', '_protected_selection_proof', namespace)
    proof = source_function('installers/macos/lib/pixel-native-finalize.py', 'protected_proof', namespace)
    if pending:
        with pytest.raises(ValueError, match='still-pending'): proof('fixture', CANDIDATE, 'f' * 64, 'a' * 40)
        assert checked == []
    else:
        assert proof('fixture', CANDIDATE, 'f' * 64, 'a' * 40)['status'] == 'active'
        assert len(checked) == 1


@pytest.mark.parametrize('field', ['available', 'scope', 'configured_mode', 'effective_mode',
    'runtime_verified', 'busy', 'pending', 'reason'])
@pytest.mark.parametrize('value', [None, False, True, '', 'unknown', 'sandboxed', 'full-access', 0, 1])
def test_pure_access_readiness_matches_startup_reproof_contract(field, value):
    original = source_function('bin/pixel_access_reconcile.py', 'ready', {})
    status = dict(available=True, scope='owner-host', configured_mode='sandboxed', effective_mode='sandboxed',
                  runtime_verified=True, busy=False, pending=False, reason=None)
    status[field] = value
    assert repair.access_ready(status) == original(status)


@pytest.mark.parametrize('fault', [None, 'wrong-group', 'wrong-owner', 'wrong-mode', 'not-socket',
    'pid-changed', 'missing-newline', 'oversize', 'bad-status', 'extra-field', 'duplicate-field'])
def test_controller_status_binds_socket_metadata_and_process_birth(monkeypatch, fault):
    metadata = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=0, st_gid=20)
    if fault == 'wrong-group': metadata.st_gid = 0
    if fault == 'wrong-owner': metadata.st_uid = 501
    if fault == 'wrong-mode': metadata.st_mode = stat.S_IFSOCK | 0o666
    if fault == 'not-socket': metadata.st_mode = stat.S_IFREG | 0o660
    response = {'status': 200, 'body': {'available': True}}
    if fault == 'bad-status': response['status'] = 200.0
    if fault == 'extra-field': response['extra'] = True
    raw = encoded(response)
    if fault == 'missing-newline': raw = raw.rstrip()
    if fault == 'oversize': raw = b'x' * 65537 + b'\n'
    if fault == 'duplicate-field': raw = raw.replace(b'{', b'{"status":500,', 1)
    sent = []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, value): assert value == 20
        def connect(self, address): sent.append(('connect', address))
        def sendall(self, body): sent.append(('send', body))
        def makefile(self, mode): return io.BytesIO(raw)
    monkeypatch.setattr(live, 'socket', SimpleNamespace(AF_UNIX=1, SOCK_STREAM=1, socket=lambda *args: Connection()))
    identities = iter([(99, 123, 0), (100 if fault == 'pid-changed' else 99, 123, 0)])
    installer = SimpleNamespace(_upgrade_service_identity=lambda service: next(identities),
        _launchd=SimpleNamespace(ACCESS_SOCKET=SimpleNamespace(lstat=lambda: metadata)),
        _repair=repair, InstallError=ValueError)
    if fault:
        with pytest.raises(ValueError): live.controller_status(installer, object(), owner_gid=20)
        if fault in ('wrong-group', 'wrong-owner', 'wrong-mode', 'not-socket'): assert sent == []
    else:
        assert live.controller_status(installer, object(), owner_gid=20) == {'available': True}
        assert sent[-1] == ('send', b'{"operation":"status"}\n')


class PublicationFilesystem:
    """Map the adapter's dirfd calls onto a temporary directory on any OS.

    This checks publication ordering/fault handling, not POSIX custody itself.
    Existing custody tests and Linux/macOS CI cover the real dirfd primitives.
    """
    def __init__(self, root, failure=None, after=False):
        self.root, self.failure, self.after, self.events = root, failure, after, []
        self.O_WRONLY, self.O_CREAT, self.O_EXCL, self.O_NOFOLLOW = os.O_WRONLY, os.O_CREAT, os.O_EXCL, 0
    def effect(self, name, action):
        index = len(self.events); self.events.append(name)
        failed = index == self.failure
        if failed: self.failure = None
        if failed and not self.after: raise OSError('injected before ' + name)
        result = action()
        if failed: raise OSError('injected after ' + name)
        return result
    def urandom(self, count): return b'a' * count
    def fstat(self, fd): return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700) if fd == -1 else os.fstat(fd)
    def fchown(self, fd, uid, gid):
        assert (uid, gid) == (0, 0)
        return self.effect('ownership', lambda: None)
    def open(self, name, flags, mode, *, dir_fd):
        assert dir_fd == -1
        return os.open(self.root / name, flags, mode)
    def fdopen(self, fd, mode):
        original, fs = os.fdopen(fd, mode), self
        class Stream:
            def __enter__(self): return self
            def __exit__(self, *args): original.close()
            def fileno(self): return original.fileno()
            def write(self, body): return fs.effect('write', lambda: original.write(body))
            def flush(self): return fs.effect('flush', original.flush)
        return Stream()
    def fsync(self, fd): return self.effect('directory-fsync' if fd == -1 else 'file-fsync', lambda: None if fd == -1 else os.fsync(fd))
    def stat(self, name, **kwargs): return self.effect('absence-check', lambda: os.stat(self.root / name, follow_symlinks=False))
    def rename(self, before, after, **kwargs): return self.effect('rename', lambda: os.rename(self.root / before, self.root / after))
    def unlink(self, name, **kwargs): return os.unlink(self.root / name)


def publication_adapter(monkeypatch, root, failure=None, after=False):
    filesystem = PublicationFilesystem(root, failure, after)
    @contextmanager
    def protected_directory(path):
        assert path == root
        yield -1
    monkeypatch.setitem(sys.modules, 'pixel_macos_custody', SimpleNamespace(
        protected_directory=protected_directory, _verify_fd=lambda *args, **kwargs: None))
    monkeypatch.setattr(live, 'os', filesystem)
    adapter = object.__new__(live.Adapter)
    adapter.i = SimpleNamespace(InstallError=ValueError)
    return adapter, filesystem


@pytest.mark.parametrize('after', [False, True])
def test_initial_intent_publication_faults_leave_absence_or_complete_single_link_authority(monkeypatch, tmp_path, after):
    baseline = tmp_path / 'baseline'; baseline.mkdir()
    adapter, fs = publication_adapter(monkeypatch, baseline)
    adapter._write(baseline / 'intent.json', {'phase': 'prepared'})
    for index, event in enumerate(fs.events):
        if after and event == 'absence-check':
            # This successful precondition is a FileNotFoundError, not a
            # returned effect after which the test can inject another error.
            continue
        directory = tmp_path / str(index); directory.mkdir()
        adapter, _ = publication_adapter(monkeypatch, directory, index, after)
        target = directory / 'intent.json'
        with pytest.raises(OSError): adapter._write(target, {'phase': 'prepared'})
        if target.exists():
            assert json.loads(target.read_bytes()) == {'phase': 'prepared'}
            assert target.stat().st_nlink == 1
        assert not list(directory.glob('.controller-repair-*'))


def test_initial_publication_never_overwrites_an_existing_intent(monkeypatch, tmp_path):
    adapter, _ = publication_adapter(monkeypatch, tmp_path)
    target = tmp_path / 'intent.json'
    adapter._write(target, {'phase': 'prepared'})
    before = target.read_bytes()
    with pytest.raises(ValueError, match='already-exists'):
        adapter._write(target, {'phase': 'forged'})
    assert target.read_bytes() == before


@pytest.mark.parametrize('phase,running,identity,witness,expected', [
    ('stopping', True, 'old', True, 'stop'),
    ('stopping', True, 'new', True, 'reject'),
    ('stopping', False, 'old', True, 'witness-only'),
    ('stopping', False, 'old', False, 'reject'),
    ('starting', True, 'new', True, 'already-started'),
    ('starting', True, 'old', True, 'reject'),
    ('starting', False, 'old', True, 'start'),
    ('starting', False, 'old', False, 'reject'),
])
def test_real_service_adapter_preserves_birth_and_stop_witness_authority(phase, running, identity, witness, expected):
    _, _, _, intent, _ = fixture(); intent['phase'] = phase
    events = []
    def assert_stopped():
        events.append('witness')
        if not witness: raise ValueError('missing-stop-witness')
    adapter = object.__new__(live.Adapter)
    adapter.loaded = lambda: running
    adapter.access = SimpleNamespace(assert_stopped=assert_stopped)
    adapter.i = SimpleNamespace(InstallError=ValueError,
        _upgrade_service_identity=lambda value: intent['identities']['access'] if identity == 'old' else [999, 5678, 0],
        _stop_upgrade_service=lambda value: events.append('stop'),
        _start_upgrade_service=lambda value: events.append('start'))
    method = adapter.stop if phase == 'stopping' else adapter.start
    if expected == 'reject':
        with pytest.raises(ValueError): method(intent)
        assert not {'stop', 'start'} & set(events)
    else:
        method(intent)
        assert events == {'stop': ['stop'], 'witness-only': ['witness'],
                          'start': ['witness', 'start'], 'already-started': []}[expected]


@pytest.mark.parametrize('fault', [None, 'pending-upgrade', 'pending-policy', 'archive', 'context', 'service',
    'hold', 'sidecar-type', 'target-bytes', 'other-file', 'metadata', 'policy', 'disabled-service',
    'other-process', 'old-access-process', 'unavailable', 'pending-status', 'busy-status',
    'bad-scope', 'bad-surface', 'bad-reason', 'services-readiness', 'gateway-readiness', 'late-authority-drift'])
def test_real_adapter_revalidates_all_authority_before_terminal_repair(monkeypatch, fault):
    before, after, snapshots, intent, records = fixture(); intent['phase'] = 'verifying'
    if fault in ('archive', 'context', 'service', 'hold'): snapshots[fault] += b' '
    plan = {'owner': SimpleNamespace(pw_name='fixture', pw_uid=501, pw_gid=20),
            'access_settings': {'gateway_policy': {}, 'gateway_port': 18789}}
    journal = SimpleNamespace(value=json.loads(snapshots['archive']))
    services = {name: SimpleNamespace(target=name) for name in intent['identities']}
    metadata = []
    def check_existing(path, body, **kwargs):
        metadata.append((path, kwargs))
        if fault == 'metadata': raise ValueError('wrong-metadata')
    def readiness(which):
        if fault == which + '-readiness': raise ValueError('not-ready')
        if fault == 'late-authority-drift' and which == 'gateway': snapshots['context'] += b' '
    def process(service):
        identity = intent['identities'][service.target]
        if service.target == 'access' and fault != 'old-access-process': return [999, 5678, 0]
        if service.target == 'gateway' and fault == 'other-process': return [999] + identity[1:]
        return identity
    def policy(value):
        if fault == 'policy': raise ValueError('wrong-policy')
    pending_name = {'pending-upgrade': 'runtime-upgrade.json', 'pending-policy': 'policy-activation.json'}.get(fault)
    monkeypatch.setattr(live, 'os', SimpleNamespace(path=SimpleNamespace(lexists=lambda path: path.name == pending_name)))
    adapter = object.__new__(live.Adapter)
    adapter.selection = dict(current_digest=CURRENT, candidate_digest=CANDIDATE, owner_name='fixture')
    adapter.plan, adapter.journal, adapter.records = plan, journal, records
    adapter.r, adapter.services, adapter.access = repair, services, services['access']
    adapter.snapshots = lambda: dict(snapshots)
    actual = copy.deepcopy(intent)
    if fault == 'sidecar-type': actual['target']['uid'] = False
    adapter.document = lambda: (encoded(actual), actual)
    adapter.read = lambda path, **kwargs: ((b'unknown' if fault == 'target-bytes' else after) if path == repair.TARGET
        else b'changed' if fault == 'other-file' else b'keep')
    held_checks = []
    adapter.hold = lambda value: held_checks.append(value)
    adapter.i = SimpleNamespace(InstallError=ValueError,
        _launchd=SimpleNamespace(ACCESS_STATE=Path('/protected/state')),
        _load_upgrade_recovery_base=lambda **kwargs: (plan, journal, records),
        _verify_recovery_bindings=lambda *args: None, _controller_repair_owner=lambda value: OWNER,
        _check_existing=check_existing, _policy=SimpleNamespace(policy_state=policy),
        _job_disabled=lambda target: fault == 'disabled-service', _upgrade_service_identity=process,
        _verify_new_services=lambda value: readiness('services'), _ready_gateway=lambda *args: readiness('gateway'))
    status = dict(available=True, surface='darwin', scope='owner-host', pending=False, busy=False, reason='runtime-proof-required')
    if fault == 'unavailable': status['available'] = False
    if fault == 'pending-status': status['pending'] = True
    if fault == 'busy-status': status['busy'] = True
    if fault == 'bad-scope': status['scope'] = 'unknown'
    if fault == 'bad-surface': status['surface'] = 'unknown'
    if fault == 'bad-reason': status['reason'] = 'inspection-failed'
    monkeypatch.setattr(live, 'controller_status', lambda *args, **kwargs: status)
    if fault:
        with pytest.raises(ValueError): adapter.verify(intent, {repair.AFTER}, terminal=True)
    else:
        adapter.verify(intent, {repair.AFTER}, terminal=True)
        assert len(metadata) == len(records) and len(held_checks) == 1


@pytest.mark.parametrize('fault', [None, 'released', 'different-container', 'not-running', 'different-hold'])
def test_real_hold_adapter_only_reads_the_bound_live_container(monkeypatch, fault):
    _, _, snapshots, _, _ = fixture()
    hold = json.loads(snapshots['hold'])
    if fault == 'released': hold['phase'] = 'released'; snapshots['hold'] = encoded(hold)
    commands, requests = [], []
    container = ('f' if fault == 'different-container' else 'c') * 64
    running = 'false' if fault == 'not-running' else 'true'
    def command(args, **kwargs):
        commands.append(args)
        assert args[:3] == ['docker', 'inspect', 'c' * 64]
        return SimpleNamespace(stdout=f'{container} {running} pixel-edge')
    def request(plan, selected):
        requests.append((plan, selected))
        return dict(hold['status'], admission_blocked=False) if fault == 'different-hold' else hold['status']
    adapter = object.__new__(live.Adapter)
    adapter.r, adapter.plan = repair, {'approved': True}
    adapter.i = SimpleNamespace(InstallError=ValueError, _migration_edge_context=lambda value: {},
        subprocess=SimpleNamespace(run=command, PIPE=-1, DEVNULL=-2), _migration_edge_request=request)
    if fault:
        with pytest.raises(ValueError): adapter.hold(snapshots)
    else:
        adapter.hold(snapshots)
        assert len(commands) == len(requests) == 1


@pytest.mark.parametrize('loading', ['portable-function', 'native-module'])
@pytest.mark.parametrize('fault', [None, 'absent', 'incomplete', 'pending', 'context-drift',
    'archive-drift', 'sidecar-drift', 'late-context-drift'])
def test_installer_recovery_wrapper_applies_only_the_validated_completed_exception(monkeypatch, loading, fault):
    if loading == 'native-module' and os.name == 'nt':
        pytest.skip('Full POSIX installer import runs in Linux/macOS CI')
    _, after, snapshots, intent, records = fixture()
    intent['phase'] = 'verifying' if fault == 'incomplete' else 'repaired'
    archive = json.loads(snapshots['archive'])
    if fault == 'archive-drift': snapshots['archive'] = encoded(dict(archive, phase='restored'))
    if fault == 'context-drift': snapshots['context'] += b' '
    calls = {'sidecar': 0, 'snapshots': 0}
    def read(*args):
        calls['sidecar'] += 1
        return encoded(intent) + (b' ' if fault == 'sidecar-drift' and calls['sidecar'] > 1 else b'')
    def snapshot(*args):
        calls['snapshots'] += 1
        value = dict(snapshots)
        if fault == 'late-context-drift' and calls['snapshots'] > 1: value['context'] += b' '
        return value
    plan = {'approved': True}
    namespace = dict(_load_upgrade_recovery_base=lambda **kwargs: (plan, SimpleNamespace(value=archive), records),
        _controller_repair_path=lambda candidate: Path('/protected/repair.json'),
        os=SimpleNamespace(path=SimpleNamespace(lexists=lambda path: fault != 'absent')),
        _controller_private_bytes=read, _controller_repair_snapshots=snapshot,
        _controller_repair_owner=lambda value: OWNER, _repair=repair, InstallError=ValueError, json=json)
    if loading == 'native-module':
        monkeypatch.syspath_prepend(str(ROOT / 'bin'))
        spec = importlib.util.spec_from_file_location('repair_native_installer',
            ROOT / 'installers/macos/lib/pixel-macos-access-install.py')
        native = importlib.util.module_from_spec(spec); spec.loader.exec_module(native)
        for name, value in namespace.items(): monkeypatch.setattr(native, name, value)
        load = native._load_upgrade_recovery
    else:
        load = source_function('installers/macos/lib/pixel-macos-access-install.py', '_load_upgrade_recovery', namespace)
    kwargs = dict(current_digest=CURRENT, candidate_digest=CANDIDATE, owner_name='fixture', completed=fault != 'pending')
    if fault not in (None, 'absent'):
        with pytest.raises(ValueError): load(**kwargs)
    else:
        result = load(**kwargs)
        assert result[0] == plan
        assert result[2][0]['after'] == (records[0]['after'] if fault == 'absent' else after)
        assert result[2][1] == records[1]


@pytest.mark.parametrize('rollback', [False, True])
@pytest.mark.parametrize('body_kind', ['old', 'new', 'unknown'])
@pytest.mark.parametrize('cas_failure', [None, 'before', 'after'])
def test_real_replace_adapter_only_cas_changes_the_exact_bridge(rollback, body_kind, cas_failure):
    before, after, snapshots, intent, _ = fixture()
    body = {'old': before, 'new': after, 'unknown': b'unknown'}[body_kind]
    original = body
    calls = []
    adapter = object.__new__(live.Adapter)
    adapter.r = repair
    adapter.selection = dict(current_digest=CURRENT, candidate_digest=CANDIDATE)
    adapter.snapshots = lambda: snapshots
    adapter.access = SimpleNamespace(assert_stopped=lambda: calls.append('stopped-proof'))
    adapter.read = lambda path: body
    def cas(path, *, expected, replacement, mode):
        nonlocal body, cas_failure
        calls.append('cas')
        assert path == repair.TARGET and mode == 0o644
        assert (expected, replacement) == ((after, before) if rollback else (before, after))
        if body != expected: raise ValueError('source-drift')
        failure, cas_failure = cas_failure, None
        if failure == 'before': raise OSError('before-cas')
        body = replacement
        if failure == 'after': raise OSError('after-cas')
    adapter.cas = cas
    wanted = before if rollback else after
    if body_kind == 'unknown':
        with pytest.raises(ValueError): adapter.replace(intent, rollback=rollback)
        assert body == original
    else:
        if body != wanted and cas_failure:
            with pytest.raises(OSError): adapter.replace(intent, rollback=rollback)
        adapter.replace(intent, rollback=rollback)
        assert body == wanted and calls[0] == 'stopped-proof'
        count = calls.count('cas')
        adapter.replace(intent, rollback=rollback)
        assert calls.count('cas') == count
