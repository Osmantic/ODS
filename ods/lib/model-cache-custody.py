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


# Hugging Face imports are described by data/model-imports.json. Without it a
# retained imported GGUF comes back as an unregistered file, so the registry
# travels with the models it describes.
IMPORTS = 'model-imports.json'
IMPORTS_LIMIT = 8 * 1024 * 1024


def import_registry(path):
    """The lstat of a retainable import registry: one regular file, bounded."""
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1 or value.st_size > IMPORTS_LIMIT:
        raise ValueError('model import registry is not a single regular file under 8 MiB: ' + str(path))
    return value


def preflight(install):
    root, source, backup = paths(install)
    legacy = Path.home() / '.ods-models-backup'
    # A later install does not automatically restore a standalone backup.
    # Name the existing path and recovery choices before any mutation (#7425).
    for existing in (backup, legacy):
        if os.path.lexists(existing):
            raise ValueError(
                'A model backup already exists at ' + str(existing) + '. An earlier --keep-models '
                'uninstall may have left it. A later install does not automatically restore it: '
                'check it, recover needed models into ' + str(source) + ' without overwriting existing '
                'files, verify the recovered models, then move the backup aside (or delete it) '
                'and rerun. Or rerun without --keep-models, and the models are removed with the install.')
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
        registry = source.parent / IMPORTS
        if os.path.lexists(registry) and import_registry(registry).st_dev != original.st_dev:
            raise ValueError('the model import registry is on a separate mount from the models')
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
    registry = source.parent / IMPORTS
    if os.path.lexists(registry):
        import_registry(registry)
        os.rename(registry, backup / IMPORTS)
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
    inventory = {p.name for p in backup.iterdir()}
    if inventory not in ({'models', 'custody.json'}, {'models', 'custody.json', IMPORTS}):
        raise ValueError('model backup inventory changed')
    registry = backup / IMPORTS
    if IMPORTS in inventory:
        import_registry(registry)
        if os.path.lexists(root / 'data' / IMPORTS):
            raise ValueError('model import registry restore destination already exists')
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
    if IMPORTS in inventory:
        os.rename(registry, root / 'data' / IMPORTS)
    marker.unlink()
    backup.rmdir()


if __name__ == '__main__':
    try:
        operation, install = sys.argv[1:]
        {'preflight': preflight, 'preserve': preserve, 'restore': restore}[operation](install)
    except (OSError, ValueError, KeyError) as error:
        print('Model cache preservation: ' + str(error), file=sys.stderr)
        sys.exit(1)
