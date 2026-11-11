"""Real Linux FDs/CAS/Source decoder; Darwin metadata/launchd are explicit adapters."""
# ruff: noqa: E402,F401,F811
from contextlib import contextmanager
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time
from types import SimpleNamespace

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs owned-file driver, not physical Darwin qualification', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_custody import native
from test_connection_deployment_paths import deployment
from test_native_source_installer_custody import absent
from test_native_source_sidefile_archives import make_bound_archive, c, upgrade
from test_native_connection_sidefiles import installer as access
from test_native_sidefiles_preservation import linux_exclusive_rename, inode
import pixel_macos_custody as mac
import pixel_access_bridge as bridge_module


@pytest.fixture
def active(absent, monkeypatch, request):
    n = absent
    monkeypatch.setattr(bridge_module, '_native_source_clock_ns', lambda: time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW))
    monkeypatch.setattr(n.f.bridge, 'state', n.metadata, raising=False)
    (n.metadata / 'source-identity.json').unlink()
    n.records.update(Users=None, Groups=None)
    monkeypatch.setattr(c, 'NATIVE_RUNTIME_DESCRIPTOR', n.world / 'runtime-descriptor.json')
    monkeypatch.setattr(c, 'NATIVE_RUNTIME_MANIFEST', n.world / 'runtime-manifest.json')
    monkeypatch.setattr(c, 'NATIVE_PROGRAM', n.world / 'managed-program')
    monkeypatch.setattr(c, 'NATIVE_LOGS', n.world / 'source-logs')
    schedules = frozenset(str(n.world / 'schedule' / name) for name in ('pixel_schedules.py', 'scheduled_tasks_owner.py', 'scheduled_tasks_gateway.mjs'))
    monkeypatch.setattr(c, '_native_installer_schedule_additions', lambda: schedules)
    monkeypatch.setattr(upgrade, 'SCHEDULE_ADDITIONS', schedules)
    # 'guard': a main-shaped origin, whose controller also lacks the admission guard.
    guard = getattr(request, 'param', None) == 'guard'
    if guard:
        guards = frozenset({str(n.world / 'schedule' / 'pixel_source_upgrade.py')})
        monkeypatch.setattr(c, '_native_installer_guard_additions', lambda: guards)
        for module in (upgrade, access._upgrade, mac):
            monkeypatch.setattr(module, 'GUARD_ADDITIONS', guards)
    a = make_bound_archive(n, monkeypatch, guard=guard)
    parent = Path(a.manifest['parent']['path'])
    n.owners[tuple(inode(parent))] = (a.uid, a.gid)
    for name, image in a.manifest['files'].items():
        (a.path / name).rename((parent if image['uid'] else n.metadata) / name)
    shutil.rmtree(a.path)
    (n.metadata / c._SIDEFILE_ARCHIVES).write_bytes(c._native_encoded(a.previous.ledger))
    for row in a.records:
        path = Path(row['path'])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(row['after'])
        path.chmod(row['mode'])
    protected = mac.protected_directory
    @contextmanager
    def fixture_directory(path, **kwargs):
        if Path(path) in (n.metadata, n.kit, n.world):
            with protected(path, **kwargs) as fd: yield fd
        else:
            assert not kwargs and Path(path).is_relative_to(n.world)
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: yield fd
            finally: os.close(fd)
    monkeypatch.setattr(mac, 'protected_directory', fixture_directory)
    monkeypatch.setattr(mac, '_require_no_acl', c._native_no_acl)
    # The broad deployment fixture substitutes a Linux runtime reader. Restore
    # this actual FD reader for the mixed-image recovery proof under the same
    # explicit confined metadata/ACL observations used by the producer.
    spec = importlib.util.spec_from_file_location('prepublication_actual_mac_reader', Path(mac.__file__))
    real_reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real_reader)
    real_reader.protected_directory = fixture_directory
    real_reader._require_no_acl = c._native_no_acl
    real_reader.CustodyError = mac.CustodyError
    observed_fstat = os.fstat
    ancestors = set(n.world.parents)
    def ancestor_metadata(fd):
        info = observed_fstat(fd)
        path = Path(os.readlink('/proc/self/fd/' + str(fd)))
        if path not in ancestors: return info
        # Only the Linux temporary namespace's ancestor metadata is translated;
        # every child file's real fd, inode, mode, links and ACL remain checked.
        fields = {key: getattr(info, key) for key in dir(info) if key.startswith('st_')}
        return SimpleNamespace(**dict(fields, st_uid=0, st_gid=0, st_mode=info.st_mode & ~0o022))
    monkeypatch.setattr(os, 'fstat', ancestor_metadata)
    monkeypatch.setattr(mac, 'protected_bytes', real_reader.protected_bytes)
    def root_directory_metadata(path, *, private=False):
        assert Path(path) in ancestors or Path(path).is_relative_to(n.world)
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = real_reader._verify_fd(fd, directory=True)
            if private and info.st_mode & 0o077: raise access.InstallError('fixture-private-directory')
        finally: os.close(fd)
    monkeypatch.setattr(access, '_check_directory', root_directory_metadata)
    monkeypatch.setattr(access, 'RUNTIME_CONFIG_ROOT', parent.parent)
    monkeypatch.setattr(access._launchd, 'ACCESS_STATE', n.metadata)
    monkeypatch.setattr(access, '_sidefile_archive_rename', linux_exclusive_rename)
    monkeypatch.setattr(access._upgrade, 'SOURCE_ADDITIONS', c.native_installer_source_family())
    monkeypatch.setattr(access._upgrade, 'SOURCE_PRIVATE_RECEIPT', str(n.metadata / 'source-runtime-installation.json'))
    monkeypatch.setattr(access._upgrade, 'SCHEDULE_ADDITIONS', schedules)
    monkeypatch.setattr(mac, 'SCHEDULE_ADDITIONS', schedules)
    a.plan = dict(a.context, owner=n.f.bridge.owner)
    a.parent = parent
    a.authority = {'context': (n.metadata / ('runtime-upgrade-context-' + a.candidate + '.json')).read_bytes(),
        'journal': (n.metadata / 'runtime-upgrade.json').read_bytes()}
    a.originals = {name: ((parent if image['uid'] else n.metadata) / name).read_bytes() for name, image in a.manifest['files'].items()}
    a.inodes = {name: inode((parent if image['uid'] else n.metadata) / name) for name, image in a.manifest['files'].items()}
    a.prior = {p.name: (p.read_bytes(), inode(p)) for p in a.previous.path.iterdir()}
    a.verify = lambda _name: access._prepublication_source_absence(n.f.bridge, a.candidate, a.plan['owner'])
    return a


