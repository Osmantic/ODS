"""Publish configuration for the exact SearXNG Assistant First canary.

The transaction store owns non-secret values and the host secret store owns
secret values.  This module verifies both durable records, then atomically
publishes only the non-secret SearXNG settings document.  The secret never
enters the settings file, lifecycle request, receipt, evidence, or response;
the later apply effect supplies it to the container from host custody.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assistant_first_secret_store import STATUS_REQUEST_SCHEMA, STATUS_SCHEMA
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
from extension_lifecycle_plan import (
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
    PlannedImage,
    PlannedOperation,
)
from extension_lifecycle_work import (
    LifecycleWorkCommand,
    LifecycleWorkError,
    LifecycleWorkExecutionError,
    LifecycleWorkStartedObservation,
    LifecycleWorkValidationError,
)

CONFIGURATION_EVIDENCE_SCHEMA = "ods.extension-configuration-evidence.v1"
TRANSACTION_CONFIGURATION_SCHEMA = (
    "ods.assistant-first.transaction-configuration.v1"
)
CONFIGURATION_SCHEMA = "ods.assistant-first.configuration-schema.v1"
CANARY_SETTINGS_PATH = "config/searxng/settings.yml"
CANARY_SETTINGS_BYTES = b"""use_default_settings: true
server:
  bind_address: \"0.0.0.0\"
  port: 8080
  limiter: false
search:
  safe_search: 0
  formats:
    - html
    - json
