"""Streaming, immutable multi-path snapshots for generic extension data.

The host selects this backend only for admitted generic backup. It captures the
old/new data union from an attested plan into a private tar file, streaming
regular-file contents without embedding them in JSON or keeping them in
memory.  The archive ends with a canonical metadata index and is hard-link
published only after every source identity check, the selected route's two
Docker/lease quiescence observations, and fsync succeed. Generic restore and
the production transaction executor remain disabled until paired effects and
live qualification exist. Direct store callers are source-only and do not
constitute a host-authorized backup.
"""

from __future__ import annotations

import errno
import hashlib
import io
import json
import os
import re
import secrets
import stat
import tarfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from extension_data_backup_runtime import (
    DataBackupRuntimeError,
    _close_quietly,
    _directory_flags,
    _file_read_flags,
    _identity,
    _open_absolute_directory,
    _open_relative_directory,
    _validate_platform,
    _validate_source_mode,
)
from extension_data_prior_effect import verify_installed_prior_data
from extension_data_scope_contract import BoundDataScope, BoundServiceData, BoundDataPath, DataPathRecord, bind_data_scope
from extension_lifecycle_work import REQUEST_SCHEMA, LifecycleWorkCommand, LifecycleWorkExecutionError


SNAPSHOT_SCHEMA = "ods.extension-data-stream-snapshot.v2"
INDEX_MEMBER = "_ods_snapshot/index.json"
_MAX_ENTRIES = 20000
_MAX_PATHS = 2048
_MAX_DEPTH = 32
_MAX_FILE_BYTES = 512 * 1024 * 1024
_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
_MAX_ARCHIVE_BYTES = _MAX_TOTAL_BYTES + 32 * 1024 * 1024
_MAX_INDEX_BYTES = 8 * 1024 * 1024
_TEMP_MODE = 0o600
_SEALED_MODE = 0o400
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_TIME_NS_RE = re.compile(r"^-?(?:0|[1-9][0-9]{0,18})$")


class StreamSnapshotError(LifecycleWorkExecutionError):
    """Stable, value-free source or archive refusal."""


@dataclass(frozen=True)
class StreamSnapshotReceipt:
    archive_sha256: str
    index_sha256: str
    file_count: int
    content_bytes: int


@dataclass
class _Budget:
    entries: int = 0
    files: int = 0
    content_bytes: int = 0


def _fail(code: str, cause: BaseException | None = None) -> None:
    if cause is None:
        raise StreamSnapshotError(code) from None
    raise StreamSnapshotError(code) from cause


def _require_quiescence(witness: Callable[[], bool]) -> None:
    if not callable(witness):
        _fail("lifecycle-work-data-quiescence-witness-required")
    try:
        quiet = witness()
    except LifecycleWorkExecutionError:
        raise
    except Exception as exc:
        _fail("lifecycle-work-data-quiescence-unavailable", exc)
    if quiet is not True:
        _fail("lifecycle-work-data-quiescence-active-writer")


