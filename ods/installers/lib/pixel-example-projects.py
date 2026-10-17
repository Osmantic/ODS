"""Add the finished example projects ODS ships to an owner's Portal workspace.

Elf of Doom is copied to Playground/elf-of-doom whenever Playground/elf-of-doom
is absent and this workspace has never received it (each example likewise to
Playground/<name>). A private record in Portal's state folder, outside the
workspace the agent's sandbox mounts (.ods-example-projects/<SHA-256 of the
workspace path>.json), lists the examples the workspace has received, so an
example the owner deleted or renamed is never added again; a fresh workspace
gets it on its first install or update. The record also lists the examples
ODS itself added: only those are the shipped example to Portal. Anything already of that name (a
folder, even empty, a file or a link) is ordinary owner content that ODS never
overwrites, merges into, chmods, deletes or treats as the example, unless it
holds exactly the pinned files and nothing else: that is ODS's own copy from a
run that stopped before its record was written. Portal's sidebar lists the
added folder with the other projects; nothing is printed about it. Run as the
non-root workspace owner after Portal is verified. Every shipped file's
SHA-256 is pinned below; when any source file differs, nothing is copied.

The command prints the outcome as JSON. Exit status 3 means the workspace
layout is one this helper never writes into (see LayoutRefused), which only
the owner can change; exit status 4 means something else stopped the copy.
Either way stdout then holds one sentence naming the cause.
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
# The records of received examples: one private folder in Portal's (OpenClaw's)
# state folder, which the agent's sandbox never mounts, with one file per
# workspace named by the SHA-256 of its path. Portal's plugin reads the same
# file (plugin/example-projects.mjs).
RECORD_DIRECTORY = '.ods-example-projects'
MAX_RECORD_BYTES = 4096
# Linux renameat2(2) RENAME_NOREPLACE and macOS renameatx_np(2) RENAME_EXCL.
RENAME_NOREPLACE = 0x1
RENAME_EXCL = 0x4
# Elf of Doom: written in ODS by Portal from natural-language prompts. The game
# files are exactly the bytes published at https://odsspark.com/game/.
EXAMPLES = {
    'elf-of-doom': {
        'README.md': 'a7d4e1a0d6ac4e3477d17b4f3de4a42ff4f3da998e36d0c9450d57875abe8944',
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
# Exit statuses of the command, after its one-sentence cause on stdout.
LAYOUT_REFUSED = 3
NOT_ADDED = 4


class LayoutRefused(ValueError):
    """The workspace layout is one this helper never writes into.

    A linked folder on the workspace or state path, a workspace, state folder
    or Playground that is not the owner's private folder, or a Playground that
    is a file or a link. Only the owner can change that, so rerunning cannot
    help.
    """


def _guidance():
    spec = importlib.util.spec_from_file_location(
        'ods_example_workspace_guidance', Path(__file__).resolve().with_name('pixel-workspace-guidance.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _owner_directory(path, what):
    """Open the workspace or state folder with the guidance migration's owner walk.

    That walk opens every component from / without following links, and
    requires an owner-owned folder that is not group- or other-writable.
    """
    try:
        return _guidance()._directory(path)
    except FileNotFoundError as error:
        # Not created yet, which is not a layout the owner chose. The walk
        # names only the missing component; name the whole path.
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(path)) from error
    except (ValueError, OSError) as error:
        if isinstance(error, OSError) and error.errno not in (errno.ELOOP, errno.ENOTDIR, errno.EACCES, errno.EPERM):
            raise
        raise LayoutRefused(
            f'the Portal {what} {path} is not a private folder of this user reached without links ({error})') from error


def _read_source(directory, expected):
    """Return the pinned files' bytes; any difference stops before a write.

    A missing, linked, unreadable or non-regular source file is ODS's own copy
    being incomplete, never a problem with the owner's workspace.
    """
    files = {}
    for name, digest in sorted(expected.items()):
        try:
            fd = os.open(str(directory / name), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except OSError as error:
            raise ValueError(f'example source {name} could not be read ({error.strerror})') from error
        # Checked on the raw descriptor: os.fdopen() refuses a folder outright.
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise ValueError(f'example source {name} must be a regular file')
        with os.fdopen(fd, 'rb') as stream:
            try:
                body = stream.read(MAX_BYTES + 1)
            except OSError as error:
                raise ValueError(f'example source {name} could not be read ({error.strerror})') from error
        if len(body) > MAX_BYTES or hashlib.sha256(body).hexdigest() != digest:
            raise ValueError(f'example source {name} does not match the shipped bytes')
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
    try:
        playground = os.open('Playground', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=workspace)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise LayoutRefused('Playground in the Portal workspace is a link or a file, not a folder') from error
        raise
    info = os.fstat(playground)
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        os.close(playground)
        raise LayoutRefused('Playground in the Portal workspace is not a private folder of this user')
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


def _unchanged_copy(playground, name, files):
    """Whether Playground/<name> holds exactly the pinned files and nothing else.

    Such a folder is ODS's own copy from a run that stopped (or could not write
    its record) after the folder was renamed into place. Any other content, a
    link or a file the owner does not own is not.
    """
    try:
        directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=playground)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR, errno.ENOENT, errno.EACCES):
            return False
        raise
    try:
        if os.fstat(directory).st_uid != os.getuid() or sorted(os.listdir(directory)) != sorted(files):
            return False
        for filename, body in files.items():
            try:
                fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            except OSError as error:
                if error.errno in (errno.ELOOP, errno.ENOENT, errno.EACCES):
                    return False
                raise
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                os.close(fd)
                return False
            with os.fdopen(fd, 'rb') as stream:
                if stream.read(len(body) + 1) != body:
                    return False
        return True
    finally:
        os.close(directory)


def _names(value):
    return isinstance(value, list) and all(isinstance(name, str) for name in value)


class RecordUnusable(Exception):
    """The record (or its folder) is not the owner's own private file of the expected shape."""


