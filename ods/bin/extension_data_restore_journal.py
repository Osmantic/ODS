"""Private, first-write-wins intent custody for future generic data restore.

The caller must hold a fully verified StreamSnapshotStore archive lease before
beginning an intent. This module does not extract data, rename live paths,
select a host effect, resume services, or discard staged/quarantined data.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from pathlib import Path
from typing import Any

from extension_data_backup_runtime import (
    DataBackupRuntimeError,
    _close_quietly,
    _identity,
    _open_absolute_directory,
    _open_relative_directory,
    _safe_relative_parts,
    _validate_platform,
)
from extension_data_scope_contract import bind_data_scope
from extension_data_stream_snapshot import StreamSnapshotReceipt
from extension_lifecycle_work import LifecycleWorkCommand, LifecycleWorkExecutionError


JOURNAL_SCHEMA = "ods.extension-data-restore-intent.v1"
_MAX_RECORD_BYTES = 16 * 1024
_TEMP_MODE = 0o600
_SEALED_MODE = 0o400
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_UNSIGNED_RE = re.compile(r"^(?:0|[1-9][0-9]{0,19})$")
_SIGNED_RE = re.compile(r"^-?(?:0|[1-9][0-9]{0,18})$")
_TARGET_KEYS = frozenset({"dev", "ino", "uid", "gid", "mode", "mtimeNs", "ctimeNs"})


class RestoreJournalError(LifecycleWorkExecutionError):
    """Value-free refusal of invalid journal custody or target drift."""


def _fail(code: str, cause: BaseException | None = None) -> None:
    if cause is None:
        raise RestoreJournalError(code) from None
    raise RestoreJournalError(code) from cause


def _canonical(value: Any) -> bytes:
    try:
        raw = (json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                          separators=(",", ":")) + "\n").encode("ascii")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        _fail("lifecycle-work-data-restore-journal-invalid", exc)
    if not 0 < len(raw) <= _MAX_RECORD_BYTES:
        _fail("lifecycle-work-data-restore-journal-size-limit")
    return raw


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("lifecycle-work-data-restore-journal-invalid")
        result[key] = value
    return result


def _parse(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("ascii", errors="strict"), object_pairs_hook=_unique_pairs)
    except (UnicodeError, ValueError, RecursionError) as exc:
        _fail("lifecycle-work-data-restore-journal-invalid", exc)
    if not isinstance(value, dict) or _canonical(value) != raw:
        _fail("lifecycle-work-data-restore-journal-invalid")
    return value


def _decimal(value: Any, *, signed: bool) -> None:
    pattern = _SIGNED_RE if signed else _UNSIGNED_RE
    if not isinstance(value, str) or pattern.fullmatch(value) is None or str(int(value)) != value:
        _fail("lifecycle-work-data-restore-journal-invalid")
    number = int(value)
    if signed and not -(1 << 63) <= number < (1 << 63):
        _fail("lifecycle-work-data-restore-journal-invalid")
    if not signed and not 0 <= number < (1 << 64):
        _fail("lifecycle-work-data-restore-journal-invalid")


def _target_ref(info: os.stat_result, parent: os.stat_result) -> dict[str, Any]:
    mode = stat.S_IMODE(info.st_mode)
    if (
        not stat.S_ISDIR(info.st_mode) or info.st_dev != parent.st_dev
        or info.st_uid != os.geteuid() or info.st_gid != os.getegid()
        or mode & 0o700 != 0o700 or mode & 0o7022
    ):
        _fail("lifecycle-work-data-restore-target-unsafe")
    return {
        "dev": str(info.st_dev), "ino": str(info.st_ino),
        "uid": str(info.st_uid), "gid": str(info.st_gid), "mode": mode,
        "mtimeNs": str(info.st_mtime_ns), "ctimeNs": str(info.st_ctime_ns),
    }


def _validate_target_ref(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict) or frozenset(value) != _TARGET_KEYS:
        _fail("lifecycle-work-data-restore-journal-invalid")
    for field in ("dev", "ino", "uid", "gid"):
        _decimal(value[field], signed=False)
    for field in ("mtimeNs", "ctimeNs"):
        _decimal(value[field], signed=True)
    if (
        type(value["mode"]) is not int or not 0 <= value["mode"] <= 0o7777
        or value["mode"] & 0o700 != 0o700 or value["mode"] & 0o7022
    ):
        _fail("lifecycle-work-data-restore-journal-invalid")


def _names(command: LifecycleWorkCommand, service_id: str, index: int, path: str) -> tuple[str, str, str, str]:
    seed = _canonical({
        "transactionId": command.transaction_id, "planHash": command.plan_hash,
        "serviceId": service_id, "pathIndex": index, "path": path,
    })
    key = hashlib.sha256(seed).hexdigest()
    return (key, f"r-{key}.json", f".ods-restore-stage-{key}", f".ods-restore-quarantine-{key}")


def _stabilize_link(root: int, name: str, descriptor: int, info: os.stat_result) -> None:
    if info.st_nlink == 1:
        return
    if info.st_nlink != 2:
        _fail("lifecycle-work-data-restore-journal-custody-invalid")
    prefix = f".tmp-{name[:-5]}-"
    try:
        names = os.listdir(root)
        if len(names) > 100000:
            _fail("lifecycle-work-data-restore-journal-custody-invalid")
        matches: list[str] = []
        for candidate_name in names:
            if not candidate_name.startswith(prefix) or not candidate_name.endswith(".json"):
                continue
            token = candidate_name[len(prefix):-5]
            if len(token) != 32 or any(character not in "0123456789abcdef" for character in token):
                continue
            candidate = os.stat(candidate_name, dir_fd=root, follow_symlinks=False)
            if (
                stat.S_ISREG(candidate.st_mode) and candidate.st_uid == os.geteuid()
                and candidate.st_gid == os.getegid() and candidate.st_nlink == 2
                and stat.S_IMODE(candidate.st_mode) == _SEALED_MODE
                and (candidate.st_dev, candidate.st_ino) == (info.st_dev, info.st_ino)
            ):
                matches.append(candidate_name)
        if len(matches) != 1:
            _fail("lifecycle-work-data-restore-journal-custody-invalid")
        os.unlink(matches[0], dir_fd=root)
        os.fsync(root)
        if os.fstat(descriptor).st_nlink != 1:
            _fail("lifecycle-work-data-restore-journal-custody-invalid")
    except OSError as exc:
        _fail("lifecycle-work-data-restore-journal-custody-invalid", exc)


def _read(root: int, name: str) -> tuple[dict[str, Any], bytes] | None:
    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                                 dir_fd=root)
        except FileNotFoundError:
            return None
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_gid != os.getegid() or info.st_nlink not in {1, 2}
            or stat.S_IMODE(info.st_mode) != _SEALED_MODE
            or not 0 < info.st_size <= _MAX_RECORD_BYTES
        ):
            _fail("lifecycle-work-data-restore-journal-custody-invalid")
        chunks: list[bytes] = []
        remaining = info.st_size
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                _fail("lifecycle-work-data-restore-journal-custody-invalid")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1) or _identity(os.fstat(descriptor)) != _identity(info):
            _fail("lifecycle-work-data-restore-journal-custody-invalid")
        raw = b"".join(chunks)
        value = _parse(raw)
        _stabilize_link(root, name, descriptor, info)
        return value, raw
    except OSError as exc:
        _fail("lifecycle-work-data-restore-journal-custody-invalid", exc)
    finally:
        _close_quietly(descriptor)


def _check_temp_name(root: int, name: str, descriptor: int, *, links: int) -> None:
    observed = os.stat(name, dir_fd=root, follow_symlinks=False)
    opened = os.fstat(descriptor)
    if (
        not stat.S_ISREG(observed.st_mode) or observed.st_uid != os.geteuid()
        or observed.st_gid != os.getegid() or stat.S_IMODE(observed.st_mode) != _SEALED_MODE
        or (observed.st_dev, observed.st_ino) != (opened.st_dev, opened.st_ino)
        or observed.st_nlink != links or opened.st_nlink != links
    ):
        _fail("lifecycle-work-data-restore-journal-custody-invalid")


def _publish(root: int, name: str, raw: bytes) -> tuple[dict[str, Any], bytes]:
    temporary_name = f".tmp-{name[:-5]}-{secrets.token_hex(16)}.json"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0), _TEMP_MODE, dir_fd=root,
        )
        remaining = memoryview(raw)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                _fail("lifecycle-work-data-restore-journal-write-failed")
            remaining = remaining[written:]
        os.fchmod(descriptor, _SEALED_MODE)
        os.fsync(descriptor)
        _check_temp_name(root, temporary_name, descriptor, links=1)
        try:
            os.link(temporary_name, name, src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)
        except FileExistsError:
            existing = _read(root, name)
            if existing is None or existing[1] != raw:
                _fail("lifecycle-work-data-restore-intent-mismatch")
            return existing
        os.fsync(root)
        _check_temp_name(root, temporary_name, descriptor, links=2)
        os.unlink(temporary_name, dir_fd=root)
        os.fsync(root)
        return _parse(raw), raw
    except OSError as exc:
        _fail("lifecycle-work-data-restore-journal-write-failed", exc)
    finally:
        if descriptor is not None:
            try:
                temporary = os.stat(temporary_name, dir_fd=root, follow_symlinks=False)
                opened = os.fstat(descriptor)
                if (temporary.st_dev, temporary.st_ino) == (opened.st_dev, opened.st_ino) and opened.st_nlink == 1:
                    os.unlink(temporary_name, dir_fd=root)
                    os.fsync(root)
            except OSError:
                # Leave ambiguous links for exact, bounded recovery at read.
                pass
        _close_quietly(descriptor)


class RestoreIntentJournal:
    """Metadata-only intent journal; no live restore effect is selected."""

    def __init__(self, install_dir: Path, journal_root: Path) -> None:
        _validate_platform()
        self.install_dir = install_dir
        self.journal_root = journal_root
        install = _open_absolute_directory(install_dir, private=False)
        root = _open_absolute_directory(journal_root, private=True)
        _close_quietly(install)
        _close_quietly(root)

    def _observe_target(self, path: str, *, require_absent: tuple[str, ...] = ()) -> dict[str, Any] | None:
        install: int | None = None
        parent: int | None = None
        try:
            install = _open_absolute_directory(self.install_dir, private=False)
            parts = _safe_relative_parts(path)
            parent = _open_relative_directory(install, parts[:-1], missing_ok=False)
            assert parent is not None
            parent_info = os.fstat(parent)
            for name in require_absent:
                try:
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                _fail("lifecycle-work-data-restore-transient-collision")
            try:
                target = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return None
            return _target_ref(target, parent_info)
        except (OSError, DataBackupRuntimeError) as exc:
            _fail("lifecycle-work-data-restore-target-unavailable", exc)
        finally:
            _close_quietly(parent)
            _close_quietly(install)

    def begin(
        self, command: LifecycleWorkCommand, receipt: StreamSnapshotReceipt,
        service_id: str, index: int, path_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Publish one approved path intent; replay adopts its original target ref."""
        scope = bind_data_scope(command)
        if scope.operation_key != "restore" or type(receipt) is not StreamSnapshotReceipt:
            _fail("lifecycle-work-data-restore-journal-scope-invalid")
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            _fail("lifecycle-work-data-restore-journal-scope-invalid")
        service = next((item for item in scope.services if item.service_id == service_id), None)
        if service is None or index >= len(service.paths):
            _fail("lifecycle-work-data-restore-journal-scope-invalid")
        path = service.paths[index].path
        if (
            not isinstance(path_state, dict) or path_state.get("path") != path
            or type(path_state.get("present")) is not bool
            or _HASH_RE.fullmatch(receipt.archive_sha256) is None
            or _HASH_RE.fullmatch(receipt.index_sha256) is None
        ):
            _fail("lifecycle-work-data-restore-journal-scope-invalid")
        key, name, stage_name, quarantine_name = _names(command, service_id, index, path)
        baseline: dict[str, Any] = {
            "schema": JOURNAL_SCHEMA, "key": key,
            "transactionId": command.transaction_id, "planHash": command.plan_hash,
            "serviceId": service_id, "pathIndex": index, "path": path,
            "archiveSha256": receipt.archive_sha256, "indexSha256": receipt.index_sha256,
            "sourcePresent": path_state["present"],
            "stageName": stage_name, "quarantineName": quarantine_name,
        }
        root = _open_absolute_directory(self.journal_root, private=True)
        try:
            existing = _read(root, name)
            if existing is not None:
                document = existing[0]
                if frozenset(document) != frozenset(baseline) | {"targetBefore"} or any(
                    document.get(field) != value for field, value in baseline.items()
                ):
                    _fail("lifecycle-work-data-restore-intent-mismatch")
                _validate_target_ref(document.get("targetBefore"))
                return document
            baseline["targetBefore"] = self._observe_target(
                path, require_absent=(stage_name, quarantine_name),
            )
            return _publish(root, name, _canonical(baseline))[0]
        finally:
            _close_quietly(root)

    def expect_original_target(
        self, command: LifecycleWorkCommand, receipt: StreamSnapshotReceipt,
        service_id: str, index: int, path_state: dict[str, Any],
    ) -> None:
        """Refuse drift after intent; later replay handles proven rename states."""
        intent = self.begin(command, receipt, service_id, index, path_state)
        _validate_target_ref(intent.get("targetBefore"))
        if self._observe_target(intent["path"]) != intent["targetBefore"]:
            _fail("lifecycle-work-data-restore-target-drift")


__all__ = ["JOURNAL_SCHEMA", "RestoreJournalError", "RestoreIntentJournal"]
