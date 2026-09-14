"""Extension resource reservation store.

Stdlib-only POSIX snapshot store for ODS 5G-S extension resource reservations.
Canonical JSON file with fcntl-exclusive locking, atomic same-root replacement,
and cross-process validation of every read.

Custody model mirrors ``extension_artifact_stage_store``: the absolute root
value is validated without following it, every component is traversed from the
anchor with descriptor-relative ``O_DIRECTORY|O_NOFOLLOW`` opens, custody is
re-proven on every public call, and no ``Path.resolve``, ``stat``, or path-open
is ever used on the root or the state file.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA = "ods.extension-resource-reservation.v1"
STORE_SCHEMA = "ods.extension-resource-reservations.v1"
BATCH_RELEASE_SCHEMA = "ods.extension-resource-batch-release.v1"
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB
MAX_RECORDS = 1024

# ---------------------------------------------------------------------------
# Typed batch-release expectation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReleaseExpectation:
    """Exact pre-validated expectation for one service in a batch release.

    All fields come from the adapter's plan-bound proof and are re-validated
    atomically inside ``batch_release`` under the store lock.
    """

    service_id: str
    action: str
    claims: ReservationClaims

TXN_RE = re.compile(r"^txn-[0-9a-f]{24}$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")
EXCLUSIVE_RE = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,127}$")

VALID_ACTIONS = frozenset({"install", "enable", "repair", "update"})
ACTIVE = "active"
RELEASED = "released"
FAILED = "failed"
VALID_STATUSES = frozenset({ACTIVE, RELEASED, FAILED})
TERMINAL_STATUSES = frozenset({RELEASED, FAILED})

TEMP_PREFIX = "tmp-"
TEMP_SUFFIX = ".tmp"
SNAPSHOT_NAME = "reservations.json"

_ROOT_MODE = 0o700
_FILE_MODE = 0o600

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ReservationStoreError(Exception):
    """Store error with a machine-readable code. Value-free."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    def __repr__(self) -> str:
        return f"ReservationStoreError({self.code!r})"


def _fail(code: str) -> None:
    raise ReservationStoreError(code) from None


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HostPort:
    port: int
    protocol: str


@dataclass(frozen=True)
class ReservationClaims:
    """Frozen reservation claims in enforced canonical order."""

    host_ports: tuple[HostPort, ...]
    exclusive: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.host_ports, tuple) or not isinstance(
            self.exclusive, tuple
        ):
            raise ValueError("claims must be tuples")
        seen: set[tuple[int, str]] = set()
        previous: tuple[int, str] | None = None
        for hp in self.host_ports:
            if not isinstance(hp, HostPort):
                raise ValueError("host_ports must contain HostPort")
            if isinstance(hp.port, bool) or not isinstance(hp.port, int):
                raise ValueError("invalid port type")
            if hp.port < 1 or hp.port > 65535:
                raise ValueError("invalid port range")
            if hp.protocol not in ("tcp", "udp"):
                raise ValueError("invalid protocol")
            key = (hp.port, hp.protocol)
            if key in seen:
                raise ValueError("duplicate host port")
            if previous is not None and key <= previous:
                raise ValueError("host_ports not in canonical order")
            seen.add(key)
            previous = key
        seen_tokens: set[str] = set()
        previous_token: str | None = None
        for token in self.exclusive:
            if not isinstance(token, str):
                raise ValueError("exclusive token must be a string")
            if EXCLUSIVE_RE.fullmatch(token) is None:
                raise ValueError("invalid exclusive token")
            if token in seen_tokens:
                raise ValueError("duplicate exclusive token")
            if previous_token is not None and token <= previous_token:
                raise ValueError("exclusive tokens not in canonical order")
            seen_tokens.add(token)
            previous_token = token

    def to_dict(self) -> dict[str, Any]:
        return {
            "hostPorts": [
                {"port": hp.port, "protocol": hp.protocol} for hp in self.host_ports
            ],
            "exclusive": list(self.exclusive),
        }


@dataclass(frozen=True)
class ReservationRecord:
    schema: str
    transaction_id: str
    plan_hash: str
    service_id: str
    action: str
    status: str
    created_at: str
    updated_at: str
    claims: ReservationClaims
    claims_digest: str
    record_sha256: str
    duplicate: bool = False


# ---------------------------------------------------------------------------
# Argument validators
# ---------------------------------------------------------------------------


def _validate_transaction_id(value: Any) -> str:
    if not isinstance(value, str) or TXN_RE.fullmatch(value) is None:
        _fail("binding-invalid")
    return value


