"""Add the finished example projects ODS ships to an owner's Portal workspace.

Elf of Doom is copied to Playground/elf-of-doom whenever Playground/elf-of-doom
is absent and this workspace has never received it (each example likewise to
Playground/<name>). A hidden record in the workspace, .ods-examples.json, lists
the examples it has received, so an example the owner deleted or renamed is
never added again; a fresh workspace gets it on its first install or update.
Anything already of that name (a folder, even empty, a file or a link) is
ordinary owner content that ODS never overwrites, merges into, chmods or
deletes. Portal's sidebar lists the folder with the other projects; nothing is
printed about it. Run as the non-root workspace owner after Portal is verified.
Every shipped file's SHA-256 is pinned below; when any source file differs,
nothing is copied.
"""
import argparse
import ctypes
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time

SOURCE = Path(__file__).resolve().parents[2] / 'extensions/services/pixel-agent/example-projects'
MAX_BYTES = 4 * 1024 * 1024
# Hidden: Portal's preview and project routing skip names that start with '.'.
STAGING_PREFIX = '.ods-example-'
STAGING_NAME = re.compile(re.escape(STAGING_PREFIX) + r'[0-9a-f]{16}')
# A copy takes seconds; an older staging folder was left by an interrupted run.
STALE_STAGING_SECONDS = 3600
# The examples this workspace has received, at its root. Hidden like Portal's
# own .ods-projects metadata, so Portal's preview and sidebar never show it.
RECORD = '.ods-examples.json'
MAX_RECORD_BYTES = 4096
# Linux renameat2(2) RENAME_NOREPLACE and macOS renameatx_np(2) RENAME_EXCL.
RENAME_NOREPLACE = 0x1
RENAME_EXCL = 0x4
# Elf of Doom: written in ODS by Portal from natural-language prompts. The game
# files are exactly the bytes published at https://odsspark.com/game/.
EXAMPLES = {
    'elf-of-doom': {
        'README.md': 'c46837ef5b67f3d83c22c7251c3a9a5be8e3d750a38dad41c2f11d6f43cd9b1d',
        'chars.js': '161b08c8a331fef5f09ac45e6ade2cff62e03316ba7c6ac197adf8147af629db',
        'dungeon.js': '80dfc2e0517cb89bce4f445d75e6a85a4a9c2317893a6762d87666b5b136004c',
        'engine.js': '2f573a818c04b23554a95426bbe7a8a367780c9c583e03488fbe5e66e19dbedf',
        'index.html': 'af1c8b2b0a9f4e3ec4f5c6389078cf0b3a187a53badd451006ce24b737fd9906',
        'legacy.js': '0d33ded8670f4113f47baba1325ed52247305cbb9a0c92271aa7c8ef0c498165',
        'style.css': 'e063614db72fbc5c607bd4c79d4e2e967a0e5deceb3962f0a50f045e55197ee9',
        'ui.js': '5d0138ba4517d812359d4dd474f6febccf4dc703870412234a092cf8a04ef7f1',
        'world.js': 'b45864ccd2bed3d9413e571a167683f8b44cba29559bbee856a9f4d56ff8aff5',
    },
}
PINNED_NAMES = frozenset(name for files in EXAMPLES.values() for name in files)


