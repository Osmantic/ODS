"""Durable fixed-root active-application records for Assistant First.

This POSIX-only store persists the canonical records produced by
``extension_application_observation``. It owns no Docker, Compose, process,
network, configuration, or transaction-executor authority. Its root must be
created by the installer and is re-opened, re-validated, and locked for every
operation.
"""

from __future__ import annotations

import errno
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from typing import Any

try:  # Import must remain safe on unqualified non-POSIX hosts.
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows CI
    fcntl = None  # type: ignore[assignment]

from extension_application_identity import produce_application_identity
from extension_application_observation import (
    parse_active_record,
    produce_active_record,
)
from extension_lifecycle_work import LifecycleWorkCommand, LifecycleWorkError

STORE_SCHEMA = "ods.extension-application-records.v1"
SNAPSHOT_NAME = "application-state.json"
MAX_RECORDS = 256
MAX_FILE_BYTES = 2 * 1024 * 1024

_ROOT_MODE = 0o700
_FILE_MODE = 0o600
_TEMP_PREFIX = "tmp-"
_TEMP_SUFFIX = ".application-state"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SERVICE_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")


class ApplicationRecordStoreError(LifecycleWorkError):
    """One stable, value-free store failure."""


def _fail(code: str) -> None:
    raise ApplicationRecordStoreError(code) from None


@dataclass(frozen=True)
class ApplicationRecord:
    """Immutable public form of one canonical active-application record."""

    schema: str
    service_id: str
    version: str
    action: str
    transaction_id: str
    plan_sha256: str
    request_sha256: str
    definition_sha256: str
    compose_sha256: str
    identity_sha256: str
    config_sha256: str
    override_sha256: str
    expected_containers: tuple[str, ...]
    record_sha256: str


@dataclass(frozen=True)
class PublishResult:
    """A proven create, exact replay, or compare-and-replace result."""

    record: ApplicationRecord
    outcome: str


@dataclass(frozen=True)
class RemoveResult:
    """A proven removal, or an observation that the service was absent."""

    outcome: str
    prior: ApplicationRecord | None


class _DuplicateKey(ValueError):
    pass


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKey
        value[key] = item
    return value


def _reject_number(_value: str) -> Any:
    raise ValueError


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        encoded = (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail("application-record-store-json-invalid")
    if not encoded or len(encoded) > MAX_FILE_BYTES:
        _fail("application-record-store-size")
    return encoded


def _record_model(value: Any) -> tuple[dict[str, Any], ApplicationRecord]:
    if not isinstance(value, dict):
        _fail("application-record-store-record-invalid")
    try:
        parsed = parse_active_record(_canonical_json_bytes(value))
    except LifecycleWorkError:
        _fail("application-record-store-record-invalid")
    record = ApplicationRecord(
        schema=parsed["schema"],
        service_id=parsed["service_id"],
        version=parsed["version"],
        action=parsed["action"],
        transaction_id=parsed["transaction_id"],
        plan_sha256=parsed["plan_sha256"],
        request_sha256=parsed["request_sha256"],
        definition_sha256=parsed["definition_sha256"],
        compose_sha256=parsed["compose_sha256"],
        identity_sha256=parsed["identity_sha256"],
        config_sha256=parsed["config_sha256"],
        override_sha256=parsed["override_sha256"],
        expected_containers=tuple(parsed["expected_containers"]),
        record_sha256=parsed["record_sha256"],
    )
    return parsed, record


def _validate_snapshot(
    value: Any,
) -> tuple[list[dict[str, Any]], tuple[ApplicationRecord, ...]]:
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "records"}
        or value.get("schema") != STORE_SCHEMA
        or not isinstance(value.get("records"), list)
    ):
        _fail("application-record-store-schema")
    supplied = value["records"]
    if len(supplied) > MAX_RECORDS:
        _fail("application-record-store-size")

    dictionaries: list[dict[str, Any]] = []
    models: list[ApplicationRecord] = []
    previous: str | None = None
    for supplied_record in supplied:
        parsed, record = _record_model(supplied_record)
        if previous is not None and record.service_id <= previous:
            _fail("application-record-store-order")
        previous = record.service_id
        dictionaries.append(parsed)
        models.append(record)
    return dictionaries, tuple(models)


def _parse_snapshot(
    raw: bytes,
) -> tuple[list[dict[str, Any]], tuple[ApplicationRecord, ...]]:
    if type(raw) is not bytes or not raw or len(raw) > MAX_FILE_BYTES:
        _fail("application-record-store-size")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_no_duplicate_keys,
            parse_int=_reject_number,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except _DuplicateKey:
        _fail("application-record-store-duplicate-key")
    except (TypeError, ValueError, UnicodeError, RecursionError, json.JSONDecodeError):
        _fail("application-record-store-json-invalid")
    dictionaries, models = _validate_snapshot(value)
    if _canonical_json_bytes(value) != raw:
        _fail("application-record-store-noncanonical")
    return dictionaries, models