def _canonical(value: Any) -> bytes:
    try:
        payload = (json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        _fail("lifecycle-work-data-snapshot-invalid", exc)
    if len(payload) > _MAX_INDEX_BYTES:
        _fail("lifecycle-work-data-snapshot-index-limit")
    return payload


def _record(value: DataPathRecord | None) -> dict[str, str] | None:
    if value is None:
        return None
    if type(value) is not DataPathRecord:
        _fail("lifecycle-work-data-snapshot-scope-invalid")
    return {
        "path": value.path, "backupClass": value.backup_class,
        "owner": value.owner, "uninstall": value.uninstall, "purge": value.purge,
    }


def _scope_index(scope: BoundDataScope) -> list[dict[str, Any]]:
    if type(scope) is not BoundDataScope or scope.operation_key not in {"backup", "restore"}:
        _fail("lifecycle-work-data-snapshot-scope-invalid")
    result: list[dict[str, Any]] = []
    path_count = 0
    for service in scope.services:
        if type(service) is not BoundServiceData:
            _fail("lifecycle-work-data-snapshot-scope-invalid")
        paths: list[dict[str, Any]] = []
        for bound in service.paths:
            path_count += 1
            if path_count > _MAX_PATHS:
                _fail("lifecycle-work-data-snapshot-path-limit")
            if type(bound) is not BoundDataPath:
                _fail("lifecycle-work-data-snapshot-scope-invalid")
            paths.append({
                "path": bound.path, "prior": _record(bound.prior),
                "selected": _record(bound.selected),
                "present": False, "rootMode": None,
                "rootAtimeNs": None, "rootMtimeNs": None, "entries": [],
            })
        result.append({
            "serviceId": service.service_id, "action": service.action,
            "selectedDefinitionSha256": service.selected_definition_sha256,
            "selectedDataSchemaVersion": service.selected_data_schema_version,
            "priorDefinitionSha256": service.prior_definition_sha256,
            "priorDataSchemaVersion": service.prior_data_schema_version,
            "paths": paths,
        })
    return result


def _safe_name(name: str) -> str:
    if not isinstance(name, str) or name in {"", ".", ".."} or "/" in name or "\\" in name or "\x00" in name:
        _fail("lifecycle-work-data-snapshot-path-invalid")
    try:
        length = len(name.encode("utf-8", errors="strict"))
    except UnicodeError as exc:
        _fail("lifecycle-work-data-snapshot-path-invalid", exc)
    if length > 255:
        _fail("lifecycle-work-data-snapshot-path-invalid")
    return name


def _time_ns(value: int) -> str:
    if type(value) is not int or not -(1 << 63) <= value < (1 << 63):
        _fail("lifecycle-work-data-snapshot-time-invalid")
    return str(value)


def _verified_time_ns(value: Any) -> int:
    if (
        not isinstance(value, str) or _TIME_NS_RE.fullmatch(value) is None
        or str(int(value)) != value
    ):
        _fail("lifecycle-work-data-snapshot-index-invalid")
    parsed = int(value)
    if not -(1 << 63) <= parsed < (1 << 63):
        _fail("lifecycle-work-data-snapshot-index-invalid")
    return parsed


def _snapshot_identity(info: os.stat_result) -> tuple[int, ...]:
    # O_NOATIME must prevent the backup itself changing user-visible access time.
    return _identity(info) + (info.st_atime_ns,)


def _source_directory_flags() -> int:
    if not hasattr(os, "O_NOATIME"):
        _fail("lifecycle-work-data-snapshot-platform-unsupported")
    return _directory_flags() | os.O_NOATIME


def _source_file_flags() -> int:
    if not hasattr(os, "O_NOATIME"):
        _fail("lifecycle-work-data-snapshot-platform-unsupported")
    return _file_read_flags() | os.O_NOATIME


def _check_extended_metadata(descriptor: int) -> None:
    if not callable(getattr(os, "listxattr", None)):
        _fail("lifecycle-work-data-snapshot-platform-unsupported")
    try:
        names = os.listxattr(descriptor)
    except OSError as exc:
        _fail("lifecycle-work-data-snapshot-metadata-unavailable", exc)
    if names:
        # Includes visible POSIX ACL, SELinux, capability, and user xattrs.
        _fail("lifecycle-work-data-snapshot-metadata-unsupported")


def _check_sparse_file(descriptor: int, info: os.stat_result) -> None:
    if info.st_size == 0:
        return
    if not all(hasattr(os, name) for name in ("SEEK_DATA", "SEEK_HOLE")):
        _fail("lifecycle-work-data-snapshot-platform-unsupported")
    if not isinstance(getattr(info, "st_blocks", None), int):
        _fail("lifecycle-work-data-snapshot-metadata-unavailable")
    if info.st_blocks * 512 < info.st_size:
        _fail("lifecycle-work-data-snapshot-sparse-unsupported")
    try:
        data = os.lseek(descriptor, 0, os.SEEK_DATA)
        if data != 0 or os.lseek(descriptor, data, os.SEEK_HOLE) < info.st_size:
            _fail("lifecycle-work-data-snapshot-sparse-unsupported")
        os.lseek(descriptor, 0, os.SEEK_SET)
    except OSError as exc:
        if exc.errno == errno.ENXIO:
            _fail("lifecycle-work-data-snapshot-sparse-unsupported", exc)
        _fail("lifecycle-work-data-snapshot-metadata-unavailable", exc)


class _DigestReader:
    """Bounded fileobj for tarfile.addfile; never stores source bytes."""

    def __init__(self, descriptor: int, length: int) -> None:
        self.descriptor = descriptor
        self.remaining = length
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        wanted = min(self.remaining, 64 * 1024) if size < 0 else min(size, self.remaining)
        if not wanted:
            return b""
        chunks: list[bytes] = []
        remaining = wanted
        while remaining:
            chunk = os.read(self.descriptor, min(remaining, 64 * 1024))
            if not chunk:
                _fail("lifecycle-work-data-snapshot-source-changed")
            chunks.append(chunk)
            self.digest.update(chunk)
            self.remaining -= len(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)


def _tar_info(name: str, *, mode: int, size: int, directory: bool) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name + "/" if directory else name)
    info.type = tarfile.DIRTYPE if directory else tarfile.REGTYPE
    info.mode = mode
    info.size = size
    info.uid = os.geteuid()
    info.gid = os.getegid()
    info.mtime = 0
    info.uname = ""
    info.gname = ""
    return info


