"""Real filesystem replacement tests with root custody boundary simulated."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import pixel_macos_custody as custody


@pytest.fixture
def target(tmp_path, monkeypatch):
    @contextmanager
    def directory(path):
        assert Path(path) == tmp_path
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            yield fd
        finally:
            os.close(fd)
    def check(fd, *, directory):
        value = os.fstat(fd)
        assert not directory
        if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or value.st_mode & 0o022:
            raise custody.CustodyError('invalid-fixture-custody')
        return value
    monkeypatch.setattr(custody, 'protected_directory', directory)
    monkeypatch.setattr(custody, '_verify_fd', check)
    monkeypatch.setattr(custody.os, 'fchown', lambda *_: None)
    path = tmp_path / 'config'
    path.write_bytes(b'original')
    path.chmod(0o600)
    return path


def replace(path, **changes):
    args = dict(expected=b'original', replacement=b'updated', mode=0o600, gid=path.stat().st_gid)
    args.update(changes)
    custody.replace_protected_bytes(path, **args)


@pytest.mark.parametrize('mode', [0o600, 0o640, 0o644, 0o755])
def test_replacement_is_new_inode_and_preserves_mode(target, mode):
    target.chmod(mode)
    before = target.stat().st_ino
    replace(target, mode=mode)
    assert target.read_bytes() == b'updated'
    assert target.stat().st_ino != before
    assert stat.S_IMODE(target.stat().st_mode) == mode
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.parametrize('change', ['bytes', 'mode', 'gid', 'hardlink', 'symlink'])
def test_drift_never_overwrites_target(target, change):
    args = {}
    if change == 'bytes': target.write_bytes(b'changed')
    if change == 'mode': target.chmod(0o644)
    if change == 'gid': args['gid'] = target.stat().st_gid + 100
    if change == 'hardlink': os.link(target, target.with_name('other'))
    if change == 'symlink':
        other = target.with_name('other')
        target.rename(other)
        target.symlink_to(other)
    before = target.read_bytes()
    with pytest.raises((custody.CustodyError, OSError)):
        replace(target, **args)
    assert target.read_bytes() == before
    assert not list(target.parent.glob('.ods-replace-*'))


def test_write_fsync_failure_leaves_original_and_cleans_temporary(target, monkeypatch):
    def fail(_): raise OSError('synthetic disk failure')
    monkeypatch.setattr(custody.os, 'fsync', fail)
    with pytest.raises(OSError): replace(target)
    assert target.read_bytes() == b'original'
    assert list(target.parent.iterdir()) == [target]


def test_drift_during_staging_is_not_overwritten(target, monkeypatch):
    original_fsync = os.fsync
    def race(fd):
        original_fsync(fd)
        target.write_bytes(b'concurrent change')
    monkeypatch.setattr(custody.os, 'fsync', race)
    with pytest.raises(custody.CustodyError, match='source-changed'): replace(target)
    assert target.read_bytes() == b'concurrent change'
    assert list(target.parent.iterdir()) == [target]


def test_directory_fsync_failure_is_reported_after_replacement(target, monkeypatch):
    original_fsync = os.fsync
    def fail(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode): raise OSError('synthetic directory fsync failure')
        original_fsync(fd)
    monkeypatch.setattr(custody.os, 'fsync', fail)
    with pytest.raises(OSError): replace(target)
    assert target.read_bytes() == b'updated'
    assert list(target.parent.iterdir()) == [target]


@pytest.fixture
def inspection_target(target, monkeypatch):
    from types import SimpleNamespace
    target.unlink()
    monkeypatch.setattr(custody, 'INSPECTION_ADDITIONS', frozenset([str(target)]))
    verify = custody._verify_fd
    def root_group(fd, *, directory):
        value = os.fstat(fd) if directory else verify(fd, directory=False)
        if directory:
            assert stat.S_ISDIR(value.st_mode)
        fields = {key: getattr(value, key) for key in dir(value) if key.startswith('st_')}
        fields['st_gid'] = 0
        return SimpleNamespace(**fields)
    monkeypatch.setattr(custody, '_verify_fd', root_group)
    return target


def test_inspection_addition_roundtrip_uses_real_filesystem(inspection_target):
    path = inspection_target
    assert custody.protected_inspection_bytes(path) is None
    custody.replace_protected_inspection_bytes(path, expected=None, replacement=b'approved', mode=0o644)
    assert custody.protected_inspection_bytes(path) == b'approved'
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    custody.replace_protected_inspection_bytes(path, expected=b'approved', replacement=None, mode=0o644)
    assert custody.protected_inspection_bytes(path) is None
    assert list(path.parent.iterdir()) == []


@pytest.mark.parametrize('fault', ['foreign', 'symlink', 'existing', 'mode', 'gid'])
def test_inspection_creation_never_overwrites_unapproved_state(inspection_target, fault):
    path = inspection_target
    options = dict(expected=None, replacement=b'approved', mode=0o644)
    if fault == 'foreign': path = path.with_name('foreign')
    if fault == 'existing': path.write_bytes(b'other')
    if fault == 'symlink': path.symlink_to(path.with_name('missing'))
    if fault == 'mode': options['mode'] = 0o600
    if fault == 'gid': options['gid'] = 1
    with pytest.raises((custody.CustodyError, OSError)):
        custody.replace_protected_inspection_bytes(path, **options)
    if fault == 'existing': assert path.read_bytes() == b'other'
    elif fault == 'symlink': assert path.is_symlink()
    else: assert not path.exists()
    assert not list(path.parent.glob('.ods-inspection-add-*'))


@pytest.mark.parametrize('after_publication', [False, True])
def test_inspection_creation_fsync_failure_retains_recoverable_state(inspection_target, monkeypatch, after_publication):
    path = inspection_target
    real = os.fsync
    def fail(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode) == after_publication:
            raise OSError('synthetic fsync failure')
        real(fd)
    monkeypatch.setattr(custody.os, 'fsync', fail)
    with pytest.raises(OSError):
        custody.replace_protected_inspection_bytes(path, expected=None, replacement=b'approved', mode=0o644)
    assert custody.protected_inspection_bytes(path) == (b'approved' if after_publication else None)
    assert not list(path.parent.glob('.ods-inspection-add-*'))
