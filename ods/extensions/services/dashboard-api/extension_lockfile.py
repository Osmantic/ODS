"""Canonical desired-state lockfile custody for Assistant First extensions.

The lockfile is written only from a durable, committed transaction. It records
immutable definitions, resolved dependency edges, configuration schema hashes,
and opaque secret references without copying configuration or secret values.
The store serializes writers, verifies the prior-hash chain, and replaces the
canonical record atomically.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any, Mapping

from assistant_first_planner import PlanningError, normalize_host_state
from extension_operation_locks import ServiceLockError, exclusive_file_lock


LOCKFILE_SCHEMA = "ods.extensions.lockfile.v1"
LOCKFILE_ENVELOPE_SCHEMA = "ods.extensions.lockfile-envelope.v1"
LOCKFILE_FILENAME = "extensions.lock.json"

_DOCUMENT_FIELDS = frozenset(
    {
        "schema",
        "odsVersion",
        "catalogRevision",
        "platform",
        "architecture",
        "containerRuntime",
        "runtimeMode",
        "postCommitObservedStateRevision",
        "extensions",
        "lastCommittedTransaction",
        "priorLockfileHash",
        "backupReference",
    }
)
_ENVELOPE_FIELDS = frozenset({"schema", "lockfileHash", "lockfile"})
_EXTENSION_FIELDS = frozenset(
    {
        "id",
        "desiredState",
        "version",
        "manifestSchemaVersion",
        "dataSchemaVersion",
        "odsCompatibility",
        "definitionHashes",
        "imageDigests",
        "dependencyEdges",
        "configuration",
    }
)
_COMPATIBILITY_FIELDS = frozenset({"minimum", "maximum"})
_DEFINITION_HASH_FIELDS = frozenset({"manifestSha256", "composeSha256"})
_CONFIGURATION_FIELDS = frozenset(
    {
        "schemaSha256",
        "presentConfigKeys",
        "presentSecretKeys",
        "secretReference",
    }
)
_TRANSACTION_FIELDS = frozenset(
    {"transactionId", "planHash", "sequence", "plannedObservedStateRevision"}
)

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_CAPABILITY_RE = re.compile(
    r"^[a-z0-9][a-z0-9._-]{0,63}@[1-9][0-9]*(?:\.[0-9]+){0,2}$"
)
_CONFIG_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_TRANSACTION_RE = re.compile(r"^txn-[0-9a-f]{24}$")
_SECRET_REFERENCE_RE = re.compile(r"^secret-v1-[0-9a-f]{48}$")
_BACKUP_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_VERSION_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_SAFE_VERSION_TEXT_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")
_RUNTIME_MODES = frozenset({"assistant-first", "full", "core", "custom"})
_PLATFORMS = frozenset({"linux", "darwin", "windows"})
_ARCHITECTURES = frozenset({"amd64", "arm64"})
_CONTAINER_RUNTIMES = frozenset({"docker", "podman", "none"})
_DESIRED_STATES = frozenset({"enabled", "disabled"})
_ENSURE_ACTIONS = frozenset({"install", "enable", "repair", "update", "noop"})
_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_EXTENSIONS = 512
_MAX_LIST_ITEMS = 4096
_MAX_JSON_DEPTH = 48
_MAX_JSON_NODES = 100_000


class ExtensionLockfileError(RuntimeError):
    """A stable lockfile failure that does not project private values."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    def __repr__(self) -> str:
        return f"ExtensionLockfileError({self.code!r})"


def _fail(code: str) -> None:
    raise ExtensionLockfileError(code)


def _json_safe(
    value: Any,
    *,
    depth: int = 0,
    budget: list[int] | None = None,
    active: set[int] | None = None,
) -> None:
    if budget is None:
        budget = [_MAX_JSON_NODES]
    if active is None:
        active = set()
    budget[0] -= 1
    if budget[0] < 0 or depth > _MAX_JSON_DEPTH:
        _fail("lockfile-too-large")
    if value is None or type(value) in {bool, int}:
        return
    if isinstance(value, str):
        if any(
            ord(character) < 32
            or ord(character) == 127
            or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        ):
            _fail("lockfile-invalid-text")
        return
    if type(value) is float:
        _fail("lockfile-float-not-canonical")
    if type(value) not in {dict, list}:
        _fail("lockfile-invalid-json-type")
    identity = id(value)
    if identity in active:
        _fail("lockfile-cyclic-json")
    active.add(identity)
    try:
        if type(value) is dict:
            if len(value) > _MAX_LIST_ITEMS:
                _fail("lockfile-too-large")
            for key, item in value.items():
                if not isinstance(key, str):
                    _fail("lockfile-invalid-object-key")
                _json_safe(key, depth=depth + 1, budget=budget, active=active)
                _json_safe(item, depth=depth + 1, budget=budget, active=active)
        else:
            if len(value) > _MAX_LIST_ITEMS:
                _fail("lockfile-too-large")
            for item in value:
                _json_safe(item, depth=depth + 1, budget=budget, active=active)
    finally:
        active.remove(identity)


