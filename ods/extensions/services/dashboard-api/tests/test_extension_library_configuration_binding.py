from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest


BIN = Path(__file__).resolve().parents[4] / "bin"
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))

import assistant_first_secret_store as secret_store  # noqa: E402
import extension_library_configuration_binding as binding  # noqa: E402
import extension_configuration as dashboard_configuration  # noqa: E402
from extension_lifecycle_plan import (  # noqa: E402
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
    PlannedOperation,
)
from extension_lifecycle_work import (  # noqa: E402
    LifecycleWorkCommand,
    LifecycleWorkValidationError,
)


TXN = "txn-" + "1" * 24
PLAN = "2" * 64
REQUEST = "3" * 64
REFERENCE = "secret-v1-" + "4" * 48
CATALOG = BIN.parent / "config" / "extensions-catalog.json"


def _canonical(value) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()


def _field(
    key: str,
    *,
    kind: str = "string",
    required: bool = False,
    secret: bool = False,
    source: str = "user",
    default=None,
    validation=None,
):
    value = {
        "key": key,
        "type": kind,
        "required": required,
        "secret": secret,
        "source": source,
        "restartBehavior": "service",
    }
    if validation is not None:
        value["validation"] = validation
    if default is not None:
        value["default"] = default
    return value


def _definition(service_id: str, fields: list[dict], *, source="library"):
    document = {"id": service_id, "configuration": fields}
    raw = _canonical(document)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    return PlannedDefinition(
        service_id=service_id,
        service_type="docker",
        manifest_schema_version="ods.services.v2",
        version="1.0.0",
        data_schema_version="1",
        definition_sha256=digest,
        compose_sha256="sha256:" + "5" * 64,
        definition_source=source,
        compose_file="compose.yaml",
        images=(),
        builds=(),
        canonical_document=raw,
        source_tree_sha256="sha256:" + "6" * 64,
    )


def _command(definitions, *, target=0, state="applying"):
    operations = tuple(
        PlannedOperation(item.service_id, "install") for item in definitions
    )
    service_id = definitions[target].service_id
    material = LifecyclePlanMaterial(
        schema=PLAN_MATERIAL_SCHEMA,
        transaction_id=TXN,
        plan_hash=PLAN,
        state=state,
        operations=operations,
        definitions=tuple(definitions),
        attested_approval=True,
    )
    return LifecycleWorkCommand(
        transaction_id=TXN,
        plan_hash=PLAN,
        operation_key=f"apply:{service_id}",
        request_hash=REQUEST,
        service_ids=(service_id,),
        payload={"operation": {"serviceId": service_id, "action": "install"}},
        timeout_seconds=180,
        plan_material=material,
    )


def _schema_hash(definitions) -> str:
    contracts = {}
    for definition in definitions:
        document = json.loads(definition.canonical_document)
        for field in document["configuration"]:
            normalized = binding._normalize_contract(field)
            contracts[normalized["key"]] = normalized
    return hashlib.sha256(
        _canonical(
            {
                "schema": binding.CONFIGURATION_SCHEMA,
                "fields": [contracts[key] for key in sorted(contracts)],
            }
        )
    ).hexdigest()


def _record(
    definitions,
    *,
    values=None,
    secret_keys=(),
    default_keys=(),
    reference=None,
):
    values = values or {}
    return {
        "actor": "owner",
        "appliedDefaultKeys": list(default_keys),
        "configuredAt": "2026-09-16T00:00:00Z",
        "idempotencyKey": "7" * 64,
        "planHash": PLAN,
        "presentConfigKeys": sorted(values),
        "presentSecretKeys": list(secret_keys),
        "schema": binding.TRANSACTION_CONFIGURATION_SCHEMA,
        "schemaHash": _schema_hash(definitions),
        "secretReference": reference,
        "transactionId": TXN,
        "values": values,
    }


