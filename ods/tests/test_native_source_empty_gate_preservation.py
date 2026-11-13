"""Real receipts/FDs with the existing Darwin metadata and syscall driver.

No native account, provider or installed service runs. The initial interrupted
allocation, canonical installer history, archive bytes and rename are real.
"""
# ruff: noqa: E402,F401,F811
from contextlib import contextmanager
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux metadata driver, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_installer_code_only import (first_target, material, native, deployment, built,
    source, package, runtime_custody, bundle, services, helper, qualification_operation,
    old_component_owner_observation, finish_source_caller)
from pixel_connections import custody as c
import pixel_macos_custody as mac

spec = importlib.util.spec_from_file_location('empty_gate_preserver',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-source-prefix.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
pytestmark = pytest.mark.parametrize('first_target', ['code-only'], indirect=True)


@pytest.fixture
def gate(first_target, monkeypatch):
    n = first_target
    finish_source_caller(n, monkeypatch)
    n.upgrade.rename(n.metadata / ('runtime-upgrade-' + n.transaction['candidateDigest'] + '.completed.json'))
    def ds_write(argv, **kwargs):
        assert argv[:4] == ['/usr/bin/dscl', '-plist', '.', '-create']
        intent = c._native_identity_intent(json.loads((n.metadata / 'source-identity.json').read_bytes()))
        kind, key, value = argv[4].split('/')[1], argv[5], argv[6]
        if n.records[kind] is None:
            n.records[kind] = {}
        n.records[kind][key] = [value]
        if key == 'GeneratedUID':
            (n.acl.identities if kind == 'Users' else n.acl.group_identities)[intent['id']] = uuid.UUID(value).bytes
        return SimpleNamespace(returncode=0)
    with monkeypatch.context() as ds:
        ds.setattr(c.subprocess, 'run', ds_write)
        n.identity = c.native_prepare_identity(n.selected)
    original_save = c._native_save_at
    def interrupt(selection, directory, name, value, **kwargs):
        if name == 'source-custody-prepare.json' and value['gate'] is not None:
            raise RuntimeError('allocation-before-inode-receipt')
        return original_save(selection, directory, name, value, **kwargs)
    with monkeypatch.context() as fault:
        fault.setattr(c, '_native_save_at', interrupt)
        with pytest.raises(RuntimeError, match='allocation-before-inode-receipt'):
            c.native_prepare_state(n.selected, n.identity)
    assert c.NATIVE_STATE.is_dir() and not list(c.NATIVE_STATE.iterdir())
    assert json.loads((n.metadata / 'source-custody-prepare.json').read_bytes())['gate'] is None
    n.gate_inode = [c.NATIVE_STATE.stat().st_dev, c.NATIVE_STATE.stat().st_ino]
    n.expected = {'runtime': n.selected.deployment.document['bundleSha256'], 'gate': list(n.gate_inode), 'images': {}}
    with mac.protected_directory(n.metadata) as fd:
        for dest, leaf in p.COPIES.items():
            raw, pair = c._native_root_file(fd, leaf, limit=131072)
            n.expected['images'][dest] = {'inode': pair, 'sha256': hashlib.sha256(raw).hexdigest()}
    n.originals = {leaf: ((n.metadata / leaf).read_bytes(), (n.metadata / leaf).stat().st_ino)
        for leaf in p.COPIES.values()}
    monkeypatch.setattr(p, 'ARCHIVES', c.NATIVE_STATE.parent / 'preserved-empty-gates')
    previous_directory = mac.protected_directory
    @contextmanager
    def directory(path, **kwargs):
        if Path(path) != p.ARCHIVES and Path(path).parent != p.ARCHIVES:
            with previous_directory(path, **kwargs) as fd:
                yield fd
            return
        assert not kwargs
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            p.private_dir(c, fd)
            yield fd
        finally:
            os.close(fd)
    monkeypatch.setattr(mac, 'protected_directory', directory)
    # Only the Darwin renameatx_np kernel boundary is simulated. All receipt,
    # metadata, history, inventory and post-move checks execute unchanged.
    def rename(source, name, target, destination):
        assert c._native_at_inode(target, destination) is None
        os.rename(name, destination, src_dir_fd=source, dst_dir_fd=target)
    monkeypatch.setattr(p, 'rename_exclusive', rename)
    command = n.f.bridge.command
    def census(argv, timeout=10):
        if argv == ['/bin/launchctl', 'print', 'system']:
            return 'system = {\n}\n'
        if argv == ['/bin/ps', '-axo', 'pid=,uid=']:
            return '1 0\n42 1000\n'
        return command(argv, timeout=timeout)
    monkeypatch.setattr(n.f.bridge, 'command', census)
    return n


def preserve(n):
    return p.preserve_empty_gate(c, mac, n.selected, n.expected)


def originals_unchanged(n):
    assert {leaf: ((n.metadata / leaf).read_bytes(), (n.metadata / leaf).stat().st_ino)
        for leaf in p.COPIES.values()} == n.originals


def test_preserves_unadoptable_gate_and_receipts_then_replays_same_capsule(gate):
    n = gate
    result = preserve(n)
    archive = Path(result['archive'])
    assert result['status'] == 'preserved-empty-first-gate' and result['sourcePrepared'] is False
    assert not c.NATIVE_STATE.exists()
    assert (archive / 'gate').stat().st_ino == n.gate_inode[1]
    assert not list((archive / 'gate').iterdir())
    originals_unchanged(n)
    assert preserve(n) == result
    originals_unchanged(n)
    # Normal preparer creates and records a NEW gate; the retained inode is
    # not adopted or given the owner's/broker's list/search ACL.
    result = c.native_prepare_state(n.selected, n.identity)
    assert result['phase'] == 'prepared-no-jobs'
    assert c.NATIVE_STATE.stat().st_ino != n.gate_inode[1]
    assert json.loads((n.metadata / 'source-custody-prepare.json').read_bytes())['gate'][1] == c.NATIVE_STATE.stat().st_ino
    assert (archive / 'gate').stat().st_ino == n.gate_inode[1]


@pytest.mark.parametrize('change', ['content', 'mode', 'owner', 'group', 'acl', 'symlink', 'receipt-hardlink', 'selected-inode', 'selected-receipt',
    'recorded-gate', 'later-stage', 'unknown-metadata', 'google-record', 'source-job', 'source-process', 'runtime'])
def test_refuses_changed_or_nonempty_prefix_before_archive_creation(gate, monkeypatch, change):
    n = gate
    if change == 'content':
        (c.NATIVE_STATE / 'keep.txt').write_bytes(b'invented data retained')
    elif change == 'mode':
        c.NATIVE_STATE.chmod(0o750)
    elif change == 'owner':
        n.owners[tuple(n.gate_inode)] = (1000, 0)
    elif change == 'group':
        n.owners[tuple(n.gate_inode)] = (0, 20)
    elif change == 'acl':
        n.acl.acls[tuple(n.gate_inode)] = {'entries': [{'tag': 1, 'uuid': b'x' * 16, 'mask': 1}], 'flags': 0}
    elif change == 'symlink':
        other = n.world / 'unrelated-empty-directory'
        other.mkdir(mode=0o700)
        c.NATIVE_STATE.rmdir()
        c.NATIVE_STATE.symlink_to(other, target_is_directory=True)
    elif change == 'receipt-hardlink':
        os.link(n.metadata / 'source-identity.json', n.world / 'identity-link')
    elif change == 'selected-inode':
        n.expected['gate'][1] += 1
    elif change == 'selected-receipt':
        n.expected['images']['custody.json']['sha256'] = '0' * 64
    elif change == 'recorded-gate':
        leaf = n.metadata / 'source-custody-prepare.json'
        doc = json.loads(leaf.read_bytes())
        doc['gate'] = n.gate_inode
        leaf.write_bytes(c._native_encoded(doc))
        n.expected['images']['custody.json']['sha256'] = hashlib.sha256(leaf.read_bytes()).hexdigest()
    elif change == 'later-stage':
        (n.metadata / 'source-execution.json').write_bytes(b'{}')
    elif change == 'unknown-metadata':
        (n.metadata / 'source-unrecognized.json').write_bytes(b'{}')
    elif change == 'google-record':
        monkeypatch.setattr(c, 'NATIVE_CONNECTION_RECORD', n.world / 'existing-connection.json')
        c.NATIVE_CONNECTION_RECORD.write_bytes(b'invented preserved record')
    elif change in ('source-job', 'source-process'):
        previous = n.f.bridge.command
        def command(argv, timeout=10):
            if change == 'source-job' and argv == ['/bin/launchctl', 'print', 'system']:
                return 'system = {\n' + c.NATIVE_LABEL + 'sync = {}\n}\n'
            if change == 'source-process' and argv == ['/bin/ps', '-axo', 'pid=,uid=']:
                return '1 0\n42 ' + str(n.identity['id']) + '\n'
            return previous(argv, timeout=timeout)
        monkeypatch.setattr(n.f.bridge, 'command', command)
    else:
        n.expected['runtime'] = '0' * 64
    with pytest.raises((p.PrefixError, c.CustodyError, OSError)):
        preserve(n)
    assert c.NATIVE_STATE.exists() and not p.ARCHIVES.exists()


@pytest.mark.parametrize('when', ['before-rename', 'after-rename'])
def test_sealed_manifest_and_exclusive_move_recover_lost_acknowledgement(gate, monkeypatch, when):
    n = gate
    original = p.rename_exclusive
    def interrupted(*args):
        if when == 'after-rename':
            original(*args)
        raise OSError('invented lost acknowledgement')
    with monkeypatch.context() as fault:
        fault.setattr(p, 'rename_exclusive', interrupted)
        with pytest.raises(OSError, match='lost acknowledgement'):
            preserve(n)
    result = preserve(n)
    assert (Path(result['archive']) / 'gate').stat().st_ino == n.gate_inode[1]
    assert not c.NATIVE_STATE.exists()
    originals_unchanged(n)


def test_archive_tampering_refuses_and_keeps_the_retained_directory(gate):
    n = gate
    result = preserve(n)
    archive = Path(result['archive'])
    copy = archive / 'custody.json'
    copy.write_bytes(copy.read_bytes() + b' ')
    with pytest.raises(p.PrefixError, match='copy-changed'):
        preserve(n)
    assert not c.NATIVE_STATE.exists() and (archive / 'gate').stat().st_ino == n.gate_inode[1]
    originals_unchanged(n)


def test_a_new_source_slot_is_never_removed_or_adopted_by_replay(gate):
    n = gate
    result = preserve(n)
    c.NATIVE_STATE.mkdir(mode=0o700)
    new_inode = c.NATIVE_STATE.stat().st_ino
    with pytest.raises(p.PrefixError, match='slot-not-empty'):
        preserve(n)
    assert c.NATIVE_STATE.stat().st_ino == new_inode
    assert (Path(result['archive']) / 'gate').stat().st_ino == n.gate_inode[1]


def test_unsealed_partial_capsule_is_retained_and_never_recreated(gate, monkeypatch):
    n = gate
    original = p.write_new
    def interrupted(custody, directory, name, body):
        if name == 'custody.json':
            raise OSError('invented interrupted copy')
        return original(custody, directory, name, body)
    with monkeypatch.context() as fault:
        fault.setattr(p, 'write_new', interrupted)
        with pytest.raises(OSError, match='interrupted copy'):
            preserve(n)
    saved = {path: (path.read_bytes(), path.stat().st_ino) for path in p.ARCHIVES.rglob('*') if path.is_file()}
    with pytest.raises(p.PrefixError, match='unsealed-or-unknown'):
        preserve(n)
    assert {path: (path.read_bytes(), path.stat().st_ino) for path in saved} == saved
    assert c.NATIVE_STATE.stat().st_ino == n.gate_inode[1]
    originals_unchanged(n)


@pytest.fixture
def last_child(gate, monkeypatch):
    n = gate
    preserve(n)
    original = c._native_save_at
    def interrupt(selection, directory, name, value, **kwargs):
        if name == 'source-custody-prepare.json' and 'proposals' in value['children']:
            raise RuntimeError('last-allocation-before-receipt')
        return original(selection, directory, name, value, **kwargs)
    with monkeypatch.context() as fault:
        fault.setattr(c, '_native_save_at', interrupt)
        with pytest.raises(RuntimeError, match='last-allocation-before-receipt'):
            c.native_prepare_state(n.selected, n.identity)
    gate_info = c.NATIVE_STATE.stat()
    child_info = (c.NATIVE_STATE / 'proposals').stat()
    n.expected['gate'] = [gate_info.st_dev, gate_info.st_ino]
    n.expected['child'] = {'name': 'proposals', 'inode': [child_info.st_dev, child_info.st_ino]}
    with mac.protected_directory(n.metadata) as fd:
        for dest, leaf in p.COPIES.items():
            raw, pair = c._native_root_file(fd, leaf, limit=131072)
            n.expected['images'][dest] = {'inode': pair, 'sha256': hashlib.sha256(raw).hexdigest()}
    n.originals = {leaf: ((n.metadata / leaf).read_bytes(), (n.metadata / leaf).stat().st_ino)
        for leaf in p.COPIES.values()}
    return n


def test_preserved_last_child_is_replaced_by_normal_preparer(last_child):
    n = last_child
    result = p.preserve_empty_child(c, mac, n.selected, n.expected)
    assert result['status'] == 'preserved-empty-first-child'
    archive = Path(result['archive'])
    assert (archive / 'child').stat().st_ino == n.expected['child']['inode'][1]
    assert not (c.NATIVE_STATE / 'proposals').exists()
    originals_unchanged(n)
    assert p.preserve_empty_child(c, mac, n.selected, n.expected) == result
    assert c.native_prepare_state(n.selected, n.identity)['phase'] == 'prepared-no-jobs'
    assert (c.NATIVE_STATE / 'proposals').stat().st_ino != n.expected['child']['inode'][1]
    assert (archive / 'child').stat().st_ino == n.expected['child']['inode'][1]


@pytest.mark.parametrize('change', ['child-data', 'private-data', 'child-inode', 'recorded-inode', 'acl', 'later'])
def test_last_child_preservation_refuses_changes(last_child, change):
    n = last_child
    if change == 'child-data':
        (c.NATIVE_STATE / 'proposals/keep').write_bytes(b'preserve')
    elif change == 'private-data':
        (c.NATIVE_STATE / 'private/keep').write_bytes(b'preserve')
    elif change == 'child-inode':
        n.expected['child']['inode'][1] += 1
    elif change == 'recorded-inode':
        n.expected['gate'][1] += 1
    elif change == 'acl':
        n.acl.acls[tuple(n.expected['child']['inode'])] = {'entries': [{'tag': 1, 'uuid': b'x' * 16, 'mask': 1}], 'flags': 0}
    else:
        (n.metadata / 'source-environment.json').write_bytes(b'{}')
    archive = p.ARCHIVES / ('first-child-' + p.selection_id(n.expected))
    with pytest.raises((p.PrefixError, c.CustodyError, OSError)):
        p.preserve_empty_child(c, mac, n.selected, n.expected)
    assert (c.NATIVE_STATE / 'proposals').is_dir() and not archive.exists()


@pytest.mark.parametrize('when', ['before', 'after'])
def test_last_child_lost_rename_acknowledgement(last_child, monkeypatch, when):
    n = last_child
    rename = p.rename_exclusive
    def interrupt(*args):
        if when == 'after':
            rename(*args)
        raise OSError('lost-child-ack')
    with monkeypatch.context() as fault:
        fault.setattr(p, 'rename_exclusive', interrupt)
        with pytest.raises(OSError, match='lost-child-ack'):
            p.preserve_empty_child(c, mac, n.selected, n.expected)
    result = p.preserve_empty_child(c, mac, n.selected, n.expected)
    assert (Path(result['archive']) / 'child').stat().st_ino == n.expected['child']['inode'][1]
    originals_unchanged(n)
