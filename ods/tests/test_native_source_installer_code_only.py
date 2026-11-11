"""Canonical inert-code upgrade through the actual installer and publisher."""
# ruff: noqa: E402,F401,F811
import hashlib
import json
from contextlib import contextmanager
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux metadata driver, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_installer_first_kit import (first_target, material, native, deployment, built,
    source, package, runtime_custody, bundle, services, helper, qualification_operation)
from test_native_source_installer_withdrawal import files
from test_macos_pixel_access_install import installer
from pixel_connections import custody as c, source_backend, native_executor, status
import pixel_access_bridge as access_bridge
import pixel_macos_custody as mac


@pytest.fixture(autouse=True)
def old_component_owner_observation(first_target, monkeypatch):
    """Only the immutable old component's Apple UID/GID observation differs."""
    original = c._native_installer_old_publisher
    component = Path(first_target.absence['runtimeRoot']).parent / 'native-source'
    def load(authority, **kwargs):
        module = original(authority, **kwargs)  # Actual old Root journal/runtime proof.
        read = module._read_at
        def old_read(directory, name, **kwargs):
            if Path(os.readlink('/proc/self/fd/' + str(directory))) == component:
                kwargs.update(uid=os.getuid(), gid=os.getgid())
            return read(directory, name, **kwargs)
        monkeypatch.setattr(module, '_read_at', old_read)
        return module
    monkeypatch.setattr(c, '_native_installer_old_publisher', load)


def finish_source_caller(n, monkeypatch):
    context = json.loads(n.context.read_bytes())
    # Only reconstruction of the physical bridge/settings location is an OS
    # boundary. Actual selection, context, journal, code, owner FD and typed
    # publication/verification execute their production implementations.
    settings = n.world / 'test-access-settings.json'
    settings.write_bytes(c._native_encoded({'fixture': 'physical-bridge-driver'}))
    settings.chmod(0o600)
    monkeypatch.setitem(installer.ACCESS_FILES, 'config', settings)
    monkeypatch.setattr(installer, '_bridge_from_recovery_plan', lambda plan, *, owner_name: n.f.bridge)
    def discovered(bridge):
        # This mechanical fixture already supplies the physically discovered
        # Apple bridge. Cold protected-import/discovery is a separate packet.
        assert bridge is n.f.bridge and bridge.owner.pw_uid == n.f.bridge.owner.pw_uid
        assert bridge._runtime_config_path == n.f.config
        helper._material(n.selected)  # Actual immutable runtime remains checked.
    monkeypatch.setattr(installer, '_discover_source_installer_bridge', discovered)
    plan = dict(source_lifecycle=context['source_lifecycle'], owner=n.f.bridge.owner,
        upgrade_qualification=context['upgrade_qualification'])
    rows = [dict(path=str(settings), before=settings.read_bytes(), after=settings.read_bytes())]
    journal = SimpleNamespace(value=json.loads(n.upgrade.read_bytes()))
    installer._finish_source_upgrade(plan, rows, journal)
    return plan, rows, journal


@pytest.mark.parametrize('first_target', ['code-only'], indirect=True)
def test_code_only_actual_installer_target_retains_original_kit_and_owner_images(first_target, monkeypatch):
    n = first_target
    before = {path: (path.read_bytes(), path.stat().st_ino) for path in (n.f.env, n.f.answers)}
    old_kit = n.kit.stat().st_ino
    old_publication = (n.metadata / 'source-publication.json').stat().st_ino
    old_sidefiles = (n.metadata / 'source-sidefiles.json').stat().st_ino
    plan, rows, journal = finish_source_caller(n, monkeypatch)
    result = helper.verify_installer_code_only_kit(n.selected, transaction=n.transaction, proof=n.absence)
    assert result['status'] == 'prepared-no-jobs' and result['transaction'] == n.transaction
    record = json.loads((n.metadata / 'source-installer-code-only.json').read_bytes())
    assert record['phase'] == 'committed'
    assert (n.kit.parent / record['backup']).stat().st_ino == old_kit
    assert (n.metadata / ('.source-code-only-publication-before-' + record['nonce'] + '.json')).stat().st_ino == old_publication
    assert (n.metadata / ('.source-code-only-sidefiles-before-' + record['nonce'] + '.json')).stat().st_ino == old_sidefiles
    assert {path: (path.read_bytes(), path.stat().st_ino) for path in before} == before
    assert not c.NATIVE_STATE.exists() and not c.NATIVE_LOGS.exists() and not (n.metadata / 'source-identity.json').exists()
    snapshot = files(n)
    installer._finish_source_upgrade(plan, rows, journal)
    assert helper.publish_installer_code_only_kit(n.selected, transaction=n.transaction, proof=n.absence) == result
    assert files(n) == snapshot and n.records == {'Users': None, 'Groups': None}