def canonical_lockfile_bytes(value: Any) -> bytes:
    """Return the sole canonical JSON byte representation for lockfile data."""

    _json_safe(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:  # defensive after shape validation
        raise ExtensionLockfileError("lockfile-invalid-json") from exc
    return (encoded + "\n").encode("utf-8", errors="strict")


def _object(value: Any, fields: frozenset[str], code: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        _fail(code)
    return value


def _hash(value: Any, code: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _digest(value: Any, code: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _service_id(value: Any, code: str = "invalid-extension-id") -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _safe_version_text(value: Any, code: str) -> str:
    if not isinstance(value, str) or _SAFE_VERSION_TEXT_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _semver(value: Any, code: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or _VERSION_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _sorted_unique_keys(value: Any, code: str) -> list[str]:
    if type(value) is not list:
        _fail(code)
    if any(
        not isinstance(item, str) or _CONFIG_KEY_RE.fullmatch(item) is None
        for item in value
    ):
        _fail(code)
    if value != sorted(set(value)):
        _fail(code)
    return list(value)


def _validate_configuration(value: Any) -> dict[str, Any]:
    config = _object(value, _CONFIGURATION_FIELDS, "configuration-fields")
    _hash(config["schemaSha256"], "configuration-schema-hash")
    present_config = _sorted_unique_keys(
        config["presentConfigKeys"], "configuration-present-keys"
    )
    present_secret = _sorted_unique_keys(
        config["presentSecretKeys"], "configuration-secret-keys"
    )
    if set(present_config) & set(present_secret):
        _fail("configuration-key-overlap")
    reference = config["secretReference"]
    if present_secret:
        if (
            not isinstance(reference, str)
            or _SECRET_REFERENCE_RE.fullmatch(reference) is None
        ):
            _fail("configuration-secret-reference")
    elif reference is not None:
        _fail("configuration-secret-reference")
    return config


def _edge_sort_key(edge: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(edge.get("kind", "")),
        str(edge.get("capability", "")),
        str(edge.get("target", "")),
    )


def _validate_extension(value: Any) -> dict[str, Any]:
    extension = _object(value, _EXTENSION_FIELDS, "extension-fields")
    service_id = _service_id(extension["id"])
    if extension["desiredState"] not in _DESIRED_STATES:
        _fail("invalid-desired-state")
    _semver(extension["version"], "invalid-extension-version")
    _safe_version_text(
        extension["manifestSchemaVersion"], "invalid-manifest-schema-version"
    )
    _safe_version_text(extension["dataSchemaVersion"], "invalid-data-schema-version")
    compatibility = _object(
        extension["odsCompatibility"],
        _COMPATIBILITY_FIELDS,
        "compatibility-fields",
    )
    _semver(compatibility["minimum"], "invalid-compatibility-minimum")
    _semver(
        compatibility["maximum"],
        "invalid-compatibility-maximum",
        optional=True,
    )
    definition_hashes = _object(
        extension["definitionHashes"],
        _DEFINITION_HASH_FIELDS,
        "definition-hash-fields",
    )
    _digest(definition_hashes["manifestSha256"], "invalid-manifest-hash")
    _digest(
        definition_hashes["composeSha256"],
        "invalid-compose-hash",
        optional=True,
    )
    image_digests = extension["imageDigests"]
    if type(image_digests) is not list or any(
        not isinstance(item, str) or _DIGEST_RE.fullmatch(item) is None
        for item in image_digests
    ):
        _fail("invalid-image-digests")
    if image_digests != sorted(set(image_digests)):
        _fail("invalid-image-digests")
    edges = extension["dependencyEdges"]
    if type(edges) is not list:
        _fail("invalid-dependency-edges")
    if any(type(edge) is not dict for edge in edges):
        _fail("invalid-dependency-edge")
    if edges != sorted(edges, key=_edge_sort_key):
        _fail("invalid-dependency-edges")
    seen_edges: set[tuple[str, str, str]] = set()
    for edge in edges:
        if type(edge) is not dict:
            _fail("invalid-dependency-edge")
        kind = edge.get("kind")
        if kind == "service":
            if set(edge) != {"kind", "target"}:
                _fail("invalid-dependency-edge")
            capability = ""
        elif kind == "capability":
            if set(edge) != {"kind", "capability", "target"}:
                _fail("invalid-dependency-edge")
            capability = edge["capability"]
            if (
                not isinstance(capability, str)
                or _CAPABILITY_RE.fullmatch(capability) is None
            ):
                _fail("invalid-dependency-edge")
        else:
            _fail("invalid-dependency-edge")
        target = _service_id(edge.get("target"), "invalid-dependency-target")
        if target == service_id:
            _fail("self-dependency-edge")
        identity = (kind, capability, target)
        if identity in seen_edges:
            _fail("duplicate-dependency-edge")
        seen_edges.add(identity)
    _validate_configuration(extension["configuration"])
    return extension


def validate_lockfile_document(value: Any) -> dict[str, Any]:
    """Validate and clone one strict canonical lockfile document."""

    document = _object(value, _DOCUMENT_FIELDS, "lockfile-fields")
    if document["schema"] != LOCKFILE_SCHEMA:
        _fail("lockfile-schema")
    _semver(document["odsVersion"], "invalid-ods-version")
    _hash(document["catalogRevision"], "invalid-catalog-revision")
    if document["platform"] not in _PLATFORMS:
        _fail("invalid-platform")
    if document["architecture"] not in _ARCHITECTURES:
        _fail("invalid-architecture")
    if document["containerRuntime"] not in _CONTAINER_RUNTIMES:
        _fail("invalid-container-runtime")
    if document["runtimeMode"] not in _RUNTIME_MODES:
        _fail("invalid-runtime-mode")
    _hash(
        document["postCommitObservedStateRevision"],
        "invalid-post-commit-observed-state-revision",
    )

    extensions = document["extensions"]
    if type(extensions) is not list or len(extensions) > _MAX_EXTENSIONS:
        _fail("invalid-extensions")
    for extension in extensions:
        _validate_extension(extension)
    ids = [item["id"] for item in extensions]
    if ids != sorted(set(ids)):
        _fail("extensions-not-canonical")
    known_ids = set(ids)
    for extension in extensions:
        for edge in extension["dependencyEdges"]:
            if edge["target"] not in known_ids:
                _fail("dependency-target-not-locked")

    transaction = _object(
        document["lastCommittedTransaction"],
        _TRANSACTION_FIELDS,
        "transaction-fields",
    )
    if (
        not isinstance(transaction["transactionId"], str)
        or _TRANSACTION_RE.fullmatch(transaction["transactionId"]) is None
    ):
        _fail("invalid-transaction-id")
    _hash(transaction["planHash"], "invalid-plan-hash")
    _hash(
        transaction["plannedObservedStateRevision"],
        "invalid-planned-observed-state-revision",
    )
    if type(transaction["sequence"]) is not int or transaction["sequence"] < 0:
        _fail("invalid-transaction-sequence")
    prior_hash = document["priorLockfileHash"]
    if prior_hash is not None:
        _hash(prior_hash, "invalid-prior-lockfile-hash")
    backup_reference = document["backupReference"]
    if backup_reference is not None and (
        not isinstance(backup_reference, str)
        or ".." in backup_reference
        or _BACKUP_REFERENCE_RE.fullmatch(backup_reference) is None
    ):
        _fail("invalid-backup-reference")

    return json.loads(canonical_lockfile_bytes(document))


def lockfile_envelope(document: Any) -> dict[str, Any]:
    """Return a validated lockfile wrapped in its external hash anchor."""

    normalized = validate_lockfile_document(document)
    lockfile_hash = hashlib.sha256(canonical_lockfile_bytes(normalized)).hexdigest()
    return {
        "schema": LOCKFILE_ENVELOPE_SCHEMA,
        "lockfileHash": lockfile_hash,
        "lockfile": normalized,
    }


def validate_lockfile_envelope(value: Any) -> dict[str, Any]:
    """Validate an envelope and require its hash to match canonical bytes."""

    envelope = _object(value, _ENVELOPE_FIELDS, "lockfile-envelope-fields")
    if envelope["schema"] != LOCKFILE_ENVELOPE_SCHEMA:
        _fail("lockfile-envelope-schema")
    supplied_hash = _hash(envelope["lockfileHash"], "invalid-lockfile-hash")
    document = validate_lockfile_document(envelope["lockfile"])
    actual_hash = hashlib.sha256(canonical_lockfile_bytes(document)).hexdigest()
    if supplied_hash != actual_hash:
        _fail("lockfile-hash-mismatch")
    return {
        "schema": LOCKFILE_ENVELOPE_SCHEMA,
        "lockfileHash": supplied_hash,
        "lockfile": document,
    }


def _configuration_record(
    transaction: Mapping[str, Any],
    plan_hash: str,
    selected_ids: set[str],
    definitions: Mapping[str, Mapping[str, Any]],
) -> tuple[set[str], set[str], str | None]:
    record = transaction.get("configuration")
    if record is None:
        return set(), set(), None
    if not isinstance(record, Mapping):
        _fail("configuration-record")
    if record.get("transactionId") != transaction.get("transactionId"):
        _fail("configuration-transaction-mismatch")
    if record.get("planHash") != plan_hash:
        _fail("configuration-plan-mismatch")
    _hash(record.get("schemaHash"), "configuration-schema-hash")
    present_config = _sorted_unique_keys(
        record.get("presentConfigKeys"), "configuration-present-keys"
    )
    present_secret = _sorted_unique_keys(
        record.get("presentSecretKeys"), "configuration-secret-keys"
    )
    if set(present_config) & set(present_secret):
        _fail("configuration-key-overlap")
    reference = record.get("secretReference")
    if present_secret:
        if (
            not isinstance(reference, str)
            or _SECRET_REFERENCE_RE.fullmatch(reference) is None
        ):
            _fail("configuration-secret-reference")
    elif reference is not None:
        _fail("configuration-secret-reference")

    declared_config: set[str] = set()
    declared_secret: set[str] = set()
    for service_id in selected_ids:
        configuration = definitions[service_id].get("configuration")
        if type(configuration) is not list:
            _fail("definition-configuration")
        for item in configuration:
            if not isinstance(item, Mapping):
                _fail("definition-configuration")
            key = item.get("key")
            if not isinstance(key, str) or _CONFIG_KEY_RE.fullmatch(key) is None:
                _fail("definition-configuration")
            if item.get("secret") is True:
                declared_secret.add(key)
            elif item.get("secret") is False:
                declared_config.add(key)
            else:
                _fail("definition-configuration")
    if not set(present_config) <= declared_config or not set(present_secret) <= declared_secret:
        _fail("configuration-keys-not-declared")
    return set(present_config), set(present_secret), reference


def _definition_entry(
    definition: Mapping[str, Any],
    *,
    operation: str,
    provider_bindings: Mapping[str, str],
    selected_ids: set[str],
    present_config: set[str],
    present_secret: set[str],
    secret_reference: str | None,
) -> dict[str, Any]:
    service_id = _service_id(definition.get("id"))
    if operation not in _ENSURE_ACTIONS:
        _fail("unsupported-lockfile-operation")
    configuration_contract = definition.get("configuration")
    if type(configuration_contract) is not list:
        _fail("definition-configuration")
    config_keys: set[str] = set()
    secret_keys: set[str] = set()
    for item in configuration_contract:
        if not isinstance(item, Mapping):
            _fail("definition-configuration")
        key = item.get("key")
        if not isinstance(key, str) or _CONFIG_KEY_RE.fullmatch(key) is None:
            _fail("definition-configuration")
        if item.get("secret") is True:
            secret_keys.add(key)
        elif item.get("secret") is False:
            config_keys.add(key)
        else:
            _fail("definition-configuration")
    schema_material = {
        "schema": "ods.extensions.configuration-contract.v1",
        "serviceId": service_id,
        "configuration": copy.deepcopy(configuration_contract),
    }
    schema_hash = hashlib.sha256(canonical_lockfile_bytes(schema_material)).hexdigest()

    edges: list[dict[str, str]] = []
    depends_on = definition.get("dependsOn")
    if type(depends_on) is not list:
        _fail("definition-dependencies")
    for target in depends_on:
        target = _service_id(target, "invalid-dependency-target")
        if target not in selected_ids:
            _fail("dependency-target-not-selected")
        edges.append({"kind": "service", "target": target})
    requires = definition.get("requires")
    if type(requires) is not list:
        _fail("definition-requirements")
    for capability in requires:
        if (
            not isinstance(capability, str)
            or _CAPABILITY_RE.fullmatch(capability) is None
        ):
            _fail("invalid-capability")
        target = provider_bindings.get(capability)
        if target not in selected_ids:
            _fail("missing-provider-binding")
        edges.append(
            {"kind": "capability", "capability": capability, "target": target}
        )
    edges.sort(key=_edge_sort_key)

    artifacts = definition.get("artifacts")
    if not isinstance(artifacts, Mapping) or type(artifacts.get("images")) is not list:
        _fail("definition-artifacts")
    image_digests: list[str] = []
    for item in artifacts["images"]:
        if not isinstance(item, Mapping):
            _fail("definition-artifacts")
        image_digests.append(_digest(item.get("digest"), "invalid-image-digest"))

    compatibility = definition.get("odsCompatibility")
    if not isinstance(compatibility, Mapping):
        _fail("definition-compatibility")
    result = {
        "id": service_id,
        "desiredState": "enabled",
        "version": definition.get("version"),
        "manifestSchemaVersion": definition.get("manifestSchemaVersion"),
        "dataSchemaVersion": definition.get("dataSchemaVersion"),
        "odsCompatibility": {
            "minimum": compatibility.get("minimum"),
            "maximum": compatibility.get("maximum"),
        },
        "definitionHashes": {
            "manifestSha256": definition.get("definitionSha256"),
            "composeSha256": definition.get("composeSha256"),
        },
        "imageDigests": sorted(set(image_digests)),
        "dependencyEdges": edges,
        "configuration": {
            "schemaSha256": schema_hash,
            "presentConfigKeys": sorted(present_config & config_keys),
            "presentSecretKeys": sorted(present_secret & secret_keys),
            "secretReference": (
                secret_reference if present_secret & secret_keys else None
            ),
        },
    }
    _validate_extension(result)
    return result


def build_lockfile(
    *,
    transaction: Mapping[str, Any],
    observed_state: Mapping[str, Any],
    runtime_mode: str,
    backup_reference: str | None,
    prior_lockfile_hash: str | None = None,
    previous_lockfile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a lockfile from one exact committed transaction and host snapshot.

    Unselected entries from a validated previous lockfile are retained. This
    prevents an on-demand transaction from silently dropping desired state for
    extensions that were installed by earlier transactions.
    """

    if not isinstance(transaction, Mapping):
        _fail("invalid-transaction")
    if transaction.get("state") != "committed":
        _fail("transaction-not-committed")
    transaction_id = transaction.get("transactionId")
    if (
        not isinstance(transaction_id, str)
        or _TRANSACTION_RE.fullmatch(transaction_id) is None
    ):
        _fail("invalid-transaction-id")
    sequence = transaction.get("sequence")
    if type(sequence) is not int or sequence < 0:
        _fail("invalid-transaction-sequence")
    envelope = transaction.get("envelope")
    if not isinstance(envelope, Mapping):
        _fail("invalid-plan-envelope")
    plan = envelope.get("plan")
    if not isinstance(plan, Mapping):
        _fail("invalid-plan")
    if plan.get("schema") != "ods.assistant-first.plan.v1":
        _fail("invalid-plan-schema")
    if plan.get("requestedAction") != "ensure":
        _fail("unsupported-requested-action")
    plan_hash = envelope.get("planHash")
    _hash(plan_hash, "invalid-plan-hash")
    actual_plan_hash = hashlib.sha256(canonical_lockfile_bytes(plan)).hexdigest()
    if actual_plan_hash != plan_hash:
        _fail("plan-hash-mismatch")
    catalog_revision = envelope.get("catalogRevision")
    _hash(catalog_revision, "invalid-catalog-revision")
    if plan.get("catalogRevision") != catalog_revision:
        _fail("catalog-revision-mismatch")
    if plan.get("observedStateRevision") != envelope.get("observedStateRevision"):
        _fail("plan-state-revision-mismatch")

    selected = plan.get("selectedServices")
    if type(selected) is not list:
        _fail("invalid-selected-services")
    selected_ids = [_service_id(item) for item in selected]
    if len(selected_ids) != len(set(selected_ids)):
        _fail("duplicate-selected-service")
    selected_set = set(selected_ids)
    definitions_value = plan.get("definitions")
    if type(definitions_value) is not list:
        _fail("invalid-definitions")
    definitions: dict[str, Mapping[str, Any]] = {}
    for definition in definitions_value:
        if not isinstance(definition, Mapping):
            _fail("invalid-definition")
        service_id = _service_id(definition.get("id"))
        if service_id in definitions:
            _fail("duplicate-definition")
        definitions[service_id] = definition
    if set(definitions) != selected_set:
        _fail("selected-definition-mismatch")

    operations_value = plan.get("operations")
    if type(operations_value) is not list:
        _fail("invalid-operations")
    operations: dict[str, str] = {}
    operation_order: list[str] = []
    for operation in operations_value:
        if not isinstance(operation, Mapping) or set(operation) != {
            "serviceId",
            "action",
        }:
            _fail("invalid-operation")
        service_id = _service_id(operation.get("serviceId"))
        action = operation.get("action")
        if service_id in operations or action not in _ENSURE_ACTIONS:
            _fail("invalid-operation")
        operations[service_id] = action
        operation_order.append(service_id)
    if operation_order != selected_ids:
        _fail("operation-order-mismatch")

    binding_values = plan.get("providerBindings")
    if type(binding_values) is not list:
        _fail("invalid-provider-bindings")
    provider_bindings: dict[str, str] = {}
    for binding in binding_values:
        if not isinstance(binding, Mapping) or set(binding) != {
            "capability",
            "serviceId",
        }:
            _fail("invalid-provider-binding")
        capability = binding.get("capability")
        service_id = _service_id(binding.get("serviceId"))
        if (
            not isinstance(capability, str)
            or _CAPABILITY_RE.fullmatch(capability) is None
            or capability in provider_bindings
            or service_id not in selected_set
        ):
            _fail("invalid-provider-binding")
        provider_bindings[capability] = service_id

    present_config, present_secret, secret_reference = _configuration_record(
        transaction,
        plan_hash,
        selected_set,
        definitions,
    )
    current_entries = {
        service_id: _definition_entry(
            definitions[service_id],
            operation=operations[service_id],
            provider_bindings=provider_bindings,
            selected_ids=selected_set,
            present_config=present_config,
            present_secret=present_secret,
            secret_reference=secret_reference,
        )
        for service_id in selected_ids
    }

    prior_hash = prior_lockfile_hash
    entries: dict[str, dict[str, Any]] = {}
    if previous_lockfile is not None:
        previous = validate_lockfile_envelope(previous_lockfile)
        if prior_hash is not None and prior_hash != previous["lockfileHash"]:
            _fail("prior-lockfile-mismatch")
        prior_hash = previous["lockfileHash"]
        entries = {
            item["id"]: copy.deepcopy(item)
            for item in previous["lockfile"]["extensions"]
        }
    elif prior_hash is not None:
        _hash(prior_hash, "invalid-prior-lockfile-hash")
        _fail("previous-lockfile-required")
    entries.update(current_entries)

    if runtime_mode not in _RUNTIME_MODES:
        _fail("invalid-runtime-mode")
    try:
        normalized_state = normalize_host_state(observed_state)
    except PlanningError as exc:
        raise ExtensionLockfileError("invalid-observed-state") from exc
    installed = {
        item["id"]: item for item in normalized_state["installedServices"]
    }
    for service_id, entry in current_entries.items():
        observation = installed.get(service_id)
        if observation is None or (
            observation["status"] != "enabled"
            or observation["version"] != entry["version"]
            or observation["definitionSha256"]
            != entry["definitionHashes"]["manifestSha256"]
        ):
            _fail("post-commit-observation-mismatch")
    observed_revision = hashlib.sha256(
        canonical_lockfile_bytes(normalized_state)
    ).hexdigest()
    document = {
        "schema": LOCKFILE_SCHEMA,
        "odsVersion": normalized_state["odsVersion"],
        "catalogRevision": catalog_revision,
        "platform": normalized_state["platform"],
        "architecture": normalized_state["architecture"],
        "containerRuntime": normalized_state["containerRuntime"],
        "runtimeMode": runtime_mode,
        "postCommitObservedStateRevision": observed_revision,
        "extensions": [entries[key] for key in sorted(entries)],
        "lastCommittedTransaction": {
            "transactionId": transaction_id,
            "planHash": plan_hash,
            "sequence": sequence,
            "plannedObservedStateRevision": envelope["observedStateRevision"],
        },
        "priorLockfileHash": prior_hash,
        "backupReference": backup_reference,
    }
    return lockfile_envelope(document)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("lockfile-duplicate-key")
        result[key] = value
    return result


class ExtensionLockfileStore:
    """Owner-only, hash-chained, atomic storage for one extension lockfile."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.path = self.root / LOCKFILE_FILENAME
        self._lock_path = self.root / ".extensions-lockfile.lock"

    def _reject_symlink_components(self) -> None:
        candidates = [self.root, *self.root.parents]
        for candidate in reversed(candidates):
            if candidate == Path(candidate.anchor):
                continue
            try:
                component_stat = os.lstat(candidate)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise ExtensionLockfileError("lockfile-root-unavailable") from exc
            if stat.S_ISLNK(component_stat.st_mode):
                _fail("lockfile-root-symlink")

    def _prepare_root(self) -> None:
        if not self.root.is_absolute() or self.root == Path(self.root.anchor):
            _fail("lockfile-root-invalid")
        self._reject_symlink_components()
        created = False
        try:
            if not self.root.exists():
                self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
                created = True
        except OSError as exc:
            raise ExtensionLockfileError("lockfile-root-unavailable") from exc
        self._reject_symlink_components()
        try:
            root_stat = os.lstat(self.root)
        except OSError as exc:
            raise ExtensionLockfileError("lockfile-root-unavailable") from exc
        if not stat.S_ISDIR(root_stat.st_mode):
            _fail("lockfile-root-not-directory")
        if os.name == "posix":
            if created:
                os.chmod(self.root, 0o700)
                root_stat = os.lstat(self.root)
            if root_stat.st_uid != os.getuid():
                _fail("lockfile-root-owner")
            if root_stat.st_mode & 0o777 != 0o700:
                _fail("lockfile-root-mode")
        self._validate_lock_path()

    def _validate_lock_path(self) -> None:
        try:
            lock_stat = os.lstat(self._lock_path)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ExtensionLockfileError("lockfile-lock-stat-failed") from exc
        if stat.S_ISLNK(lock_stat.st_mode):
            _fail("lockfile-lock-symlink")
        if not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1:
            _fail("lockfile-lock-unsafe-file")
        if os.name == "posix":
            if lock_stat.st_uid != os.getuid():
                _fail("lockfile-lock-owner")
            if lock_stat.st_mode & 0o777 != 0o600:
                _fail("lockfile-lock-mode")

    def _read_bytes(self) -> bytes | None:
        try:
            path_stat = os.lstat(self.path)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise ExtensionLockfileError("lockfile-stat-failed") from exc
        if stat.S_ISLNK(path_stat.st_mode):
            _fail("lockfile-symlink")
        if not stat.S_ISREG(path_stat.st_mode) or path_stat.st_nlink != 1:
            _fail("lockfile-unsafe-file")
        if path_stat.st_size > _MAX_FILE_BYTES:
            _fail("lockfile-too-large")
        if os.name == "posix":
            if path_stat.st_uid != os.getuid():
                _fail("lockfile-owner")
            if path_stat.st_mode & 0o777 != 0o600:
                _fail("lockfile-mode")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(
            os, "O_NOFOLLOW", 0
        )
        try:
            descriptor = os.open(self.path, flags)
        except OSError as exc:
            raise ExtensionLockfileError("lockfile-open-failed") from exc
        try:
            descriptor_stat = os.fstat(descriptor)
            if not stat.S_ISREG(descriptor_stat.st_mode) or descriptor_stat.st_nlink != 1:
                _fail("lockfile-unsafe-file")
            if os.name == "posix" and (
                descriptor_stat.st_dev,
                descriptor_stat.st_ino,
            ) != (path_stat.st_dev, path_stat.st_ino):
                _fail("lockfile-replaced")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, 65_536)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_FILE_BYTES:
                    _fail("lockfile-too-large")
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(descriptor)

    def _read_unlocked(self) -> dict[str, Any] | None:
        raw = self._read_bytes()
        if raw is None:
            return None
        try:
            decoded = json.loads(
                raw.decode("utf-8", errors="strict"),
                object_pairs_hook=_reject_duplicate_keys,
            )
        except ExtensionLockfileError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise ExtensionLockfileError("lockfile-parse-error") from exc
        envelope = validate_lockfile_envelope(decoded)
        if raw != canonical_lockfile_bytes(envelope):
            _fail("lockfile-noncanonical")
        return envelope

    def read(self) -> dict[str, Any] | None:
        self._prepare_root()
        try:
            with exclusive_file_lock(self._lock_path):
                self._validate_lock_path()
                return self._read_unlocked()
        except ServiceLockError as exc:
            raise ExtensionLockfileError("lockfile-lock-failed") from exc

    def _atomic_write(self, payload: bytes) -> None:
        descriptor = -1
        temporary_path: Path | None = None
        replaced = False
        try:
            descriptor, raw_path = tempfile.mkstemp(
                prefix=".extensions.lock.", suffix=".tmp", dir=self.root
            )
            temporary_path = Path(raw_path)
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            written = 0
            while written < len(payload):
                count = os.write(descriptor, payload[written:])
                if count <= 0:
                    _fail("lockfile-write-failed")
                written += count
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary_path, self.path)
            replaced = True
            temporary_path = None
            self._sync_root_directory()
        except ExtensionLockfileError:
            raise
        except OSError as exc:
            code = (
                "lockfile-durability-uncertain"
                if replaced
                else "lockfile-write-failed"
            )
            raise ExtensionLockfileError(code) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _sync_root_directory(self) -> None:
        if os.name != "posix":
            return
        try:
            directory_descriptor = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError as exc:
            raise ExtensionLockfileError("lockfile-durability-uncertain") from exc

    def confirm_durable(self, document: Mapping[str, Any]) -> dict[str, Any]:
        """Re-fsync and verify one exact active lockfile before receipting it."""

        candidate = lockfile_envelope(document)
        self._prepare_root()
        try:
            with exclusive_file_lock(self._lock_path):
                self._validate_lock_path()
                if self._read_unlocked() != candidate:
                    _fail("lockfile-durability-mismatch")
                self._sync_root_directory()
                if self._read_unlocked() != candidate:
                    _fail("lockfile-durability-mismatch")
                return copy.deepcopy(candidate)
        except ServiceLockError as exc:
            raise ExtensionLockfileError("lockfile-lock-failed") from exc

    def commit(self, document: Mapping[str, Any]) -> dict[str, Any]:
        candidate = lockfile_envelope(document)
        self._prepare_root()
        try:
            with exclusive_file_lock(self._lock_path):
                self._validate_lock_path()
                current = self._read_unlocked()
                if current == candidate:
                    return copy.deepcopy(candidate)
                current_hash = None if current is None else current["lockfileHash"]
                if candidate["lockfile"]["priorLockfileHash"] != current_hash:
                    _fail("prior-lockfile-mismatch")
                if current is not None and (
                    candidate["lockfile"]["lastCommittedTransaction"]["transactionId"]
                    == current["lockfile"]["lastCommittedTransaction"]["transactionId"]
                ):
                    _fail("transaction-already-recorded")
                self._atomic_write(canonical_lockfile_bytes(candidate))
                written = self._read_unlocked()
                if written != candidate:
                    _fail("lockfile-write-verify-failed")
                return copy.deepcopy(candidate)
        except ServiceLockError as exc:
            raise ExtensionLockfileError("lockfile-lock-failed") from exc


__all__ = [
    "ExtensionLockfileError",
    "ExtensionLockfileStore",
    "LOCKFILE_ENVELOPE_SCHEMA",
    "LOCKFILE_FILENAME",
    "LOCKFILE_SCHEMA",
    "build_lockfile",
    "canonical_lockfile_bytes",
    "lockfile_envelope",
    "validate_lockfile_document",
    "validate_lockfile_envelope",
]
