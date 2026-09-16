"""Dormant Compose effect for approved Manifest v2 library installs.

The effect consumes only plan-bound, already-captured library bytes.  It
materializes those bytes into the fixed user-extension root, publishes
owner-private observation evidence, starts the complete Compose graph without
building or pulling, discovers the exact container names, and publishes the
active application record last.  It deliberately does not claim health and is
not imported by the production host agent.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import signal
import stat
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows source inspection only
    fcntl = None

from assistant_first_secret_store import USE_REQUEST_SCHEMA
from extension_application_identity import (
    ApplicationIdentity,
    identity_labels,
    produce_application_identity,
)
from extension_application_record_store import ApplicationRecordStore
from extension_document_digest import CanonicalDocumentError, canonical_document_sha256
from extension_library_configuration_binding import BoundLibraryConfiguration
from extension_library_effect_input import VerifiedLibraryEffectInput
from extension_library_install_materializer import LibraryInstallMaterializer
from extension_library_tree_digest import (
    LibraryTreeDigestError,
    validate_library_tree_snapshot,
)
from extension_lifecycle_plan import (
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
    PlannedHostPort,
    PlannedOperation,
)
from extension_lifecycle_work import (
    LifecycleWorkCommand,
    LifecycleWorkExecutionError,
    LifecycleWorkUncertainEffect,
    LifecycleWorkValidationError,
)


_APPROVED_SERVICES = frozenset({"gitea", "miniflux", "ntfy", "ollama"})
_VALUE_KEYS = {
    "gitea": frozenset(
        {"GITEA_APP_NAME", "GITEA_HOST", "GITEA_PORT", "GITEA_SSH_PORT"}
    ),
    "miniflux": frozenset({"MINIFLUX_BASE_URL", "MINIFLUX_PORT"}),
    "ntfy": frozenset({"NTFY_BASE_URL", "NTFY_PORT"}),
    "ollama": frozenset({"EXT_OLLAMA_PORT", "OLLAMA_MODEL"}),
}
_PORT_KEYS = {
    "gitea": ("GITEA_PORT", "GITEA_SSH_PORT"),
    "miniflux": ("MINIFLUX_PORT",),
    "ntfy": ("NTFY_PORT",),
    "ollama": ("EXT_OLLAMA_PORT",),
}
_SECRET_KEYS = {
    "gitea": frozenset(),
    "miniflux": frozenset(
        {"MINIFLUX_ADMIN_PASSWORD", "MINIFLUX_DB_PASSWORD"}
    ),
    "ntfy": frozenset(),
    "ollama": frozenset(),
}
_APPLICATION_PARTS = (".ods-assistant-first", "applications")
_MANIFEST_NAME = "manifest.yaml"
_COMPOSE_NAME = "compose.yaml"
_CONFIG_NAME = "configuration.json"
_OVERRIDE_NAME = "compose.override.yaml"
_LOCK_NAME = ".apply.lock"
_CONFIG_SCHEMA = "ods.assistant-first.active-library-configuration.v1"
_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_MAX_CONFIG_BYTES = 128 * 1024
_MAX_OVERRIDE_BYTES = 128 * 1024
_MAX_CAPTURE_BYTES = 256 * 1024
_MAX_SERVICES = 32
_MAX_TIMEOUT_SECONDS = 900
_MAX_SAFE_INTEGER = (1 << 53) - 1
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REFERENCE_RE = re.compile(r"^secret-v1-[0-9a-f]{48}$")
_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_SERVICE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_CONTAINER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,255}$")
_DIR_FLAGS = (
    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
_Runner = Callable[[tuple[str, ...], dict[str, str], int], bool]
_Capture = Callable[[tuple[str, ...], dict[str, str], int, int], bytes | None]


class LibraryComposeApplyEffectError(LifecycleWorkExecutionError):
    """Stable, value-free failure before a conclusive application result."""


class LibraryComposeApplyUncertain(LifecycleWorkUncertainEffect):
    """Library files, containers, or the active record may have changed."""


def _deny(code: str) -> None:
    raise LifecycleWorkValidationError(code) from None


def _fail(code: str) -> None:
    raise LibraryComposeApplyEffectError(code) from None


@dataclass(frozen=True)
class LibraryComposeApplyResult:
    service_id: str
    identity_sha256: str
    definition_sha256: str
    compose_sha256: str
    config_sha256: str
    override_sha256: str
    compose_services: tuple[str, ...]
    expected_containers: tuple[str, ...]
    materialization_outcome: str
    active_files_outcome: str
    record_outcome: str


def _definition(command: LifecycleWorkCommand, service_id: str) -> PlannedDefinition:
    material = command.plan_material
    if (
        type(material) is not LifecyclePlanMaterial
        or material.schema != PLAN_MATERIAL_SCHEMA
        or material.state != "applying"
        or material.transaction_id != command.transaction_id
        or material.plan_hash != command.plan_hash
        or material.attested_approval is not True
        or type(material.operations) is not tuple
        or type(material.definitions) is not tuple
        or not material.operations
        or len(material.operations) != len(material.definitions)
    ):
        _deny("library-compose-plan-mismatch")
    seen: set[str] = set()
    matches: list[PlannedDefinition] = []
    for operation, definition in zip(
        material.operations, material.definitions, strict=True
    ):
        if (
            type(operation) is not PlannedOperation
            or type(definition) is not PlannedDefinition
            or definition.service_id != operation.service_id
            or definition.service_id in seen
        ):
            _deny("library-compose-plan-mismatch")
        seen.add(definition.service_id)
        if definition.service_id == service_id:
            if operation.action != "install":
                _deny("library-compose-plan-mismatch")
            matches.append(definition)
    if len(matches) != 1 or type(matches[0]) is not PlannedDefinition:
        _deny("library-compose-plan-mismatch")
    definition = matches[0]
    if (
        definition.service_type != "docker"
        or definition.manifest_schema_version != "ods.services.v2"
        or definition.definition_source != "library"
        or definition.compose_file != _COMPOSE_NAME
        or not isinstance(definition.definition_sha256, str)
        or _DIGEST_RE.fullmatch(definition.definition_sha256) is None
        or not isinstance(definition.compose_sha256, str)
        or _DIGEST_RE.fullmatch(definition.compose_sha256) is None
        or not definition.images
        or definition.builds != ()
    ):
        _deny("library-compose-definition-unsupported")
    return definition


def _validate_key_tuple(value: Any) -> tuple[str, ...]:
    if (
        type(value) is not tuple
        or tuple(sorted(value)) != value
        or len(value) != len(set(value))
        or any(type(key) is not str or _ENV_KEY_RE.fullmatch(key) is None for key in value)
    ):
        _deny("library-compose-configuration-mismatch")
    return value


def _snapshot_file(effect: VerifiedLibraryEffectInput, name: str) -> bytes:
    matches = [item.content for item in effect.payload.files if item.relative_path == name]
    if len(matches) != 1 or type(matches[0]) is not bytes:
        _deny("library-compose-payload-mismatch")
    return matches[0]


def _validate_inputs(
    command: Any,
    effect: Any,
    configuration: Any,
    identity: Any,
) -> tuple[bytes, bytes]:
    if type(command) is not LifecycleWorkCommand or len(command.service_ids) != 1:
        _deny("library-compose-command-invalid")
    service_id = command.service_ids[0]
    if (
        service_id not in _APPROVED_SERVICES
        or command.operation_key != f"apply:{service_id}"
        or command.payload
        != {"operation": {"serviceId": service_id, "action": "install"}}
        or type(command.timeout_seconds) is not int
        or not 1 <= command.timeout_seconds <= _MAX_TIMEOUT_SECONDS
    ):
        _deny("library-compose-command-invalid")
    try:
        expected_identity = produce_application_identity(command)
    except Exception:  # noqa: BLE001 - values from a malformed plan never escape
        _deny("library-compose-plan-mismatch")
    if type(identity) is not ApplicationIdentity or identity != expected_identity:
        _deny("library-compose-plan-mismatch")
    definition = _definition(command, service_id)
    if (
        type(effect) is not VerifiedLibraryEffectInput
        or effect.transaction_id != command.transaction_id
        or effect.plan_hash != command.plan_hash
        or effect.service_id != service_id
        or effect.action != "install"
        or type(configuration) is not BoundLibraryConfiguration
        or configuration.transaction_id != command.transaction_id
        or configuration.plan_hash != command.plan_hash
        or configuration.service_id != service_id
        or type(configuration.schema_hash) is not str
        or _HASH_RE.fullmatch(configuration.schema_hash) is None
        or type(configuration.configured) is not bool
    ):
        _deny("library-compose-binding-mismatch")
    try:
        validate_library_tree_snapshot(effect.payload)
    except LibraryTreeDigestError:
        _deny("library-compose-payload-mismatch")
    manifest = _snapshot_file(effect, _MANIFEST_NAME)
    compose = _snapshot_file(effect, _COMPOSE_NAME)
    try:
        if (
            canonical_document_sha256(manifest) != definition.definition_sha256
            or canonical_document_sha256(compose) != definition.compose_sha256
            or identity.definition_sha256 != definition.definition_sha256
            or identity.compose_sha256 != definition.compose_sha256
        ):
            _deny("library-compose-payload-mismatch")
    except CanonicalDocumentError:
        _deny("library-compose-payload-mismatch")

    if type(configuration.values) is not tuple:
        _deny("library-compose-configuration-mismatch")
    keys: list[str] = []
    for item in configuration.values:
        if type(item) is not tuple or len(item) != 2:
            _deny("library-compose-configuration-mismatch")
        key, value = item
        if (
            type(key) is not str
            or _ENV_KEY_RE.fullmatch(key) is None
            or key in {"PATH", "BIND_ADDRESS"}
            or type(value) not in {bool, int, str}
            or (type(value) is int and abs(value) > _MAX_SAFE_INTEGER)
        ):
            _deny("library-compose-configuration-mismatch")
        keys.append(key)
    if (
        keys != sorted(keys)
        or len(keys) != len(set(keys))
        or set(keys) != _VALUE_KEYS[service_id]
    ):
        _deny("library-compose-configuration-mismatch")
    # Typed configuration is collected after planning.  Never let a selected
    # Compose port escape the exact host-port claims covered by approval.
    values = dict(configuration.values)
    configured_ports = tuple(values[key] for key in _PORT_KEYS[service_id])
    planned_ports = definition.host_ports
    if (
        type(planned_ports) is not tuple
        or len(planned_ports) != len(configured_ports)
        or any(type(port) is not int or not 1 <= port <= 65535 for port in configured_ports)
        or any(
            type(claim) is not PlannedHostPort
            or claim.protocol != "tcp"
            or type(claim.port) is not int
            or not 1 <= claim.port <= 65535
            for claim in planned_ports
        )
        or len(set(configured_ports)) != len(configured_ports)
        or sorted(configured_ports) != sorted(claim.port for claim in planned_ports)
    ):
        _deny("library-compose-port-plan-mismatch")
    service_secrets = _validate_key_tuple(configuration.secret_keys)
    expected_secrets = _validate_key_tuple(configuration.expected_secret_keys)
    if (
        set(keys) & set(service_secrets)
        or not set(service_secrets) <= set(expected_secrets)
        or set(service_secrets) & {"PATH", "BIND_ADDRESS"}
        or set(service_secrets) != _SECRET_KEYS[service_id]
    ):
        _deny("library-compose-configuration-mismatch")
    reference = configuration.secret_reference
    if expected_secrets:
        if type(reference) is not str or _REFERENCE_RE.fullmatch(reference) is None:
            _deny("library-compose-configuration-mismatch")
    elif reference is not None:
        _deny("library-compose-configuration-mismatch")
    return manifest, compose


def _configuration_bytes(
    identity: ApplicationIdentity, configuration: BoundLibraryConfiguration
) -> bytes:
    target_reference = configuration.secret_reference if configuration.secret_keys else None
    payload = {
        "configured": configuration.configured,
        "identitySha256": identity.identity_sha256,
        "planHash": identity.plan_sha256,
        "schema": _CONFIG_SCHEMA,
        "schemaHash": configuration.schema_hash,
        "secretKeys": list(configuration.secret_keys),
        "secretReference": target_reference,
        "serviceId": identity.service_id,
        "transactionId": identity.transaction_id,
        "values": {key: value for key, value in configuration.values},
    }
    raw = (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    if not 0 < len(raw) <= _MAX_CONFIG_BYTES:
        _deny("library-compose-configuration-mismatch")
    return raw


def _override_bytes(identity: ApplicationIdentity, services: tuple[str, ...]) -> bytes:
    labels = identity_labels(identity)
    lines = ["services:"]
    for service in services:
        lines.extend([f"  {json.dumps(service)}:", "    labels:"])
        for key, value in sorted(labels.items()):
            lines.append(f"      {json.dumps(key)}: {json.dumps(value)}")
    lines.append("")
    result = "\n".join(lines).encode("utf-8")
    if not 0 < len(result) <= _MAX_OVERRIDE_BYTES:
        _fail("library-compose-override-invalid")
    return result


def _environment_value(value: bool | int | str) -> str:
    if type(value) is bool:
        return "true" if value else "false"
    return str(value)


def _base_environment(configuration: BoundLibraryConfiguration) -> dict[str, str]:
    environment = {"PATH": _PATH, "BIND_ADDRESS": "127.0.0.1"}
    for key, value in configuration.values:
        environment[key] = _environment_value(value)
    return environment


def _services(raw: bytes, service_id: str) -> tuple[str, ...]:
    try:
        lines = raw.decode("utf-8", "strict").splitlines()
    except UnicodeError:
        _fail("library-compose-service-discovery-invalid")
    if (
        not lines
        or len(lines) > _MAX_SERVICES
        or len(lines) != len(set(lines))
        or any(_SERVICE_KEY_RE.fullmatch(item) is None for item in lines)
        or service_id not in lines
    ):
        _fail("library-compose-service-discovery-invalid")
    return tuple(sorted(lines))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _project_name(service_id: str) -> str:
    return "ods-af-" + service_id


def _containers(
    raw: bytes, services: tuple[str, ...], project_name: str
) -> tuple[str, ...]:
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeError:
        _fail("library-compose-container-discovery-invalid")
    try:
        document = json.loads(text, object_pairs_hook=_unique_object)
    except ValueError:
        try:
            lines = text.splitlines()
            if not lines or any(not line.strip() for line in lines):
                _fail("library-compose-container-discovery-invalid")
            rows = [
                json.loads(line, object_pairs_hook=_unique_object) for line in lines
            ]
        except ValueError:
            _fail("library-compose-container-discovery-invalid")
    else:
        rows = [document] if type(document) is dict else document
    if type(rows) is not list or not rows or len(rows) > _MAX_SERVICES:
        _fail("library-compose-container-discovery-invalid")
    by_service: dict[str, str] = {}
    for row in rows:
        try:
            service = row["Service"]
            name = row["Name"]
            project = row["Project"]
        except (KeyError, TypeError):
            _fail("library-compose-container-discovery-invalid")
        if (
            type(service) is not str
            or service not in services
            or service in by_service
            or type(name) is not str
            or _CONTAINER_NAME_RE.fullmatch(name) is None
            or type(project) is not str
            or project != project_name
        ):
            _fail("library-compose-container-discovery-invalid")
        by_service[service] = name
    if set(by_service) != set(services) or len(set(by_service.values())) != len(by_service):
        _fail("library-compose-container-discovery-invalid")
    return tuple(sorted(by_service.values()))


def _platform() -> None:
    required = (os.open, os.stat, os.mkdir, os.link, os.unlink)
    if (
        os.name != "posix"
        or fcntl is None
        or not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or any(function not in os.supports_dir_fd for function in required)
        or os.stat not in os.supports_follow_symlinks
    ):
        _fail("library-compose-platform-unqualified")


def _open_install(path: Path) -> int:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or any(part in {".", ".."} for part in path.parts)
    ):
        _fail("library-compose-custody-invalid")
    descriptor = -1
    try:
        descriptor = os.open("/", _DIR_FLAGS)
        for part in path.parts[1:]:
            child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            _fail("library-compose-custody-invalid")
        return descriptor
    except LibraryComposeApplyEffectError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail("library-compose-custody-invalid")


def _open_owned_child(parent: int, name: str) -> int:
    descriptor = -1
    try:
        try:
            descriptor = os.open(name, _DIR_FLAGS, dir_fd=parent)
        except FileNotFoundError:
            try:
                os.mkdir(name, 0o700, dir_fd=parent)
                os.fsync(parent)
            except FileExistsError:
                pass
            descriptor = os.open(name, _DIR_FLAGS, dir_fd=parent)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            os.close(descriptor)
            _fail("library-compose-custody-invalid")
        return descriptor
    except LibraryComposeApplyEffectError:
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail("library-compose-custody-invalid")


def _read_exact(directory: int, name: str, expected: bytes) -> bool:
    try:
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=directory)
    except FileNotFoundError:
        return False
    except OSError:
        _fail("library-compose-file-drift")
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size != len(expected)
            or os.read(descriptor, len(expected) + 1) != expected
        ):
            _fail("library-compose-file-drift")
        return True
    except OSError:
        _fail("library-compose-file-drift")
    finally:
        os.close(descriptor)


def _assert_lock(directory: int, descriptor: int) -> None:
    try:
        held = os.fstat(descriptor)
        current = os.stat(_LOCK_NAME, dir_fd=directory, follow_symlinks=False)
    except OSError:
        _fail("library-compose-lock-drift")
    if (
        not stat.S_ISREG(held.st_mode)
        or held.st_uid != os.geteuid()
        or held.st_nlink != 1
        or held.st_size != 0
        or stat.S_IMODE(held.st_mode) != 0o600
        or (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino)
        or not stat.S_ISREG(current.st_mode)
    ):
        _fail("library-compose-lock-drift")


def _lock(directory: int) -> int:
    descriptor = -1
    try:
        try:
            descriptor = os.open(
                _LOCK_NAME,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory,
            )
            os.fsync(directory)
        except FileExistsError:
            descriptor = os.open(
                _LOCK_NAME, os.O_RDWR | os.O_NOFOLLOW, dir_fd=directory
            )
        _assert_lock(directory, descriptor)
        deadline = time.monotonic() + 10
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    _fail("library-compose-lock-busy")
                time.sleep(0.05)
        _assert_lock(directory, descriptor)
        return descriptor
    except LibraryComposeApplyEffectError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail("library-compose-lock-unavailable")


def _assert_entries(directory: int) -> None:
    try:
        entries = set(os.listdir(directory))
    except OSError:
        _fail("library-compose-custody-invalid")
    if not entries.issubset(
        {_LOCK_NAME, _MANIFEST_NAME, _COMPOSE_NAME, _CONFIG_NAME, _OVERRIDE_NAME}
    ):
        _fail("library-compose-file-drift")


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        count = os.write(descriptor, content[offset:])
        if count <= 0:
            _fail("library-compose-file-unavailable")
        offset += count


def _publish(directory: int, name: str, content: bytes) -> bool:
    if _read_exact(directory, name, content):
        return False
    temporary = ".ods-library-compose-" + secrets.token_hex(16)
    descriptor = -1
    temporary_identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        info = os.fstat(descriptor)
        temporary_identity = (info.st_dev, info.st_ino)
        _write_all(descriptor, content)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(
                temporary,
                name,
                src_dir_fd=directory,
                dst_dir_fd=directory,
                follow_symlinks=False,
            )
            created = True
        except FileExistsError:
            created = False
        os.unlink(temporary, dir_fd=directory)
        temporary_identity = None
        os.fsync(directory)
        if not _read_exact(directory, name, content):
            _fail("library-compose-file-drift")
        return created
    except LibraryComposeApplyEffectError:
        raise
    except OSError:
        _fail("library-compose-file-unavailable")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_identity is not None:
            try:
                current = os.stat(temporary, dir_fd=directory, follow_symlinks=False)
                if (
                    (current.st_dev, current.st_ino) == temporary_identity
                    and stat.S_ISREG(current.st_mode)
                ):
                    os.unlink(temporary, dir_fd=directory)
            except OSError:
                pass


def run_compose(
    argv: tuple[str, ...], environment: dict[str, str], timeout: int
) -> bool:
    """Run one non-capturing Compose command without a shell or output."""
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            argv,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
        try:
            return process.wait(timeout=timeout) == 0
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
            return False
    except (OSError, subprocess.SubprocessError):
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
        return False


def capture_compose(
    argv: tuple[str, ...],
    environment: dict[str, str],
    timeout: int,
    maximum: int,
) -> bytes | None:
    """Capture bounded, contract-selected stdout while suppressing stderr."""
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            argv,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
        try:
            stdout, _stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)
            return None
        if process.returncode != 0 or type(stdout) is not bytes or len(stdout) > maximum:
            return None
        return stdout
    except (OSError, subprocess.SubprocessError):
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
        return None


class LibraryComposeApplyEffect:
    """Apply one approved library install without granting production routing."""

    def __init__(
        self,
        install_root: Path,
        user_extensions_root: Path,
        runner: _Runner = run_compose,
        capture: _Capture = capture_compose,
    ) -> None:
        self._install_root = install_root
        self._user_root = user_extensions_root
        self._runner = runner
        self._capture = capture

    def apply(
        self,
        command: LifecycleWorkCommand,
        effect_input: VerifiedLibraryEffectInput,
        configuration: BoundLibraryConfiguration,
        identity: ApplicationIdentity,
        secret_store: Any,
    ) -> LibraryComposeApplyResult:
        manifest, compose = _validate_inputs(
            command, effect_input, configuration, identity
        )
        _platform()
        if not callable(self._runner) or not callable(self._capture):
            _deny("library-compose-consumer-invalid")
        if configuration.secret_keys and not callable(
            getattr(secret_store, "invoke_with_secrets", None)
        ):
            _deny("library-compose-consumer-invalid")

        config_metadata = _configuration_bytes(identity, configuration)
        config_sha256 = canonical_document_sha256(config_metadata)
        root = _open_install(self._install_root)
        directory = root
        lock = -1
        effect_may_have_started = False
        uncertain_code: str | None = None
        try:
            for part in (*_APPLICATION_PARTS, identity.service_id):
                child = _open_owned_child(directory, part)
                if directory != root:
                    os.close(directory)
                directory = child
            lock = _lock(directory)
            _assert_entries(directory)
            _read_exact(directory, _MANIFEST_NAME, manifest)
            _read_exact(directory, _COMPOSE_NAME, compose)
            _read_exact(directory, _CONFIG_NAME, config_metadata)

            effect_may_have_started = True
            materialized = LibraryInstallMaterializer(self._user_root).materialize(
                effect_input
            )
            created_manifest = _publish(directory, _MANIFEST_NAME, manifest)
            created_compose = _publish(directory, _COMPOSE_NAME, compose)
            created_config = _publish(directory, _CONFIG_NAME, config_metadata)

            active = self._install_root.joinpath(
                *_APPLICATION_PARTS, identity.service_id
            )
            project_directory = self._user_root / identity.service_id
            project_name = _project_name(identity.service_id)
            base_argv = (
                "docker",
                "compose",
                "--project-name",
                project_name,
                "--project-directory",
                str(project_directory),
                "-f",
                str(active / _COMPOSE_NAME),
            )
            expected_names: tuple[str, ...] | None = None
            compose_services: tuple[str, ...] | None = None
            override: bytes | None = None
            created_override = False
            environment = _base_environment(configuration)

            def execute(values: Mapping[str, Any]) -> None:
                nonlocal compose_services, created_override, expected_names, override
                for key in configuration.secret_keys:
                    value = values.get(key)
                    if type(value) is not str or not value:
                        _fail("library-compose-secret-unavailable")
                    environment[key] = value
                try:
                    discovered = self._capture(
                        (*base_argv, "config", "--services"),
                        environment,
                        command.timeout_seconds,
                        _MAX_CAPTURE_BYTES,
                    )
                    if discovered is None:
                        _fail("library-compose-service-discovery-unavailable")
                    compose_services = _services(discovered, identity.service_id)
                    override = _override_bytes(identity, compose_services)
                    created_override = _publish(directory, _OVERRIDE_NAME, override)
                    argv = (*base_argv, "-f", str(active / _OVERRIDE_NAME))
                    if (
                        self._runner(
                            (*argv, "up", "-d", "--no-build", "--pull", "never"),
                            environment,
                            command.timeout_seconds,
                        )
                        is not True
                    ):
                        _fail("library-compose-runner-failed")
                    observed = self._capture(
                        (*argv, "ps", "--all", "--format", "json"),
                        environment,
                        command.timeout_seconds,
                        _MAX_CAPTURE_BYTES,
                    )
                    if observed is None:
                        _fail("library-compose-container-discovery-unavailable")
                    expected_names = _containers(
                        observed, compose_services, project_name
                    )
                finally:
                    environment.clear()

            if configuration.secret_keys:
                request = {
                    "schema": USE_REQUEST_SCHEMA,
                    "transactionId": command.transaction_id,
                    "planHash": command.plan_hash,
                    "schemaHash": configuration.schema_hash,
                    "reference": configuration.secret_reference,
                    "expectedSecretKeys": list(configuration.expected_secret_keys),
                }
                unavailable = False
                try:
                    secret_store.invoke_with_secrets(request, execute)
                except Exception:  # noqa: BLE001 - discard secret-bearing causes
                    unavailable = True
                if unavailable:
                    environment.clear()
                    _fail("library-compose-secret-or-runner-unavailable")
            else:
                execute({})
            if compose_services is None or expected_names is None or override is None:
                _fail("library-compose-post-state-unavailable")

            records = ApplicationRecordStore(
                self._install_root.joinpath(*_APPLICATION_PARTS)
            )
            try:
                published = records.publish(
                    command,
                    config_sha256,
                    expected_names,
                    canonical_document_sha256(override),
                )
            except Exception:  # noqa: BLE001 - record details remain private
                _fail("library-compose-record-unavailable")
            _assert_lock(directory, lock)
            _assert_entries(directory)
            return LibraryComposeApplyResult(
                service_id=identity.service_id,
                identity_sha256=identity.identity_sha256,
                definition_sha256=identity.definition_sha256,
                compose_sha256=identity.compose_sha256,
                config_sha256=config_sha256,
                override_sha256=canonical_document_sha256(override),
                compose_services=compose_services,
                expected_containers=expected_names,
                materialization_outcome=materialized.outcome,
                active_files_outcome=(
                    "materialized"
                    if (
                        created_manifest
                        or created_compose
                        or created_config
                        or created_override
                    )
                    else "replayed"
                ),
                record_outcome=published.outcome,
            )
        except LibraryComposeApplyEffectError as exc:
            if not effect_may_have_started:
                raise
            uncertain_code = exc.code
        except Exception:  # noqa: BLE001 - post-effect causes never cross receipts
            if not effect_may_have_started:
                raise
            uncertain_code = "library-compose-effect-uncertain"
        finally:
            if lock >= 0:
                os.close(lock)
            if directory != root:
                os.close(directory)
            os.close(root)
        if uncertain_code is not None:
            raise LibraryComposeApplyUncertain(uncertain_code) from None
        raise LibraryComposeApplyUncertain("library-compose-effect-uncertain") from None


__all__ = [
    "LibraryComposeApplyEffect",
    "LibraryComposeApplyEffectError",
    "LibraryComposeApplyResult",
    "LibraryComposeApplyUncertain",
    "capture_compose",
    "run_compose",
]
