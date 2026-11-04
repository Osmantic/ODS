"""Real exclusive filesystem transitions; explicit Linux Darwin metadata adapter.

The complete Source context/journal/archive reader remains real. These tests do
not claim live launchd qualification; the production caller supplies that gate.
"""
# ruff: noqa: E402,F401,F811
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs FD fixture, not Darwin qualification', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_custody import native
from test_connection_deployment_paths import deployment
from test_native_source_sidefile_archives import make_archive, c, CURRENT, CANDIDATE
from test_native_connection_sidefiles import installer
import pixel_macos_custody as mac


def linux_exclusive_rename(source, name, target, destination):
    """Only the kernel ABI changes: actual renameat2(RENAME_NOREPLACE)."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(source, os.fsencode(name), target, os.fsencode(destination), 1):
        raise OSError(ctypes.get_errno(), 'fixture-exclusive-rename-failed')


def inode(path):
    value = path.lstat()
    return [value.st_dev, value.st_ino]


@pytest.fixture
def unbound(native, monkeypatch):
    n = native
    a = make_archive(n, monkeypatch)
    parent = Path(a.manifest['parent']['path'])
    n.owners[tuple(inode(parent))] = (a.uid, a.gid)
    # Build the exact legacy failure from a valid synthetic capsule, retaining
    # its original file inodes. All removal here is confined to test fixtures.
    (a.path / 'gateway.env').rename(parent / 'gateway.env')
    (a.path / 'source-sidefiles.json').rename(n.metadata / 'source-sidefiles.json')
    shutil.rmtree(a.path)
    (n.metadata / c._SIDEFILE_ARCHIVES).unlink()
    protected = mac.protected_directory
    @contextmanager
    def fixture_directory(path, **kwargs):
        if Path(path) != parent.parent:
            with protected(path, **kwargs) as fd: yield fd
        else:
            assert not kwargs
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: yield fd
            finally: os.close(fd)
    monkeypatch.setattr(mac, 'protected_directory', fixture_directory)
    monkeypatch.setattr(mac, '_require_no_acl', c._native_no_acl)
    monkeypatch.setattr(installer, 'RUNTIME_CONFIG_ROOT', parent.parent)
    monkeypatch.setattr(installer._launchd, 'ACCESS_STATE', n.metadata)
    monkeypatch.setattr(installer, '_sidefile_archive_rename', linux_exclusive_rename)
    a.parent = parent
    a.plan = dict(a.context, owner=SimpleNamespace(pw_uid=a.uid, pw_gid=a.gid))
    a.authority = {kind: (n.metadata / item['sourceName']).read_bytes()
        for kind, item in a.manifest['authority'].items()}
    a.receipt = n.metadata / 'source-sidefiles.json'
    a.gateway = parent / 'gateway.env'
    a.expected_receipt, a.expected_gateway = inode(a.receipt), inode(a.gateway)
    a.receipt_body = a.receipt.read_bytes()
    a.verifications = []
    a.verify = a.verifications.append
    return a


def preserve(a):
    return installer._preserve_unbound_sidefiles(a.plan, a.authority,
        expected_receipt=a.expected_receipt, expected_gateway=a.expected_gateway, verify=a.verify)


def assert_preserved(a):
    assert not a.receipt.exists() and not a.gateway.exists()
    assert inode(a.path / 'source-sidefiles.json') == a.expected_receipt
    assert inode(a.path / 'gateway.env') == a.expected_gateway
    assert (a.path / 'source-sidefiles.json').read_bytes() == a.receipt_body
    assert (a.path / 'gateway.env').read_bytes() == b''
    assert not (a.parent / 'onboarding.json').exists()
    assert set(p.name for p in a.path.iterdir()) == {
        'context.json', 'restored.json', 'manifest.json', 'source-sidefiles.json', 'gateway.env'}
    for kind, item in a.manifest['authority'].items():
        assert (a.n.metadata / item['sourceName']).read_bytes() == a.authority[kind]
        assert (a.path / (kind + '.json')).read_bytes() == a.authority[kind]
    view = c.native_sidefile_archives(owner_uid=a.uid, owner_gid=a.gid)
    assert set(view['archives']) == {a.path.name}
    return view


def test_preserves_real_original_inodes_and_full_decoder_accepts_terminal_history(unbound):
    a = unbound
    changes = list(a.n.changes)
    assert preserve(a) == a.path.name
    first = assert_preserved(a)
    assert len(a.verifications) == 5 and set(a.verifications) == {a.path.name}
    assert a.n.changes == changes  # No chown of the unbound originals.
    assert preserve(a) == a.path.name
    assert assert_preserved(a) == first


@pytest.mark.parametrize('leaf', ['gateway.env', 'source-sidefiles.json'])
def test_lost_rename_ack_replays_only_selected_preserved_inodes(unbound, monkeypatch, leaf):
    a = unbound
    calls = []
    def interrupted(source, name, target, destination):
        calls.append(name)
        linux_exclusive_rename(source, name, target, destination)
        if name == leaf: raise OSError('fixture-lost-rename-ack')
    monkeypatch.setattr(installer, '_sidefile_archive_rename', interrupted)
    with pytest.raises(OSError, match='lost-rename-ack'): preserve(a)
    assert not (a.n.metadata / c._SIDEFILE_ARCHIVES).exists()
    assert inode(a.path / leaf) == (a.expected_gateway if leaf == 'gateway.env' else a.expected_receipt)
    monkeypatch.setattr(installer, '_sidefile_archive_rename', linux_exclusive_rename)
    preserve(a)
    assert_preserved(a)
    assert calls == (['gateway.env'] if leaf == 'gateway.env' else ['gateway.env', 'source-sidefiles.json'])


@pytest.mark.parametrize('after_link', [False, True])
def test_terminal_ledger_publication_interruption_preserves_originals_and_replays(unbound, monkeypatch, after_link):
    a = unbound
    actual = os.link
    def interrupted(source, destination, **kwargs):
        assert destination == c._SIDEFILE_ARCHIVES
        if after_link: actual(source, destination, **kwargs)
        raise OSError('fixture-ledger-ack')
    monkeypatch.setattr(os, 'link', interrupted)
    with pytest.raises(OSError, match='ledger-ack'): preserve(a)
    assert inode(a.path / 'gateway.env') == a.expected_gateway
    assert inode(a.path / 'source-sidefiles.json') == a.expected_receipt
    assert (a.n.metadata / c._SIDEFILE_ARCHIVES).exists() is after_link
    assert not any(p.name.startswith('.sidefile-archive-ledger-') for p in a.n.metadata.iterdir())
    monkeypatch.setattr(os, 'link', actual)
    preserve(a)
    assert_preserved(a)


@pytest.mark.parametrize('fault', ['selected-gateway', 'selected-receipt', 'gateway-data', 'gateway-owner',
    'gateway-mode', 'gateway-hardlink', 'gateway-symlink', 'gateway-acl', 'receipt-ready', 'receipt-ready-int', 'receipt-binding',
    'receipt-parent', 'onboarding', 'parent-mode', 'parent-symlink', 'unbound-directory', 'source-gate'])
def test_refuses_unknown_state_before_moving_originals(unbound, fault):
    a = unbound
    if fault == 'selected-gateway': a.expected_gateway[1] += 1
    elif fault == 'selected-receipt': a.expected_receipt[1] += 1
    elif fault == 'gateway-data': a.gateway.write_bytes(b'preserve private content')
    elif fault == 'gateway-owner': a.n.owners[tuple(a.expected_gateway)] = (0, 0)
    elif fault == 'gateway-mode': a.gateway.chmod(0o640)
    elif fault == 'gateway-hardlink': os.link(a.gateway, a.n.world / 'test-alias')
    elif fault == 'gateway-symlink':
        a.gateway.rename(a.n.world / 'preserved-fixture-gateway')
        a.gateway.symlink_to(a.n.world / 'preserved-fixture-gateway')
    elif fault == 'gateway-acl':
        a.n.acl.acls[tuple(a.expected_gateway)] = {
            'entries': [{'tag': 1, 'mask': 2, 'flags': 0, 'uuid': b'R' * 16}], 'flags': 0}
    elif fault.startswith('receipt-'):
        record = json.loads(a.receipt.read_bytes())
        if fault in ('receipt-ready', 'receipt-ready-int'):
            record['children']['gateway.env']['ready'] = True if fault == 'receipt-ready' else 0
        elif fault == 'receipt-binding': record['bundleSha256'] = 'f' * 64
        else: record['parent'][1] += 1
        a.receipt.write_text(json.dumps(record))
    elif fault == 'onboarding': (a.parent / 'onboarding.json').write_bytes(b'preserve owner data')
    elif fault == 'parent-mode': a.parent.chmod(0o750)
    elif fault == 'parent-symlink':
        saved_parent = a.parent.rename(a.parent.parent / 'saved-parent')
        a.parent.symlink_to(saved_parent, target_is_directory=True)
    elif fault == 'unbound-directory': a.path.mkdir(mode=0o700)
    else:
        def unavailable(_name): raise installer.InstallError('fixture-source-present')
        a.verify = unavailable
    before = {str(path): (inode(path), path.read_bytes()) for path in (a.receipt, a.gateway)}
    changes = list(a.n.changes)
    with pytest.raises((installer.InstallError, mac.CustodyError, c.CustodyError, OSError)): preserve(a)
    assert {str(path): (inode(path), path.read_bytes()) for path in (a.receipt, a.gateway)} == before
    assert a.n.changes == changes
    assert not (a.n.metadata / c._SIDEFILE_ARCHIVES).exists()


@pytest.mark.parametrize('fault', ['manifest', 'receipt', 'context', 'destination', 'source-gate'])
def test_partial_replay_refuses_drift_preserving_both_locations(unbound, monkeypatch, fault):
    a = unbound
    def interrupted(source, name, target, destination):
        linux_exclusive_rename(source, name, target, destination)
        raise OSError('fixture-lost-rename-ack')
    monkeypatch.setattr(installer, '_sidefile_archive_rename', interrupted)
    with pytest.raises(OSError): preserve(a)
    monkeypatch.setattr(installer, '_sidefile_archive_rename', linux_exclusive_rename)
    if fault == 'manifest':
        path = a.path / 'manifest.json'
        value = json.loads(path.read_bytes())
        value['files']['source-sidefiles.json']['size'] += 1
        path.write_bytes(c._native_encoded(value))
    elif fault == 'receipt': a.receipt.write_bytes(a.receipt_body + b' ')
    elif fault == 'context':
        path = a.n.metadata / a.manifest['authority']['context']['sourceName']
        path.write_bytes(path.read_bytes() + b' ')
    elif fault == 'destination': (a.path / 'source-sidefiles.json').write_bytes(b'preserve collision')
    else:
        def unavailable(_name): raise installer.InstallError('fixture-source-present')
        a.verify = unavailable
    receipt_before = (inode(a.receipt), a.receipt.read_bytes())
    with pytest.raises((installer.InstallError, mac.CustodyError, c.CustodyError, OSError)): preserve(a)
    assert (inode(a.receipt), a.receipt.read_bytes()) == receipt_before
    assert inode(a.path / 'gateway.env') == a.expected_gateway
    assert not (a.n.metadata / c._SIDEFILE_ARCHIVES).exists()


def test_exclusive_move_never_overwrites_appearance_after_inspection(unbound, monkeypatch):
    a = unbound
    def collision(source, name, target, destination):
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600, dir_fd=target)
        os.write(fd, b'preserve concurrent object')
        os.close(fd)
        linux_exclusive_rename(source, name, target, destination)
    monkeypatch.setattr(installer, '_sidefile_archive_rename', collision)
    with pytest.raises(FileExistsError): preserve(a)
    assert inode(a.gateway) == a.expected_gateway and a.gateway.read_bytes() == b''
    assert (a.path / 'gateway.env').read_bytes() == b'preserve concurrent object'
    assert a.receipt.read_bytes() == a.receipt_body


def test_dead_source_gate_after_first_move_leaves_private_replayable_plan(unbound):
    a = unbound
    def verify(name):
        a.verifications.append(name)
        if len(a.verifications) == 3: raise installer.InstallError('fixture-source-appeared')
    a.verify = verify
    with pytest.raises(installer.InstallError, match='source-appeared'): preserve(a)
    assert inode(a.path / 'gateway.env') == a.expected_gateway
    assert inode(a.receipt) == a.expected_receipt
    assert not (a.n.metadata / c._SIDEFILE_ARCHIVES).exists()
    a.verify = a.verifications.append
    preserve(a)
    assert_preserved(a)


@pytest.mark.parametrize('boundary', ['context.json', 'restored.json', 'manifest.json'])
def test_manifest_without_all_authority_never_becomes_adoption(unbound, monkeypatch, boundary):
    a = unbound
    actual = os.open
    def interrupted(name, flags, *args, **kwargs):
        if name == boundary and flags & os.O_CREAT: raise OSError('fixture-before-manifest')
        return actual(name, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', interrupted)
    with pytest.raises(OSError, match='before-manifest'): preserve(a)
    monkeypatch.setattr(os, 'open', actual)
    before = {p.name: (inode(p), p.read_bytes()) for p in a.path.iterdir()}
    with pytest.raises(FileNotFoundError): preserve(a)
    assert {p.name: (inode(p), p.read_bytes()) for p in a.path.iterdir()} == before
    assert inode(a.gateway) == a.expected_gateway and inode(a.receipt) == a.expected_receipt


@pytest.mark.parametrize('failure', [OSError, c.CustodyError])
def test_cli_requires_explicit_observed_inodes_and_reports_safe_failure(monkeypatch, capsys, failure):
    called = []
    def archive(**kwargs):
        called.append(kwargs)
        raise failure('private-content-must-not-appear')
    monkeypatch.setattr(installer, 'archive_sidefiles', archive)
    args = ['--owner', 'fixture', '--current-bundle-digest', CURRENT, '--bundle-digest', CANDIDATE,
        '--expected-receipt-inode', '12:34', '--expected-gateway-inode', '12:56']
    assert installer.main(['archive-sidefiles', *args]) == 1
    assert called[0]['expected_receipt'] == [12, 34] and called[0]['expected_gateway'] == [12, 56]
    captured = capsys.readouterr()
    assert 'native-sidefiles-archive-failed' in captured.err
    assert 'private-content-must-not-appear' not in captured.err
    args[-1] = '0:56'
    assert installer.main(['archive-sidefiles', *args]) == 1
    assert len(called) == 1 and 'selection-invalid' in capsys.readouterr().err


@pytest.mark.parametrize('fault', [None, 'private-state', 'kit', 'directory-service', 'account',
    'unknown-receipt', 'unknown-archive', 'loaded-job', 'bad-launchctl', 'bad-processes', 'empty-processes'])
def test_source_census_refuses_present_or_unavailable_actor_state(unbound, monkeypatch, fault):
    import grp
    import pwd
    a = unbound
    # The Source fixture starts with an invented kit/account. Explicitly model
    # the absent first-kit boundary; no host account database is consulted.
    shutil.rmtree(c.NATIVE_KIT)
    (a.n.metadata / 'source-identity.json').unlink()
    monkeypatch.setattr(c, 'NATIVE_LOGS', a.n.world / 'absent-logs')
    monkeypatch.setattr(c, '_native_ds_record', lambda kind: {} if fault == 'directory-service' else None)
    def absent(_name):
        if fault == 'account': return object()
        raise KeyError()
    monkeypatch.setattr(pwd, 'getpwnam', absent)
    monkeypatch.setattr(grp, 'getgrnam', absent)
    if fault == 'private-state': c.NATIVE_STATE.mkdir()
    elif fault == 'kit': c.NATIVE_KIT.mkdir()
    elif fault == 'unknown-receipt': (a.n.metadata / 'source-private-token.json').write_bytes(b'private')
    elif fault == 'unknown-archive': (a.n.metadata / ('source-sidefiles-archive-' + 'f' * 64)).mkdir()
    calls = []
    def command(argv, *, timeout):
        calls.append(argv)
        assert timeout == 10
        if argv == ['/bin/launchctl', 'print', 'system']:
            if fault == 'bad-launchctl': return 'unknown format'
            return 'system = {\n' + (c.NATIVE_LABEL if fault == 'loaded-job' else '') + '\n}'
        assert argv == ['/bin/ps', '-axo', 'pid=,uid=']
        return {'bad-processes': '1 0\n1 0\n', 'empty-processes': ''}.get(fault, '1 0\n2 501\n')
    bridge = SimpleNamespace(state=a.n.metadata, command=command)
    if fault:
        with pytest.raises(installer.InstallError): installer._archive_sidefiles_no_source(bridge, a.path.name)
    else:
        installer._archive_sidefiles_no_source(bridge, a.path.name)
        assert len(calls) == 2
    assert inode(a.gateway) == a.expected_gateway and a.receipt.read_bytes() == a.receipt_body


@pytest.mark.parametrize('fault', [None, 'not-restored', 'existing-source', 'live-drift', 'authority-drift'])
def test_cli_operation_holds_original_lock_and_uses_before_bridge_for_every_live_gate(unbound, monkeypatch, fault):
    """Exercise orchestration + physical protocol + full Source archive decoder.

    Launchd readiness/discovery are explicit ordering observations here; their
    own tests qualify actual definitions. They are not physical Mac evidence.
    """
    a = unbound
    events, active = [], set()
    class Bridge:
        def __init__(self, name): self.name = name
        @contextmanager
        def locked(self):
            assert self.name == 'original'
            active.add('original-lock')
            try: yield
            finally: active.remove('original-lock')
        @contextmanager
        def bounded(self, seconds):
            assert seconds == 900 and 'original-lock' in active
            if self.name == 'before': assert 'original-bounded' in active
            active.add(self.name + '-bounded')
            try: yield
            finally: active.remove(self.name + '-bounded')
    original, before = Bridge('original'), Bridge('before')
    a.plan['owner'].pw_name = 'fixture-owner'
    a.plan['access_settings'] = {'selection': 'candidate'}
    config = a.n.metadata / 'controller.json'
    monkeypatch.setitem(installer.ACCESS_FILES, 'config', config)
    records = [{'path': str(config), 'before': b'{"selection":"approved-before"}'}]
    journal = SimpleNamespace(value=json.loads(a.authority['restored']),
        path=a.n.metadata / a.manifest['authority']['restored']['sourceName'])
    if fault == 'not-restored': journal.value['phase'] = 'active'
    elif fault == 'existing-source': journal.value['sourceAddition']['proof']['kit'] = {'present': True}
    monkeypatch.setattr(installer.sys, 'platform', 'darwin')
    monkeypatch.setattr(os, 'geteuid', lambda: 0)
    monkeypatch.setattr(installer, '_recovery_bridge', lambda **_kw: original)
    def load(**kwargs):
        assert kwargs == dict(current_digest=CURRENT, candidate_digest=CANDIDATE,
            owner_name='fixture-owner', completed=True)
        return a.plan, journal, records
    monkeypatch.setattr(installer, '_load_upgrade_recovery', load)
    monkeypatch.setattr(installer, '_verify_recovery_bindings', lambda plan, rows:
        events.append('binding') if plan is a.plan and rows is records else pytest.fail('wrong bindings'))
    monkeypatch.setattr(mac, 'protected_bytes', lambda path, **_kw: Path(path).read_bytes())
    def before_bridge(plan, *, owner_name):
        assert plan['access_settings'] == {'selection': 'approved-before'} and owner_name == 'fixture-owner'
        if fault == 'authority-drift': journal.path.write_bytes(a.authority['restored'] + b' ')
        return before
    monkeypatch.setattr(installer, '_bridge_from_recovery_plan', before_bridge)
    def live(plan, rows):
        assert plan is a.plan and rows is records
        assert active == {'original-lock', 'original-bounded', 'before-bounded'}
        events.append('live-before')
        if fault == 'live-drift': raise installer.InstallError('fixture-live-drift')
    monkeypatch.setattr(installer, '_verify_retirement_live', live)
    def discover(bridge):
        assert bridge is before and 'original-lock' in active
        events.append('discover-before')
    monkeypatch.setattr(installer, '_discover_source_installer_bridge', discover)
    def census(bridge, name):
        assert bridge is before and name == a.path.name and 'original-lock' in active
        events.append('census-before')
    monkeypatch.setattr(installer, '_archive_sidefiles_no_source', census)
    def run():
        return installer.archive_sidefiles(current_digest=CURRENT, candidate_digest=CANDIDATE,
            owner_name='fixture-owner', expected_receipt=a.expected_receipt, expected_gateway=a.expected_gateway)
    if fault:
        with pytest.raises(installer.InstallError): run()
        assert inode(a.gateway) == a.expected_gateway and inode(a.receipt) == a.expected_receipt
        assert not a.path.exists()
    else:
        assert run() == a.path.name
        assert_preserved(a)
        assert events == ['binding'] + ['binding', 'live-before', 'discover-before', 'census-before'] * 5
    assert active == set()


def test_archive_cli_stages_the_full_protected_installer_closure(monkeypatch):
    import pixel_native_installer_entry as entry
    source = installer.HERE.parents[2]
    assert 'installers/macos/lib/pixel-macos-access-install.py' in entry._sources(source)
    assert set(entry.MAC_LIB) == {p.name for p in (source / 'installers/macos/lib').glob('*.py')}
    monkeypatch.setattr(installer.sys, 'platform', 'darwin')
    monkeypatch.setattr(os, 'geteuid', lambda: 0)
    monkeypatch.setattr(entry, 'active_support', lambda: None)
    python = '/qualified/root/python'
    root = entry.SUPPORT_ROOT / ('e' * 64)
    staged = dict(root=str(root), entry=str(root / entry.ENTRY), codeSha256=root.name, python=python)
    calls = []
    def stage(selected, **kwargs):
        assert selected == source and kwargs == {'python': python}
        calls.append('full-stage')
        return staged
    monkeypatch.setattr(entry, 'stage_support', stage)
    monkeypatch.setattr(installer._native_services, 'helper', lambda name:
        SimpleNamespace(select_python=lambda: python) if name == 'ops-service' else pytest.fail('wrong helper'))
    args = ['archive-sidefiles', '--owner', 'fixture-owner', '--current-bundle-digest', CURRENT,
        '--bundle-digest', CANDIDATE, '--expected-receipt-inode', '12:34', '--expected-gateway-inode', '12:56']
    def run(argv, **kwargs):
        assert argv == [python, '-I', '-B', str(root / entry.ENTRY), '--peer', 'access-install', '--', *args]
        assert kwargs == {'check': False, 'timeout': 1800}
        calls.append('protected-peer')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(installer.subprocess, 'run', run)
    assert installer._protected_cli(args) == 0
    assert calls == ['full-stage', 'protected-peer']
