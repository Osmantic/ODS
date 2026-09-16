"""Host-side, read-only evidence collector for approved application observation.

This adapter does not apply, verify health, or terminalize work.  It collects
current files, the active record, Docker containers, and the exact receipt for
one plan-bound apply operation, then delegates classification to the pure
application observation contract.  The caller must hold an admitted host lease.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from extension_application_identity import (
    LABEL_NAMESPACE,
    produce_application_observation_identity,
)
from extension_application_observation import (
    MAX_CONTAINERS,
    ApplicationObservationError,
    ContainerObservation,
    CurrentEvidence,
    ObservationResult,
    observe_application,
)
from extension_application_record_store import ApplicationRecord, ApplicationRecordStore
from extension_document_digest import canonical_document_sha256
from extension_lifecycle_work import LifecycleWorkCommand

_DIRECTORY_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
_CONTAINER_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,255}$")
_MAX_DOCKER_BYTES = 2 * 1024 * 1024
_MAX_FILE_BYTES = 1024 * 1024
_MAX_HOST_CONTAINERS = 512


class ApplicationEvidenceError(ApplicationObservationError):
    """Value-free failure to obtain trustworthy current host evidence."""


def _fail(code: str) -> None:
    raise ApplicationEvidenceError(code) from None


def _run_docker(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        argv,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=15,
        check=False,
    )


def _output(
    runner: Callable[[list[str]], subprocess.CompletedProcess[bytes]],
    argv: list[str],
) -> bytes:
    try:
        result = runner(argv)
    except Exception:
        _fail("application-evidence-docker-unavailable")
    if (
        not isinstance(result, subprocess.CompletedProcess)
        or type(result.returncode) is not int
        or result.returncode != 0
        or type(result.stdout) is not bytes
        or len(result.stdout) > _MAX_DOCKER_BYTES
        or type(result.stderr) is not bytes
        or len(result.stderr) > 128 * 1024
    ):
        _fail("application-evidence-docker-unavailable")
    return result.stdout


def _owned_directory(descriptor: int) -> None:
    info = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o022
    ):
        _fail("application-evidence-file-custody")


def _open_child(parent: int, name: str, *, optional: bool = False) -> int | None:
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
    except FileNotFoundError:
        if optional:
            return None
        _fail("application-evidence-root-unavailable")
    except OSError:
        _fail("application-evidence-file-custody")
    try:
        _owned_directory(descriptor)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _file_digest(parent: int, name: str) -> str | None:
    try:
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent)
    except FileNotFoundError:
        return None
    except OSError:
        _fail("application-evidence-file-custody")
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or before.st_mode & 0o022
            or not 0 < before.st_size <= _MAX_FILE_BYTES
        ):
            _fail("application-evidence-file-custody")
        content = bytearray()
        while len(content) < before.st_size:
            part = os.read(descriptor, before.st_size - len(content))
            if not part:
                _fail("application-evidence-file-drift")
            content.extend(part)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            _fail("application-evidence-file-drift")
        try:
            return canonical_document_sha256(bytes(content))
        except Exception:
            _fail("application-evidence-document-invalid")
    except OSError:
        _fail("application-evidence-file-custody")
    finally:
        os.close(descriptor)


def _open_absolute_install(path: Path) -> int:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or any(part in {".", ".."} for part in path.parts)
    ):
        _fail("application-evidence-root-invalid")
    try:
        descriptor = os.open("/", _DIRECTORY_FLAGS)
    except OSError:
        _fail("application-evidence-root-unavailable")
    try:
        for component in path.parts[1:]:
            child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        _owned_directory(descriptor)
        return descriptor
    except ApplicationEvidenceError:
        os.close(descriptor)
        raise
    except OSError:
        os.close(descriptor)
        _fail("application-evidence-root-unavailable")


def _current_files(
    install_root: Path, service_id: str
) -> tuple[str | None, str | None, str | None]:
    root = _open_absolute_install(install_root)
    try:
        parent = root
        opened: list[int] = []
        try:
            for name in (".ods-assistant-first", "applications"):
                child = _open_child(parent, name)
                assert child is not None
                opened.append(child)
                parent = child
            service = _open_child(parent, service_id, optional=True)
            if service is None:
                return None, None, None
            opened.append(service)
            return (
                _file_digest(service, "manifest.yaml"),
                _file_digest(service, "compose.yaml"),
                _file_digest(service, "configuration.json"),
            )
        finally:
            for descriptor in reversed(opened):
                os.close(descriptor)
    finally:
        os.close(root)


def _current_override(install_root: Path, service_id: str) -> str | None:
    """Hash the active generated Compose override under the same custody rules."""

    root = _open_absolute_install(install_root)
    try:
        parent = root
        opened: list[int] = []
        try:
            for name in (".ods-assistant-first", "applications"):
                child = _open_child(parent, name)
                assert child is not None
                opened.append(child)
                parent = child
            service = _open_child(parent, service_id, optional=True)
            if service is None:
                return None
            opened.append(service)
            return _file_digest(service, "compose.override.yaml")
        finally:
            for descriptor in reversed(opened):
                os.close(descriptor)
    finally:
        os.close(root)


def _ids(raw: bytes) -> set[str]:
    try:
        lines = raw.decode("utf-8", "strict").splitlines()
    except UnicodeError:
        _fail("application-evidence-docker-invalid")
    if len(lines) > _MAX_HOST_CONTAINERS:
        _fail("application-evidence-docker-limit")
    ids = set(lines)
    if any(_CONTAINER_ID.fullmatch(value) is None for value in ids) or len(ids) != len(
        lines
    ):
        _fail("application-evidence-docker-invalid")
    return ids


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate Docker field")
        result[key] = value
    return result


def _current_containers(
    service_id: str,
    expected_names: tuple[str, ...],
    runner: Callable[[list[str]], subprocess.CompletedProcess[bytes]],
) -> tuple[ContainerObservation, ...]:
    # Observe every container name, including stopped containers, so an
    # expected container with missing identity labels is still a partial effect.
    raw = _output(
        runner, ["docker", "ps", "-a", "--no-trunc", "--format", "{{.ID}} {{.Names}}"]
    )
    try:
        lines = raw.decode("utf-8", "strict").splitlines()
    except UnicodeError:
        _fail("application-evidence-docker-invalid")
    if len(lines) > _MAX_HOST_CONTAINERS:
        _fail("application-evidence-docker-limit")
    names: dict[str, str] = {}
    for line in lines:
        parts = line.split(" ", 1)
        if (
            len(parts) != 2
            or _CONTAINER_ID.fullmatch(parts[0]) is None
            or _CONTAINER_NAME.fullmatch(parts[1]) is None
            or parts[0] in names
        ):
            _fail("application-evidence-docker-invalid")
        names[parts[0]] = parts[1]
    by_identity = _ids(
        _output(
            runner,
            [
                "docker",
                "ps",
                "-aq",
                "--no-trunc",
                "--filter",
                f"label={LABEL_NAMESPACE}.service_id={service_id}",
            ],
        )
    )
    by_compose = _ids(
        _output(
            runner,
            [
                "docker",
                "ps",
                "-aq",
                "--no-trunc",
                "--filter",
                f"label=com.docker.compose.service={service_id}",
            ],
        )
    )
    # A Compose application may contain dependency services whose Compose
    # service label is not the requested extension ID.  Keep the whole fixed
    # project in scope even after its active record has been removed during
    # compensation, so an orphan dependency cannot masquerade as ABSENT.
    by_project = _ids(
        _output(
            runner,
            [
                "docker",
                "ps",
                "-aq",
                "--no-trunc",
                "--filter",
                f"label=com.docker.compose.project=ods-af-{service_id}",
            ],
        )
    )
    if not (by_identity | by_compose | by_project) <= names.keys():
        _fail("application-evidence-docker-drift")
    chosen = (
        by_identity
        | by_compose
        | by_project
        | {
            container_id
            for container_id, name in names.items()
            if name in expected_names or name == service_id
        }
    )
    if len(chosen) > MAX_CONTAINERS:
        _fail("application-evidence-docker-limit")
    observations: list[ContainerObservation] = []
    selected = sorted(chosen)
    if selected:
        raw_inspect = _output(
            runner, ["docker", "inspect", "--format", "{{json .}}", *selected]
        )
        try:
            inspected = raw_inspect.decode("utf-8", "strict").splitlines()
        except UnicodeError:
            _fail("application-evidence-docker-invalid")
        if len(inspected) != len(selected):
            _fail("application-evidence-docker-drift")
    else:
        inspected = []
    for container_id, line in zip(selected, inspected):
        try:
            document: Any = json.loads(line, object_pairs_hook=_unique_keys)
            status = document["State"]["Status"]
            health = document["State"].get("Health")
            health_name = "no_healthcheck" if health is None else health["Status"]
            labels = document["Config"]["Labels"]
            name = document["Name"]
            if (
                document["Id"] != container_id
                or name != "/" + names[container_id]
                or not isinstance(labels, dict)
                or any(
                    type(key) is not str or type(value) is not str
                    for key, value in labels.items()
                )
                or type(status) is not str
                or type(health_name) is not str
            ):
                raise ValueError
        except (KeyError, TypeError, ValueError, UnicodeError):
            _fail("application-evidence-docker-invalid")
        observations.append(
            ContainerObservation(
                name=names[container_id],
                state=status,
                health=health_name,
                labels=labels,
            )
        )
    return tuple(sorted(observations, key=lambda item: item.name))


class ApplicationObservationAdapter:
    """Collect twice under an admitted lease and refuse changed evidence."""

    def __init__(
        self,
        install_root: Path,
        record_store: ApplicationRecordStore,
        receipt_store: Any,
        plan_loader: Callable[[LifecycleWorkCommand], LifecycleWorkCommand],
        active_lease: Callable[[], bool],
        docker_runner: Callable[
            [list[str]], subprocess.CompletedProcess[bytes]
        ] = _run_docker,
    ) -> None:
        if sys.platform != "linux" or not all(
            (
                callable(getattr(record_store, "snapshot", None)),
                callable(getattr(receipt_store, "snapshot", None)),
                callable(plan_loader),
                callable(active_lease),
                callable(docker_runner),
            )
        ):
            _fail("application-evidence-platform-unqualified")
        self._root = install_root
        self._records = record_store
        self._receipts = receipt_store
        self._loader = plan_loader
        self._lease = active_lease
        self._docker = docker_runner

    def __call__(self, command: LifecycleWorkCommand) -> ObservationResult:
        if (
            type(command) is not LifecycleWorkCommand
            or not command.operation_key.startswith("apply:")
            or command.service_ids != (command.operation_key.removeprefix("apply:"),)
        ):
            _fail("application-evidence-command-invalid")
        try:
            if self._lease() is not True:
                _fail("application-evidence-lease-invalid")
            bound = self._loader(command)
            if (
                type(bound) is not LifecycleWorkCommand
                or bound.plan_material is None
                or any(
                    getattr(bound, field) != getattr(command, field)
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
                _fail("application-evidence-plan-unavailable")
            identity = produce_application_observation_identity(bound)
            record = self._records.snapshot(identity.service_id)
            if record is not None and type(record) is not ApplicationRecord:
                _fail("application-evidence-record-invalid")
            snapshot = self._receipts.snapshot(
                command.transaction_id, command.operation_key
            )
            compensation_key = f"compensate:{identity.service_id}"
            compensation = (
                self._receipts.snapshot(command.transaction_id, compensation_key)
                if bound.plan_material.state == "reconciling"
                else None
            )
            expected_names = () if record is None else record.expected_containers
            first_files = _current_files(self._root, identity.service_id)
            first_override = _current_override(self._root, identity.service_id)
            first_containers = _current_containers(
                identity.service_id, expected_names, self._docker
            )
            second_files = _current_files(self._root, identity.service_id)
            second_override = _current_override(self._root, identity.service_id)
            second_containers = _current_containers(
                identity.service_id, expected_names, self._docker
            )
            if (
                first_files != second_files
                or first_override != second_override
                or first_containers != second_containers
                or self._records.snapshot(identity.service_id) != record
                or self._receipts.snapshot(
                    command.transaction_id, command.operation_key
                )
                != snapshot
                or (
                    bound.plan_material.state == "reconciling"
                    and self._receipts.snapshot(
                        command.transaction_id, compensation_key
                    )
                    != compensation
                )
                or self._lease() is not True
            ):
                _fail("application-evidence-current-drift")
            active_record = None
            if record is not None:
                active_record = asdict(record)
                active_record["expected_containers"] = list(record.expected_containers)
            return observe_application(
                bound,
                CurrentEvidence(
                    active_record=active_record,
                    active_definition_digest=first_files[0],
                    active_compose_digest=first_files[1],
                    active_config_digest=first_files[2],
                    container_observations=first_containers,
                    receipt_snapshot=snapshot,
                    topology="docker",
                    docker_available=True,
                    compensation_snapshot=compensation,
                    active_override_digest=first_override,
                ),
            )
        except ApplicationObservationError:
            raise
        except Exception:
            _fail("application-evidence-unavailable")


__all__ = ["ApplicationEvidenceError", "ApplicationObservationAdapter"]
