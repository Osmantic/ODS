"""Stage verified generic extension data without mutating the live path.

This is not a paired restore or a host effect. An existing *complete* stage is
rehash-verified on replay; an incomplete or foreign stage is retained and
refused for owner review. No stage or live data is automatically removed.
"""

from __future__ import annotations

import hashlib
import os
import stat
import tarfile
from pathlib import Path
from typing import Any

from extension_data_backup_runtime import (
    DataBackupRuntimeError,
    _close_quietly,
    _directory_flags,
    _identity,
    _open_absolute_directory,
    _open_relative_directory,
    _safe_relative_parts,
    _validate_platform,
)
from extension_data_restore_journal import RestoreIntentJournal
from extension_data_stream_snapshot import (
    StreamSnapshotStore, _check_extended_metadata, _check_sparse_file,
)
from extension_lifecycle_work import LifecycleWorkCommand, LifecycleWorkExecutionError


class StreamRestoreStageError(LifecycleWorkExecutionError):
    """Value-free stage custody, extraction, or readback refusal."""


def _fail(code: str, cause: BaseException | None = None) -> None:
    if cause is None:
        raise StreamRestoreStageError(code) from None
    raise StreamRestoreStageError(code) from cause


def _opened_dir(parent: int, name: str) -> int:
    descriptor = os.open(name, _directory_flags() | os.O_NOATIME, dir_fd=parent)
    try:
        info, parent_info = os.fstat(descriptor), os.fstat(parent)
        mode = stat.S_IMODE(info.st_mode)
        if (
            not stat.S_ISDIR(info.st_mode) or info.st_dev != parent_info.st_dev
            or info.st_uid != os.geteuid() or info.st_gid != os.getegid()
            or mode & 0o700 != 0o700 or mode & 0o7022
        ):
            _fail("lifecycle-work-data-restore-stage-unsafe")
        _check_extended_metadata(descriptor)
        return descriptor
    except BaseException:
        _close_quietly(descriptor)
        raise


def _beneath(root: int, parts: tuple[str, ...]) -> int:
    descriptor = os.dup(root)
    try:
        for part in parts:
            child = _opened_dir(descriptor, part)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        _close_quietly(descriptor)
        raise


def _stamp(descriptor: int, mode: int, atime: str, mtime: str) -> None:
    os.fchmod(descriptor, mode)
    os.utime(descriptor, ns=(int(atime), int(mtime)))
    os.fsync(descriptor)
    info = os.fstat(descriptor)
    if (
        stat.S_IMODE(info.st_mode) != mode or info.st_atime_ns != int(atime)
        or info.st_mtime_ns != int(mtime)
    ):
        _fail("lifecycle-work-data-restore-stage-metadata-invalid")


def _copy_file(parent: int, name: str, archive: tarfile.TarFile,
               member: tarfile.TarInfo, entry: dict[str, Any]) -> None:
    if not member.isfile() or member.size != entry["size"] or member.mode != entry["mode"]:
        _fail("lifecycle-work-data-restore-stage-member-invalid")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            name, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=parent,
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode) or info.st_dev != os.fstat(parent).st_dev
            or info.st_uid != os.geteuid() or info.st_gid != os.getegid()
            or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600
        ):
            _fail("lifecycle-work-data-restore-stage-unsafe")
        _check_extended_metadata(descriptor)
        source = archive.extractfile(member)
        if source is None:
            _fail("lifecycle-work-data-restore-stage-member-invalid")
        digest = hashlib.sha256()
        remaining = entry["size"]
        while remaining:
            chunk = source.read(min(remaining, 64 * 1024))
            if not chunk:
                _fail("lifecycle-work-data-restore-stage-content-invalid")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    _fail("lifecycle-work-data-restore-stage-write-failed")
                view = view[written:]
            remaining -= len(chunk)
        if source.read(1) or digest.hexdigest() != entry["sha256"]:
            _fail("lifecycle-work-data-restore-stage-content-invalid")
        os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        readback = hashlib.sha256()
        remaining = entry["size"]
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                _fail("lifecycle-work-data-restore-stage-content-invalid")
            readback.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1) or readback.hexdigest() != entry["sha256"]:
            _fail("lifecycle-work-data-restore-stage-content-invalid")
        _stamp(descriptor, entry["mode"], entry["atimeNs"], entry["mtimeNs"])
        _check_extended_metadata(descriptor)
        if os.fstat(descriptor).st_size != entry["size"]:
            _fail("lifecycle-work-data-restore-stage-content-invalid")
        os.fsync(parent)
    finally:
        _close_quietly(descriptor)