def _transaction(definitions, record):
    schema_hash = _schema_hash(definitions)
    if record is None:
        configured = False
        values = {}
        config_keys = ()
        secret_keys = ()
        default_keys = ()
    else:
        configured = True
        values = record["values"]
        config_keys = tuple(record["presentConfigKeys"])
        secret_keys = tuple(record["presentSecretKeys"])
        default_keys = tuple(record["appliedDefaultKeys"])
    approval = {
        "transactionId": TXN,
        "planHash": PLAN,
        "configurationSchemaHash": schema_hash,
        "configurationHash": binding._configuration_attestation(
            TXN,
            PLAN,
            schema_hash,
            configured,
            values,
            config_keys,
            secret_keys,
            default_keys,
        ),
        "privateConfigurationDigest": binding._private_digest(
            record, schema_hash
        ),
    }
    return {
        "transactionId": TXN,
        "state": "applying",
        "envelope": {"planHash": PLAN},
        "approval": approval,
        "configuration": record,
    }


def _status(record, **changes):
    value = {
        "schema": secret_store.STATUS_SCHEMA,
        "transactionId": TXN,
        "planHash": PLAN,
        "schemaHash": record["schemaHash"],
        "reference": record["secretReference"],
        "configured": True,
        "presentSecretKeys": record["presentSecretKeys"],
    }
    value.update(changes)
    return value


def test_library_contract_normalization_matches_dashboard_for_real_v2_apps():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    entries = {item["id"]: item for item in catalog["extensions"]}
    for service_id in ("gitea", "miniflux", "ntfy", "ollama"):
        fields = entries[service_id]["planning"]["configuration"]
        assert [binding._normalize_contract(item) for item in fields] == [
            dashboard_configuration._normalize_contract(
                item, f"definition.{service_id}.configuration[{index}]"
            )
            for index, item in enumerate(fields)
        ]


def test_default_only_library_configuration_needs_no_secret_or_record():
    fields = [
        _field(
            "GITEA_PORT",
            kind="integer",
            default=7830,
            validation={"minimum": 1, "maximum": 65535},
        ),
        _field(
            "GITEA_HOST",
            default="localhost",
            validation={"minLength": 1, "maxLength": 253},
        ),
    ]
    definitions = [_definition("gitea", fields)]
    transaction = _transaction(definitions, None)
    calls = []

    result = binding.bind_library_configuration(
        _command(definitions),
        lambda _transaction_id: transaction,
        lambda request: calls.append(request),
    )

    assert result == binding.BoundLibraryConfiguration(
        transaction_id=TXN,
        plan_hash=PLAN,
        service_id="gitea",
        schema_hash=_schema_hash(definitions),
        values=(("GITEA_HOST", "localhost"), ("GITEA_PORT", 7830)),
        secret_keys=(),
        expected_secret_keys=(),
        secret_reference=None,
        configured=False,
    )
    assert calls == []


def test_secret_configuration_is_bound_without_reading_secret_values():
    fields = [
        _field(
            "MINIFLUX_PORT",
            kind="integer",
            default=8098,
            validation={"minimum": 1, "maximum": 65535},
        ),
        _field(
            "MINIFLUX_DB_PASSWORD",
            required=True,
            secret=True,
            validation={"minLength": 1, "maxLength": 1024},
        ),
        _field(
            "MINIFLUX_ADMIN_PASSWORD",
            required=True,
            secret=True,
            validation={"minLength": 1, "maxLength": 1024},
        ),
    ]
    definitions = [_definition("miniflux", fields)]
    record = _record(
        definitions,
        secret_keys=("MINIFLUX_ADMIN_PASSWORD", "MINIFLUX_DB_PASSWORD"),
        default_keys=("MINIFLUX_PORT",),
        reference=REFERENCE,
    )
    transaction = _transaction(definitions, record)
    calls = []

    result = binding.bind_library_configuration(
        _command(definitions),
        lambda _transaction_id: transaction,
        lambda request: calls.append(request) or _status(record),
    )

    assert result.values == (("MINIFLUX_PORT", 8098),)
    assert result.secret_keys == (
        "MINIFLUX_ADMIN_PASSWORD",
        "MINIFLUX_DB_PASSWORD",
    )
    assert result.expected_secret_keys == result.secret_keys
    assert result.secret_reference == REFERENCE
    assert calls == [
        {
            "schema": secret_store.STATUS_REQUEST_SCHEMA,
            "transactionId": TXN,
            "planHash": PLAN,
            "schemaHash": record["schemaHash"],
            "reference": REFERENCE,
        }
    ]
    assert "PASSWORD" in repr(result)
    assert "secret-value" not in repr(result)