def record_name(workspace):
    """The record's file name for this workspace path: the path's SHA-256."""
    return hashlib.sha256(os.fsencode(str(workspace))).hexdigest() + '.json'


def _records(state, create=False):
    """The private record folder in the state folder, or None when it is absent.

    It must be the owner's own real folder that nobody else can read or
    write; anything else at that name raises RecordUnusable.
    """
    if create:
        try:
            os.mkdir(RECORD_DIRECTORY, 0o700, dir_fd=state)
        except FileExistsError:
            pass
    try:
        records = os.open(RECORD_DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=state)
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise RecordUnusable() from error
        raise
    info = os.fstat(records)
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        os.close(records)
        raise RecordUnusable()
    return records


def _received(records, workspace):
    """The record's (received, added) example names; RecordUnusable when it cannot be trusted.

    received lists every example this workspace has received; added lists the
    ones ODS itself added, which Portal treats as the shipped example. No
    record means none. A record that is not the owner's own single regular
    file of the expected shape, for this workspace, is unusable: it is never
    trusted or replaced, and no example is added while it is there.
    """
    if records is None:
        return frozenset(), frozenset()
    try:
        fd = os.open(record_name(workspace), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=records)
    except FileNotFoundError:
        return frozenset(), frozenset()
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise RecordUnusable() from error  # A link where the record belongs.
        raise
    info = os.fstat(fd)
    # Checked on the raw descriptor: os.fdopen() refuses a folder outright.
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        os.close(fd)
        raise RecordUnusable()
    with os.fdopen(fd, 'rb') as stream:
        body = stream.read(MAX_RECORD_BYTES + 1)
    try:
        value = json.loads(body.decode('utf-8')) if len(body) <= MAX_RECORD_BYTES else None
    except (UnicodeDecodeError, ValueError) as error:
        raise RecordUnusable() from error
    if (not isinstance(value, dict) or set(value) != {'schemaVersion', 'workspace', 'received', 'added'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or value['workspace'] != str(workspace)
            or not _names(value['received']) or not _names(value['added'])
            or not set(value['added']) <= set(value['received'])):
        raise RecordUnusable()
    return frozenset(value['received']), frozenset(value['added'])


def _record(records, workspace, received, added):
    """Replace the record with these names: a private file renamed into place."""
    body = (json.dumps({'schemaVersion': 1, 'workspace': str(workspace), 'received': sorted(received),
                        'added': sorted(added)}, separators=(',', ':')) + '\n').encode()
    temporary = '.' + secrets.token_hex(8) + '.tmp'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=records)
    renamed = False
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        # rename(2) replaces a link or file at the record name itself, never
        # what a link points to; _received has just checked that entry.
        os.rename(temporary, record_name(workspace), src_dir_fd=records, dst_dir_fd=records)
        renamed = True
        os.fsync(records)
    finally:
        if not renamed:
            try:
                os.unlink(temporary, dir_fd=records)
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
        # Exactly the pinned files: this helper renamed it into place, then
        # stopped before the record was written.
        return 'added' if _unchanged_copy(playground, name, files) else 'present'
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