def _validate_plan_hash(value: Any) -> str:
    if not isinstance(value, str) or HEX64_RE.fullmatch(value) is None:
        _fail("binding-invalid")
    return value


def _validate_service_id(value: Any) -> str:
    if not isinstance(value, str) or SERVICE_RE.fullmatch(value) is None:
        _fail("binding-invalid")
    return value


def _validate_action(value: Any) -> str:
    if not isinstance(value, str) or value not in VALID_ACTIONS:
        _fail("binding-invalid")
    return value


def _validate_terminal_status(value: Any) -> str:
    if not isinstance(value, str) or value not in TERMINAL_STATUSES:
        _fail("transition-invalid")
    return value


def _validate_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        _fail("timestamp-invalid")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        _fail("timestamp-invalid")
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        _fail("timestamp-invalid")
    return value


def _validate_claims(value: Any) -> ReservationClaims:
    if not isinstance(value, ReservationClaims):
        _fail("binding-invalid")
    return value


def _release_claims_dict(value: Any) -> dict[str, Any]:
    """Re-prove exact release claims and return a detached canonical value."""
    if type(value) is not ReservationClaims:
        _fail("binding-invalid")
    if type(value.host_ports) is not tuple or type(value.exclusive) is not tuple:
        _fail("binding-invalid")

    host_ports: list[dict[str, Any]] = []
    previous_port: tuple[int, str] | None = None
    for host_port in value.host_ports:
        if type(host_port) is not HostPort:
            _fail("binding-invalid")
        if type(host_port.port) is not int or type(host_port.protocol) is not str:
            _fail("binding-invalid")
        if host_port.port < 1 or host_port.port > 65535:
            _fail("binding-invalid")
        if host_port.protocol not in ("tcp", "udp"):
            _fail("binding-invalid")
        key = (host_port.port, host_port.protocol)
        if previous_port is not None and key <= previous_port:
            _fail("binding-invalid")
        previous_port = key
        host_ports.append({"port": host_port.port, "protocol": host_port.protocol})

    exclusive: list[str] = []
    previous_token: str | None = None
    for token in value.exclusive:
        if type(token) is not str or EXCLUSIVE_RE.fullmatch(token) is None:
            _fail("binding-invalid")
        if previous_token is not None and token <= previous_token:
            _fail("binding-invalid")
        previous_token = token
        exclusive.append(token)

    return {"hostPorts": host_ports, "exclusive": exclusive}


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_number(_value: str) -> Any:
    raise ValueError("unsupported number")


def _reject_constant(_value: str) -> Any:
    raise ValueError("unsupported constant")


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail("corrupt-snapshot-json")


def _sha256hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Record construction, conversion, validation
# ---------------------------------------------------------------------------


def _build_record(
    transaction_id: str,
    plan_hash: str,
    service_id: str,
    action: str,
    claims: ReservationClaims,
    status: str,
    created_at: str,
    updated_at: str,
) -> ReservationRecord:
    claims_dict = claims.to_dict()
    claims_digest = _sha256hex(_canonical_json_bytes(claims_dict))
    base: dict[str, Any] = {
        "schema": SCHEMA,
        "transactionId": transaction_id,
        "planHash": plan_hash,
        "serviceId": service_id,
        "action": action,
        "status": status,
        "createdAt": created_at,
        "updatedAt": updated_at,
        "claims": claims_dict,
        "claimsDigest": claims_digest,
    }
    record_sha256 = _sha256hex(_canonical_json_bytes(base))
    return ReservationRecord(
        schema=SCHEMA,
        transaction_id=transaction_id,
        plan_hash=plan_hash,
        service_id=service_id,
        action=action,
        status=status,
        created_at=created_at,
        updated_at=updated_at,
        claims=claims,
        claims_digest=claims_digest,
        record_sha256=record_sha256,
        duplicate=False,
    )


def _record_to_dict(record: ReservationRecord) -> dict[str, Any]:
    return {
        "schema": record.schema,
        "transactionId": record.transaction_id,
        "planHash": record.plan_hash,
        "serviceId": record.service_id,
        "action": record.action,
        "status": record.status,
        "createdAt": record.created_at,
        "updatedAt": record.updated_at,
        "claims": record.claims.to_dict(),
        "claimsDigest": record.claims_digest,
        "recordSha256": record.record_sha256,
    }


def _claims_dict_to_claims(value: dict[str, Any]) -> ReservationClaims:
    ports = [
        HostPort(port=entry["port"], protocol=entry["protocol"])
        for entry in value["hostPorts"]
    ]
    return ReservationClaims(
        host_ports=tuple(ports),
        exclusive=tuple(value["exclusive"]),
    )


