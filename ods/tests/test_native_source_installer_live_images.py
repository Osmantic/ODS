"""Real protected reads and a prepared legacy journal; only Root metadata is modeled."""
import errno
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from test_native_source_installer_recovery import upgrade, binding, selection
from pixel_connections import custody as source
import pixel_access_bridge as bridge
import pixel_macos_custody as mac


@pytest.fixture
def images(tmp_path, monkeypatch):
    program, metadata, config = (tmp_path / name for name in ('program', 'metadata', 'config'))
    for name, path in (('NATIVE_PROGRAM', program), ('NATIVE_META', metadata),
                       ('NATIVE_RUNTIME_DESCRIPTOR', config / 'runtime.json'),
                       ('NATIVE_RUNTIME_MANIFEST', config / 'manifest.json')):
        monkeypatch.setattr(source, name, path)
    family = source.native_installer_source_family()
    inspection = frozenset(str(tmp_path / 'inspection' / Path(p).name) for p in upgrade.INSPECTION_ADDITIONS)
    integration = frozenset(str(tmp_path / 'integration' / Path(p).name) for p in upgrade.INTEGRATION_ADDITIONS)
    schedules = frozenset(str(program / Path(p).name) for p in upgrade.SCHEDULE_ADDITIONS)
    monkeypatch.setattr(source, '_native_installer_legacy_additions', lambda: (inspection, integration))
    monkeypatch.setattr(source, '_native_installer_schedule_additions', lambda: schedules)
    for name, paths in (('SOURCE_ADDITIONS', family), ('INSPECTION_ADDITIONS', inspection),
                        ('INTEGRATION_ADDITIONS', integration), ('SCHEDULE_ADDITIONS', schedules)):
        monkeypatch.setattr(upgrade, name, paths)
    receipt = str(metadata / 'source-runtime-installation.json')
    monkeypatch.setattr(upgrade, 'SOURCE_PRIVATE_RECEIPT', receipt)
    records = [dict(path=p, before=None, after=b'candidate code',
                    mode=0o600 if p == receipt else 0o644, gid=0)
               for p in sorted(family | inspection | integration | schedules)]
    for item in records:
        Path(item['path']).parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    current = tmp_path / 'existing-core'
    current.write_bytes(b'current code')
    current.chmod(0o644)
    records.append(dict(path=str(current), before=b'current code', after=b'new core', mode=0o644, gid=0))
    ancestors = {(s.st_dev, s.st_ino) for s in (p.stat() for p in tmp_path.parents)}
    rejected_acls = set()

    def root_metadata(fd, *, directory):
        info = os.fstat(fd)
        identity = (info.st_dev, info.st_ino)
        mode = info.st_mode & ~0o022 if directory and identity in ancestors else info.st_mode
        if (not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode))
                or mode & 0o022 or not directory and info.st_nlink != 1 or identity in rejected_acls):
            raise mac.CustodyError('fixture-root-metadata-refused')
        fields = {key: getattr(info, key) for key in dir(info) if key.startswith('st_')}
        return SimpleNamespace(**dict(fields, st_mode=mode, st_uid=0, st_gid=0))

    # protected_bytes/protected_directory and their FD/no-follow walk remain
    # production code. Root ownership/Apple ACLs are the explicit OS boundary.
    monkeypatch.setattr(mac, '_verify_fd', root_metadata)
    real_private = bridge.private_json
    monkeypatch.setattr(bridge, 'private_json', lambda p, _uid, maximum: real_private(p, os.getuid(), maximum))
    return SimpleNamespace(root=tmp_path, records=records, family=family, inspection=inspection,
        integration=integration, schedules=schedules, rejected_acls=rejected_acls)


