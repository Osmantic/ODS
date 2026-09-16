"""Read-only Docker observation for host-owned extension-data quiescence.

This is deliberately not a production restore dispatcher or a complete
quiescence proof. The host must supply a token-authenticated lease-status
callback while holding the active mutation window. It must stop relevant
services and rule out non-Docker writers and same-device bind aliases before
selecting live restore.
No Docker command here starts, stops, removes, or changes a container.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Callable

from extension_data_backup_runtime import (
    _close_quietly, _open_absolute_directory, _safe_relative_parts,
)
from extension_data_scope_contract import bind_data_scope
from extension_lifecycle_work import LifecycleWorkCommand, LifecycleWorkExecutionError
from extension_operation_leases import LEASE_SCHEMA, MAX_LEASE_SERVICES


_CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_CONTAINERS = 512
_MAX_SERVICE_BYTES = 128 * 1024
_MAX_MOUNT_BYTES = 2 * 1024 * 1024
_MAX_PATH_COMPONENTS = 256
_MAX_PATH_BYTES = 4096
_MAX_IDENTITY_PROBES = 16384
_LEASE_KEYS = frozenset({"schema", "leaseId", "transactionId", "planHash", "serviceIds"})
_STATUS_KEYS = _LEASE_KEYS | {"active"}


class DockerQuiescenceError(LifecycleWorkExecutionError):
    """Value-free refusal of a missing lease or unverifiable Docker state."""


def _fail(code: str, cause: BaseException | None = None) -> None:
    if cause is None:
        raise DockerQuiescenceError(code) from None
    raise DockerQuiescenceError(code) from cause


def _result(run: Callable[[list[str]], subprocess.CompletedProcess[bytes]],
            argv: list[str], limit: int) -> bytes:
    try:
        result = run(argv)
    except Exception as exc:
        _fail("lifecycle-work-data-quiescence-docker-unavailable", exc)
    if (
        not isinstance(result, subprocess.CompletedProcess)
        or type(result.returncode) is not int or result.returncode != 0
        or not isinstance(result.stdout, bytes) or len(result.stdout) > limit
        or not isinstance(result.stderr, bytes) or len(result.stderr) > 128 * 1024
    ):
        _fail("lifecycle-work-data-quiescence-docker-unavailable")
    return result.stdout


def _path(value: str) -> tuple[str, ...]:
    try:
        size = len(value.encode("utf-8", "strict"))
    except UnicodeError:
        _fail("lifecycle-work-data-quiescence-mount-unverifiable")
    path = Path(value)
    if (
        size > _MAX_PATH_BYTES
        or not value.startswith("/") or path.parts[0] != "/"
        or len(path.parts) > _MAX_PATH_COMPONENTS + 1
        or any(part in {"", ".", ".."} for part in path.parts[1:])
        or "\x00" in value
    ):
        _fail("lifecycle-work-data-quiescence-mount-unverifiable")
    return tuple(path.parts[1:])


def _overlap(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return left[:len(right)] == right or right[:len(left)] == left


def _inode_prefixes(
    parts: tuple[str, ...], budget: list[int], *, allow_missing: bool,
) -> tuple[tuple[int, int], ...]:
    """Walk current local identities; refuse opaque links and stat failures."""
    identities = []
    for end in range(1, len(parts) + 1):
        budget[0] -= 1
        if budget[0] < 0:
            _fail("lifecycle-work-data-quiescence-mount-unverifiable")
        path = "/" + "/".join(parts[:end])
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            if allow_missing:
                break
            _fail("lifecycle-work-data-quiescence-mount-unverifiable")
        except OSError:
            _fail("lifecycle-work-data-quiescence-mount-unverifiable")
        if (
            stat.S_ISLNK(info.st_mode)
            or (end < len(parts) and not stat.S_ISDIR(info.st_mode))
            or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)
        ):
            _fail("lifecycle-work-data-quiescence-mount-unverifiable")
        identities.append((info.st_dev, info.st_ino))
    return tuple(identities)


class DockerQuiescenceObserver:
    """Repeated, read-only fail-closed observation for admitted data work."""

    def __init__(
        self, command: LifecycleWorkCommand, install_dir: Path,
        admission: dict[str, object],
        run: Callable[[list[str]], subprocess.CompletedProcess[bytes]],
        status: Callable[[], dict[str, object]],
    ) -> None:
        if sys.platform != "linux" or not isinstance(command, LifecycleWorkCommand):
            _fail("lifecycle-work-data-quiescence-platform-unsupported")
        if command.operation_key not in {"backup", "restore"} or not callable(run) or not callable(status):
            _fail("lifecycle-work-data-quiescence-scope-invalid")
        if (
            not isinstance(admission, dict) or frozenset(admission) != _LEASE_KEYS
            or admission.get("schema") != LEASE_SCHEMA
            or admission.get("transactionId") != command.transaction_id
            or admission.get("planHash") != command.plan_hash
            or not isinstance(admission.get("leaseId"), str) or not admission["leaseId"]
            or not isinstance(admission.get("serviceIds"), list)
            or admission["serviceIds"] != list(command.service_ids)
        ):
            _fail("lifecycle-work-data-quiescence-lease-required")
        scope = bind_data_scope(command)
        if tuple(item.service_id for item in scope.services) != command.service_ids:
            _fail("lifecycle-work-data-quiescence-scope-invalid")
        if not isinstance(install_dir, Path) or not install_dir.is_absolute():
            _fail("lifecycle-work-data-quiescence-scope-invalid")
        descriptor = _open_absolute_directory(install_dir, private=False)
        _close_quietly(descriptor)
        root = _path(str(install_dir))
        targets = []
        for service in scope.services:
            for item in service.paths:
                targets.append(root + _safe_relative_parts(item.path))
        if not targets:
            _fail("lifecycle-work-data-quiescence-scope-invalid")
        self._service_ids = command.service_ids
        self._targets = tuple(targets)
        self._run = run
        self._status = status
        self._lease_id = admission["leaseId"]
        self._transaction_id = command.transaction_id
        self._plan_hash = command.plan_hash

    def _require_active_lease(self) -> bool:
        """Require fresh host status from the caller's authorized use window."""
        try:
            record = self._status()
        except Exception as exc:
            _fail("lifecycle-work-data-quiescence-lease-not-active", exc)
        if not isinstance(record, dict) or frozenset(record) != _STATUS_KEYS:
            _fail("lifecycle-work-data-quiescence-lease-not-active")
        ids = record.get("serviceIds")
        if (
            record.get("schema") != LEASE_SCHEMA
            or record.get("leaseId") != self._lease_id
            or record.get("transactionId") != self._transaction_id
            or record.get("planHash") != self._plan_hash
            or record.get("active") is not True
            or not isinstance(ids, list)
            or not ids or len(ids) > MAX_LEASE_SERVICES
            or not all(isinstance(item, str) for item in ids)
            or ids != sorted(set(ids))
            or not set(self._service_ids).issubset(ids)
        ):
            _fail("lifecycle-work-data-quiescence-lease-not-active")
        return True

    def __call__(self) -> bool:
        """False means known active writer; malformed or failed evidence raises."""
        self._require_active_lease()
        for service_id in self._service_ids:
            raw = _result(self._run, [
                "container", "ls", "--all",
                "--filter", f"label=com.docker.compose.service={service_id}",
                "--format", "{{.State}}",
            ], _MAX_SERVICE_BYTES)
            try:
                states = [line.decode("ascii", "strict") for line in raw.splitlines()]
            except UnicodeError as exc:
                _fail("lifecycle-work-data-quiescence-docker-unverifiable", exc)
            if len(states) > _MAX_CONTAINERS or any(
                state not in {"exited", "created", "running", "paused", "restarting", "removing", "dead"}
                for state in states
            ):
                _fail("lifecycle-work-data-quiescence-docker-unverifiable")
            if any(state != "exited" for state in states):
                return False

        raw_ids = _result(self._run, ["container", "ls", "--no-trunc", "--format", "{{.ID}}"],
                          _MAX_SERVICE_BYTES)
        try:
            ids = [line.decode("ascii", "strict") for line in raw_ids.splitlines()]
        except UnicodeError as exc:
            _fail("lifecycle-work-data-quiescence-docker-unverifiable", exc)
        if len(ids) > _MAX_CONTAINERS or len(ids) != len(set(ids)) or any(
            _CONTAINER_ID_RE.fullmatch(item) is None for item in ids
        ):
            _fail("lifecycle-work-data-quiescence-docker-unverifiable")
        budget = [_MAX_IDENTITY_PROBES]
        target_prefixes = set()
        target_finals = set()
        for target in self._targets:
            prefixes = _inode_prefixes(target, budget, allow_missing=True)
            target_prefixes.update(prefixes)
            if len(prefixes) == len(target):
                target_finals.add(prefixes[-1])
        if not ids:
            return self._require_active_lease()
        raw = _result(self._run, ["inspect", "--type", "container", "--format",
                                  "{{json .Mounts}}", *ids], _MAX_MOUNT_BYTES)
        lines = raw.splitlines()
        if len(lines) != len(ids):
            _fail("lifecycle-work-data-quiescence-mount-unverifiable")
        for line in lines:
            try:
                mounts = json.loads(line.decode("utf-8", "strict"))
            except (UnicodeError, ValueError, RecursionError) as exc:
                _fail("lifecycle-work-data-quiescence-mount-unverifiable", exc)
            if not isinstance(mounts, list) or len(mounts) > 4096:
                _fail("lifecycle-work-data-quiescence-mount-unverifiable")
            for mount in mounts:
                if (
                    not isinstance(mount, dict) or mount.get("Type") not in {"bind", "volume", "tmpfs"}
                    or not isinstance(mount.get("Source"), str)
                ):
                    _fail("lifecycle-work-data-quiescence-mount-unverifiable")
                if mount["Type"] == "tmpfs":
                    continue
                source = _path(mount["Source"])
                if any(_overlap(source, target) for target in self._targets):
                    return False
                # Docker's Source string can be a symlink, hardlink, or a
                # different bind path to the same local inode. A lexical
                # non-overlap is not enough to authorize a data swap.
                source_prefixes = _inode_prefixes(
                    source, budget, allow_missing=False
                )
                if (
                    source_prefixes[-1] in target_prefixes
                    or any(item in source_prefixes for item in target_finals)
                ):
                    return False
        return self._require_active_lease()


__all__ = ["DockerQuiescenceError", "DockerQuiescenceObserver"]