def _snapshot_bytes(records: list[dict[str, Any]]) -> bytes:
    envelope = {"schema": STORE_SCHEMA, "records": records}
    _validate_snapshot(envelope)
    return _canonical_json_bytes(envelope)


def _validate_platform() -> None:
    if (
        os.name != "posix"
        or fcntl is None
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_NONBLOCK")
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
        or os.stat not in os.supports_follow_symlinks
        or os.unlink not in os.supports_dir_fd
        or not callable(getattr(os, "replace", None))
    ):
        _fail("application-record-store-platform-unsupported")


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )


def _file_read_flags() -> int:
    return (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | os.O_NONBLOCK
        | getattr(os, "O_CLOEXEC", 0)
    )


def _close_quietly(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


def _root_components(value: Any) -> tuple[str, ...]:
    try:
        raw = os.fspath(value)
    except (TypeError, ValueError):
        _fail("application-record-store-root-invalid")
    if (
        not isinstance(raw, str)
        or "\x00" in raw
        or not raw.startswith("/")
        or raw == "/"
    ):
        _fail("application-record-store-root-invalid")
    parts = tuple(raw.split("/")[1:])
    if any(part in {"", ".", ".."} for part in parts):
        _fail("application-record-store-root-invalid")
    return parts


def _open_root(parts: tuple[str, ...]) -> int:
    try:
        descriptor = os.open("/", _directory_flags())
    except OSError:
        _fail("application-record-store-root-missing")
    try:
        for component in parts:
            parent = descriptor
            descriptor = -1
            try:
                descriptor = os.open(component, _directory_flags(), dir_fd=parent)
            except FileNotFoundError:
                _fail("application-record-store-root-missing")
            except OSError as error:
                if error.errno in {errno.ELOOP, errno.ENOTDIR}:
                    _fail("application-record-store-root-invalid")
                _fail("application-record-store-root-io")
            finally:
                _close_quietly(parent)
        try:
            info = os.fstat(descriptor)
        except OSError:
            _fail("application-record-store-root-io")
        if not stat.S_ISDIR(info.st_mode):
            _fail("application-record-store-root-invalid")
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != _ROOT_MODE:
            _fail("application-record-store-root-custody")
        return descriptor
    except BaseException:
        if descriptor >= 0:
            _close_quietly(descriptor)
        raise


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_uid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _check_snapshot_file(descriptor: int) -> os.stat_result:
    try:
        info = os.fstat(descriptor)
    except OSError:
        _fail("application-record-store-read")
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != _FILE_MODE
        or info.st_nlink != 1
    ):
        _fail("application-record-store-custody")
    if info.st_size > MAX_FILE_BYTES:
        _fail("application-record-store-size")
    return info


def _read_all(descriptor: int, expected_size: int) -> bytes:
    content = bytearray()
    try:
        while len(content) <= MAX_FILE_BYTES:
            remaining = MAX_FILE_BYTES + 1 - len(content)
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            content.extend(chunk)
    except OSError:
        _fail("application-record-store-read")
    if len(content) > MAX_FILE_BYTES:
        _fail("application-record-store-size")
    if len(content) != expected_size:
        _fail("application-record-store-integrity")
    return bytes(content)


def _read_snapshot(root_descriptor: int) -> bytes:
    try:
        descriptor = os.open(
            SNAPSHOT_NAME, _file_read_flags(), dir_fd=root_descriptor
        )
    except FileNotFoundError:
        _fail("application-record-store-missing")
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            _fail("application-record-store-custody")
        _fail("application-record-store-read")
    try:
        before = _check_snapshot_file(descriptor)
        raw = _read_all(descriptor, before.st_size)
        after = _check_snapshot_file(descriptor)
        if _identity(before) != _identity(after):
            _fail("application-record-store-integrity")
        try:
            named = os.stat(
                SNAPSHOT_NAME,
                dir_fd=root_descriptor,
                follow_symlinks=False,
            )
        except OSError:
            _fail("application-record-store-integrity")
        if (named.st_dev, named.st_ino) != (after.st_dev, after.st_ino):
            _fail("application-record-store-integrity")
        return raw
    finally:
        _close_quietly(descriptor)


def _read_records(
    root_descriptor: int,
) -> tuple[list[dict[str, Any]], tuple[ApplicationRecord, ...], bool]:
    try:
        raw = _read_snapshot(root_descriptor)
    except ApplicationRecordStoreError as error:
        if error.code == "application-record-store-missing":
            return [], (), False
        raise
    dictionaries, models = _parse_snapshot(raw)
    return dictionaries, models, True


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    try:
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                _fail("application-record-store-write-failed")
            offset += written
    except OSError:
        _fail("application-record-store-write-failed")


