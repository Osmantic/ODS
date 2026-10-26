"""Descriptor-relative custody for the separately provisioned recovery package."""
import hashlib
import ctypes
import json
import os
from pathlib import Path
import stat


class Refused(RuntimeError):
    """A fixed diagnostic, never private source/configuration content."""


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def decode(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise Refused('duplicate-field')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs)


def leaf(name):
    if type(name) is not str or name in ('', '.', '..') or '/' in name or '\0' in name:
        raise Refused('invalid-leaf')
    return name


def identity(info):
    return (info.st_dev, info.st_ino)


class Directory:
    """Pin every ancestor; writes use only these descriptors, never a pathname."""
    def __init__(self, path, *, owner=0):
        self.path = Path(path)
        self.owner = owner
        self.handles = []
        if not self.path.is_absolute() or '..' in self.path.parts:
            raise Refused('invalid-directory')
        try:
            fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
            self.handles.append((fd, '/'))
            for name in self.path.parts[1:]:
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                self.handles.append((fd, name))
            self.validate()
        except BaseException:
            self.close()
            raise

    @property
    def fd(self):
        return self.handles[-1][0]

    def validate(self):
        for index, (fd, name) in enumerate(self.handles):
            info = os.fstat(fd)
            named = os.stat(name, dir_fd=self.handles[index - 1][0] if index else None, follow_symlinks=False)
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, self.owner)
                    or info.st_mode & 0o022 or identity(info) != identity(named)):
                raise Refused('directory-custody-changed')
        return os.fstat(self.fd)

    def stat(self, name):
        self.validate()
        return os.stat(leaf(name), dir_fd=self.fd, follow_symlinks=False)

    def read(self, name, *, uid=0, maximum=64 * 1024 * 1024, forbidden_mode=0o022):
        self.validate()
        fd = os.open(leaf(name), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=self.fd)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != uid or before.st_nlink != 1
                    or before.st_mode & forbidden_mode or before.st_size > maximum):
                raise Refused('file-custody-unavailable')
            with os.fdopen(os.dup(fd), 'rb') as stream:
                raw = stream.read(maximum + 1)
            after = os.fstat(fd)
            named = self.stat(name)
            if (len(raw) > maximum or identity(before) != identity(named)
                    or (before.st_mtime_ns, before.st_size, before.st_ctime_ns)
                    != (after.st_mtime_ns, after.st_size, after.st_ctime_ns)):
                raise Refused('file-changed-during-read')
            return raw
        finally:
            os.close(fd)

    def write(self, name, raw, *, mode=0o600, absent=False):
        if self.owner != 0:
            raise Refused('root-directory-required')
        self.validate()
        try:
            self.read(name)
            if absent:
                raise Refused('record-already-exists')
        except FileNotFoundError:
            pass
        temporary = '.recovery-write-' + os.urandom(16).hex()
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     mode, dir_fd=self.fd)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            self.validate()
            if absent:
                # Link/unlink publication leaves nlink=2 after a crash between
                # those calls. Atomic NOREPLACE keeps a durable claim readable
                # while still refusing every existing destination.
                libc = ctypes.CDLL(None, use_errno=True)
                if libc.renameat2(self.fd, os.fsencode(temporary), self.fd, os.fsencode(leaf(name)), 1):
                    raise OSError(ctypes.get_errno(), 'claim-publication-failed')
            else:
                os.replace(temporary, leaf(name), src_dir_fd=self.fd, dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except FileNotFoundError:
                pass

    def mkdir(self, name, *, mode=0o700):
        self.validate()
        if self.owner != 0:
            raise Refused('root-directory-required')
        os.mkdir(leaf(name), mode, dir_fd=self.fd)
        os.fsync(self.fd)
        return Directory(self.path / name)

    def unlink(self, name, expected):
        if self.read(name) != expected:
            raise Refused('record-changed')
        self.validate()
        os.unlink(leaf(name), dir_fd=self.fd)
        os.fsync(self.fd)

    def close(self):
        while self.handles:
            os.close(self.handles.pop()[0])

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def read(path, *, uid=0, maximum=64 * 1024 * 1024, forbidden_mode=0o022):
    path = Path(path)
    with Directory(path.parent, owner=uid) as directory:
        return directory.read(path.name, uid=uid, maximum=maximum, forbidden_mode=forbidden_mode)


def process_start(pid):
    try:
        return Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()[19]
    except FileNotFoundError:
        return None
