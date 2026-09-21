"""Fail-closed file custody for the forthcoming macOS service adapter.

This verifies deployment files, not a running process or its isolation. Callers
must still verify the loaded launchd job and qualify the host access controls.
"""
import ctypes
from contextlib import contextmanager
import errno
import hashlib
import os
import plistlib
import re
import stat
import sys
from xml.parsers.expat import ExpatError


class CustodyError(ValueError):
    pass


def verify_loaded_launchd_definition(raw, target, filename, expected):
    """Compare a launchctl print snapshot with a trusted deployment definition.

    This supplements on-disk custody: kickstart does not reload a changed plist.
    It proves neither process identity nor absence of surviving descendants.
    Unknown launchctl formatting fails closed; never parse untrusted output as
    a deployment specification.
    """
    def fail():
        raise CustodyError('launchd-loaded-definition-mismatch')

    if (not isinstance(raw, str) or not isinstance(expected, dict)
            or not isinstance(target, str) or not re.fullmatch(
                r'(?:system|gui/[0-9]+)/[A-Za-z0-9][A-Za-z0-9.-]{0,127}', target)):
        fail()
    lines = raw.splitlines()
    if not lines or lines[0] != target + ' = {' or lines[-1] != '}':
        fail()

    def scalar(name):
        values = re.findall(r'^\t' + re.escape(name) + r' = ([^\n]*)$', raw, re.M)
        if len(values) != 1:
            fail()
        return values[0]

    def block(name, *, optional=False):
        start = '\t' + name + ' = {'
        positions = [i for i, line in enumerate(lines) if line == start]
        if not positions and optional:
            return []
        if len(positions) != 1:
            fail()
        values = []
        for line in lines[positions[0] + 1:]:
            if line == '\t}':
                return values
            if not line.startswith('\t\t') or line.startswith('\t\t\t'):
                fail()
            values.append(line[2:])
        fail()

    args = expected.get('ProgramArguments')
    environment = expected.get('EnvironmentVariables', {})
    if (expected.get('Label') != target.split('/')[-1]
            or not isinstance(args, list) or not args
            or any(not isinstance(a, str) or not a or any(c in a for c in '\n\r\t') for a in args)
            or not isinstance(environment, dict)
            or any(not isinstance(k, str) or not isinstance(v, str)
                   or any(c in k + v for c in '\n\r\t') for k, v in environment.items())):
        fail()
    for key, value in {'path': os.fspath(filename),
                       'program': expected.get('Program', args[0]),
                       'working directory': expected.get('WorkingDirectory'),
                       'stdout path': expected.get('StandardOutPath'),
                       'stderr path': expected.get('StandardErrorPath')}.items():
        if not isinstance(value, str) or scalar(key) != value:
            fail()
    # env -i is an explicit process boundary: launchd still lists session
    # variables, but they are cleared before executing the approved arguments.
    clean_environment = args[:2] == ['/usr/bin/env', '-i'] and len(args) > 2
    if (block('arguments') != args
            or block('inherited environment', optional=True) and not clean_environment):
        fail()
    loaded = {}
    for line in block('environment'):
        key, separator, value = line.partition(' => ')
        if not separator or not key or key in loaded:
            fail()
        loaded[key] = value
    # launchd injects these independently of EnvironmentVariables.
    if loaded.pop('XPC_SERVICE_NAME', None) != expected['Label']:
        fail()
    rate = loaded.pop('OSLogRateLimit', None)
    if rate is not None and not rate.isdecimal():
        fail()
    if loaded != environment:
        fail()


def _require_no_acl(fd):
    if sys.platform != 'darwin':
        raise CustodyError('macos-custody-platform-required')
    libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    libc.acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
    libc.acl_get_fd_np.restype = ctypes.c_void_p
    libc.acl_valid.argtypes = [ctypes.c_void_p]
    libc.acl_valid.restype = ctypes.c_int
    libc.acl_get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                   ctypes.POINTER(ctypes.c_void_p)]
    libc.acl_get_entry.restype = ctypes.c_int
    libc.acl_free.argtypes = [ctypes.c_void_p]
    libc.acl_free.restype = ctypes.c_int
    ctypes.set_errno(0)
    acl = libc.acl_get_fd_np(fd, 0x100)  # ACL_TYPE_EXTENDED from sys/acl.h.
    if not acl:
        # Apple's filesec_get_property reports absent FILESEC_ACL as ENOENT.
        # This is a descriptor query, not a missing pathname. Other errors fail.
        if ctypes.get_errno() == errno.ENOENT:
            os.fstat(fd)
            return
        raise CustodyError('macos-custody-acl-unavailable')
    try:
        if libc.acl_valid(acl) != 0:
            raise CustodyError('macos-custody-acl-invalid')
        entry = ctypes.c_void_p()
        ctypes.set_errno(0)
        result = libc.acl_get_entry(acl, 0, ctypes.byref(entry))
        # Darwin returns 0 for an entry, -1/EINVAL for an empty valid ACL.
        # Reject even restrictive ACLs: deployment never needs custom entries.
        if result != -1 or ctypes.get_errno() != errno.EINVAL:
            raise CustodyError('macos-custody-acl-present')
    finally:
        libc.acl_free(acl)


