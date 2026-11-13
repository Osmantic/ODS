"""Read-only partial-private-layout validation under a separately bound root receipt.

All filesystem objects are disposable. Root identity and Darwin ACL APIs are
explicitly simulated; these tests do not establish Mac kernel enforcement.
"""
import sys
if sys.platform == 'win32':
    from unittest import SkipTest
    raise SkipTest('Unix descriptor contracts; run in WSL/Linux or macOS')

from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('preserved_native_integrations',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-integrations-service.py')
native = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(native)


@pytest.fixture
def world(tmp_path, monkeypatch):
    uid, gid = os.getuid(), os.getgid()
    if uid == 0:
        pytest.skip('Nonroot disposable identity fixture')
    runtime, auth, logs = [tmp_path / name for name in ('runtime', 'auth', 'logs')]
    roots, acl = set(), {}
    original = os.fstat
    def metadata(fd):
        value = original(fd)
        if value.st_ino in roots:
            values = list(value)
            values[4], values[5] = 0, 0
            return os.stat_result(values)
        return value
    def no_acl(fd):
        if original(fd).st_ino in acl:
            raise ValueError('ACL present')
    def owner_acl(fd, who, *, directory=False):
        if who != uid or acl.get(original(fd).st_ino) != ('folder' if directory else 'key'):
            raise ValueError('foreign or additional ACL')
    @contextmanager
    def directory(path, **_kwargs):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            no_acl(fd)
            yield fd
        finally:
            os.close(fd)
    def read_key(who):
        assert who == uid
        folder = auth / str(uid)
        key = folder / 'owner.key'
        assert acl.get(key.stat().st_ino) == 'key' and acl.get(folder.stat().st_ino) == 'folder'
        value = key.read_bytes()
        assert len(value) == 32
        return value
    common = SimpleNamespace(verify_native_no_acl=no_acl, verify_native_owner_acl=owner_acl, native_owner_key=read_key)
    monkeypatch.setattr(native, 'operations_helpers', lambda: SimpleNamespace(custody=SimpleNamespace(
        protected_directory=directory, _require_no_acl=no_acl, protected_bytes=lambda *args, **kwargs: b'approved source')))
    monkeypatch.setattr(native, '_common_from_body', lambda _body: common)
    monkeypatch.setattr(native.pwd, 'getpwnam', lambda _owner: SimpleNamespace(
        pw_uid=uid, pw_gid=gid, pw_name='fixture', pw_dir='/Users/fixture'))
    monkeypatch.setattr(native.sys, 'platform', 'darwin')
    monkeypatch.setattr(native.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(native.os, 'fstat', metadata)
    opened = os.open
    def create(path, flags, *args, **kwargs):
        fd = opened(path, flags, *args, **kwargs)
        if path == 'owner.key' and flags & os.O_CREAT:
            roots.add(original(fd).st_ino)
        return fd
    monkeypatch.setattr(native.os, 'open', create)
    def chown(fd, who, group):
        assert (who, group) == (uid, gid)
        roots.discard(original(fd).st_ino)
    monkeypatch.setattr(native.os, 'fchown', chown)
    def grant(path, value):
        acl[path.stat().st_ino] = 'key' if path.name == 'owner.key' else 'folder'
    monkeypatch.setattr(native, '_acl', grant)
    for name, path in [('RUNTIME', runtime), ('AUTH_ROOT', auth), ('LOGS', logs)]:
        monkeypatch.setattr(native, name, path)
    def root(path, *, directory=False, size=32):
        if directory:
            path.mkdir(mode=0o700)
        else:
            path.write_bytes(b'k' * size)
            path.chmod(0o600)
        roots.add(path.stat().st_ino)
        return path
    return SimpleNamespace(runtime=runtime, auth=auth, logs=logs, uid=uid, gid=gid,
        roots=roots, acl=acl, root=root, base=tmp_path)


def full_layout(world):
    world.logs.mkdir(mode=0o755)
    world.roots.add(world.logs.stat().st_ino)
    for name in ('stdout.log', 'stderr.log'):
        (world.logs / name).write_text('private fixture\n')
        (world.logs / name).chmod(0o600)
        os.chown(world.logs / name, world.uid, world.gid)
    world.runtime.mkdir(mode=0o700)
    # Darwin inherits the parent group, including wheel under /private/tmp.
    # Reproduce the owner's runtime/log ownership established by provision().
    os.chown(world.runtime, world.uid, world.gid)
    world.auth.mkdir(mode=0o755)
    world.roots.add(world.auth.stat().st_ino)
    folder = world.root(world.auth / str(world.uid), directory=True)
    key = world.root(folder / 'owner.key')
    world.acl[key.stat().st_ino], world.acl[folder.stat().st_ino] = 'key', 'folder'
    return folder, key


@pytest.mark.parametrize('stage', ['absent', 'logs-empty', 'logs-root-stdout', 'runtime-root', 'auth-empty',
    'uid-empty', 'key-empty', 'key-partial', 'key-complete', 'key-acl', 'complete'])
def test_exact_provisioning_prefixes_can_be_preserved_without_repair_or_data_reads(world, stage):
    if stage != 'absent':
        world.logs.mkdir(mode=0o755)
        world.roots.add(world.logs.stat().st_ino)
    if stage not in ('absent', 'logs-empty'):
        world.root(world.logs / 'stdout.log', size=0)
    if stage not in ('absent', 'logs-empty', 'logs-root-stdout'):
        world.root(world.logs / 'stderr.log', size=0)
        world.root(world.runtime, directory=True)
    if stage not in ('absent', 'logs-empty', 'logs-root-stdout', 'runtime-root'):
        world.auth.mkdir(mode=0o755)
        world.roots.add(world.auth.stat().st_ino)
    if stage not in ('absent', 'logs-empty', 'logs-root-stdout', 'runtime-root', 'auth-empty'):
        folder = world.root(world.auth / str(world.uid), directory=True)
        if stage != 'uid-empty':
            key = world.root(folder / 'owner.key', size=0 if stage == 'key-empty' else 13 if stage == 'key-partial' else 32)
            if stage in ('key-acl', 'complete'):
                world.acl[key.stat().st_ino] = 'key'
            if stage == 'complete':
                world.acl[folder.stat().st_ino] = 'folder'
    before = {str(p): (p.lstat().st_ino, p.lstat().st_mode, p.lstat().st_mtime_ns) for p in world.base.rglob('*')}
    assert native.validate_preserved('fixture', common_body=b'approved source') is True
    assert before == {str(p): (p.lstat().st_ino, p.lstat().st_mode, p.lstat().st_mtime_ns) for p in world.base.rglob('*')}


@pytest.mark.parametrize('fault', ['extra-root-owner', 'extra-key', 'linked-key', 'key-size', 'key-mode',
    'folder-write-acl', 'key-write-acl', 'folder-before-key-acl', 'folder-without-key',
    'partial-key-with-acl', 'stderr-only', 'linked-log', 'exposed-log', 'runtime-extra'])
def test_preservation_never_adopts_unknown_or_exposed_private_data(world, fault):
    folder, key = full_layout(world)
    if fault == 'extra-root-owner':
        (world.auth / 'other-owner').mkdir()
    elif fault == 'extra-key':
        (folder / 'unexpected').write_text('preserve')
    elif fault == 'linked-key':
        os.link(key, world.base / 'alias')
    elif fault == 'key-size':
        key.write_bytes(b'k' * 33)
    elif fault == 'key-mode':
        key.chmod(0o640)
    elif fault == 'folder-write-acl':
        world.acl[folder.stat().st_ino] = 'unknown'
    elif fault == 'key-write-acl':
        world.acl[key.stat().st_ino] = 'unknown'
    elif fault == 'folder-before-key-acl':
        del world.acl[key.stat().st_ino]
    elif fault == 'folder-without-key':
        key.unlink()
    elif fault == 'partial-key-with-acl':
        key.write_bytes(b'k')
    elif fault == 'stderr-only':
        (world.logs / 'stdout.log').unlink()
    elif fault == 'linked-log':
        os.link(world.logs / 'stdout.log', world.base / 'alias')
    elif fault == 'exposed-log':
        (world.logs / 'stderr.log').chmod(0o644)
    else:
        (world.runtime / 'unexpected').write_text('preserve')
    before = {str(p): p.lstat().st_ino for p in world.base.rglob('*')}
    with pytest.raises((ValueError, OSError)):
        native.validate_preserved('fixture', common_body=b'approved source')
    assert before == {str(p): p.lstat().st_ino for p in world.base.rglob('*')}


@pytest.mark.parametrize('stage', ['missing', 'empty', 'partial', 'complete-unpublished', 'key-acl', 'published'])
def test_verified_retry_completes_only_missing_preparation_without_rotating_key(world, monkeypatch, stage):
    folder, key = full_layout(world)
    original = b'kept-prefix' if stage == 'partial' else b'' if stage == 'empty' else key.read_bytes()
    if stage == 'missing':
        del world.acl[key.stat().st_ino]
        key.unlink()
        original = b''
    else:
        key.write_bytes(original)
        if stage not in ('key-acl', 'published'):
            del world.acl[key.stat().st_ino]
    if stage != 'published':
        del world.acl[folder.stat().st_ino]
    inode = key.stat().st_ino if key.exists() else None
    monkeypatch.setattr(native.os, 'urandom', lambda count: b'x' * count)
    entry = native.owner_entry('fixture')
    native._resume_private_preparation(entry, native.operations_helpers().custody)
    assert key.read_bytes() == original + b'x' * (32 - len(original))
    if inode is not None:
        assert key.stat().st_ino == inode
    assert world.acl[key.stat().st_ino] == 'key' and world.acl[folder.stat().st_ino] == 'folder'
    complete = key.read_bytes()
    native._resume_private_preparation(entry, native.operations_helpers().custody)
    assert key.read_bytes() == complete


def test_interrupted_key_append_retries_without_discarding_any_written_prefix(world, monkeypatch):
    folder, key = full_layout(world)
    del world.acl[key.stat().st_ino]
    del world.acl[folder.stat().st_ino]
    key.write_bytes(b'original-prefix')
    write, count = os.write, []
    def interrupted(fd, body):
        count.append(1)
        if len(count) == 1:
            return write(fd, body[:3])
        raise OSError('invented interrupted append')
    monkeypatch.setattr(native.os, 'write', interrupted)
    entry, custody = native.owner_entry('fixture'), native.operations_helpers().custody
    with pytest.raises(OSError):
        native._resume_private_preparation(entry, custody)
    partial = key.read_bytes()
    assert partial.startswith(b'original-prefix') and len(partial) == len(b'original-prefix') + 3
    monkeypatch.setattr(native.os, 'write', write)
    native._resume_private_preparation(entry, custody)
    assert len(key.read_bytes()) == 32 and key.read_bytes().startswith(partial)


def test_verified_retry_finishes_empty_root_created_runtime_and_logs_without_touching_data(world):
    world.logs.mkdir(mode=0o755)
    world.roots.add(world.logs.stat().st_ino)
    log = world.root(world.logs / 'stdout.log', size=0)
    world.root(world.runtime, directory=True)
    native._resume_private_preparation(native.owner_entry('fixture'), native.operations_helpers().custody)
    assert log.stat().st_ino not in world.roots and world.runtime.stat().st_ino not in world.roots
    assert log.read_bytes() == b'' and not (world.logs / 'stderr.log').exists()