@pytest.mark.parametrize('first_target', ['code-only'], indirect=True)
def test_code_only_directory_created_before_inode_record_is_refused_on_retry(first_target, monkeypatch):
    n = first_target
    mkdir = os.mkdir
    interrupted = []
    def after_mkdir(path, *args, **kwargs):
        result = mkdir(path, *args, **kwargs)
        if str(path).startswith('.source-kit-') and not interrupted:
            interrupted.append(True)
            raise OSError('interrupted after candidate mkdir')
        return result
    monkeypatch.setattr(os, 'mkdir', after_mkdir)
    with pytest.raises(OSError, match='interrupted after candidate mkdir'):
        helper.publish_installer_code_only_kit(n.selected, transaction=n.transaction, proof=n.absence)
    monkeypatch.setattr(os, 'mkdir', mkdir)
    record = json.loads((n.metadata / 'source-installer-code-only.json').read_bytes())
    candidate = n.kit.parent / record['candidate']
    assert interrupted and record['phase'] == 'staging' and record['candidateInode'] is None
    assert candidate.is_dir() and not list(candidate.iterdir())
    snapshot = files(n)
    with pytest.raises(c.CustodyError, match='native-source-installer-code-only-prefix-changed'):
        helper.publish_installer_code_only_kit(n.selected, transaction=n.transaction, proof=n.absence)
    assert files(n) == snapshot


@pytest.mark.parametrize('first_target', ['code-only'], indirect=True)
@pytest.mark.parametrize('fault', [None, 'nonempty', 'hold-changed', 'snapshot-changed', 'receipt-conflict'])
def test_explicit_empty_stage_quarantine_keeps_inodes_and_requires_live_authority(first_target, monkeypatch, fault):
    n = first_target
    mkdir = os.mkdir
    interrupted = []
    def after_mkdir(path, *args, **kwargs):
        result = mkdir(path, *args, **kwargs)
        if str(path).startswith('.source-kit-') and not interrupted:
            interrupted.append(True)
            raise OSError('interrupted after candidate mkdir')
        return result
    monkeypatch.setattr(os, 'mkdir', after_mkdir)
    with pytest.raises(OSError, match='interrupted after candidate mkdir'):
        helper.publish_installer_code_only_kit(n.selected, transaction=n.transaction, proof=n.absence)
    monkeypatch.setattr(os, 'mkdir', mkdir)
    record = json.loads((n.metadata / 'source-installer-code-only.json').read_bytes())
    candidate = n.kit.parent / record['candidate']
    inode, old_kit = candidate.stat().st_ino, n.kit.stat().st_ino
    if fault == 'nonempty': (candidate / 'unexpected').write_text('preserve')
    receipt = n.metadata / ('.ods-empty-source-stage-' + record['nonce'] + '.json')
    if fault == 'receipt-conflict':
        receipt.write_bytes(b'{"different":true}')
        receipt.chmod(0o600)
    context = json.loads(n.context.read_bytes())
    settings = n.world / 'test-access-settings.json'
    settings.write_bytes(c._native_encoded({'fixture': 'physical-bridge-driver'}))
    settings.chmod(0o600)
    monkeypatch.setitem(installer.ACCESS_FILES, 'config', settings)
    monkeypatch.setattr(installer, '_bridge_from_recovery_plan', lambda plan, *, owner_name: n.f.bridge)
    monkeypatch.setattr(installer, '_discover_source_installer_bridge', lambda _bridge: None)
    calls = []
    def held(_plan):
        calls.append('held')
        return 'changed' if fault == 'hold-changed' and len(calls) > 1 else 'same-hold'
    monkeypatch.setattr(installer, '_resume_migration_hold', held)
    def exclusive_rename(src, name, dst, target):
        assert c._native_at_inode(dst, target) is None
        os.rename(name, target, src_dir_fd=src, dst_dir_fd=dst)
    monkeypatch.setattr(installer, '_sidefile_archive_rename', exclusive_rename)
    plan = dict(source_lifecycle=context['source_lifecycle'], owner=n.f.bridge.owner,
        upgrade_qualification=context['upgrade_qualification'])
    rows = [dict(path=str(settings), before=settings.read_bytes(), after=settings.read_bytes())]
    journal = SimpleNamespace(value=json.loads(n.upgrade.read_bytes()))
    quarantine = n.metadata / ('.ods-quarantined-source-kit-' + record['nonce'])
    def verify():
        if fault == 'snapshot-changed':
            raise installer.InstallError('native-source-empty-stage-snapshot-changed')
    def run():
        installer._quarantine_unrecorded_source_stage(plan, rows, journal, verify_snapshots=verify)
    if fault:
        with pytest.raises(installer.InstallError, match='native-source-empty-stage-'):
            run()
        assert candidate.stat().st_ino == inode and not quarantine.exists()
        if fault == 'nonempty': assert (candidate / 'unexpected').read_text() == 'preserve'
        if fault == 'receipt-conflict': assert receipt.read_bytes() == b'{"different":true}'
    else:
        run()
        assert not candidate.exists() and quarantine.stat().st_ino == inode
        assert json.loads(receipt.read_bytes())['directoryInode'][1] == inode
        assert n.kit.stat().st_ino == old_kit
        run()  # A lost reply never renames or removes the retained object.
        assert quarantine.stat().st_ino == inode
        finish_source_caller(n, monkeypatch)
        assert json.loads((n.metadata / 'source-installer-code-only.json').read_bytes())['phase'] == 'committed'
        assert quarantine.stat().st_ino == inode


