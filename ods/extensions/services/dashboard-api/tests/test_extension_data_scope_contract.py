"""Adversarial qualification of the generic old/new data scope boundary."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_data_scope_contract as scope  # noqa: E402
from extension_lifecycle_plan import (  # noqa: E402
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
    PlannedOperation,
    PlannedPriorDataBinding,
    PlannedPriorDataPath,
)
from extension_lifecycle_work import LifecycleWorkCommand, LifecycleWorkValidationError  # noqa: E402


TRANSACTION_ID = "txn-" + "1" * 24
PLAN_HASH = "a" * 64


def record(path: str) -> dict:
    return {
        "path": path, "backupClass": "required", "owner": "user",
        "uninstall": "preserve", "purge": "separate-approval",
    }


def definition(service_id: str, paths: list[str]) -> PlannedDefinition:
    digest = "sha256:" + "d" * 64
    document = {
        "id": service_id, "manifestSchemaVersion": "ods.services.v2",
        "definitionSha256": digest, "dataSchemaVersion": "2",
        "data": [record(path) for path in paths],
    }
    canonical = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    return PlannedDefinition(
        service_id=service_id, service_type="docker", manifest_schema_version="ods.services.v2",
        version="2.0.0", data_schema_version="2", definition_sha256=digest,
        compose_sha256="sha256:" + "e" * 64, definition_source="library",
        compose_file="compose.yaml", images=(), builds=(), canonical_document=canonical,
    )


def prior(service_id: str, paths: list[str]) -> PlannedPriorDataBinding:
    return PlannedPriorDataBinding(
        service_id=service_id, version="1.0.0", data_schema_version="1",
        definition_sha256="sha256:" + "c" * 64,
        paths=tuple(
            PlannedPriorDataPath(path, "required", "user", "preserve", "separate-approval")
            for path in paths
        ),
    )


def command(
    *, operation_key: str = "backup", actions: tuple[str, ...] = ("install", "update"),
    selected_paths: tuple[list[str], ...] = (["data/new"], ["data/newer"]),
    prior_paths: list[str] = ["data/old"], attested: bool = True,
) -> LifecycleWorkCommand:
    ids = ("alpha", "beta")
    operations = tuple(PlannedOperation(service_id, action) for service_id, action in zip(ids, actions))
    definitions = tuple(definition(service_id, paths) for service_id, paths in zip(ids, selected_paths))
    prior_bindings = (prior("beta", prior_paths),) if actions[1] != "install" else ()
    material = LifecyclePlanMaterial(
        schema=PLAN_MATERIAL_SCHEMA, transaction_id=TRANSACTION_ID, plan_hash=PLAN_HASH,
        state="configuring" if operation_key == "backup" else "reconciling",
        operations=operations, definitions=definitions, prior_data_bindings=prior_bindings,
        attested_approval=attested,
    )
    return LifecycleWorkCommand(
        transaction_id=TRANSACTION_ID, plan_hash=PLAN_HASH, operation_key=operation_key,
        request_hash="b" * 64, service_ids=ids, payload={"serviceIds": list(ids)},
        timeout_seconds=120, plan_material=material,
    )


def rejected(value: LifecycleWorkCommand) -> None:
    with pytest.raises(LifecycleWorkValidationError) as caught:
        scope.bind_data_scope(value)
    assert caught.value.code == "lifecycle-work-data-scope-mismatch"


@pytest.mark.parametrize("operation_key", ["backup", "restore"])
def test_scope_binds_old_and_new_paths_without_caller_supplied_scope(operation_key: str):
    bound = scope.bind_data_scope(command(operation_key=operation_key))
    assert bound.operation_key == operation_key
    assert [service.service_id for service in bound.services] == ["alpha", "beta"]
    assert [path.path for path in bound.services[0].paths] == ["data/new"]
    assert [(path.path, path.prior is not None, path.selected is not None)
            for path in bound.services[1].paths] == [
                ("data/newer", False, True), ("data/old", True, False),
            ]
    assert bound.services[1].prior_definition_sha256 == "sha256:" + "c" * 64


def test_update_without_actual_prior_v2_scope_is_denied():
    value = command()
    material = replace(value.plan_material, prior_data_bindings=())
    rejected(replace(value, plan_material=material))


def test_same_path_in_prior_and_selected_definition_is_one_bound_snapshot():
    bound = scope.bind_data_scope(command(
        selected_paths=(["data/new"], ["data/beta"]), prior_paths=["data/beta"]
    ))
    assert len(bound.services[1].paths) == 1
    path = bound.services[1].paths[0]
    assert path.path == "data/beta"
    assert path.prior is not None and path.selected is not None


@pytest.mark.parametrize("paths", [
    (["data/app"], ["data/app/sub"], ["data/old"]),
    (["data/shared"], ["data/shared"], ["data/old"]),
    (["data/new"], ["data/newer"], ["data/newer/sub"]),
    (["data/foo"], ["data/foo-bar"], ["data/foo/sub"]),
])
def test_overlapping_or_nested_paths_are_denied(paths):
    alpha, beta, old = paths
    rejected(command(selected_paths=(alpha, beta), prior_paths=old))


@pytest.mark.parametrize("unsafe", ["../private", "data//app", "data/./app", "data/app/"])
def test_unsafe_or_aliased_selected_data_path_is_denied(unsafe: str):
    rejected(command(selected_paths=([unsafe], ["data/newer"])))


def test_no_attested_approval_or_payload_scope_expansion_is_denied():
    rejected(command(attested=False))
    value = command()
    rejected(replace(value, payload={"serviceIds": ["alpha", "beta"], "paths": ["/private"]}))


def test_malformed_prior_record_type_is_rejected_without_type_error():
    value = command()
    malformed = replace(prior("beta", ["data/old"]), service_id=["beta"])
    material = replace(value.plan_material, prior_data_bindings=(malformed,))
    rejected(replace(value, plan_material=material))
    malformed_operation = replace(value.plan_material.operations[0], action=["install"])
    material = replace(value.plan_material, operations=(malformed_operation, value.plan_material.operations[1]))
    rejected(replace(value, plan_material=material))
