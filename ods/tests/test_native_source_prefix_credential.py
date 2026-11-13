"""The administrative helper reads an owner credential using Root's digest pin."""
import hashlib
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

if sys.platform == 'win32':
    pytest.skip('POSIX owner-file credential contract', allow_module_level=True)

SPEC = importlib.util.spec_from_file_location('prefix_credential',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-source-prefix.py')
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


@pytest.fixture
def credential(tmp_path, monkeypatch):
    uid = os.getuid() or 65534
    path = tmp_path / 'owner-key'
    raw = b'invented-local-owner-credential-0123456789'
    path.write_bytes(raw)
    path.chmod(0o600)
    if os.getuid() == 0:
        os.chown(path, uid, -1)
    monkeypatch.setattr(p.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_uid=uid))
    settings = {'owner': 'fixture-owner', 'edge_owner_key_sha256': hashlib.sha256(raw).hexdigest()}
    return path, raw, settings, uid


def test_owner_private_key_matches_protected_pin_without_changing_file(credential):
    path, raw, settings, uid = credential
    before = path.stat()
    assert p.owner_relay_key(settings, path) == raw.decode('ascii')
    after = path.stat()
    assert (before.st_dev, before.st_ino, before.st_uid, before.st_mode, before.st_mtime_ns) == (
        after.st_dev, after.st_ino, after.st_uid, after.st_mode, after.st_mtime_ns)
    assert path.read_bytes() == raw and after.st_uid == uid


@pytest.mark.parametrize('case', ['owner', 'root-owner', 'permissions', 'hardlink', 'symlink',
    'missing-pin', 'changed-pin', 'non-ascii', 'whitespace', 'oversize', 'short'])
def test_changed_owner_credential_is_refused_without_repair(credential, monkeypatch, case):
    path, raw, settings, uid = credential
    if case in ('owner', 'root-owner'):
        monkeypatch.setattr(p.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_uid=0 if case == 'root-owner' else uid + 1))
    elif case == 'permissions':
        path.chmod(0o640)
    elif case == 'hardlink':
        os.link(path, path.with_name('linked-key'))
    elif case == 'symlink':
        target = path.with_name('original-key')
        path.rename(target)
        path.symlink_to(target)
    elif case == 'missing-pin':
        settings.pop('edge_owner_key_sha256')
    elif case == 'changed-pin':
        settings['edge_owner_key_sha256'] = '0' * 64
    else:
        changed = {'non-ascii': b'\xff' * 40, 'whitespace': raw + b'\n',
                   'oversize': b'x' * 4097, 'short': b'x' * 31}[case]
        path.write_bytes(changed)
        settings['edge_owner_key_sha256'] = hashlib.sha256(changed).hexdigest()
    before = path.stat()
    data = path.read_bytes()
    with pytest.raises((p.PrefixError, OSError)):
        p.owner_relay_key(settings, path)
    assert path.read_bytes() == data
    after = path.stat()
    assert (before.st_ino, before.st_uid, before.st_mode) == (after.st_ino, after.st_uid, after.st_mode)


def test_metadata_change_during_fd_read_is_not_authority(credential, monkeypatch):
    path, raw, settings, uid = credential
    original = os.fstat
    calls = []
    def changed(fd):
        info = original(fd)
        calls.append(fd)
        if len(calls) == 2:
            values = {name: getattr(info, name) for name in dir(info) if name.startswith('st_')}
            values['st_ctime_ns'] += 1
            return SimpleNamespace(**values)
        return info
    monkeypatch.setattr(p.os, 'fstat', changed)
    with pytest.raises(p.PrefixError, match='credential-changed'):
        p.owner_relay_key(settings, path)
    assert path.read_bytes() == raw