@pytest.mark.parametrize('missing_parent', [False, True])
def test_prepared_legacy_journal_proves_real_missing_images_without_publication(images, missing_parent):
    n = images
    if missing_parent:
        (n.root / 'integration').rmdir()
        (n.root / 'program/pixel_connections').rmdir()
    addition = binding([row for row in n.records if row['path'] in n.family])
    value = upgrade.encode_recovery(n.records, source_addition=addition, **selection(n.records))
    assert value['schemaVersion'] == 5 and value['phase'] == 'prepared'
    path = n.root / 'runtime-upgrade.json'
    upgrade.RecoveryJournal.create(path, value)
    journal, records = upgrade.RecoveryJournal.load(path, **selection(n.records))
    before = path.read_bytes(), path.stat().st_ino
    # The real reader wraps ENOENT; the old FileNotFoundError fallback never ran.
    with pytest.raises(mac.CustodyError, match='^macos-custody-file-unavailable$'):
        mac.protected_bytes(records[0]['path'])
    source._native_installer_live_images(records)
    assert (path.read_bytes(), path.stat().st_ino) == before and journal.value == value
    assert all(not Path(row['path']).exists() for row in records if row['before'] is None)
    for row in records:
        Path(row['path']).parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        Path(row['path']).write_bytes(row['after'])
        Path(row['path']).chmod(row['mode'])
    source._native_installer_live_images(records)
    assert (path.read_bytes(), path.stat().st_ino) == before


@pytest.mark.parametrize('fault', ['unknown', 'existing-before', 'symlink', 'parent-symlink',
    'directory', 'hardlink', 'file-mode', 'parent-mode', 'file-acl', 'parent-acl', 'permission', 'io',
    'read-permission', 'read-io', 'body'])
def test_missing_image_proof_preserves_all_existing_custody_refusals(images, monkeypatch, fault):
    n = images
    row = next(r.copy() for r in n.records if r['path'] in n.family and '/pixel_connections/' in r['path'])
    path = Path(row['path'])
    if fault == 'unknown': row['path'] = str(n.root / 'unapproved-missing')
    elif fault == 'existing-before': row['before'] = b'previous code'
    elif fault == 'symlink': path.symlink_to(n.root / 'missing-target')
    elif fault == 'parent-symlink':
        target = path.parent.with_name('other-directory')
        path.parent.rename(target)
        path.parent.symlink_to(target, target_is_directory=True)
    elif fault == 'directory': path.mkdir()
    elif fault in ('hardlink', 'file-mode', 'file-acl', 'body'):
        path.write_bytes(b'unknown code' if fault == 'body' else row['after'])
        path.chmod(0o666 if fault == 'file-mode' else 0o644)
        if fault == 'hardlink': os.link(path, path.with_name('other-link'))
        elif fault == 'file-acl':
            info = path.stat()
            n.rejected_acls.add((info.st_dev, info.st_ino))
    elif fault == 'parent-mode': path.parent.chmod(0o777)
    elif fault == 'parent-acl':
        info = path.parent.stat()
        n.rejected_acls.add((info.st_dev, info.st_ino))
    elif fault in ('permission', 'io'):
        original = os.stat
        def denied(name, *args, **kwargs):
            if name == path.name and kwargs.get('dir_fd') is not None:
                raise OSError(errno.EACCES if fault == 'permission' else errno.EIO, 'synthetic leaf failure')
            return original(name, *args, **kwargs)
        monkeypatch.setattr(os, 'stat', denied)
    elif fault in ('read-permission', 'read-io'):
        path.write_bytes(row['after'])
        path.chmod(0o644)
        def unreadable(*_args):
            raise OSError(errno.EACCES if fault == 'read-permission' else errno.EIO, 'synthetic read failure')
        monkeypatch.setattr(os, 'read', unreadable)
    with pytest.raises((source.CustodyError, mac.CustodyError, OSError)) as failure:
        source._native_installer_live_images([row])
    if fault in ('permission', 'io'):
        assert isinstance(failure.value, OSError)
        assert failure.value.errno == (errno.EACCES if fault == 'permission' else errno.EIO)
    elif fault in ('read-permission', 'read-io'):
        assert type(failure.value) is mac.CustodyError
        assert str(failure.value) == 'macos-custody-file-unavailable'
