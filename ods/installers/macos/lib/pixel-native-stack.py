"""Retain native Pixel Compose selection when a management cache is rebuilt.

Owner-side receipts select fixed Compose fragments only. They are not evidence
of protected runtime custody or current service health.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys


SPEC = importlib.util.spec_from_file_location('native_stack_install',
    Path(__file__).with_name('pixel-native-install.py'))
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)
MIGRATION_STORAGE = 'data/pixel-native/preparation/storage.compose.json'


def read_record(path):
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
            raise ValueError('invalid-native-selection-record')
        value = json.loads(stream.read(2 * 1024 * 1024 + 1))
    if not isinstance(value, dict):
        raise ValueError('invalid-native-selection-record')
    return value


def resolve_files(install_dir, files):
    install_dir = Path(install_dir).resolve(strict=True)
    preparation = install_dir / 'data/pixel-native/preparation'
    activation = preparation / 'activation.json'
    if not os.path.lexists(activation):
        return list(files)
    record = read_record(activation)
    prepared = read_record(preparation / 'preparation.json')
    migration = prepared.get('kind') == 'legacy-native'
    if migration:
        selection_valid = (prepared.get('phase') == 'awaiting-joint-activation'
            and prepared.get('installDir') == str(install_dir)
            and bool(re.fullmatch('[a-f0-9]{64}', str(prepared.get('currentDigest', '')))))
    else:
        selection_valid = (prepared.get('phase') == 'awaiting-protected-activation'
            and prepared.get('home') == str(install_dir / 'data/pixel-native/home'))
    if (record.get('status') != 'ready' or record.get('phase') != 'services-ready'
            or prepared.get('status') != 'prepared'
            or not selection_valid
            or any(not record.get(key) or record.get(key) != prepared.get(key)
                   for key in ('runtimeDigest', 'serviceDigest'))):
        raise ValueError('native-installation-needs-recovery')
    fragments = list(installer.FRAGMENTS)
    if migration:
        storage_path = install_dir / MIGRATION_STORAGE
        storage = read_record(storage_path)
        # Canonical serialization binds the approved external-volume selection.
        digest = hashlib.sha256(json.dumps(storage, sort_keys=True,
            separators=(',', ':')).encode()).hexdigest()
        if (prepared.get('storageDigest') != digest or record.get('storageDigest') != digest
                or set(storage) != {'volumes'} or type(storage['volumes']) is not dict
                or set(storage['volumes']) != {'pixel-native-runtime', 'pixel-native-previews',
                    'pixel-native-preview-runtime', 'pixel-transition-state'}
                or any(type(value) is not dict or set(value) != {'external', 'name'}
                    or value['external'] is not True
                    or not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]*', str(value['name']))
                    for value in storage['volumes'].values())):
            raise ValueError('native-migration-storage-needs-recovery')
        fragments.append(MIGRATION_STORAGE)
    for relative in fragments:
        path = install_dir / relative
        if not path.is_file() or path.resolve(strict=True) != path:
            raise ValueError('installed-native-compose-fragment-required')
    # Match absolute and relative spellings so a stale cache cannot duplicate or
    # reverse the shared Edge/native override order.
    required = {install_dir / relative for relative in fragments}
    # Only a completed migration supersedes the legacy VM routing override.
    # Keep the old file on disk so recovery can still use its original stack.
    if migration:
        required.add(install_dir / 'extensions/services/pixel-vm-link/compose.yaml')
    result = []
    for value in files:
        path = Path(value)
        absolute = path if path.is_absolute() else install_dir / path
        if absolute.resolve(strict=False) not in required:
            result.append(value)
    return result + fragments


def resolve_flags(install_dir, flags):
    tokens = shlex.split(flags)
    if len(tokens) % 2 or any(token != '-f' for token in tokens[::2]):
        raise ValueError('compose-file-flags-required')
    files = resolve_files(install_dir, tokens[1::2])
    # The shell CLI currently consumes unquoted word-split flags. Refuse inputs
    # it cannot represent faithfully, rather than printing misleading quoting.
    if any(not value or any(char.isspace() or char in '*?[]' for char in value) for value in files):
        raise ValueError('compose-flags-require-shell-array-support')
    return ' '.join('-f ' + value for value in files)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', required=True)
    parser.add_argument('--flags', required=True)
    args = parser.parse_args()
    try:
        print(resolve_flags(args.install_dir, args.flags))
    except (ValueError, OSError):
        print('Native Pixel Compose selection needs review; keep its receipts and configuration intact.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