def _observed(parent: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _candidate_file(info: os.stat_result, parent: int, entry: dict[str, Any]) -> None:
    mode = stat.S_IMODE(info.st_mode)
    if (
        not stat.S_ISREG(info.st_mode) or info.st_dev != os.fstat(parent).st_dev
        or info.st_uid != os.geteuid() or info.st_gid != os.getegid()
        or info.st_nlink != 1 or info.st_size > entry["size"]
        or mode not in {0o600, entry["mode"]}
        or (info.st_size < entry["size"] and mode != 0o600)
    ):
        _fail("lifecycle-work-data-restore-stage-foreign")


def _exact_source_bytes(source: Any, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = source.read(remaining)
        if not chunk:
            _fail("lifecycle-work-data-restore-stage-content-invalid")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _exact_fd_bytes(descriptor: int, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.read(descriptor, remaining)
        if not chunk:
            _fail("lifecycle-work-data-restore-stage-drift")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _check_prefix(parent: int, name: str, archive: tarfile.TarFile,
                  member: tarfile.TarInfo, entry: dict[str, Any]) -> None:
    observed = _observed(parent, name)
    if observed is None:
        _fail("lifecycle-work-data-restore-stage-drift")
    _candidate_file(observed, parent, entry)
    if not member.isfile() or member.size != entry["size"] or member.mode != entry["mode"]:
        _fail("lifecycle-work-data-restore-stage-member-invalid")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME
            | getattr(os, "O_CLOEXEC", 0), dir_fd=parent,
        )
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(observed):
            _fail("lifecycle-work-data-restore-stage-drift")
        _check_extended_metadata(descriptor)
        source = archive.extractfile(member)
        if source is None:
            _fail("lifecycle-work-data-restore-stage-member-invalid")
        digest = hashlib.sha256()
        remaining = opened.st_size
        while remaining:
            size = min(remaining, 64 * 1024)
            staged_bytes = _exact_fd_bytes(descriptor, size)
            archive_bytes = _exact_source_bytes(source, size)
            if staged_bytes != archive_bytes:
                _fail("lifecycle-work-data-restore-stage-prefix-invalid")
            digest.update(archive_bytes)
            remaining -= size
        if opened.st_size == entry["size"] and digest.hexdigest() != entry["sha256"]:
            _fail("lifecycle-work-data-restore-stage-prefix-invalid")
        if _identity(os.fstat(descriptor)) != _identity(opened) or (
            (visible := _observed(parent, name)) is None or _identity(visible) != _identity(opened)
        ):
            _fail("lifecycle-work-data-restore-stage-drift")
    finally:
        _close_quietly(descriptor)


def _resume_file(parent: int, name: str, archive: tarfile.TarFile,
                 member: tarfile.TarInfo, entry: dict[str, Any]) -> None:
    # Repeat the read-only preflight immediately before appending; an earlier
    # whole-tree classification is not authority to trust a changed inode.
    _check_prefix(parent, name, archive, member, entry)
    observed = _observed(parent, name)
    if observed is None:
        _fail("lifecycle-work-data-restore-stage-drift")
    flags = os.O_RDWR if observed.st_size < entry["size"] else os.O_RDONLY
    descriptor: int | None = None
    try:
        descriptor = os.open(
            name, flags | os.O_NOFOLLOW | os.O_NOATIME
            | getattr(os, "O_CLOEXEC", 0), dir_fd=parent,
        )
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(observed):
            _fail("lifecycle-work-data-restore-stage-drift")
        _candidate_file(opened, parent, entry)
        _check_extended_metadata(descriptor)
        source = archive.extractfile(member)
        if source is None or not member.isfile() or member.size != entry["size"]:
            _fail("lifecycle-work-data-restore-stage-member-invalid")
        digest = hashlib.sha256()
        remaining_prefix = opened.st_size
        while remaining_prefix:
            size = min(remaining_prefix, 64 * 1024)
            staged_bytes = _exact_fd_bytes(descriptor, size)
            archive_bytes = _exact_source_bytes(source, size)
            if staged_bytes != archive_bytes:
                _fail("lifecycle-work-data-restore-stage-prefix-invalid")
            digest.update(archive_bytes)
            remaining_prefix -= size
        if _identity(os.fstat(descriptor)) != _identity(opened) or (
            (visible := _observed(parent, name)) is None or _identity(visible) != _identity(opened)
        ):
            _fail("lifecycle-work-data-restore-stage-drift")
        remaining = entry["size"] - opened.st_size
        while remaining:
            chunk = source.read(min(remaining, 64 * 1024))
            if not chunk:
                _fail("lifecycle-work-data-restore-stage-content-invalid")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    _fail("lifecycle-work-data-restore-stage-write-failed")
                view = view[written:]
            remaining -= len(chunk)
        if source.read(1) or digest.hexdigest() != entry["sha256"]:
            _fail("lifecycle-work-data-restore-stage-content-invalid")
        os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        readback = hashlib.sha256()
        remaining = entry["size"]
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                _fail("lifecycle-work-data-restore-stage-content-invalid")
            readback.update(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1) or readback.hexdigest() != entry["sha256"]:
            _fail("lifecycle-work-data-restore-stage-content-invalid")
        _stamp(descriptor, entry["mode"], entry["atimeNs"], entry["mtimeNs"])
        _check_extended_metadata(descriptor)
        if os.fstat(descriptor).st_size != entry["size"] or (
            (visible := _observed(parent, name)) is None
            or (visible.st_dev, visible.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            _fail("lifecycle-work-data-restore-stage-drift")
        os.fsync(parent)
    finally:
        _close_quietly(descriptor)


def _plan(path_state: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[tuple[str, ...], set[str]]]:
    directories, files = [], []
    children: dict[tuple[str, ...], set[str]] = {(): set()}
    directory_paths: set[tuple[str, ...]] = set()
    for entry in path_state["entries"]:
        parts = _safe_relative_parts(entry["path"])
        if parts[:-1] not in children or parts[-1] in children[parts[:-1]]:
            _fail("lifecycle-work-data-restore-stage-index-invalid")
        children[parts[:-1]].add(parts[-1])
        if entry["type"] == "directory":
            directories.append(entry)
            directory_paths.add(parts)
            children[parts] = set()
        elif entry["type"] == "file":
            files.append(entry)
        else:
            _fail("lifecycle-work-data-restore-stage-index-invalid")
    # The verified index normally uses pre-order; still prove every ancestor.
    for entry in directories + files:
        parts = _safe_relative_parts(entry["path"])
        if any(parts[:depth] not in directory_paths for depth in range(1, len(parts))):
            _fail("lifecycle-work-data-restore-stage-index-invalid")
    return directories, files, children


def _classify_partial(root: int, archive: tarfile.TarFile, path_state: dict[str, Any],
                      children: dict[tuple[str, ...], set[str]], service_id: str,
                      index: int) -> None:
    """Prove the whole existing namespace is an expected subset before writes."""
    directory_entries = {tuple(entry["path"].split("/")): entry for entry in path_state["entries"]
                         if entry["type"] == "directory"}
    file_entries = {tuple(entry["path"].split("/")): entry for entry in path_state["entries"]
                    if entry["type"] == "file"}
    for parts in sorted(children, key=lambda value: (len(value), value)):
        try:
            descriptor = _beneath(root, parts)
        except FileNotFoundError:
            continue
        try:
            entry = directory_entries.get(parts)
            expected_mode = path_state["rootMode"] if entry is None else entry["mode"]
            mode = stat.S_IMODE(os.fstat(descriptor).st_mode)
            if mode not in {0o700, expected_mode}:
                _fail("lifecycle-work-data-restore-stage-foreign")
            actual = set(os.listdir(descriptor))
            if not actual <= children[parts] or (
                mode == expected_mode and expected_mode != 0o700 and actual != children[parts]
            ):
                _fail("lifecycle-work-data-restore-stage-foreign")
            for name in sorted(actual):
                child_parts = parts + (name,)
                observed = _observed(descriptor, name)
                if observed is None:
                    _fail("lifecycle-work-data-restore-stage-drift")
                file_entry = file_entries.get(child_parts)
                if file_entry is not None:
                    _candidate_file(observed, descriptor, file_entry)
                    member = archive.getmember(f"payload/{service_id}/{index}/{file_entry['path']}")
                    _check_prefix(descriptor, name, archive, member, file_entry)
                    continue
                directory_entry = directory_entries.get(child_parts)
                if directory_entry is None or not stat.S_ISDIR(observed.st_mode):
                    _fail("lifecycle-work-data-restore-stage-foreign")
                child = _opened_dir(descriptor, name)
                try:
                    if (
                        (os.fstat(child).st_dev, os.fstat(child).st_ino)
                        != (observed.st_dev, observed.st_ino)
                        or stat.S_IMODE(observed.st_mode) not in {0o700, directory_entry["mode"]}
                    ):
                        _fail("lifecycle-work-data-restore-stage-foreign")
                finally:
                    _close_quietly(child)
        finally:
            _close_quietly(descriptor)


def _verify_tree(root: int, path_state: dict[str, Any],
                 children: dict[tuple[str, ...], set[str]]) -> None:
    directory_entries = {tuple(entry["path"].split("/")): entry for entry in path_state["entries"]
                         if entry["type"] == "directory"}
    file_entries = {tuple(entry["path"].split("/")): entry for entry in path_state["entries"]
                    if entry["type"] == "file"}
    for parts in sorted(children, key=lambda value: (len(value), value)):
        descriptor = _beneath(root, parts)
        try:
            entry = directory_entries.get(parts)
            mode = path_state["rootMode"] if entry is None else entry["mode"]
            atime = path_state["rootAtimeNs"] if entry is None else entry["atimeNs"]
            mtime = path_state["rootMtimeNs"] if entry is None else entry["mtimeNs"]
            info = os.fstat(descriptor)
            if (
                stat.S_IMODE(info.st_mode) != mode or info.st_atime_ns != int(atime)
                or info.st_mtime_ns != int(mtime)
                or set(os.listdir(descriptor)) != children[parts]
            ):
                _fail("lifecycle-work-data-restore-stage-readback-invalid")
            for name in children[parts]:
                file_entry = file_entries.get(parts + (name,))
                if file_entry is None:
                    continue
                file_descriptor = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME
                    | getattr(os, "O_CLOEXEC", 0), dir_fd=descriptor,
                )
                try:
                    file_info = os.fstat(file_descriptor)
                    if (
                        not stat.S_ISREG(file_info.st_mode) or file_info.st_dev != info.st_dev
                        or file_info.st_uid != os.geteuid() or file_info.st_gid != os.getegid()
                        or file_info.st_nlink != 1 or file_info.st_size != file_entry["size"]
                        or stat.S_IMODE(file_info.st_mode) != file_entry["mode"]
                        or file_info.st_atime_ns != int(file_entry["atimeNs"])
                        or file_info.st_mtime_ns != int(file_entry["mtimeNs"])
                    ):
                        _fail("lifecycle-work-data-restore-stage-readback-invalid")
                    _check_extended_metadata(file_descriptor)
                    _check_sparse_file(file_descriptor, file_info)
                    digest = hashlib.sha256()
                    remaining = file_entry["size"]
                    while remaining:
                        chunk = os.read(file_descriptor, min(remaining, 64 * 1024))
                        if not chunk:
                            _fail("lifecycle-work-data-restore-stage-readback-invalid")
                        digest.update(chunk)
                        remaining -= len(chunk)
                    if os.read(file_descriptor, 1) or digest.hexdigest() != file_entry["sha256"]:
                        _fail("lifecycle-work-data-restore-stage-readback-invalid")
                finally:
                    _close_quietly(file_descriptor)
        finally:
            _close_quietly(descriptor)


class StreamRestoreStager:
    """Extract only into a verified sibling stage; never select a live effect."""

    def __init__(self, install_dir: Path, store: StreamSnapshotStore,
                 journal: RestoreIntentJournal) -> None:
        _validate_platform()
        if os.utime not in os.supports_fd or not hasattr(os, "O_NOATIME"):
            _fail("lifecycle-work-data-restore-stage-platform-unsupported")
        if install_dir != store.install_dir or install_dir != journal.install_dir:
            _fail("lifecycle-work-data-restore-stage-scope-invalid")
        self.install_dir, self.store, self.journal = install_dir, store, journal

    def stage(self, command: LifecycleWorkCommand, service_id: str, index: int) -> str | None:
        with self.store.open_verified(command) as (archive, document, receipt):
            if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                _fail("lifecycle-work-data-restore-stage-scope-invalid")
            service = next((item for item in document["services"] if item["serviceId"] == service_id), None)
            if service is None or index >= len(service["paths"]):
                _fail("lifecycle-work-data-restore-stage-scope-invalid")
            path_state = service["paths"][index]
            intent = self.journal.begin(command, receipt, service_id, index, path_state)
            self.journal.expect_original_target(command, receipt, service_id, index, path_state)
            if not intent["sourcePresent"]:
                return None
            directories, files, children = _plan(path_state)
            install: int | None = None
            parent: int | None = None
            staged: int | None = None
            try:
                install = _open_absolute_directory(self.install_dir, private=False)
                parts = _safe_relative_parts(intent["path"])
                parent = _open_relative_directory(install, parts[:-1], missing_ok=False)
                assert parent is not None
                if stat.S_IMODE(os.fstat(parent).st_mode) != 0o700:
                    _fail("lifecycle-work-data-restore-stage-parent-not-private")
                stage_name = intent["stageName"]
                try:
                    observed = os.stat(stage_name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    observed = None
                if observed is None:
                    try:
                        os.mkdir(stage_name, 0o700, dir_fd=parent)
                    except FileExistsError:
                        _fail("lifecycle-work-data-restore-stage-collision")
                    os.fsync(parent)
                elif not stat.S_ISDIR(observed.st_mode):
                    _fail("lifecycle-work-data-restore-stage-collision")
                staged = _opened_dir(parent, stage_name)
                completed = False
                if observed is not None:
                    if (os.fstat(staged).st_dev, os.fstat(staged).st_ino) != (observed.st_dev, observed.st_ino):
                        _fail("lifecycle-work-data-restore-stage-collision")
                    try:
                        _verify_tree(staged, path_state, children)
                        completed = True
                    except StreamRestoreStageError as exc:
                        if exc.code != "lifecycle-work-data-restore-stage-readback-invalid":
                            raise
                    if not completed:
                        _classify_partial(staged, archive, path_state, children, service_id, index)
                if not completed:
                    for entry in directories:
                        child_parts = _safe_relative_parts(entry["path"])
                        directory_parent = _beneath(staged, child_parts[:-1])
                        try:
                            previous = _observed(directory_parent, child_parts[-1])
                            if previous is None:
                                os.mkdir(child_parts[-1], 0o700, dir_fd=directory_parent)
                                os.fsync(directory_parent)
                            child = _opened_dir(directory_parent, child_parts[-1])
                            try:
                                if previous is not None and (
                                    (os.fstat(child).st_dev, os.fstat(child).st_ino)
                                    != (previous.st_dev, previous.st_ino)
                                ):
                                    _fail("lifecycle-work-data-restore-stage-drift")
                            finally:
                                _close_quietly(child)
                        finally:
                            _close_quietly(directory_parent)
                    for entry in files:
                        file_parts = _safe_relative_parts(entry["path"])
                        file_parent = _beneath(staged, file_parts[:-1])
                        try:
                            member = archive.getmember(f"payload/{service_id}/{index}/{entry['path']}")
                            if _observed(file_parent, file_parts[-1]) is None:
                                _copy_file(file_parent, file_parts[-1], archive, member, entry)
                            else:
                                _resume_file(file_parent, file_parts[-1], archive, member, entry)
                        finally:
                            _close_quietly(file_parent)
                    for entry in sorted(directories, key=lambda item: len(item["path"].split("/")), reverse=True):
                        child = _beneath(staged, _safe_relative_parts(entry["path"]))
                        try:
                            _stamp(child, entry["mode"], entry["atimeNs"], entry["mtimeNs"])
                        finally:
                            _close_quietly(child)
                    _stamp(staged, path_state["rootMode"], path_state["rootAtimeNs"],
                           path_state["rootMtimeNs"])
                    _verify_tree(staged, path_state, children)
                visible = os.stat(stage_name, dir_fd=parent, follow_symlinks=False)
                if (visible.st_dev, visible.st_ino) != (os.fstat(staged).st_dev, os.fstat(staged).st_ino):
                    _fail("lifecycle-work-data-restore-stage-drift")
                self.journal.expect_original_target(command, receipt, service_id, index, path_state)
                return stage_name
            except (OSError, DataBackupRuntimeError, tarfile.TarError, KeyError, ValueError) as exc:
                _fail("lifecycle-work-data-restore-stage-unavailable", exc)
            finally:
                _close_quietly(staged)
                _close_quietly(parent)
                _close_quietly(install)


__all__ = ["StreamRestoreStageError", "StreamRestoreStager"]
