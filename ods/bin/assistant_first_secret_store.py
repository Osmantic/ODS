"""Host-owned custody for Assistant First extension configuration secrets.

The store is deliberately Linux/POSIX-only for the first qualification phase.
It never returns secret values and never places them in exception messages.
Every record is bound to one transaction, plan hash, and configuration schema
hash; callers receive only an opaque reference and presence metadata.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import re
import secrets
import stat
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

try:  # pragma: no cover - unavailable by design on native Windows
    import fcntl
except ImportError:  # pragma: no cover - native Windows
    fcntl = None


STAGE_REQUEST_SCHEMA = "ods.assistant-first.secret-stage-request.v1"
STATUS_REQUEST_SCHEMA = "ods.assistant-first.secret-status-request.v1"
DELETE_REQUEST_SCHEMA = "ods.assistant-first.secret-delete-request.v1"
RECORD_SCHEMA = "ods.assistant-first.secret-record.v1"
STATUS_SCHEMA = "ods.assistant-first.secret-status.v1"

_TRANSACTION_RE = re.compile(r"^txn-[0-9a-f]{24}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_REFERENCE_RE = re.compile(r"^secret-v1-[0-9a-f]{48}$")
_MAX_SECRET_KEYS = 128
_MAX_SECRET_VALUE_LENGTH = 65_536
_MAX_RECORD_BYTES = 256 * 1024
_MAX_SAFE_INTEGER = (1 << 53) - 1
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | _CLOEXEC
)
_FILE_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_TEMPORARY_RE = re.compile(r"^\.txn-[0-9a-f]{24}\.json\.[0-9a-f]{24}\.tmp$")


class SecretStoreError(RuntimeError):
    """Stable, value-free failure safe to project across the host API."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"SecretStoreError({self.code!r})"


def _fail(code: str) -> None:
    raise SecretStoreError(code)


