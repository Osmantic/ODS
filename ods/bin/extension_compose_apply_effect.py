"""Dormant, exact-canary Compose effect for Assistant First.

This is an effect substrate, not an installed-state observer or lifecycle
dispatcher. It is deliberately not imported by the production host agent.
The caller must separately prove the current plan/configuration/reservation,
observe the effect, publish an active record last, and recover partial effects.
"""

from __future__ import annotations

import hashlib
import json
import os
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
from extension_artifact_stage_store import (
    StagedArtifactBatch,
    StagedArtifactFile,
    StagedDefinitionArtifacts,
)
from extension_configuration_effect_runtime import (
    CANARY_CONFIGURATION,
    CONFIGURATION_SCHEMA,
    BoundConfiguration,
)
from extension_document_digest import CanonicalDocumentError, canonical_document_sha256
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
from extension_lifecycle_work import (
    LifecycleWorkCommand,
    LifecycleWorkExecutionError,
    LifecycleWorkUncertainEffect,
    LifecycleWorkValidationError,
)

_CANARY_SCHEMA_HASH = hashlib.sha256(
    (
        json.dumps(
            {"schema": CONFIGURATION_SCHEMA, "fields": CANARY_CONFIGURATION},
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
).hexdigest()
_ROOT_PARTS = (".ods-assistant-first", "applications", CANARY_SERVICE_ID)
_MANIFEST_NAME = "manifest.yaml"
_CONFIG_NAME = "configuration.json"
_COMPOSE_NAME = "compose.yaml"
_OVERRIDE_NAME = "compose.override.yaml"
_LOCK_NAME = ".apply.lock"
_CONFIG_SCHEMA = "ods.assistant-first.active-configuration.v1"
_MAX_COMPOSE_BYTES = 1024 * 1024
_MAX_OVERRIDE_BYTES = 4096
_MAX_CONFIG_BYTES = 4096
_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
_Runner = Callable[[tuple[str, ...], dict[str, str], int], bool]


class ComposeApplyEffectError(LifecycleWorkExecutionError):
    """Value-free, stable effect failure; never retain a secret-bearing cause."""


class ComposeApplyUncertainEffect(LifecycleWorkUncertainEffect):
    """Files or Docker may have changed; only current observation can decide."""


def _deny(code: str) -> None:
    raise LifecycleWorkValidationError(code) from None


def _fail(code: str) -> None:
    raise ComposeApplyEffectError(code) from None


@dataclass(frozen=True)
class ComposeApplyResult:
    service_id: str
    identity_sha256: str
    definition_sha256: str
    compose_sha256: str
    config_sha256: str
    override_sha256: str
    outcome: str  # file custody only; not APPLIED or healthy


def _verified_file(value: Any, expected_path: str, expected_digest: str) -> bytes:
    if (
        type(value) is not StagedArtifactFile
        or value.relative_path != expected_path
        or value.semantic_sha256 != expected_digest
        or type(value.content) is not bytes
        or type(value.size) is not int
        or value.size != len(value.content)
        or value.size > _MAX_COMPOSE_BYTES
        or value.raw_sha256 != "sha256:" + hashlib.sha256(value.content).hexdigest()
    ):
        _deny("lifecycle-work-compose-stage-mismatch")
    try:
        semantic = canonical_document_sha256(value.content)
    except CanonicalDocumentError:
        _deny("lifecycle-work-compose-stage-mismatch")
    if semantic != expected_digest:
        _deny("lifecycle-work-compose-stage-mismatch")
    return value.content


def _validate_inputs(
    command: Any, staged: Any, configuration: Any, identity: Any
) -> tuple[bytes, bytes]:
    if type(command) is not LifecycleWorkCommand:
        _deny("lifecycle-work-command-invalid")
    if command.operation_key != "apply:searxng" or command.service_ids != (
        CANARY_SERVICE_ID,
    ):
        _deny("lifecycle-work-compose-canary-denied")
    try:
        expected_identity = produce_application_identity(command)
    except Exception:  # noqa: BLE001 - malformed plans never leak their values
        _deny("lifecycle-work-compose-plan-mismatch")
    if (
        type(identity) is not ApplicationIdentity
        or identity != expected_identity
        or identity.definition_sha256 != CANARY_DEFINITION_SHA256
        or identity.compose_sha256 != CANARY_COMPOSE_SHA256
    ):
        _deny("lifecycle-work-compose-plan-mismatch")
    material = command.plan_material
    if (
        len(material.definitions) != 1
        or len(material.operations) != 1
        or material.definitions[0].service_type != "docker"
        or material.definitions[0].manifest_schema_version != CANARY_MANIFEST_SCHEMA
        or material.definitions[0].version != CANARY_VERSION
        or material.definitions[0].data_schema_version != CANARY_DATA_SCHEMA_VERSION
        or material.definitions[0].definition_source != "builtin"
        or material.definitions[0].compose_file != "compose.yaml"
        or len(material.definitions[0].images) != 1
        or material.definitions[0].images[0].reference != CANARY_IMAGE_REFERENCE
        or material.definitions[0].images[0].digest != CANARY_IMAGE_DIGEST
        or material.definitions[0].images[0].download_bytes
        != CANARY_IMAGE_DOWNLOAD_BYTES
        or material.definitions[0].builds != ()
        or type(command.timeout_seconds) is not int
        or not 1 <= command.timeout_seconds <= 600
    ):
        _deny("lifecycle-work-compose-plan-mismatch")
    if (
        type(staged) is not StagedArtifactBatch
        or staged.transaction_id != command.transaction_id
        or staged.plan_hash != command.plan_hash
        or staged.service_ids != (CANARY_SERVICE_ID,)
        or type(staged.definitions) is not tuple
        or len(staged.definitions) != 1
        or type(staged.definitions[0]) is not StagedDefinitionArtifacts
    ):
        _deny("lifecycle-work-compose-stage-mismatch")
    definition = staged.definitions[0]
    if (
        definition.service_id != CANARY_SERVICE_ID
        or definition.definition_source != "builtin"
    ):
        _deny("lifecycle-work-compose-stage-mismatch")
    manifest = _verified_file(
        definition.manifest, _MANIFEST_NAME, CANARY_DEFINITION_SHA256
    )
    compose = _verified_file(definition.compose, "compose.yaml", CANARY_COMPOSE_SHA256)
    if (
        type(configuration) is not BoundConfiguration
        or type(configuration.schema_hash) is not str
        or configuration.schema_hash != _CANARY_SCHEMA_HASH
        or type(configuration.port) is not int
        or not 1 <= configuration.port <= 65535
        or type(configuration.used_default_port) is not bool
        or type(configuration.secret_reference) is not str
    ):
        _deny("lifecycle-work-compose-configuration-mismatch")
    return manifest, compose


def _configuration_bytes(
    identity: ApplicationIdentity, configuration: BoundConfiguration
) -> bytes:
    """Persist only plan-bound, secret-free active configuration metadata."""
    payload = {
        "identitySha256": identity.identity_sha256,
        "planHash": identity.plan_sha256,
        "port": configuration.port,
        "schema": _CONFIG_SCHEMA,
        "schemaHash": configuration.schema_hash,
        "serviceId": identity.service_id,
        "transactionId": identity.transaction_id,
        "usedDefaultPort": configuration.used_default_port,
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
        _deny("lifecycle-work-compose-configuration-mismatch")
    return raw


def _override_bytes(identity: ApplicationIdentity, port: int) -> bytes:
    # The staged Compose file already resolves the port from SEARXNG_PORT. The
    # override pins labels and records the same port without adding a second
    # published port to the merged Compose service.
    labels = identity_labels(identity)
    lines = ["services:", "  searxng:", "    labels:"]
    for key, value in sorted(labels.items()):
        lines.append(f"      {json.dumps(key)}: {json.dumps(value)}")
    lines.extend([f"    x-ods-bound-port: {port}", ""])
    result = "\n".join(lines).encode("utf-8")
    if len(result) > _MAX_OVERRIDE_BYTES:
        _fail("lifecycle-work-compose-override-invalid")
    return result


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
        _fail("lifecycle-work-compose-platform-unqualified")


def _open_install(path: Path) -> int:
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        _fail("lifecycle-work-compose-custody-invalid")
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
            _fail("lifecycle-work-compose-custody-invalid")
        return descriptor
    except ComposeApplyEffectError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail("lifecycle-work-compose-custody-invalid")


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
            _fail("lifecycle-work-compose-custody-invalid")
        return descriptor
    except ComposeApplyEffectError:
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail("lifecycle-work-compose-custody-invalid")


def _read_exact(directory: int, name: str, expected: bytes) -> bool:
    try:
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=directory)
    except FileNotFoundError:
        return False
    except OSError:
        _fail("lifecycle-work-compose-file-drift")
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size != len(expected)
        ):
            _fail("lifecycle-work-compose-file-drift")
        if os.read(descriptor, len(expected) + 1) != expected:
            _fail("lifecycle-work-compose-file-drift")
        return True
    except OSError:
        _fail("lifecycle-work-compose-file-drift")
    finally:
        os.close(descriptor)


def _assert_lock_active(directory: int, descriptor: int) -> None:
    try:
        held = os.fstat(descriptor)
        current = os.stat(_LOCK_NAME, dir_fd=directory, follow_symlinks=False)
    except OSError:
        _fail("lifecycle-work-compose-lock-drift")
    if (
        not stat.S_ISREG(held.st_mode)
        or held.st_uid != os.geteuid()
        or held.st_nlink != 1
        or held.st_size != 0
        or stat.S_IMODE(held.st_mode) != 0o600
        or (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino)
        or not stat.S_ISREG(current.st_mode)
    ):
        _fail("lifecycle-work-compose-lock-drift")


def _assert_active_entries(directory: int) -> None:
    try:
        entries = set(os.listdir(directory))
    except OSError:
        _fail("lifecycle-work-compose-custody-invalid")
    if not entries.issubset(
        {_LOCK_NAME, _MANIFEST_NAME, _CONFIG_NAME, _COMPOSE_NAME, _OVERRIDE_NAME}
    ):
        _fail("lifecycle-work-compose-file-drift")


def _lock_active(directory: int) -> int:
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
        _assert_lock_active(directory, descriptor)
        deadline = time.monotonic() + 10
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    _fail("lifecycle-work-compose-lock-busy")
                time.sleep(0.05)
        _assert_lock_active(directory, descriptor)
        return descriptor
    except ComposeApplyEffectError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError:
        if descriptor >= 0:
            os.close(descriptor)
        _fail("lifecycle-work-compose-lock-unavailable")


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        count = os.write(descriptor, content[offset:])
        if count <= 0:
            _fail("lifecycle-work-compose-file-unavailable")
        offset += count


def _publish(directory: int, name: str, content: bytes) -> bool:
    if _read_exact(directory, name, content):
        return False
    temp_name = ".ods-compose-" + secrets.token_hex(16)
    temp = -1
    temp_identity: tuple[int, int] | None = None
    try:
        temp = os.open(
            temp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        info = os.fstat(temp)
        temp_identity = (info.st_dev, info.st_ino)
        _write_all(temp, content)
        os.fsync(temp)
        os.close(temp)
        temp = -1
        try:
            os.link(
                temp_name,
                name,
                src_dir_fd=directory,
                dst_dir_fd=directory,
                follow_symlinks=False,
            )
            created = True
        except FileExistsError:
            created = False
        os.unlink(temp_name, dir_fd=directory)
        temp_identity = None
        os.fsync(directory)
        if not _read_exact(directory, name, content):
            _fail("lifecycle-work-compose-file-drift")
        return created
    except ComposeApplyEffectError:
        raise
    except OSError:
        _fail("lifecycle-work-compose-file-unavailable")
    finally:
        if temp >= 0:
            os.close(temp)
        if temp_identity is not None:
            try:
                info = os.stat(temp_name, dir_fd=directory, follow_symlinks=False)
                if (info.st_dev, info.st_ino) == temp_identity and stat.S_ISREG(
                    info.st_mode
                ):
                    os.unlink(temp_name, dir_fd=directory)
            except OSError:
                pass


def run_compose(
    argv: tuple[str, ...], environment: dict[str, str], timeout: int
) -> bool:
    """Production runner: no shell or secret-bearing command output."""
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


class ComposeApplyEffect:
    def __init__(self, install_root: Path, runner: _Runner = run_compose) -> None:
        self._root = install_root
        self._runner = runner

    def apply(
        self,
        command: LifecycleWorkCommand,
        staged: StagedArtifactBatch,
        configuration: BoundConfiguration,
        identity: ApplicationIdentity,
        secret_store: Any,
    ) -> ComposeApplyResult:
        manifest, compose = _validate_inputs(command, staged, configuration, identity)
        _platform()
        if not callable(self._runner) or not callable(
            getattr(secret_store, "invoke_with_secrets", None)
        ):
            _deny("lifecycle-work-compose-consumer-invalid")
        override = _override_bytes(identity, configuration.port)
        config_metadata = _configuration_bytes(identity, configuration)
        config_sha256 = canonical_document_sha256(config_metadata)
        root = _open_install(self._root)
        directory = root
        lock = -1
        effect_may_have_started = False
        uncertain_code: str | None = None
        try:
            for name in _ROOT_PARTS:
                child = _open_owned_child(directory, name)
                if directory != root:
                    os.close(directory)
                directory = child
            lock = _lock_active(directory)
            _assert_active_entries(directory)
            # Reject an already unsafe or drifted target before the first
            # possible publication. _publish rechecks under the same lock;
            # a later race/failure is still an uncertain effect.
            _read_exact(directory, _MANIFEST_NAME, manifest)
            _read_exact(directory, _CONFIG_NAME, config_metadata)
            _read_exact(directory, _COMPOSE_NAME, compose)
            _read_exact(directory, _OVERRIDE_NAME, override)
            # A failed publication can still have changed the active file.
            # After this point a failed terminal receipt is never justified.
            effect_may_have_started = True
            created_manifest = _publish(directory, _MANIFEST_NAME, manifest)
            created_config = _publish(directory, _CONFIG_NAME, config_metadata)
            created_compose = _publish(directory, _COMPOSE_NAME, compose)
            created_override = _publish(directory, _OVERRIDE_NAME, override)
            active = self._root.joinpath(*_ROOT_PARTS)
            argv = (
                "docker",
                "compose",
                "--project-directory",
                str(self._root),
                "-f",
                str(active / _COMPOSE_NAME),
                "-f",
                str(active / _OVERRIDE_NAME),
                "up",
                "-d",
                "--no-build",
                "--no-deps",
                CANARY_SERVICE_ID,
            )
            request = {
                "schema": USE_REQUEST_SCHEMA,
                "transactionId": command.transaction_id,
                "planHash": command.plan_hash,
                "schemaHash": configuration.schema_hash,
                "reference": configuration.secret_reference,
                "expectedSecretKeys": ["SEARXNG_SECRET"],
            }
            succeeded = False

            def consume(values: Mapping[str, Any]) -> None:
                nonlocal succeeded
                value = values.get("SEARXNG_SECRET")
                if type(value) is not str or not value:
                    _fail("lifecycle-work-compose-secret-unavailable")
                environment = {
                    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                    "BIND_ADDRESS": "127.0.0.1",
                    "SEARXNG_PORT": str(configuration.port),
                    "SEARXNG_SECRET": value,
                }
                try:
                    succeeded = (
                        self._runner(argv, environment, command.timeout_seconds) is True
                    )
                finally:
                    environment.clear()

            unavailable = False
            try:
                secret_store.invoke_with_secrets(request, consume)
            except Exception:  # noqa: BLE001 - discard secret-bearing callback causes
                unavailable = True
            if unavailable:
                _fail("lifecycle-work-compose-secret-or-runner-unavailable")
            if not succeeded:
                _fail("lifecycle-work-compose-runner-failed")
            _assert_lock_active(directory, lock)
            _assert_active_entries(directory)
            return ComposeApplyResult(
                service_id=CANARY_SERVICE_ID,
                identity_sha256=identity.identity_sha256,
                definition_sha256=identity.definition_sha256,
                compose_sha256=identity.compose_sha256,
                config_sha256=config_sha256,
                override_sha256="sha256:" + hashlib.sha256(override).hexdigest(),
                outcome="materialized"
                if created_manifest
                or created_config
                or created_compose
                or created_override
                else "replayed",
            )
        except ComposeApplyEffectError as exc:
            if not effect_may_have_started:
                raise
            uncertain_code = exc.code
        finally:
            if lock >= 0:
                os.close(lock)
            if directory != root:
                os.close(directory)
            os.close(root)
        if uncertain_code is not None:
            # Raise outside the handler so no secret-bearing exception chain
            # crosses the host receipt boundary.
            raise ComposeApplyUncertainEffect(uncertain_code) from None
        raise ComposeApplyUncertainEffect(
            "lifecycle-work-compose-effect-uncertain"
        ) from None
