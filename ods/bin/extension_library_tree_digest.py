"""Deterministic, content-bound provenance for a complete library payload.

Unlike the legacy one-click cache digest, this does not trust timestamps or
follow symlinks. The catalog and future apply bridge can compare the same
definition tree without publishing file contents or host paths in a plan.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path
from typing import Any, NamedTuple

MAX_TREE_BYTES = 50 * 1024 * 1024
MAX_TREE_ENTRIES = 4096
MAX_TREE_DEPTH = 64
_RECEIPT = ".ods-library-receipt.json"
_DOMAIN = b"ods-extension-library-tree-v1\0"


class LibraryTreeDigestError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class LibraryTreeFile(NamedTuple):
    relative_path: str
    content: bytes
    executable: bool


class LibraryTreeSnapshot(NamedTuple):
    """Approved payload bytes; a later effect must consume these, not live paths."""

    digest: str
    directories: tuple[str, ...]
    files: tuple[LibraryTreeFile, ...]
    total_bytes: int


def _fail(code: str) -> None:
    raise LibraryTreeDigestError(code) from None


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        # Windows path stat can lag descriptor stat for ctime immediately
        # after a write. The indexed catalog path remains byte-exact.
        info.st_ctime_ns if os.name != "nt" else 0,
        info.st_mode,
    )


def _check_runtime_custody(info: os.stat_result) -> None:
    # The Git-index catalog has no host owner. A live Linux payload does:
    # group/other writers can bypass an ODS transaction's service lock.
    if os.name == "posix" and (info.st_uid != os.geteuid() or info.st_mode & 0o022):
        _fail("library-tree-custody-invalid")


def _name(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    if (
        not relative
        or relative.startswith("/")
        or any(part in {"", ".", ".."} for part in relative.split("/"))
        or "\x00" in relative
    ):
        _fail("library-tree-path-invalid")
    return relative


def _read_file(path: Path, before: os.stat_result, remaining: int) -> bytes:
    _check_runtime_custody(before)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size < 0
        or before.st_size > remaining
    ):
        _fail("library-tree-file-invalid")
    # Windows text-mode os.read translates CRLF and can return fewer bytes
    # than fstat size. Tree provenance must hash the exact on-disk bytes.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        _fail("library-tree-file-unavailable")
    try:
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(before) or not stat.S_ISREG(opened.st_mode):
            _fail("library-tree-file-drift")
        _check_runtime_custody(opened)
        content = bytearray()
        while len(content) < opened.st_size:
            chunk = os.read(descriptor, min(1024 * 1024, opened.st_size - len(content)))
            if not chunk:
                _fail("library-tree-file-drift")
            content.extend(chunk)
        if _identity(os.fstat(descriptor)) != _identity(before):
            _fail("library-tree-file-drift")
        try:
            after = path.lstat()
        except OSError:
            _fail("library-tree-file-drift")
        if _identity(after) != _identity(before):
            _fail("library-tree-file-drift")
        return bytes(content)
    finally:
        os.close(descriptor)


def _file_hash(digest: Any, name: str, content: bytes, executable: bool) -> None:
    digest.update(
        b"F\0"
        + name.encode("utf-8")
        + b"\0"
        + (b"1" if executable else b"0")
        + b"\0"
        + len(content).to_bytes(8, "big")
        + content
    )


def _bounded_paths(root: Path) -> list[Path]:
    pending = [root]
    paths: list[Path] = []
    while pending:
        parent = pending.pop()
        try:
            with os.scandir(parent) as entries:
                for entry in entries:
                    paths.append(Path(entry.path))
                    if len(paths) > MAX_TREE_ENTRIES:
                        _fail("library-tree-entry-limit")
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(Path(entry.path))
                    elif not entry.is_file(follow_symlinks=False):
                        _fail("library-tree-entry-invalid")
        except OSError:
            _fail("library-tree-enumeration-unavailable")
    return sorted(paths, key=lambda item: item.relative_to(root).as_posix())


def digest_extension_tree(root: Any) -> str:
    """Hash every definition file and directory, excluding only its receipt.

    Returns a ``sha256:`` digest. It refuses symlinks, special files, oversized
    trees, and a changed file. Runtime custody and approval remain separate.
    """
    if not isinstance(root, Path) or not root.is_absolute():
        _fail("library-tree-root-invalid")
    try:
        root_info = root.lstat()
    except OSError:
        _fail("library-tree-root-unavailable")
    if not stat.S_ISDIR(root_info.st_mode) or root.is_symlink():
        _fail("library-tree-root-invalid")
    _check_runtime_custody(root_info)
    paths = _bounded_paths(root)
    digest = hashlib.sha256(_DOMAIN)
    total = 0
    for path in paths:
        name = _name(path, root)
        if name == _RECEIPT:
            continue
        try:
            before = path.lstat()
        except OSError:
            _fail("library-tree-entry-drift")
        if stat.S_ISDIR(before.st_mode):
            _check_runtime_custody(before)
            digest.update(b"D\0" + name.encode("utf-8") + b"\0")
        elif stat.S_ISREG(before.st_mode):
            content = _read_file(path, before, MAX_TREE_BYTES - total)
            total += len(content)
            _file_hash(digest, name, content, bool(before.st_mode & 0o111))
        else:
            _fail("library-tree-entry-invalid")
    if not root.is_dir() or _identity(root.lstat()) != _identity(root_info):
        _fail("library-tree-root-drift")
    return "sha256:" + digest.hexdigest()


def _snapshot_directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _snapshot_root(root: Path) -> int:
    """Open every ancestor without following a symlink; retain the root inode."""

    descriptor = -1
    try:
        descriptor = os.open(root.anchor, _snapshot_directory_flags())
        for part in root.parts[1:]:
            child = os.open(part, _snapshot_directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode):
            _fail("library-tree-root-invalid")
        _check_runtime_custody(info)
        return descriptor
    except (OSError, LibraryTreeDigestError):
        if descriptor >= 0:
            os.close(descriptor)
        _fail("library-tree-root-invalid")


def _snapshot_relative_directory(root_descriptor: int, relative: str) -> int:
    descriptor = os.dup(root_descriptor)
    try:
        if relative:
            for part in relative.split("/"):
                child = os.open(part, _snapshot_directory_flags(), dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
                _check_runtime_custody(os.fstat(descriptor))
        return descriptor
    except (OSError, LibraryTreeDigestError):
        os.close(descriptor)
        _fail("library-tree-entry-drift")


def _snapshot_file(
    parent: int, name: str, before: os.stat_result, remaining: int
) -> bytes:
    _check_runtime_custody(before)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size < 0
        or before.st_size > remaining
    ):
        _fail("library-tree-file-invalid")
    flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(name, flags, dir_fd=parent)
    except OSError:
        _fail("library-tree-file-unavailable")
    try:
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(before) or not stat.S_ISREG(opened.st_mode):
            _fail("library-tree-file-drift")
        content = bytearray()
        while len(content) < opened.st_size:
            chunk = os.read(descriptor, min(1024 * 1024, opened.st_size - len(content)))
            if not chunk:
                _fail("library-tree-file-drift")
            content.extend(chunk)
        if _identity(os.fstat(descriptor)) != _identity(before):
            _fail("library-tree-file-drift")
        after = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if _identity(after) != _identity(before):
            _fail("library-tree-file-drift")
        return bytes(content)
    except OSError:
        _fail("library-tree-file-drift")
    finally:
        os.close(descriptor)


def snapshot_extension_tree(root: Any) -> LibraryTreeSnapshot:
    """Capture one bounded, owner-controlled tree through no-follow descriptors.

    The exact file bytes and executable bits are kept in memory for a future
    host-owned materializer. A caller must still bind ``digest`` to the approved
    plan and check the staged manifest/Compose bytes before any effect.
    """

    if (
        os.name != "posix"
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
        or os.stat not in os.supports_follow_symlinks
    ):
        _fail("library-tree-platform-unsupported")
    if (
        not isinstance(root, Path)
        or not root.is_absolute()
        or root == Path(root.anchor)
        or ".." in root.parts
    ):
        _fail("library-tree-root-invalid")
    descriptor = _snapshot_root(root)
    try:
        original = os.fstat(descriptor)
        pending = [""]
        directories: list[str] = []
        files: list[LibraryTreeFile] = []
        total = 0
        entries_seen = 0
        while pending:
            relative = pending.pop()
            parent = _snapshot_relative_directory(descriptor, relative)
            try:
                with os.scandir(parent) as iterator:
                    entries = list(iterator)
                for entry in entries:
                    name = f"{relative}/{entry.name}" if relative else entry.name
                    if not name or any(
                        part in {"", ".", ".."} for part in name.split("/")
                    ):
                        _fail("library-tree-path-invalid")
                    try:
                        name.encode("utf-8", "strict")
                        before = entry.stat(follow_symlinks=False)
                    except (OSError, UnicodeError):
                        _fail("library-tree-entry-drift")
                    entries_seen += 1
                    if entries_seen > MAX_TREE_ENTRIES:
                        _fail("library-tree-entry-limit")
                    if name == _RECEIPT:
                        continue
                    if stat.S_ISDIR(before.st_mode):
                        _check_runtime_custody(before)
                        if len(name.split("/")) > MAX_TREE_DEPTH:
                            _fail("library-tree-entry-limit")
                        child = os.open(
                            entry.name, _snapshot_directory_flags(), dir_fd=parent
                        )
                        try:
                            if _identity(os.fstat(child)) != _identity(before):
                                _fail("library-tree-entry-drift")
                        finally:
                            os.close(child)
                        directories.append(name)
                        pending.append(name)
                    elif stat.S_ISREG(before.st_mode):
                        _check_runtime_custody(before)
                        content = _snapshot_file(
                            parent, entry.name, before, MAX_TREE_BYTES - total
                        )
                        total += len(content)
                        files.append(
                            LibraryTreeFile(name, content, bool(before.st_mode & 0o111))
                        )
                    else:
                        _fail("library-tree-entry-invalid")
            except OSError:
                _fail("library-tree-entry-drift")
            finally:
                os.close(parent)
        if _identity(os.fstat(descriptor)) != _identity(original):
            _fail("library-tree-root-drift")
        digest = hashlib.sha256(_DOMAIN)
        records = [(name, None) for name in directories] + [
            (item.relative_path, item) for item in files
        ]
        for name, item in sorted(records, key=lambda pair: pair[0]):
            if item is None:
                digest.update(b"D\0" + name.encode("utf-8") + b"\0")
            else:
                _file_hash(digest, name, item.content, item.executable)
        return LibraryTreeSnapshot(
            digest="sha256:" + digest.hexdigest(),
            directories=tuple(sorted(directories)),
            files=tuple(sorted(files, key=lambda item: item.relative_path)),
            total_bytes=total,
        )
    finally:
        os.close(descriptor)


def _git(repository: Path, *argv: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["git", "-C", str(repository), *argv],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail("library-tree-git-unavailable")


def digest_indexed_extension_tree(repository: Any, root: Any) -> str:
    """Hash Git-index blobs, not platform-converted working-tree text.

    The generator uses this for a repository-owned library. It refuses
    unstaged edits and untracked additions, while permitting staged source
    edits to be cataloged before their commit. A future effect must consume
    these exact blobs or re-prove a matching staged payload.
    """
    if (
        not isinstance(repository, Path)
        or not isinstance(root, Path)
        or not repository.is_absolute()
        or not root.is_absolute()
        or not root.is_relative_to(repository)
        or root == repository
        or root.is_symlink()
    ):
        _fail("library-tree-root-invalid")
    relative_root = root.relative_to(repository).as_posix()
    if _git(repository, "diff", "--quiet", "--", relative_root).returncode != 0:
        _fail("library-tree-unstaged-drift")
    others = _git(
        repository, "ls-files", "--others", "--exclude-standard", "--", relative_root
    )
    if others.returncode != 0 or others.stdout:
        _fail("library-tree-untracked-entry")
    listing = _git(repository, "ls-files", "--stage", "-z", "--", relative_root)
    if listing.returncode != 0 or not listing.stdout:
        _fail("library-tree-git-unavailable")
    files: dict[str, tuple[str, str]] = {}
    directories: set[str] = set()
    prefix = relative_root + "/"
    for raw in listing.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            metadata, path = raw.split(b"\t", 1)
            mode, object_id, stage = metadata.decode("ascii").split(" ")
            source = path.decode("utf-8")
        except (UnicodeError, ValueError):
            _fail("library-tree-git-entry-invalid")
        if (
            mode not in {"100644", "100755"}
            or stage != "0"
            or not source.startswith(prefix)
            or not all(char in "0123456789abcdef" for char in object_id)
            or len(object_id) not in {40, 64}
        ):
            _fail("library-tree-git-entry-invalid")
        name = source[len(prefix) :]
        if name == _RECEIPT:
            continue
        if not name or any(part in {"", ".", ".."} for part in name.split("/")):
            _fail("library-tree-path-invalid")
        if name in files:
            _fail("library-tree-git-entry-invalid")
        files[name] = (mode, object_id)
        parts = name.split("/")
        directories.update("/".join(parts[:index]) for index in range(1, len(parts)))
    if len(files) + len(directories) > MAX_TREE_ENTRIES:
        _fail("library-tree-entry-limit")
    digest = hashlib.sha256(_DOMAIN)
    total = 0
    for name in sorted(files.keys() | directories):
        if name in directories:
            digest.update(b"D\0" + name.encode("utf-8") + b"\0")
            continue
        mode, object_id = files[name]
        size = _git(repository, "cat-file", "-s", object_id)
        if size.returncode != 0 or not size.stdout.strip().isdigit():
            _fail("library-tree-git-blob-invalid")
        blob_size = int(size.stdout.strip())
        if blob_size > MAX_TREE_BYTES - total:
            _fail("library-tree-git-blob-invalid")
        blob = _git(repository, "cat-file", "blob", object_id)
        if blob.returncode != 0 or len(blob.stdout) != blob_size:
            _fail("library-tree-git-blob-invalid")
        total += len(blob.stdout)
        _file_hash(digest, name, blob.stdout, mode == "100755")
    return "sha256:" + digest.hexdigest()


__all__ = [
    "LibraryTreeFile",
    "LibraryTreeSnapshot",
    "LibraryTreeDigestError",
    "digest_extension_tree",
    "digest_indexed_extension_tree",
    "snapshot_extension_tree",
]
