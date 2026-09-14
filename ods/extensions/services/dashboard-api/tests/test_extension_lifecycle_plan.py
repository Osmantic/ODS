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

import extension_lifecycle_plan as lifecycle_plan  # noqa: E402
import extension_lifecycle_work as lifecycle_work  # noqa: E402


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
                "definitions": [definition("documents"), definition("voice")],
            },
        },
    }


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