def _capture_directory(
    archive: tarfile.TarFile, descriptor: int, *, prefix: str,
    archive_prefix: str, entries: list[dict[str, Any]], budget: _Budget, depth: int,
) -> None:
    if depth > _MAX_DEPTH:
        _fail("lifecycle-work-data-snapshot-depth-limit")
    before = os.fstat(descriptor)
    _validate_source_mode(before, directory=True)
    _check_extended_metadata(descriptor)
    try:
        names = sorted(_safe_name(name) for name in os.listdir(descriptor))
    except (OSError, UnicodeError) as exc:
        _fail("lifecycle-work-data-snapshot-read-failed", exc)
    if _snapshot_identity(os.fstat(descriptor)) != _snapshot_identity(before):
        _fail("lifecycle-work-data-snapshot-source-changed")
    for name in names:
        budget.entries += 1
        if budget.entries > _MAX_ENTRIES:
            _fail("lifecycle-work-data-snapshot-entry-limit")
        path = prefix + "/" + name if prefix else name
        if len(path.encode("utf-8", errors="strict")) > 1024:
            _fail("lifecycle-work-data-snapshot-path-invalid")
        try:
            observed = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(observed.st_mode):
                mode = _validate_source_mode(observed, directory=True)
                child: int | None = None
                try:
                    child = os.open(name, _source_directory_flags(), dir_fd=descriptor)
                    if _snapshot_identity(os.fstat(child)) != _snapshot_identity(observed):
                        _fail("lifecycle-work-data-snapshot-source-changed")
                    archive.addfile(_tar_info(archive_prefix + "/" + path, mode=mode, size=0, directory=True))
                    entries.append({"path": path, "type": "directory", "mode": mode,
                                    "atimeNs": _time_ns(observed.st_atime_ns),
                                    "mtimeNs": _time_ns(observed.st_mtime_ns)})
                    _capture_directory(archive, child, prefix=path, archive_prefix=archive_prefix,
                                       entries=entries, budget=budget, depth=depth + 1)
                finally:
                    _close_quietly(child)
            elif stat.S_ISREG(observed.st_mode):
                mode = _validate_source_mode(observed, directory=False)
                if observed.st_size < 0 or observed.st_size > _MAX_FILE_BYTES:
                    _fail("lifecycle-work-data-snapshot-size-limit")
                budget.files += 1
                budget.content_bytes += observed.st_size
                if budget.content_bytes > _MAX_TOTAL_BYTES:
                    _fail("lifecycle-work-data-snapshot-size-limit")
                child = None
                try:
                    child = os.open(name, _source_file_flags(), dir_fd=descriptor)
                    if _snapshot_identity(os.fstat(child)) != _snapshot_identity(observed):
                        _fail("lifecycle-work-data-snapshot-source-changed")
                    _check_extended_metadata(child)
                    _check_sparse_file(child, observed)
                    reader = _DigestReader(child, observed.st_size)
                    archive.addfile(_tar_info(archive_prefix + "/" + path, mode=mode,
                                              size=observed.st_size, directory=False), reader)
                    if reader.remaining or os.read(child, 1):
                        _fail("lifecycle-work-data-snapshot-source-changed")
                    after = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                    _check_extended_metadata(child)
                    if (_snapshot_identity(os.fstat(child)) != _snapshot_identity(observed)
                            or _snapshot_identity(after) != _snapshot_identity(observed)):
                        _fail("lifecycle-work-data-snapshot-source-changed")
                    entries.append({"path": path, "type": "file", "mode": mode,
                                    "size": observed.st_size, "sha256": reader.digest.hexdigest(),
                                    "atimeNs": _time_ns(observed.st_atime_ns),
                                    "mtimeNs": _time_ns(observed.st_mtime_ns)})
                finally:
                    _close_quietly(child)
            else:
                _fail("lifecycle-work-data-snapshot-special-file-denied")
        except (OSError, DataBackupRuntimeError, tarfile.TarError) as exc:
            _fail("lifecycle-work-data-snapshot-read-failed", exc)
    _check_extended_metadata(descriptor)
    if _snapshot_identity(os.fstat(descriptor)) != _snapshot_identity(before):
        _fail("lifecycle-work-data-snapshot-source-changed")
    # Some filesystems can report the same directory timestamps for two
    # mutations inside one clock tick. Recheck the namespace itself so a file
    # added or removed during the first listing cannot be silently omitted.
    try:
        if sorted(_safe_name(name) for name in os.listdir(descriptor)) != names:
            _fail("lifecycle-work-data-snapshot-source-changed")
    except (OSError, UnicodeError) as exc:
        _fail("lifecycle-work-data-snapshot-read-failed", exc)
    if _snapshot_identity(os.fstat(descriptor)) != _snapshot_identity(before):
        _fail("lifecycle-work-data-snapshot-source-changed")


