"""Immutable data backup and restore for the first Assistant First canary.

This module grants exactly two plan-bound host effects for the bundled
SearXNG Manifest v2 canary: snapshot ``config/searxng`` before configuration
and restore that first snapshot while reconciling a failed transaction.  It
does not accept caller-provided paths, shell text, container identifiers, or
secret values.

Snapshots are owner-private, immutable, canonical JSON files.  Source and
restore walks are descriptor-relative and reject links, special files,
foreign ownership, unsafe modes, concurrent mutation, and oversized trees.
An absent source path is recorded explicitly so rollback can remove only the
exact canary path created by the failed transaction.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from extension_image_artifact_runtime import (
    CANARY_COMPOSE_SHA256,
    CANARY_DATA_SCHEMA_VERSION,
    CANARY_DEFINITION_SHA256,
    CANARY_IMAGE_DIGEST,
    CANARY_IMAGE_DOWNLOAD_BYTES,
    CANARY_IMAGE_REFERENCE,
    CANARY_MANIFEST_SCHEMA,
    CANARY_SERVICE_ID,
    CANARY_VERSION,
)
from extension_lifecycle_plan import (
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
    PlannedImage,
    PlannedOperation,
)
from extension_lifecycle_work import (
    LifecycleWorkCommand,
    LifecycleWorkError,
    LifecycleWorkExecutionError,
    LifecycleWorkStartedObservation,
    LifecycleWorkValidationError,
)

BACKUP_SCHEMA = "ods.extension-data-backup.v1"
BACKUP_EVIDENCE_SCHEMA = "ods.extension-data-backup-evidence.v1"
CANARY_DATA_PATH = "config/searxng"
CANARY_DATA_RECORD = {
    "path": CANARY_DATA_PATH,
    "backupClass": "required",
    "owner": "user",
    "uninstall": "preserve",
    "purge": "separate-approval",
}

_ACTIONS = frozenset({"install", "enable", "repair", "update"})
_ROOT_MODE = 0o700
_TEMP_MODE = 0o600
_PUBLISHED_MODE = 0o400
_MAX_ENTRIES = 1024
_MAX_DEPTH = 16
_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_TOTAL_BYTES = 8 * 1024 * 1024
_MAX_SNAPSHOT_BYTES = 12 * 1024 * 1024
_TRANSACTION_ID_RE = re.compile(r"^txn-[0-9a-f]{24}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_TEMP_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_PlanLoader = Callable[[LifecycleWorkCommand], LifecycleWorkCommand]


class DataBackupRuntimeError(LifecycleWorkExecutionError):
    """Stable, value-free failure after a data operation was accepted."""


@dataclass(frozen=True)
class _BoundData:
    definition_sha256: str
    path: str = CANARY_DATA_PATH


def _validation_error(code: str) -> None:
    raise LifecycleWorkValidationError(code) from None


def _execution_error(code: str, cause: BaseException | None = None) -> None:
    if cause is None:
        raise DataBackupRuntimeError(code) from None
    raise DataBackupRuntimeError(code) from cause


def _canonical_json(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        _execution_error("lifecycle-work-data-backup-invalid", exc)


class _DuplicateKey(ValueError):
    pass


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _reject_number(_value: str) -> Any:
    raise ValueError("non-integer-number")


def _parse_canonical_json(raw: bytes, *, code: str) -> Any:
    try:
        value = json.loads(
            raw.decode("ascii", errors="strict"),
            object_pairs_hook=_no_duplicate_keys,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, _DuplicateKey) as exc:
        _execution_error(code, exc)
    if _canonical_json(value) != raw:
        _execution_error(code)
    return value


def _parse_definition_document(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_no_duplicate_keys,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, _DuplicateKey):
        _validation_error("lifecycle-work-plan-mismatch")
    if not isinstance(value, dict):
        _validation_error("lifecycle-work-plan-mismatch")
    try:
        canonical = (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError):
        _validation_error("lifecycle-work-plan-mismatch")
    if canonical != raw:
        _validation_error("lifecycle-work-plan-mismatch")
    return value


def _validate_bound_command(command: Any, operation_key: str) -> _BoundData:
    """Re-prove the exact SearXNG data contract before any filesystem effect."""

    if type(command) is not LifecycleWorkCommand:
        _validation_error("lifecycle-work-command-invalid")
    if (
        not isinstance(command.transaction_id, str)
        or _TRANSACTION_ID_RE.fullmatch(command.transaction_id) is None
        or not isinstance(command.plan_hash, str)
        or _HASH_RE.fullmatch(command.plan_hash) is None
        or not isinstance(command.request_hash, str)
        or _HASH_RE.fullmatch(command.request_hash) is None
    ):
        _validation_error("lifecycle-work-command-invalid")
    if command.operation_key != operation_key:
        _validation_error("lifecycle-work-operation-mismatch")
    if command.service_ids != (CANARY_SERVICE_ID,):
        _validation_error("lifecycle-work-data-canary-denied")
    if command.payload != {"serviceIds": [CANARY_SERVICE_ID]}:
        _validation_error("lifecycle-work-plan-mismatch")

    expected_state = "configuring" if operation_key == "backup" else "reconciling"
    material = command.plan_material
    if (
        type(material) is not LifecyclePlanMaterial
        or material.schema != PLAN_MATERIAL_SCHEMA
        or material.transaction_id != command.transaction_id
        or material.plan_hash != command.plan_hash
        or material.state != expected_state
        or type(material.operations) is not tuple
        or type(material.definitions) is not tuple
        or len(material.operations) != 1
        or len(material.definitions) != 1
    ):
        _validation_error("lifecycle-work-plan-mismatch")

    operation = material.operations[0]
    definition = material.definitions[0]
    if (
        type(operation) is not PlannedOperation
        or type(definition) is not PlannedDefinition
        or operation.service_id != CANARY_SERVICE_ID
        or definition.service_id != CANARY_SERVICE_ID
        or operation.action not in _ACTIONS
    ):
        _validation_error("lifecycle-work-plan-mismatch")

    if (
        definition.service_type != "docker"
        or definition.manifest_schema_version != CANARY_MANIFEST_SCHEMA
        or definition.version != CANARY_VERSION
        or definition.data_schema_version != CANARY_DATA_SCHEMA_VERSION
        or definition.definition_source != "builtin"
        or definition.definition_sha256 != CANARY_DEFINITION_SHA256
        or definition.compose_sha256 != CANARY_COMPOSE_SHA256
        or definition.compose_file != "compose.yaml"
        or type(definition.images) is not tuple
        or len(definition.images) != 1
        or type(definition.builds) is not tuple
        or definition.builds
    ):
        _validation_error("lifecycle-work-data-canary-denied")
    image = definition.images[0]
    if (
        type(image) is not PlannedImage
        or image.reference != CANARY_IMAGE_REFERENCE
        or image.digest != CANARY_IMAGE_DIGEST
        or image.download_bytes != CANARY_IMAGE_DOWNLOAD_BYTES
        or isinstance(image.download_bytes, bool)
    ):
        _validation_error("lifecycle-work-data-canary-denied")

    document = _parse_definition_document(definition.canonical_document)
    if document.get("data") != [CANARY_DATA_RECORD]:
        _validation_error("lifecycle-work-data-canary-denied")
    return _BoundData(definition_sha256=definition.definition_sha256)


def _validate_loaded_command(
    original: LifecycleWorkCommand, loaded: Any
) -> LifecycleWorkCommand:
    if (
        type(loaded) is not LifecycleWorkCommand
        or loaded.plan_material is None
        or any(
            getattr(loaded, field) != getattr(original, field)
            for field in (
                "transaction_id",
                "plan_hash",
                "operation_key",
                "request_hash",
                "service_ids",
                "payload",
                "timeout_seconds",
            )
        )
    ):
        _validation_error("lifecycle-work-plan-mismatch")
    return loaded


def _validate_platform() -> None:
    required_dir_fd = (os.open, os.stat, os.unlink, os.mkdir, os.rmdir, os.link)
    if (
        os.name != "posix"
        or not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW"))
        or any(function not in os.supports_dir_fd for function in required_dir_fd)
        or os.stat not in os.supports_follow_symlinks
        or os.link not in os.supports_follow_symlinks
        or os.listdir not in os.supports_fd
        or not callable(getattr(os, "fsync", None))
        or not callable(getattr(os, "fchmod", None))
    ):
        _execution_error("lifecycle-work-data-platform-unsupported")


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _file_read_flags() -> int:
    return (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )


def _close_quietly(descriptor: int | None) -> None:
    if descriptor is None:
        return
    try:
        os.close(descriptor)
    except OSError:
        pass


def _root_components(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Path) or not value.is_absolute():
        _execution_error("lifecycle-work-data-root-invalid")
    parts = value.parts
    if not parts or parts[0] != "/" or any(part in {"", ".", ".."} for part in parts[1:]):
        _execution_error("lifecycle-work-data-root-invalid")
    return tuple(parts[1:])


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _check_owned_directory(info: os.stat_result, *, private: bool) -> None:
    mode = stat.S_IMODE(info.st_mode)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_gid != os.getegid()
        or (private and mode != _ROOT_MODE)
        or (not private and (mode & 0o700) != 0o700)
        or mode & 0o022
    ):
        _execution_error("lifecycle-work-data-custody-invalid")


def _open_absolute_directory(path: Path, *, private: bool) -> int:
    parts = _root_components(path)
    descriptor = os.open("/", _directory_flags())
    try:
        for component in parts:
            child = os.open(component, _directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        _check_owned_directory(os.fstat(descriptor), private=private)
        return descriptor
    except DataBackupRuntimeError:
        _close_quietly(descriptor)
        raise
    except OSError as exc:
        _close_quietly(descriptor)
        _execution_error("lifecycle-work-data-custody-invalid", exc)


def _safe_relative_parts(value: str) -> tuple[str, ...]:
    if (
        not isinstance(value, str)
        or not value
        or value.startswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        _execution_error("lifecycle-work-data-path-invalid")
    return tuple(value.split("/"))


def _open_relative_directory(
    root_descriptor: int,
    parts: tuple[str, ...],
    *,
    missing_ok: bool,
) -> int | None:
    descriptor = os.dup(root_descriptor)
    try:
        for component in parts:
            try:
                child = os.open(component, _directory_flags(), dir_fd=descriptor)
            except FileNotFoundError:
                if missing_ok:
                    _close_quietly(descriptor)
                    return None
                raise
            os.close(descriptor)
            descriptor = child
            _check_owned_directory(os.fstat(descriptor), private=False)
        return descriptor
    except DataBackupRuntimeError:
        _close_quietly(descriptor)
        raise
    except OSError as exc:
        _close_quietly(descriptor)
        _execution_error("lifecycle-work-data-path-unavailable", exc)


def _validate_name(name: Any) -> str:
    if (
        not isinstance(name, str)
        or name in {"", ".", ".."}
        or "/" in name
        or "\x00" in name
        or len(name.encode("utf-8", errors="strict")) > 255
    ):
        _execution_error("lifecycle-work-data-path-invalid")
    return name


def _validate_source_mode(info: os.stat_result, *, directory: bool) -> int:
    mode = stat.S_IMODE(info.st_mode)
    expected_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if (
        not expected_type
        or info.st_uid != os.geteuid()
        or info.st_gid != os.getegid()
        or mode & 0o022
        or mode & 0o7000
        or (directory and (mode & 0o700) != 0o700)
        or (not directory and (mode & 0o400) == 0)
        or (not directory and info.st_nlink != 1)
    ):
        _execution_error("lifecycle-work-data-custody-invalid")
    return mode


@dataclass
class _CaptureBudget:
    entries: int = 0
    total_bytes: int = 0


def _read_regular_file(
    parent_descriptor: int, name: str, path_info: os.stat_result, budget: _CaptureBudget
) -> tuple[bytes, int]:
    mode = _validate_source_mode(path_info, directory=False)
    if path_info.st_size < 0 or path_info.st_size > _MAX_FILE_BYTES:
        _execution_error("lifecycle-work-data-size-limit")
    descriptor: int | None = None
    try:
        descriptor = os.open(name, _file_read_flags(), dir_fd=parent_descriptor)
        before = os.fstat(descriptor)
        if _identity(before) != _identity(path_info):
            _execution_error("lifecycle-work-data-source-changed")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                _execution_error("lifecycle-work-data-source-changed")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _execution_error("lifecycle-work-data-source-changed")
        after = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if _identity(after) != _identity(before) or _identity(current) != _identity(before):
            _execution_error("lifecycle-work-data-source-changed")
        content = b"".join(chunks)
        budget.total_bytes += len(content)
        if budget.total_bytes > _MAX_TOTAL_BYTES:
            _execution_error("lifecycle-work-data-size-limit")
        return content, mode
    except DataBackupRuntimeError:
        raise
    except OSError as exc:
        _execution_error("lifecycle-work-data-read-failed", exc)
    finally:
        _close_quietly(descriptor)


def _capture_directory(
    descriptor: int,
    *,
    prefix: str,
    depth: int,
    budget: _CaptureBudget,
) -> list[dict[str, Any]]:
    if depth > _MAX_DEPTH:
        _execution_error("lifecycle-work-data-depth-limit")
    before = os.fstat(descriptor)
    _validate_source_mode(before, directory=True)
    try:
        names = sorted(_validate_name(name) for name in os.listdir(descriptor))
    except (OSError, UnicodeError) as exc:
        _execution_error("lifecycle-work-data-read-failed", exc)
    entries: list[dict[str, Any]] = []
    for name in names:
        budget.entries += 1
        if budget.entries > _MAX_ENTRIES:
            _execution_error("lifecycle-work-data-entry-limit")
        relative = f"{prefix}/{name}" if prefix else name
        if len(relative.encode("utf-8", errors="strict")) > 1024:
            _execution_error("lifecycle-work-data-path-invalid")
        try:
            path_info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except OSError as exc:
            _execution_error("lifecycle-work-data-read-failed", exc)
        if stat.S_ISDIR(path_info.st_mode):
            mode = _validate_source_mode(path_info, directory=True)
            child: int | None = None
            try:
                child = os.open(name, _directory_flags(), dir_fd=descriptor)
                if _identity(os.fstat(child)) != _identity(path_info):
                    _execution_error("lifecycle-work-data-source-changed")
                entries.append(
                    {
                        "path": relative,
                        "type": "directory",
                        "mode": mode,
                        "uid": path_info.st_uid,
                        "gid": path_info.st_gid,
                    }
                )
                entries.extend(
                    _capture_directory(
                        child,
                        prefix=relative,
                        depth=depth + 1,
                        budget=budget,
                    )
                )
            except DataBackupRuntimeError:
                raise
            except OSError as exc:
                _execution_error("lifecycle-work-data-read-failed", exc)
            finally:
                _close_quietly(child)
        elif stat.S_ISREG(path_info.st_mode):
            content, mode = _read_regular_file(descriptor, name, path_info, budget)
            entries.append(
                {
                    "path": relative,
                    "type": "file",
                    "mode": mode,
                    "uid": path_info.st_uid,
                    "gid": path_info.st_gid,
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "content": base64.b64encode(content).decode("ascii"),
                }
            )
        else:
            _execution_error("lifecycle-work-data-special-file-denied")
    if _identity(os.fstat(descriptor)) != _identity(before):
        _execution_error("lifecycle-work-data-source-changed")
    return entries


def _capture_path(install_descriptor: int, relative_path: str) -> dict[str, Any]:
    parts = _safe_relative_parts(relative_path)
    descriptor = _open_relative_directory(
        install_descriptor, parts, missing_ok=True
    )
    if descriptor is None:
        return {"present": False, "root": None, "entries": []}
    try:
        info = os.fstat(descriptor)
        mode = _validate_source_mode(info, directory=True)
        entries = _capture_directory(
            descriptor, prefix="", depth=0, budget=_CaptureBudget()
        )
        if entries != sorted(entries, key=lambda item: item["path"]):
            _execution_error("lifecycle-work-data-source-changed")
        return {
            "present": True,
            "root": {"mode": mode, "uid": info.st_uid, "gid": info.st_gid},
            "entries": entries,
        }
    finally:
        _close_quietly(descriptor)


def _snapshot_document(
    command: LifecycleWorkCommand, bound: _BoundData, state: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema": BACKUP_SCHEMA,
        "transactionId": command.transaction_id,
        "planHash": command.plan_hash,
        "serviceId": CANARY_SERVICE_ID,
        "definitionSha256": bound.definition_sha256,
        "dataSchemaVersion": CANARY_DATA_SCHEMA_VERSION,
        "data": {**CANARY_DATA_RECORD, "state": state},
    }


def _validate_int(value: Any, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        _execution_error("lifecycle-work-data-backup-invalid")
    return value


def _validate_snapshot_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"present", "root", "entries"}:
        _execution_error("lifecycle-work-data-backup-invalid")
    present = value["present"]
    root = value["root"]
    entries = value["entries"]
    if not isinstance(present, bool) or not isinstance(entries, list):
        _execution_error("lifecycle-work-data-backup-invalid")
    if not present:
        if root is not None or entries:
            _execution_error("lifecycle-work-data-backup-invalid")
        return value
    if not isinstance(root, dict) or set(root) != {"mode", "uid", "gid"}:
        _execution_error("lifecycle-work-data-backup-invalid")
    root_mode = _validate_int(root["mode"], maximum=0o777)
    root_uid = _validate_int(root["uid"], maximum=(1 << 31) - 1)
    root_gid = _validate_int(root["gid"], maximum=(1 << 31) - 1)
    if root_mode & 0o022 or (root_mode & 0o700) != 0o700:
        _execution_error("lifecycle-work-data-backup-invalid")
    if root_uid != os.geteuid() or root_gid != os.getegid():
        _execution_error("lifecycle-work-data-custody-invalid")

    previous: str | None = None
    directories: set[str] = set()
    total = 0
    if len(entries) > _MAX_ENTRIES:
        _execution_error("lifecycle-work-data-entry-limit")
    for entry in entries:
        if not isinstance(entry, dict):
            _execution_error("lifecycle-work-data-backup-invalid")
        entry_type = entry.get("type")
        required = {"path", "type", "mode", "uid", "gid"}
        if entry_type == "file":
            required |= {"size", "sha256", "content"}
        if set(entry) != required or entry_type not in {"directory", "file"}:
            _execution_error("lifecycle-work-data-backup-invalid")
        path = entry["path"]
        parts = _safe_relative_parts(path)
        if previous is not None and path <= previous:
            _execution_error("lifecycle-work-data-backup-invalid")
        previous = path
        if len(parts) > _MAX_DEPTH + 1:
            _execution_error("lifecycle-work-data-depth-limit")
        parent = "/".join(parts[:-1])
        if parent and parent not in directories:
            _execution_error("lifecycle-work-data-backup-invalid")
        mode = _validate_int(entry["mode"], maximum=0o777)
        uid = _validate_int(entry["uid"], maximum=(1 << 31) - 1)
        gid = _validate_int(entry["gid"], maximum=(1 << 31) - 1)
        if uid != os.geteuid() or gid != os.getegid():
            _execution_error("lifecycle-work-data-custody-invalid")
        if (
            mode & 0o022
            or (entry_type == "directory" and (mode & 0o700) != 0o700)
            or (entry_type == "file" and (mode & 0o400) == 0)
        ):
            _execution_error("lifecycle-work-data-backup-invalid")
        if entry_type == "directory":
            directories.add(path)
            continue
        size = _validate_int(entry["size"], maximum=_MAX_FILE_BYTES)
        digest = entry["sha256"]
        content_text = entry["content"]
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not isinstance(content_text, str)
        ):
            _execution_error("lifecycle-work-data-backup-invalid")
        try:
            content = base64.b64decode(content_text, validate=True)
        except (binascii.Error, ValueError) as exc:
            _execution_error("lifecycle-work-data-backup-invalid", exc)
        if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
            _execution_error("lifecycle-work-data-backup-invalid")
        total += size
        if total > _MAX_TOTAL_BYTES:
            _execution_error("lifecycle-work-data-size-limit")
    return value


def _validate_snapshot_document(
    value: Any, command: LifecycleWorkCommand, bound: _BoundData
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "transactionId",
        "planHash",
        "serviceId",
        "definitionSha256",
        "dataSchemaVersion",
        "data",
    }:
        _execution_error("lifecycle-work-data-backup-invalid")
    if (
        value["schema"] != BACKUP_SCHEMA
        or value["transactionId"] != command.transaction_id
        or value["planHash"] != command.plan_hash
        or value["serviceId"] != CANARY_SERVICE_ID
        or value["definitionSha256"] != bound.definition_sha256
        or value["dataSchemaVersion"] != CANARY_DATA_SCHEMA_VERSION
    ):
        _execution_error("lifecycle-work-data-backup-invalid")
    data = value["data"]
    if not isinstance(data, dict) or set(data) != set(CANARY_DATA_RECORD) | {"state"}:
        _execution_error("lifecycle-work-data-backup-invalid")
    for key, expected in CANARY_DATA_RECORD.items():
        if data.get(key) != expected:
            _execution_error("lifecycle-work-data-backup-invalid")
    _validate_snapshot_state(data["state"])
    return value


def _snapshot_name(command: LifecycleWorkCommand) -> str:
    return f"{command.transaction_id}.{command.plan_hash}.json"


def _check_snapshot_file(info: os.stat_result, *, allow_link_recovery: bool) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_gid != os.getegid()
        or stat.S_IMODE(info.st_mode) != _PUBLISHED_MODE
        or info.st_size <= 0
        or info.st_size > _MAX_SNAPSHOT_BYTES
        or info.st_nlink < 1
        or (not allow_link_recovery and info.st_nlink != 1)
    ):
        _execution_error("lifecycle-work-data-backup-custody-invalid")


def _stabilize_snapshot(root_descriptor: int, name: str) -> None:
    try:
        final = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        final = None
    except OSError as exc:
        _execution_error("lifecycle-work-data-backup-read-failed", exc)
    if final is not None:
        _check_snapshot_file(final, allow_link_recovery=True)
    prefix = f".{name}."
    changed = False
    try:
        candidates = os.listdir(root_descriptor)
        for candidate in candidates:
            if not candidate.startswith(prefix) or not candidate.endswith(".tmp"):
                continue
            token = candidate[len(prefix) : -len(".tmp")]
            if _TEMP_TOKEN_RE.fullmatch(token) is None:
                continue
            try:
                info = os.stat(candidate, dir_fd=root_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            linked_to_final = (
                final is not None
                and info.st_dev == final.st_dev
                and info.st_ino == final.st_ino
            )
            sealed_orphan = (
                final is not None
                and not linked_to_final
                and stat.S_ISREG(info.st_mode)
                and info.st_uid == os.geteuid()
                and info.st_gid == os.getegid()
                and stat.S_IMODE(info.st_mode) == _PUBLISHED_MODE
                and 0 < info.st_size <= _MAX_SNAPSHOT_BYTES
                and info.st_nlink == 1
            )
            if not linked_to_final and not sealed_orphan:
                continue
            try:
                os.unlink(candidate, dir_fd=root_descriptor)
            except FileNotFoundError:
                changed = True
                continue
            changed = True
        if changed:
            os.fsync(root_descriptor)
        if final is None:
            return
        current = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
        if current.st_dev != final.st_dev or current.st_ino != final.st_ino:
            _execution_error("lifecycle-work-data-backup-changed")
        _check_snapshot_file(current, allow_link_recovery=False)
    except DataBackupRuntimeError:
        raise
    except FileNotFoundError:
        _execution_error("lifecycle-work-data-backup-changed")
    except OSError as exc:
        _execution_error("lifecycle-work-data-backup-read-failed", exc)


def _read_snapshot(
    root_descriptor: int,
    name: str,
    command: LifecycleWorkCommand,
    bound: _BoundData,
    *,
    missing_ok: bool,
) -> tuple[bytes, dict[str, Any]] | None:
    _stabilize_snapshot(root_descriptor, name)
    descriptor: int | None = None
    try:
        descriptor = os.open(name, _file_read_flags(), dir_fd=root_descriptor)
    except FileNotFoundError:
        if missing_ok:
            return None
        _execution_error("lifecycle-work-data-backup-missing")
    except OSError as exc:
        _execution_error("lifecycle-work-data-backup-read-failed", exc)
    try:
        before = os.fstat(descriptor)
        _check_snapshot_file(before, allow_link_recovery=False)
        remaining = before.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                _execution_error("lifecycle-work-data-backup-invalid")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _execution_error("lifecycle-work-data-backup-invalid")
        after = os.fstat(descriptor)
        named = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
        if _identity(after) != _identity(before) or _identity(named) != _identity(before):
            _execution_error("lifecycle-work-data-backup-changed")
        raw = b"".join(chunks)
        value = _parse_canonical_json(raw, code="lifecycle-work-data-backup-invalid")
        return raw, _validate_snapshot_document(value, command, bound)
    except DataBackupRuntimeError:
        raise
    except OSError as exc:
        _execution_error("lifecycle-work-data-backup-read-failed", exc)
    finally:
        _close_quietly(descriptor)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            _execution_error("lifecycle-work-data-backup-write-failed")
        offset += written


def _publish_snapshot(
    root_descriptor: int,
    name: str,
    payload: bytes,
    command: LifecycleWorkCommand,
    bound: _BoundData,
) -> tuple[bytes, dict[str, Any]]:
    temp_name = f".{name}.{secrets.token_hex(16)}.tmp"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor: int | None = None
    published = False
    try:
        descriptor = os.open(temp_name, flags, _TEMP_MODE, dir_fd=root_descriptor)
        temp_info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(temp_info.st_mode)
            or temp_info.st_uid != os.geteuid()
            or temp_info.st_gid != os.getegid()
            or stat.S_IMODE(temp_info.st_mode) != _TEMP_MODE
            or temp_info.st_nlink != 1
        ):
            _execution_error("lifecycle-work-data-backup-custody-invalid")
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.fchmod(descriptor, _PUBLISHED_MODE)
        os.fsync(descriptor)
        sealed = os.fstat(descriptor)
        if sealed.st_size != len(payload) or stat.S_IMODE(sealed.st_mode) != _PUBLISHED_MODE:
            _execution_error("lifecycle-work-data-backup-write-failed")
        os.close(descriptor)
        descriptor = None
        try:
            os.link(
                temp_name,
                name,
                src_dir_fd=root_descriptor,
                dst_dir_fd=root_descriptor,
                follow_symlinks=False,
            )
            published = True
        except FileExistsError:
            published = False
        except FileNotFoundError:
            # Once another publisher has installed the canonical final, a
            # response-loss observer may remove this losing sealed temp before
            # it reaches link(2). The mandatory final readback below decides
            # whether publication completed; a missing final still fails.
            published = False
        try:
            os.unlink(temp_name, dir_fd=root_descriptor)
        except FileNotFoundError:
            # A concurrent response-loss observer may already have stabilized
            # the exact published inode. The final readback below remains the
            # authority for whether publication completed.
            pass
        os.fsync(root_descriptor)
        loaded = _read_snapshot(
            root_descriptor, name, command, bound, missing_ok=False
        )
        assert loaded is not None
        if published and loaded[0] != payload:
            _execution_error("lifecycle-work-data-backup-write-failed")
        return loaded
    except DataBackupRuntimeError:
        raise
    except OSError as exc:
        _execution_error("lifecycle-work-data-backup-write-failed", exc)
    finally:
        _close_quietly(descriptor)
        try:
            os.unlink(temp_name, dir_fd=root_descriptor)
        except OSError:
            pass


def _entry_info(parent_descriptor: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _remove_tree_entry(parent_descriptor: int, name: str) -> None:
    info = _entry_info(parent_descriptor, name)
    if info is None:
        return
    if info.st_uid != os.geteuid() or info.st_gid != os.getegid():
        _execution_error("lifecycle-work-data-custody-invalid")
    if stat.S_ISREG(info.st_mode):
        if info.st_nlink != 1:
            _execution_error("lifecycle-work-data-custody-invalid")
        os.unlink(name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
        return
    if not stat.S_ISDIR(info.st_mode):
        _execution_error("lifecycle-work-data-special-file-denied")
    descriptor: int | None = None
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent_descriptor)
        if _identity(os.fstat(descriptor)) != _identity(info):
            _execution_error("lifecycle-work-data-source-changed")
        for child_name in sorted(_validate_name(item) for item in os.listdir(descriptor)):
            _remove_tree_entry(descriptor, child_name)
        os.fsync(descriptor)
    except DataBackupRuntimeError:
        raise
    except OSError as exc:
        _execution_error("lifecycle-work-data-restore-failed", exc)
    finally:
        _close_quietly(descriptor)
    try:
        current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if not stat.S_ISDIR(current.st_mode) or current.st_uid != os.geteuid():
            _execution_error("lifecycle-work-data-custody-invalid")
        os.rmdir(name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
    except DataBackupRuntimeError:
        raise
    except OSError as exc:
        _execution_error("lifecycle-work-data-restore-failed", exc)


def _open_descendant(root_descriptor: int, parts: tuple[str, ...]) -> int:
    descriptor = os.dup(root_descriptor)
    try:
        for component in parts:
            child = os.open(component, _directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        _close_quietly(descriptor)
        raise


def _create_snapshot_tree(parent_descriptor: int, name: str, state: dict[str, Any]) -> None:
    root = state["root"]
    assert isinstance(root, dict)
    try:
        os.mkdir(name, _ROOT_MODE, dir_fd=parent_descriptor)
        root_descriptor = os.open(name, _directory_flags(), dir_fd=parent_descriptor)
    except OSError as exc:
        _execution_error("lifecycle-work-data-restore-failed", exc)
    try:
        entries = state["entries"]
        for entry in entries:
            if entry["type"] != "directory":
                continue
            parts = tuple(entry["path"].split("/"))
            parent = _open_descendant(root_descriptor, parts[:-1])
            try:
                os.mkdir(parts[-1], _ROOT_MODE, dir_fd=parent)
                os.fsync(parent)
            finally:
                _close_quietly(parent)
        for entry in entries:
            if entry["type"] != "file":
                continue
            parts = tuple(entry["path"].split("/"))
            parent = _open_descendant(root_descriptor, parts[:-1])
            descriptor: int | None = None
            try:
                descriptor = os.open(
                    parts[-1],
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | os.O_NOFOLLOW
                    | getattr(os, "O_CLOEXEC", 0),
                    _TEMP_MODE,
                    dir_fd=parent,
                )
                content = base64.b64decode(entry["content"], validate=True)
                _write_all(descriptor, content)
                os.fchmod(descriptor, entry["mode"])
                os.fsync(descriptor)
                os.fsync(parent)
            finally:
                _close_quietly(descriptor)
                _close_quietly(parent)
        directories = [entry for entry in entries if entry["type"] == "directory"]
        for entry in sorted(
            directories, key=lambda item: item["path"].count("/"), reverse=True
        ):
            descriptor = _open_descendant(
                root_descriptor, tuple(entry["path"].split("/"))
            )
            try:
                os.fchmod(descriptor, entry["mode"])
                os.fsync(descriptor)
            finally:
                _close_quietly(descriptor)
        os.fchmod(root_descriptor, root["mode"])
        os.fsync(root_descriptor)
    except DataBackupRuntimeError:
        raise
    except (OSError, binascii.Error, ValueError) as exc:
        _execution_error("lifecycle-work-data-restore-failed", exc)
    finally:
        _close_quietly(root_descriptor)
    try:
        os.fsync(parent_descriptor)
    except OSError as exc:
        _execution_error("lifecycle-work-data-restore-failed", exc)


def _restore_path(
    install_descriptor: int, relative_path: str, state: dict[str, Any]
) -> None:
    parts = _safe_relative_parts(relative_path)
    parent = _open_relative_directory(
        install_descriptor, parts[:-1], missing_ok=False
    )
    assert parent is not None
    try:
        _remove_tree_entry(parent, parts[-1])
        if state["present"]:
            _create_snapshot_tree(parent, parts[-1], state)
    finally:
        _close_quietly(parent)


def _evidence_hash(
    command: LifecycleWorkCommand, snapshot_sha256: str
) -> str:
    return hashlib.sha256(
        _canonical_json(
            {
                "schema": BACKUP_EVIDENCE_SCHEMA,
                "transactionId": command.transaction_id,
                "planHash": command.plan_hash,
                "operationKey": command.operation_key,
                "serviceIds": list(command.service_ids),
                "snapshotSha256": snapshot_sha256,
            }
        )
    ).hexdigest()


class DataBackupStore:
    """Owner-private immutable snapshots and exact idempotent restoration."""

    def __init__(self, install_dir: Path, backup_root: Path) -> None:
        _validate_platform()
        self._install_dir = install_dir
        self._backup_root = backup_root
        install_descriptor = _open_absolute_directory(install_dir, private=False)
        backup_descriptor = _open_absolute_directory(backup_root, private=True)
        _close_quietly(install_descriptor)
        _close_quietly(backup_descriptor)

    @property
    def root(self) -> Path:
        return self._backup_root

    def backup(self, command: LifecycleWorkCommand, bound: _BoundData) -> str:
        install_descriptor = _open_absolute_directory(self._install_dir, private=False)
        backup_descriptor = _open_absolute_directory(self._backup_root, private=True)
        try:
            name = _snapshot_name(command)
            existing = _read_snapshot(
                backup_descriptor, name, command, bound, missing_ok=True
            )
            if existing is not None:
                return hashlib.sha256(existing[0]).hexdigest()
            state = _capture_path(install_descriptor, bound.path)
            payload = _canonical_json(_snapshot_document(command, bound, state))
            if len(payload) > _MAX_SNAPSHOT_BYTES:
                _execution_error("lifecycle-work-data-size-limit")
            stored, _document = _publish_snapshot(
                backup_descriptor, name, payload, command, bound
            )
            return hashlib.sha256(stored).hexdigest()
        finally:
            _close_quietly(install_descriptor)
            _close_quietly(backup_descriptor)

    def snapshot_hash(
        self,
        command: LifecycleWorkCommand,
        bound: _BoundData,
        *,
        missing_ok: bool,
    ) -> str | None:
        descriptor = _open_absolute_directory(self._backup_root, private=True)
        try:
            loaded = _read_snapshot(
                descriptor,
                _snapshot_name(command),
                command,
                bound,
                missing_ok=missing_ok,
            )
            return None if loaded is None else hashlib.sha256(loaded[0]).hexdigest()
        finally:
            _close_quietly(descriptor)

    def restore(self, command: LifecycleWorkCommand, bound: _BoundData) -> str:
        install_descriptor = _open_absolute_directory(self._install_dir, private=False)
        backup_descriptor = _open_absolute_directory(self._backup_root, private=True)
        try:
            loaded = _read_snapshot(
                backup_descriptor,
                _snapshot_name(command),
                command,
                bound,
                missing_ok=False,
            )
            assert loaded is not None
            raw, document = loaded
            state = document["data"]["state"]
            if _capture_path(install_descriptor, bound.path) != state:
                _restore_path(install_descriptor, bound.path, state)
            if _capture_path(install_descriptor, bound.path) != state:
                _execution_error("lifecycle-work-data-restore-unverified")
            return hashlib.sha256(raw).hexdigest()
        finally:
            _close_quietly(install_descriptor)
            _close_quietly(backup_descriptor)

    def is_restored(
        self, command: LifecycleWorkCommand, bound: _BoundData
    ) -> tuple[bool, str]:
        install_descriptor = _open_absolute_directory(self._install_dir, private=False)
        backup_descriptor = _open_absolute_directory(self._backup_root, private=True)
        try:
            loaded = _read_snapshot(
                backup_descriptor,
                _snapshot_name(command),
                command,
                bound,
                missing_ok=False,
            )
            assert loaded is not None
            raw, document = loaded
            matches = _capture_path(install_descriptor, bound.path) == document["data"]["state"]
            return matches, hashlib.sha256(raw).hexdigest()
        finally:
            _close_quietly(install_descriptor)
            _close_quietly(backup_descriptor)


class DataBackupDispatcher:
    def __init__(self, store: DataBackupStore) -> None:
        self._store = store

    def __call__(self, command: LifecycleWorkCommand) -> str:
        bound = _validate_bound_command(command, "backup")
        return _evidence_hash(command, self._store.backup(command, bound))


class DataRestoreDispatcher:
    def __init__(self, store: DataBackupStore) -> None:
        self._store = store

    def __call__(self, command: LifecycleWorkCommand) -> str:
        bound = _validate_bound_command(command, "restore")
        return _evidence_hash(command, self._store.restore(command, bound))


class DataBackupStartedObserver:
    def __init__(self, plan_loader: _PlanLoader, store: DataBackupStore) -> None:
        self._plan_loader = plan_loader
        self._store = store

    def __call__(
        self, command: LifecycleWorkCommand
    ) -> LifecycleWorkStartedObservation:
        try:
            loaded = self._plan_loader(command)
        except LifecycleWorkError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep raw plan errors private
            _execution_error("lifecycle-work-data-observation-failed", exc)
        bound_command = _validate_loaded_command(command, loaded)
        bound = _validate_bound_command(bound_command, "backup")
        snapshot_hash = self._store.snapshot_hash(
            bound_command, bound, missing_ok=True
        )
        if snapshot_hash is None:
            return LifecycleWorkStartedObservation(state="missing")
        return LifecycleWorkStartedObservation(
            state="completed",
            evidence_hash=_evidence_hash(bound_command, snapshot_hash),
        )


class DataRestoreStartedObserver:
    def __init__(self, plan_loader: _PlanLoader, store: DataBackupStore) -> None:
        self._plan_loader = plan_loader
        self._store = store

    def __call__(
        self, command: LifecycleWorkCommand
    ) -> LifecycleWorkStartedObservation:
        try:
            loaded = self._plan_loader(command)
        except LifecycleWorkError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep raw plan errors private
            _execution_error("lifecycle-work-data-observation-failed", exc)
        bound_command = _validate_loaded_command(command, loaded)
        bound = _validate_bound_command(bound_command, "restore")
        restored, snapshot_hash = self._store.is_restored(bound_command, bound)
        if not restored:
            return LifecycleWorkStartedObservation(state="missing")
        return LifecycleWorkStartedObservation(
            state="completed",
            evidence_hash=_evidence_hash(bound_command, snapshot_hash),
        )


@dataclass(frozen=True)
class DataBackupRuntime:
    root: Path
    store: DataBackupStore
    backup_dispatcher: DataBackupDispatcher
    backup_started_observer: DataBackupStartedObserver
    restore_dispatcher: DataRestoreDispatcher
    restore_started_observer: DataRestoreStartedObserver


def build_data_backup_runtime(
    *, install_dir: Path, data_dir: Path, plan_loader: _PlanLoader
) -> DataBackupRuntime:
    """Compose the paired canary effects without taking a snapshot or restoring."""

    if not callable(plan_loader):
        _execution_error("lifecycle-work-data-runtime-invalid")
    root = data_dir / "assistant-first" / "data-backups"
    store = DataBackupStore(install_dir, root)
    return DataBackupRuntime(
        root=root,
        store=store,
        backup_dispatcher=DataBackupDispatcher(store),
        backup_started_observer=DataBackupStartedObserver(plan_loader, store),
        restore_dispatcher=DataRestoreDispatcher(store),
        restore_started_observer=DataRestoreStartedObserver(plan_loader, store),
    )


__all__ = [
    "BACKUP_EVIDENCE_SCHEMA",
    "BACKUP_SCHEMA",
    "CANARY_DATA_PATH",
    "CANARY_DATA_RECORD",
    "DataBackupDispatcher",
    "DataBackupRuntime",
    "DataBackupRuntimeError",
    "DataBackupStartedObserver",
    "DataBackupStore",
    "DataRestoreDispatcher",
    "DataRestoreStartedObserver",
    "build_data_backup_runtime",
]