@pytest.mark.parametrize('first_target', ['code-only'], indirect=True)
@pytest.mark.parametrize('fault', [None, 'nonempty', 'recorded-changed', 'hold-changed',
    'two-unrecorded', 'receipt-conflict', 'replay-receipt-changed'])
def test_empty_unrecorded_file_is_preserved_only_with_qualified_authority(first_target, monkeypatch, fault):
    n = first_target
    open_file = os.open
    created = []
    def after_open(path, flags, *args, **kwargs):
        fd = open_file(path, flags, *args, **kwargs)
        parent_fd = kwargs.get('dir_fd')
        if (flags & os.O_CREAT and isinstance(path, str) and parent_fd is not None
                and path in {'source-kit.json', *helper.PAYLOADS}
                and Path(os.readlink('/proc/self/fd/' + str(parent_fd))).name.startswith('.source-kit-')):
            created.append(path)
            if len(created) == 5:
                os.close(fd)
                raise OSError('interrupted after fifth file creation')
        return fd
    monkeypatch.setattr(os, 'open', after_open)
    with pytest.raises(OSError, match='interrupted after fifth file creation'):
        helper.publish_installer_code_only_kit(n.selected, transaction=n.transaction, proof=n.absence)
    monkeypatch.setattr(os, 'open', open_file)
    record = json.loads((n.metadata / 'source-installer-code-only.json').read_bytes())
    candidate = n.kit.parent / record['candidate']
    leaf = created[-1]
    orphan = candidate / leaf
    assert sum(value['ready'] for value in record['stagedFiles'].values()) == 4
    assert record['stagedFiles'][leaf] == {'inode': None, 'ready': False}
    assert orphan.read_bytes() == b''
    orphan_inode = orphan.stat().st_ino
    orphan_device = orphan.stat().st_dev
    tag = hashlib.sha256(leaf.encode()).hexdigest()[:16] + '-' + format(orphan_device, 'x') + '-' + format(orphan_inode, 'x')
    receipt = n.metadata / ('.ods-quarantined-source-file-' + record['nonce'] + '-' + tag + '.json')
    prior = {name: ((candidate / name).read_bytes(), (candidate / name).stat().st_ino)
        for name, image in record['stagedFiles'].items() if image['ready']}
    if fault == 'nonempty': orphan.write_bytes(b'preserve')
    if fault == 'recorded-changed':
        name = next(iter(prior))
        (candidate / name).write_bytes(b'changed')
    if fault == 'two-unrecorded':
        name = next(name for name, image in record['stagedFiles'].items()
            if image['inode'] is None and name != leaf)
        (candidate / name).write_bytes(b'')
        (candidate / name).chmod(0o600)
    if fault == 'receipt-conflict':
        receipt.write_bytes(b'{"different":true}')
        receipt.chmod(0o600)
    context = json.loads(n.context.read_bytes())
    settings = n.world / 'test-access-settings.json'
    settings.write_bytes(c._native_encoded({'fixture': 'physical-bridge-driver'}))
    settings.chmod(0o600)
    monkeypatch.setitem(installer.ACCESS_FILES, 'config', settings)
    monkeypatch.setattr(installer, '_bridge_from_recovery_plan', lambda plan, *, owner_name: n.f.bridge)
    monkeypatch.setattr(installer, '_discover_source_installer_bridge', lambda _bridge: None)
    calls = []
    def held(_plan):
        calls.append('held')
        return 'changed' if fault == 'hold-changed' and len(calls) > 1 else 'same-hold'
    monkeypatch.setattr(installer, '_resume_migration_hold', held)
    def exclusive_rename(src, name, dst, target):
        assert c._native_at_inode(dst, target) is None
        os.rename(name, target, src_dir_fd=src, dst_dir_fd=dst)
    monkeypatch.setattr(installer, '_sidefile_archive_rename', exclusive_rename)
    plan = dict(source_lifecycle=context['source_lifecycle'], owner=n.f.bridge.owner,
        upgrade_qualification=context['upgrade_qualification'])
    rows = [dict(path=str(settings), before=settings.read_bytes(), after=settings.read_bytes())]
    journal = SimpleNamespace(value=json.loads(n.upgrade.read_bytes()))
    def run():
        installer._quarantine_unrecorded_source_stage(plan, rows, journal, verify_snapshots=lambda: None)
    if fault and fault != 'replay-receipt-changed':
        with pytest.raises((installer.InstallError, c.CustodyError, ValueError)):
            run()
        assert orphan.stat().st_ino == orphan_inode
        if fault == 'nonempty': assert orphan.read_bytes() == b'preserve'
        if fault == 'receipt-conflict': assert receipt.read_bytes() == b'{"different":true}'
    else:
        run()
        retained = list(n.metadata.glob('.ods-quarantined-source-file-*.file'))
        assert len(retained) == 1 and retained[0].stat().st_ino == orphan_inode and retained[0].read_bytes() == b''
        assert not orphan.exists()
        assert {name: ((candidate / name).read_bytes(), (candidate / name).stat().st_ino) for name in prior} == prior
        if fault == 'replay-receipt-changed':
            changed = json.loads(receipt.read_bytes())
            changed['unexpected'] = True
            receipt.write_bytes(c._native_encoded(changed))
            with pytest.raises(installer.InstallError, match='receipt-changed'): run()
            assert retained[0].stat().st_ino == orphan_inode
            return
        run()  # A lost acknowledgement reuses only the matching protected receipt.
        finish_source_caller(n, monkeypatch)
        assert json.loads((n.metadata / 'source-installer-code-only.json').read_bytes())['phase'] == 'committed'
        assert retained[0].stat().st_ino == orphan_inode