def test_target_projection_keeps_full_secret_key_set_for_custody():
    alpha = _definition(
        "alpha",
        [
            _field("ALPHA_PORT", kind="integer", default=8101),
            _field("ALPHA_SECRET", required=True, secret=True),
        ],
    )
    beta = _definition(
        "beta",
        [
            _field("BETA_PORT", kind="integer", default=8102),
            _field("BETA_SECRET", required=True, secret=True),
        ],
    )
    definitions = [alpha, beta]
    record = _record(
        definitions,
        secret_keys=("ALPHA_SECRET", "BETA_SECRET"),
        default_keys=("ALPHA_PORT", "BETA_PORT"),
        reference=REFERENCE,
    )
    transaction = _transaction(definitions, record)

    result = binding.bind_library_configuration(
        _command(definitions, target=1),
        lambda _transaction_id: transaction,
        lambda _request: _status(record),
    )

    assert result.service_id == "beta"
    assert result.values == (("BETA_PORT", 8102),)
    assert result.secret_keys == ("BETA_SECRET",)
    assert result.expected_secret_keys == ("ALPHA_SECRET", "BETA_SECRET")


@pytest.mark.parametrize(
    "change",
    ["value", "defaults", "schema", "private-digest", "public-hash"],
)
def test_tampered_record_or_approval_is_rejected_before_secret_status(change):
    definitions = [
        _definition(
            "ntfy",
            [_field("NTFY_PORT", kind="integer", default=8097)],
        )
    ]
    record = _record(
        definitions,
        values={"NTFY_PORT": 9000},
        default_keys=(),
    )
    transaction = _transaction(definitions, record)
    if change == "value":
        transaction["configuration"]["values"]["NTFY_PORT"] = 9001
    elif change == "defaults":
        transaction["configuration"]["appliedDefaultKeys"] = ["NTFY_PORT"]
    elif change == "schema":
        transaction["configuration"]["schemaHash"] = "8" * 64
    elif change == "private-digest":
        transaction["approval"]["privateConfigurationDigest"] = "8" * 64
    else:
        transaction["approval"]["configurationHash"] = "8" * 64
    calls = []

    with pytest.raises(LifecycleWorkValidationError):
        binding.bind_library_configuration(
            _command(definitions),
            lambda _transaction_id: transaction,
            lambda request: calls.append(request),
        )
    assert calls == []


def test_secret_status_must_match_complete_approved_presence():
    definitions = [
        _definition(
            "demo",
            [_field("DEMO_SECRET", required=True, secret=True)],
        )
    ]
    record = _record(
        definitions,
        secret_keys=("DEMO_SECRET",),
        reference=REFERENCE,
    )
    transaction = _transaction(definitions, record)

    with pytest.raises(
        binding.LibraryConfigurationBindingError,
        match="library-configuration-secret-mismatch",
    ):
        binding.bind_library_configuration(
            _command(definitions),
            lambda _transaction_id: transaction,
            lambda _request: _status(record, presentSecretKeys=[]),
        )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda command: replace(command, operation_key="apply:other"),
        lambda command: replace(command, plan_material=replace(command.plan_material, attested_approval=False)),
        lambda command: replace(command, plan_material=replace(command.plan_material, state="configuring")),
        lambda command: replace(command, payload={"operation": {"serviceId": "demo", "action": "noop"}}),
    ],
)
def test_unbound_or_wrong_state_commands_are_refused(mutate):
    definitions = [_definition("demo", [])]
    command = mutate(_command(definitions))
    calls = []
    with pytest.raises(LifecycleWorkValidationError):
        binding.bind_library_configuration(
            command,
            lambda _transaction_id: calls.append("transaction"),
            lambda _request: calls.append("secret"),
        )
    assert calls == []


def test_required_configuration_cannot_use_an_empty_approval():
    definitions = [
        _definition(
            "demo",
            [_field("DEMO_REQUIRED", required=True, validation={"minLength": 1})],
        )
    ]
    transaction = _transaction(definitions, None)
    with pytest.raises(
        LifecycleWorkValidationError,
        match="library-configuration-record-mismatch",
    ):
        binding.bind_library_configuration(
            _command(definitions),
            lambda _transaction_id: transaction,
            None,
        )
