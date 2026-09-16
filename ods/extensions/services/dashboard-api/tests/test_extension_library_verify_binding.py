"""Pure fail-closed selection for the read-only library verifier."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

from extension_library_verify_binding import bind_library_verify  # noqa: E402
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

TRANSACTION_ID = "txn-" + "1" * 24
PLAN_HASH = "2" * 64


def _definition(service_id: str) -> PlannedDefinition:
    return PlannedDefinition(
        service_id=service_id,
        service_type="docker",
        manifest_schema_version="ods.services.v2",
        version="1.0.0",
        data_schema_version="1",
        definition_sha256="sha256:" + "3" * 64,
        compose_sha256="sha256:" + "4" * 64,
        definition_source="library",
        compose_file="compose.yaml",
        images=(),
        builds=(),
        canonical_document=b"{}",
        source_tree_sha256="sha256:" + "5" * 64,
    )


def _command() -> LifecycleWorkCommand:
    operations = (
        PlannedOperation("ntfy", "noop"),
        PlannedOperation("gitea", "install"),
    )
    return LifecycleWorkCommand(
        transaction_id=TRANSACTION_ID,
        plan_hash=PLAN_HASH,
        operation_key="verify",
        request_hash="6" * 64,
        service_ids=("gitea", "ntfy"),
        payload={"serviceIds": ["gitea", "ntfy"]},
        timeout_seconds=600,
        plan_material=LifecyclePlanMaterial(
            schema=PLAN_MATERIAL_SCHEMA,
            transaction_id=TRANSACTION_ID,
            plan_hash=PLAN_HASH,
            state="verifying",
            operations=operations,
            definitions=(_definition("ntfy"), _definition("gitea")),
            attested_approval=True,
        ),
    )


def test_binding_selects_mutable_and_noop_in_canonical_service_order() -> None:
    selected = bind_library_verify(_command())
    assert [(item.service_id, item.action) for item in selected] == [
        ("gitea", "install"),
        ("ntfy", "noop"),
    ]


@pytest.mark.parametrize(
    "change",
    [
        lambda c: replace(c, operation_key="configure"),
        lambda c: replace(c, service_ids=("ntfy", "gitea")),
        lambda c: replace(c, payload={"serviceIds": ["gitea"]}),
        lambda c: replace(c, plan_material=replace(c.plan_material, state="applying")),
        lambda c: replace(
            c, plan_material=replace(c.plan_material, attested_approval=False)
        ),
        lambda c: replace(
            c,
            plan_material=replace(
                c.plan_material,
                operations=(PlannedOperation("searxng", "noop"),)
                + c.plan_material.operations[1:],
            ),
        ),
        lambda c: replace(
            c,
            plan_material=replace(
                c.plan_material,
                definitions=(
                    replace(c.plan_material.definitions[0], definition_source="user"),
                )
                + c.plan_material.definitions[1:],
            ),
        ),
        lambda c: replace(
            c,
            plan_material=replace(
                c.plan_material,
                definitions=(
                    replace(c.plan_material.definitions[0], source_tree_sha256=None),
                )
                + c.plan_material.definitions[1:],
            ),
        ),
    ],
)
def test_binding_refuses_invalid_or_mixed_verify_plan(change) -> None:
    with pytest.raises(LifecycleWorkValidationError) as caught:
        bind_library_verify(change(_command()))
    assert caught.value.code == "library-verify-plan-mismatch"
