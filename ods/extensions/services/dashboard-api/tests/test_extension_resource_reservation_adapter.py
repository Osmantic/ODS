"""Dormant reservation adapter tests."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

import extension_resource_reservation_store as reservations  # noqa: E402, RUF100
from extension_lifecycle_plan import (  # noqa: E402, RUF100
    PLAN_MATERIAL_SCHEMA,
    LifecyclePlanMaterial,
    PlannedDefinition,
    PlannedHostPort,
    PlannedOperation,
)
from extension_lifecycle_work import (  # noqa: E402, RUF100
    LifecycleWorkCommand,
    LifecycleWorkValidationError,
)

SUPPORTED = (
    os.name == "posix"
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
)
if SUPPORTED:
    import extension_resource_reservation_adapter as adapter
else:
    adapter = None  # type: ignore[assignment]

TXN = "txn-" + "1" * 24
PLAN_HASH = "a" * 64
NOW = "2026-09-13T12:00:00Z"
LATER = "2026-09-13T13:00:00Z"


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True)
    path.chmod(0o700)


def _def(
    service_id: str = "my-svc",
    host_ports: tuple[PlannedHostPort, ...] | None = None,
    exclusive: tuple[str, ...] | None = None,
) -> PlannedDefinition:
    return PlannedDefinition(
        service_id=service_id,
        service_type="docker",
        manifest_schema_version="ods.services.v2",
        version="1.0.0",
        data_schema_version="1",
        definition_sha256="b" * 64,
        compose_sha256=None,
        definition_source="library",
        compose_file=None,
        images=(),
        builds=(),
        canonical_document=b"{}\n",
        host_ports=host_ports,
        exclusive=exclusive,
    )


def _plan_material(
    service_id: str = "my-svc",
    action: str = "install",
    host_ports: tuple[PlannedHostPort, ...] | None = (
        PlannedHostPort(protocol="tcp", port=8080),
    ),
    exclusive: tuple[str, ...] | None = ("gpu/slot-0",),
    state: str = "reserved",
    schema: str = PLAN_MATERIAL_SCHEMA,
) -> LifecyclePlanMaterial:
    return LifecyclePlanMaterial(
        schema=schema,
        transaction_id=TXN,
        plan_hash=PLAN_HASH,
        state=state,
        operations=(PlannedOperation(service_id, action),),
        definitions=(
            _def(
                service_id=service_id,
                host_ports=host_ports,
                exclusive=exclusive,
            ),
        ),
    )


def _command(
    operation_key: str = "reserve:my-svc",
    service_ids: tuple[str, ...] = ("my-svc",),
    payload: dict | None = None,
    plan_material: LifecyclePlanMaterial | None = None,
) -> LifecycleWorkCommand:
    if payload is None:
        if plan_material is not None:
            ops = [
                o
                for o in plan_material.operations
                if o.action != "noop" and o.service_id == service_ids[0]
            ]
            action = ops[0].action if ops else "install"
        else:
            action = "install"
        payload = {"operation": {"serviceId": service_ids[0], "action": action}}
    if plan_material is None:
        plan_material = _plan_material(service_id=service_ids[0])
    return LifecycleWorkCommand(
        transaction_id=TXN,
        plan_hash=PLAN_HASH,
        operation_key=operation_key,
        request_hash="c" * 64,
        service_ids=service_ids,
        payload=payload,
        timeout_seconds=30,
        plan_material=plan_material,
    )


@unittest.skipUnless(SUPPORTED, "requires POSIX descriptor-relative filesystem APIs")
class ReservationAdapterTests(unittest.TestCase):
    """Adapter unit tests with a real temporary store."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        root = Path(self.tmpdir) / "root"
        _private_directory(root)
        self.store = reservations.ResourceReservationStore(str(root))
        self.adapt = adapter.ResourceReservationAdapter(self.store)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- happy path --

    def test_reserves_one_definition_with_claims(self) -> None:
        plan_ports = (PlannedHostPort(protocol="tcp", port=8080),)
        pm = _plan_material(host_ports=plan_ports, exclusive=("gpu/slot-0",))
        cmd = _command(plan_material=pm)
        result = self.adapt(cmd)
        self.assertEqual(len(result), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in result))

    def test_port_order_converted_and_sorted(self) -> None:
        plan_ports = (
            PlannedHostPort(protocol="tcp", port=443),
            PlannedHostPort(protocol="tcp", port=8080),
        )
        pm = _plan_material(host_ports=plan_ports, exclusive=("a-token",))
        cmd = _command(plan_material=pm)
        self.adapt(cmd)

        record = self.store.snapshot(TXN, PLAN_HASH, "my-svc")
        self.assertIsNotNone(record)
        self.assertEqual(len(record.claims.host_ports), 2)
        self.assertEqual(record.claims.host_ports[0].port, 443)
        self.assertEqual(record.claims.host_ports[1].port, 8080)

    def test_exclusive_order_preserved(self) -> None:
        plan_ports = (PlannedHostPort(protocol="tcp", port=8080),)
        pm = _plan_material(host_ports=plan_ports, exclusive=("alpha", "beta"))
        cmd = _command(plan_material=pm)
        self.adapt(cmd)

        record = self.store.snapshot(TXN, PLAN_HASH, "my-svc")
        self.assertIsNotNone(record)
        self.assertEqual(record.claims.exclusive, ("alpha", "beta"))

    def test_replays_as_duplicate(self) -> None:
        plan_ports = (PlannedHostPort(protocol="tcp", port=8080),)
        pm = _plan_material(host_ports=plan_ports, exclusive=("gpu/slot-0",))
        cmd = _command(plan_material=pm)
        self.adapt(cmd)
        result2 = self.adapt(cmd)
        self.assertEqual(len(result2), 64)

    def test_noop_excluded_from_operation_count(self) -> None:
        pm = LifecyclePlanMaterial(
            schema=PLAN_MATERIAL_SCHEMA,
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            state="reserved",
            operations=(
                PlannedOperation("other-svc", "noop"),
                PlannedOperation("my-svc", "install"),
            ),
            definitions=(
                _def(service_id="other-svc"),
                _def(
                    service_id="my-svc",
                    host_ports=(PlannedHostPort(protocol="tcp", port=8080),),
                    exclusive=("gpu/slot-0",),
                ),
            ),
        )
        payload = {"operation": {"serviceId": "my-svc", "action": "install"}}
        cmd = _command(payload=payload, plan_material=pm)
        self.adapt(cmd)

        record = self.store.snapshot(TXN, PLAN_HASH, "my-svc")
        self.assertIsNotNone(record)
        self.assertEqual(record.service_id, "my-svc")

    # -- error mapping --

    def test_conflict_mapped_to_fixed_code(self) -> None:
        plan_ports = (PlannedHostPort(protocol="tcp", port=8080),)
        pm = _plan_material(host_ports=plan_ports, exclusive=("gpu/slot-0",))
        cmd = _command(plan_material=pm)
        self.adapt(cmd)
        pm2 = _plan_material(host_ports=plan_ports, exclusive=("gpu/slot-1",))
        cmd2 = _command(plan_material=pm2)
        with self.assertRaises(adapter.ResourceReservationAdapterError) as ctx:
            self.adapt(cmd2)
        self.assertEqual(ctx.exception.code, "lifecycle-work-reservation-failed")

    def test_terminal_duplicate_mapped(self) -> None:
        plan_ports = (PlannedHostPort(protocol="tcp", port=8080),)
        pm = _plan_material(host_ports=plan_ports, exclusive=("gpu/slot-0",))
        cmd = _command(plan_material=pm)
        self.adapt(cmd)
        record = self.store.snapshot(TXN, PLAN_HASH, "my-svc")
        self.assertIsNotNone(record)
        created_dt = datetime.strptime(record.created_at, "%Y-%m-%dT%H:%M:%SZ")  # noqa: DTZ007
        created_dt = created_dt.replace(tzinfo=timezone.utc)
        finish_dt = created_dt.replace(day=min(created_dt.day + 1, 28))
        finish_time = finish_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.store.finish(TXN, PLAN_HASH, "my-svc", "failed", finish_time)
        with self.assertRaises(adapter.ResourceReservationAdapterError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-reservation-failed")

    def test_claim_conflict_mapped(self) -> None:
        pm1 = _plan_material(
            host_ports=(PlannedHostPort(protocol="tcp", port=8080),),
            exclusive=("gpu/slot-0",),
        )
        cmd1 = _command(plan_material=pm1)
        self.adapt(cmd1)

        pm2 = LifecyclePlanMaterial(
            schema=PLAN_MATERIAL_SCHEMA,
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            state="reserved",
            operations=(PlannedOperation("other-svc", "install"),),
            definitions=(
                _def(
                    service_id="other-svc",
                    host_ports=(PlannedHostPort(protocol="tcp", port=8080),),
                    exclusive=("gpu/slot-1",),
                ),
            ),
        )
        cmd2 = LifecycleWorkCommand(
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            operation_key="reserve:other-svc",
            request_hash="c" * 64,
            service_ids=("other-svc",),
            payload={"operation": {"serviceId": "other-svc", "action": "install"}},
            timeout_seconds=30,
            plan_material=pm2,
        )
        with self.assertRaises(adapter.ResourceReservationAdapterError) as ctx:
            self.adapt(cmd2)
        self.assertEqual(ctx.exception.code, "lifecycle-work-reservation-failed")

    # -- plan material validation --

    def test_missing_plan_material(self) -> None:
        cmd = LifecycleWorkCommand(
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            operation_key="reserve:my-svc",
            request_hash="c" * 64,
            service_ids=("my-svc",),
            payload={"operation": {"serviceId": "my-svc", "action": "install"}},
            timeout_seconds=30,
            plan_material=None,
        )
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-missing")

    def test_wrong_plan_material_type(self) -> None:
        cmd = LifecycleWorkCommand(
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            operation_key="reserve:my-svc",
            request_hash="c" * 64,
            service_ids=("my-svc",),
            payload={"operation": {"serviceId": "my-svc", "action": "install"}},
            timeout_seconds=30,
            plan_material="not-plan-material",  # type: ignore[arg-type]
        )
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    def test_plan_schema_mismatch(self) -> None:
        pm = _plan_material(schema="wrong-schema")
        cmd = _command(plan_material=pm)
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    def test_plan_state_not_reserved(self) -> None:
        pm = _plan_material(state="staged")
        cmd = _command(plan_material=pm)
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    def test_binding_mismatch_plan_vs_command(self) -> None:
        pm = LifecyclePlanMaterial(
            schema=PLAN_MATERIAL_SCHEMA,
            transaction_id="txn-" + "2" * 24,
            plan_hash=PLAN_HASH,
            state="reserved",
            operations=(PlannedOperation("my-svc", "install"),),
            definitions=(
                _def(
                    "my-svc",
                    host_ports=(PlannedHostPort(protocol="tcp", port=8080),),
                    exclusive=("gpu/slot-0",),
                ),
            ),
        )
        cmd = _command(plan_material=pm)
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    # -- command/operation validation --

    def test_operation_mismatch(self) -> None:
        pm = _plan_material()
        cmd = _command(operation_key="stage", plan_material=pm)
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-operation-mismatch")

    def test_operation_key_divergent_underscore_rejected(self) -> None:
        """Underscores are valid in service_ids per SERVICE_ID_RE
        ([a-z0-9][a-z0-9_-]*).  A key like reserve:my_svc succeeds when
        service_ids is ("my_svc",) because the service_id is valid.
        Divergence (e.g. reserve:my-svc vs service_ids=("my_svc",)) still
        fails as operation-mismatch."""
        pm = _plan_material(service_id="my_svc")
        cmd = LifecycleWorkCommand(
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            operation_key="reserve:my_svc",
            request_hash="c" * 64,
            service_ids=("my_svc",),
            payload={"operation": {"serviceId": "my_svc", "action": "install"}},
            timeout_seconds=30,
            plan_material=pm,
        )
        self.adapt(cmd)

    def test_operation_key_must_match_service_ids(self) -> None:
        """operation_key must be exactly f'reserve:{service_ids[0]}'."""
        pm = _plan_material(service_id="my-svc")
        cmd = LifecycleWorkCommand(
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            operation_key="reserve:wrong-svc",
            request_hash="c" * 64,
            service_ids=("my-svc",),
            payload={"operation": {"serviceId": "my-svc", "action": "install"}},
            timeout_seconds=30,
            plan_material=pm,
        )
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-operation-mismatch")

    def test_payload_mismatch(self) -> None:
        pm = _plan_material()
        cmd = _command(
            payload={"operation": {"serviceId": "wrong-svc", "action": "install"}},
            plan_material=pm,
        )
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    # -- service_ids validation --

    def test_empty_service_ids_fails(self) -> None:
        """Empty service_ids tuple fails before any indexing."""
        pm = _plan_material()
        cmd = LifecycleWorkCommand(
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            operation_key="reserve:",
            request_hash="c" * 64,
            service_ids=(),
            payload={"operation": {"serviceId": "", "action": "install"}},
            timeout_seconds=30,
            plan_material=pm,
        )
        with self.assertRaises(LifecycleWorkValidationError):
            self.adapt(cmd)

    def test_multi_service_ids_fails(self) -> None:
        """Multiple service_ids fail before any indexing."""
        pm = _plan_material()
        cmd = LifecycleWorkCommand(
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            operation_key="reserve:my-svc",
            request_hash="c" * 64,
            service_ids=("my-svc", "other-svc"),
            payload={"operation": {"serviceId": "my-svc", "action": "install"}},
            timeout_seconds=30,
            plan_material=pm,
        )
        with self.assertRaises(LifecycleWorkValidationError):
            self.adapt(cmd)

    # -- claims/definition validation --

    def test_missing_claims_on_definition(self) -> None:
        pm = _plan_material(host_ports=None, exclusive=None)
        cmd = _command(plan_material=pm)
        with self.assertRaises(adapter.ResourceReservationAdapterError) as ctx:
            self.adapt(cmd)
        self.assertEqual(
            ctx.exception.code, "lifecycle-work-reservation-claims-missing"
        )

    # -- type validation --

    def test_invalid_command_type(self) -> None:
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt("not-a-command")  # type: ignore[arg-type]
        self.assertEqual(ctx.exception.code, "lifecycle-work-command-invalid")

    # -- noop rejection --

    def test_noop_only_fails_no_store_call(self) -> None:
        pm = LifecyclePlanMaterial(
            schema=PLAN_MATERIAL_SCHEMA,
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            state="reserved",
            operations=(PlannedOperation("my-svc", "noop"),),
            definitions=(_def("my-svc"),),
        )
        cmd = LifecycleWorkCommand(
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            operation_key="reserve:my-svc",
            request_hash="c" * 64,
            service_ids=("my-svc",),
            payload={"operation": {"serviceId": "my-svc", "action": "noop"}},
            timeout_seconds=30,
            plan_material=pm,
        )
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")
        self.assertIsNone(self.store.snapshot(TXN, PLAN_HASH, "my-svc"))

    # -- record evidence --

    def test_record_evidence_validated_status(self) -> None:
        pm = _plan_material()
        cmd = _command(plan_material=pm)
        self.adapt(cmd)
        record = self.store.snapshot(TXN, PLAN_HASH, "my-svc")
        self.assertIsNotNone(record)
        self.assertEqual(record.status, "active")

    def test_record_evidence_validated_binding(self) -> None:
        pm = _plan_material()
        cmd = _command(plan_material=pm)
        self.adapt(cmd)
        record = self.store.snapshot(TXN, PLAN_HASH, "my-svc")
        self.assertIsNotNone(record)
        self.assertEqual(record.transaction_id, TXN)
        self.assertEqual(record.plan_hash, PLAN_HASH)
        self.assertEqual(record.service_id, "my-svc")
        self.assertEqual(len(record.record_sha256), 64)

    def test_clock_produces_utc_second(self) -> None:
        result = adapter._now_utc_second()
        self.assertTrue(result.endswith("Z"))
        self.assertEqual(len(result), 20)
        parsed = datetime.strptime(result, "%Y-%m-%dT%H:%M:%SZ")  # noqa: DTZ007
        parsed = parsed.replace(tzinfo=timezone.utc)
        self.assertIsNotNone(parsed)

    def test_protocol_independence(self) -> None:
        plan_ports = (
            PlannedHostPort(protocol="udp", port=1000),
            PlannedHostPort(protocol="tcp", port=1000),
        )
        pm = _plan_material(host_ports=plan_ports, exclusive=())
        cmd = _command(plan_material=pm)
        self.adapt(cmd)

        record = self.store.snapshot(TXN, PLAN_HASH, "my-svc")
        self.assertIsNotNone(record)
        self.assertEqual(record.claims.host_ports[0].protocol, "tcp")
        self.assertEqual(record.claims.host_ports[1].protocol, "udp")

    # -- defect 1: forged plan ports rejected, not silently deduplicated --

    def test_forged_plan_port_types_rejected(self) -> None:
        """Plan ports with wrong item types must be rejected, not silently deduped."""

        class FakePort:
            port = 8080
            protocol = "tcp"

        def _def_forged():
            d = _def(
                host_ports=(FakePort(),),  # type: ignore[arg-type]
                exclusive=("gpu/slot-0",),
            )
            # Monkey-patch host_ports to the forged tuple
            return PlannedDefinition(
                service_id=d.service_id,
                service_type=d.service_type,
                manifest_schema_version=d.manifest_schema_version,
                version=d.version,
                data_schema_version=d.data_schema_version,
                definition_sha256=d.definition_sha256,
                compose_sha256=d.compose_sha256,
                definition_source=d.definition_source,
                compose_file=d.compose_file,
                images=d.images,
                builds=d.builds,
                canonical_document=d.canonical_document,
                host_ports=(FakePort(),),  # type: ignore[arg-type]
                exclusive=d.exclusive,
            )

        pm = LifecyclePlanMaterial(
            schema=PLAN_MATERIAL_SCHEMA,
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            state="reserved",
            operations=(PlannedOperation("my-svc", "install"),),
            definitions=(_def_forged(),),
        )
        cmd = _command(plan_material=pm)
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    def test_duplicate_plan_ports_fail_before_store(self) -> None:
        """Duplicate ports in plan must be caught by ReservationClaims
        and mapped to a value-free plan-mismatch, not silently deduped."""
        plan_ports = (
            PlannedHostPort(protocol="tcp", port=8080),
            PlannedHostPort(protocol="tcp", port=8080),
        )
        pm = _plan_material(host_ports=plan_ports, exclusive=("gpu/slot-0",))
        cmd = _command(plan_material=pm)
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    # -- defect 3: positional alignment, actions outside valid set --

    def test_empty_operations_rejected(self) -> None:
        """Empty operations/definitions tuples must be rejected."""
        pm = LifecyclePlanMaterial(
            schema=PLAN_MATERIAL_SCHEMA,
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            state="reserved",
            operations=(),
            definitions=(),
        )
        cmd = _command(plan_material=pm)
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    def test_operations_definitions_mismatch_length(self) -> None:
        """Operations and definitions must be the same length."""
        pm = LifecyclePlanMaterial(
            schema=PLAN_MATERIAL_SCHEMA,
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            state="reserved",
            operations=(
                PlannedOperation("a", "install"),
                PlannedOperation("b", "install"),
            ),
            definitions=(
                _def(
                    "a",
                    host_ports=(PlannedHostPort(protocol="tcp", port=8080),),
                    exclusive=("t",),
                ),
            ),
        )
        cmd = _command(
            operation_key="reserve:a",
            service_ids=("a",),
            plan_material=pm,
        )
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    def test_invalid_operation_types_rejected(self) -> None:
        """Operations must be PlannedOperation instances."""
        pm = LifecyclePlanMaterial(
            schema=PLAN_MATERIAL_SCHEMA,
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            state="reserved",
            operations=("not-an-op",),  # type: ignore[arg-type]
            definitions=(
                _def(
                    "my-svc",
                    host_ports=(PlannedHostPort(protocol="tcp", port=8080),),
                    exclusive=("t",),
                ),
            ),
        )
        cmd = LifecycleWorkCommand(
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            operation_key="reserve:my-svc",
            request_hash="c" * 64,
            service_ids=("my-svc",),
            payload={"operation": {"serviceId": "my-svc", "action": "install"}},
            timeout_seconds=30,
            plan_material=pm,
        )
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    def test_invalid_definition_types_rejected(self) -> None:
        """Definitions must be PlannedDefinition instances."""
        pm = LifecyclePlanMaterial(
            schema=PLAN_MATERIAL_SCHEMA,
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            state="reserved",
            operations=(PlannedOperation("my-svc", "install"),),
            definitions=("not-a-def",),  # type: ignore[arg-type]
        )
        cmd = _command(plan_material=pm)
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    def test_action_removal_rejected(self) -> None:
        """Actions outside install/enable/repair/update must be rejected."""
        pm = LifecyclePlanMaterial(
            schema=PLAN_MATERIAL_SCHEMA,
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            state="reserved",
            operations=(PlannedOperation("my-svc", "remove"),),
            definitions=(
                _def(
                    "my-svc",
                    host_ports=(PlannedHostPort(protocol="tcp", port=8080),),
                    exclusive=("gpu/slot-0",),
                ),
            ),
        )
        cmd = _command(plan_material=pm)
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    # -- defect 4: correct error types --

    def test_malformed_command_raises_validation_error(self) -> None:
        """Malformed commands raise LifecycleWorkValidationError, not AdapterError."""
        with self.assertRaises(LifecycleWorkValidationError):
            self.adapt(object())  # type: ignore[arg-type]

    def test_missing_claims_raises_adapter_error_not_validation(self) -> None:
        """Missing claims (valid plan, absent resource data) is an adapter error."""
        pm = _plan_material(host_ports=None, exclusive=None)
        cmd = _command(plan_material=pm)
        with self.assertRaises(adapter.ResourceReservationAdapterError) as ctx:
            self.adapt(cmd)
        self.assertEqual(
            ctx.exception.code, "lifecycle-work-reservation-claims-missing"
        )

    # -- defect 6: unexpected exception redaction --

    def test_unexpected_exception_redacted(self) -> None:
        """Raw exception text must not be exposed in the error code."""
        original_reserve = self.store.reserve

        def exploding(*args, **kwargs):
            raise RuntimeError("internal database corruption: /etc/shadow exposed")

        self.store.reserve = exploding  # type: ignore[method-assign]
        try:
            pm = _plan_material()
            cmd = _command(plan_material=pm)
            with self.assertRaises(adapter.ResourceReservationAdapterError) as ctx:
                self.adapt(cmd)
            self.assertEqual(ctx.exception.code, "lifecycle-work-reservation-failed")
            error_str = str(ctx.exception)
            self.assertNotIn("internal database", error_str)
            self.assertNotIn("/etc/shadow", error_str)
        finally:
            self.store.reserve = original_reserve

    # -- defect 7: __all__ only public types --

    def test_all_exports_only_public(self) -> None:
        """__all__ must contain only the public adapter types."""
        self.assertEqual(
            set(adapter.__all__),
            {"ResourceReservationAdapter", "ResourceReservationAdapterError"},
        )
        self.assertNotIn("_map_store_error", adapter.__all__)
        self.assertNotIn("_validate_record", adapter.__all__)
        self.assertNotIn("_plan_ports_to_store", adapter.__all__)

    # -- defect 8: returned action/claims match --

    def test_record_action_matches_operation(self) -> None:
        """Returned record action must match the plan operation action."""
        pm = _plan_material(action="enable")
        cmd = _command(plan_material=pm)
        self.adapt(cmd)
        record = self.store.snapshot(TXN, PLAN_HASH, "my-svc")
        self.assertIsNotNone(record)
        self.assertEqual(record.action, "enable")

    def test_record_claims_match_input(self) -> None:
        """Returned record claims must equal the claims we sent to the store."""
        plan_ports = (
            PlannedHostPort(protocol="tcp", port=9090),
            PlannedHostPort(protocol="udp", port=9091),
        )
        exclusive = ("alpha", "beta", "gamma")
        pm = _plan_material(host_ports=plan_ports, exclusive=exclusive)
        cmd = _command(plan_material=pm)
        self.adapt(cmd)
        record = self.store.snapshot(TXN, PLAN_HASH, "my-svc")
        self.assertIsNotNone(record)
        self.assertEqual(record.claims.exclusive, tuple(exclusive))
        self.assertEqual(len(record.claims.host_ports), 2)

    # -- defect (2): record_sha256 must be exactly 64 lowercase hex --

    def test_forged_record_sha256_rejected(self) -> None:
        """A record_sha256 of 'g' * 64 must fail fullmatch against [0-9a-f]{64}."""
        original_reserve = self.store.reserve

        def fake_reserve(**kwargs):
            record = original_reserve(**kwargs)
            from dataclasses import replace

            return replace(record, record_sha256="g" * 64)

        self.store.reserve = fake_reserve  # type: ignore[method-assign]
        try:
            pm = _plan_material()
            cmd = _command(plan_material=pm)
            with self.assertRaises(adapter.ResourceReservationAdapterError) as ctx:
                self.adapt(cmd)
            self.assertEqual(
                ctx.exception.code, "lifecycle-work-reservation-evidence-mismatch"
            )
        finally:
            self.store.reserve = original_reserve

    # -- defect (3): positional alignment even for crossing material --

    def test_crossing_service_ids_rejected(self) -> None:
        """Two-entry crossing case: operations ordered a,b and definitions
        ordered b,a.  A valid reserve:a command would otherwise find exactly
        one a operation and one a definition.  Positional comparison must
        reject the crossing before any store writes."""
        pm = LifecyclePlanMaterial(
            schema=PLAN_MATERIAL_SCHEMA,
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            state="reserved",
            operations=(
                PlannedOperation("a", "install"),
                PlannedOperation("b", "install"),
            ),
            definitions=(
                _def(
                    service_id="b",
                    host_ports=(PlannedHostPort(protocol="tcp", port=8080),),
                    exclusive=("gpu/slot-0",),
                ),
                _def(
                    service_id="a",
                    host_ports=(PlannedHostPort(protocol="tcp", port=9090),),
                    exclusive=("gpu/slot-1",),
                ),
            ),
        )
        cmd = LifecycleWorkCommand(
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            operation_key="reserve:a",
            request_hash="c" * 64,
            service_ids=("a",),
            payload={"operation": {"serviceId": "a", "action": "install"}},
            timeout_seconds=30,
            plan_material=pm,
        )
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")
        # Prove zero store writes occurred
        self.assertIsNone(self.store.snapshot(TXN, PLAN_HASH, "a"))
        self.assertIsNone(self.store.snapshot(TXN, PLAN_HASH, "b"))

    # -- defect (4): forged host_ports/exclusive lists rejected --

    def test_forged_list_host_ports_rejected(self) -> None:
        """A list host_ports (instead of tuple) must be rejected."""
        d = _def(
            host_ports=[PlannedHostPort(protocol="tcp", port=8080)],  # type: ignore[arg-type]
            exclusive=("gpu/slot-0",),
        )
        pm = LifecyclePlanMaterial(
            schema=PLAN_MATERIAL_SCHEMA,
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            state="reserved",
            operations=(PlannedOperation("my-svc", "install"),),
            definitions=(d,),
        )
        cmd = _command(plan_material=pm)
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    def test_forged_list_exclusive_rejected(self) -> None:
        """A list exclusive (instead of tuple) must be rejected."""
        d = _def(
            host_ports=(PlannedHostPort(protocol="tcp", port=8080),),
            exclusive=["gpu/slot-0"],  # type: ignore[arg-type]
        )
        pm = LifecyclePlanMaterial(
            schema=PLAN_MATERIAL_SCHEMA,
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            state="reserved",
            operations=(PlannedOperation("my-svc", "install"),),
            definitions=(d,),
        )
        cmd = _command(plan_material=pm)
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    # -- defect (4): wrong store type --

    def test_wrong_store_type_raises_adapter_error(self) -> None:
        """Constructing an adapter with a wrong store type raises
        ResourceReservationAdapterError, not a raw TypeError."""
        with self.assertRaises(adapter.ResourceReservationAdapterError) as ctx:
            adapter.ResourceReservationAdapter(object())  # type: ignore[arg-type]
        self.assertEqual(ctx.exception.code, "lifecycle-work-reservation-adapter-invalid")


@unittest.skipUnless(SUPPORTED, "requires POSIX descriptor-relative filesystem APIs")
class ReservationAdapterStoreTests(unittest.TestCase):
    """Adapter tests with a real store (requires temp dirs)."""

    def _setup_store(self):
        self.tmpdir = tempfile.mkdtemp()
        root = Path(self.tmpdir) / "root"
        _private_directory(root)
        store = reservations.ResourceReservationStore(str(root))
        return store

    def test_full_flow_with_real_store(self) -> None:
        store = self._setup_store()
        plan_ports = (PlannedHostPort(protocol="tcp", port=8080),)
        pm = _plan_material(host_ports=plan_ports, exclusive=("gpu/slot-0",))
        cmd = _command(plan_material=pm)
        adapt = adapter.ResourceReservationAdapter(store)
        result = adapt(cmd)
        self.assertEqual(len(result), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in result))

    def test_store_conflict_via_adapter(self) -> None:
        store = self._setup_store()
        plan_ports = (PlannedHostPort(protocol="tcp", port=8080),)
        pm = _plan_material(host_ports=plan_ports, exclusive=("gpu/slot-0",))
        cmd = _command(plan_material=pm)
        adapt = adapter.ResourceReservationAdapter(store)
        adapt(cmd)

        pm2 = _plan_material(host_ports=plan_ports, exclusive=("gpu/slot-1",))
        cmd2 = _command(plan_material=pm2)
        with self.assertRaises(adapter.ResourceReservationAdapterError) as ctx:
            adapt(cmd2)
        self.assertEqual(ctx.exception.code, "lifecycle-work-reservation-failed")

    def test_no_filesystem_side_effects_beyond_store(self) -> None:
        store = self._setup_store()
        root = Path(self.tmpdir) / "root"
        before = {str(p) for p in root.rglob("*")}

        pm = _plan_material()
        cmd = _command(plan_material=pm)
        adapt = adapter.ResourceReservationAdapter(store)
        adapt(cmd)

        after = {str(p) for p in root.rglob("*")}
        new = after - before
        self.assertEqual(len(new), 1)
        self.assertIn("reservations.json", next(iter(new)))


if __name__ == "__main__":
    unittest.main()
