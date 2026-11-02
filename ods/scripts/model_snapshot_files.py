"""Confined file operations for catalog snapshots.

On POSIX, held directory descriptors and O_NOFOLLOW prevent path substitution
from redirecting reads or writes. Platforms without dir_fd use link checks;
they do not provide the POSIX race guarantee.
"""
from __future__ import annotations

import errno
import os
import stat
from contextlib import contextmanager
from pathlib import Path


class UnsafePath(OSError):
    """A snapshot path is not a private regular file or directory."""


_DIR_FD = all(fn in os.supports_dir_fd for fn in (os.open, os.mkdir, os.stat, os.unlink))
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DELETE_DIR_FD = (_DIR_FD and os.rmdir in os.supports_dir_fd
                  and os.listdir in os.supports_fd and bool(_NOFOLLOW))


def _directory(path: Path, parent_fd: int | None = None, name: str | None = None) -> int:
    if path.is_symlink() and parent_fd is None:
        raise UnsafePath(f"refusing symbolic link directory {path}")
    flags = os.O_RDONLY | os.O_DIRECTORY | _NOFOLLOW
    try:
        fd = os.open(name if parent_fd is not None else path, flags, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise UnsafePath(f"refusing linked or non-directory snapshot path {path}") from exc
        raise
    if not stat.S_ISDIR(os.fstat(fd).st_mode):
        os.close(fd)
        raise UnsafePath(f"not a directory: {path}")
    return fd


class SnapshotFile:
    def __init__(self, path: Path, parent_fd: int | None):
        self.path = path
        self.parent_fd = parent_fd
        self.identity: tuple[int, int] | None = None

    @property
    def name(self) -> str:
        return self.path.name

    def __str__(self) -> str:
        return str(self.path)

    def with_name(self, name: str) -> SnapshotFile:
        if name != Path(name).name or name in ("", ".", ".."):
            raise UnsafePath("invalid snapshot filename")
        return SnapshotFile(self.path.with_name(name), self.parent_fd)

    def _stat(self):
        if self.parent_fd is None:
            return self.path.lstat()
        return os.stat(self.name, dir_fd=self.parent_fd, follow_symlinks=False)

    def stat(self):
        info = self._stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise UnsafePath(f"refusing linked or non-regular snapshot file {self.path}")
        if self.identity is not None and (info.st_dev, info.st_ino) != self.identity:
            raise UnsafePath(f"snapshot file changed during download: {self.path}")
        return info

    def exists(self) -> bool:
        try:
            self.stat()
        except FileNotFoundError:
            return False
        return True

    def is_file(self) -> bool:
        return self.exists()

    def is_symlink(self) -> bool:
        try:
            return stat.S_ISLNK(self._stat().st_mode)
        except FileNotFoundError:
            return False

    def open(self, mode: str):
        if mode not in ("rb", "wb", "ab"):
            raise ValueError(f"unsupported snapshot mode: {mode}")
        # Never truncate before fstat: a pre-existing hardlink must remain intact.
        flags = os.O_RDONLY if mode == "rb" else os.O_WRONLY | os.O_CREAT
        flags |= _NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        if mode == "ab":
            flags |= os.O_APPEND
        if self.parent_fd is None:
            self.exists()  # conservative Windows link/type check
        fd = os.open(self.name if self.parent_fd is not None else self.path,
                     flags, 0o600, dir_fd=self.parent_fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise UnsafePath(f"refusing linked or non-regular snapshot file {self.path}")
            identity = (info.st_dev, info.st_ino)
            if self.identity is not None and identity != self.identity:
                raise UnsafePath(f"snapshot file changed during download: {self.path}")
            self.identity = identity
            if mode == "wb":
                os.ftruncate(fd, 0)
            return os.fdopen(fd, mode)
        except BaseException:
            os.close(fd)
            raise

    def unlink(self) -> None:
        if self.parent_fd is None:
            self.path.unlink()
        else:
            os.unlink(self.name, dir_fd=self.parent_fd)

    def publish(self, final: SnapshotFile, identity: tuple[int, int]) -> None:
        if self.parent_fd != final.parent_fd:
            raise UnsafePath("snapshot publication changed directory")
        current = self.stat()
        if (current.st_dev, current.st_ino) != identity:
            raise UnsafePath("verified partial changed before publication")
        if self.parent_fd is None:
            os.replace(self.path, final.path)
        else:
            os.replace(self.name, final.name,
                       src_dir_fd=self.parent_fd, dst_dir_fd=self.parent_fd)
        final.identity = identity
        published = final.stat()
        if (published.st_dev, published.st_ino) != identity:
            raise UnsafePath("verified partial changed during publication")


class Snapshot:
    def __init__(self, models_dir: Path, folder: str):
        if Path(folder).name != folder or folder in ("", ".", ".."):
            raise UnsafePath("invalid snapshot directory")
        # The configured store itself may be a deliberate owner-managed symlink.
        models_dir.mkdir(parents=True, exist_ok=True)
        self.store = models_dir.resolve(strict=True)
        self.path = self.store / folder
        self.fd: int | None = None
        self.folder = folder

    def __enter__(self) -> Snapshot:
        if not _DIR_FD:
            if self.path.is_symlink():
                raise UnsafePath(f"refusing symbolic link directory {self.path}")
            self.path.mkdir(exist_ok=True)
            if not self.path.is_dir():
                raise UnsafePath(f"not a directory: {self.path}")
            return self
        root_fd = _directory(self.store)
        try:
            try:
                os.mkdir(self.folder, dir_fd=root_fd)
            except FileExistsError:
                pass
            self.fd = _directory(self.path, root_fd, self.folder)
        finally:
            os.close(root_fd)
        return self

    def __exit__(self, *_exc) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def check_current(self) -> None:
        """The named snapshot must still be the directory this operation opened."""
        current = self.path.lstat()
        if not stat.S_ISDIR(current.st_mode):
            raise UnsafePath("snapshot directory changed during download")
        if self.fd is not None:
            held = os.fstat(self.fd)
            if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
                raise UnsafePath("snapshot directory changed during download")

    @contextmanager
    def file(self, relative: str, *, create: bool = True):
        parts = _split_relative(relative)
        path = self.path
        parent_fd = os.dup(self.fd) if self.fd is not None else None
        try:
            for part in parts[:-1]:
                path /= part
                if parent_fd is None:
                    if path.is_symlink():
                        raise UnsafePath(f"refusing symbolic link directory {path}")
                    if create:
                        path.mkdir(exist_ok=True)
                    if not path.is_dir():
                        raise UnsafePath(f"not a directory: {path}")
                else:
                    if create:
                        try:
                            os.mkdir(part, dir_fd=parent_fd)
                        except FileExistsError:
                            pass
                    child = _directory(path, parent_fd, part)
                    os.close(parent_fd)
                    parent_fd = child
            yield SnapshotFile(path / parts[-1], parent_fd)
        finally:
            if parent_fd is not None:
                os.close(parent_fd)


def _split_relative(relative: str) -> list[str]:
    parts = relative.split("/")
    if (not parts or any(p in ("", ".", "..") or "\\" in p or ":" in p for p in parts)
            or Path(relative).is_absolute()):
        raise UnsafePath("invalid snapshot file path")
    return parts


def _same_directory(parent_fd: int, name: str, held_fd: int) -> None:
    current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    held = os.fstat(held_fd)
    if (not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino)):
        raise UnsafePath("snapshot directory changed during deletion")


def delete_snapshot(models_dir: Path, folder: str, files: list[dict]) -> int:
    """Remove only catalog files/partials after inspecting the whole snapshot.

    The caller owns lifecycle admission and active-snapshot protection. Held
    directory descriptors confine unlinks even if names are replaced. External
    concurrent changes can cause partial removal and an error, never success
    inferred from a best-effort recursive delete.
    """
    if not _DELETE_DIR_FD:
        raise UnsafePath("snapshot deletion requires POSIX directory descriptors")
    if Path(folder).name != folder or folder in ("", ".", ".."):
        raise UnsafePath("invalid snapshot directory")
    allowed_files: set[str] = set()
    allowed_dirs: set[str] = set()
    for item in files:
        parts = _split_relative(item["path"])
        relative = "/".join(parts)
        allowed_files.update((relative, relative + ".partial"))
        allowed_dirs.update("/".join(parts[:n]) for n in range(1, len(parts)))
    if allowed_files & allowed_dirs:
        raise UnsafePath("catalog file and directory paths overlap")
    store = models_dir.resolve(strict=True)
    store_fd = _directory(store)
    held: dict[str, int] = {}
    witnesses: list[tuple[str, str, int, int]] = []
    try:
        held[""] = _directory(store / folder, store_fd, folder)

        def inspect(relative: str) -> None:
            fd = held[relative]
            for name in sorted(os.listdir(fd)):
                child = f"{relative}/{name}" if relative else name
                if child in allowed_dirs:
                    held[child] = _directory(store / folder / child, fd, name)
                    inspect(child)
                elif child in allowed_files:
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise UnsafePath(f"refusing linked or non-regular snapshot file {child}")
                    witnesses.append((relative, name, info.st_dev, info.st_ino))
                else:
                    raise UnsafePath(f"unexpected file in snapshot: {child}")

        inspect("")
        _same_directory(store_fd, folder, held[""])
        for relative, fd in held.items():
            if relative:
                parent, _, name = relative.rpartition("/")
                _same_directory(held[parent], name, fd)
        removed = 0
        for relative, name, device, inode in witnesses:
            fd = held[relative]
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or (info.st_dev, info.st_ino) != (device, inode)):
                raise UnsafePath("snapshot file changed during deletion")
            os.unlink(name, dir_fd=fd)
            removed += info.st_size
        for relative in sorted(held, key=lambda r: (r.count("/"), r), reverse=True):
            if relative:
                parent, _, name = relative.rpartition("/")
                _same_directory(held[parent], name, held[relative])
                os.rmdir(name, dir_fd=held[parent])
        _same_directory(store_fd, folder, held[""])
        os.rmdir(folder, dir_fd=store_fd)
        return removed
    finally:
        for fd in held.values():
            os.close(fd)
        os.close(store_fd)
