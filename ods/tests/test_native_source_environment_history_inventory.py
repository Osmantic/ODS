"""Owned Root Linux files, with only the physical Darwin ACL API simulated.

These are the inventory boundary controls, not an installer or launchd claim.
"""
import importlib.util
import os
from pathlib import Path
import sys

import pytest

if sys.platform != 'linux' or os.geteuid() != 0:
    pytest.skip('actual root-owned temporary Linux files required', allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
from pixel_connections import custody as c  # noqa: E402

spec = importlib.util.spec_from_file_location('history_inventory_publisher',
    ROOT / 'installers/macos/lib/pixel-native-source-broker.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(c, '_native_no_acl', lambda _fd: None)  # No physical Mac claim.
    jobs = tmp_path / 'jobs'
    jobs.mkdir(mode=0o700)
    for name in ('manifest.json', '.gate-before'):
        path = jobs / name
        path.write_bytes(b'{}\n')
        path.chmod(0o600)
    history = jobs / 'history'
    history.mkdir(mode=0o700)
    current = {'retained': {}, 'transaction': None,
        'history': {'directory': [history.stat().st_dev, history.stat().st_ino], 'count': 1234, 'pending': None}}
    fd = os.open(jobs, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield jobs, history, fd, current
    finally:
        os.close(fd)


def freeze(fd, current):
    proof = helper._environment_tree(c, fd, 60001, origin={'definitions': {}}, active=current)
    return {'schemaVersion': 2, 'old': {'directory': proof['directory'], 'jobs': proof}}


def test_large_history_is_opaque_and_same_inode_survives_archive_rename(inventory, monkeypatch):
    jobs, history, fd, current = inventory
    parent = history
    for index in range(10):
        parent = parent / str(index)
        parent.mkdir(mode=0o700)
    for index in range(4100):
        (parent / str(index)).touch(mode=0o600)
    record = freeze(fd, current)
    inode = [history.stat().st_dev, history.stat().st_ino]
    real_listdir = os.listdir
    def listdir(path):
        info = os.fstat(path) if isinstance(path, int) else os.stat(path)
        assert [info.st_dev, info.st_ino] != inode, 'old history is not scanned'
        return real_listdir(path)
    monkeypatch.setattr(os, 'listdir', listdir)
    assert helper._environment_history_matches(c, fd, 60001, record)
    archive = jobs.parent / 'retained'
    jobs.rename(archive)
    assert helper._environment_history_matches(c, fd, 60001, record)
    assert (archive / 'history/0/1/2/3/4/5/6/7/8/9/4099').is_file()


@pytest.mark.parametrize('change', ['unknown-live', 'history-inode', 'live-bytes', 'live-inode', 'history-mode'])
def test_history_opacity_does_not_relax_live_or_directory_identity(inventory, change):
    jobs, history, fd, current = inventory
    record = freeze(fd, current)
    if change == 'unknown-live':
        (jobs / 'foreign').touch(mode=0o600)
    elif change == 'history-inode':
        history.rename(jobs.parent / 'saved-history')
        history.mkdir(mode=0o700)
    elif change == 'live-bytes':
        (jobs / 'manifest.json').write_bytes(b'changed')
    elif change == 'live-inode':
        (jobs / 'manifest.json').rename(jobs.parent / 'saved-manifest')
        (jobs / 'manifest.json').write_bytes(b'{}\n')
        (jobs / 'manifest.json').chmod(0o600)
    else:
        history.chmod(0o755)
    before = {str(path): (path.stat().st_ino, path.read_bytes())
        for path in jobs.parent.rglob('*') if path.is_file()}
    try:
        accepted = helper._environment_history_matches(c, fd, 60001, record)
    except (ValueError, c.CustodyError):
        accepted = False
    assert not accepted
    assert before == {str(path): (path.stat().st_ino, path.read_bytes())
        for path in jobs.parent.rglob('*') if path.is_file()}


def test_legacy_receipt_still_uses_its_original_tree_digest(inventory):
    _jobs, history, fd, current = inventory
    # Legacy records retain the original full-tree fingerprint. They are not
    # silently reinterpreted as the newer opaque-history receipt.
    old = helper._environment_legacy_tree(c, fd, 60001)
    record = {'schemaVersion': 1, 'old': {'treeSha256': old}}
    assert helper._environment_history_matches(c, fd, 60001, record)
    (history / 'later').touch(mode=0o600)
    assert not helper._environment_history_matches(c, fd, 60001, record)
