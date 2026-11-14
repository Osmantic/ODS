#!/usr/bin/env python3
"""Consistent SQLite payloads for the opt-in durable Talk backup pair.

Individual online snapshots, not an atomic snapshot of the two runtimes.
Only private backup/restore staging is modified. Stop services before restore.
"""
from __future__ import annotations

import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time

DATABASES = ('data/talk/conversations.sqlite3', 'data/hermes/state.db')
SIDECARS = ('-wal', '-shm', '-journal')
FIELD = 'talk_sqlite_snapshots'


def regular_file(root: Path, relative: str) -> Path:
    path = root / relative
    if any(part.is_symlink() for part in (path, *path.parents) if part != root.parent):
        raise ValueError('Talk backup/restore refuses symlinked database paths')
    if not path.is_file():
        raise ValueError('Talk backup/restore database is missing')
    return path


def connect(path: Path, *, immutable: bool = False) -> sqlite3.Connection:
    return sqlite3.connect(path.as_uri() + '?mode=ro' + ('&immutable=1' if immutable else ''), uri=True, timeout=5)


def integrity(path: Path) -> None:
    with closing(connect(path, immutable=True)) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('Talk backup/restore SQLite integrity check failed')


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def snapshot(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.talk-snapshot-', dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    deadline = time.monotonic() + 30

    def progress(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() > deadline:
            raise TimeoutError('Talk SQLite snapshot exceeded 30 seconds; retry after stopping services')

    try:
        with closing(connect(source)) as reader, closing(sqlite3.connect(temporary)) as writer:
            reader.backup(writer, pages=256, progress=progress, sleep=0.05)
            writer.execute('PRAGMA journal_mode=DELETE')
        integrity(temporary)
        shutil.copystat(source, temporary)
        if hasattr(os, 'geteuid') and os.geteuid() == 0:
            metadata = source.stat()
            os.chown(temporary, metadata.st_uid, metadata.st_gid)
        # These are copied files in the unpublished backup, never live WALs.
        for suffix in SIDECARS:
            destination.with_name(destination.name + suffix).unlink(missing_ok=True)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def check_roots(backup: Path) -> None:
    with closing(connect(backup / DATABASES[0], immutable=True)) as mapping:
        references = mapping.execute('SELECT DISTINCT root,tip FROM conversations WHERE root IS NOT NULL').fetchall()
    if not references:
        return
    native = regular_file(backup, DATABASES[1])
    with closing(connect(native, immutable=True)) as db:
        parents = dict(db.execute('SELECT id,parent_session_id FROM sessions'))
        for root, tip in references:
            if root not in parents:
                raise ValueError('Talk backup has a conversation root missing from the paired Hermes database')
            current = tip or root
            seen = set()
            reached_root = False
            while current is not None:
                if current in seen or current not in parents:
                    raise ValueError('Talk backup has a missing or cyclic Hermes compression-chain reference')
                seen.add(current)
                reached_root = reached_root or current == root
                current = parents[current]
            if not reached_root:
                raise ValueError('Talk backup tip does not descend from its retained root')


def capture(install: Path, backup: Path) -> None:
    if not (install / DATABASES[0]).exists():
        return  # No durable Talk state; preserve the existing backup contract.
    manifest_path = backup / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    snapshots = {}
    # Native root is persisted before ODS records it. Capture mappings first,
    # then native history, and refuse any missing roots in the resulting pair.
    for relative in DATABASES:
        if relative == DATABASES[1] and not (install / relative).exists():
            continue
        source = regular_file(install, relative)
        destination = backup / relative
        if destination.is_symlink():
            raise ValueError('Talk backup refuses symlinked staging database')
        snapshot(source, destination)
        snapshots[relative] = digest(destination)
    check_roots(backup)
    manifest[FIELD] = {'version': 1, 'consistency': 'individual-sqlite-snapshots', 'sha256': snapshots}
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')


def validate(backup: Path) -> dict[str, str]:
    manifest = json.loads((backup / 'manifest.json').read_text())
    metadata = manifest.get(FIELD)
    if metadata is None:
        if (backup / DATABASES[0]).exists():
            raise ValueError('Talk database lacks a consistent SQLite snapshot receipt; recreate the backup')
        return {}
    if not isinstance(metadata, dict) or metadata.get('version') != 1:
        raise ValueError('Unsupported Talk SQLite snapshot receipt')
    snapshots = metadata.get('sha256')
    if not isinstance(snapshots, dict) or DATABASES[0] not in snapshots or set(snapshots) - set(DATABASES):
        raise ValueError('Invalid Talk SQLite snapshot paths')
    for relative, expected in snapshots.items():
        path = regular_file(backup, relative)
        if digest(path) != expected or any(path.with_name(path.name + suffix).exists() for suffix in SIDECARS):
            raise ValueError('Talk SQLite snapshot digest or standalone-file contract failed')
        integrity(path)
    check_roots(backup)
    return snapshots


def prepare(backup: Path, relative_directory: str, staged: Path) -> None:
    for relative in validate(backup):
        if str(Path(relative).parent) != relative_directory:
            continue
        path = staged / Path(relative).name
        # Additive restore copied destination state first. Its old WAL must
        # never be replayed against the restored standalone database.
        for suffix in SIDECARS:
            path.with_name(path.name + suffix).unlink(missing_ok=True)
        integrity(regular_file(staged, path.name))
        # Companion removal changes the directory mtime; retain the archived
        # metadata expected by the existing transactional rsync verification.
        shutil.copystat(backup / relative_directory, staged)


def main() -> None:
    command = sys.argv[1]
    if command == 'capture':
        capture(Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve())
    elif command == 'validate':
        validate(Path(sys.argv[2]).resolve())
    elif command == 'prepare':
        prepare(Path(sys.argv[2]).resolve(), sys.argv[3], Path(sys.argv[4]).resolve())
    elif command == 'requires-stopped':
        if validate(Path(sys.argv[2]).resolve()):
            print('true')
    else:
        raise ValueError('Unknown Talk backup operation')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, sqlite3.Error, TimeoutError) as error:
        print(f'Talk backup/restore: {error}', file=sys.stderr)
        raise SystemExit(1)