def _verify_fd(fd, *, directory):
    info = os.fstat(fd)
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
            or not directory and info.st_nlink != 1):
        raise CustodyError('macos-root-custody-required')
    _require_no_acl(fd)
    return info


@contextmanager
def protected_directory(filename, *, create=False):
    """Open a canonical root-owned directory, optionally creating missing parts.

    Every component is checked before descending. Creation never changes an
    existing directory's ownership, mode or ACL to make it appear trusted.
    """
    value = os.fspath(filename)
    if (not isinstance(value, str) or not value.startswith('/') or value == '/'
            or any(ord(c) < 32 for c in value)
            or any(part in ('', '.', '..') for part in value.split('/')[1:])):
        raise CustodyError('macos-custody-path-invalid')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open('/', flags)
    try:
        _verify_fd(fd, directory=True)
        for part in value.split('/')[1:]:
            created = False
            if create:
                try:
                    os.mkdir(part, mode=0o755, dir_fd=fd)
                    created = True
                    os.fsync(fd)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            _verify_fd(fd, directory=True)
            if created:
                os.fchmod(fd, 0o755)
                os.fsync(fd)
        yield fd
    finally:
        os.close(fd)


def protected_tree_metadata(root):
    """Require root custody of every entry; content/link topology is separate.

    Internal links are allowed only to other entries under this verified tree.
    Target files/directories are checked independently, including their ACLs.
    This does not authorize any executable or verify manifest hashes.
    """
    from pathlib import Path
    root = Path(root)
    with protected_directory(root) as root_fd:
        def visit(directory):
            for name in os.listdir(directory):
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    if info.st_uid != 0 or info.st_nlink != 1:
                        raise CustodyError('macos-root-custody-required')
                    continue
                is_directory = stat.S_ISDIR(info.st_mode)
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                fd = os.open(name, flags | (os.O_DIRECTORY if is_directory else 0), dir_fd=directory)
                try:
                    _verify_fd(fd, directory=is_directory)
                    if is_directory:
                        visit(fd)
                finally:
                    os.close(fd)
        visit(root_fd)
        # Resolve only after all directories are root verified. The publication
        # caller holds the root deployment lock while checking this tree.
        for directory, folders, files in os.walk(root, followlinks=False):
            for name in folders + files:
                path = Path(directory) / name
                if path.is_symlink():
                    try:
                        target = path.resolve(strict=True)
                    except (OSError, RuntimeError):
                        raise CustodyError('macos-custody-link-invalid') from None
                    if os.readlink(path).startswith('/') or (target != root and root not in target.parents):
                        raise CustodyError('macos-custody-link-invalid')


def protected_bytes(filename, *, limit=1024 * 1024):
    """Read a bounded root-owned file through individually verified dirfds.

    No resolve(): that would hide symlinks before checking them. Walking from
    an open root prevents replacement of an unchecked intermediate directory.
    """
    value = os.fspath(filename)
    if (not isinstance(value, str) or not value.startswith('/') or '\0' in value
            or any(part in ('', '.', '..') for part in value.split('/')[1:])
            or type(limit) is not int or limit < 1):
        raise CustodyError('macos-custody-path-invalid')
    parts = value.split('/')[1:]
    opened = []
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        parent = os.open('/', flags | os.O_DIRECTORY)
        opened.append(parent)
        _verify_fd(parent, directory=True)
        for part in parts[:-1]:
            parent = os.open(part, flags | os.O_DIRECTORY, dir_fd=parent)
            opened.append(parent)
            _verify_fd(parent, directory=True)
        fd = os.open(parts[-1], flags, dir_fd=parent)
        opened.append(fd)
        before = _verify_fd(fd, directory=False)
        if before.st_size > limit:
            raise CustodyError('macos-custody-file-too-large')
        chunks, length = [], 0
        while length <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - length))
            if not chunk:
                break
            chunks.append(chunk)
            length += len(chunk)
        after = _verify_fd(fd, directory=False)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if identity(before) != identity(after):
            raise CustodyError('macos-custody-file-changed')
        if length > limit:
            raise CustodyError('macos-custody-file-too-large')
        return b''.join(chunks)
    except OSError:
        raise CustodyError('macos-custody-file-unavailable') from None
    finally:
        for fd in reversed(opened):
            os.close(fd)


