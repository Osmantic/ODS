#!/usr/bin/env python3
"""Retain models with same-filesystem rename, never mv's copy/delete fallback."""
import json
import os
from pathlib import Path
import stat
import sys


def paths(install):
    root = Path(install)
    if not root.is_absolute() or root == root.parent or root.is_symlink():
        raise ValueError('model cache install path must be absolute, non-symlink and non-root')
    # Match the installer's canonical target identity, including macOS /var
    # aliases. A changed ancestor on restore cannot match the custody receipt.
    root = root.resolve()
    if root == root.parent:
        raise ValueError('model cache install path cannot resolve to root')
    backup = root.with_name(root.name + '.models-backup')
    return root, root / 'data/models', backup


def directory(path):
    value = path.lstat()
    if not stat.S_ISDIR(value.st_mode):
        raise ValueError('model cache path is not a real directory: ' + str(path))
    return value


def preflight(install):
    root, source, backup = paths(install)
    legacy = Path.home() / '.ods-models-backup'
    if os.path.lexists(backup) or os.path.lexists(legacy):
        raise ValueError('existing model backup must be recovered first: ' + str(backup) + ' or ' + str(legacy))
    directory(root)
    if os.path.lexists(root / 'data'):
        directory(root / 'data')
    if os.path.lexists(source):
        original = directory(source)
        if original.st_dev != directory(root.parent).st_dev:
            raise ValueError('models are on a separate mount; same-filesystem preservation is unavailable')
        for parent in (source.parent, root.parent):
            if not os.access(parent, os.W_OK | os.X_OK):
                raise ValueError('model preservation parent is not writable/searchable: ' + str(parent))
    return root, source, backup


def preserve(install):
    root, source, backup = preflight(install)
    if not source.exists():
        return
    original = directory(source)
    # Exclusive private wrapper leaves both the bytes and their custody outside
    # the tree the uninstaller removes. Existing/dangling backups never win.
    backup.mkdir(mode=0o700)
    receipt = {'schemaVersion': 1, 'installRoot': str(root),
               'device': original.st_dev, 'inode': original.st_ino}
    with (backup / 'custody.json').open('x', encoding='utf-8') as handle:
        os.chmod(handle.name, 0o600)
        json.dump(receipt, handle)
        handle.flush()
        os.fsync(handle.fileno())
    # os.rename refuses EXDEV without copying even if a mount changes after
    # preflight. On error the original tree remains; keep the receipt for recovery.
    os.rename(source, backup / 'models')
    after = directory(backup / 'models')
    if (after.st_dev, after.st_ino) != (original.st_dev, original.st_ino):
        raise ValueError('retained model directory identity changed')
    print(backup)


def restore(install):
    root, destination, backup = paths(install)
    # A legacy backup has no per-install custody. Never guess ownership or copy
    # it across mounts; retain the old refusal/recovery boundary.
    if os.path.lexists(Path.home() / '.ods-models-backup'):
        raise ValueError('legacy ~/.ods-models-backup requires explicit recovery')
    if not os.path.lexists(backup):
        return
    info = directory(backup)
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('model backup owner or permissions changed')
    if {p.name for p in backup.iterdir()} != {'models', 'custody.json'}:
        raise ValueError('model backup inventory changed')
    marker = backup / 'custody.json'
    marker_info = marker.lstat()
    if (not stat.S_ISREG(marker_info.st_mode) or marker_info.st_nlink != 1
            or marker_info.st_size > 4096 or marker_info.st_uid != os.getuid()
            or marker_info.st_mode & 0o077):
        raise ValueError('invalid model custody receipt')
    receipt = json.loads(marker.read_text(encoding='utf-8'))
    if (not isinstance(receipt, dict) or type(receipt.get('schemaVersion')) is not int
            or any(type(receipt.get(key)) is not int for key in ('device', 'inode'))):
        raise ValueError('invalid model custody schema')
    retained = directory(backup / 'models')
    if receipt != {'schemaVersion': 1, 'installRoot': str(root),
                   'device': retained.st_dev, 'inode': retained.st_ino}:
        raise ValueError('retained model custody changed')
    directory(root)
    if os.path.lexists(destination):
        raise ValueError('model restore destination already exists')
    data = root / 'data'
    if not os.path.lexists(data):
        data.mkdir()
    parent = directory(data)
    if parent.st_dev != retained.st_dev:
        raise ValueError('model restore crossed filesystems')
    os.rename(backup / 'models', destination)
    marker.unlink()
    backup.rmdir()


if __name__ == '__main__':
    try:
        operation, install = sys.argv[1:]
        {'preflight': preflight, 'preserve': preserve, 'restore': restore}[operation](install)
    except (OSError, ValueError, KeyError) as error:
        print('Model cache preservation: ' + str(error), file=sys.stderr)
        sys.exit(1)