def _archive_name(command: LifecycleWorkCommand) -> str:
    return f"{command.transaction_id}.{command.plan_hash}.tar"


def _backup_request_hash(command: LifecycleWorkCommand) -> str:
    """Derive the original backup hash from the fixed lifecycle request shape."""
    unsigned = {
        "schema": REQUEST_SCHEMA,
        "transactionId": command.transaction_id,
        "planHash": command.plan_hash,
        "operationKey": "backup",
        "serviceIds": list(command.service_ids),
        "payload": {"serviceIds": list(command.service_ids)},
    }
    encoded = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _snapshot_document(command: LifecycleWorkCommand, scope: BoundDataScope) -> dict[str, Any]:
    return {
        "schema": SNAPSHOT_SCHEMA,
        "transactionId": command.transaction_id,
        "planHash": command.plan_hash,
        "requestHash": command.request_hash if scope.operation_key == "backup" else _backup_request_hash(command),
        "services": _scope_index(scope),
    }


def _capture_path(
    archive: tarfile.TarFile, install_descriptor: int, path: dict[str, Any],
    *, archive_prefix: str, budget: _Budget,
) -> None:
    descriptor = _open_relative_directory(
        install_descriptor, tuple(path["path"].split("/")), missing_ok=True
    )
    if descriptor is None:
        return
    guarded: int | None = None
    try:
        guarded = os.open(".", _source_directory_flags(), dir_fd=descriptor)
        if _snapshot_identity(os.fstat(guarded)) != _snapshot_identity(os.fstat(descriptor)):
            _fail("lifecycle-work-data-snapshot-source-changed")
        _close_quietly(descriptor)
        descriptor = guarded
        guarded = None
        info = os.fstat(descriptor)
        path["present"] = True
        path["rootMode"] = _validate_source_mode(info, directory=True)
        path["rootAtimeNs"] = _time_ns(info.st_atime_ns)
        path["rootMtimeNs"] = _time_ns(info.st_mtime_ns)
        _capture_directory(archive, descriptor, prefix="", archive_prefix=archive_prefix,
                           entries=path["entries"], budget=budget, depth=0)
    finally:
        _close_quietly(guarded)
        _close_quietly(descriptor)


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("lifecycle-work-data-snapshot-index-invalid")
        result[key] = value
    return result