def _path_matches(
    root_descriptor: int, name: str, expected: os.stat_result
) -> bool:
    try:
        current = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
    except OSError:
        return False
    return (current.st_dev, current.st_ino) == (expected.st_dev, expected.st_ino)


def _cleanup_temp(
    root_descriptor: int, name: str, expected: os.stat_result
) -> None:
    if not _path_matches(root_descriptor, name, expected):
        return
    try:
        os.unlink(name, dir_fd=root_descriptor)
    except OSError:
        pass


def _fsync_root(root_descriptor: int) -> bool:
    for _attempt in range(2):
        try:
            os.fsync(root_descriptor)
            return True
        except OSError:
            continue
    return False


def _durably_matches(root_descriptor: int, payload: bytes) -> bool:
    if not _fsync_root(root_descriptor):
        return False
    try:
        return _read_snapshot(root_descriptor) == payload
    except ApplicationRecordStoreError:
        return False


def _prove_unchanged(
    root_descriptor: int,
    records: list[dict[str, Any]],
    snapshot_present: bool,
) -> None:
    if not _fsync_root(root_descriptor):
        _fail("application-record-store-write-ambiguous")
    if snapshot_present:
        expected = _snapshot_bytes(records)
        try:
            current = _read_snapshot(root_descriptor)
        except ApplicationRecordStoreError:
            _fail("application-record-store-write-ambiguous")
        if current != expected:
            _fail("application-record-store-write-ambiguous")
        return
    try:
        _read_snapshot(root_descriptor)
    except ApplicationRecordStoreError as error:
        if error.code == "application-record-store-missing":
            return
    _fail("application-record-store-write-ambiguous")


def _write_snapshot(root_descriptor: int, records: list[dict[str, Any]]) -> None:
    # Validate the entire candidate before allocating a temporary entry.
    payload = _snapshot_bytes(records)
    temp_name = _TEMP_PREFIX + secrets.token_hex(16) + _TEMP_SUFFIX
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(
            temp_name, flags, _FILE_MODE, dir_fd=root_descriptor
        )
    except OSError:
        _fail("application-record-store-write-failed")

    temp_info: os.stat_result | None = None
    try:
        try:
            temp_info = os.fstat(descriptor)
        except OSError:
            _fail("application-record-store-write-failed")
        if (
            not stat.S_ISREG(temp_info.st_mode)
            or temp_info.st_uid != os.geteuid()
            or stat.S_IMODE(temp_info.st_mode) != _FILE_MODE
            or temp_info.st_nlink != 1
        ):
            _fail("application-record-store-write-failed")

        _write_all(descriptor, payload)
        try:
            os.fsync(descriptor)
            sealed = os.fstat(descriptor)
        except OSError:
            _fail("application-record-store-write-failed")
        if (
            (sealed.st_dev, sealed.st_ino) != (temp_info.st_dev, temp_info.st_ino)
            or not stat.S_ISREG(sealed.st_mode)
            or sealed.st_uid != os.geteuid()
            or stat.S_IMODE(sealed.st_mode) != _FILE_MODE
            or sealed.st_nlink != 1
            or sealed.st_size != len(payload)
        ):
            _fail("application-record-store-write-failed")
        temp_info = sealed
        try:
            named_temp = os.stat(
                temp_name, dir_fd=root_descriptor, follow_symlinks=False
            )
        except OSError:
            _fail("application-record-store-write-failed")
        if _identity(named_temp) != _identity(sealed):
            _fail("application-record-store-write-failed")

        try:
            os.replace(
                temp_name,
                SNAPSHOT_NAME,
                src_dir_fd=root_descriptor,
                dst_dir_fd=root_descriptor,
            )
        except OSError:
            # A wrapper or interrupted syscall may raise after replacement.
            if _durably_matches(root_descriptor, payload):
                return
            if _path_matches(root_descriptor, temp_name, sealed):
                _fail("application-record-store-write-failed")
            _fail("application-record-store-write-ambiguous")

        if not _durably_matches(root_descriptor, payload):
            _fail("application-record-store-write-ambiguous")
    finally:
        _close_quietly(descriptor)
        if temp_info is not None:
            _cleanup_temp(root_descriptor, temp_name, temp_info)


def _validate_service_id(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 128
        or _SERVICE_RE.fullmatch(value) is None
    ):
        _fail("application-record-store-binding-invalid")
    return value


def _validate_record_hash(value: Any, *, optional: bool) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        _fail("application-record-store-binding-invalid")
    return value