def _dict_to_record(
    value: dict[str, Any],
    *,
    duplicate: bool = False,
) -> ReservationRecord:
    return ReservationRecord(
        schema=value["schema"],
        transaction_id=value["transactionId"],
        plan_hash=value["planHash"],
        service_id=value["serviceId"],
        action=value["action"],
        status=value["status"],
        created_at=value["createdAt"],
        updated_at=value["updatedAt"],
        claims=_claims_dict_to_claims(value["claims"]),
        claims_digest=value["claimsDigest"],
        record_sha256=value["recordSha256"],
        duplicate=duplicate,
    )


def _validate_timestamp_field(value: Any) -> datetime:
    if not isinstance(value, str):
        _fail("corrupt-timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        _fail("corrupt-timestamp")
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        _fail("corrupt-timestamp")
    return parsed


def _validate_record(record: Any) -> None:
    """Validate one record dict from disk. Raises on any fault."""
    if not isinstance(record, dict):
        _fail("corrupt-record-keys")
    required_keys = {
        "schema",
        "transactionId",
        "planHash",
        "serviceId",
        "action",
        "status",
        "createdAt",
        "updatedAt",
        "claims",
        "claimsDigest",
        "recordSha256",
    }
    if set(record.keys()) != required_keys:
        _fail("corrupt-record-keys")
    if record["schema"] != SCHEMA:
        _fail("corrupt-record-schema")

    transaction_id = record["transactionId"]
    if not isinstance(transaction_id, str) or TXN_RE.fullmatch(transaction_id) is None:
        _fail("corrupt-transaction-id")
    plan_hash = record["planHash"]
    if not isinstance(plan_hash, str) or HEX64_RE.fullmatch(plan_hash) is None:
        _fail("corrupt-plan-hash")
    service_id = record["serviceId"]
    if not isinstance(service_id, str) or SERVICE_RE.fullmatch(service_id) is None:
        _fail("corrupt-service-id")

    action = record["action"]
    if not isinstance(action, str) or action not in VALID_ACTIONS:
        _fail("corrupt-action")
    status = record["status"]
    if not isinstance(status, str) or status not in VALID_STATUSES:
        _fail("corrupt-status")

    created = _validate_timestamp_field(record["createdAt"])
    updated = _validate_timestamp_field(record["updatedAt"])
    if created > updated:
        _fail("corrupt-timestamp-order")

    claims = record["claims"]
    if not isinstance(claims, dict):
        _fail("corrupt-claims")
    if set(claims.keys()) != {"hostPorts", "exclusive"}:
        _fail("corrupt-claims-keys")
    host_ports = claims["hostPorts"]
    exclusive = claims["exclusive"]
    if not isinstance(host_ports, list) or not isinstance(exclusive, list):
        _fail("corrupt-claims-types")

    previous: tuple[int, str] | None = None
    for entry in host_ports:
        if not isinstance(entry, dict) or set(entry.keys()) != {"port", "protocol"}:
            _fail("corrupt-port-entry")
        port = entry["port"]
        protocol = entry["protocol"]
        if isinstance(port, bool) or not isinstance(port, int):
            _fail("corrupt-port-range")
        if port < 1 or port > 65535:
            _fail("corrupt-port-range")
        if protocol not in ("tcp", "udp"):
            _fail("corrupt-port-protocol")
        key = (port, protocol)
        if previous is not None and key <= previous:
            _fail("corrupt-port-order")
        previous = key

    previous_token: str | None = None
    for token in exclusive:
        if not isinstance(token, str):
            _fail("corrupt-exclusive-type")
        if EXCLUSIVE_RE.fullmatch(token) is None:
            _fail("corrupt-exclusive-token")
        if previous_token is not None and token <= previous_token:
            _fail("corrupt-exclusive-order")
        previous_token = token

    if record["claimsDigest"] != _sha256hex(_canonical_json_bytes(claims)):
        _fail("corrupt-claims-digest")

    without_sha = {k: v for k, v in record.items() if k != "recordSha256"}
    if record["recordSha256"] != _sha256hex(_canonical_json_bytes(without_sha)):
        _fail("corrupt-record-sha")


def _validate_snapshot(data: Any) -> list[dict[str, Any]]:
    """Validate the complete snapshot state. Raises on any fault."""
    if not isinstance(data, dict):
        _fail("corrupt-snapshot-schema")
    if set(data.keys()) != {"schema", "records"}:
        _fail("corrupt-snapshot-schema")
    if data["schema"] != STORE_SCHEMA:
        _fail("corrupt-snapshot-schema")
    records = data["records"]
    if not isinstance(records, list):
        _fail("corrupt-records-type")
    if len(records) > MAX_RECORDS:
        _fail("oversize-records")

    triples: set[tuple[str, str, str]] = set()
    previous_triple: tuple[str, str, str] | None = None
    for record in records:
        _validate_record(record)
        triple = (record["transactionId"], record["planHash"], record["serviceId"])
        if triple in triples:
            _fail("corrupt-duplicate-binding")
        if previous_triple is not None and triple <= previous_triple:
            _fail("corrupt-record-order")
        triples.add(triple)
        previous_triple = triple

    active_ports: list[set[tuple[int, str]]] = []
    active_tokens: list[set[str]] = []
    for record in records:
        if record["status"] != ACTIVE:
            continue
        ports = {
            (entry["port"], entry["protocol"])
            for entry in record["claims"]["hostPorts"]
        }
        tokens = set(record["claims"]["exclusive"])
        for index in range(len(active_ports)):
            if ports & active_ports[index] or tokens & active_tokens[index]:
                _fail("corrupt-claim-conflict")
        active_ports.append(ports)
        active_tokens.append(tokens)
    return records


def _parse_snapshot(raw: bytes) -> list[dict[str, Any]]:
    """Decode strictly, validate, then require canonical byte equality."""
    try:
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_no_duplicate_keys,
            parse_float=_reject_number,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, TypeError, RecursionError):
        _fail("corrupt-snapshot-json")
    records = _validate_snapshot(value)
    if _canonical_json_bytes(value) != raw:
        _fail("snapshot-noncanonical")
    return records