def _reject_number(_value: str) -> Any:
    _fail("lifecycle-work-data-snapshot-index-invalid")


def _index_document(raw: bytes) -> dict[str, Any]:
    if len(raw) > _MAX_INDEX_BYTES:
        _fail("lifecycle-work-data-snapshot-index-limit")
    try:
        value = json.loads(
            raw.decode("ascii", errors="strict"), object_pairs_hook=_unique_pairs,
            parse_float=_reject_number, parse_constant=_reject_number,
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        _fail("lifecycle-work-data-snapshot-index-invalid", exc)
    if not isinstance(value, dict) or _canonical(value) != raw:
        _fail("lifecycle-work-data-snapshot-index-invalid")
    return value


def _verify_archive(
    archive: tarfile.TarFile, expected: dict[str, Any], archive_sha256: str,
) -> tuple[StreamSnapshotReceipt, dict[str, Any]]:
    members = archive.getmembers()
    if not members or len(members) > _MAX_ENTRIES + 1:
        _fail("lifecycle-work-data-snapshot-index-invalid")
    index_member = members[-1]
    if (
        index_member.name != INDEX_MEMBER or not index_member.isfile()
        or index_member.size <= 0 or index_member.size > _MAX_INDEX_BYTES
        or index_member.mode != _SEALED_MODE
        or index_member.uid != os.geteuid() or index_member.gid != os.getegid()
    ):
        _fail("lifecycle-work-data-snapshot-index-invalid")
    index_stream = archive.extractfile(index_member)
    if index_stream is None:
        _fail("lifecycle-work-data-snapshot-index-invalid")
    raw = index_stream.read(_MAX_INDEX_BYTES + 1)
    if len(raw) != index_member.size:
        _fail("lifecycle-work-data-snapshot-index-invalid")
    document = _index_document(raw)
    if frozenset(document) != frozenset(expected) or any(
        document[key] != expected[key] for key in ("schema", "transactionId", "planHash", "requestHash")
    ):
        _fail("lifecycle-work-data-snapshot-index-invalid")
    actual_services = document["services"]
    expected_services = expected["services"]
    if not isinstance(actual_services, list) or len(actual_services) != len(expected_services):
        _fail("lifecycle-work-data-snapshot-index-invalid")
    expected_members: dict[str, dict[str, Any]] = {}
    file_count = 0
    content_bytes = 0
    entry_count = 0
    for service, baseline in zip(actual_services, expected_services):
        if not isinstance(service, dict) or frozenset(service) != frozenset(baseline):
            _fail("lifecycle-work-data-snapshot-index-invalid")
        if any(service[key] != baseline[key] for key in baseline if key != "paths"):
            _fail("lifecycle-work-data-snapshot-index-invalid")
        paths = service["paths"]
        if not isinstance(paths, list) or len(paths) != len(baseline["paths"]):
            _fail("lifecycle-work-data-snapshot-index-invalid")
        for index, (path, expected_path) in enumerate(zip(paths, baseline["paths"])):
            if not isinstance(path, dict) or frozenset(path) != frozenset(expected_path):
                _fail("lifecycle-work-data-snapshot-index-invalid")
            if any(path[key] != expected_path[key] for key in ("path", "prior", "selected")):
                _fail("lifecycle-work-data-snapshot-index-invalid")
            present = path["present"]
            mode = path["rootMode"]
            root_atime = path["rootAtimeNs"]
            root_mtime = path["rootMtimeNs"]
            entries = path["entries"]
            if type(present) is not bool or not isinstance(entries, list):
                _fail("lifecycle-work-data-snapshot-index-invalid")
            if present:
                if type(mode) is not int or mode & 0o700 != 0o700 or mode & 0o7022:
                    _fail("lifecycle-work-data-snapshot-index-invalid")
                _verified_time_ns(root_atime)
                _verified_time_ns(root_mtime)
            elif mode is not None or root_atime is not None or root_mtime is not None or entries:
                _fail("lifecycle-work-data-snapshot-index-invalid")
            for entry in entries:
                entry_count += 1
                if entry_count > _MAX_ENTRIES or not isinstance(entry, dict):
                    _fail("lifecycle-work-data-snapshot-index-invalid")
                relative = entry.get("path")
                if (
                    not isinstance(relative, str) or len(relative.encode("utf-8", errors="strict")) > 1024
                    or any(_safe_name(part) != part for part in relative.split("/"))
                ):
                    _fail("lifecycle-work-data-snapshot-index-invalid")
                name = f"payload/{service['serviceId']}/{index}/{relative}"
                if name in expected_members:
                    _fail("lifecycle-work-data-snapshot-index-invalid")
                kind = entry.get("type")
                entry_mode = entry.get("mode")
                if type(entry_mode) is not int or entry_mode & 0o7022:
                    _fail("lifecycle-work-data-snapshot-index-invalid")
                _verified_time_ns(entry.get("atimeNs"))
                _verified_time_ns(entry.get("mtimeNs"))
                if kind == "directory":
                    if (frozenset(entry) != frozenset({"path", "type", "mode", "atimeNs", "mtimeNs"})
                            or entry_mode & 0o700 != 0o700):
                        _fail("lifecycle-work-data-snapshot-index-invalid")
                elif kind == "file":
                    if (
                        frozenset(entry) != frozenset({"path", "type", "mode", "size", "sha256", "atimeNs", "mtimeNs"})
                        or entry_mode & 0o400 != 0o400
                        or type(entry.get("size")) is not int
                        or not 0 <= entry["size"] <= _MAX_FILE_BYTES
                        or not isinstance(entry.get("sha256"), str)
                        or _SHA256_RE.fullmatch(entry["sha256"]) is None
                    ):
                        _fail("lifecycle-work-data-snapshot-index-invalid")
                    file_count += 1
                    content_bytes += entry["size"]
                    if content_bytes > _MAX_TOTAL_BYTES:
                        _fail("lifecycle-work-data-snapshot-size-limit")
                else:
                    _fail("lifecycle-work-data-snapshot-index-invalid")
                expected_members[name] = entry
    if len(members) != len(expected_members) + 1:
        _fail("lifecycle-work-data-snapshot-index-invalid")
    seen: set[str] = set()
    for member in members[:-1]:
        name = member.name.rstrip("/")
        entry = expected_members.get(name)
        if name in seen or entry is None:
            _fail("lifecycle-work-data-snapshot-index-invalid")
        seen.add(name)
        if (
            member.mode != entry["mode"] or member.uid != os.geteuid()
            or member.gid != os.getegid() or member.mtime != 0
            or member.uname or member.gname
            or getattr(member, "sparse", None)
        ):
            _fail("lifecycle-work-data-snapshot-index-invalid")
        if entry["type"] == "directory":
            if not member.isdir() or member.size != 0:
                _fail("lifecycle-work-data-snapshot-index-invalid")
            continue
        if not member.isfile() or member.size != entry["size"]:
            _fail("lifecycle-work-data-snapshot-index-invalid")
        stream = archive.extractfile(member)
        if stream is None:
            _fail("lifecycle-work-data-snapshot-index-invalid")
        digest = hashlib.sha256()
        remaining = member.size
        while remaining:
            chunk = stream.read(min(remaining, 64 * 1024))
            if not chunk:
                _fail("lifecycle-work-data-snapshot-content-invalid")
            digest.update(chunk)
            remaining -= len(chunk)
        if digest.hexdigest() != entry["sha256"]:
            _fail("lifecycle-work-data-snapshot-content-invalid")
    return (
        StreamSnapshotReceipt(
            archive_sha256=archive_sha256,
            index_sha256=hashlib.sha256(raw).hexdigest(),
            file_count=file_count, content_bytes=content_bytes,
        ),
        document,
    )


def _stabilize_published_link(
    root: int, descriptor: int, info: os.stat_result, command: LifecycleWorkCommand,
) -> None:
    """Remove only a proved in-root temp link left after archive publication."""
    if info.st_nlink == 1:
        return
    if info.st_nlink != 2:
        _fail("lifecycle-work-data-snapshot-custody-invalid")
    prefix = f".tmp-{command.transaction_id}-"
    try:
        names = os.listdir(root)
        if len(names) > 100000:
            _fail("lifecycle-work-data-snapshot-custody-invalid")
        matches: list[str] = []
        for name in names:
            if not name.startswith(prefix) or not name.endswith(".tar"):
                continue
            token = name[len(prefix):-4]
            if len(token) != 32 or any(character not in "0123456789abcdef" for character in token):
                continue
            candidate = os.stat(name, dir_fd=root, follow_symlinks=False)
            if (
                stat.S_ISREG(candidate.st_mode)
                and (candidate.st_dev, candidate.st_ino) == (info.st_dev, info.st_ino)
                and candidate.st_nlink == 2
                and stat.S_IMODE(candidate.st_mode) == _SEALED_MODE
            ):
                matches.append(name)
        if len(matches) != 1:
            _fail("lifecycle-work-data-snapshot-custody-invalid")
        os.unlink(matches[0], dir_fd=root)
        os.fsync(root)
        if os.fstat(descriptor).st_nlink != 1:
            _fail("lifecycle-work-data-snapshot-custody-invalid")
    except OSError as exc:
        _fail("lifecycle-work-data-snapshot-custody-invalid", exc)


class StreamSnapshotStore:
    """First-write-wins tar snapshots; no restore or production dispatch."""

    def __init__(self, install_dir: Path, data_dir: Path, backup_root: Path) -> None:
        _validate_platform()
        self.install_dir = install_dir
        self.data_dir = data_dir
        self.backup_root = backup_root
        install = _open_absolute_directory(install_dir, private=False)
        root = _open_absolute_directory(backup_root, private=True)
        _close_quietly(install)
        _close_quietly(root)

    def backup(self, command: LifecycleWorkCommand, *,
               quiescence: Callable[[], bool] | None = None) -> StreamSnapshotReceipt:
        scope = bind_data_scope(command)
        if scope.operation_key != "backup":
            _fail("lifecycle-work-data-snapshot-scope-invalid")
        root = _open_absolute_directory(self.backup_root, private=True)
        install: int | None = None
        temp: int | None = None
        temporary_name: str | None = None
        try:
            name = _archive_name(command)
            try:
                os.stat(name, dir_fd=root, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                return self.verify(command)
            verify_installed_prior_data(scope, install_dir=self.install_dir, data_dir=self.data_dir)
            install = _open_absolute_directory(self.install_dir, private=False)
            if quiescence is not None:
                _require_quiescence(quiescence)
            temporary_name = f".tmp-{command.transaction_id}-{secrets.token_hex(16)}.tar"
            temp = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                _TEMP_MODE, dir_fd=root,
            )
            document = _snapshot_document(command, scope)
            budget = _Budget()
            with os.fdopen(os.dup(temp), "wb", closefd=True) as writer:
                with tarfile.open(fileobj=writer, mode="w", format=tarfile.PAX_FORMAT) as archive:
                    for service in document["services"]:
                        for index, path in enumerate(service["paths"]):
                            _capture_path(
                                archive, install, path,
                                archive_prefix=f"payload/{service['serviceId']}/{index}", budget=budget,
                            )
                    index_bytes = _canonical(document)
                    archive.addfile(
                        _tar_info(INDEX_MEMBER, mode=_SEALED_MODE, size=len(index_bytes), directory=False),
                        io.BytesIO(index_bytes),
                    )
            # A changed Docker/lease observation must refuse before the
            # temporary inode can be sealed or linked into durable state.
            if quiescence is not None:
                _require_quiescence(quiescence)
            if os.fstat(temp).st_size > _MAX_ARCHIVE_BYTES:
                _fail("lifecycle-work-data-snapshot-size-limit")
            os.fchmod(temp, _SEALED_MODE)
            os.fsync(temp)
            try:
                os.link(temporary_name, name, src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)
            except FileExistsError:
                os.unlink(temporary_name, dir_fd=root)
                temporary_name = None
                os.fsync(root)
                return self.verify(command)
            os.fsync(root)
            os.unlink(temporary_name, dir_fd=root)
            temporary_name = None
            os.fsync(root)
            receipt = self.verify(command)
            if receipt.file_count != budget.files or receipt.content_bytes != budget.content_bytes:
                _fail("lifecycle-work-data-snapshot-readback-invalid")
            return receipt
        except (OSError, tarfile.TarError) as exc:
            _fail("lifecycle-work-data-snapshot-write-failed", exc)
        finally:
            if temporary_name is not None and temp is not None:
                try:
                    temporary = os.stat(temporary_name, dir_fd=root, follow_symlinks=False)
                    opened = os.fstat(temp)
                    if (
                        (temporary.st_dev, temporary.st_ino) == (opened.st_dev, opened.st_ino)
                        and temporary.st_nlink == 1
                    ):
                        os.unlink(temporary_name, dir_fd=root)
                        os.fsync(root)
                except OSError:
                    # A failed effect remains failed; never infer publication
                    # or remove a different inode during cleanup.
                    pass
            _close_quietly(temp)
            _close_quietly(install)
            _close_quietly(root)

    @contextmanager
    def open_verified(self, command: LifecycleWorkCommand) -> Iterator[
        tuple[tarfile.TarFile, dict[str, Any], StreamSnapshotReceipt]
    ]:
        """Hold the exact fully verified sealed inode while a restore stages it."""
        scope = bind_data_scope(command)
        with ExitStack() as stack:
            root = _open_absolute_directory(self.backup_root, private=True)
            stack.callback(_close_quietly, root)
            try:
                descriptor = os.open(
                    _archive_name(command), os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=root,
                )
                stack.callback(_close_quietly, descriptor)
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_gid != os.getegid() or info.st_nlink not in {1, 2}
                    or stat.S_IMODE(info.st_mode) != _SEALED_MODE
                    or not 0 < info.st_size <= _MAX_ARCHIVE_BYTES
                ):
                    _fail("lifecycle-work-data-snapshot-custody-invalid")
                archive_digest = hashlib.sha256()
                remaining = info.st_size
                while remaining:
                    chunk = os.read(descriptor, min(remaining, 64 * 1024))
                    if not chunk:
                        _fail("lifecycle-work-data-snapshot-readback-invalid")
                    archive_digest.update(chunk)
                    remaining -= len(chunk)
                if os.read(descriptor, 1) or _identity(os.fstat(descriptor)) != _identity(info):
                    _fail("lifecycle-work-data-snapshot-readback-invalid")
                os.lseek(descriptor, 0, os.SEEK_SET)
                reader = stack.enter_context(os.fdopen(os.dup(descriptor), "rb", closefd=True))
                archive = stack.enter_context(tarfile.open(fileobj=reader, mode="r:"))
                receipt, document = _verify_archive(
                    archive, _snapshot_document(command, scope), archive_digest.hexdigest(),
                )
                _stabilize_published_link(root, descriptor, info, command)
            except (OSError, tarfile.TarError) as exc:
                _fail("lifecycle-work-data-snapshot-readback-invalid", exc)
            yield archive, document, receipt

    def verify(self, command: LifecycleWorkCommand) -> StreamSnapshotReceipt:
        with self.open_verified(command) as (_archive, _document, receipt):
            return receipt


__all__ = ["SNAPSHOT_SCHEMA", "StreamSnapshotError", "StreamSnapshotReceipt", "StreamSnapshotStore"]
