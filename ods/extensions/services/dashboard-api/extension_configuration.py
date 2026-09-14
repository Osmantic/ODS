"""Pure typed-configuration boundary for stored Assistant First plans.

The module has no filesystem, host-agent, framework, or secret-store access.
It verifies the stored plan, derives the complete configuration contract from
that plan, validates a split submission, and returns redacted metadata only.
Secret custody is deliberately deferred to the host-owned Phase 4B boundary.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit


_PLAN_SCHEMA = "ods.assistant-first.plan.v1"
_SCHEMA = "ods.assistant-first.configuration-schema.v1"
_RECEIPT_SCHEMA = "ods.assistant-first.configuration-validation.v1"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ENCODED_CONTROL_RE = re.compile(r"%(?:0[0-9a-f]|1[0-9a-f]|7f)", re.IGNORECASE)
_REQUIRED_CONTRACT_KEYS = frozenset(
    {"key", "type", "required", "secret", "source", "restartBehavior"}
)
_OPTIONAL_CONTRACT_KEYS = frozenset({"validation", "default"})
_VALID_TYPES = frozenset({"string", "integer", "boolean", "url", "enum"})
_VALID_SOURCES = frozenset({"user", "generated", "system", "provider"})
_VALID_RESTARTS = frozenset({"none", "service", "stack"})
_VALIDATION_KEYS = frozenset(
    {"choices", "minLength", "maxLength", "minimum", "maximum"}
)
_MAX_STRING_LENGTH = 65_536
_MAX_ENUM_CHOICES = 128
_MAX_SAFE_INTEGER = (1 << 53) - 1
_MAX_DEFINITIONS = 512
_MAX_CONFIGURATION_FIELDS = 2_048
_MAX_JSON_DEPTH = 32
_MAX_JSON_NODES = 50_000


class ExtensionConfigurationError(ValueError):
    """Stable validation error whose message never contains submitted values."""

    def __init__(self, code: str, **details: Any) -> None:
        super().__init__(code)
        self.code = code
        self.details = copy.deepcopy(details)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "details": copy.deepcopy(self.details)}

    def __repr__(self) -> str:
        return f"ExtensionConfigurationError({self.code!r})"


def _fail(code: str, **details: Any) -> None:
    raise ExtensionConfigurationError(code, **details)


def _json_shape(value: Any, *, depth: int = 0, budget: list[int] | None = None) -> None:
    """Bound attacker-controlled material before canonical serialization."""
    if budget is None:
        budget = [_MAX_JSON_NODES]
    budget[0] -= 1
    if budget[0] < 0 or depth > _MAX_JSON_DEPTH:
        _fail("configuration-input-too-large")
    if value is None or type(value) in {bool, int}:
        return
    if type(value) is float:
        _fail("float-not-canonical")
    if isinstance(value, str):
        if len(value) > _MAX_STRING_LENGTH or _has_control(value):
            _fail("invalid-configuration-text")
        return
    if type(value) is dict:
        if len(value) > _MAX_CONFIGURATION_FIELDS:
            _fail("configuration-input-too-large")
        if any(not isinstance(key, str) for key in value):
            _fail("invalid-configuration-object-key")
        for key, item in value.items():
            _json_shape(key, depth=depth + 1, budget=budget)
            _json_shape(item, depth=depth + 1, budget=budget)
        return
    if type(value) is list:
        if len(value) > _MAX_JSON_NODES:
            _fail("configuration-input-too-large")
        for item in value:
            _json_shape(item, depth=depth + 1, budget=budget)
        return
    _fail("invalid-configuration-json-type")


def _canonical_json_bytes(value: Any) -> bytes:
    _json_shape(value)
    try:
        document = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        _fail("invalid-configuration-json")
    return (document + "\n").encode("utf-8", errors="strict")


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if type(value) is not dict or any(not isinstance(key, str) for key in value):
        _fail("invalid-configuration-object", field=field)
    return value


def _sequence(value: Any, field: str) -> list[Any]:
    if type(value) is not list:
        _fail("invalid-configuration-list", field=field)
    return value


def _has_control(value: str) -> bool:
    return any(
        ord(character) < 32
        or ord(character) == 127
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in value
    )


def _safe_key_list(value: Any, field: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(_sequence(value, field)):
        if not isinstance(item, str) or _KEY_RE.fullmatch(item) is None:
            _fail("invalid-configuration-key", field=f"{field}[{index}]")
        if item in seen:
            _fail("duplicate-configuration-key", field=field, key=item)
        seen.add(item)
        result.append(item)
    return result


def _normalize_validation(value: Any, config_type: str, field: str) -> dict[str, Any]:
    rules = _mapping(value, field)
    keys = set(rules)
    if not keys or not keys <= _VALIDATION_KEYS:
        _fail("invalid-validation-fields", field=field, fields=sorted(keys))
    allowed = {
        "string": frozenset({"minLength", "maxLength"}),
        "url": frozenset({"minLength", "maxLength"}),
        "integer": frozenset({"minimum", "maximum"}),
        "enum": frozenset({"choices"}),
        "boolean": frozenset(),
    }[config_type]
    if not keys <= allowed:
        _fail("incompatible-validation", field=field, configType=config_type)

    normalized: dict[str, Any] = {}
    if "choices" in rules:
        choices: list[str] = []
        seen: set[str] = set()
        for index, choice in enumerate(_sequence(rules["choices"], f"{field}.choices")):
            if (
                not isinstance(choice, str)
                or not choice
                or len(choice) > 256
                or _has_control(choice)
            ):
                _fail("invalid-enum-choice", field=f"{field}.choices[{index}]")
            if choice in seen:
                _fail("duplicate-enum-choice", field=f"{field}.choices", choice=choice)
            seen.add(choice)
            choices.append(choice)
        if not choices or len(choices) > _MAX_ENUM_CHOICES:
            _fail("invalid-enum-choices", field=f"{field}.choices")
        normalized["choices"] = choices

    for key in ("minLength", "maxLength"):
        if key in rules:
            bound = rules[key]
            if type(bound) is not int or not 0 <= bound <= _MAX_STRING_LENGTH:
                _fail("invalid-validation-bound", field=f"{field}.{key}")
            normalized[key] = bound
    for key in ("minimum", "maximum"):
        if key in rules:
            bound = rules[key]
            if (
                type(bound) is not int
                or not -_MAX_SAFE_INTEGER <= bound <= _MAX_SAFE_INTEGER
            ):
                _fail("invalid-validation-bound", field=f"{field}.{key}")
            normalized[key] = bound

    if normalized.get("minLength", 0) > normalized.get("maxLength", _MAX_STRING_LENGTH):
        _fail("invalid-validation-range", field=field)
    if normalized.get("minimum", -_MAX_SAFE_INTEGER) > normalized.get(
        "maximum", _MAX_SAFE_INTEGER
    ):
        _fail("invalid-validation-range", field=field)
    return normalized


def _validate_url(value: str, field: str) -> None:
    if (
        any(character.isspace() for character in value)
        or "\\" in value
        or _ENCODED_CONTROL_RE.search(value) is not None
    ):
        _fail("invalid-configuration-url", field=field)
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        _fail("invalid-configuration-url", field=field)
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or not hostname.isascii()
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or port is not None
        and not 1 <= port <= 65535
    ):
        _fail("invalid-configuration-url", field=field)


def _validate_value(value: Any, contract: Mapping[str, Any], field: str) -> None:
    config_type = contract["type"]
    if config_type == "integer":
        if type(value) is not int:
            _fail("invalid-configuration-value-type", field=field)
    elif config_type == "boolean":
        if type(value) is not bool:
            _fail("invalid-configuration-value-type", field=field)
    elif config_type in {"string", "url", "enum"}:
        if (
            not isinstance(value, str)
            or len(value) > _MAX_STRING_LENGTH
            or _has_control(value)
        ):
            _fail("invalid-configuration-value-type", field=field)
    else:  # pragma: no cover - normalized contracts cannot reach this branch
        _fail("invalid-configuration-type", field=field)

    rules = contract.get("validation", {})
    if config_type in {"string", "url"}:
        if len(value) < rules.get("minLength", 0):
            _fail("configuration-value-too-short", field=field)
        if len(value) > rules.get("maxLength", _MAX_STRING_LENGTH):
            _fail("configuration-value-too-long", field=field)
    if config_type == "integer":
        if value < rules.get("minimum", -_MAX_SAFE_INTEGER):
            _fail("configuration-value-too-small", field=field)
        if value > rules.get("maximum", _MAX_SAFE_INTEGER):
            _fail("configuration-value-too-large", field=field)
    if config_type == "enum" and value not in rules["choices"]:
        _fail("invalid-configuration-choice", field=field)
    if config_type == "url":
        _validate_url(value, field)


def _normalize_contract(value: Any, field: str) -> dict[str, Any]:
    item = _mapping(value, field)
    keys = set(item)
    if not _REQUIRED_CONTRACT_KEYS <= keys or not keys <= (
        _REQUIRED_CONTRACT_KEYS | _OPTIONAL_CONTRACT_KEYS
    ):
        _fail("invalid-configuration-contract-fields", field=field, fields=sorted(keys))
    key = item["key"]
    if not isinstance(key, str) or _KEY_RE.fullmatch(key) is None:
        _fail("invalid-configuration-key", field=f"{field}.key")
    config_type = item["type"]
    if config_type not in _VALID_TYPES:
        _fail("invalid-configuration-type", field=f"{field}.type", key=key)
    if type(item["required"]) is not bool or type(item["secret"]) is not bool:
        _fail("invalid-configuration-contract", field=field, key=key)
    if (
        item["source"] not in _VALID_SOURCES
        or item["restartBehavior"] not in _VALID_RESTARTS
    ):
        _fail("invalid-configuration-contract", field=field, key=key)

    normalized: dict[str, Any] = {
        "key": key,
        "type": config_type,
        "required": item["required"],
        "secret": item["secret"],
        "source": item["source"],
        "restartBehavior": item["restartBehavior"],
    }
    if "validation" in item:
        normalized["validation"] = _normalize_validation(
            item["validation"], config_type, f"{field}.validation"
        )
    if config_type == "enum" and "validation" not in normalized:
        _fail("missing-enum-validation", field=f"{field}.validation", key=key)
    if "default" in item:
        if item["secret"]:
            _fail("secret-default-forbidden", field=field, key=key)
        _validate_value(item["default"], normalized, f"{field}.default")
        normalized["default"] = copy.deepcopy(item["default"])
    return normalized


def _verified_plan(
    envelope: Any,
    expected_plan_hash: str,
) -> tuple[Mapping[str, Any], str]:
    outer = _mapping(envelope, "envelope")
    plan = _mapping(outer.get("plan"), "envelope.plan")
    plan_hash = outer.get("planHash")
    if (
        not isinstance(expected_plan_hash, str)
        or _HASH_RE.fullmatch(expected_plan_hash) is None
    ):
        _fail("invalid-expected-plan-hash")
    if not isinstance(plan_hash, str) or _HASH_RE.fullmatch(plan_hash) is None:
        _fail("invalid-plan-hash")
    if plan_hash != expected_plan_hash:
        _fail("stored-plan-hash-mismatch")
    if plan.get("schema") != _PLAN_SCHEMA:
        _fail("invalid-plan-schema")
    snapshot = copy.deepcopy(plan)
    actual = hashlib.sha256(_canonical_json_bytes(snapshot)).hexdigest()
    if actual != plan_hash:
        _fail("plan-hash-mismatch")
    return snapshot, plan_hash


def _configuration_contracts(plan: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    selected = _sequence(plan.get("selectedServices"), "plan.selectedServices")
    if len(selected) > _MAX_DEFINITIONS:
        _fail("too-many-selected-services")
    selected_ids: list[str] = []
    selected_seen: set[str] = set()
    for index, service_id in enumerate(selected):
        if not isinstance(service_id, str) or _ID_RE.fullmatch(service_id) is None:
            _fail("invalid-selected-service", field=f"plan.selectedServices[{index}]")
        if service_id in selected_seen:
            _fail("duplicate-selected-service", serviceId=service_id)
        selected_seen.add(service_id)
        selected_ids.append(service_id)

    definitions = _sequence(plan.get("definitions"), "plan.definitions")
    if len(definitions) > _MAX_DEFINITIONS:
        _fail("too-many-definitions")
    by_id: dict[str, Mapping[str, Any]] = {}
    for index, raw_definition in enumerate(definitions):
        definition = _mapping(raw_definition, f"plan.definitions[{index}]")
        service_id = definition.get("id")
        if not isinstance(service_id, str) or _ID_RE.fullmatch(service_id) is None:
            _fail("invalid-definition-service", field=f"plan.definitions[{index}].id")
        if service_id in by_id:
            _fail("duplicate-service-definition", serviceId=service_id)
        by_id[service_id] = definition
    if set(by_id) != selected_seen:
        _fail(
            "selected-definition-mismatch",
            missing=sorted(selected_seen - set(by_id)),
            extra=sorted(set(by_id) - selected_seen),
        )

    contracts: dict[str, dict[str, Any]] = {}
    for service_id in selected_ids:
        raw_configuration = _sequence(
            by_id[service_id].get("configuration"),
            f"definition.{service_id}.configuration",
        )
        if len(raw_configuration) > _MAX_CONFIGURATION_FIELDS:
            _fail("too-many-configuration-fields", serviceId=service_id)
        for index, raw_contract in enumerate(raw_configuration):
            contract = _normalize_contract(
                raw_contract, f"definition.{service_id}.configuration[{index}]"
            )
            key = contract["key"]
            previous = contracts.get(key)
            if previous is not None and previous != contract:
                _fail("configuration-contract-conflict", key=key)
            contracts[key] = contract

    required_config = sorted(
        key
        for key, item in contracts.items()
        if item["required"] and not item["secret"]
    )
    required_secrets = sorted(
        key for key, item in contracts.items() if item["required"] and item["secret"]
    )
    if (
        _safe_key_list(plan.get("requiredConfigKeys"), "plan.requiredConfigKeys")
        != required_config
    ):
        _fail("required-configuration-contract-mismatch")
    if (
        _safe_key_list(plan.get("requiredSecretKeys"), "plan.requiredSecretKeys")
        != required_secrets
    ):
        _fail("required-secret-contract-mismatch")
    return {key: contracts[key] for key in sorted(contracts)}


def configuration_contracts(
    envelope: Any,
    *,
    expected_plan_hash: str,
) -> dict[str, dict[str, Any]]:
    """Derive all contracts from one externally anchored stored plan."""
    plan, _ = _verified_plan(envelope, expected_plan_hash)
    return _configuration_contracts(plan)


def _configuration_schema_document(
    contracts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": _SCHEMA,
        "fields": [copy.deepcopy(contracts[key]) for key in sorted(contracts)],
    }


def configuration_schema(
    envelope: Any,
    *,
    expected_plan_hash: str,
) -> dict[str, Any]:
    """Return complete public metadata without a value or secret default."""
    plan, plan_hash = _verified_plan(envelope, expected_plan_hash)
    contracts = _configuration_contracts(plan)
    document = _configuration_schema_document(contracts)
    return {
        **document,
        "planHash": plan_hash,
        "schemaHash": hashlib.sha256(_canonical_json_bytes(document)).hexdigest(),
    }


def validate_configuration_submission(
    envelope: Any,
    values: Any,
    secret_values: Any,
    *,
    expected_plan_hash: str,
) -> dict[str, Any]:
    """Validate user input and return only redacted presence/default metadata."""
    plan, plan_hash = _verified_plan(envelope, expected_plan_hash)
    contracts = _configuration_contracts(plan)
    value_map = _mapping(values, "values")
    secret_map = _mapping(secret_values, "secretValues")
    value_keys = set(value_map)
    secret_keys = set(secret_map)
    if value_keys & secret_keys:
        _fail("duplicate-submission-key", keys=sorted(value_keys & secret_keys))
    unknown = (value_keys | secret_keys) - set(contracts)
    if unknown:
        _fail("unknown-configuration-key", keys=sorted(unknown))
    wrong_values = sorted(key for key in value_keys if contracts[key]["secret"])
    wrong_secrets = sorted(key for key in secret_keys if not contracts[key]["secret"])
    if wrong_values:
        _fail("secret-in-nonsecret-values", keys=wrong_values)
    if wrong_secrets:
        _fail("nonsecret-in-secret-values", keys=wrong_secrets)

    submitted = value_keys | secret_keys
    restricted = sorted(key for key in submitted if contracts[key]["source"] != "user")
    if restricted:
        _fail("configuration-source-restricted", keys=restricted)
    missing_config = sorted(
        key
        for key, item in contracts.items()
        if item["source"] == "user"
        and item["required"]
        and not item["secret"]
        and "default" not in item
        and key not in value_keys
    )
    missing_secrets = sorted(
        key
        for key, item in contracts.items()
        if item["source"] == "user"
        and item["required"]
        and item["secret"]
        and key not in secret_keys
    )
    if missing_config or missing_secrets:
        _fail(
            "missing-required-configuration",
            configKeys=missing_config,
            secretKeys=missing_secrets,
        )

    for key in sorted(value_keys):
        _validate_value(value_map[key], contracts[key], f"values.{key}")
    for key in sorted(secret_keys):
        _validate_value(secret_map[key], contracts[key], f"secretValues.{key}")

    schema_document = _configuration_schema_document(contracts)
    schema_hash = hashlib.sha256(_canonical_json_bytes(schema_document)).hexdigest()
    return {
        "schema": _RECEIPT_SCHEMA,
        "planHash": plan_hash,
        "schemaHash": schema_hash,
        "presentConfigKeys": sorted(value_keys),
        "presentSecretKeys": sorted(secret_keys),
        "appliedDefaultKeys": sorted(
            key
            for key, item in contracts.items()
            if item["source"] == "user"
            and not item["secret"]
            and "default" in item
            and key not in value_keys
        ),
    }
