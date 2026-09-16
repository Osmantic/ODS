"""Atomically publish one approved library payload without starting an app.

The caller must supply only a ``VerifiedLibraryEffectInput`` produced under the
same host admission as this call. This dormant Linux substrate never reopens
the extensions library, evaluates Compose, invokes hooks, reads secrets, or
claims an application is healthy. It materializes the captured bytes into the
fixed user-extensions root with no-overwrite publication and exact replay.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows source inspection only
    fcntl = None

from extension_library_effect_input import VerifiedLibraryEffectInput
from extension_library_tree_digest import (
    LibraryTreeDigestError,
    LibraryTreeSnapshot,
    MAX_TREE_ENTRIES,
    snapshot_extension_tree,
    validate_library_tree_snapshot,
)
from extension_lifecycle_work import (
    LifecycleWorkExecutionError,
    LifecycleWorkUncertainEffect,
    LifecycleWorkValidationError,
)


RECEIPT_NAME = ".ods-library-receipt.json"
RECEIPT_SCHEMA = "ods.assistant-first.library-materialization.v1"
_LOCK_NAME = ".assistant-first-library-materializer.lock"
_TEMP_PREFIX = ".ods-assistant-first-materialization-"
_TEMP_SUFFIX = ".tmp"
_ROOT_MODE = 0o700
_DIRECTORY_MODE = 0o755
_FILE_MODE = 0o644
_EXECUTABLE_MODE = 0o755
_PRIVATE_FILE_MODE = 0o600
_RENAME_NOREPLACE = 1
_TRANSACTION_RE = re.compile(r"^txn-[0-9a-f]{24}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_TEMP_RE = re.compile(r"^\.ods-assistant-first-materialization-[0-9a-f]{64}\.tmp$")
_TEMP_DOMAIN = b"ods-assistant-first-library-materialization-temp-v1\0"


class LibraryInstallMaterializationError(LifecycleWorkExecutionError):
    """Stable value-free failure before publication is proven."""


class LibraryInstallMaterializationUncertain(LifecycleWorkUncertainEffect):
    """The target may have been published; observation must decide."""


@dataclass(frozen=True)
class LibraryInstallMaterializationResult:
    transaction_id: str
    plan_hash: str
    service_id: str
    source_tree_sha256: str
    receipt_sha256: str
    outcome: str  # "published" | "replayed"


def _deny(code: str) -> None:
    raise LifecycleWorkValidationError(code) from None


def _fail(code: str) -> None:
    raise LibraryInstallMaterializationError(code) from None


def _uncertain(code: str) -> None:
    raise LibraryInstallMaterializationUncertain(code) from None


def _validate_platform() -> Any:
    if (
        os.name != "posix"
        or fcntl is None
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or os.open not in os.supports_dir_fd
        or os.mkdir not in os.supports_dir_fd
        or os.rename not in os.supports_dir_fd
        or os.rmdir not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
        or os.stat not in os.supports_follow_symlinks
        or os.unlink not in os.supports_dir_fd
    ):
        _fail("library-materialization-platform-unsupported")
    try:
        library = ctypes.CDLL(None, use_errno=True)
        rename = library.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        return rename
    except (AttributeError, OSError, TypeError):
        _fail("library-materialization-platform-unsupported")


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _file_flags() -> int:
    return os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _owned_directory(descriptor: int, *, root: bool = False) -> os.stat_result:
    try:
        info = os.fstat(descriptor)
    except OSError:
        _fail("library-materialization-io-error")
    required_mode = _ROOT_MODE if root else None
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o022
        or (required_mode is not None and stat.S_IMODE(info.st_mode) != required_mode)
    ):
        _fail("library-materialization-custody-invalid")
    return info


def _open_root(root: Path) -> int:
    if (
        not isinstance(root, Path)
        or not root.is_absolute()
        or root == Path(root.anchor)
        or ".." in root.parts
    ):
        _deny("library-materialization-root-invalid")
    descriptor = -1
    try:
        descriptor = os.open(root.anchor, _directory_flags())
        for part in root.parts[1:]:
            child = os.open(part, _directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        _owned_directory(descriptor, root=True)
        return descriptor
    except (LibraryInstallMaterializationError, LifecycleWorkValidationError):
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail("library-materialization-root-unavailable")


def _entry(root: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=root, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError:
        _fail("library-materialization-io-error")


def _open_directory_at(parent: int, name: str) -> int:
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent)
        _owned_directory(descriptor)
        return descriptor
    except LibraryInstallMaterializationError:
        raise
    except OSError:
        _fail("library-materialization-custody-invalid")


def _open_relative_directory(root: int, relative: str) -> int:
    descriptor = os.dup(root)
    try:
        if relative:
            for part in relative.split("/"):
                child = _open_directory_at(descriptor, part)
                os.close(descriptor)
                descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    try:
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                _fail("library-materialization-io-error")
            offset += written
    except OSError:
        _fail("library-materialization-io-error")


def _canonical_json(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _deny("library-materialization-input-invalid")


def _legacy_digest(snapshot: LibraryTreeSnapshot) -> str:
    digest = hashlib.sha256()
    records = [(item, None) for item in snapshot.directories] + [
        (item.relative_path, item) for item in snapshot.files
    ]
    for relative, item in sorted(records, key=lambda pair: pair[0]):
        canonical = "compose.yaml" if relative == "compose.yaml.disabled" else relative
        if item is None:
            digest.update(f"D\0{canonical}\0".encode())
        else:
            digest.update(f"F\0{canonical}\0{int(item.executable)}\0".encode())
            digest.update(item.content)
            digest.update(b"\0")
    return digest.hexdigest()


def _receipt(effect: VerifiedLibraryEffectInput) -> bytes:
    snapshot = effect.payload
    legacy = _legacy_digest(snapshot)
    return _canonical_json(
        {
            "assistant_first": {
                "directory_count": len(snapshot.directories),
                "file_count": len(snapshot.files),
                "plan_hash": effect.plan_hash,
                "schema": RECEIPT_SCHEMA,
                "service_id": effect.service_id,
                "source_tree_sha256": snapshot.digest,
                "total_bytes": snapshot.total_bytes,
                "transaction_id": effect.transaction_id,
            },
            "installed_digest": legacy,
            "schema_version": 1,
            "source_digest": legacy,
        }
    )


def _validate_input(value: Any) -> VerifiedLibraryEffectInput:
    if (
        type(value) is not VerifiedLibraryEffectInput
        or not isinstance(value.transaction_id, str)
        or _TRANSACTION_RE.fullmatch(value.transaction_id) is None
        or not isinstance(value.plan_hash, str)
        or _HASH_RE.fullmatch(value.plan_hash) is None
        or not isinstance(value.service_id, str)
        or _SERVICE_RE.fullmatch(value.service_id) is None
        or value.action != "install"
    ):
        _deny("library-materialization-input-invalid")
    try:
        validate_library_tree_snapshot(value.payload)
    except LibraryTreeDigestError:
        _deny("library-materialization-input-invalid")
    return value


def _temp_name(effect: VerifiedLibraryEffectInput) -> str:
    binding = _canonical_json(
        {
            "planHash": effect.plan_hash,
            "serviceId": effect.service_id,
            "transactionId": effect.transaction_id,
        }
    )
    return (
        _TEMP_PREFIX + hashlib.sha256(_TEMP_DOMAIN + binding).hexdigest() + _TEMP_SUFFIX
    )


def _lock_root(root: int) -> int:
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        descriptor = os.open(_LOCK_NAME, flags, _PRIVATE_FILE_MODE, dir_fd=root)
        info = os.fstat(descriptor)
        current = os.stat(_LOCK_NAME, dir_fd=root, follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != _PRIVATE_FILE_MODE
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
        ):
            _fail("library-materialization-lock-invalid")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except LibraryInstallMaterializationError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail("library-materialization-lock-unavailable")


def _safe_temp_directory(info: os.stat_result, *, private: bool) -> None:
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o022
        or (private and stat.S_IMODE(info.st_mode) != _ROOT_MODE)
    ):
        _fail("library-materialization-temp-invalid")


def _remove_directory(
    parent: int, name: str, budget: list[int], *, private: bool = False
) -> None:
    info = _entry(parent, name)
    if info is None:
        return
    _safe_temp_directory(info, private=private)
    descriptor = _open_directory_at(parent, name)
    try:
        try:
            entries = list(os.scandir(descriptor))
        except OSError:
            _fail("library-materialization-temp-invalid")
        for entry in entries:
            budget[0] += 1
            if budget[0] > MAX_TREE_ENTRIES + 1:
                _fail("library-materialization-temp-invalid")
            try:
                child = entry.stat(follow_symlinks=False)
            except OSError:
                _fail("library-materialization-temp-invalid")
            if stat.S_ISDIR(child.st_mode):
                _remove_directory(descriptor, entry.name, budget)
            elif (
                stat.S_ISREG(child.st_mode)
                and child.st_uid == os.geteuid()
                and child.st_nlink == 1
                and not child.st_mode & 0o022
            ):
                try:
                    os.unlink(entry.name, dir_fd=descriptor)
                except OSError:
                    _fail("library-materialization-temp-invalid")
            else:
                _fail("library-materialization-temp-invalid")
        try:
            os.fsync(descriptor)
        except OSError:
            _fail("library-materialization-temp-invalid")
    finally:
        os.close(descriptor)
    try:
        os.rmdir(name, dir_fd=parent)
        os.fsync(parent)
    except OSError:
        _fail("library-materialization-temp-invalid")


def _clear_temp(root: int, name: str) -> None:
    if _TEMP_RE.fullmatch(name) is None:
        _fail("library-materialization-temp-invalid")
    _remove_directory(root, name, [0], private=True)


def _write_file(parent: int, name: str, content: bytes, mode: int) -> None:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = -1
    try:
        descriptor = os.open(name, flags, _PRIVATE_FILE_MODE, dir_fd=parent)
        _write_all(descriptor, content)
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        info = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != mode
            or info.st_size != len(content)
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
        ):
            _fail("library-materialization-write-invalid")
    except LibraryInstallMaterializationError:
        raise
    except OSError:
        _fail("library-materialization-io-error")
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _populate_temp(
    root: int,
    name: str,
    snapshot: LibraryTreeSnapshot,
    receipt: bytes,
) -> None:
    try:
        os.mkdir(name, _ROOT_MODE, dir_fd=root)
        os.fsync(root)
    except FileExistsError:
        _fail("library-materialization-temp-invalid")
    except OSError:
        _fail("library-materialization-io-error")
    temporary = _open_directory_at(root, name)
    try:
        for relative in snapshot.directories:
            parent_name, _, leaf = relative.rpartition("/")
            parent = _open_relative_directory(temporary, parent_name)
            try:
                os.mkdir(leaf, _DIRECTORY_MODE, dir_fd=parent)
                child = _open_directory_at(parent, leaf)
                try:
                    os.fchmod(child, _DIRECTORY_MODE)
                    os.fsync(child)
                finally:
                    os.close(child)
                os.fsync(parent)
            except OSError:
                _fail("library-materialization-write-invalid")
            finally:
                os.close(parent)
        for item in snapshot.files:
            parent_name, _, leaf = item.relative_path.rpartition("/")
            parent = _open_relative_directory(temporary, parent_name)
            try:
                _write_file(
                    parent,
                    leaf,
                    item.content,
                    _EXECUTABLE_MODE if item.executable else _FILE_MODE,
                )
                os.fsync(parent)
            except OSError:
                _fail("library-materialization-io-error")
            finally:
                os.close(parent)
        _write_file(temporary, RECEIPT_NAME, receipt, _PRIVATE_FILE_MODE)
        for relative in reversed(snapshot.directories):
            directory = _open_relative_directory(temporary, relative)
            try:
                os.fsync(directory)
            except OSError:
                _fail("library-materialization-io-error")
            finally:
                os.close(directory)
        os.fsync(temporary)
    except OSError:
        _fail("library-materialization-io-error")
    finally:
        os.close(temporary)


def _read_exact_receipt(root: int, service_id: str, expected: bytes) -> bool:
    try:
        target = _open_directory_at(root, service_id)
    except LibraryInstallMaterializationError:
        return False
    descriptor = -1
    try:
        descriptor = os.open(RECEIPT_NAME, _file_flags(), dir_fd=target)
        info = os.fstat(descriptor)
        current = os.stat(RECEIPT_NAME, dir_fd=target, follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != _PRIVATE_FILE_MODE
            or info.st_size != len(expected)
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
        ):
            return False
        content = bytearray()
        while len(content) <= len(expected):
            chunk = os.read(descriptor, len(expected) + 1 - len(content))
            if not chunk:
                break
            content.extend(chunk)
        return bytes(content) == expected
    except OSError:
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(target)


def _matches_target(
    root_path: Path,
    root: int,
    effect: VerifiedLibraryEffectInput,
    receipt: bytes,
) -> bool:
    info = _entry(root, effect.service_id)
    if info is None or not stat.S_ISDIR(info.st_mode):
        return False
    if not _read_exact_receipt(root, effect.service_id, receipt):
        return False
    try:
        observed = snapshot_extension_tree(root_path / effect.service_id)
    except LibraryTreeDigestError:
        return False
    return observed == effect.payload


def _result(
    effect: VerifiedLibraryEffectInput, receipt: bytes, outcome: str
) -> LibraryInstallMaterializationResult:
    return LibraryInstallMaterializationResult(
        transaction_id=effect.transaction_id,
        plan_hash=effect.plan_hash,
        service_id=effect.service_id,
        source_tree_sha256=effect.payload.digest,
        receipt_sha256="sha256:" + hashlib.sha256(receipt).hexdigest(),
        outcome=outcome,
    )


class LibraryInstallMaterializer:
    """Fixed-root, install-only publisher for captured Manifest v2 payloads."""

    def __init__(self, user_extensions_root: Path) -> None:
        self._root = user_extensions_root

    def materialize(
        self, effect_input: VerifiedLibraryEffectInput
    ) -> LibraryInstallMaterializationResult:
        effect = _validate_input(effect_input)
        rename_noreplace = _validate_platform()
        receipt = _receipt(effect)
        temporary_name = _temp_name(effect)
        root = _open_root(self._root)
        lock = -1
        try:
            lock = _lock_root(root)
            target = _entry(root, effect.service_id)
            if target is not None:
                if _matches_target(self._root, root, effect, receipt):
                    return _result(effect, receipt, "replayed")
                _fail("library-materialization-target-conflict")

            _clear_temp(root, temporary_name)
            try:
                _populate_temp(root, temporary_name, effect.payload, receipt)
            except BaseException:
                _clear_temp(root, temporary_name)
                raise

            effect_may_have_started = True
            try:
                ctypes.set_errno(0)
                result = rename_noreplace(
                    root,
                    temporary_name.encode("utf-8"),
                    root,
                    effect.service_id.encode("utf-8"),
                    _RENAME_NOREPLACE,
                )
                if result != 0:
                    error = ctypes.get_errno()
                    if error == errno.EEXIST:
                        _clear_temp(root, temporary_name)
                        if _matches_target(self._root, root, effect, receipt):
                            return _result(effect, receipt, "replayed")
                        _fail("library-materialization-target-conflict")
                    if (
                        _entry(root, effect.service_id) is None
                        and _entry(root, temporary_name) is not None
                    ):
                        effect_may_have_started = False
                        _clear_temp(root, temporary_name)
                        _fail("library-materialization-publish-failed")
                    _uncertain("library-materialization-publish-uncertain")

                try:
                    os.fsync(root)
                except OSError:
                    _uncertain("library-materialization-publish-uncertain")
                if not _matches_target(self._root, root, effect, receipt):
                    _uncertain("library-materialization-post-state-uncertain")
                return _result(effect, receipt, "published")
            except LibraryInstallMaterializationUncertain:
                raise
            except LibraryInstallMaterializationError:
                raise
            except BaseException:
                if effect_may_have_started:
                    _uncertain("library-materialization-publish-uncertain")
                raise
        finally:
            if lock >= 0:
                os.close(lock)
            os.close(root)


__all__ = [
    "LibraryInstallMaterializationError",
    "LibraryInstallMaterializationResult",
    "LibraryInstallMaterializationUncertain",
    "LibraryInstallMaterializer",
    "RECEIPT_NAME",
    "RECEIPT_SCHEMA",
]