def prepare(a):
    return access._bound_sidefile_archive(a.plan, authority=a.authority, verify=a.verify)


def preserve(a):
    return access._bound_sidefile_archive(a.plan, authority=a.authority, preserve=True, verify=a.verify)


def assert_preserved(a):
    view = c.native_sidefile_archives(owner_uid=a.uid, owner_gid=a.gid)
    assert set(view['archives']) == {a.path.name, a.previous.path.name}
    for name, body in a.originals.items():
        assert (a.path / name).read_bytes() == body and inode(a.path / name) == a.inodes[name]
        assert not ((a.parent if name in ('gateway.env', 'onboarding.json') else a.n.metadata) / name).exists()
    assert {p.name: (p.read_bytes(), inode(p)) for p in a.previous.path.iterdir()} == a.prior
    return view


def test_bound_preservation_keeps_prior_history_and_all_four_original_inodes(active):
    a = active
    assert prepare(a) == a.authority
    for name, image in a.manifest['files'].items():
        assert inode((a.parent if image['uid'] else a.n.metadata) / name) == a.inodes[name]
    assert preserve(a) == a.authority
    first = assert_preserved(a)
    assert preserve(a) == a.authority and assert_preserved(a) == first


@pytest.mark.parametrize('leaf', ['gateway.env', 'onboarding.json', 'source-sidefiles.json', 'source-runtime-installation.json'])
def test_each_lost_move_ack_replays_exact_inode_without_overwrite(active, monkeypatch, leaf):
    a = active
    prepare(a)
    def lost(source, name, target, destination):
        linux_exclusive_rename(source, name, target, destination)
        if name == leaf: raise OSError('invented lost rename reply')
    monkeypatch.setattr(access, '_sidefile_archive_rename', lost)
    with pytest.raises(OSError): preserve(a)
    assert inode(a.path / leaf) == a.inodes[leaf]
    monkeypatch.setattr(access, '_sidefile_archive_rename', linux_exclusive_rename)
    preserve(a)
    assert_preserved(a)