def replace_protected_bytes(filename, *, expected, replacement, mode, gid=0):
    """Replace an existing protected file while the caller holds its transaction lock.

    No ownership repair, new target creation or symlink resolution is allowed.
    An fsync failure after rename remains an error: callers must inspect the
    target and use their durable rollback journal, not assume no write happened.
    """
    if (type(expected) is not bytes or type(replacement) is not bytes
            or max(len(expected), len(replacement)) > 8 * 1024 * 1024
            or type(mode) is not int or mode not in (0o600, 0o640, 0o644, 0o755)
            or type(gid) is not int or gid < 0):
        raise CustodyError('macos-custody-replacement-invalid')
    value = os.fspath(filename)
    if not isinstance(value, str) or '/' not in value:
        raise CustodyError('macos-custody-path-invalid')
    parent, name = value.rsplit('/', 1)
    if not name or name in ('.', '..') or any(ord(c) < 32 for c in name):
        raise CustodyError('macos-custody-path-invalid')
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    with protected_directory(parent) as directory:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        temporary = None
        try:
            before = _verify_fd(fd, directory=False)
            if stat.S_IMODE(before.st_mode) != mode or before.st_gid != gid:
                raise CustodyError('macos-custody-replacement-metadata-changed')
            chunks, length = [], 0
            while length <= len(expected):
                chunk = os.read(fd, min(65536, len(expected) + 1 - length))
                if not chunk:
                    break
                chunks.append(chunk)
                length += len(chunk)
            if b''.join(chunks) != expected or identity(os.fstat(fd)) != identity(before):
                raise CustodyError('macos-custody-replacement-source-changed')
            candidate = '.ods-replace-' + os.urandom(16).hex()
            out = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                          0o600, dir_fd=directory)
            temporary = candidate
            try:
                os.fchown(out, 0, gid)
                os.fchmod(out, mode)
                _verify_fd(out, directory=False)
                with os.fdopen(os.dup(out), 'wb') as stream:
                    stream.write(replacement)
                    stream.flush()
                os.fsync(out)
            finally:
                os.close(out)
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (identity(current) != identity(before) or identity(os.fstat(fd)) != identity(before)):
                raise CustodyError('macos-custody-replacement-source-changed')
            os.rename(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            temporary = None
            os.fsync(directory)
        finally:
            os.close(fd)
            if temporary is not None:
                os.unlink(temporary, dir_fd=directory)


def launchd_document_binding(filename, expected):
    """Pin a complete approved plist; never infer approval from its label alone.

    expected must come from the trusted deployment specification, not the
    candidate file. This does not prove executable/plugin/config file custody.
    """
    if not isinstance(expected, dict) or not expected:
        raise CustodyError('launchd-specification-required')
    body = protected_bytes(filename)
    try:
        document = plistlib.loads(body)
        # Canonical bytes also distinguish plist booleans from integer values.
        actual = plistlib.dumps(document, fmt=plistlib.FMT_BINARY, sort_keys=True)
        wanted = plistlib.dumps(expected, fmt=plistlib.FMT_BINARY, sort_keys=True)
    except (ValueError, TypeError, OverflowError, plistlib.InvalidFileException, ExpatError):
        raise CustodyError('launchd-document-invalid') from None
    if actual != wanted:
        raise CustodyError('launchd-document-changed')
    # Only installer-generated encodings are accepted, avoiding duplicate XML
    # keys or other parser ambiguities between plistlib and launchd.
    if body not in (wanted, plistlib.dumps(expected, sort_keys=True)):
        raise CustodyError('launchd-document-noncanonical')
    return {'schemaVersion': 1, 'path': os.fspath(filename),
            'sha256': hashlib.sha256(body).hexdigest()}
