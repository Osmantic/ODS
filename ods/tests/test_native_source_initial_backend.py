"""Normal first prepare from inert installer kit; OS/DS observations simulated."""
# ruff: noqa: E402,F401,F811
from contextlib import contextmanager
import json
import os
from pathlib import Path
import plistlib
import sys
import time
from types import SimpleNamespace
import uuid

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux metadata driver, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_executor import qualification_operation, Launchd
from test_native_source_custody import native
from test_connection_deployment_paths import deployment
from test_native_source_runtime_publication import built, source, package
from test_native_source_publication import material, publication, helper
from pixel_connections import custody as c, source_backend as b, native_executor as ex
import pixel_access_bridge as access
import pixel_macos_custody as mac


@pytest.fixture
def initial(material, monkeypatch, request):
    n = material
    # Ordinary new installer: no prior Source kit, identity or private data.
    for path in n.kit.iterdir():
        path.unlink()
    n.kit.rmdir()
    retained = getattr(request, 'param', False)
    if not retained:
        (n.metadata / 'source-identity.json').unlink()
        n.records.update(Users=None, Groups=None)
    n.boot = '01234567-89AB-4DEF-8123-0123456789AB'
    original_command = n.f.bridge.command
    def command(argv, timeout=20):
        if argv == ['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid']:
            return n.boot + '\n'
        return original_command(argv, timeout=timeout)
    monkeypatch.setattr(n.f.bridge, 'command', command)
    monkeypatch.setattr(c, '_native_boot_uuid', lambda: n.boot)
    clock = lambda: time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
    monkeypatch.setattr(c, '_native_clock_ns', clock)
    monkeypatch.setattr(access, '_native_source_clock_ns', clock)
    units = n.world / 'units'
    units.mkdir(mode=0o755)
    monkeypatch.setattr(c, 'NATIVE_DEFINITIONS', units)
    original_directory = mac.protected_directory
    @contextmanager
    def directory(path):
        if Path(path) == units:
            fd = os.open(units, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                yield fd
            finally:
                os.close(fd)
        else:
            with original_directory(path) as fd:
                yield fd
    monkeypatch.setattr(mac, 'protected_directory', directory)
    monkeypatch.setattr(ex, 'protected_directory', directory)
    monkeypatch.setattr(ex, '_publisher', lambda selected: helper if helper._material(selected) else None)
    monkeypatch.setattr(n.f.bridge, 'connection_deployment', lambda: n.f.value)
    if retained:
        def absent_command(argv, **kwargs):
            assert kwargs['stdin'] is c.subprocess.DEVNULL and kwargs['timeout'] == 10
            assert argv in (['/bin/launchctl', 'print', 'system'], ['/bin/ps', '-axo', 'pid=,uid='])
            return SimpleNamespace(returncode=0, stdout='system = {\n}\n' if argv[0] == '/bin/launchctl' else '1 0\n42 1000\n')
        with monkeypatch.context() as proof:
            proof.setattr(c.subprocess, 'run', absent_command)
            n.retained = c.native_retained_identity_only(owner_uid=n.f.bridge.owner.pw_uid, owner_gid=n.f.bridge.owner.pw_gid)
        assert n.retained['record'] == n.identity and not c.NATIVE_STATE.exists()
        assert not c.NATIVE_KIT.exists()
    writes = []
    def run(argv, **kwargs):
        assert argv[:4] == ['/usr/bin/dscl', '-plist', '.', '-create']
        intent = c._native_identity_intent(json.loads((n.metadata / 'source-identity.json').read_bytes()))
        kind, key, value = argv[4].split('/')[1], argv[5], argv[6]
        if n.records[kind] is None:
            n.records[kind] = {}
        n.records[kind][key] = [value]
        if key == 'GeneratedUID':
            (n.acl.identities if kind == 'Users' else n.acl.group_identities)[intent['id']] = uuid.UUID(value).bytes
        writes.append((kind, key))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(c.subprocess, 'run', run)
    with n.f.bridge.bounded(900):
        helper.publish_kit(n.selected)
        saved = json.loads((n.metadata / 'source-publication.json').read_bytes())
        assert saved['old'] is None and saved['bindings'] == {}
        assert saved['witness']['brokerUid'] == (n.identity['id'] if retained else None)
        n.before = (n.metadata / 'source-publication.json').read_bytes()
        n.backend = b.for_bridge(n.f.bridge, linux_paths=c.Paths(), linux_run=None)
        n.writes, n.units = writes, units
        yield n


@pytest.mark.parametrize('changed_boot', [False, True])
def test_ordinary_inert_kit_status_is_readonly_before_first_prepare(initial, changed_boot):
    n = initial
    if changed_boot:
        n.boot = '11234567-89AB-4DEF-8123-0123456789AB'
    status = b.native_status_projection(n.f.bridge)
    assert not status['statePresent'] and status['tokenIdentity'] is None and status['rawStatus'] is None
    assert status['brokerUid'] is None and not n.writes and not c.NATIVE_STATE.exists()
    assert (n.metadata / 'source-publication.json').read_bytes() == n.before


@pytest.mark.parametrize('changed_boot', [False, True])
def test_ordinary_inert_kit_status_then_actual_first_prepare(initial, changed_boot):
    n = initial
    if changed_boot:
        n.boot = '11234567-89AB-4DEF-8123-0123456789AB'
    status = b.native_status_projection(n.f.bridge)
    assert not status['statePresent'] and status['tokenIdentity'] is None and status['rawStatus'] is None
    assert status['brokerUid'] is None and not n.writes and not c.NATIVE_STATE.exists()
    assert (n.metadata / 'source-publication.json').read_bytes() == n.before
    n.backend.prepare(reader=n.f.bridge.owner.pw_name)
    identity = json.loads((n.metadata / 'source-identity.json').read_bytes())
    origin = json.loads((n.metadata / 'source-jobs-prepare.json').read_bytes())
    assert identity['id'] != n.f.bridge.owner.pw_uid and n.writes
    assert origin['phase'] == 'committed-inactive' and origin['witness']['boot'] == n.boot
    assert sorted(row['role'] for row in origin['definitions'].values()) == ['direct', 'sync']
    assert json.loads((c.NATIVE_STATE / 'custody.json').read_bytes())['schemaVersion'] == 2
    assert not (c.NATIVE_STATE / 'private/google-token.json').exists()
    assert not list((c.NATIVE_STATE / 'private/oauth').iterdir())
    assert not any(argv[:2] in (['/bin/launchctl', 'bootstrap'], ['/bin/launchctl', 'kickstart']) for argv in n.commands)
    first = (n.metadata / 'source-jobs-prepare.json').read_bytes()
    n.backend.prepare(reader=n.f.bridge.owner.pw_name)
    assert (n.metadata / 'source-jobs-prepare.json').read_bytes() == first


@pytest.mark.parametrize('initial', [True], indirect=True)
@pytest.mark.parametrize('changed_boot', [False, True])
def test_actual_retained_identity_reinstall_then_code_only_kit_and_first_prepare(initial, changed_boot):
    n = initial
    receipt = (n.metadata / 'source-identity.json').read_bytes()
    inode = (n.metadata / 'source-identity.json').stat().st_ino
    if changed_boot:
        n.boot = '11234567-89AB-4DEF-8123-0123456789AB'
    status = b.native_status_projection(n.f.bridge)
    assert status['brokerUid'] is None and not status['statePresent']
    assert not n.writes and not c.NATIVE_STATE.exists()
    n.backend.prepare(reader=n.f.bridge.owner.pw_name)
    origin = json.loads((n.metadata / 'source-jobs-prepare.json').read_bytes())
    assert origin['phase'] == 'committed-inactive' and origin['witness']['boot'] == n.boot
    assert origin['witness']['brokerUid'] == n.retained['record']['id'] and not n.writes
    assert (n.metadata / 'source-identity.json').read_bytes() == receipt
    assert (n.metadata / 'source-identity.json').stat().st_ino == inode
    assert not (c.NATIVE_STATE / 'private/google-token.json').exists()
    assert not any(argv[:2] in (['/bin/launchctl', 'bootstrap'], ['/bin/launchctl', 'kickstart']) for argv in n.commands)


@pytest.mark.parametrize('initial', [True], indirect=True)
def test_retained_identity_reinstall_does_not_adopt_changed_directory_service_guid(initial):
    n = initial
    n.records['Users']['GeneratedUID'] = ['11234567-89AB-4DEF-8123-0123456789AB']
    before = (n.metadata / 'source-identity.json').read_bytes()
    with pytest.raises(c.CustodyError):
        n.backend.prepare(reader=n.f.bridge.owner.pw_name)
    assert not n.writes and not c.NATIVE_STATE.exists()
    assert (n.metadata / 'source-identity.json').read_bytes() == before


@pytest.mark.parametrize('prefix', ['before-child', 'committed-child'])
def test_initial_static_snapshot_qualifies_old_boot_before_fresh_fixed_label_quiescence(initial, monkeypatch, prefix):
    n = initial
    original_save = c._native_save_at
    hit = []
    def save(selection, directory, name, value, **kwargs):
        if name == 'source-initial-generation.json' and not hit:
            if prefix == 'committed-child' and value['phase'] == 'committed':
                hit.append(True)
                raise OSError('invented initial parent reply loss')
            result = original_save(selection, directory, name, value, **kwargs)
            if prefix == 'before-child' and value['phase'] == 'generation':
                hit.append(True)
                raise OSError('invented initial parent reply loss')
            return result
        return original_save(selection, directory, name, value, **kwargs)
    monkeypatch.setattr(c, '_native_save_at', save)
    with pytest.raises(OSError, match='initial parent reply loss'):
        n.backend.prepare(reader=n.f.bridge.owner.pw_name)
    monkeypatch.setattr(c, '_native_save_at', original_save)
    parent_raw = (n.metadata / 'source-initial-generation.json').read_bytes()
    old_boot = n.boot
    n.boot = '11234567-89AB-4DEF-8123-0123456789AB'
    n.identity = json.loads((n.metadata / 'source-identity.json').read_bytes())
    driver = Launchd(n)
    for path in n.units.iterdir():
        driver.jobs[path.stem] = {'path': path, 'document': plistlib.loads(path.read_bytes()),
            'runs': 0, 'exit': None, 'process': None, 'release': None}
    def command(argv, timeout=20):
        if argv == ['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid']:
            return n.boot + '\n'
        return driver(argv, timeout=timeout)
    monkeypatch.setattr(n.f.bridge, 'command', command)
    # Static custody is valid while exact inert jobs are loaded. It must not
    # substitute an absence callback or claim the historical boot is current.
    snapshot = ex.initial_quiesce_snapshot(n.selected)
    assert snapshot['boot'] == old_boot
    assert len(snapshot['definitions']) == (0 if prefix == 'before-child' else 2)
    assert (n.metadata / 'source-initial-generation.json').read_bytes() == parent_raw
    stopped = ex.quiesce_previous_boot(n.selected)
    assert stopped['status'] == 'stopped' and not driver.jobs
    assert (n.metadata / 'source-initial-generation.json').read_bytes() == parent_raw
    assert not any('-k' in argv for argv in driver.sent)
