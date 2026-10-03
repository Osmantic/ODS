"""Bounded, no-follow source snapshots for isolated project builds (candidate)."""
from __future__ import annotations

import hashlib
import io
import os
import stat
import tarfile


MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_FILES = 2048
SKIP = frozenset({".git", ".hg", ".svn", "node_modules", ".next", "dist", "out",
                  ".npmrc", ".yarnrc", ".yarnrc.yml", ".ssh", ".aws", ".npm", "ods-builds",
                  ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                  ".tox", ".nox", ".hypothesis", ".cache", ".uv", ".pip", ".pypirc",
                  "pip.conf", "pip.ini", "uv.toml"})
RESERVED = frozenset({".ods-python-env", ".ods-python-wheels"})


class UnsafeProjectSource(ValueError):
    pass


def _component(value):
    return (isinstance(value, str) and 0 < len(value.encode()) <= 255
            and value not in (".", "..") and not any(c in value for c in "/\\:")
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def snapshot_project(workspace: str, relative: str) -> dict:
    """Read a project below a trusted configured workspace; never follow links.

    Returned bytes are immutable inputs, not a host mount. The caller must bind
    this generation to approval/job identity and validate its package lock.
    """
    if not isinstance(relative, str) or len(relative) > 1024:
        raise UnsafeProjectSource("invalid project path")
    parts = relative.split("/")
    if len(parts) > 16 or not all(_component(part) for part in parts):
        raise UnsafeProjectSource("invalid project path")
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise UnsafeProjectSource("source snapshot requires a supported POSIX runtime")
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    root = os.open(workspace, flags | os.O_DIRECTORY)
    files, modes, omitted = {}, {}, []
    total = entries = 0
    try:
        for part in parts:
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=root)
            os.close(root)
            root = child
        owner = os.fstat(root).st_uid

        def walk(directory, prefix="", depth=0):
            nonlocal total, entries
            if depth > 24:
                raise UnsafeProjectSource("project nesting limit exceeded")
            before_dir = _identity(os.fstat(directory))
            names = []
            with os.scandir(directory) as listing:
                for item in listing:
                    names.append(item.name)
                    if len(names) > MAX_FILES:
                        raise UnsafeProjectSource("directory entry limit exceeded")
            names.sort()
            for name in names:
                entries += 1
                if entries > 4096:
                    raise UnsafeProjectSource("project entry limit exceeded")
                if not _component(name):
                    raise UnsafeProjectSource("invalid project filename")
                path = prefix + name
                if name in RESERVED:
                    raise UnsafeProjectSource("project source uses a reserved executor path")
                if name in SKIP or name == ".env" or name.startswith(".env."):
                    omitted.append(path)
                    if len(omitted) > MAX_FILES:
                        raise UnsafeProjectSource("omitted entry limit exceeded")
                    continue
                fd = os.open(name, flags, dir_fd=directory)
                try:
                    info = os.fstat(fd)
                    if info.st_uid != owner:
                        raise UnsafeProjectSource("project ownership mismatch")
                    if stat.S_ISDIR(info.st_mode):
                        walk(fd, path + "/", depth + 1)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                        if info.st_size > MAX_FILE or len(files) >= MAX_FILES:
                            raise UnsafeProjectSource("source file limit exceeded")
                        chunks = bytearray()
                        while len(chunks) <= MAX_FILE:
                            chunk = os.read(fd, min(65536, MAX_FILE + 1 - len(chunks)))
                            if not chunk:
                                break
                            chunks.extend(chunk)
                        total += len(chunks)
                        if len(chunks) > MAX_FILE or total > MAX_TOTAL:
                            raise UnsafeProjectSource("source size limit exceeded")
                        if _identity(info) != _identity(os.fstat(fd)):
                            raise UnsafeProjectSource("source changed during read")
                        files[path] = bytes(chunks)
                        modes[path] = 0o755 if info.st_mode & 0o111 else 0o644
                    else:
                        raise UnsafeProjectSource("source is not a regular file or directory")
                    current = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    if _identity(info) != _identity(current):
                        raise UnsafeProjectSource("source path changed during read")
                finally:
                    os.close(fd)
            if before_dir != _identity(os.fstat(directory)):
                raise UnsafeProjectSource("source directory changed during read")

        walk(root)
    finally:
        os.close(root)
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, contents in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(contents), modes[name]
            member.uid = member.gid = 1000
            archive.addfile(member, io.BytesIO(contents))
    packed = data.getvalue()
    return {"archive": packed, "sha256": hashlib.sha256(packed).hexdigest(),
            "files": files, "omitted": omitted, "bytes": total}
