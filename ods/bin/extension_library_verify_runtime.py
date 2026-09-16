"""Read-only current-state verification for approved library applications.

This route is deliberately separate from apply observation: an APPLIED receipt
can coexist with a stopped or unhealthy service. Verification double-samples
the owner-custodied record, active files, and Docker state around a bounded
host-facing HTTP request. It never changes a container or a receipt.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from extension_application_identity import (
    ApplicationIdentity,
    ApplicationIdentityError,
    parse_observed_labels,
)
from extension_application_observation_adapter import (
    _current_containers,
    _current_files,
    _current_override,
    _ids,
    _output,
    _run_docker,
    _unique_keys,
)
from extension_application_record_store import ApplicationRecord, ApplicationRecordStore
from extension_application_observation import ContainerObservation
from extension_document_digest import canonical_document_sha256
from extension_library_compose_apply_effect import _override_bytes, _project_name
from extension_library_verify_binding import LibraryVerifySelection, bind_library_verify
from extension_lifecycle_work import (
    REQUEST_SCHEMA,
    LifecycleWorkCommand,
    LifecycleWorkExecutionError,
)
from extension_lifecycle_plan import PlannedHostPort

_SCHEMA = "ods.extension-library-current-verification.v1"
_APPLICATION_PARTS = (".ods-assistant-first", "applications")
_PILOT_HTTP = {
    "gitea": (3000, "/api/healthz"),
    "miniflux": (8080, "/healthcheck"),
    "ntfy": (8080, "/v1/health"),
    "ollama": (11434, "/api/tags"),
}
_PILOT_COMPOSE_SERVICES = {
    "gitea": ("gitea",),
    "miniflux": ("miniflux", "miniflux-db"),
    "ntfy": ("ntfy",),
    "ollama": ("ollama",),
}
_MAX_HTTP_BYTES = 64 * 1024
_MAX_INSPECT_BYTES = 2 * 1024 * 1024
_POLL_SECONDS = 2.0
_READY_TIMEOUT_SECONDS = 120.0
_DOCKER_ID = re.compile(r"^[0-9a-f]{64}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_PORT_SPEC = re.compile(r"^([0-9]{1,5})/tcp$")


class LibraryVerifyError(LifecycleWorkExecutionError):
    """Value-free failure; current health has not been established."""


def _fail(code: str) -> None:
    raise LibraryVerifyError(code) from None


def _identity(record: ApplicationRecord) -> ApplicationIdentity:
    return ApplicationIdentity(
        service_id=record.service_id,
        version=record.version,
        action=record.action,
        transaction_id=record.transaction_id,
        plan_sha256=record.plan_sha256,
        request_sha256=record.request_sha256,
        definition_sha256=record.definition_sha256,
        compose_sha256=record.compose_sha256,
        identity_sha256=record.identity_sha256,
    )


def _apply_request_hash(
    command: LifecycleWorkCommand, selected: LibraryVerifySelection
) -> str:
    request = {
        "schema": REQUEST_SCHEMA,
        "transactionId": command.transaction_id,
        "planHash": command.plan_hash,
        "operationKey": f"apply:{selected.service_id}",
        "serviceIds": [selected.service_id],
        "payload": {
            "operation": {"serviceId": selected.service_id, "action": selected.action}
        },
    }
    return hashlib.sha256(
        json.dumps(
            request, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def _inspect_current_container(
    identity: ApplicationIdentity,
    observed: ContainerObservation,
    docker_runner: Callable[[list[str]], subprocess.CompletedProcess[bytes]],
) -> tuple[dict[int, int], str, str]:
    raw = _output(
        docker_runner, ["docker", "inspect", "--format", "{{json .}}", observed.name]
    )
    if len(raw) > _MAX_INSPECT_BYTES:
        _fail("library-verify-docker-invalid")
    try:
        document = json.loads(raw, object_pairs_hook=_unique_keys)
        ports = document["NetworkSettings"]["Ports"]
        name = document["Name"]
        container_id = document["Id"]
        image_id = document["Image"]
        labels = document["Config"]["Labels"]
        state = document["State"]["Status"]
        health = document["State"].get("Health")
        health_name = "no_healthcheck" if health is None else health["Status"]
        if (
            name != "/" + observed.name
            or type(container_id) is not str
            or _DOCKER_ID.fullmatch(container_id) is None
            or type(image_id) is not str
            or _IMAGE_ID.fullmatch(image_id) is None
            or type(labels) is not dict
            or labels != observed.labels
            or state != observed.state
            or health_name != observed.health
            or type(ports) is not dict
        ):
            raise ValueError
        if parse_observed_labels(labels) != identity:
            raise ValueError
        published: dict[int, int] = {}
        for spec, bindings in ports.items():
            if bindings is None or bindings == []:
                continue
            if type(spec) is not str or (match := _PORT_SPEC.fullmatch(spec)) is None:
                raise ValueError
            internal_port = int(match.group(1))
            if (
                not 1 <= internal_port <= 65535
                or type(bindings) is not list
                or len(bindings) != 1
                or type(bindings[0]) is not dict
                or set(bindings[0]) != {"HostIp", "HostPort"}
                or bindings[0]["HostIp"] != "127.0.0.1"
            ):
                raise ValueError
            port_text = bindings[0]["HostPort"]
            if (
                type(port_text) is not str
                or not port_text.isascii()
                or not port_text.isdecimal()
                or not 1 <= int(port_text) <= 65535
            ):
                raise ValueError
            published[internal_port] = int(port_text)
        if len(set(published.values())) != len(published):
            raise ValueError
        return published, container_id, image_id
    except (ApplicationIdentityError, KeyError, TypeError, ValueError, UnicodeError):
        _fail("library-verify-port-unbound")


def _project_ids(
    service_id: str,
    docker_runner: Callable[[list[str]], subprocess.CompletedProcess[bytes]],
) -> set[str]:
    return _ids(
        _output(
            docker_runner,
            [
                "docker",
                "ps",
                "-aq",
                "--no-trunc",
                "--filter",
                f"label=com.docker.compose.project={_project_name(service_id)}",
            ],
        )
    )


def _declared_probe(selected: LibraryVerifySelection) -> tuple[int, str]:
    internal_port, path = _PILOT_HTTP[selected.service_id]
    try:
        document = json.loads(
            selected.definition.canonical_document, object_pairs_hook=_unique_keys
        )
        lifecycle = document["lifecycle"]
        resources = document["resources"]
        if (
            type(lifecycle) is not dict
            or lifecycle.get("healthChecks") != [path]
            or lifecycle.get("readiness") != ["healthy"]
            or type(resources) is not dict
            or internal_port not in resources.get("containerPorts", [])
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, UnicodeError):
        _fail("library-verify-probe-contract-mismatch")
    return internal_port, path


def _probe_loopback(port: int, path: str) -> bool:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(
            "GET", path, headers={"Host": "127.0.0.1", "Connection": "close"}
        )
        response = connection.getresponse()
        if response.status != 200:
            return False
        return len(response.read(_MAX_HTTP_BYTES + 1)) <= _MAX_HTTP_BYTES
    except (OSError, TimeoutError, http.client.HTTPException):
        return False
    finally:
        connection.close()


@dataclass(frozen=True)
class _CurrentService:
    service_id: str
    action: str
    record_sha256: str
    definition_sha256: str
    compose_sha256: str
    config_sha256: str
    override_sha256: str
    containers: tuple[tuple[str, str, str, str, str], ...]
    host_port: int
    path: str
    ready: bool

    def evidence(self) -> dict[str, Any]:
        return {
            "serviceId": self.service_id,
            "action": self.action,
            "recordSha256": self.record_sha256,
            "definitionSha256": self.definition_sha256,
            "composeSha256": self.compose_sha256,
            "configSha256": self.config_sha256,
            "overrideSha256": self.override_sha256,
            "containers": [
                {
                    "name": name,
                    "containerId": container_id,
                    "imageId": image_id,
                    "state": state,
                    "health": health,
                }
                for name, container_id, image_id, state, health in self.containers
            ],
            "hostPort": self.host_port,
            "hostProbePath": self.path,
            "hostProbeResult": "ok",
        }


class LibraryVerifyDispatcher:
    """Verify all selected pilot apps, including noops, under one active lease."""

    def __init__(
        self,
        *,
        install_dir: Path,
        record_store: ApplicationRecordStore,
        active_lease: Callable[[], bool],
        docker_runner: Callable[
            [list[str]], subprocess.CompletedProcess[bytes]
        ] = _run_docker,
        probe: Callable[[int, str], bool] = _probe_loopback,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if (
            sys.platform != "linux"
            or not isinstance(install_dir, Path)
            or not callable(getattr(record_store, "snapshot", None))
            or not callable(active_lease)
            or not callable(docker_runner)
            or not callable(probe)
            or not callable(clock)
            or not callable(sleep)
        ):
            _fail("library-verify-platform-unqualified")
        self._install = install_dir
        self._records = record_store
        self._lease = active_lease
        self._docker = docker_runner
        self._probe = probe
        self._clock = clock
        self._sleep = sleep

    def _sample(
        self, command: LifecycleWorkCommand, selected: LibraryVerifySelection
    ) -> _CurrentService:
        if self._lease() is not True:
            _fail("library-verify-lease-invalid")
        record = self._records.snapshot(selected.service_id)
        definition = selected.definition
        if (
            type(record) is not ApplicationRecord
            or record.service_id != selected.service_id
            or record.version != definition.version
            or record.definition_sha256 != definition.definition_sha256
            or record.compose_sha256 != definition.compose_sha256
        ):
            _fail("library-verify-record-mismatch")
        if selected.action != "noop" and (
            record.transaction_id != command.transaction_id
            or record.plan_sha256 != command.plan_hash
            or record.action != selected.action
            or record.request_sha256 != _apply_request_hash(command, selected)
        ):
            _fail("library-verify-record-mismatch")
        files = _current_files(self._install, selected.service_id)
        if files != (
            definition.definition_sha256,
            definition.compose_sha256,
            record.config_sha256,
        ):
            _fail("library-verify-active-files-mismatch")
        containers = _current_containers(
            selected.service_id, record.expected_containers, self._docker
        )
        if (
            not record.expected_containers
            or len(containers) != len(record.expected_containers)
            or {item.name for item in containers} != set(record.expected_containers)
        ):
            _fail("library-verify-container-set-mismatch")
        identity = _identity(record)
        internal_port, path = _declared_probe(selected)
        planned_ports = definition.host_ports
        if (
            type(planned_ports) is not tuple
            or not planned_ports
            or any(
                type(claim) is not PlannedHostPort
                or claim.protocol != "tcp"
                or type(claim.port) is not int
                or not 1 <= claim.port <= 65535
                for claim in planned_ports
            )
            or len({claim.port for claim in planned_ports}) != len(planned_ports)
        ):
            _fail("library-verify-port-unbound")
        main = []
        states = []
        port = None
        for item in containers:
            try:
                observed = parse_observed_labels(item.labels)
            except ApplicationIdentityError:
                _fail("library-verify-container-identity-mismatch")
            if observed != identity:
                _fail("library-verify-container-identity-mismatch")
            published, container_id, image_id = _inspect_current_container(
                identity, item, self._docker
            )
            if item.labels.get("com.docker.compose.service") == selected.service_id:
                main.append(item.name)
                if sorted(published.values()) != sorted(
                    claim.port for claim in planned_ports
                ):
                    _fail("library-verify-port-unbound")
                port = published.get(internal_port)
                if port is None:
                    _fail("library-verify-port-unbound")
            elif published:
                _fail("library-verify-port-unbound")
            states.append((item.name, container_id, image_id, item.state, item.health))
        if len(main) != 1:
            _fail("library-verify-main-container-mismatch")
        if _project_ids(selected.service_id, self._docker) != {
            container_id for _, container_id, _, _, _ in states
        }:
            _fail("library-verify-container-set-mismatch")
        observed_services = [
            item.labels.get("com.docker.compose.service") for item in containers
        ]
        if any(type(name) is not str for name in observed_services):
            _fail("library-verify-container-set-mismatch")
        compose_services = tuple(sorted(set(observed_services)))
        if compose_services != _PILOT_COMPOSE_SERVICES[selected.service_id]:
            _fail("library-verify-container-set-mismatch")
        expected_override = canonical_document_sha256(
            _override_bytes(identity, compose_services)
        )
        override = _current_override(self._install, selected.service_id)
        if override != expected_override:
            _fail("library-verify-override-mismatch")
        if port is None:
            _fail("library-verify-port-unbound")
        return _CurrentService(
            service_id=selected.service_id,
            action=selected.action,
            record_sha256=record.record_sha256,
            definition_sha256=files[0],
            compose_sha256=files[1],
            config_sha256=files[2],
            override_sha256=override,
            containers=tuple(sorted(states)),
            host_port=port,
            path=path,
            ready=all(
                state == "running" and health == "healthy"
                for _, _, _, state, health in states
            ),
        )

    def __call__(self, command: LifecycleWorkCommand) -> str:
        selected = bind_library_verify(command)
        try:
            return self._verify(command, selected)
        except LibraryVerifyError:
            raise
        except Exception:  # noqa: BLE001 - never expose host evidence details
            _fail("library-verify-unavailable")

    def _verify(
        self,
        command: LifecycleWorkCommand,
        selected: tuple[LibraryVerifySelection, ...],
    ) -> str:
        deadline = self._clock() + min(command.timeout_seconds, _READY_TIMEOUT_SECONDS)
        while True:
            first = tuple(self._sample(command, item) for item in selected)
            if all(item.ready for item in first) and all(
                self._probe(item.host_port, item.path) is True for item in first
            ):
                second = tuple(self._sample(command, item) for item in selected)
                if second != first or self._lease() is not True:
                    _fail("library-verify-current-drift")
                if self._clock() > deadline:
                    _fail("library-verify-readiness-failed")
                payload = {
                    "schema": _SCHEMA,
                    "transactionId": command.transaction_id,
                    "planHash": command.plan_hash,
                    "requestHash": command.request_hash,
                    "services": [item.evidence() for item in second],
                }
                return hashlib.sha256(
                    json.dumps(
                        payload,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ).encode("utf-8")
                ).hexdigest()
            if self._clock() >= deadline:
                _fail("library-verify-readiness-failed")
            self._sleep(min(_POLL_SECONDS, max(0.0, deadline - self._clock())))


def build_library_verify_runtime(
    install_dir: Path, active_lease: Callable[[], bool]
) -> LibraryVerifyDispatcher:
    """Build without observing or changing an installed application."""

    records = ApplicationRecordStore(install_dir.joinpath(*_APPLICATION_PARTS))
    return LibraryVerifyDispatcher(
        install_dir=install_dir, record_store=records, active_lease=active_lease
    )


__all__ = [
    "LibraryVerifyDispatcher",
    "LibraryVerifyError",
    "build_library_verify_runtime",
]