"""
CANARY_CONFIGURATION = [
    {
        "key": "SEARXNG_PORT",
        "type": "integer",
        "required": False,
        "secret": False,
        "source": "user",
        "restartBehavior": "service",
        "validation": {"minimum": 1, "maximum": 65535},
        "default": 8888,
    },
    {
        "key": "SEARXNG_SECRET",
        "type": "string",
        "required": True,
        "secret": True,
        "source": "generated",
        "restartBehavior": "service",
        "validation": {"minLength": 32, "maxLength": 128},
    },
]

_ACTIONS = frozenset({"install", "enable", "repair", "update"})
_CONFIGURATION_KEYS = frozenset(
    {
        "actor",
        "appliedDefaultKeys",
        "configuredAt",
        "idempotencyKey",
        "planHash",
        "presentConfigKeys",
        "presentSecretKeys",
        "schema",
        "schemaHash",
        "secretReference",
        "transactionId",
        "values",
    }
)
_SECRET_STATUS_KEYS = frozenset(
    {
        "configured",
        "planHash",
        "presentSecretKeys",
        "reference",
        "schema",
        "schemaHash",
        "transactionId",
    }
)
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_REFERENCE_RE = re.compile(r"^secret-v1-[0-9a-f]{48}$")
_TRANSACTION_RE = re.compile(r"^txn-[0-9a-f]{24}$")
_TEMP_RE = re.compile(r"^\.settings\.yml\.[0-9a-f]{24}\.tmp$")
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | _CLOEXEC
)
_FILE_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_PlanLoader = Callable[[LifecycleWorkCommand], LifecycleWorkCommand]
_TransactionLoader = Callable[[str], dict[str, Any]]
_SecretStatus = Callable[[dict[str, Any]], dict[str, Any]]


class ConfigurationEffectRuntimeError(LifecycleWorkExecutionError):
    """Stable, value-free failure after a configuration command was accepted."""


@dataclass(frozen=True)
class BoundConfiguration:
    """Validated, secret-free configuration metadata for one lifecycle state.

    The opaque secret reference is safe to pass back to the owner-private
    secret store.  Secret values are never materialized by this object.
    """

    schema_hash: str
    port: int
    used_default_port: bool
    secret_reference: str


def _validation_error(code: str) -> None:
    raise LifecycleWorkValidationError(code) from None


def _execution_error(code: str, cause: BaseException | None = None) -> None:
    if cause is None:
        raise ConfigurationEffectRuntimeError(code) from None
    raise ConfigurationEffectRuntimeError(code) from cause


def _canonical_json(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError) as exc:
        _execution_error("lifecycle-work-configuration-invalid", exc)


class _DuplicateKey(ValueError):
    pass


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _reject_number(_value: str) -> Any:
    raise ValueError("non-integer-number")


def _definition_document(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_no_duplicate_keys,
            parse_float=_reject_number,
            parse_constant=_reject_number,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, _DuplicateKey):
        _validation_error("lifecycle-work-plan-mismatch")
    if not isinstance(value, dict) or _canonical_json(value) != raw:
        _validation_error("lifecycle-work-plan-mismatch")
    return value


def _validate_plan(command: Any) -> None:
    if type(command) is not LifecycleWorkCommand:
        _validation_error("lifecycle-work-command-invalid")
    if (
        not isinstance(command.transaction_id, str)
        or _TRANSACTION_RE.fullmatch(command.transaction_id) is None
        or not isinstance(command.plan_hash, str)
        or _HASH_RE.fullmatch(command.plan_hash) is None
        or command.operation_key != "configure"
        or command.service_ids != (CANARY_SERVICE_ID,)
        or command.payload != {"serviceIds": [CANARY_SERVICE_ID]}
    ):
        _validation_error("lifecycle-work-configuration-canary-denied")

    material = command.plan_material
    if (
        type(material) is not LifecyclePlanMaterial
        or material.schema != PLAN_MATERIAL_SCHEMA
        or material.transaction_id != command.transaction_id
        or material.plan_hash != command.plan_hash
        or material.state != "configuring"
        or type(material.operations) is not tuple
        or type(material.definitions) is not tuple
        or len(material.operations) != 1
        or len(material.definitions) != 1
    ):
        _validation_error("lifecycle-work-plan-mismatch")

    operation = material.operations[0]
    definition = material.definitions[0]
    if (
        type(operation) is not PlannedOperation
        or type(definition) is not PlannedDefinition
        or operation.service_id != CANARY_SERVICE_ID
        or definition.service_id != CANARY_SERVICE_ID
        or operation.action not in _ACTIONS
    ):
        _validation_error("lifecycle-work-plan-mismatch")
    if (
        definition.service_type != "docker"
        or definition.manifest_schema_version != CANARY_MANIFEST_SCHEMA
        or definition.version != CANARY_VERSION
        or definition.data_schema_version != CANARY_DATA_SCHEMA_VERSION
        or definition.definition_source != "builtin"
        or definition.definition_sha256 != CANARY_DEFINITION_SHA256
        or definition.compose_sha256 != CANARY_COMPOSE_SHA256
        or definition.compose_file != "compose.yaml"
        or type(definition.images) is not tuple
        or len(definition.images) != 1
        or type(definition.builds) is not tuple
        or definition.builds
    ):
        _validation_error("lifecycle-work-configuration-canary-denied")
    image = definition.images[0]
    if (
        type(image) is not PlannedImage
        or image.reference != CANARY_IMAGE_REFERENCE
        or image.digest != CANARY_IMAGE_DIGEST
        or image.download_bytes != CANARY_IMAGE_DOWNLOAD_BYTES
        or isinstance(image.download_bytes, bool)
    ):
        _validation_error("lifecycle-work-configuration-canary-denied")
    document = _definition_document(definition.canonical_document)
    if document.get("configuration") != CANARY_CONFIGURATION:
        _validation_error("lifecycle-work-configuration-canary-denied")


def _validate_loaded_command(
    original: LifecycleWorkCommand, loaded: Any
) -> LifecycleWorkCommand:
    if (
        type(loaded) is not LifecycleWorkCommand
        or loaded.plan_material is None
        or any(
            getattr(loaded, field) != getattr(original, field)
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
        _validation_error("lifecycle-work-plan-mismatch")
    return loaded


def load_bound_configuration(
    command: LifecycleWorkCommand,
    transaction_loader: _TransactionLoader,
    secret_status: _SecretStatus,
    *,
    expected_state: str,
) -> BoundConfiguration:
    """Re-prove the canary configuration in ``configuring`` or ``applying``.

    Configuration is collected and sealed while the transaction is in its
    configuring state, then deliberately retained as immutable input for the
    later applying state.  Keeping this validation in one public helper avoids
    a second, subtly different configuration parser in the application effect.
    """

    if type(command) is not LifecycleWorkCommand:
        _validation_error("lifecycle-work-command-invalid")
    if type(expected_state) is not str or expected_state not in {
        "configuring",
        "applying",
    }:
        _validation_error("lifecycle-work-configuration-state-invalid")
    if (
        not isinstance(command.transaction_id, str)
        or _TRANSACTION_RE.fullmatch(command.transaction_id) is None
        or not isinstance(command.plan_hash, str)
        or _HASH_RE.fullmatch(command.plan_hash) is None
        or command.service_ids != (CANARY_SERVICE_ID,)
    ):
        _validation_error("lifecycle-work-configuration-state-invalid")
    material = command.plan_material
    if (
        type(material) is not LifecyclePlanMaterial
        or material.schema != PLAN_MATERIAL_SCHEMA
        or material.transaction_id != command.transaction_id
        or material.plan_hash != command.plan_hash
        or material.state != expected_state
    ):
        _validation_error("lifecycle-work-configuration-state-invalid")
    if expected_state == "configuring":
        operation_matches = (
            command.operation_key == "configure"
            and command.payload == {"serviceIds": [CANARY_SERVICE_ID]}
        )
    else:
        operation = (
            command.payload.get("operation")
            if type(command.payload) is dict
            else None
        )
        operation_matches = (
            command.operation_key == f"apply:{CANARY_SERVICE_ID}"
            and type(operation) is dict
            and set(operation) == {"serviceId", "action"}
            and operation.get("serviceId") == CANARY_SERVICE_ID
            and operation.get("action") in _ACTIONS
        )
    if not operation_matches:
        _validation_error("lifecycle-work-configuration-state-invalid")
    if not callable(transaction_loader) or not callable(secret_status):
        _execution_error("lifecycle-work-configuration-runtime-invalid")

    try:
        transaction = transaction_loader(command.transaction_id)
    except LifecycleWorkError:
        raise
    except Exception as exc:  # noqa: BLE001 - keep transaction details private
        _execution_error("lifecycle-work-configuration-store-unavailable", exc)
    if (
        type(transaction) is not dict
        or transaction.get("transactionId") != command.transaction_id
        or transaction.get("state") != expected_state
        or not isinstance(transaction.get("envelope"), dict)
        or transaction["envelope"].get("planHash") != command.plan_hash
        or not isinstance(transaction.get("approval"), dict)
        or transaction["approval"].get("planHash") != command.plan_hash
    ):
        _validation_error("lifecycle-work-plan-mismatch")
    record = transaction.get("configuration")
    if type(record) is not dict or set(record) != _CONFIGURATION_KEYS:
        _validation_error("lifecycle-work-configuration-mismatch")
    schema_hash = hashlib.sha256(
        _canonical_json(
            {"schema": CONFIGURATION_SCHEMA, "fields": CANARY_CONFIGURATION}
        )
    ).hexdigest()
    if (
        record.get("schema") != TRANSACTION_CONFIGURATION_SCHEMA
        or record.get("transactionId") != command.transaction_id
        or record.get("planHash") != command.plan_hash
        or record.get("schemaHash") != schema_hash
        or record.get("presentSecretKeys") != ["SEARXNG_SECRET"]
        or not isinstance(record.get("secretReference"), str)
        or _REFERENCE_RE.fullmatch(record["secretReference"]) is None
    ):
        _validation_error("lifecycle-work-configuration-mismatch")

    values = record.get("values")
    present = record.get("presentConfigKeys")
    defaults = record.get("appliedDefaultKeys")
    if values == {} and present == [] and defaults == ["SEARXNG_PORT"]:
        port = 8888
        used_default = True
    elif (
        type(values) is dict
        and set(values) == {"SEARXNG_PORT"}
        and present == ["SEARXNG_PORT"]
        and defaults == []
        and type(values["SEARXNG_PORT"]) is int
        and 1 <= values["SEARXNG_PORT"] <= 65535
    ):
        port = values["SEARXNG_PORT"]
        used_default = False
    else:
        _validation_error("lifecycle-work-configuration-mismatch")

    status_request = {
        "schema": STATUS_REQUEST_SCHEMA,
        "transactionId": command.transaction_id,
        "planHash": command.plan_hash,
        "schemaHash": schema_hash,
        "reference": record["secretReference"],
    }
    try:
        status = secret_status(status_request)
    except Exception as exc:  # noqa: BLE001 - secret errors must remain value-free
        _execution_error("lifecycle-work-configuration-secret-unavailable", exc)
    if (
        type(status) is not dict
        or set(status) != _SECRET_STATUS_KEYS
        or status.get("schema") != STATUS_SCHEMA
        or status.get("transactionId") != command.transaction_id
        or status.get("planHash") != command.plan_hash
        or status.get("schemaHash") != schema_hash
        or status.get("reference") != record["secretReference"]
        or status.get("configured") is not True
        or status.get("presentSecretKeys") != ["SEARXNG_SECRET"]
    ):
        _execution_error("lifecycle-work-configuration-secret-mismatch")
    return BoundConfiguration(
        schema_hash=schema_hash,
        port=port,
        used_default_port=used_default,
        secret_reference=record["secretReference"],
    )


def _validate_platform() -> None:
    required = (os.open, os.stat, os.mkdir, os.unlink, os.rename)
    if (
        os.name != "posix"
        or not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW"))
        or any(function not in os.supports_dir_fd for function in required)
        or os.stat not in os.supports_follow_symlinks
        or not callable(getattr(os, "fsync", None))
    ):
        _execution_error("lifecycle-work-configuration-platform-unqualified")


def _verify_directory(info: os.stat_result) -> None:
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o022
    ):
        _execution_error("lifecycle-work-configuration-custody-invalid")


def _open_absolute_directory(path: Path) -> int:
    if not path.is_absolute():
        _execution_error("lifecycle-work-configuration-custody-invalid")
    try:
        descriptor = os.open("/", _DIRECTORY_FLAGS)
    except OSError as exc:
        _execution_error("lifecycle-work-configuration-path-unavailable", exc)
    try:
        parts = path.parts[1:]
        for index, part in enumerate(parts):
            if part in {"", ".", ".."}:
                _execution_error("lifecycle-work-configuration-custody-invalid")
            try:
                child = os.open(part, _DIRECTORY_FLAGS, dir_fd=descriptor)
            except OSError as exc:
                _execution_error(
                    "lifecycle-work-configuration-path-unavailable", exc
                )
            os.close(descriptor)
            descriptor = child
            if index == len(parts) - 1:
                try:
                    _verify_directory(os.fstat(descriptor))
                except OSError as exc:
                    _execution_error(
                        "lifecycle-work-configuration-path-unavailable", exc
                    )
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_child_directory(parent: int, name: str, *, create: bool) -> int:
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
    except FileNotFoundError:
        if not create:
            raise
        try:
            os.mkdir(name, 0o755, dir_fd=parent)
            os.fsync(parent)
        except FileExistsError:
            pass
        except OSError as exc:
            _execution_error("lifecycle-work-configuration-path-unavailable", exc)
        try:
            descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
        except OSError as exc:
            _execution_error("lifecycle-work-configuration-path-unavailable", exc)
    except OSError as exc:
        _execution_error("lifecycle-work-configuration-path-unavailable", exc)
    try:
        _verify_directory(os.fstat(descriptor))
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _verify_settings_file(info: os.stat_result) -> None:
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) not in {0o600, 0o644}
        or info.st_size > 64 * 1024
    ):
        _execution_error("lifecycle-work-configuration-custody-invalid")


def _read_settings(
    directory: int, *, missing_ok: bool
) -> tuple[bytes, int] | None:
    try:
        before = os.stat("settings.yml", dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return None
        _execution_error("lifecycle-work-configuration-observation-failed")
    except OSError as exc:
        _execution_error("lifecycle-work-configuration-observation-failed", exc)
    _verify_settings_file(before)
    try:
        descriptor = os.open(
            "settings.yml",
            os.O_RDONLY | _FILE_NOFOLLOW | _CLOEXEC,
            dir_fd=directory,
        )
    except OSError as exc:
        _execution_error("lifecycle-work-configuration-observation-failed", exc)
    try:
        try:
            opened = os.fstat(descriptor)
            _verify_settings_file(opened)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                _execution_error("lifecycle-work-configuration-race")
            content = b""
            while len(content) < opened.st_size:
                chunk = os.read(descriptor, opened.st_size - len(content))
                if not chunk:
                    _execution_error(
                        "lifecycle-work-configuration-observation-failed"
                    )
                content += chunk
            after = os.fstat(descriptor)
            if (
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                != (
                    opened.st_dev,
                    opened.st_ino,
                    opened.st_size,
                    opened.st_mtime_ns,
                )
            ):
                _execution_error("lifecycle-work-configuration-race")
            named_after = os.stat(
                "settings.yml", dir_fd=directory, follow_symlinks=False
            )
            _verify_settings_file(named_after)
            if (
                named_after.st_dev,
                named_after.st_ino,
                named_after.st_size,
                named_after.st_mtime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                _execution_error("lifecycle-work-configuration-race")
            return content, stat.S_IMODE(after.st_mode)
        except OSError as exc:
            _execution_error("lifecycle-work-configuration-observation-failed", exc)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset:])
        if written <= 0:
            _execution_error("lifecycle-work-configuration-write-failed")
        offset += written


def _publish_settings(directory: int) -> None:
    current = _read_settings(directory, missing_ok=True)
    if current == (CANARY_SETTINGS_BYTES, 0o644):
        return
    temporary = f".settings.yml.{secrets.token_hex(12)}.tmp"
    if _TEMP_RE.fullmatch(temporary) is None:
        _execution_error("lifecycle-work-configuration-write-failed")
    descriptor = -1
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_NOFOLLOW | _CLOEXEC,
            0o600,
            dir_fd=directory,
        )
        _write_all(descriptor, CANARY_SETTINGS_BYTES)
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o644)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.rename(
            temporary,
            "settings.yml",
            src_dir_fd=directory,
            dst_dir_fd=directory,
        )
        temporary = ""
        os.fsync(directory)
    except OSError as exc:
        _execution_error("lifecycle-work-configuration-write-failed", exc)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
            except OSError:
                _execution_error("lifecycle-work-configuration-write-failed")
    if _read_settings(directory, missing_ok=False) != (
        CANARY_SETTINGS_BYTES,
        0o644,
    ):
        _execution_error("lifecycle-work-configuration-write-unverified")


def _evidence_hash(
    command: LifecycleWorkCommand, bound: BoundConfiguration
) -> str:
    configuration_hash = hashlib.sha256(
        _canonical_json(
            {
                "schemaHash": bound.schema_hash,
                "port": bound.port,
                "usedDefaultPort": bound.used_default_port,
                "secretReference": bound.secret_reference,
            }
        )
    ).hexdigest()
    return hashlib.sha256(
        _canonical_json(
            {
                "schema": CONFIGURATION_EVIDENCE_SCHEMA,
                "transactionId": command.transaction_id,
                "planHash": command.plan_hash,
                "operationKey": command.operation_key,
                "serviceIds": list(command.service_ids),
                "configurationSha256": configuration_hash,
                "settingsSha256": hashlib.sha256(CANARY_SETTINGS_BYTES).hexdigest(),
            }
        )
    ).hexdigest()


class ConfigurationEffectStore:
    """Descriptor-relative atomic publication under one fixed install root."""

    _lock = threading.RLock()

    def __init__(self, install_dir: Path) -> None:
        _validate_platform()
        self._install_dir = install_dir
        descriptor = _open_absolute_directory(install_dir)
        try:
            config = _open_child_directory(descriptor, "config", create=False)
            os.close(config)
        except FileNotFoundError as exc:
            _execution_error("lifecycle-work-configuration-path-unavailable", exc)
        finally:
            os.close(descriptor)

    def publish(self) -> None:
        with self._lock:
            install = _open_absolute_directory(self._install_dir)
            config = searxng = -1
            try:
                config = _open_child_directory(install, "config", create=False)
                searxng = _open_child_directory(config, "searxng", create=True)
                _publish_settings(searxng)
            except FileNotFoundError as exc:
                _execution_error("lifecycle-work-configuration-path-unavailable", exc)
            finally:
                if searxng >= 0:
                    os.close(searxng)
                if config >= 0:
                    os.close(config)
                os.close(install)

    def matches(self) -> bool:
        with self._lock:
            install = _open_absolute_directory(self._install_dir)
            config = searxng = -1
            try:
                config = _open_child_directory(install, "config", create=False)
                try:
                    searxng = _open_child_directory(config, "searxng", create=False)
                except FileNotFoundError:
                    return False
                return _read_settings(searxng, missing_ok=True) == (
                    CANARY_SETTINGS_BYTES,
                    0o644,
                )
            except FileNotFoundError:
                return False
            finally:
                if searxng >= 0:
                    os.close(searxng)
                if config >= 0:
                    os.close(config)
                os.close(install)


class ConfigurationEffectDispatcher:
    def __init__(
        self,
        store: ConfigurationEffectStore,
        transaction_loader: _TransactionLoader,
        secret_status: _SecretStatus,
    ) -> None:
        self._store = store
        self._transaction_loader = transaction_loader
        self._secret_status = secret_status

    def __call__(self, command: LifecycleWorkCommand) -> str:
        _validate_plan(command)
        bound = load_bound_configuration(
            command,
            self._transaction_loader,
            self._secret_status,
            expected_state="configuring",
        )
        self._store.publish()
        if not self._store.matches():
            _execution_error("lifecycle-work-configuration-write-unverified")
        return _evidence_hash(command, bound)


class ConfigurationEffectStartedObserver:
    def __init__(
        self,
        plan_loader: _PlanLoader,
        transaction_loader: _TransactionLoader,
        secret_status: _SecretStatus,
        store: ConfigurationEffectStore,
    ) -> None:
        self._plan_loader = plan_loader
        self._transaction_loader = transaction_loader
        self._secret_status = secret_status
        self._store = store

    def __call__(
        self, command: LifecycleWorkCommand
    ) -> LifecycleWorkStartedObservation:
        try:
            loaded = self._plan_loader(command)
        except LifecycleWorkError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep raw plan errors private
            _execution_error("lifecycle-work-configuration-observation-failed", exc)
        bound_command = _validate_loaded_command(command, loaded)
        _validate_plan(bound_command)
        bound = load_bound_configuration(
            bound_command,
            self._transaction_loader,
            self._secret_status,
            expected_state="configuring",
        )
        if not self._store.matches():
            return LifecycleWorkStartedObservation(state="missing")
        return LifecycleWorkStartedObservation(
            state="completed",
            evidence_hash=_evidence_hash(bound_command, bound),
        )


@dataclass(frozen=True)
class ConfigurationEffectRuntime:
    store: ConfigurationEffectStore
    dispatcher: ConfigurationEffectDispatcher
    started_observer: ConfigurationEffectStartedObserver


def build_configuration_effect_runtime(
    *,
    install_dir: Path,
    plan_loader: _PlanLoader,
    transaction_loader: _TransactionLoader,
    secret_status: _SecretStatus,
) -> ConfigurationEffectRuntime:
    """Compose the exact canary effect without publishing configuration."""

    if not all(
        callable(value) for value in (plan_loader, transaction_loader, secret_status)
    ):
        _execution_error("lifecycle-work-configuration-runtime-invalid")
    store = ConfigurationEffectStore(install_dir)
    return ConfigurationEffectRuntime(
        store=store,
        dispatcher=ConfigurationEffectDispatcher(
            store, transaction_loader, secret_status
        ),
        started_observer=ConfigurationEffectStartedObserver(
            plan_loader, transaction_loader, secret_status, store
        ),
    )


__all__ = [
    "BoundConfiguration",
    "CANARY_CONFIGURATION",
    "CANARY_SETTINGS_BYTES",
    "CANARY_SETTINGS_PATH",
    "CONFIGURATION_EVIDENCE_SCHEMA",
    "ConfigurationEffectDispatcher",
    "ConfigurationEffectRuntime",
    "ConfigurationEffectRuntimeError",
    "ConfigurationEffectStartedObserver",
    "ConfigurationEffectStore",
    "build_configuration_effect_runtime",
    "load_bound_configuration",
]
