"""Exact owner-approved plan binding for host lifecycle work."""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))
DASH_API_DIR = Path(__file__).resolve().parents[1]
if str(DASH_API_DIR) not in sys.path:
    sys.path.insert(0, str(DASH_API_DIR))

import extension_lifecycle_plan as lifecycle_plan  # noqa: E402
import extension_lifecycle_work as lifecycle_work  # noqa: E402
import extension_data_scope_contract as data_scope  # noqa: E402
import extension_transactions as transaction_store  # noqa: E402


def claims(ports: list | None = None, exclusive: list | None = None) -> dict:
    return {
        "hostPorts": [] if ports is None else ports,
        "exclusive": [] if exclusive is None else exclusive,
    }


def port_entry(port: int, protocol: str = "tcp") -> dict:
    return {"port": port, "protocol": protocol}


TRANSACTION_ID = "txn-" + "1" * 24
PLAN_HASH = "2" * 64


def definition(service_id: str) -> dict:
    return {
        "id": service_id,
        "serviceType": "docker",
        "manifestSchemaVersion": "ods.services.v2",
        "version": "1.2.3",
        "dataSchemaVersion": "1",
        "odsCompatibility": {"minimum": "2.0.0", "maximum": "3.0.0"},
        "definitionSha256": "sha256:" + "3" * 64,
        "composeSha256": "sha256:" + "4" * 64,
        "definitionSource": "library",
        "composeFile": "compose.yaml",
        "dependsOn": [],
        "provides": [],
        "requires": [],
        "conflicts": [],
        "requirements": {},
        "estimates": {},
        "configuration": [],
        "artifacts": {
            "images": [
                {
                    "reference": f"example.invalid/{service_id}:1.2.3",
                    "digest": "sha256:" + "5" * 64,
                    "downloadBytes": 123,
                }
            ],
            "builds": [],
        },
        "resources": {},
        "lifecycle": {},
        "data": [],
        "trust": {},
        "support": {},
    }


def transaction(state: str) -> dict:
    operations = [
        {"serviceId": "documents", "action": "install"},
        {"serviceId": "voice", "action": "noop"},
    ]
    documents = definition("documents")
    documents["resources"] = claims(
        [port_entry(8080), port_entry(8081, "udp")], ["gpu:0"]
    )
    return {
        "transactionId": TRANSACTION_ID,
        "state": state,
        "approval": {
            "transactionId": TRANSACTION_ID,
            "planHash": PLAN_HASH,
            "approvedBy": "owner",
        },
        "envelope": {
            "planHash": PLAN_HASH,
            "plan": {
                "selectedServices": ["documents", "voice"],
                "operations": operations,
                "definitions": [documents, definition("voice")],
            },
        },
    }


def attested_transaction(state: str) -> dict:
    stored = transaction(state)
    stored["approval"] = {
        "actor": "owner-42",
        "approvedAt": "2026-09-11T12:01:00Z",
        "approvedBy": "owner-" + "a" * 16,
        "catalogRevision": "a" * 64,
        "configurationHash": "b" * 64,
        "configurationSchemaHash": "c" * 64,
        "idempotencyKey": "d" * 64,
        "observedStateRevision": "e" * 64,
        "planHash": PLAN_HASH,
        "policyRevision": "f" * 64,
        "privateConfigurationDigest": "1" * 64,
        "transactionId": TRANSACTION_ID,
        "validUntil": "2026-10-01T00:00:00Z",
    }
    return stored


def test_host_plan_binding_strict_gate_requires_exact_attested_approval():
    parsed = command(
        "download-and-verify", ["documents"], {"operations": [INSTALL]}
    )
    legacy = lifecycle_plan.bind_lifecycle_plan(
        parsed, transaction("downloading")
    )
    assert legacy.plan_material.attested_approval is False
    with pytest.raises(lifecycle_work.LifecycleWorkValidationError) as caught:
        lifecycle_plan.bind_lifecycle_plan(
            parsed, transaction("downloading"),
            require_attested_approval=True,
        )
    assert caught.value.code == "lifecycle-work-plan-mismatch"

    bound = lifecycle_plan.bind_lifecycle_plan(
        parsed, attested_transaction("downloading"),
        require_attested_approval=True,
    )
    assert bound.plan_material.attested_approval is True
    assert bound.plan_material.plan_hash == PLAN_HASH


