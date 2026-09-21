"""SIGKILL during real file replacement; root ownership and services excluded."""
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
import pixel_access_bridge as bridge
import pixel_macos_custody as custody

spec = importlib.util.spec_from_file_location('crash_upgrade', ROOT / 'installers/macos/lib/pixel-runtime-upgrade.py')
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)


@contextmanager
def owner_fixture(root):
    @contextmanager
    def directory(path):
        assert Path(path) == root
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: yield fd
        finally: os.close(fd)
    def check(fd, *, directory):
        value = os.fstat(fd)
        assert not directory
        if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or value.st_mode & 0o022:
            raise custody.CustodyError('invalid-fixture-custody')
        return value
    private_read = bridge.private_json
    with patch.object(custody, 'protected_directory', directory), \
         patch.object(custody, '_verify_fd', check), \
         patch.object(custody.os, 'fchown', lambda *args: None), \
         patch.object(bridge, 'private_json', lambda path, uid, maximum:
                      private_read(path, os.getuid(), maximum)):
        yield


def selection(root):
    return dict(current_digest='a' * 64, candidate_digest='b' * 64,
                allowed_paths={str(root / name) for name in ('first', 'second')})


def worker(root, checkpoint):
    records = [dict(path=str(root / name), mode=0o600, gid=os.getgid(),
                    before=b'original-' + name.encode(), after=b'updated-' + name.encode())
               for name in ('first', 'second')]
    for item in records:
        path = Path(item['path'])
        path.write_bytes(item['before'])
        path.chmod(0o600)
        item['gid'] = path.stat().st_gid
    def stop_at(name):
        if checkpoint == name:
            print(name, flush=True)
            while True: time.sleep(60)
    with owner_fixture(root):
        journal = upgrade.RecoveryJournal.create(root / 'runtime-upgrade.json',
            upgrade.encode_recovery(records, **selection(root)))
        stop_at('prepared')
        journal.phase('replacing-files')
        count = 0
        def replace(path, **kwargs):
            nonlocal count
            custody.replace_protected_bytes(path, **kwargs)
            count += 1
            stop_at('replaced-' + str(count))
        upgrade.replace_deployment_files(records, read=lambda path: Path(path).read_bytes(), replace=replace)
    raise RuntimeError('checkpoint not reached')


@pytest.mark.skipif(os.name != 'posix', reason='SIGKILL and POSIX filesystem test')
@pytest.mark.parametrize('checkpoint', ['prepared', 'replaced-1', 'replaced-2'])
@pytest.mark.parametrize('drift', [False, True])
def test_killed_updater_restores_only_known_snapshots(tmp_path, checkpoint, drift):
    process = subprocess.Popen([sys.executable, __file__, '--worker', str(tmp_path), checkpoint],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(20), 'worker failed to reach checkpoint'
            assert process.stdout.readline().strip() == checkpoint
        process.kill()
        process.communicate(timeout=10)
        assert process.returncode == -signal.SIGKILL
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
    with owner_fixture(tmp_path):
        journal, records = upgrade.RecoveryJournal.load(tmp_path / 'runtime-upgrade.json', **selection(tmp_path))
        before = {item['path']: Path(item['path']).read_bytes() for item in records}
        assert sum(value.startswith(b'updated') for value in before.values()) == {
            'prepared': 0, 'replaced-1': 1, 'replaced-2': 2}[checkpoint]
        if drift:
            Path(records[-1]['path']).write_bytes(b'owner-changed')
            snapshot = {item['path']: Path(item['path']).read_bytes() for item in records}
            with pytest.raises(upgrade.UpgradeError, match='rollback-file-drift'):
                upgrade.restore_deployment_files(records, read=lambda path: Path(path).read_bytes(),
                                                  replace=custody.replace_protected_bytes)
            assert snapshot == {path: Path(path).read_bytes() for path in snapshot}
            assert journal.path.exists()
        else:
            upgrade.restore_deployment_files(records, read=lambda path: Path(path).read_bytes(),
                                              replace=custody.replace_protected_bytes)
            def verify():
                for item in records:
                    assert Path(item['path']).read_bytes() == item['before']
                    assert stat.S_IMODE(Path(item['path']).stat().st_mode) == 0o600
            verify()
            journal.phase('restored')
            journal.finish(verify)
            assert not journal.path.exists()
            assert (tmp_path / ('runtime-upgrade-' + 'b' * 64 + '.completed.json')).exists()


if __name__ == '__main__' and sys.argv[1] == '--worker':
    worker(Path(sys.argv[2]), sys.argv[3])