@pytest.mark.parametrize('after', [False, True])
def test_prior_ledger_cas_lost_ack_keeps_a9_and_replays_completed_original_moves(active, monkeypatch, after):
    a = active
    prepare(a)
    replace = mac.replace_protected_bytes
    def interrupted(*args, **kwargs):
        if after: replace(*args, **kwargs)
        raise OSError('invented lost ledger reply')
    monkeypatch.setattr(mac, 'replace_protected_bytes', interrupted)
    with pytest.raises(OSError): preserve(a)
    assert all(inode(a.path / name) == expected for name, expected in a.inodes.items())
    monkeypatch.setattr(mac, 'replace_protected_bytes', replace)
    preserve(a)
    assert_preserved(a)


@pytest.mark.parametrize('leaf', ['context.json', 'journal.json', 'manifest.json'])
def test_unsealed_preparation_interruption_preserves_live_originals_and_refuses_adoption(active, monkeypatch, leaf):
    a = active
    actual = os.open
    def interrupted(path, flags, *args, **kwargs):
        if path == leaf and flags & os.O_CREAT: raise OSError('invented capsule-write failure')
        return actual(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', interrupted)
    with pytest.raises(OSError): prepare(a)
    monkeypatch.setattr(os, 'open', actual)
    with pytest.raises((access.InstallError, c.CustodyError, OSError)): prepare(a)
    for name, expected in a.inodes.items():
        assert inode((a.parent if name in ('gateway.env', 'onboarding.json') else a.n.metadata) / name) == expected


@pytest.mark.parametrize('fault', ['body', 'inode', 'mode', 'hardlink', 'symlink', 'manifest', 'context', 'journal', 'history', 'extra'])
def test_sealed_capsule_or_selected_original_drift_never_moves_private_data(active, monkeypatch, fault):
    a = active
    prepare(a)
    target = a.parent / 'gateway.env'
    if fault == 'body': target.write_bytes(b'preserve changed private body')
    elif fault == 'inode':
        replacement = a.n.world / 'new-owner-file'
        replacement.write_bytes(target.read_bytes())
        replacement.chmod(0o600)
        a.n.owners[tuple(inode(replacement))] = (a.uid, a.gid)
        replacement.replace(target)
    elif fault == 'mode': target.chmod(0o640)
    elif fault == 'hardlink': os.link(target, a.n.world / 'alias')
    elif fault == 'symlink':
        target.rename(a.n.world / 'retained-owner-file')
        target.symlink_to(a.n.world / 'retained-owner-file')
    elif fault == 'history': (a.previous.path / 'context.json').write_bytes(b'preserve changed authority')
    elif fault == 'extra': (a.path / 'foreign').write_bytes(b'preserve unknown')
    else: (a.path / (fault + '.json')).write_bytes(b'{}')
    monkeypatch.setattr(access, '_sidefile_archive_rename', lambda *_a: pytest.fail('must not move'))
    with pytest.raises((access.InstallError, c.CustodyError, OSError, ValueError)): preserve(a)
    assert (a.n.metadata / 'source-sidefiles.json').exists()
    assert (a.n.metadata / 'source-runtime-installation.json').exists()


@pytest.mark.parametrize('fault', ['state', 'kit', 'logs', 'ds-user', 'ds-group', 'loaded-job', 'publication', 'enrollment', 'unknown-history'])
def test_fresh_source_state_refuses_before_capsule_preparation(active, monkeypatch, fault):
    a = active
    if fault in ('state', 'kit', 'logs'):
        getattr(c, 'NATIVE_' + fault.upper()).mkdir(mode=0o700)
    elif fault in ('ds-user', 'ds-group'):
        a.n.records['Users' if fault == 'ds-user' else 'Groups'] = {'invented': ['present']}
    elif fault == 'loaded-job':
        original = a.n.f.bridge.command
        monkeypatch.setattr(a.n.f.bridge, 'command', lambda argv, timeout=10:
            'system = { ' + c.NATIVE_LABEL + 'invented }' if argv[0] == '/bin/launchctl' else original(argv, timeout=timeout))
    else:
        name = {'publication': 'source-publication.json', 'enrollment': 'native-app-enrollment.json',
            'unknown-history': 'source-sidefiles-archive-' + 'e' * 64}[fault]
        (a.n.metadata / name).write_bytes(b'preserve foreign Source evidence')
    with pytest.raises((access.InstallError, c.CustodyError, OSError, ValueError)): prepare(a)
    assert not a.path.exists() and (a.n.metadata / 'source-sidefiles.json').exists()


def test_real_source_verifier_and_restore_cas_accept_only_preserved_before_receipt(active):
    a = active
    prepare(a)
    preserve(a)
    proof = a.journal['sourceAddition']['proof']
    transaction = dict(kind='native-installer-upgrade', transaction=proof['transaction'],
        currentDigest=proof['currentDigest'], candidateDigest=proof['candidateDigest'])
    checks = []
    def fresh():
        checks.append(True)
        assert c.native_verify_installer_absence(a.n.f.bridge, proof, transaction=transaction) == proof
    def read(path):
        return access._source_snapshot_read(path, limit=8 * 1024 * 1024)
    with a.n.f.bridge.bounded(60):
        fresh()
        access._upgrade.restore_deployment_files(a.records, read=read, replace=mac.replace_protected_inspection_bytes,
            source_addition=a.journal['sourceAddition'], verify_source=fresh, remove_source_added=access._source_added_remove)
    assert checks and all(read(row['path']) == row['before'] for row in a.records)
    assert_preserved(a)


@pytest.mark.parametrize('fault', ['before-image', 'unknown-image', 'missing-hold', 'changed-journal'])
def test_explicit_selection_refuses_before_stop_or_capsule_when_live_authority_is_wrong(active, monkeypatch, fault):
    a = active
    row = next(row for row in a.records if row['path'].endswith('/activity.py'))
    if fault == 'before-image': Path(row['path']).unlink()
    elif fault == 'unknown-image': Path(row['path']).write_bytes(b'unknown code')
    elif fault == 'changed-journal':
        (a.n.metadata / 'runtime-upgrade.json').write_bytes(c._native_encoded(dict(a.journal, phase='prepared')))
    def hold(_plan):
        if fault == 'missing-hold': raise access.InstallError('runtime-upgrade-edge-hold-missing')
        return {'held': True}
    monkeypatch.setattr(access, '_resume_migration_hold', hold)
    monkeypatch.setattr(access, '_recover_upgrade', lambda *_a, **_kw: pytest.fail('must not stop'))
    journal = SimpleNamespace(value=a.journal, path=a.n.metadata / 'runtime-upgrade.json')
    with pytest.raises((access.InstallError, c.CustodyError, OSError)):
        access._recover_before_source_publication(a.plan, a.records, journal, a.n.f.bridge, verify_snapshots=lambda: None)
    assert not a.path.exists() and (a.n.metadata / 'source-sidefiles.json').exists()


@pytest.mark.parametrize('active', ['schedules', 'guard'], indirect=True)
@pytest.mark.parametrize('interruption', ['partial-move', 'restored'])
def test_recover_install_replays_sealed_active_authority_through_real_journal_and_file_cas(active, monkeypatch, interruption):
    """Full selection/sequencer/FD archive/Source reader; launchd/bundle adapters.

    It exercises the actual public recovery dispatcher and RecoveryJournal.
    Process observation and bundle/plist qualification are separate OS adapters,
    not claimed here as real Darwin execution.
    """
    a = active
    a.plan['access_settings'] = {'gateway_port': 18789}
    monkeypatch.setattr(access.sys, 'platform', 'darwin')
    monkeypatch.setattr(access.os, 'geteuid', lambda: 0)
    read_private = bridge_module.private_json
    journal = access._upgrade.RecoveryJournal(a.n.metadata / 'runtime-upgrade.json', copy.deepcopy(a.journal))
    held, events = [], []
    @contextmanager
    def lock(_self):
        held.append(True)
        try: yield
        finally: held.pop()
    monkeypatch.setattr(bridge_module.LaunchdAccessBridge, 'recovery_locked', lock)
    def load(**_kwargs):
        assert held
        journal.value = read_private(journal.path, 0, access._upgrade.JOURNAL_LIMIT)
        transaction = dict(kind='native-installer-upgrade', transaction=a.journal['sourceAddition']['contextSha256'],
            currentDigest=a.journal['currentDigest'], candidateDigest=a.candidate)
        context = c._native_installer_context_bytes(a.authority['context'], transaction)
        c._native_installer_journal_bytes(c._native_encoded(journal.value), transaction, context)
        return a.plan, journal, a.records
    monkeypatch.setattr(access, '_load_upgrade_recovery', load)
    monkeypatch.setattr(access, '_verify_recovery_bindings', lambda *_a: None)
    monkeypatch.setattr(access, '_verify_recovery_runtime', lambda *_a: None)
    monkeypatch.setattr(access, '_previous_upgrade_guard', lambda *_a: False)
    monkeypatch.setattr(access, '_recover_unstarted_upgrade', lambda *_a, **_kw: False)
    monkeypatch.setattr(access, '_resume_migration_hold', lambda *_a: {'held': True})
    roles = access._upgrade.CORE_ROLES
    states = {role: 'candidate' for role in roles}
    previous = {role: SimpleNamespace(target=role, version='previous') for role in roles}
    candidate = {role: SimpleNamespace(target=role, version='candidate') for role in roles}
    def absent(before, after): assert states[before.target] == 'absent'
    for role, item in candidate.items():
        item.assert_stopped = lambda role=role: absent(candidate[role], candidate[role])
    for role, item in previous.items():
        item.assert_stopped = lambda role=role: absent(previous[role], previous[role])
    monkeypatch.setattr(access, '_upgrade_services', lambda *_a: (previous, candidate))
    monkeypatch.setattr(access, '_observe_upgrade_service', lambda before, after: states[before.target])
    monkeypatch.setattr(access, '_assert_upgrade_absent', absent)
    def stop(service):
        states[service.target] = 'absent'
        events.append('stop')
    monkeypatch.setattr(access, '_stop_upgrade_service', stop)
    monkeypatch.setattr(access, '_restore_upgrade_owner', lambda *_a: None)
    monkeypatch.setattr(access, '_start_upgrade_service', lambda service: states.update({service.target: 'previous'}))
    for name in ('_upgrade_service_identity', '_upgrade_policy_mode', '_ready_gateway', '_ready_access', '_ready_access_relay',
            '_clear_candidate_stop_witnesses', '_finish_migration_hold'):
        monkeypatch.setattr(access, name, lambda *_a, **_kw: None)
    transaction = dict(kind='native-installer-upgrade', transaction=a.journal['sourceAddition']['contextSha256'],
        currentDigest=a.journal['currentDigest'], candidateDigest=a.candidate)
    def finish(*_args):
        assert journal.value['phase'] == 'restored'
        c.native_verify_installer_absence(a.n.f.bridge, a.journal['sourceAddition']['proof'], transaction=transaction)
        if interruption == 'restored' and 'interrupted' not in events:
            events.append('interrupted')
            raise OSError('invented interruption after restored')
    monkeypatch.setattr(access, '_finish_source_upgrade', finish)
    def rename(source, name, target, destination):
        assert set(states.values()) == {'absent'}
        linux_exclusive_rename(source, name, target, destination)
        if interruption == 'partial-move' and name == 'onboarding.json' and 'interrupted' not in events:
            events.append('interrupted')
            raise OSError('invented lost partial move reply')
    monkeypatch.setattr(access, '_sidefile_archive_rename', rename)
    def recover():
        return access.recover_install(a.n.f.bridge, current_digest=a.journal['currentDigest'], candidate_digest=a.candidate,
            owner_name=a.plan['owner'].pw_name, rollback_before_source_publication=True)
    with pytest.raises((OSError, access._upgrade.UpgradeError)): recover()
    assert journal.value['phase'] == ('recovery-required' if interruption == 'partial-move' else 'restored')
    assert (a.path / 'journal.json').read_bytes() == a.authority['journal']
    assert recover() == 'restored'
    assert_preserved(a)
    # Every journaled addition, the guard included for a main-shaped origin, is absent again.
    assert all(access._source_snapshot_read(row['path'], limit=8 * 1024 * 1024) == row['before'] for row in a.records)
    assert not journal.path.exists() and set(states.values()) == {'previous'}
    archived = json.loads((a.n.metadata / ('runtime-upgrade-' + a.candidate + '.completed.json')).read_bytes())
    assert archived == dict(a.journal, phase='restored')