def _read_record(state, workspace):
    """The record's (received, added) names; RecordUnusable when it cannot be trusted or read.

    An error opening or reading the record folder or file in the state folder
    (EACCES, EIO) is logged and makes the record unusable: nothing is added,
    and nothing in the workspace is blamed for it.
    """
    try:
        records = _records(state)
        try:
            return _received(records, workspace)
        finally:
            if records is not None:
                os.close(records)
    except OSError as error:
        print(f'the example record in {RECORD_DIRECTORY} of the Portal state folder could not be read: {error}',
              file=sys.stderr)
        raise RecordUnusable() from error


def _write_record(state, workspace, received, added):
    records = _records(state, create=True)
    try:
        _record(records, workspace, received, added)
    finally:
        os.close(records)


def _deliver(root, projects, received):
    playground = _playground(root)
    try:
        _remove_interrupted_staging(playground)
        return {name: 'previously-received' if name in received else _add(playground, name, files)
                for name, files in projects.items()}
    finally:
        os.close(playground)


def add_projects(workspace, state_dir, source=SOURCE):
    """Add each shipped example this workspace has never received.

    workspace is the owner's Portal workspace and state_dir Portal's state
    folder that holds the record (OPENCLAW_STATE_DIR). Returns each example's
    outcome: 'added'; 'present' when something else of that name already
    exists; 'previously-received' when the record lists it (the owner may have
    deleted or renamed it since); or 'record-unusable' when the record cannot
    be trusted or read. 'added' and 'present' are recorded as received, so a later run
    never adds that example again, and only 'added' is recorded as added: an
    owner's own folder of that name never becomes the example. Raises
    LayoutRefused for a workspace layout this helper never writes into.
    """
    if os.geteuid() == 0:
        raise ValueError('add example projects as the non-root workspace owner')
    projects = {name: _read_source(Path(source) / name, expected) for name, expected in sorted(EXAMPLES.items())}
    workspace = Path(workspace)
    root = _owner_directory(workspace, 'workspace')
    try:
        state = _owner_directory(state_dir, 'state folder')
        try:
            try:
                received, added = _read_record(state, workspace)
            except RecordUnusable:
                return {name: 'record-unusable' for name in projects}
            outcome = _deliver(root, projects, received)
            delivered = {name for name, status in outcome.items() if status in ('added', 'present')}
            if delivered - received:
                try:
                    _write_record(state, workspace, received | delivered,
                                  added | {name for name, status in outcome.items() if status == 'added'})
                except (OSError, RecordUnusable) as error:
                    # The examples are in place but unrecorded: Portal treats
                    # one as ordinary owner content until a later run records
                    # it, which recognizes an unchanged copy as ODS's own.
                    print(f'could not record the added example projects: {type(error).__name__} {error}',
                          file=sys.stderr)
            return outcome
        finally:
            os.close(state)
    finally:
        os.close(root)


def _cause(error):
    """One sentence naming why the copy stopped, for the installer's warning."""
    if isinstance(error, LayoutRefused):
        return f'ODS does not write into this workspace layout: {error}.'
    if isinstance(error, ValueError) and 'example source' in str(error):
        return ("ODS's own copy of the game files is damaged or incomplete. "
                'Download ODS again, then rerun the installer.')
    if isinstance(error, FileNotFoundError):
        return (f'the Portal folder {error.filename} was not found. '
                'Rerun the installer so it sets Portal up again.')
    if isinstance(error, OSError):
        reason = error.strerror or type(error).__name__
        return (f'a file could not be written in the Portal workspace ({reason}). '
                "Free some disk space or fix that folder's permissions, then rerun the installer.")
    return f'the copy stopped ({error}).'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, help="the owner's absolute Portal workspace path")
    parser.add_argument('--state-dir', required=True, help="Portal's absolute state folder (OPENCLAW_STATE_DIR)")
    args = parser.parse_args()
    try:
        outcome = add_projects(args.workspace, args.state_dir, SOURCE)
    except (ValueError, OSError) as error:
        # The installer logs this line and shows, or only logs, the cause.
        print(f'example projects not added: {type(error).__name__}: {error}', file=sys.stderr)
        print(_cause(error))
        return LAYOUT_REFUSED if isinstance(error, LayoutRefused) else NOT_ADDED
    print(json.dumps(outcome, sort_keys=True, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