# ---------------------------------------------------------------------------
# Platform and root custody
# ---------------------------------------------------------------------------


def _validate_platform() -> None:
    required = ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")
    if (
        os.name != "posix"
        or any(not hasattr(os, name) for name in required)
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
        or os.stat not in os.supports_follow_symlinks
        or os.unlink not in os.supports_dir_fd
    ):
        _fail("platform-unsupported")


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _file_read_flags() -> int:
    return os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)


def _close_quietly(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


def _root_components(value: Any) -> tuple[str, ...]:
    """Validate the absolute root value strictly, without following it."""
    if not isinstance(value, str) or "\x00" in value:
        _fail("root-invalid")
    if not value.startswith("/") or value == "/":
        _fail("root-invalid")
    parts = value.split("/")[1:]
    if any(part in {"", ".", ".."} for part in parts):
        _fail("root-invalid")
    return tuple(parts)


def _open_root(parts: tuple[str, ...]) -> int:
    """Traverse every component from the anchor descriptor-relative; prove custody."""
    try:
        descriptor = os.open("/", _directory_flags())
    except OSError:
        _fail("root-missing")
    try:
        info = os.fstat(descriptor)
    except OSError:
        _close_quietly(descriptor)
        _fail("root-io-error")
    try:
        if not stat.S_ISDIR(info.st_mode):
            _close_quietly(descriptor)
            _fail("root-invalid")
        for component in parts:
            parent = descriptor
            descriptor = -1
            try:
                descriptor = os.open(component, _directory_flags(), dir_fd=parent)
            except FileNotFoundError:
                _fail("root-missing")
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    _fail("root-invalid")
                _fail("root-io-error")
            finally:
                _close_quietly(parent)
            try:
                info = os.fstat(descriptor)
            except OSError:
                _fail("root-io-error")
            if not stat.S_ISDIR(info.st_mode):
                _fail("root-invalid")
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != _ROOT_MODE:
            _fail("root-custody-violation")
        return descriptor
    except BaseException:
        if descriptor >= 0:
            _close_quietly(descriptor)
        raise


# ---------------------------------------------------------------------------
# Snapshot file I/O
# ---------------------------------------------------------------------------


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
        _fail("snapshot-io-error")
    if not stat.S_ISREG(info.st_mode):
        _fail("corrupt-snapshot-file")
    if info.st_uid != os.geteuid():
        _fail("corrupt-snapshot-owner")
    if stat.S_IMODE(info.st_mode) != _FILE_MODE:
        _fail("corrupt-snapshot-mode")
    if info.st_nlink != 1:
        _fail("corrupt-snapshot-nlink")
    if info.st_size > MAX_FILE_BYTES:
        _fail("oversize-file")
    return info


def _read_all(descriptor: int, size: int) -> bytes:
    content = bytearray()
    try:
        while len(content) <= MAX_FILE_BYTES:
            remaining = MAX_FILE_BYTES + 1 - len(content)
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            content.extend(chunk)
    except OSError:
        _fail("snapshot-io-error")
    if len(content) > MAX_FILE_BYTES:
        _fail("oversize-file")
    if len(content) != size:
        _fail("snapshot-integrity")
    return bytes(content)


def _read_snapshot(root_fd: int) -> bytes:
    """Descriptor-relative read of the state file with full custody checks.

    Missing state is reported only on FileNotFoundError; temp names are
    ignored because only the exact state name is opened.
    """
    try:
        fd = os.open(SNAPSHOT_NAME, _file_read_flags(), dir_fd=root_fd)
    except FileNotFoundError:
        _fail("snapshot-missing")
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            _fail("snapshot-integrity")
        _fail("snapshot-io-error")
    try:
        before = _check_snapshot_file(fd)
        raw = _read_all(fd, before.st_size)
        after = _check_snapshot_file(fd)
        if _identity(before) != _identity(after):
            _fail("snapshot-integrity")
        try:
            path_info = os.stat(SNAPSHOT_NAME, dir_fd=root_fd, follow_symlinks=False)
        except OSError:
            _fail("snapshot-integrity")
        if (path_info.st_dev, path_info.st_ino) != (after.st_dev, after.st_ino):
            _fail("snapshot-integrity")
        return raw
    finally:
        _close_quietly(fd)


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    try:
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                _fail("snapshot-io-error")
            offset += written
    except OSError:
        _fail("snapshot-io-error")


def _cleanup_temp(root_fd: int, temp_name: str, expected: os.stat_result) -> None:
    """Unlink the temp entry descriptor-relative only when identity matches."""
    try:
        current = os.stat(temp_name, dir_fd=root_fd, follow_symlinks=False)
    except OSError:
        return
    if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
        return
    try:
        os.unlink(temp_name, dir_fd=root_fd)
    except OSError:
        pass


def _write_snapshot(root_fd: int, records: list[dict[str, Any]]) -> None:
    """Atomic write: unpredictable temp, complete write, fsync, replace, verify."""
    envelope = {"schema": STORE_SCHEMA, "records": records}
    # Never replace a readable snapshot with state that this store would reject
    # on the next call. Validate before even allocating a temporary entry.
    _validate_snapshot(envelope)
    payload = _canonical_json_bytes(envelope)
    if len(payload) > MAX_FILE_BYTES:
        _fail("oversize-file")

    temp_name = TEMP_PREFIX + secrets.token_hex(16) + TEMP_SUFFIX
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        temp_fd = os.open(temp_name, flags, _FILE_MODE, dir_fd=root_fd)
    except OSError:
        _fail("snapshot-io-error")

    temp_info: os.stat_result | None = None
    replaced = False
    try:
        try:
            temp_info = os.fstat(temp_fd)
        except OSError:
            _fail("snapshot-io-error")
        if (
            not stat.S_ISREG(temp_info.st_mode)
            or temp_info.st_uid != os.geteuid()
            or stat.S_IMODE(temp_info.st_mode) != _FILE_MODE
            or temp_info.st_nlink != 1
        ):
            _fail("snapshot-integrity")

        _write_all(temp_fd, payload)
        try:
            os.fsync(temp_fd)
            sealed = os.fstat(temp_fd)
        except OSError:
            _fail("snapshot-io-error")
        if (
            (sealed.st_dev, sealed.st_ino) != (temp_info.st_dev, temp_info.st_ino)
            or not stat.S_ISREG(sealed.st_mode)
            or sealed.st_uid != os.geteuid()
            or sealed.st_size != len(payload)
            or stat.S_IMODE(sealed.st_mode) != _FILE_MODE
            or sealed.st_nlink != 1
        ):
            _fail("snapshot-integrity")

        # Narrow the same-UID name-swap window before rename. Python's stdlib
        # cannot atomically bind rename's source name to our open descriptor, so
        # the exact payload readback below remains the final authority.
        try:
            named_temp = os.stat(temp_name, dir_fd=root_fd, follow_symlinks=False)
        except OSError:
            _fail("snapshot-integrity")
        if _identity(named_temp) != _identity(sealed):
            _fail("snapshot-integrity")

        try:
            os.replace(
                temp_name,
                SNAPSHOT_NAME,
                src_dir_fd=root_fd,
                dst_dir_fd=root_fd,
            )
            replaced = True
        except OSError:
            _fail("snapshot-io-error")

        try:
            os.fsync(root_fd)
        except OSError:
            _fail("snapshot-io-error")

        # Reopen and require the exact payload bytes.
        if _read_snapshot(root_fd) != payload:
            _fail("snapshot-integrity")
    finally:
        _close_quietly(temp_fd)
        if not replaced and temp_info is not None:
            _cleanup_temp(root_fd, temp_name, temp_info)


def _read_records(root_fd: int) -> list[dict[str, Any]]:
    """Read the snapshot under the held root-fd lock; missing state is empty."""
    try:
        raw = _read_snapshot(root_fd)
    except ReservationStoreError as exc:
        if exc.code == "snapshot-missing":
            return []
        raise
    return _parse_snapshot(raw)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class ResourceReservationStore:
    """Snapshot-backed resource reservation store in an owner-private root.

    The root is re-validated and re-opened for every public call; the exclusive
    ``flock`` is taken on the opened root descriptor and explicitly released in
    a ``finally`` block before the descriptor is closed.
    """

    def __init__(self, root_path: str | os.PathLike[str]) -> None:
        _validate_platform()
        try:
            raw = os.fspath(root_path)
        except (TypeError, ValueError):
            _fail("root-invalid")
        self._root_parts = _root_components(raw)
        # Prove custody eagerly; every public call re-proves it by reopening.
        _close_quietly(_open_root(self._root_parts))

    def _run_locked(self, operation: Any) -> Any:
        """Re-open the root, lock it exclusively, and run ``operation(root_fd)``."""
        root_fd = _open_root(self._root_parts)
        locked = False
        try:
            try:
                fcntl.flock(root_fd, fcntl.LOCK_EX)
            except OSError:
                _fail("lock-error")
            locked = True
            return operation(root_fd)
        finally:
            if locked:
                try:
                    fcntl.flock(root_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            _close_quietly(root_fd)

    # -- readers -------------------------------------------------------------

    def snapshot(
        self,
        transaction_id: str,
        plan_hash: str,
        service_id: str,
    ) -> ReservationRecord | None:
        """Return the record for the given binding, or ``None`` if absent."""
        transaction_id = _validate_transaction_id(transaction_id)
        plan_hash = _validate_plan_hash(plan_hash)
        service_id = _validate_service_id(service_id)

        def operation(root_fd: int) -> ReservationRecord | None:
            for record in _read_records(root_fd):
                if (
                    record["transactionId"] == transaction_id
                    and record["planHash"] == plan_hash
                    and record["serviceId"] == service_id
                ):
                    return _dict_to_record(record)
            return None

        return self._run_locked(operation)

    def active(self) -> tuple[ReservationRecord, ...]:
        """Return all active records sorted by (transactionId, planHash, serviceId)."""

        def operation(root_fd: int) -> tuple[ReservationRecord, ...]:
            matches = [
                record
                for record in _read_records(root_fd)
                if record["status"] == ACTIVE
            ]
            matches.sort(
                key=lambda r: (r["transactionId"], r["planHash"], r["serviceId"])
            )
            return tuple(_dict_to_record(record) for record in matches)

        return self._run_locked(operation)

    # -- writers -------------------------------------------------------------

    def reserve(
        self,
        transaction_id: str,
        plan_hash: str,
        service_id: str,
        action: str,
        claims: ReservationClaims,
        now: str,
    ) -> ReservationRecord:
        """Reserve extension resources.

        An exact replay of an active record (same action and same claims)
        returns the persisted record with ``duplicate=True``.  An active replay
        with a different action or different claims raises
        ``resource-reservation-binding-conflict``.  Any reserve against a
        terminal record with the same binding raises
        ``resource-reservation-terminal-duplicate``.  Host-port or exclusive
        overlap with any other active record raises
        ``resource-reservation-claim-conflict``.
        """
        transaction_id = _validate_transaction_id(transaction_id)
        plan_hash = _validate_plan_hash(plan_hash)
        service_id = _validate_service_id(service_id)
        action = _validate_action(action)
        _validate_claims(claims)
        _validate_timestamp(now)

        def operation(root_fd: int) -> ReservationRecord:
            records = _read_records(root_fd)

            for record in records:
                if (
                    record["transactionId"] == transaction_id
                    and record["planHash"] == plan_hash
                    and record["serviceId"] == service_id
                ):
                    if record["status"] == ACTIVE:
                        existing = _claims_dict_to_claims(record["claims"])
                        if (
                            record["action"] == action
                            and existing.host_ports == claims.host_ports
                            and existing.exclusive == claims.exclusive
                        ):
                            return _dict_to_record(record, duplicate=True)
                        _fail("resource-reservation-binding-conflict")
                    _fail("resource-reservation-terminal-duplicate")

            new_ports = {(hp.port, hp.protocol) for hp in claims.host_ports}
            new_tokens = set(claims.exclusive)
            for record in records:
                if record["status"] != ACTIVE:
                    continue
                active_ports = {
                    (entry["port"], entry["protocol"])
                    for entry in record["claims"]["hostPorts"]
                }
                if active_ports & new_ports:
                    _fail("resource-reservation-claim-conflict")
                if set(record["claims"]["exclusive"]) & new_tokens:
                    _fail("resource-reservation-claim-conflict")

            if len(records) >= MAX_RECORDS:
                _fail("oversize-records")

            new_record = _build_record(
                transaction_id,
                plan_hash,
                service_id,
                action,
                claims,
                ACTIVE,
                now,
                now,
            )
            records.append(_record_to_dict(new_record))
            records.sort(
                key=lambda record: (
                    record["transactionId"],
                    record["planHash"],
                    record["serviceId"],
                )
            )
            _write_snapshot(root_fd, records)
            return new_record

        return self._run_locked(operation)

    def finish(
        self,
        transaction_id: str,
        plan_hash: str,
        service_id: str,
        status: str,
        now: str,
    ) -> ReservationRecord:
        """Transition an active record to ``released`` or ``failed``.

        An identical terminal replay returns the persisted record with
        ``duplicate=True``.  Any other transition against a non-active record
        or an absent binding raises ``transition-invalid``.
        """
        transaction_id = _validate_transaction_id(transaction_id)
        plan_hash = _validate_plan_hash(plan_hash)
        service_id = _validate_service_id(service_id)
        status = _validate_terminal_status(status)
        _validate_timestamp(now)

        def operation(root_fd: int) -> ReservationRecord:
            records = _read_records(root_fd)

            index: int | None = None
            for position, record in enumerate(records):
                if (
                    record["transactionId"] == transaction_id
                    and record["planHash"] == plan_hash
                    and record["serviceId"] == service_id
                ):
                    index = position
                    break
            if index is None:
                _fail("transition-invalid")

            existing = records[index]
            if existing["status"] == status:
                return _dict_to_record(existing, duplicate=True)
            if existing["status"] != ACTIVE:
                _fail("transition-invalid")
            now_value = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ")
            created_value = _validate_timestamp_field(existing["createdAt"])
            updated_value = _validate_timestamp_field(existing["updatedAt"])
            if now_value < created_value or now_value < updated_value:
                _fail("timestamp-invalid")

            finished = dict(existing)
            finished["status"] = status
            finished["updatedAt"] = now
            finished["recordSha256"] = _sha256hex(
                _canonical_json_bytes(
                    {k: v for k, v in finished.items() if k != "recordSha256"}
                )
            )
            records[index] = finished
            _write_snapshot(root_fd, records)
            return _dict_to_record(finished)

        return self._run_locked(operation)

    def batch_release(
        self,
        transaction_id: str,
        plan_hash: str,
        expectations: tuple[ReleaseExpectation, ...],
        now: str,
    ) -> tuple[ReservationRecord, ...]:
        """Atomically transition a batch of active reservations to released.

        Every record in ``expectations`` is re-proved against the *same*
        snapshot read under the store's exclusive lock: transaction/plan/
        service binding, action, claims, status, and timestamp ordering.

        If any expectation cannot be matched or validated, the entire batch
        fails before effect.  A post-write readback failure is handled by
        re-observing the persisted state: success only if every targeted
        record exactly matches the released state; otherwise a single
        value-free failure is emitted.

        A replay where every targeted record is already released returns the
        persisted records with ``duplicate=True``.  Mixed active/released
        state is rejected without a write because an atomic batch cannot
        produce it.
        """
        if type(transaction_id) is not str or type(plan_hash) is not str:
            _fail("binding-invalid")
        if type(now) is not str:
            _fail("timestamp-invalid")
        transaction_id = _validate_transaction_id(transaction_id)
        plan_hash = _validate_plan_hash(plan_hash)
        _validate_timestamp(now)

        def operation(root_fd: int) -> tuple[ReservationRecord, ...]:
            # Re-prove and detach every caller-controlled expectation while
            # holding the same lock used for the authoritative snapshot.
            if type(expectations) is not tuple or not expectations:
                _fail("binding-invalid")
            service_ids: list[str] = []
            expectation_map: dict[str, tuple[str, dict[str, Any]]] = {}
            for expectation in expectations:
                if type(expectation) is not ReleaseExpectation:
                    _fail("binding-invalid")
                if type(expectation.service_id) is not str:
                    _fail("binding-invalid")
                service_id = _validate_service_id(expectation.service_id)
                if type(expectation.action) is not str:
                    _fail("binding-invalid")
                action = _validate_action(expectation.action)
                claims = _release_claims_dict(expectation.claims)
                if service_id in expectation_map:
                    _fail("binding-invalid")
                expectation_map[service_id] = (action, claims)
                service_ids.append(service_id)

            service_ids_tuple = tuple(service_ids)
            service_set = frozenset(service_ids_tuple)
            now_value = _validate_timestamp_field(now)
            records = _read_records(root_fd)

            # Locate every target under one snapshot read
            targets: dict[str, int] = {}
            for position, record in enumerate(records):
                if (
                    record["transactionId"] == transaction_id
                    and record["planHash"] == plan_hash
                    and record["serviceId"] in service_set
                ):
                    if record["serviceId"] in targets:
                        _fail("corrupt-duplicate-binding")
                    targets[record["serviceId"]] = position

            if len(targets) != len(service_ids_tuple):
                _fail("transition-invalid")

            # Validate every target, including already-released replay records,
            # against its exact detached expectation before any effect.
            statuses: list[str] = []
            for service_id in service_ids_tuple:
                record = records[targets[service_id]]
                status = record["status"]
                if status not in (ACTIVE, RELEASED):
                    _fail("transition-invalid")
                action, claims = expectation_map[service_id]
                if record["action"] != action or record["claims"] != claims:
                    _fail("transition-invalid")
                created_value = _validate_timestamp_field(record["createdAt"])
                updated_value = _validate_timestamp_field(record["updatedAt"])
                if now_value < created_value or now_value < updated_value:
                    _fail("timestamp-invalid")
                statuses.append(status)

            # Exact replay: all targets already released
            if all(status == RELEASED for status in statuses):
                return tuple(
                    _dict_to_record(records[targets[sid]], duplicate=True)
                    for sid in service_ids_tuple
                )

            # An atomic batch can be either wholly active or wholly released.
            # Mixed state proves this is not an exact replay of this batch.
            if any(status == RELEASED for status in statuses):
                _fail("transition-invalid")

            # Transition active records to released
            for sid in service_ids_tuple:
                idx = targets[sid]
                rec = records[idx]
                if rec["status"] == RELEASED:
                    continue
                finished = dict(rec)
                finished["status"] = RELEASED
                finished["updatedAt"] = now
                finished["recordSha256"] = _sha256hex(
                    _canonical_json_bytes(
                        {k: v for k, v in finished.items() if k != "recordSha256"}
                    )
                )
                records[idx] = finished

            try:
                _write_snapshot(root_fd, records)
            except Exception:  # noqa: BLE001 -- reconcile an ambiguous publish
                # Post-write ambiguity: the atomic rename may have succeeded
                # before the error was raised.  Re-observe the exact persisted
                # state and return success only if every targeted record
                # exactly matches the expected released state.
                try:
                    post_records = _read_records(root_fd)
                except ReservationStoreError:
                    _fail("snapshot-integrity")
                post_targets: dict[str, int] = {}
                for position, record in enumerate(post_records):
                    if (
                        record["transactionId"] == transaction_id
                        and record["planHash"] == plan_hash
                        and record["serviceId"] in service_set
                    ):
                        service_id = record["serviceId"]
                        if service_id in post_targets:
                            _fail("snapshot-integrity")
                        post_targets[service_id] = position

                if len(post_targets) != len(service_ids_tuple):
                    _fail("snapshot-integrity")

                for sid in service_ids_tuple:
                    expected = records[targets[sid]]
                    observed = post_records[post_targets[sid]]
                    if observed != expected:
                        _fail("snapshot-integrity")
                return tuple(
                    _dict_to_record(post_records[post_targets[sid]])
                    for sid in service_ids_tuple
                )

            return tuple(
                _dict_to_record(records[targets[sid]]) for sid in service_ids_tuple
            )

        return self._run_locked(operation)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------

__all__ = [
    "ACTIVE",
    "BATCH_RELEASE_SCHEMA",
    "FAILED",
    "RELEASED",
    "SCHEMA",
    "STORE_SCHEMA",
    "HostPort",
    "ReleaseExpectation",
    "ReservationClaims",
    "ReservationRecord",
    "ReservationStoreError",
    "ResourceReservationStore",
]
