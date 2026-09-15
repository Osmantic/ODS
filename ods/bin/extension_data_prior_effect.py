"""Recheck actual installed Manifest v2 prior data before generic backup.

Planning evidence is immutable, but an installed definition may change between
approval and the first host effect.  This read-only Linux boundary opens the
owner-local manifest descriptor-relatively and compares its semantic digest,
schema, and data records with the approved prior binding.  It must run before
capturing a new generic snapshot; restore instead trusts the sealed snapshot,
because an interrupted update may already have replaced the installed manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from extension_data_scope_contract import BoundDataScope, BoundServiceData, DataPathRecord
from extension_document_digest import CanonicalDocumentError, canonical_document_bytes
from extension_lifecycle_work import LifecycleWorkValidationError


_MAX_MANIFEST_BYTES = 1024 * 1024
_DATA_KEYS = frozenset({"path", "backup_class", "owner", "uninstall", "purge"})
_SERVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def _reject() -> None:
    raise LifecycleWorkValidationError("lifecycle-work-prior-data-drift") from None


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def _safe_directory(info: os.stat_result) -> None:
    mode = stat.S_IMODE(info.st_mode)
    sticky_root = info.st_uid == 0 and mode & 0o1000 and mode & 0o777 == 0o777
    if (
        not stat.S_ISDIR(info.st_mode)
        or (mode & 0o022 and not sticky_root)
        or info.st_uid not in {0, os.geteuid()}
    ):
        _reject()


def _open_directory(path: Path) -> int | None:
    if not isinstance(path, Path) or not path.is_absolute() or path.parts[0] != "/":
        _reject()
    descriptor: int | None = None
    try:
        descriptor = os.open("/", _directory_flags())
        for component in path.parts[1:]:
            if component in {"", ".", ".."}:
                _reject()
            child = os.open(component, _directory_flags(), dir_fd=descriptor)
            try:
                _safe_directory(os.fstat(child))
            except (OSError, LifecycleWorkValidationError):
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor
    except LifecycleWorkValidationError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except FileNotFoundError:
        if descriptor is not None:
            os.close(descriptor)
        return None
    except (OSError, ValueError):
        if descriptor is not None:
            os.close(descriptor)
        _reject()


def _open_service(root: Path, service_id: str) -> int | None:
    if not isinstance(service_id, str) or _SERVICE_ID_RE.fullmatch(service_id) is None:
        _reject()
    descriptor = _open_directory(root)
    if descriptor is None:
        return None
    child: int | None = None
    try:
        child = os.open(service_id, _directory_flags(), dir_fd=descriptor)
        _safe_directory(os.fstat(child))
        result = child
        child = None
        return result
    except FileNotFoundError:
        return None
    except OSError:
        _reject()
    finally:
        if child is not None:
            os.close(child)
        os.close(descriptor)


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
        info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _read_manifest(directory: int) -> bytes:
    descriptor: int | None = None
    try:
        before = os.stat("manifest.yaml", dir_fd=directory, follow_symlinks=False)
        if (
            not stat.S_ISREG(before.st_mode)
            or not 0 < before.st_size <= _MAX_MANIFEST_BYTES
            or before.st_nlink != 1
            or before.st_uid not in {0, os.geteuid()}
            or stat.S_IMODE(before.st_mode) & 0o7022
        ):
            _reject()
        descriptor = os.open(
            "manifest.yaml",
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory,
        )
        opened = os.fstat(descriptor)
        if _identity(opened) != _identity(before):
            _reject()
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                _reject()
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _reject()
        after = os.stat("manifest.yaml", dir_fd=directory, follow_symlinks=False)
        if _identity(os.fstat(descriptor)) != _identity(before) or _identity(after) != _identity(before):
            _reject()
        return b"".join(chunks)
    except (OSError, ValueError):
        _reject()
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _prior_records(value: Any) -> tuple[DataPathRecord, ...]:
    if not isinstance(value, list) or len(value) > 2048:
        _reject()
    records: list[DataPathRecord] = []
    for item in value:
        if not isinstance(item, dict) or frozenset(item) != _DATA_KEYS:
            _reject()
        fields = (item["path"], item["backup_class"], item["owner"], item["uninstall"], item["purge"])
        if any(not isinstance(field, str) for field in fields):
            _reject()
        records.append(DataPathRecord(*fields))
    if tuple(item.path for item in records) != tuple(sorted({item.path for item in records})):
        _reject()
    return tuple(records)


def _check_manifest(raw: bytes, service: BoundServiceData) -> str:
    try:
        canonical = canonical_document_bytes(raw)
        document = json.loads(canonical.decode("utf-8", errors="strict"))
    except (CanonicalDocumentError, ValueError, UnicodeError, RecursionError):
        _reject()
    if not isinstance(document, dict) or document.get("schema_version") != "ods.services.v2":
        _reject()
    item = document.get("service")
    if (
        not isinstance(item, dict)
        or item.get("id") != service.service_id
        or item.get("data_schema_version") != service.prior_data_schema_version
    ):
        _reject()
    planning = item.get("planning")
    if not isinstance(planning, dict):
        _reject()
    digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
    expected = tuple(path.prior for path in service.paths if path.prior is not None)
    if digest != service.prior_definition_sha256 or _prior_records(planning.get("data")) != expected:
        _reject()
    return digest


def verify_installed_prior_data(scope: BoundDataScope, *, install_dir: Path, data_dir: Path) -> tuple[tuple[str, str], ...]:
    """Fail closed on installed-prior drift before the first generic snapshot."""
    if (
        os.name != "posix"
        or not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW"))
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
        or os.stat not in os.supports_follow_symlinks
        or type(scope) is not BoundDataScope
        or scope.operation_key != "backup"
    ):
        _reject()
    result: list[tuple[str, str]] = []
    for service in scope.services:
        if type(service) is not BoundServiceData:
            _reject()
        if service.prior_definition_sha256 is None:
            if service.action != "install":
                _reject()
            continue
        if service.action not in {"enable", "repair", "update"}:
            _reject()
        user: int | None = None
        builtin: int | None = None
        try:
            user = _open_service(data_dir / "user-extensions", service.service_id)
            builtin = _open_service(install_dir / "extensions" / "services", service.service_id)
            if (user is None) == (builtin is None):
                _reject()
            directory = user if user is not None else builtin
            assert directory is not None
            result.append((service.service_id, _check_manifest(_read_manifest(directory), service)))
        finally:
            if user is not None:
                os.close(user)
            if builtin is not None:
                os.close(builtin)
    return tuple(result)


__all__ = ["verify_installed_prior_data"]
