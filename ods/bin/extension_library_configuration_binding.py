"""Bind approved configuration to one Manifest v2 library apply effect.

The Dashboard validates and seals configuration before owner approval.  A
later host effect must not trust a loose collection of values, however: it
must prove the exact transaction record is the record covered by that
approval, rederive the configuration schema from the immutable plan, and
verify host secret custody without reading a secret value.

This module performs that binding only.  It has no filesystem, subprocess,
Docker, Compose, network, or secret-value access.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from assistant_first_secret_store import STATUS_REQUEST_SCHEMA, STATUS_SCHEMA
from extension_lifecycle_plan import (
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
    PlannedOperation,
)
from extension_lifecycle_work import (
    LifecycleWorkCommand,
    LifecycleWorkExecutionError,
    LifecycleWorkValidationError,
)


CONFIGURATION_SCHEMA = "ods.assistant-first.configuration-schema.v1"
TRANSACTION_CONFIGURATION_SCHEMA = (
    "ods.assistant-first.transaction-configuration.v1"
)

_ACTIONS = frozenset({"install", "enable", "repair", "update"})
_RECORD_KEYS = frozenset(
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
_STATUS_KEYS = frozenset(
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
_CONTRACT_REQUIRED = frozenset(
    {"key", "type", "required", "secret", "source", "restartBehavior"}
)
_CONTRACT_OPTIONAL = frozenset({"validation", "default"})
_VALIDATION_KEYS = frozenset(
    {"choices", "minLength", "maxLength", "minimum", "maximum"}
)
_TYPES = frozenset({"string", "integer", "boolean", "url", "enum"})
_SOURCES = frozenset({"user", "generated", "system", "provider"})
_RESTARTS = frozenset({"none", "service", "stack"})
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_REFERENCE_RE = re.compile(r"^secret-v1-[0-9a-f]{48}$")
_ENCODED_CONTROL_RE = re.compile(r"%(?:0[0-9a-f]|1[0-9a-f]|7f)", re.IGNORECASE)
_MAX_FIELDS = 2_048
_MAX_STRING = 65_536
_MAX_SAFE_INTEGER = (1 << 53) - 1


class LibraryConfigurationBindingError(LifecycleWorkExecutionError):
    """Stable, value-free failure while checking host secret custody."""


@dataclass(frozen=True)
class BoundLibraryConfiguration:
    """Secret-free, approval-bound configuration for one library service."""

    transaction_id: str
    plan_hash: str
    service_id: str
    schema_hash: str
    values: tuple[tuple[str, bool | int | str], ...]
    secret_keys: tuple[str, ...]
    expected_secret_keys: tuple[str, ...]
    secret_reference: str | None
    configured: bool


def _deny(code: str) -> None:
    raise LifecycleWorkValidationError(code) from None


def _fail(code: str) -> None:
    raise LibraryConfigurationBindingError(code) from None


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
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _deny("library-configuration-invalid")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _text(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= _MAX_STRING
        and not any(
            ord(character) < 32
            or ord(character) == 127
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        )
    )


def _validate_url(value: str) -> None:
    if (
        any(character.isspace() for character in value)
        or "\\" in value
        or _ENCODED_CONTROL_RE.search(value) is not None
    ):
        _deny("library-configuration-value-invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        _deny("library-configuration-value-invalid")
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or not parsed.hostname.isascii()
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port is not None
        and not 1 <= port <= 65535
    ):
        _deny("library-configuration-value-invalid")


def _validate_value(value: Any, contract: dict[str, Any]) -> None:
    kind = contract["type"]
    if kind == "integer":
        if type(value) is not int or not -_MAX_SAFE_INTEGER <= value <= _MAX_SAFE_INTEGER:
            _deny("library-configuration-value-invalid")
    elif kind == "boolean":
        if type(value) is not bool:
            _deny("library-configuration-value-invalid")
    elif kind in {"string", "url", "enum"}:
        if not _text(value):
            _deny("library-configuration-value-invalid")
    else:
        _deny("library-configuration-schema-invalid")

    rules = contract.get("validation", {})
    if kind in {"string", "url"}:
        minimum = rules.get("minLength", 0)
        if contract["secret"]:
            minimum = max(1, minimum)
        if not minimum <= len(value) <= rules.get("maxLength", _MAX_STRING):
            _deny("library-configuration-value-invalid")
    if kind == "integer" and not (
        rules.get("minimum", -_MAX_SAFE_INTEGER)
        <= value
        <= rules.get("maximum", _MAX_SAFE_INTEGER)
    ):
        _deny("library-configuration-value-invalid")
    if kind == "enum" and value not in rules["choices"]:
        _deny("library-configuration-value-invalid")
    if kind == "url":
        _validate_url(value)


def _normalize_contract(value: Any) -> dict[str, Any]:
    if type(value) is not dict or not _CONTRACT_REQUIRED <= set(value) or not set(
        value
    ) <= _CONTRACT_REQUIRED | _CONTRACT_OPTIONAL:
        _deny("library-configuration-schema-invalid")
    key = value.get("key")
    kind = value.get("type")
    if (
        not isinstance(key, str)
        or _KEY_RE.fullmatch(key) is None
        or kind not in _TYPES
        or type(value.get("required")) is not bool
        or type(value.get("secret")) is not bool
        or value.get("source") not in _SOURCES
        or value.get("restartBehavior") not in _RESTARTS
    ):
        _deny("library-configuration-schema-invalid")
    result = {
        "key": key,
        "type": kind,
        "required": value["required"],
        "secret": value["secret"],
        "source": value["source"],
        "restartBehavior": value["restartBehavior"],
    }
    if "validation" in value:
        rules = value["validation"]
        if type(rules) is not dict or not rules or not set(rules) <= _VALIDATION_KEYS:
            _deny("library-configuration-schema-invalid")
        allowed = {
            "string": {"minLength", "maxLength"},
            "url": {"minLength", "maxLength"},
            "integer": {"minimum", "maximum"},
            "enum": {"choices"},
            "boolean": set(),
        }[kind]
        if not set(rules) <= allowed:
            _deny("library-configuration-schema-invalid")
        normalized: dict[str, Any] = {}
        if "choices" in rules:
            choices = rules["choices"]
            if (
                type(choices) is not list
                or not choices
                or len(choices) > 128
                or any(not _text(item) or not item or len(item) > 256 for item in choices)
                or len(set(choices)) != len(choices)
            ):
                _deny("library-configuration-schema-invalid")
            normalized["choices"] = list(choices)
        for name in ("minLength", "maxLength"):
            if name in rules:
                bound = rules[name]
                if type(bound) is not int or not 0 <= bound <= _MAX_STRING:
                    _deny("library-configuration-schema-invalid")
                normalized[name] = bound
        for name in ("minimum", "maximum"):
            if name in rules:
                bound = rules[name]
                if (
                    type(bound) is not int
                    or not -_MAX_SAFE_INTEGER <= bound <= _MAX_SAFE_INTEGER
                ):
                    _deny("library-configuration-schema-invalid")
                normalized[name] = bound
        if normalized.get("minLength", 0) > normalized.get(
            "maxLength", _MAX_STRING
        ) or normalized.get("minimum", -_MAX_SAFE_INTEGER) > normalized.get(
            "maximum", _MAX_SAFE_INTEGER
        ):
            _deny("library-configuration-schema-invalid")
        result["validation"] = normalized
    if kind == "enum" and "validation" not in result:
        _deny("library-configuration-schema-invalid")
    if "default" in value:
        if value["secret"]:
            _deny("library-configuration-schema-invalid")
        _validate_value(value["default"], result)
        result["default"] = value["default"]
    return result


def _definition_document(definition: PlannedDefinition) -> dict[str, Any]:
    try:
        document = json.loads(definition.canonical_document.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        _deny("library-configuration-plan-mismatch")
    if type(document) is not dict or _canonical_json(document) != definition.canonical_document:
        _deny("library-configuration-plan-mismatch")
    if document.get("id") != definition.service_id:
        _deny("library-configuration-plan-mismatch")
    return document


def _plan_contracts(
    command: Any,
    *,
    expected_state: str,
    target_service_id: str | None,
) -> tuple[str, dict[str, dict[str, Any]], dict[str, tuple[str, ...]]]:
    if (
        type(command) is not LifecycleWorkCommand
        or type(command.service_ids) is not tuple
        or any(type(item) is not str for item in command.service_ids)
    ):
        _deny("library-configuration-command-invalid")
    if expected_state == "applying" and target_service_id is None:
        if len(command.service_ids) != 1:
            _deny("library-configuration-command-invalid")
        service_id = command.service_ids[0]
        if command.operation_key != f"apply:{service_id}":
            _deny("library-configuration-command-invalid")
    elif expected_state == "configuring" and type(target_service_id) is str:
        service_id = target_service_id
        if (
            not command.service_ids
            or len(set(command.service_ids)) != len(command.service_ids)
            or service_id not in command.service_ids
            or command.operation_key != "configure"
            or command.payload != {"serviceIds": list(command.service_ids)}
        ):
            _deny("library-configuration-command-invalid")
    else:
        _deny("library-configuration-command-invalid")
    material = command.plan_material
    if (
        type(material) is not LifecyclePlanMaterial
        or material.schema != PLAN_MATERIAL_SCHEMA
        or material.transaction_id != command.transaction_id
        or material.plan_hash != command.plan_hash
        or material.state != expected_state
        or material.attested_approval is not True
        or type(material.operations) is not tuple
        or type(material.definitions) is not tuple
        or not material.operations
        or len(material.operations) != len(material.definitions)
    ):
        _deny("library-configuration-plan-mismatch")

    contracts: dict[str, dict[str, Any]] = {}
    by_service: dict[str, tuple[str, ...]] = {}
    seen_services: set[str] = set()
    selected = False
    mutable_services: list[str] = []
    for operation, definition in zip(
        material.operations, material.definitions, strict=True
    ):
        if (
            type(operation) is not PlannedOperation
            or type(definition) is not PlannedDefinition
            or definition.service_id != operation.service_id
            or definition.service_id in seen_services
            or operation.action not in _ACTIONS | {"noop"}
        ):
            _deny("library-configuration-plan-mismatch")
        seen_services.add(definition.service_id)
        if operation.action in _ACTIONS:
            mutable_services.append(definition.service_id)
        document = _definition_document(definition)
        raw_fields = document.get("configuration")
        if type(raw_fields) is not list or len(raw_fields) > _MAX_FIELDS:
            _deny("library-configuration-schema-invalid")
        keys: list[str] = []
        for raw in raw_fields:
            contract = _normalize_contract(raw)
            key = contract["key"]
            previous = contracts.get(key)
            if previous is not None and previous != contract:
                _deny("library-configuration-schema-conflict")
            contracts[key] = contract
            if key not in keys:
                keys.append(key)
        by_service[definition.service_id] = tuple(sorted(keys))
        if definition.service_id == service_id:
            if (
                selected
                or operation.action not in _ACTIONS
                or (
                    expected_state == "applying"
                    and command.payload
                    != {
                        "operation": {
                            "serviceId": service_id,
                            "action": operation.action,
                        }
                    }
                )
                or definition.service_type != "docker"
                or definition.manifest_schema_version != "ods.services.v2"
                or definition.definition_source != "library"
            ):
                _deny("library-configuration-definition-unsupported")
            selected = True
    if not selected:
        _deny("library-configuration-plan-mismatch")
    if expected_state == "configuring" and command.service_ids != tuple(
        mutable_services
    ):
        _deny("library-configuration-plan-mismatch")
    return service_id, dict(sorted(contracts.items())), by_service


def _key_list(value: Any) -> tuple[str, ...]:
    if (
        type(value) is not list
        or len(value) > _MAX_FIELDS
        or any(not isinstance(item, str) or _KEY_RE.fullmatch(item) is None for item in value)
        or value != sorted(set(value))
    ):
        _deny("library-configuration-record-mismatch")
    return tuple(value)


def _configuration_attestation(
    transaction_id: str,
    plan_hash: str,
    schema_hash: str,
    configured: bool,
    values: dict[str, Any],
    config_keys: tuple[str, ...],
    secret_keys: tuple[str, ...],
    default_keys: tuple[str, ...],
) -> str:
    return _digest(
        {
            "schema": "ods.assistant-first.configuration-attestation.v1",
            "transactionId": transaction_id,
            "planHash": plan_hash,
            "schemaHash": schema_hash,
            "configured": configured,
            "values": values,
            "presentConfigKeys": list(config_keys),
            "presentSecretKeys": list(secret_keys),
            "appliedDefaultKeys": list(default_keys),
        }
    )


def _private_digest(configuration: dict[str, Any] | None, schema_hash: str) -> str:
    return _digest(
        {
            "schema": "ods.assistant-first.configuration-record-digest.v1",
            "schemaHash": schema_hash,
            "configuration": configuration,
        }
    )


def bind_library_configuration(
    command: LifecycleWorkCommand,
    transaction_loader: Any,
    secret_status: Any,
    *,
    expected_state: str = "applying",
    target_service_id: str | None = None,
) -> BoundLibraryConfiguration:
    """Return approved, secret-free configuration for one selected service."""

    service_id, contracts, by_service = _plan_contracts(
        command,
        expected_state=expected_state,
        target_service_id=target_service_id,
    )
    if not callable(transaction_loader):
        _fail("library-configuration-store-unavailable")
    try:
        transaction = transaction_loader(command.transaction_id)
    except Exception:  # noqa: BLE001 - store details stay private
        _fail("library-configuration-store-unavailable")
    if (
        type(transaction) is not dict
        or transaction.get("transactionId") != command.transaction_id
        or transaction.get("state") != expected_state
        or type(transaction.get("envelope")) is not dict
        or transaction["envelope"].get("planHash") != command.plan_hash
        or type(transaction.get("approval")) is not dict
        or transaction["approval"].get("transactionId") != command.transaction_id
        or transaction["approval"].get("planHash") != command.plan_hash
    ):
        _deny("library-configuration-transaction-mismatch")

    schema_hash = hashlib.sha256(
        _canonical_json(
            {
                "schema": CONFIGURATION_SCHEMA,
                "fields": [contracts[key] for key in sorted(contracts)],
            }
        )
    ).hexdigest()
    approval = transaction["approval"]
    if approval.get("configurationSchemaHash") != schema_hash:
        _deny("library-configuration-approval-mismatch")

    record = transaction.get("configuration")
    if record is None:
        values: dict[str, Any] = {}
        config_keys: tuple[str, ...] = ()
        secret_keys: tuple[str, ...] = ()
        default_keys: tuple[str, ...] = ()
        reference = None
        configured = False
    else:
        if (
            type(record) is not dict
            or set(record) != _RECORD_KEYS
            or record.get("schema") != TRANSACTION_CONFIGURATION_SCHEMA
            or record.get("transactionId") != command.transaction_id
            or record.get("planHash") != command.plan_hash
            or record.get("schemaHash") != schema_hash
            or type(record.get("values")) is not dict
            or len(record["values"]) > _MAX_FIELDS
        ):
            _deny("library-configuration-record-mismatch")
        values = dict(record["values"])
        config_keys = _key_list(record.get("presentConfigKeys"))
        secret_keys = _key_list(record.get("presentSecretKeys"))
        default_keys = _key_list(record.get("appliedDefaultKeys"))
        reference = record.get("secretReference")
        configured = True

    if set(values) != set(config_keys):
        _deny("library-configuration-record-mismatch")
    if set(config_keys) & set(secret_keys) or set(config_keys) & set(default_keys):
        _deny("library-configuration-record-mismatch")
    if any(key not in contracts or contracts[key]["secret"] for key in config_keys):
        _deny("library-configuration-record-mismatch")
    if any(key not in contracts or not contracts[key]["secret"] for key in secret_keys):
        _deny("library-configuration-record-mismatch")

    expected_defaults = tuple(
        sorted(
            key
            for key, contract in contracts.items()
            if contract["source"] == "user"
            and not contract["secret"]
            and "default" in contract
            and key not in values
        )
    )
    if configured and default_keys != expected_defaults:
        _deny("library-configuration-record-mismatch")
    for key, contract in contracts.items():
        if key in values:
            _validate_value(values[key], contract)
        elif contract["required"] and not contract["secret"] and "default" not in contract:
            _deny("library-configuration-record-mismatch")
        if contract["required"] and contract["secret"] and key not in secret_keys:
            _deny("library-configuration-record-mismatch")

    attestation = _configuration_attestation(
        command.transaction_id,
        command.plan_hash,
        schema_hash,
        configured,
        values,
        config_keys,
        secret_keys,
        default_keys,
    )
    if (
        approval.get("configurationHash") != attestation
        or approval.get("privateConfigurationDigest")
        != _private_digest(record, schema_hash)
    ):
        _deny("library-configuration-approval-mismatch")

    if secret_keys:
        if (
            not isinstance(reference, str)
            or _REFERENCE_RE.fullmatch(reference) is None
            or not callable(secret_status)
        ):
            _fail("library-configuration-secret-unavailable")
        request = {
            "schema": STATUS_REQUEST_SCHEMA,
            "transactionId": command.transaction_id,
            "planHash": command.plan_hash,
            "schemaHash": schema_hash,
            "reference": reference,
        }
        try:
            status = secret_status(request)
        except Exception:  # noqa: BLE001 - secret errors remain value-free
            _fail("library-configuration-secret-unavailable")
        if (
            type(status) is not dict
            or set(status) != _STATUS_KEYS
            or status.get("schema") != STATUS_SCHEMA
            or status.get("transactionId") != command.transaction_id
            or status.get("planHash") != command.plan_hash
            or status.get("schemaHash") != schema_hash
            or status.get("reference") != reference
            or status.get("configured") is not True
            or status.get("presentSecretKeys") != list(secret_keys)
        ):
            _fail("library-configuration-secret-mismatch")
    elif reference is not None:
        _deny("library-configuration-record-mismatch")

    service_values: list[tuple[str, bool | int | str]] = []
    service_secrets: list[str] = []
    for key in by_service[service_id]:
        contract = contracts[key]
        if contract["secret"]:
            if key in secret_keys:
                service_secrets.append(key)
        elif key in values:
            service_values.append((key, values[key]))
        elif "default" in contract:
            service_values.append((key, contract["default"]))

    return BoundLibraryConfiguration(
        transaction_id=command.transaction_id,
        plan_hash=command.plan_hash,
        service_id=service_id,
        schema_hash=schema_hash,
        values=tuple(service_values),
        secret_keys=tuple(service_secrets),
        expected_secret_keys=secret_keys,
        secret_reference=reference,
        configured=configured,
    )


__all__ = [
    "BoundLibraryConfiguration",
    "CONFIGURATION_SCHEMA",
    "LibraryConfigurationBindingError",
    "TRANSACTION_CONFIGURATION_SCHEMA",
    "bind_library_configuration",
]