def test_host_attestation_key_set_tracks_the_durable_store():
    assert lifecycle_plan._V2_APPROVAL_KEYS == (
        transaction_store.APPROVAL_V2_KEYS
    )


@pytest.mark.parametrize("invalid_gate", [None, 0, 1, "true"])
def test_host_plan_binding_requires_a_boolean_gate(invalid_gate):
    parsed = command(
        "download-and-verify", ["documents"], {"operations": [INSTALL]}
    )
    with pytest.raises(lifecycle_work.LifecycleWorkValidationError) as caught:
        lifecycle_plan.bind_lifecycle_plan(
            parsed, attested_transaction("downloading"),
            require_attested_approval=invalid_gate,
        )
    assert caught.value.code == "lifecycle-work-plan-mismatch"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda record: record.pop("privateConfigurationDigest"),
        lambda record: record.update({"extra": "assistant-supplied"}),
        lambda record: record.update({"configurationHash": "B" * 64}),
        lambda record: record.update({"configurationSchemaHash": "not-a-hash"}),
    ],
)
def test_host_plan_binding_rejects_malformed_attestation(mutation):
    stored = attested_transaction("downloading")
    mutation(stored["approval"])
    parsed = command(
        "download-and-verify", ["documents"], {"operations": [INSTALL]}
    )
    with pytest.raises(lifecycle_work.LifecycleWorkValidationError) as caught:
        lifecycle_plan.bind_lifecycle_plan(
            parsed, stored, require_attested_approval=True,
        )
    assert caught.value.code == "lifecycle-work-plan-mismatch"
    assert lifecycle_plan.bind_lifecycle_plan(
        parsed, stored
    ).plan_material.attested_approval is False