def quarantine_caller(n, monkeypatch, *, hold_changes=False):
    """The explicit recovery operation over the actual journal; OS rename/discovery are drivers."""
    context = json.loads(n.context.read_bytes())
    settings = n.world / 'test-access-settings.json'
    settings.write_bytes(c._native_encoded({'fixture': 'physical-bridge-driver'}))
    settings.chmod(0o600)
    monkeypatch.setitem(installer.ACCESS_FILES, 'config', settings)
    monkeypatch.setattr(installer, '_bridge_from_recovery_plan', lambda plan, *, owner_name: n.f.bridge)
    monkeypatch.setattr(installer, '_discover_source_installer_bridge', lambda _bridge: None)
    calls = []
    def held(_plan):
        calls.append('held')
        return 'changed' if hold_changes and len(calls) > 1 else 'same-hold'
    monkeypatch.setattr(installer, '_resume_migration_hold', held)
    def exclusive_rename(src, name, dst, target):
        assert c._native_at_inode(dst, target) is None
        os.rename(name, target, src_dir_fd=src, dst_dir_fd=dst)
    monkeypatch.setattr(installer, '_sidefile_archive_rename', exclusive_rename)
    plan = dict(source_lifecycle=context['source_lifecycle'], owner=n.f.bridge.owner,
        upgrade_qualification=context['upgrade_qualification'])
    rows = [dict(path=str(settings), before=settings.read_bytes(), after=settings.read_bytes())]
    journal = SimpleNamespace(value=json.loads(n.upgrade.read_bytes()))
    return lambda: installer._quarantine_unrecorded_source_stage(plan, rows, journal, verify_snapshots=lambda: None)


@pytest.mark.parametrize('first_target', [None], indirect=True)
@pytest.mark.parametrize('fault', [None, 'nonempty', 'unknown-stage', 'hold-changed', 'receipt-conflict'])
def test_first_kit_directory_left_by_expired_finish_is_quarantined_then_published(first_target, monkeypatch, fault):
    """The physical Mac state: an expired finish created the first candidate, unjournaled."""
    n = first_target
    assert n.absence['kit'] is None
    mkdir = os.mkdir
    interrupted = []
    def after_mkdir(path, *args, **kwargs):
        result = mkdir(path, *args, **kwargs)
        if str(path).startswith('.source-kit-') and not interrupted:
            interrupted.append(True)
            raise access_bridge.AccessError('operation-deadline-exceeded')
        return result
    monkeypatch.setattr(os, 'mkdir', after_mkdir)
    with pytest.raises(access_bridge.AccessError, match='operation-deadline-exceeded'):
        finish_source_caller(n, monkeypatch)
    monkeypatch.setattr(os, 'mkdir', mkdir)
    record = json.loads((n.metadata / 'source-publication.json').read_bytes())
    candidate = n.kit.parent / record['candidate']
    assert interrupted and record['phase'] == 'staging' and record['candidateInode'] is None
    assert record['old'] is None and candidate.is_dir() and not list(candidate.iterdir()) and not n.kit.exists()
    snapshot = files(n)
    # Resuming the same finish refuses the unjournaled prefix, as on the Mac.
    with pytest.raises(c.CustodyError, match='native-source-installer-first-kit-prefix-changed'):
        finish_source_caller(n, monkeypatch)
    assert files(n) == snapshot
    inode = candidate.stat().st_ino
    if fault == 'nonempty': (candidate / 'unexpected').write_text('preserve')
    if fault == 'unknown-stage': (n.kit.parent / '.source-kit-unknown').mkdir(mode=0o700)
    receipt = n.metadata / ('.ods-empty-source-stage-' + record['nonce'] + '.json')
    if fault == 'receipt-conflict':
        receipt.write_bytes(b'{"different":true}')
        receipt.chmod(0o600)
    quarantine = n.metadata / ('.ods-quarantined-source-kit-' + record['nonce'])
    run = quarantine_caller(n, monkeypatch, hold_changes=fault == 'hold-changed')
    if fault:
        expected = {'nonempty': 'changed', 'unknown-stage': 'changed', 'hold-changed': 'authority-changed',
            'receipt-conflict': 'receipt-changed'}[fault]
        with pytest.raises(installer.InstallError, match='^native-source-empty-stage-' + expected + '$'):
            run()
        assert candidate.stat().st_ino == inode and not quarantine.exists()
        if fault == 'nonempty': assert (candidate / 'unexpected').read_text() == 'preserve'
        if fault == 'receipt-conflict': assert receipt.read_bytes() == b'{"different":true}'
        return
    run()
    assert not candidate.exists() and quarantine.stat().st_ino == inode
    sealed = json.loads(receipt.read_bytes())
    assert sealed['directoryInode'][1] == inode and sealed['candidate'] == record['candidate']
    assert sealed['journalSha256'] == hashlib.sha256((n.metadata / 'source-publication.json').read_bytes()).hexdigest()
    run()  # A lost reply never renames or removes the retained object.
    assert quarantine.stat().st_ino == inode
    finish_source_caller(n, monkeypatch)
    final = json.loads((n.metadata / 'source-publication.json').read_bytes())
    assert final['phase'] == 'prepared-no-jobs' and final['nonce'] == record['nonce']
    assert final['candidateInode'] == [n.kit.stat().st_dev, n.kit.stat().st_ino] and n.kit.stat().st_ino != inode
    assert quarantine.stat().st_ino == inode and not list(quarantine.iterdir())
    assert not c.NATIVE_STATE.exists() and not (n.metadata / 'source-identity.json').exists()