def _owner_directory(path):
    """Open the workspace with the guidance migration's checked owner walk.

    That walk opens every component from / without following links, and
    requires an owner-owned workspace that is not group- or other-writable.
    """
    spec = importlib.util.spec_from_file_location(
        'ods_example_workspace_guidance', Path(__file__).resolve().with_name('pixel-workspace-guidance.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._directory(path)


def _read_source(directory, expected):
    """Return the pinned files' bytes; any difference stops before a write."""
    files = {}
    for name, digest in sorted(expected.items()):
        fd = os.open(str(directory / name), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('example source must be a regular file')
            body = stream.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES or hashlib.sha256(body).hexdigest() != digest:
            raise ValueError('example source does not match the shipped bytes')
        files[name] = body
    return files


def _exists(directory, name):
    """Any entry counts: a folder (even empty), a file or a link (even dangling)."""
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _playground(workspace):
    try:
        # Portal creates Playground the same way when it starts a project.
        os.mkdir('Playground', 0o700, dir_fd=workspace)
    except FileExistsError:
        pass
    playground = os.open('Playground', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=workspace)
    info = os.fstat(playground)
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        os.close(playground)
        raise ValueError('private owner Playground required')
    return playground


def _write(directory, name, body):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    with os.fdopen(fd, 'wb') as stream:
        # Never inherit source or umask modes; Portal's preview refuses
        # group- or other-writable files.
        os.fchmod(stream.fileno(), 0o600)
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def _discard(playground, staging, names):
    """Remove one staging folder and the pinned files written there."""
    directory = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=playground)
    try:
        for name in names:
            try:
                os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                pass  # The run stopped before it created this file.
    finally:
        os.close(directory)
    os.rmdir(staging, dir_fd=playground)


def _remove_interrupted_staging(playground):
    """Remove staging folders an interrupted earlier copy left behind.

    Only an owner-owned real folder named exactly like this helper's staging
    folders, unchanged for an hour (so never a copy still running), holding
    nothing but regular files with pinned example names, is removed. Anything
    else that merely looks similar is left alone, and so is one that cannot be
    inspected or removed: that is logged, never a reason to skip the copy.
    """
    for entry in os.listdir(playground):
        if not STAGING_NAME.fullmatch(entry):
            continue
        try:
            _remove_one_staging(playground, entry)
        except OSError as error:
            print(f'left interrupted staging folder {entry}: {error}', file=sys.stderr)


def _remove_one_staging(playground, entry):
    info = os.stat(entry, dir_fd=playground, follow_symlinks=False)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or time.time() - info.st_mtime < STALE_STAGING_SECONDS):
        return
    directory = os.open(entry, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=playground)
    try:
        names = os.listdir(directory)
        only_pinned = all(
            name in PINNED_NAMES and stat.S_ISREG(os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode)
            for name in names)
    finally:
        os.close(directory)
    if only_pinned:
        _discard(playground, entry, names)


def _received(workspace):
    """The example names this workspace has received, or None for an unusable record.

    No record means none. A record that is not the owner's own single regular
    file of the expected shape is unusable: it is never trusted or replaced,
    and no example is added while it is there.
    """
    try:
        fd = os.open(RECORD, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=workspace)
    except FileNotFoundError:
        return frozenset()
    except OSError as error:
        if error.errno == errno.ELOOP:
            return None  # A link where the record belongs.
        raise
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            return None
        body = stream.read(MAX_RECORD_BYTES + 1)
    try:
        value = json.loads(body.decode('utf-8')) if len(body) <= MAX_RECORD_BYTES else None
    except (UnicodeDecodeError, ValueError):
        return None
    received = value.get('received') if isinstance(value, dict) else None
    if (not isinstance(value, dict) or set(value) != {'schemaVersion', 'received'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or not isinstance(received, list) or not all(isinstance(name, str) for name in received)):
        return None
    return frozenset(received)


def _record(workspace, names):
    """Replace the record with these names: a private file renamed into place."""
    body = (json.dumps({'schemaVersion': 1, 'received': sorted(names)}, separators=(',', ':')) + '\n').encode()
    temporary = '.ods-examples-' + secrets.token_hex(8) + '.tmp'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=workspace)
    renamed = False
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        # rename(2) replaces a link or file at the record name itself, never
        # what a link points to; _received has just checked that entry.
        os.rename(temporary, RECORD, src_dir_fd=workspace, dst_dir_fd=workspace)
        renamed = True
        os.fsync(workspace)
    finally:
        if not renamed:
            try:
                os.unlink(temporary, dir_fd=workspace)
            except FileNotFoundError:
                pass  # It was never created.


def _exclusive_rename(directory, source, target):
    """Rename source to target in one folder only while target does not exist.

    Linux renameat2(RENAME_NOREPLACE) and macOS renameatx_np(RENAME_EXCL) refuse
    an existing target atomically, even an empty folder, which a plain rename
    would replace. Returns False when the target exists. Raises OSError with
    ENOSYS or EINVAL when this system or filesystem has no exclusive rename;
    nothing was renamed then.
    """
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith('linux'):
        call, flag = getattr(libc, 'renameat2', None), RENAME_NOREPLACE
    elif sys.platform == 'darwin':
        call, flag = getattr(libc, 'renameatx_np', None), RENAME_EXCL
    else:
        call, flag = None, 0
    if call is None:
        raise OSError(errno.ENOSYS, 'exclusive rename is unavailable')
    call.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    call.restype = ctypes.c_int
    if call(directory, os.fsencode(source), directory, os.fsencode(target), flag) == 0:
        return True
    code = ctypes.get_errno()
    if code == errno.EEXIST:
        return False
    raise OSError(code, os.strerror(code))


def _rename_into_place(playground, staging, name):
    try:
        return _exclusive_rename(playground, staging, name)
    except OSError as error:
        if error.errno not in (errno.ENOSYS, errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP):
            raise
    # No exclusive rename here (an old C library or filesystem). rename(2)
    # still refuses a non-empty folder, a file or a link; only an empty folder
    # created after this last check could be replaced.
    if _exists(playground, name):
        return False
    os.rename(staging, name, src_dir_fd=playground, dst_dir_fd=playground)
    return True


def _add(playground, name, files):
    if _exists(playground, name):
        return 'present'
    # Stage under a hidden sibling, which Portal's preview and project routing
    # ignore, so an interrupted copy never leaves a partial Playground/<name>.
    staging = STAGING_PREFIX + secrets.token_hex(8)
    os.mkdir(staging, 0o700, dir_fd=playground)
    renamed = False
    try:
        directory = os.open(staging, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=playground)
        try:
            os.fchmod(directory, 0o700)
            staged = os.fstat(directory)
            for filename, body in sorted(files.items()):
                _write(directory, filename, body)
            os.fsync(directory)
        finally:
            os.close(directory)
        if _exists(playground, name) or not _rename_into_place(playground, staging, name):
            return 'present'
        renamed = True
        os.fsync(playground)
        # The project folder must be the folder this run filled, nothing else.
        placed = os.stat(name, dir_fd=playground, follow_symlinks=False)
        if not stat.S_ISDIR(placed.st_mode) or (placed.st_dev, placed.st_ino) != (staged.st_dev, staged.st_ino):
            raise OSError(errno.EEXIST, 'example folder changed while it was being added')
        return 'added'
    finally:
        if not renamed:
            _discard(playground, staging, files)


def add_projects(workspace, source=SOURCE):
    """Add each shipped example this workspace has never received.

    Returns each example's outcome: 'added'; 'present' when something of that
    name already exists; 'previously-received' when the record lists it (the
    owner may have deleted or renamed it since); or 'record-unusable' when the
    record cannot be trusted. 'added' and 'present' are recorded, so a later
    run never adds that example again.
    """
    if os.geteuid() == 0:
        raise ValueError('add example projects as the non-root workspace owner')
    projects = {name: _read_source(Path(source) / name, expected) for name, expected in sorted(EXAMPLES.items())}
    root = _owner_directory(workspace)
    try:
        received = _received(root)
        if received is None:
            return {name: 'record-unusable' for name in projects}
        playground = _playground(root)
        try:
            _remove_interrupted_staging(playground)
            outcome = {name: 'previously-received' if name in received else _add(playground, name, files)
                       for name, files in projects.items()}
        finally:
            os.close(playground)
        delivered = {name for name, status in outcome.items() if status in ('added', 'present')}
        if delivered - received:
            try:
                _record(root, received | delivered)
            except OSError as error:
                # The examples are in place; only a later re-add after the
                # owner deletes one is no longer prevented.
                print(f'could not record the added example projects: {error}', file=sys.stderr)
        return outcome
    finally:
        os.close(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, help="the owner's absolute Portal workspace path")
    args = parser.parse_args()
    print(json.dumps(add_projects(args.workspace), sort_keys=True, separators=(',', ':')))


if __name__ == '__main__':
    main()