def command(operation_key: str, service_ids: list[str], payload: dict):
    unsigned = {
        "schema": lifecycle_work.REQUEST_SCHEMA,
        "transactionId": TRANSACTION_ID,
        "planHash": PLAN_HASH,
        "operationKey": operation_key,
        "serviceIds": service_ids,
        "payload": payload,
    }
    request = {
        **unsigned,
        "requestHash": hashlib.sha256(
            json.dumps(
                unsigned,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest(),
    }
    return lifecycle_work.parse_lifecycle_work_request(request)


INSTALL = {"serviceId": "documents", "action": "install"}


@pytest.mark.parametrize(
    ("state", "operation_key", "service_ids", "payload"),
    [
        ("reserved", "reserve:documents", ["documents"], {"operation": INSTALL}),
        (
            "downloading",
            "download-and-verify",
            ["documents"],
            {"operations": [INSTALL]},
        ),
        ("staged", "stage", ["documents"], {"operations": [INSTALL]}),
        ("configuring", "backup", ["documents"], {"serviceIds": ["documents"]}),
        (
            "configuring",
            "configure",
            ["documents"],
            {"serviceIds": ["documents"]},
        ),
        ("applying", "apply:documents", ["documents"], {"operation": INSTALL}),
        (
            "verifying",
            "verify",
            ["documents", "voice"],
            {"serviceIds": ["documents", "voice"]},
        ),
        (
            "reconciling",
            "compensate:documents",
            ["documents"],
            {"operation": INSTALL},
        ),
        (
            "reconciling",
            "restore",
            ["documents"],
            {"serviceIds": ["documents"]},
        ),
        (
            "verifying",
            "release",
            ["documents"],
            {"serviceIds": ["documents"]},
        ),
        (
            "reconciling",
            "release",
            ["documents"],
            {"serviceIds": ["documents"]},
        ),
    ],
)
def test_bind_accepts_only_the_exact_operation_for_the_current_phase(
    state, operation_key, service_ids, payload
):
    parsed = command(operation_key, service_ids, payload)

    bound = lifecycle_plan.bind_lifecycle_plan(parsed, transaction(state))

    assert bound is not parsed
    assert bound.request_hash == parsed.request_hash
    assert bound.plan_material.schema == lifecycle_plan.PLAN_MATERIAL_SCHEMA
    assert bound.plan_material.transaction_id == TRANSACTION_ID
    assert bound.plan_material.plan_hash == PLAN_HASH
    assert bound.plan_material.state == state
    assert [item.service_id for item in bound.plan_material.operations] == [
        "documents",
        "voice",
    ]
    assert [item.service_id for item in bound.plan_material.definitions] == [
        "documents",
        "voice",
    ]
    assert bound.plan_material.definitions[0].images[0].digest == (
        "sha256:" + "5" * 64
    )
    assert bound.plan_material.definitions[0].definition_source == "library"
    assert bound.plan_material.definitions[0].compose_file == "compose.yaml"
    assert bound.plan_material.definitions[0].host_ports == (
        lifecycle_plan.PlannedHostPort(protocol="tcp", port=8080),
        lifecycle_plan.PlannedHostPort(protocol="udp", port=8081),
    )
    assert bound.plan_material.definitions[0].exclusive == ("gpu:0",)
    assert bound.plan_material.definitions[1].host_ports is None
    assert bound.plan_material.definitions[1].exclusive is None


def test_bind_accepts_empty_present_reservation_claims():
    stored = transaction("applying")
    stored["envelope"]["plan"]["definitions"][0]["resources"] = claims()

    bound = lifecycle_plan.bind_lifecycle_plan(
        command("apply:documents", ["documents"], {"operation": INSTALL}),
        stored,
    )

    material = bound.plan_material.definitions[0]
    assert material.host_ports == ()
    assert material.exclusive == ()


def test_host_binding_preserves_approved_prior_v2_data_scope_and_rejects_drift():
    stored = transaction("downloading")
    stored["envelope"]["plan"]["operations"][0]["action"] = "update"
    stored["envelope"]["plan"]["priorDataBindings"] = [{
        "serviceId": "documents", "manifestSchemaVersion": "ods.services.v2",
        "version": "1.1.0", "dataSchemaVersion": "1",
        "definitionSha256": "sha256:" + "a" * 64,
        "paths": [{
            "path": "data/documents-prior", "backupClass": "required", "owner": "user",
            "uninstall": "preserve", "purge": "separate-approval",
        }],
    }]
    parsed = command("download-and-verify", ["documents"], {
        "operations": [{"serviceId": "documents", "action": "update"}]
    })
    bound = lifecycle_plan.bind_lifecycle_plan(parsed, stored)
    prior = bound.plan_material.prior_data_bindings[0]
    assert prior.service_id == "documents"
    assert prior.definition_sha256 == "sha256:" + "a" * 64
    assert prior.paths[0].path == "data/documents-prior"
    for changed_path in ("../private", "data/documents-prior/../private", "data//documents", "data/./documents"):
        altered = transaction("downloading")
        altered["envelope"]["plan"] = json.loads(json.dumps(stored["envelope"]["plan"]))
        altered["envelope"]["plan"]["priorDataBindings"][0]["paths"][0]["path"] = changed_path
        with pytest.raises(lifecycle_work.LifecycleWorkValidationError) as caught:
            lifecycle_plan.bind_lifecycle_plan(parsed, altered)
        assert caught.value.code == "lifecycle-work-plan-mismatch"
    altered = transaction("downloading")
    altered["envelope"]["plan"] = json.loads(json.dumps(stored["envelope"]["plan"]))
    altered["envelope"]["plan"]["priorDataBindings"][0]["paths"][0]["owner"] = ["user"]
    with pytest.raises(lifecycle_work.LifecycleWorkValidationError) as caught:
        lifecycle_plan.bind_lifecycle_plan(parsed, altered)
    assert caught.value.code == "lifecycle-work-plan-mismatch"


def test_attested_host_plan_material_feeds_only_its_old_new_union_to_generic_scope():
    stored = attested_transaction("configuring")
    plan = stored["envelope"]["plan"]
    plan["operations"][0]["action"] = "update"
    plan["definitions"][0]["data"] = [{
        "path": "data/documents-new", "backupClass": "required", "owner": "user",
        "uninstall": "preserve", "purge": "separate-approval",
    }]
    plan["priorDataBindings"] = [{
        "serviceId": "documents", "manifestSchemaVersion": "ods.services.v2",
        "version": "1.1.0", "dataSchemaVersion": "1",
        "definitionSha256": "sha256:" + "a" * 64,
        "paths": [{
            "path": "data/documents-prior", "backupClass": "required", "owner": "user",
            "uninstall": "preserve", "purge": "separate-approval",
        }],
    }]
    parsed = command("backup", ["documents"], {"serviceIds": ["documents"]})
    approved = lifecycle_plan.bind_lifecycle_plan(
        parsed, stored, require_attested_approval=True
    )
    bound = data_scope.bind_data_scope(approved)
    assert [path.path for path in bound.services[0].paths] == [
        "data/documents-new", "data/documents-prior",
    ]
    assert bound.services[0].prior_definition_sha256 == "sha256:" + "a" * 64


def test_bind_accepts_reserve_for_present_empty_claims():
    stored = transaction("reserved")
    stored["envelope"]["plan"]["definitions"][0]["resources"] = claims()

    bound = lifecycle_plan.bind_lifecycle_plan(
        command("reserve:documents", ["documents"], {"operation": INSTALL}),
        stored,
    )

    assert [item.host_ports for item in bound.plan_material.definitions] == [(), None]


def test_bind_accepts_reserve_when_the_targeted_definition_has_claims():
    bound = lifecycle_plan.bind_lifecycle_plan(
        command("reserve:documents", ["documents"], {"operation": INSTALL}),
        transaction("reserved"),
    )

    material = bound.plan_material.definitions[0]
    assert material.host_ports == (
        lifecycle_plan.PlannedHostPort(protocol="tcp", port=8080),
        lifecycle_plan.PlannedHostPort(protocol="udp", port=8081),
    )
    assert material.exclusive == ("gpu:0",)


def test_bind_accepts_the_reservation_store_exclusive_token_grammar():
    stored = transaction("reserved")
    stored["envelope"]["plan"]["definitions"][0]["resources"] = claims(
        exclusive=["gpu/slot-0", "x" * 128]
    )

    bound = lifecycle_plan.bind_lifecycle_plan(
        command("reserve:documents", ["documents"], {"operation": INSTALL}),
        stored,
    )

    assert bound.plan_material.definitions[0].exclusive == (
        "gpu/slot-0",
        "x" * 128,
    )


def test_bind_rejects_reserve_when_only_the_targeted_definition_lacks_claims():
    stored = transaction("reserved")
    stored["envelope"]["plan"]["definitions"][0]["resources"] = {}

    with pytest.raises(lifecycle_work.LifecycleWorkValidationError) as raised:
        lifecycle_plan.bind_lifecycle_plan(
            command("reserve:documents", ["documents"], {"operation": INSTALL}),
            stored,
        )

    assert raised.value.code == "lifecycle-work-reservation-claims-missing"


def test_bind_rejects_reserve_when_all_definitions_lack_claims():
    stored = transaction("reserved")
    for entry in stored["envelope"]["plan"]["definitions"]:
        entry["resources"] = {}

    with pytest.raises(lifecycle_work.LifecycleWorkValidationError) as raised:
        lifecycle_plan.bind_lifecycle_plan(
            command("reserve:documents", ["documents"], {"operation": INSTALL}),
            stored,
        )

    assert raised.value.code == "lifecycle-work-reservation-claims-missing"


def test_bind_keeps_non_reserve_operations_readable_for_legacy_definitions():
    stored = transaction("downloading")
    for entry in stored["envelope"]["plan"]["definitions"]:
        entry["resources"] = {}

    bound = lifecycle_plan.bind_lifecycle_plan(
        command("download-and-verify", ["documents"], {"operations": [INSTALL]}),
        stored,
    )

    assert [item.host_ports for item in bound.plan_material.definitions] == [
        None,
        None,
    ]
    assert [item.exclusive for item in bound.plan_material.definitions] == [
        None,
        None,
    ]


def test_bind_keeps_reserve_readable_when_only_unrelated_definitions_lack_claims():
    bound = lifecycle_plan.bind_lifecycle_plan(
        command("reserve:documents", ["documents"], {"operation": INSTALL}),
        transaction("reserved"),
    )

    legacy = bound.plan_material.definitions[1]
    assert legacy.service_id == "voice"
    assert legacy.host_ports is None
    assert legacy.exclusive is None


@pytest.mark.parametrize(
    "resource_mutate",
    [
        lambda resources: resources.update(exclusive=claims([port_entry(8080)])),
        lambda resources: resources.update(exclusive=claims(exclusive=["gpu:0"])),
        lambda resources: resources.update(
            claims([port_entry(8080), port_entry(8080)])
        ),
        lambda resources: resources.update(
            claims([port_entry(8081), port_entry(8080)])
        ),
        lambda resources: resources.update(claims([port_entry(65536)])),
        lambda resources: resources.update(claims([port_entry(True)])),
        lambda resources: resources.update(claims([port_entry("8080")])),
        lambda resources: resources.update(claims([port_entry(8080, "sctp")])),
        lambda resources: resources.update(
            claims([{"port": 8080, "protocol": "tcp", "owner": "x"}])
        ),
        lambda resources: resources.update(claims(exclusive=["GPU:0"])),
        lambda resources: resources.update(claims(exclusive=["gpu:0", "gpu:0"])),
        lambda resources: resources.update(claims(exclusive=[""])),
        lambda resources: resources.update(claims(exclusive=["-gpu"])),
        lambda resources: resources.update(claims(exclusive=["x" * 129])),
        lambda resources: resources.update(claims(exclusive=["gpu:0 "])),
    ],
)
def test_bind_rejects_invalid_reservation_claim_material(resource_mutate):
    parsed = command("apply:documents", ["documents"], {"operation": INSTALL})
    stored = transaction("applying")
    resource_mutate(stored["envelope"]["plan"]["definitions"][0]["resources"])

    with pytest.raises(lifecycle_work.LifecycleWorkValidationError) as raised:
        lifecycle_plan.bind_lifecycle_plan(parsed, stored)

    assert raised.value.code == "lifecycle-work-plan-mismatch"


def test_bind_rejects_non_mapping_reservation_resources():
    parsed = command("apply:documents", ["documents"], {"operation": INSTALL})
    stored = transaction("applying")
    stored["envelope"]["plan"]["definitions"][0]["resources"] = []

    with pytest.raises(lifecycle_work.LifecycleWorkValidationError) as raised:
        lifecycle_plan.bind_lifecycle_plan(parsed, stored)

    assert raised.value.code == "lifecycle-work-plan-mismatch"


@pytest.mark.parametrize(
    "partial_resources",
    [
        lambda: {"hostPorts": [port_entry(8080)]},
        lambda: {"exclusive": ["gpu:0"]},
        lambda: {
            "hostPorts": [port_entry(8080)],
            "containerPorts": (),
            "networks": (),
            "volumes": (),
            "devices": (),
        },
        lambda: {
            "exclusive": ["gpu:0"],
            "containerPorts": (),
            "networks": (),
            "volumes": (),
            "devices": (),
        },
    ],
    ids=[
        "hostports-without-exclusive",
        "exclusive-without-hostports",
        "hostports-without-exclusive-among-ordinary-keys",
        "exclusive-without-hostports-among-ordinary-keys",
    ],
)
def test_bind_rejects_partial_reservation_claims_with_plan_mismatch(
    partial_resources
):
    parsed = command("apply:documents", ["documents"], {"operation": INSTALL})
    stored = transaction("applying")
    stored["envelope"]["plan"]["definitions"][0]["resources"] = partial_resources()

    with pytest.raises(lifecycle_work.LifecycleWorkValidationError) as raised:
        lifecycle_plan.bind_lifecycle_plan(parsed, stored)

    assert raised.value.code == "lifecycle-work-plan-mismatch"


@pytest.mark.parametrize(
    "partial_resources",
    [
        lambda: {"hostPorts": [port_entry(8080)]},
        lambda: {"exclusive": ["gpu:0"]},
    ],
    ids=["hostports-without-exclusive", "exclusive-without-hostports"],
)
def test_bind_rejects_partial_reservation_claims_for_non_reserve_operations(
    partial_resources
):
    stored = transaction("downloading")
    for entry in stored["envelope"]["plan"]["definitions"]:
        entry["resources"] = partial_resources()

    with pytest.raises(lifecycle_work.LifecycleWorkValidationError) as raised:
        lifecycle_plan.bind_lifecycle_plan(
            command("download-and-verify", ["documents"], {"operations": [INSTALL]}),
            stored,
        )

    assert raised.value.code == "lifecycle-work-plan-mismatch"


@pytest.mark.parametrize(
    "partial_resources",
    [
        lambda: {"hostPorts": [port_entry(8080)]},
        lambda: {"exclusive": ["gpu:0"]},
        lambda: {
            "hostPorts": [port_entry(8080)],
            "containerPorts": (),
            "networks": (),
            "volumes": (),
            "devices": (),
        },
    ],
    ids=[
        "hostports-without-exclusive",
        "exclusive-without-hostports",
        "hostports-without-exclusive-among-ordinary-keys",
    ],
)
def test_bind_rejects_partial_reservation_claims_for_reserve(partial_resources):
    stored = transaction("reserved")
    stored["envelope"]["plan"]["definitions"][0]["resources"] = partial_resources()

    with pytest.raises(lifecycle_work.LifecycleWorkValidationError) as raised:
        lifecycle_plan.bind_lifecycle_plan(
            command("reserve:documents", ["documents"], {"operation": INSTALL}),
            stored,
        )

    assert raised.value.code == "lifecycle-work-plan-mismatch"


def test_bind_accepts_claim_material_once_the_missing_sibling_key_is_present():
    stored = transaction("applying")
    stored["envelope"]["plan"]["definitions"][0]["resources"] = {
        "hostPorts": [port_entry(8080)],
        "exclusive": ["gpu:0"],
        "containerPorts": (),
        "networks": (),
        "volumes": (),
        "devices": (),
    }

    bound = lifecycle_plan.bind_lifecycle_plan(
        command("apply:documents", ["documents"], {"operation": INSTALL}),
        stored,
    )

    material = bound.plan_material.definitions[0]
    assert material.host_ports == (
        lifecycle_plan.PlannedHostPort(protocol="tcp", port=8080),
    )
    assert material.exclusive == ("gpu:0",)


def test_reservation_claims_come_from_the_canonical_plan_document_only():
    stored = transaction("applying")

    bound = lifecycle_plan.bind_lifecycle_plan(
        command("apply:documents", ["documents"], {"operation": INSTALL}),
        stored,
    )
    before = bound.plan_material.definitions[0].host_ports
    stored["envelope"]["plan"]["definitions"][0]["resources"]["hostPorts"].append(
        port_entry(9999)
    )

    assert bound.plan_material.definitions[0].host_ports == before
    assert bound.plan_material.definitions[0].host_ports == (
        lifecycle_plan.PlannedHostPort(protocol="tcp", port=8080),
        lifecycle_plan.PlannedHostPort(protocol="udp", port=8081),
    )


def test_bind_copies_exact_definition_material_out_of_the_mutable_store_result():
    parsed = command("apply:documents", ["documents"], {"operation": INSTALL})
    stored = transaction("applying")

    bound = lifecycle_plan.bind_lifecycle_plan(parsed, stored)
    before = bound.plan_material.definitions[0].canonical_document
    stored["envelope"]["plan"]["definitions"][0]["version"] = "9.9.9"
    stored["envelope"]["plan"]["definitions"][0]["artifacts"]["images"][0][
        "digest"
    ] = "sha256:" + "9" * 64

    assert bound.plan_material.definitions[0].version == "1.2.3"
    assert bound.plan_material.definitions[0].images[0].digest == (
        "sha256:" + "5" * 64
    )
    assert bound.plan_material.definitions[0].canonical_document == before


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(transactionId="txn-" + "9" * 24),
        lambda value: value["approval"].update(planHash="9" * 64),
        lambda value: value.update(approval=None),
        lambda value: value["envelope"].update(planHash="9" * 64),
        lambda value: value["envelope"]["plan"].update(
            selectedServices=["voice", "documents"]
        ),
        lambda value: value["envelope"]["plan"]["definitions"].reverse(),
        lambda value: value["envelope"]["plan"]["definitions"][0].update(
            definitionSha256="not-a-digest"
        ),
        lambda value: value["envelope"]["plan"]["definitions"][0].update(
            definitionSource="unknown"
        ),
        lambda value: value["envelope"]["plan"]["definitions"][0].update(
            definitionSource=[]
        ),
        lambda value: value["envelope"]["plan"]["definitions"][0].update(
            definitionSource={}
        ),
        lambda value: value["envelope"]["plan"]["definitions"][0].update(
            composeFile="../compose.yaml"
        ),
        lambda value: value["envelope"]["plan"]["definitions"][0].update(
            composeFile=None
        ),
        lambda value: value["envelope"]["plan"]["definitions"][0].update(
            composeSha256=None
        ),
        lambda value: value["envelope"]["plan"]["definitions"][0][
            "artifacts"
        ].update(images=[{"reference": "image", "digest": "bad", "downloadBytes": 1}]),
    ],
)
def test_bind_rejects_transaction_approval_operation_or_definition_drift(mutate):
    parsed = command("apply:documents", ["documents"], {"operation": INSTALL})
    stored = transaction("applying")
    mutate(stored)

    with pytest.raises(lifecycle_work.LifecycleWorkValidationError) as raised:
        lifecycle_plan.bind_lifecycle_plan(parsed, stored)

    assert raised.value.code == "lifecycle-work-plan-mismatch"


