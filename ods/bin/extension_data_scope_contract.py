"""Exact approved path scope for future paired generic data effects.

This module takes no filesystem or container action.  It is the fail-closed
boundary between plan material and a streaming backup/restore implementation:
callers cannot supply paths, and an update cannot infer its old scope from a
new catalog definition.  A host effect must still verify the installed prior
manifest and publish an immutable snapshot before applying any mutation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from extension_lifecycle_plan import (
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
    PlannedOperation,
    PlannedPriorDataBinding,
    PlannedPriorDataPath,
)
from extension_lifecycle_work import LifecycleWorkCommand, LifecycleWorkValidationError


_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_TRANSACTION_RE = re.compile(r"^txn-[0-9a-f]{24}$")
_SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_PATH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,255}")
_RECORD_KEYS = frozenset({"path", "backupClass", "owner", "uninstall", "purge"})
_CLASSES = frozenset({"required", "recommended", "ephemeral"})
_OWNERS = frozenset({"ods", "extension", "user"})
_UNINSTALL = frozenset({"preserve", "archive"})
_PURGE = frozenset({"separate-approval", "unsupported"})


@dataclass(frozen=True)
class DataPathRecord:
    path: str
    backup_class: str
    owner: str
    uninstall: str
    purge: str


@dataclass(frozen=True)
class BoundDataPath:
    path: str
    prior: DataPathRecord | None
    selected: DataPathRecord | None


@dataclass(frozen=True)
class BoundServiceData:
    service_id: str
    action: str
    selected_definition_sha256: str
    selected_data_schema_version: str
    prior_definition_sha256: str | None
    prior_data_schema_version: str | None
    paths: tuple[BoundDataPath, ...]


@dataclass(frozen=True)
class BoundDataScope:
    transaction_id: str
    plan_hash: str
    operation_key: str
    services: tuple[BoundServiceData, ...]


def _reject() -> None:
    raise LifecycleWorkValidationError("lifecycle-work-data-scope-mismatch") from None


def _path(value: Any) -> str:
    if (
        not isinstance(value, str)
        or _PATH_RE.fullmatch(value) is None
        or ".." in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        _reject()
    return value


def _record(value: Any) -> DataPathRecord:
    if not isinstance(value, dict) or frozenset(value) != _RECORD_KEYS:
        _reject()
    for key, allowed in (
        ("backupClass", _CLASSES), ("owner", _OWNERS),
        ("uninstall", _UNINSTALL), ("purge", _PURGE),
    ):
        if not isinstance(value[key], str) or value[key] not in allowed:
            _reject()
    return DataPathRecord(
        _path(value["path"]), value["backupClass"], value["owner"],
        value["uninstall"], value["purge"],
    )


def _records(value: Any) -> tuple[DataPathRecord, ...]:
    if not isinstance(value, list) or len(value) > 2048:
        _reject()
    result = tuple(_record(item) for item in value)
    if tuple(item.path for item in result) != tuple(sorted({item.path for item in result})):
        _reject()
    return result


def _definition_data(definition: PlannedDefinition) -> tuple[DataPathRecord, ...]:
    if not isinstance(definition.canonical_document, bytes) or len(definition.canonical_document) > 1024 * 1024:
        _reject()
    try:
        document = json.loads(definition.canonical_document.decode("utf-8", errors="strict"))
        canonical = (json.dumps(document, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8", errors="strict")
    except (UnicodeError, ValueError, TypeError, RecursionError):
        _reject()
    if (
        not isinstance(document, dict)
        or canonical != definition.canonical_document
        or document.get("id") != definition.service_id
        or document.get("manifestSchemaVersion") != "ods.services.v2"
        or document.get("definitionSha256") != definition.definition_sha256
        or document.get("dataSchemaVersion") != definition.data_schema_version
    ):
        _reject()
    return _records(document.get("data"))


def _prior_records(binding: PlannedPriorDataBinding) -> tuple[DataPathRecord, ...]:
    if not isinstance(binding.paths, tuple) or len(binding.paths) > 2048:
        _reject()
    result: list[DataPathRecord] = []
    for item in binding.paths:
        if type(item) is not PlannedPriorDataPath:
            _reject()
        result.append(_record({
            "path": item.path, "backupClass": item.backup_class,
            "owner": item.owner, "uninstall": item.uninstall, "purge": item.purge,
        }))
    if tuple(item.path for item in result) != tuple(sorted({item.path for item in result})):
        _reject()
    return tuple(result)


def _check_overlaps(services: tuple[BoundServiceData, ...]) -> None:
    paths = sorted((path.path, service.service_id) for service in services for path in service.paths)
    seen: set[str] = set()
    for path, _service_id in paths:
        parts = path.split("/")
        if path in seen or any("/".join(parts[:index]) in seen for index in range(1, len(parts))):
            _reject()
        seen.add(path)


def bind_data_scope(command: Any) -> BoundDataScope:
    """Derive the complete old/new path union from one attested host plan."""
    if type(command) is not LifecycleWorkCommand:
        _reject()
    operation_key = command.operation_key
    if operation_key not in {"backup", "restore"}:
        _reject()
    if (
        not isinstance(command.transaction_id, str) or _TRANSACTION_RE.fullmatch(command.transaction_id) is None
        or not isinstance(command.plan_hash, str) or _HASH_RE.fullmatch(command.plan_hash) is None
        or not isinstance(command.request_hash, str) or _HASH_RE.fullmatch(command.request_hash) is None
        or not isinstance(command.service_ids, tuple)
        or command.payload != {"serviceIds": list(command.service_ids)}
    ):
        _reject()
    material = command.plan_material
    expected_state = "configuring" if operation_key == "backup" else "reconciling"
    if (
        type(material) is not LifecyclePlanMaterial
        or material.schema != PLAN_MATERIAL_SCHEMA
        or material.transaction_id != command.transaction_id
        or material.plan_hash != command.plan_hash
        or material.state != expected_state
        or material.attested_approval is not True
        or not isinstance(material.operations, tuple)
        or not isinstance(material.definitions, tuple)
        or not isinstance(material.prior_data_bindings, tuple)
    ):
        _reject()
    operations = material.operations
    definitions = material.definitions
    if (
        not operations or len(operations) != len(definitions)
        or any(type(item) is not PlannedOperation for item in operations)
        or any(type(item) is not PlannedDefinition for item in definitions)
        or any(not isinstance(item.service_id, str) or _SERVICE_RE.fullmatch(item.service_id) is None for item in operations)
        or any(not isinstance(item.service_id, str) or _SERVICE_RE.fullmatch(item.service_id) is None for item in definitions)
        or any(not isinstance(item.action, str) or item.action not in {"install", "enable", "repair", "update", "noop"} for item in operations)
        or tuple(item.service_id for item in operations) != tuple(item.service_id for item in definitions)
        or len({item.service_id for item in operations}) != len(operations)
    ):
        _reject()
    mutable = tuple(item for item in operations if item.action != "noop")
    if not mutable or command.service_ids != tuple(item.service_id for item in mutable):
        _reject()
    prior_bindings = material.prior_data_bindings
    if any(
        type(item) is not PlannedPriorDataBinding
        or not isinstance(item.service_id, str)
        or _SERVICE_RE.fullmatch(item.service_id) is None
        for item in prior_bindings
    ):
        _reject()
    prior = {item.service_id: item for item in prior_bindings}
    required_prior = {item.service_id for item in mutable if item.action in {"enable", "repair", "update"}}
    if len(prior) != len(prior_bindings) or set(prior) != required_prior:
        _reject()
    by_id = {item.service_id: item for item in definitions}
    services: list[BoundServiceData] = []
    for operation in mutable:
        definition = by_id[operation.service_id]
        if (
            operation.action not in {"install", "enable", "repair", "update"}
            or definition.manifest_schema_version != "ods.services.v2"
            or not isinstance(definition.definition_sha256, str)
            or _DIGEST_RE.fullmatch(definition.definition_sha256) is None
            or not isinstance(definition.data_schema_version, str)
            or not definition.data_schema_version
        ):
            _reject()
        selected = {item.path: item for item in _definition_data(definition)}
        old_binding = prior.get(operation.service_id)
        old: dict[str, DataPathRecord] = {}
        if old_binding is not None:
            if (
                not isinstance(old_binding.definition_sha256, str)
                or _DIGEST_RE.fullmatch(old_binding.definition_sha256) is None
                or not isinstance(old_binding.version, str)
                or not old_binding.version
                or not isinstance(old_binding.data_schema_version, str)
                or not old_binding.data_schema_version
            ):
                _reject()
            old = {item.path: item for item in _prior_records(old_binding)}
        paths = tuple(
            BoundDataPath(path, old.get(path), selected.get(path))
            for path in sorted(set(old) | set(selected))
        )
        services.append(BoundServiceData(
            service_id=operation.service_id,
            action=operation.action,
            selected_definition_sha256=definition.definition_sha256,
            selected_data_schema_version=definition.data_schema_version,
            prior_definition_sha256=None if old_binding is None else old_binding.definition_sha256,
            prior_data_schema_version=None if old_binding is None else old_binding.data_schema_version,
            paths=paths,
        ))
    bound = tuple(services)
    _check_overlaps(bound)
    return BoundDataScope(command.transaction_id, command.plan_hash, operation_key, bound)


__all__ = ["BoundDataPath", "BoundDataScope", "BoundServiceData", "DataPathRecord", "bind_data_scope"]