@pytest.mark.parametrize('first_target', [None], indirect=True)
@pytest.mark.parametrize('fault', [None, 'nonempty', 'two-unrecorded'])
def test_first_kit_unjournaled_empty_file_is_quarantined_then_published(first_target, monkeypatch, fault):
    n = first_target
    open_file = os.open
    created = []
    def after_open(path, flags, *args, **kwargs):
        fd = open_file(path, flags, *args, **kwargs)
        parent_fd = kwargs.get('dir_fd')
        if (flags & os.O_CREAT and isinstance(path, str) and parent_fd is not None
                and path in {'source-kit.json', *helper.PAYLOADS}
                and Path(os.readlink('/proc/self/fd/' + str(parent_fd))).name.startswith('.source-kit-')):
            created.append(path)
            if len(created) == 5:
                os.close(fd)
                raise access_bridge.AccessError('operation-deadline-exceeded')
        return fd
    monkeypatch.setattr(os, 'open', after_open)
    with pytest.raises(access_bridge.AccessError, match='operation-deadline-exceeded'):
        finish_source_caller(n, monkeypatch)
    monkeypatch.setattr(os, 'open', open_file)
    record = json.loads((n.metadata / 'source-publication.json').read_bytes())
    candidate = n.kit.parent / record['candidate']
    leaf = created[-1]
    orphan = candidate / leaf
    assert sum(value['ready'] for value in record['stagedFiles'].values()) == 4
    assert record['stagedFiles'][leaf] == {'inode': None, 'ready': False} and orphan.read_bytes() == b''
    orphan_inode = orphan.stat().st_ino
    with pytest.raises(ValueError, match='native-source-generation-prefix-changed'):
        finish_source_caller(n, monkeypatch)
    prior = {name: ((candidate / name).read_bytes(), (candidate / name).stat().st_ino)
        for name, image in record['stagedFiles'].items() if image['ready']}
    if fault == 'nonempty': orphan.write_bytes(b'preserve')
    if fault == 'two-unrecorded':
        name = next(name for name, image in record['stagedFiles'].items() if image['inode'] is None and name != leaf)
        (candidate / name).write_bytes(b'')
        (candidate / name).chmod(0o600)
    run = quarantine_caller(n, monkeypatch)
    if fault:
        expected = {'nonempty': 'changed', 'two-unrecorded': 'unqualified'}[fault]
        with pytest.raises(installer.InstallError, match='^native-source-empty-file-' + expected + '$'):
            run()
        assert orphan.stat().st_ino == orphan_inode
        if fault == 'nonempty': assert orphan.read_bytes() == b'preserve'
        return
    run()
    retained = list(n.metadata.glob('.ods-quarantined-source-file-' + record['nonce'] + '-*.file'))
    assert len(retained) == 1 and retained[0].stat().st_ino == orphan_inode and retained[0].read_bytes() == b''
    assert not orphan.exists()
    assert {name: ((candidate / name).read_bytes(), (candidate / name).stat().st_ino) for name in prior} == prior
    run()  # A lost acknowledgement reuses only the matching protected receipt.
    finish_source_caller(n, monkeypatch)
    final = json.loads((n.metadata / 'source-publication.json').read_bytes())
    assert final['phase'] == 'prepared-no-jobs' and final['candidateInode'] == record['candidateInode']
    assert retained[0].stat().st_ino == orphan_inode


# Physical cost of one protected proof on the M4 Mac mini fleet host (2026-11-11),
# with the shipped 34,651-entry runtime: a deployment qualification (4.6 s) and
# its bundle/material qualification (3.6 s). The driver's publisher stand-in omits
# the production publisher's own selection proof, which is added back here.
M4_DEPLOYMENT_SECONDS, M4_BUNDLE_SECONDS = 4.6, 3.6