def _exact_dict(value: Any, keys: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        _fail(code)
    return value


def _safe_text(value: Any, pattern: re.Pattern[str], code: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _fail(code)
    return value


def _safe_secret_scalar(value: Any) -> None:
    if type(value) is bool:
        return
    if type(value) is int:
        if not -_MAX_SAFE_INTEGER <= value <= _MAX_SAFE_INTEGER:
            _fail("invalid-secret-value")
        return
    if isinstance(value, str):
        if not value or len(value) > _MAX_SECRET_VALUE_LENGTH:
            _fail("invalid-secret-value")
        for character in value:
            point = ord(character)
            if point == 0 or point == 127 or 0xD800 <= point <= 0xDFFF:
                _fail("invalid-secret-value")
            if point < 32 and character not in "\r\n\t":
                _fail("invalid-secret-value")
        return
    _fail("invalid-secret-value")


def _secret_map(value: Any) -> dict[str, Any]:
    if type(value) is not dict or not value or len(value) > _MAX_SECRET_KEYS:
        _fail("invalid-secret-values")
    result: dict[str, Any] = {}
    for key, item in value.items():
        _safe_text(key, _KEY_RE, "invalid-secret-key")
        _safe_secret_scalar(item)
        result[key] = item
    return result


def _canonical_bytes(value: Any) -> bytes:
    try:
        encoded = (
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
        _fail("invalid-secret-document")
    if len(encoded) > _MAX_RECORD_BYTES:
        _fail("secret-document-too-large")
    return encoded


def _duplicate_object_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("secret-record-integrity")
        result[key] = value
    return result


def _reject_number(_value: str) -> None:
    _fail("secret-record-integrity")


class AssistantFirstSecretStore:
    """POSIX dirfd-backed, transaction-bound secret record store."""

    _process_lock = threading.RLock()

    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir)

    @staticmethod
    def _require_posix() -> None:
        if (
            os.name != "posix"
            or fcntl is None
            or not hasattr(os, "O_DIRECTORY")
            or not hasattr(os, "O_NOFOLLOW")
        ):
            _fail("secret-store-platform-unqualified")

    @staticmethod
    def _verify_owned_directory(descriptor: int, *, root: bool) -> None:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            _fail("secret-store-directory-integrity")
        if metadata.st_uid != os.geteuid():
            _fail("secret-store-owner-mismatch")
        mode = stat.S_IMODE(metadata.st_mode)
        if root:
            if mode & 0o022:
                _fail("secret-store-root-permissions")
        elif mode != 0o700:
            _fail("secret-store-directory-permissions")

    @classmethod
    def _open_child_directory(cls, parent_fd: int, name: str) -> int:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        except OSError:
            _fail("secret-store-directory-unavailable")
        try:
            descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
        except OSError:
            _fail("secret-store-directory-integrity")
        try:
            cls._verify_owned_directory(descriptor, root=False)
        except Exception:
            os.close(descriptor)
            raise
        return descriptor

    @contextlib.contextmanager
    def _store_directory(self) -> Iterator[int]:
        self._require_posix()
        try:
            root_info = self.data_dir.lstat()
        except OSError:
            _fail("secret-store-root-unavailable")
        if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
            _fail("secret-store-root-integrity")
        try:
            data_fd = os.open(self.data_dir, _DIRECTORY_FLAGS)
        except OSError:
            _fail("secret-store-root-integrity")
        assistant_fd = -1
        secrets_fd = -1
        try:
            opened = os.fstat(data_fd)
            if (opened.st_dev, opened.st_ino) != (root_info.st_dev, root_info.st_ino):
                _fail("secret-store-root-race")
            self._verify_owned_directory(data_fd, root=True)
            assistant_fd = self._open_child_directory(data_fd, "assistant-first")
            secrets_fd = self._open_child_directory(assistant_fd, "secrets")
            yield secrets_fd
        finally:
            if secrets_fd >= 0:
                os.close(secrets_fd)
            if assistant_fd >= 0:
                os.close(assistant_fd)
            os.close(data_fd)

    @contextlib.contextmanager
    def _locked_directory(self) -> Iterator[int]:
        with self._process_lock, self._store_directory() as directory_fd:
            flags = os.O_RDWR | os.O_CREAT | _FILE_NOFOLLOW | _CLOEXEC
            try:
                lock_fd = os.open(".lock", flags, 0o600, dir_fd=directory_fd)
            except OSError:
                _fail("secret-store-lock-unavailable")
            try:
                metadata = os.fstat(lock_fd)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_nlink != 1
                    or metadata.st_uid != os.geteuid()
                ):
                    _fail("secret-store-lock-integrity")
                os.fchmod(lock_fd, 0o600)
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                self._recover_temporary_files(directory_fd)
                yield directory_fd
            except OSError:
                _fail("secret-store-lock-unavailable")
            finally:
                with contextlib.suppress(OSError):
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)

    @staticmethod
    def _filename(transaction_id: str) -> str:
        return f"{transaction_id}.json"

    @staticmethod
    def _verify_record_file(metadata: os.stat_result) -> None:
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > _MAX_RECORD_BYTES
        ):
            _fail("secret-record-integrity")

    @staticmethod
    def _recover_temporary_files(directory_fd: int) -> None:
        """Remove only owner-only orphan files left by an interrupted swap."""
        try:
            names = os.listdir(directory_fd)
        except OSError:
            _fail("secret-store-recovery-failed")
        for name in names:
            if _TEMPORARY_RE.fullmatch(name) is None:
                continue
            try:
                metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError:
                _fail("secret-temporary-integrity")
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                _fail("secret-temporary-integrity")
            try:
                os.unlink(name, dir_fd=directory_fd)
                os.fsync(directory_fd)
            except OSError:
                _fail("secret-store-recovery-failed")

    def _read_record(
        self, directory_fd: int, transaction_id: str
    ) -> dict[str, Any] | None:
        filename = self._filename(transaction_id)
        try:
            before = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError:
            _fail("secret-record-unavailable")
        self._verify_record_file(before)
        try:
            descriptor = os.open(
                filename,
                os.O_RDONLY | _FILE_NOFOLLOW | _CLOEXEC,
                dir_fd=directory_fd,
            )
        except OSError:
            _fail("secret-record-integrity")
        try:
            opened = os.fstat(descriptor)
            self._verify_record_file(opened)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                _fail("secret-record-race")
            chunks: list[bytes] = []
            remaining = opened.st_size
            while remaining:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    _fail("secret-record-integrity")
                chunks.append(chunk)
                remaining -= len(chunk)
        finally:
            os.close(descriptor)
        try:
            record = json.loads(
                b"".join(chunks).decode("utf-8", errors="strict"),
                object_pairs_hook=_duplicate_object_hook,
                parse_float=_reject_number,
                parse_constant=_reject_number,
            )
        except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            _fail("secret-record-integrity")
        return self._validated_record(record)

    @staticmethod
    def _validated_record(value: Any) -> dict[str, Any]:
        keys = frozenset(
            {
                "schema",
                "transactionId",
                "planHash",
                "schemaHash",
                "idempotencyKey",
                "reference",
                "secretValues",
            }
        )
        record = _exact_dict(value, keys, "secret-record-integrity")
        if record["schema"] != RECORD_SCHEMA:
            _fail("secret-record-integrity")
        _safe_text(record["transactionId"], _TRANSACTION_RE, "secret-record-integrity")
        _safe_text(record["planHash"], _HASH_RE, "secret-record-integrity")
        _safe_text(record["schemaHash"], _HASH_RE, "secret-record-integrity")
        _safe_text(record["idempotencyKey"], _HASH_RE, "secret-record-integrity")
        _safe_text(record["reference"], _REFERENCE_RE, "secret-record-integrity")
        _secret_map(record["secretValues"])
        return record

    @staticmethod
    def _binding(payload: dict[str, Any]) -> tuple[str, str, str]:
        return (
            _safe_text(
                payload["transactionId"], _TRANSACTION_RE, "invalid-transaction-id"
            ),
            _safe_text(payload["planHash"], _HASH_RE, "invalid-plan-hash"),
            _safe_text(payload["schemaHash"], _HASH_RE, "invalid-schema-hash"),
        )

    @staticmethod
    def _assert_binding(
        record: dict[str, Any], transaction_id: str, plan_hash: str, schema_hash: str
    ) -> None:
        if (
            record["transactionId"] != transaction_id
            or record["planHash"] != plan_hash
            or record["schemaHash"] != schema_hash
        ):
            _fail("secret-binding-mismatch")

    @staticmethod
    def _status(
        record: dict[str, Any], *, duplicate: bool | None = None
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": STATUS_SCHEMA,
            "transactionId": record["transactionId"],
            "planHash": record["planHash"],
            "schemaHash": record["schemaHash"],
            "reference": record["reference"],
            "configured": True,
            "presentSecretKeys": sorted(record["secretValues"]),
        }
        if duplicate is not None:
            result["duplicate"] = duplicate
        return result

    def _atomic_write(self, directory_fd: int, filename: str, content: bytes) -> None:
        try:
            existing = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        except OSError:
            _fail("secret-record-unavailable")
        if existing is not None:
            self._verify_record_file(existing)

        descriptor = -1
        temporary = ""
        for _attempt in range(32):
            temporary = f".{filename}.{secrets.token_hex(12)}.tmp"
            try:
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_NOFOLLOW | _CLOEXEC,
                    0o600,
                    dir_fd=directory_fd,
                )
                break
            except FileExistsError:
                continue
            except OSError:
                _fail("secret-record-write-failed")
        if descriptor < 0:
            _fail("secret-record-write-failed")
        try:
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(content):
                written = os.write(descriptor, content[offset:])
                if written <= 0:
                    _fail("secret-record-write-failed")
                offset += written
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(
                temporary,
                filename,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            temporary = ""
            os.fsync(directory_fd)
        except OSError:
            _fail("secret-record-write-failed")
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary:
                with contextlib.suppress(OSError):
                    os.unlink(temporary, dir_fd=directory_fd)

    def stage(self, payload: Any) -> dict[str, Any]:
        """Atomically stage values and return only an opaque reference."""
        keys = frozenset(
            {
                "schema",
                "transactionId",
                "planHash",
                "schemaHash",
                "idempotencyKey",
                "secretValues",
            }
        )
        request = _exact_dict(payload, keys, "invalid-secret-stage-request")
        if request["schema"] != STAGE_REQUEST_SCHEMA:
            _fail("invalid-secret-stage-schema")
        transaction_id, plan_hash, schema_hash = self._binding(request)
        idempotency_key = _safe_text(
            request["idempotencyKey"], _HASH_RE, "invalid-idempotency-key"
        )
        secret_values = _secret_map(request["secretValues"])
        with self._locked_directory() as directory_fd:
            current = self._read_record(directory_fd, transaction_id)
            if current is not None:
                self._assert_binding(current, transaction_id, plan_hash, schema_hash)
                if current["idempotencyKey"] == idempotency_key:
                    same = hmac.compare_digest(
                        _canonical_bytes(current["secretValues"]),
                        _canonical_bytes(secret_values),
                    )
                    if not same:
                        _fail("secret-idempotency-conflict")
                    return self._status(current, duplicate=True)

            record = {
                "schema": RECORD_SCHEMA,
                "transactionId": transaction_id,
                "planHash": plan_hash,
                "schemaHash": schema_hash,
                "idempotencyKey": idempotency_key,
                "reference": f"secret-v1-{secrets.token_hex(24)}",
                "secretValues": secret_values,
            }
            self._atomic_write(
                directory_fd, self._filename(transaction_id), _canonical_bytes(record)
            )
            return self._status(record, duplicate=False)

    def status(self, payload: Any) -> dict[str, Any]:
        """Verify one exact reference and return presence metadata only."""
        keys = frozenset(
            {"schema", "transactionId", "planHash", "schemaHash", "reference"}
        )
        request = _exact_dict(payload, keys, "invalid-secret-status-request")
        if request["schema"] != STATUS_REQUEST_SCHEMA:
            _fail("invalid-secret-status-schema")
        transaction_id, plan_hash, schema_hash = self._binding(request)
        reference = _safe_text(
            request["reference"], _REFERENCE_RE, "invalid-secret-reference"
        )
        with self._locked_directory() as directory_fd:
            record = self._read_record(directory_fd, transaction_id)
            if record is None:
                _fail("secret-record-not-found")
            self._assert_binding(record, transaction_id, plan_hash, schema_hash)
            if not hmac.compare_digest(record["reference"], reference):
                _fail("secret-reference-mismatch")
            return self._status(record)

    def delete(self, payload: Any) -> dict[str, Any]:
        """Delete only the record matching every supplied binding."""
        keys = frozenset(
            {"schema", "transactionId", "planHash", "schemaHash", "reference"}
        )
        request = _exact_dict(payload, keys, "invalid-secret-delete-request")
        if request["schema"] != DELETE_REQUEST_SCHEMA:
            _fail("invalid-secret-delete-schema")
        transaction_id, plan_hash, schema_hash = self._binding(request)
        reference = _safe_text(
            request["reference"], _REFERENCE_RE, "invalid-secret-reference"
        )
        with self._locked_directory() as directory_fd:
            record = self._read_record(directory_fd, transaction_id)
            if record is None:
                return {
                    "schema": STATUS_SCHEMA,
                    "transactionId": transaction_id,
                    "planHash": plan_hash,
                    "schemaHash": schema_hash,
                    "configured": False,
                    "deleted": False,
                    "presentSecretKeys": [],
                }
            self._assert_binding(record, transaction_id, plan_hash, schema_hash)
            if not hmac.compare_digest(record["reference"], reference):
                _fail("secret-reference-mismatch")
            try:
                os.unlink(self._filename(transaction_id), dir_fd=directory_fd)
                os.fsync(directory_fd)
            except OSError:
                _fail("secret-record-delete-failed")
            return {
                "schema": STATUS_SCHEMA,
                "transactionId": transaction_id,
                "planHash": plan_hash,
                "schemaHash": schema_hash,
                "configured": False,
                "deleted": True,
                "presentSecretKeys": [],
            }


__all__ = [
    "DELETE_REQUEST_SCHEMA",
    "STAGE_REQUEST_SCHEMA",
    "STATUS_REQUEST_SCHEMA",
    "STATUS_SCHEMA",
    "AssistantFirstSecretStore",
    "SecretStoreError",
]