def test_bind_rejects_wrong_phase_payload_or_service_order():
    cases = [
        (
            command("apply:documents", ["documents"], {"operation": INSTALL}),
            transaction("verifying"),
        ),
        (
            command(
                "apply:documents",
                ["documents"],
                {"operation": {"serviceId": "documents", "action": "repair"}},
            ),
            transaction("applying"),
        ),
        (
            command(
                "verify",
                ["voice", "documents"],
                {"serviceIds": ["voice", "documents"]},
            ),
            transaction("verifying"),
        ),
    ]
    for parsed, stored in cases:
        with pytest.raises(lifecycle_work.LifecycleWorkValidationError) as raised:
            lifecycle_plan.bind_lifecycle_plan(parsed, stored)
        assert raised.value.code == "lifecycle-work-plan-mismatch"


def test_bind_rejects_a_command_that_already_contains_plan_material():
    parsed = command("apply:documents", ["documents"], {"operation": INSTALL})
    first = lifecycle_plan.bind_lifecycle_plan(parsed, transaction("applying"))

    with pytest.raises(lifecycle_work.LifecycleWorkValidationError) as raised:
        lifecycle_plan.bind_lifecycle_plan(first, transaction("applying"))

    assert raised.value.code == "lifecycle-work-plan-mismatch"


def test_bind_preserves_legacy_plan_without_unhashed_origin_inference():
    stored = transaction("applying")
    legacy = stored["envelope"]["plan"]["definitions"][0]
    legacy.pop("definitionSource")
    legacy.pop("composeFile")

    bound = lifecycle_plan.bind_lifecycle_plan(
        command("apply:documents", ["documents"], {"operation": INSTALL}),
        stored,
    )

    material = bound.plan_material.definitions[0]
    assert material.definition_source is None
    assert material.compose_file is None


def test_plan_binding_is_stdlib_only_and_has_no_effect_primitives():
    module_path = BIN_DIR / "extension_lifecycle_plan.py"
    source = module_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = set()
    called_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called_names.add(node.func.id)

    assert imports <= {
        "__future__",
        "dataclasses",
        "extension_lifecycle_work",
        "json",
        "re",
        "typing",
    }
    assert called_names.isdisjoint(
        {"open", "exec", "eval", "compile", "system", "remove", "unlink"}
    )
    assert imports.isdisjoint(
        {"os", "pathlib", "requests", "shutil", "socket", "subprocess", "urllib"}
    )