@pytest.mark.parametrize('first_target', [None, 'code-only'], indirect=True)
def test_finish_bound_keeps_twice_the_measured_physical_proof_cost(first_target, monkeypatch):
    n = first_target
    counts = dict(deployment=0, bundle=0, publisher=0)
    deployment, material, publisher = access_bridge.LaunchdAccessBridge.connection_deployment, helper._material, native_executor._publisher
    def counted_deployment(self):
        counts['deployment'] += 1
        return deployment(self)
    def counted_material(selection):
        counts['bundle'] += 1
        return material(selection)
    def counted_publisher(*args, **kwargs):
        counts['publisher'] += 1
        return publisher(*args, **kwargs)
    monkeypatch.setattr(access_bridge.LaunchdAccessBridge, 'connection_deployment', counted_deployment)
    monkeypatch.setattr(helper, '_material', counted_material)
    monkeypatch.setattr(native_executor, '_publisher', counted_publisher)
    requested, bounded = [], n.f.bridge.bounded
    def recorded(seconds):
        requested.append(seconds)
        return bounded(seconds)
    monkeypatch.setattr(n.f.bridge, 'bounded', recorded, raising=False)
    finish_source_caller(n, monkeypatch)
    assert requested[0] == installer.SOURCE_FINISH_SECONDS
    estimate = ((counts['deployment'] + counts['publisher']) * M4_DEPLOYMENT_SECONDS
                + counts['bundle'] * M4_BUNDLE_SECONDS)
    assert 2 * estimate <= installer.SOURCE_FINISH_SECONDS, (counts, estimate)
    assert installer.SOURCE_TRANSACTION_SECONDS >= installer.SOURCE_FINISH_SECONDS + 900


def finished_update(n, monkeypatch):
    """The fleet Mac's state after a completed native update: a kit, no Source state."""
    finish_source_caller(n, monkeypatch)
    journal = installer._upgrade.RecoveryJournal(n.upgrade, json.loads(n.upgrade.read_bytes()))
    terminal = helper.verify_installer_code_only_kit if n.absence['kit'] is not None else helper.verify_installer_absent_kit
    journal.finish(lambda: terminal(n.selected, transaction=n.transaction, proof=n.absence))
    assert not n.upgrade.exists() and not c.NATIVE_STATE.exists()


def count_publisher_proofs(monkeypatch):
    """Record each publisher load, complete material proof and selection proof, in order."""
    calls = []
    publisher, material, selected = native_executor._publisher, helper._material, c._native_selected
    def counted_publisher(*args, **kwargs):
        calls.append('publisher:current' if kwargs.get('_current_bridge') is not None else 'publisher')
        return publisher(*args, **kwargs)
    def counted_material(selection):
        calls.append('material')
        return material(selection)
    def counted_selected(selection, **kwargs):
        calls.append('selected')
        return selected(selection, **kwargs)
    monkeypatch.setattr(native_executor, '_publisher', counted_publisher)
    monkeypatch.setattr(helper, '_material', counted_material)
    monkeypatch.setattr(c, '_native_selected', counted_selected)
    return calls


@pytest.mark.parametrize('first_target', ['code-only'], indirect=True)
def test_code_only_history_qualifies_its_publisher_material_once(first_target, monkeypatch):
    """As in the code-only and first-kit authorities: the publisher's own material, never a second one."""
    n = first_target
    finished_update(n, monkeypatch)
    calls = count_publisher_proofs(monkeypatch)
    before = files(n)
    history = c.native_installer_code_only_history(n.selected)
    assert 'source-installer-code-only.json' in history['metadata'] and files(n) == before
    # One publisher and its material (whose authority proves the selection),
    # then only initial_boot_images' own material proof.
    assert calls == ['publisher', 'material', 'selected', 'material', 'selected']


@pytest.mark.parametrize('first_target', ['code-only'], indirect=True)
def test_code_only_status_keeps_each_independent_proof_once(first_target, monkeypatch):
    """The fleet Mac's post-update status: the view's proof, the history's own, initial_boot_images'."""
    n = first_target
    finished_update(n, monkeypatch)
    calls = count_publisher_proofs(monkeypatch)
    before = files(n)
    projection = source_backend.native_status_projection(n.f.bridge)
    assert not projection['statePresent'] and projection['tokenIdentity'] is None and files(n) == before
    assert calls == ['publisher:current', 'material', 'selected',  # the status view's own proof
                     'publisher', 'material', 'selected',  # the code-only history's independent proof
                     'material', 'selected']  # initial_boot_images' own material proof


# The fleet M4 ran the 39-40 s status read of 4d798e834 (six deployment and four
# bundle proofs) past its 90 s bound while the integration journeys ran: at least
# 2.3 times its idle cost. Keep the counted physical cost inside the Root bound
# at that measured slowdown.
MEASURED_LOAD_SLOWDOWN = 2.3


@pytest.mark.parametrize('first_target', ['code-only', None], indirect=True)
def test_status_fits_its_bound_at_the_measured_loaded_proof_cost(first_target, monkeypatch):
    n = first_target
    finished_update(n, monkeypatch)
    calls = count_publisher_proofs(monkeypatch)
    deployments = []
    deployment = access_bridge.LaunchdAccessBridge.connection_deployment
    def counted_deployment(self):
        deployments.append(True)
        return deployment(self)
    monkeypatch.setattr(access_bridge.LaunchdAccessBridge, 'connection_deployment', counted_deployment)
    source_backend.native_status_projection(n.f.bridge)
    # The driver's publisher stand-in omits only the production publisher's own
    # selection proof for a supplied selection; add it back for each such call.
    estimate = ((len(deployments) + calls.count('publisher')) * M4_DEPLOYMENT_SECONDS
                + calls.count('material') * M4_BUNDLE_SECONDS)
    assert MEASURED_LOAD_SLOWDOWN * estimate <= status.NATIVE_STATUS_SECONDS, (calls, len(deployments), estimate)