def _prepare_record(
    command: Any,
    config_sha256: Any,
    expected_containers: Any,
    override_sha256: Any,
) -> tuple[dict[str, Any], ApplicationRecord]:
    if not isinstance(command, LifecycleWorkCommand):
        _fail("application-record-store-binding-invalid")
    # The producer performs the authoritative strict digest/container checks.
    try:
        identity = produce_application_identity(command)
        raw = produce_active_record(
            identity,
            config_sha256,
            expected_containers,
            override_sha256=override_sha256,
        )
        parsed = parse_active_record(raw)
    except (LifecycleWorkError, TypeError, ValueError, UnicodeError):
        _fail("application-record-store-record-invalid")
    validated, model = _record_model(parsed)
    return validated, model


class ApplicationRecordStore:
    """Descriptor-relative snapshot store for current application records."""

    def __init__(self, root_path: str | os.PathLike[str]) -> None:
        _validate_platform()
        self._root_parts = _root_components(root_path)
        _close_quietly(_open_root(self._root_parts))

    def _run_locked(self, operation: Any) -> Any:
        root_descriptor = _open_root(self._root_parts)
        locked = False
        try:
            try:
                assert fcntl is not None
                fcntl.flock(root_descriptor, fcntl.LOCK_EX)
            except OSError:
                _fail("application-record-store-lock")
            locked = True
            return operation(root_descriptor)
        finally:
            if locked:
                try:
                    assert fcntl is not None
                    fcntl.flock(root_descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            _close_quietly(root_descriptor)

    def snapshot(self, service_id: str) -> ApplicationRecord | None:
        service_id = _validate_service_id(service_id)

        def operation(root_descriptor: int) -> ApplicationRecord | None:
            _dictionaries, records, _present = _read_records(root_descriptor)
            return next(
                (record for record in records if record.service_id == service_id),
                None,
            )

        return self._run_locked(operation)

    def active(self) -> tuple[ApplicationRecord, ...]:
        def operation(root_descriptor: int) -> tuple[ApplicationRecord, ...]:
            _dictionaries, records, _present = _read_records(root_descriptor)
            return records

        return self._run_locked(operation)

    def publish(
        self,
        command: LifecycleWorkCommand,
        config_sha256: str,
        expected_containers: tuple[str, ...],
        override_sha256: str,
        previous_record_sha256: str | None = None,
    ) -> PublishResult:
        new_dictionary, new_record = _prepare_record(
            command, config_sha256, expected_containers, override_sha256
        )
        previous = _validate_record_hash(previous_record_sha256, optional=True)

        def operation(root_descriptor: int) -> PublishResult:
            dictionaries, records, present = _read_records(root_descriptor)
            index = next(
                (
                    position
                    for position, record in enumerate(records)
                    if record.service_id == new_record.service_id
                ),
                None,
            )
            if index is None:
                if previous is not None:
                    _fail("application-record-store-conflict")
                if len(records) >= MAX_RECORDS:
                    _fail("application-record-store-size")
                dictionaries.append(new_dictionary)
                dictionaries.sort(key=lambda record: record["service_id"])
                _write_snapshot(root_descriptor, dictionaries)
                return PublishResult(record=new_record, outcome="created")

            current = records[index]
            if current.record_sha256 == new_record.record_sha256:
                if previous is not None and previous != current.record_sha256:
                    _fail("application-record-store-conflict")
                _prove_unchanged(root_descriptor, dictionaries, present)
                return PublishResult(record=current, outcome="replayed")

            if previous is None or previous != current.record_sha256:
                _fail("application-record-store-conflict")
            dictionaries[index] = new_dictionary
            _write_snapshot(root_descriptor, dictionaries)
            return PublishResult(record=new_record, outcome="replaced")

        return self._run_locked(operation)

    def remove(
        self, service_id: str, expected_record_sha256: str
    ) -> RemoveResult:
        service_id = _validate_service_id(service_id)
        expected = _validate_record_hash(expected_record_sha256, optional=False)
        assert expected is not None

        def operation(root_descriptor: int) -> RemoveResult:
            dictionaries, records, present = _read_records(root_descriptor)
            index = next(
                (
                    position
                    for position, record in enumerate(records)
                    if record.service_id == service_id
                ),
                None,
            )
            if index is None:
                _prove_unchanged(root_descriptor, dictionaries, present)
                return RemoveResult(outcome="absent", prior=None)
            prior = records[index]
            if prior.record_sha256 != expected:
                _fail("application-record-store-conflict")
            del dictionaries[index]
            _write_snapshot(root_descriptor, dictionaries)
            return RemoveResult(outcome="removed", prior=prior)

        return self._run_locked(operation)


__all__ = [
    "MAX_FILE_BYTES",
    "MAX_RECORDS",
    "SNAPSHOT_NAME",
    "STORE_SCHEMA",
    "ApplicationRecord",
    "ApplicationRecordStore",
    "ApplicationRecordStoreError",
    "PublishResult",
    "RemoveResult",
]