def finish_and_first_prepare(n, monkeypatch):
    finish_source_caller(n, monkeypatch)
    journal = installer._upgrade.RecoveryJournal(n.upgrade, json.loads(n.upgrade.read_bytes()))
    terminal = helper.verify_installer_code_only_kit if n.absence['kit'] is not None else helper.verify_installer_absent_kit
    journal.finish(lambda: terminal(n.selected,
        transaction=n.transaction, proof=n.absence))
    assert not n.upgrade.exists()
    archive = n.metadata / ('runtime-upgrade-' + n.transaction['candidateDigest'] + '.completed.json')
    assert json.loads(archive.read_bytes()) == journal.value
    before_status = files(n)
    projection = source_backend.native_status_projection(n.f.bridge)
    assert not projection['statePresent'] and projection['tokenIdentity'] is None and projection['brokerUid'] is None
    assert files(n) == before_status
    original = (n.metadata / 'source-publication.json').read_bytes()
    owner = {path: (path.read_bytes(), path.stat().st_ino) for path in (n.f.env, n.f.answers)}
    units = n.world / 'initial-units'
    units.mkdir(mode=0o755)
    monkeypatch.setattr(c, 'NATIVE_DEFINITIONS', units)
    previous_directory = mac.protected_directory
    @contextmanager
    def directory(path):
        if Path(path) == units:
            fd = os.open(units, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: yield fd
            finally: os.close(fd)
        else:
            with previous_directory(path) as fd: yield fd
    monkeypatch.setattr(mac, 'protected_directory', directory)
    monkeypatch.setattr(native_executor, 'protected_directory', directory)
    def run(argv, **kwargs):
        assert argv[:4] == ['/usr/bin/dscl', '-plist', '.', '-create']
        intent = c._native_identity_intent(json.loads((n.metadata / 'source-identity.json').read_bytes()))
        kind, key, value = argv[4].split('/')[1], argv[5], argv[6]
        if n.records[kind] is None: n.records[kind] = {}
        n.records[kind][key] = [value]
        if key == 'GeneratedUID':
            (n.acl.identities if kind == 'Users' else n.acl.group_identities)[intent['id']] = uuid.UUID(value).bytes
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(c.subprocess, 'run', run)
    backend = source_backend.for_bridge(n.f.bridge, linux_paths=c.Paths(), linux_run=None)
    backend.prepare(reader=n.f.bridge.owner.pw_name)
    parent = json.loads((n.metadata / 'source-initial-generation.json').read_bytes())
    child = json.loads((n.metadata / 'source-jobs-prepare.json').read_bytes())
    assert parent['phase'] == 'committed' and child['phase'] == 'committed-inactive'
    assert sorted(row['role'] for row in child['definitions'].values()) == ['direct', 'sync']
    assert parent['before']['document'] == json.loads(original)
    assert {path: (path.read_bytes(), path.stat().st_ino) for path in owner} == owner
    assert not (c.NATIVE_STATE / 'private/google-token.json').exists()
    before = files(n)
    backend.prepare(reader=n.f.bridge.owner.pw_name)
    assert files(n) == before


@pytest.mark.parametrize('first_target', ['code-only'], indirect=True)
def test_code_only_upgrade_then_actual_first_prepare_mints_only_inactive_slots(first_target, monkeypatch):
    finish_and_first_prepare(first_target, monkeypatch)


@pytest.mark.parametrize('first_target', ['code-only', None], indirect=True)
@pytest.mark.parametrize('released_hold', [False, True])
def test_code_only_interrupted_prefix_reboot_then_actual_finish_and_first_prepare(first_target, monkeypatch, released_hold):
    n = first_target
    rename = os.rename
    hit = []
    def renamed(first, second, **kwargs):
        result = rename(first, second, **kwargs)
        if str(first).startswith('.source-kit-') and second == n.kit.name and not hit:
            hit.append(True)
            raise OSError('invented interrupted code-only boot')
        return result
    monkeypatch.setattr(os, 'rename', renamed)
    with pytest.raises(OSError, match='interrupted code-only boot'):
        producer = helper.publish_installer_code_only_kit if n.absence['kit'] is not None else helper.publish_installer_absent_kit
        producer(n.selected, transaction=n.transaction, proof=n.absence)
    monkeypatch.setattr(os, 'rename', rename)
    record_name = 'source-installer-code-only.json' if n.absence['kit'] is not None else 'source-publication.json'
    parent = json.loads((n.metadata / record_name).read_bytes())
    new_boot = '11234567-89AB-4DEF-8123-0123456789AB'
    original_command = n.f.bridge.command
    def command(argv, timeout=20):
        if argv == ['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid']: return new_boot + '\n'
        return original_command(argv, timeout=timeout)
    monkeypatch.setattr(n.f.bridge, 'command', command)
    monkeypatch.setattr(c, '_native_boot_uuid', lambda: new_boot)
    edge = {'schemaVersion': 1, 'container': 'b' * 64, 'binding': {'token': 'c' * 64, 'revision': 'd' * 64},
        'phase': 'held', 'status': {'capability': 'available', 'phase': 'held', 'streams': 0,
            'admission_blocked': True, 'revision': 'd' * 64}}
    path = n.metadata / ('runtime-upgrade-edge-' + n.transaction['candidateDigest'] + '.json')
    if released_hold:
        edge['phase'] = edge['status']['phase'] = 'released'
        edge['status']['admission_blocked'] = False
    path.write_bytes(c._native_encoded(edge))
    path.chmod(0o600)
    if released_hold:
        snapshot = files(n)
        with pytest.raises((ValueError, c.CustodyError)):
            producer(n.selected, transaction=n.transaction, proof=n.absence)
        assert files(n) == snapshot
        return
    finish_and_first_prepare(n, monkeypatch)
    if n.absence['kit'] is None:
        initial = json.loads((n.metadata / 'source-initial-generation.json').read_bytes())
        # First prepare has its own qualified witness handoff. The installer's
        # original code publication is retained rather than rewritten there.
        final = json.loads((n.metadata / ('.source-initial-publication-' + initial['nonce'] + '.json')).read_bytes())
    else:
        final = json.loads((n.metadata / record_name).read_bytes())
    assert final['nonce'] == parent['nonce'] and final['witness'] == parent['witness']
    child = json.loads((n.metadata / 'source-jobs-prepare.json').read_bytes())
    assert child['witness']['boot'] == new_boot and child['phase'] == 'committed-inactive'


@pytest.mark.parametrize('first_target', ['code-only'], indirect=True)
@pytest.mark.parametrize('where', ['old-kit', 'new-kit', 'publication', 'sidefiles', 'terminal'])
def test_code_only_lost_reply_preserves_original_inodes_and_active_journal(first_target, monkeypatch, where):
    n = first_target
    rename, save = os.rename, c._native_save_at
    hit = []
    def renamed(first, second, **kwargs):
        result = rename(first, second, **kwargs)
        chosen = (where == 'old-kit' and str(second).startswith('.source-kit-before-')
            or where == 'new-kit' and str(first).startswith('.source-kit-') and second == n.kit.name
            or where == 'publication' and str(first).startswith('.source-code-only-publication-next-')
            or where == 'sidefiles' and str(first).startswith('.source-code-only-sidefiles-next-'))
        if chosen and not hit:
            hit.append(True)
            raise OSError('invented code-only reply loss')
        return result
    def saved(selection, directory, name, value, **kwargs):
        result = save(selection, directory, name, value, **kwargs)
        if where == 'terminal' and not hit and name == 'source-installer-code-only.json' and value['phase'] == 'committed':
            hit.append(True)
            raise OSError('invented code-only reply loss')
        return result
    monkeypatch.setattr(os, 'rename', renamed)
    monkeypatch.setattr(c, '_native_save_at', saved)
    with pytest.raises(OSError, match='code-only reply loss'):
        helper.publish_installer_code_only_kit(n.selected, transaction=n.transaction, proof=n.absence)
    first = json.loads((n.metadata / 'source-installer-code-only.json').read_bytes())
    monkeypatch.setattr(os, 'rename', rename)
    monkeypatch.setattr(c, '_native_save_at', save)
    result = helper.publish_installer_code_only_kit(n.selected, transaction=n.transaction, proof=n.absence)
    final = json.loads((n.metadata / 'source-installer-code-only.json').read_bytes())
    assert hit and final['phase'] == 'committed' and first['nonce'] == final['nonce']
    assert first['oldKit'] == final['oldKit']
    assert helper.verify_installer_code_only_kit(n.selected, transaction=n.transaction, proof=n.absence) == result
    assert json.loads(n.upgrade.read_bytes())['phase'] == 'active'
    assert not c.NATIVE_STATE.exists() and not (n.metadata / 'source-identity.json').exists()


@pytest.mark.parametrize('first_target', ['code-only'], indirect=True)
@pytest.mark.parametrize('fault', ['context', 'owner', 'state', 'publication', 'unknown-kit'])
def test_code_only_unknown_authority_or_owner_drift_refuses_before_publication(first_target, fault):
    n = first_target
    if fault == 'context': n.context.write_bytes(n.context.read_bytes() + b' ')
    elif fault == 'owner': n.f.env.write_text('unknown owner replacement\n')
    elif fault == 'state': c.NATIVE_STATE.mkdir(mode=0o700)
    elif fault == 'publication':
        path = n.metadata / 'source-publication.json'
        body = json.loads(path.read_bytes())
        body['witness']['brokerUid'] = n.f.bridge.owner.pw_uid
        path.write_bytes(c._native_encoded(body))
    else: (n.kit.parent / '.source-kit-unknown').mkdir(mode=0o700)
    snapshot = files(n)
    with pytest.raises((ValueError, c.CustodyError, OSError)):
        helper.publish_installer_code_only_kit(n.selected, transaction=n.transaction, proof=n.absence)
    assert files(n) == snapshot and not (n.metadata / 'source-installer-code-only.json').exists()
