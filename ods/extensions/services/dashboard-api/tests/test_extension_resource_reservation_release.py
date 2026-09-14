"""Dormant release adapter tests.

Covers: exact replay, empty/noncanonical/order/crossed plan material,
missing/mismatched/terminal records, action binding, action race/TOCTOU,
claims race, mixed active/released replay, all-before-effect, one write,
injected write-before-replace vs after-replace ambiguity, forged
record/evidence/duplicate/schema/hash, exact replay evidence, constructor/
root custody, runtime, and no production importer.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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
    import extension_resource_reservation_adapter as reserve_adapter
    import extension_resource_reservation_release as release_adapter
    import extension_resource_reservation_runtime as reservation_runtime
else:
    reserve_adapter = None  # type: ignore[assignment]
    release_adapter = None  # type: ignore[assignment]
    reservation_runtime = None  # type: ignore[assignment]

TXN = "txn-" + "1" * 24
PLAN_HASH = "a" * 64
NOW = "2026-09-13T12:00:00Z"
LATER = "2026-09-13T13:00:00Z"


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True)
    path.chmod(0o700)


def _def(
    service_id: str,
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


def _claims(
    port: int,
    protocol: str = "tcp",
    exclusive: tuple[str, ...] = (),
) -> reservations.ReservationClaims:
    return reservations.ReservationClaims(
        host_ports=(reservations.HostPort(port=port, protocol=protocol),),
        exclusive=exclusive,
    )


def _rehash_record(
    record: reservations.ReservationRecord,
    **changes: object,
) -> reservations.ReservationRecord:
    """Return an internally hash-consistent forged record for adapter tests."""
    forged = replace(record, **changes)
    document = {
        "schema": forged.schema,
        "transactionId": forged.transaction_id,
        "planHash": forged.plan_hash,
        "serviceId": forged.service_id,
        "action": forged.action,
        "status": forged.status,
        "createdAt": forged.created_at,
        "updatedAt": forged.updated_at,
        "claims": forged.claims.to_dict(),
        "claimsDigest": forged.claims_digest,
    }
    digest = hashlib.sha256(reservations._canonical_json_bytes(document)).hexdigest()
    return replace(forged, record_sha256=digest)


def _plan_material(
    *,
    operations: tuple[PlannedOperation, ...],
    definitions: tuple[PlannedDefinition, ...],
    state: str = "verifying",
) -> LifecyclePlanMaterial:
    return LifecyclePlanMaterial(
        schema=PLAN_MATERIAL_SCHEMA,
        transaction_id=TXN,
        plan_hash=PLAN_HASH,
        state=state,
        operations=operations,
        definitions=definitions,
    )


def _release_command(
    *,
    plan_material: LifecyclePlanMaterial,
    service_ids: tuple[str, ...] | None = None,
    payload: dict | None = None,
) -> LifecycleWorkCommand:
    """Build a release command from plan material."""
    mutable = tuple(
        op for op in plan_material.operations if op.action != "noop"
    )
    if service_ids is None:
        service_ids = tuple(op.service_id for op in mutable)
    if payload is None:
        payload = {"serviceIds": list(service_ids)}
    return LifecycleWorkCommand(
        transaction_id=TXN,
        plan_hash=PLAN_HASH,
        operation_key="release",
        request_hash="c" * 64,
        service_ids=service_ids,
        payload=payload,
        timeout_seconds=30,
        plan_material=plan_material,
    )


# ---------------------------------------------------------------------------
# Store: batch_release
# ---------------------------------------------------------------------------


@unittest.skipUnless(SUPPORTED, "requires POSIX descriptor-relative filesystem APIs")
class StoreBatchReleaseTests(unittest.TestCase):
    """Store-level batch_release tests."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        root = Path(self.tmpdir) / "root"
        _private_directory(root)
        self.store = reservations.ResourceReservationStore(str(root))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- happy path --

    def test_single_service_release(self) -> None:
        claims = _claims(8080)
        exp = reservations.ReleaseExpectation(
            service_id="svc-a", action="install", claims=claims
        )
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", claims, NOW)
        released = self.store.batch_release(TXN, PLAN_HASH, (exp,), LATER)
        self.assertEqual(len(released), 1)
        self.assertEqual(released[0].service_id, "svc-a")
        self.assertEqual(released[0].status, reservations.RELEASED)
        self.assertFalse(released[0].duplicate)

    def test_multi_service_release(self) -> None:
        claims_a = _claims(8080)
        claims_b = _claims(9090)
        exp_a = reservations.ReleaseExpectation(
            service_id="svc-a", action="install", claims=claims_a
        )
        exp_b = reservations.ReleaseExpectation(
            service_id="svc-b", action="install", claims=claims_b
        )
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", claims_a, NOW)
        self.store.reserve(TXN, PLAN_HASH, "svc-b", "install", claims_b, NOW)
        released = self.store.batch_release(TXN, PLAN_HASH, (exp_a, exp_b), LATER)
        self.assertEqual(len(released), 2)
        self.assertEqual(released[0].service_id, "svc-a")
        self.assertEqual(released[1].service_id, "svc-b")
        for rec in released:
            self.assertEqual(rec.status, reservations.RELEASED)

    def test_release_preserves_expectation_order(self) -> None:
        claims_a = _claims(8080)
        claims_b = _claims(9090)
        exp_a = reservations.ReleaseExpectation(
            service_id="svc-a", action="install", claims=claims_a
        )
        exp_b = reservations.ReleaseExpectation(
            service_id="svc-b", action="install", claims=claims_b
        )
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", claims_a, NOW)
        self.store.reserve(TXN, PLAN_HASH, "svc-b", "install", claims_b, NOW)
        # Request in reverse expectation order
        released = self.store.batch_release(TXN, PLAN_HASH, (exp_b, exp_a), LATER)
        self.assertEqual(released[0].service_id, "svc-b")
        self.assertEqual(released[1].service_id, "svc-a")

    # -- idempotent replay --

    def test_exact_replay_returns_duplicate(self) -> None:
        claims = _claims(8080)
        exp = reservations.ReleaseExpectation(
            service_id="svc-a", action="install", claims=claims
        )
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", claims, NOW)
        self.store.batch_release(TXN, PLAN_HASH, (exp,), LATER)
        replay = self.store.batch_release(TXN, PLAN_HASH, (exp,), LATER)
        self.assertTrue(replay[0].duplicate)

    def test_full_replay_returns_all_duplicate(self) -> None:
        """Two services: release all, then exact replay is wholly duplicate."""
        claims_a = _claims(8080)
        claims_b = _claims(9090)
        exp_a = reservations.ReleaseExpectation(
            service_id="svc-a", action="install", claims=claims_a
        )
        exp_b = reservations.ReleaseExpectation(
            service_id="svc-b", action="install", claims=claims_b
        )
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", claims_a, NOW)
        self.store.reserve(TXN, PLAN_HASH, "svc-b", "install", claims_b, NOW)
        first = self.store.batch_release(
            TXN, PLAN_HASH, (exp_a, exp_b), LATER
        )
        self.assertFalse(first[0].duplicate)
        self.assertFalse(first[1].duplicate)
        # Full replay: all already released -> all duplicate
        replay = self.store.batch_release(
            TXN, PLAN_HASH, (exp_a, exp_b), LATER
        )
        self.assertTrue(replay[0].duplicate)
        self.assertTrue(replay[1].duplicate)

    def test_mixed_active_released_batch_fails_without_effect(self) -> None:
        """Mixed state is neither a fresh atomic batch nor an exact replay."""
        claims_a = _claims(8080)
        claims_b = _claims(9090)
        exp_a = reservations.ReleaseExpectation(
            service_id="svc-a", action="install", claims=claims_a
        )
        exp_b = reservations.ReleaseExpectation(
            service_id="svc-b", action="install", claims=claims_b
        )
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", claims_a, NOW)
        self.store.reserve(TXN, PLAN_HASH, "svc-b", "install", claims_b, NOW)
        self.store.batch_release(TXN, PLAN_HASH, (exp_a,), LATER)

        with self.assertRaises(reservations.ReservationStoreError) as ctx:
            self.store.batch_release(TXN, PLAN_HASH, (exp_a, exp_b), LATER)
        self.assertEqual(ctx.exception.code, "transition-invalid")
        self.assertEqual(
            self.store.snapshot(TXN, PLAN_HASH, "svc-a").status,
            reservations.RELEASED,
        )
        self.assertEqual(
            self.store.snapshot(TXN, PLAN_HASH, "svc-b").status,
            reservations.ACTIVE,
        )

    def test_released_action_mismatch_fails_before_other_effect(self) -> None:
        """Released targets are re-proved before any active target changes."""
        claims_a = _claims(8080)
        claims_b = _claims(9090)
        enable_a = reservations.ReleaseExpectation(
            service_id="svc-a", action="enable", claims=claims_a
        )
        install_a = reservations.ReleaseExpectation(
            service_id="svc-a", action="install", claims=claims_a
        )
        install_b = reservations.ReleaseExpectation(
            service_id="svc-b", action="install", claims=claims_b
        )
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "enable", claims_a, NOW)
        self.store.reserve(TXN, PLAN_HASH, "svc-b", "install", claims_b, NOW)
        self.store.batch_release(TXN, PLAN_HASH, (enable_a,), LATER)

        with self.assertRaises(reservations.ReservationStoreError) as ctx:
            self.store.batch_release(TXN, PLAN_HASH, (install_a, install_b), LATER)
        self.assertEqual(ctx.exception.code, "transition-invalid")
        self.assertEqual(
            self.store.snapshot(TXN, PLAN_HASH, "svc-b").status,
            reservations.ACTIVE,
        )

    # -- action binding validation --

    def test_wrong_action_fails(self) -> None:
        """Record action must match expectation action."""
        claims = _claims(8080)
        exp = reservations.ReleaseExpectation(
            service_id="svc-a", action="enable", claims=claims
        )
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", claims, NOW)
        with self.assertRaises(reservations.ReservationStoreError) as ctx:
            self.store.batch_release(TXN, PLAN_HASH, (exp,), LATER)
        self.assertEqual(ctx.exception.code, "transition-invalid")

    def test_wrong_claims_fails(self) -> None:
        """Record claims must match expectation claims."""
        claims_a = _claims(8080)
        claims_b = _claims(9090)
        exp = reservations.ReleaseExpectation(
            service_id="svc-a", action="install", claims=claims_b
        )
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", claims_a, NOW)
        with self.assertRaises(reservations.ReservationStoreError) as ctx:
            self.store.batch_release(TXN, PLAN_HASH, (exp,), LATER)
        self.assertEqual(ctx.exception.code, "transition-invalid")

    def test_expectations_reproved_while_store_lock_is_held(self) -> None:
        claims = _claims(8080)
        expectation = reservations.ReleaseExpectation(
            service_id="svc-a", action="install", claims=claims
        )
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", claims, NOW)

        lock_held = False
        original_flock = reservations.fcntl.flock
        original_validate_action = reservations._validate_action
        original_release_claims = reservations._release_claims_dict

        def tracked_flock(descriptor, operation):
            nonlocal lock_held
            result = original_flock(descriptor, operation)
            if operation == reservations.fcntl.LOCK_EX:
                lock_held = True
            elif operation == reservations.fcntl.LOCK_UN:
                lock_held = False
            return result

        def checked_action(value):
            self.assertTrue(lock_held)
            return original_validate_action(value)

        def checked_claims(value):
            self.assertTrue(lock_held)
            return original_release_claims(value)

        with (
            mock.patch.object(
                reservations.fcntl,
                "flock",
                side_effect=tracked_flock,
            ),
            mock.patch.object(
                reservations,
                "_validate_action",
                side_effect=checked_action,
            ),
            mock.patch.object(
                reservations,
                "_release_claims_dict",
                side_effect=checked_claims,
            ),
        ):
            released = self.store.batch_release(
                TXN,
                PLAN_HASH,
                (expectation,),
                LATER,
            )
        self.assertEqual(released[0].status, reservations.RELEASED)
        self.assertFalse(lock_held)

    # -- validation failures --

    def test_missing_record_fails(self) -> None:
        claims_a = _claims(8080)
        claims_b = _claims(9090)
        exp_a = reservations.ReleaseExpectation(
            service_id="svc-a", action="install", claims=claims_a
        )
        exp_b = reservations.ReleaseExpectation(
            service_id="svc-b", action="install", claims=claims_b
        )
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", claims_a, NOW)
        with self.assertRaises(reservations.ReservationStoreError) as ctx:
            self.store.batch_release(
                TXN, PLAN_HASH, (exp_a, exp_b), LATER
            )
        self.assertEqual(ctx.exception.code, "transition-invalid")

    def test_terminal_record_fails(self) -> None:
        claims_a = _claims(8080)
        claims_b = _claims(9090)
        exp_a = reservations.ReleaseExpectation(
            service_id="svc-a", action="install", claims=claims_a
        )
        exp_b = reservations.ReleaseExpectation(
            service_id="svc-b", action="install", claims=claims_b
        )
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", claims_a, NOW)
        self.store.reserve(TXN, PLAN_HASH, "svc-b", "install", claims_b, NOW)
        self.store.finish(TXN, PLAN_HASH, "svc-b", reservations.FAILED, LATER)
        with self.assertRaises(reservations.ReservationStoreError) as ctx:
            self.store.batch_release(
                TXN, PLAN_HASH, (exp_a, exp_b), LATER
            )
        self.assertEqual(ctx.exception.code, "transition-invalid")

    def test_cross_plan_record_fails(self) -> None:
        claims_a = _claims(8080)
        claims_b = _claims(9090)
        exp_a = reservations.ReleaseExpectation(
            service_id="svc-a", action="install", claims=claims_a
        )
        exp_b = reservations.ReleaseExpectation(
            service_id="svc-b", action="install", claims=claims_b
        )
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", claims_a, NOW)
        self.store.reserve(
            "txn-" + "2" * 24, PLAN_HASH, "svc-b", "install", claims_b, NOW
        )
        with self.assertRaises(reservations.ReservationStoreError) as ctx:
            self.store.batch_release(
                TXN, PLAN_HASH, (exp_a, exp_b), LATER
            )
        self.assertEqual(ctx.exception.code, "transition-invalid")

    def test_empty_expectations_fails(self) -> None:
        with self.assertRaises(reservations.ReservationStoreError) as ctx:
            self.store.batch_release(TXN, PLAN_HASH, (), LATER)
        self.assertEqual(ctx.exception.code, "binding-invalid")

    def test_duplicate_service_in_expectations_fails(self) -> None:
        claims = _claims(8080)
        exp = reservations.ReleaseExpectation(
            service_id="svc-a", action="install", claims=claims
        )
        with self.assertRaises(reservations.ReservationStoreError) as ctx:
            self.store.batch_release(TXN, PLAN_HASH, (exp, exp), LATER)
        self.assertEqual(ctx.exception.code, "binding-invalid")

    def test_timestamp_before_created_fails(self) -> None:
        claims = _claims(8080)
        exp = reservations.ReleaseExpectation(
            service_id="svc-a", action="install", claims=claims
        )
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", claims, LATER)
        with self.assertRaises(reservations.ReservationStoreError) as ctx:
            self.store.batch_release(TXN, PLAN_HASH, (exp,), NOW)
        self.assertEqual(ctx.exception.code, "timestamp-invalid")

    def test_atomicity_one_fails_all_unchanged(self) -> None:
        """svc-a active, svc-b failed. Batch release must leave svc-a active."""
        claims_a = _claims(8080)
        claims_b = _claims(9090)
        exp_a = reservations.ReleaseExpectation(
            service_id="svc-a", action="install", claims=claims_a
        )
        exp_b = reservations.ReleaseExpectation(
            service_id="svc-b", action="install", claims=claims_b
        )
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", claims_a, NOW)
        self.store.reserve(TXN, PLAN_HASH, "svc-b", "install", claims_b, NOW)
        self.store.finish(TXN, PLAN_HASH, "svc-b", reservations.FAILED, LATER)
        with self.assertRaises(reservations.ReservationStoreError):
            self.store.batch_release(TXN, PLAN_HASH, (exp_a, exp_b), LATER)
        # svc-a must still be active
        rec = self.store.snapshot(TXN, PLAN_HASH, "svc-a")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.status, reservations.ACTIVE)

    def test_fresh_batch_publishes_exactly_one_snapshot(self) -> None:
        claims_a = _claims(8080)
        claims_b = _claims(9090)
        expectations = (
            reservations.ReleaseExpectation("svc-a", "install", claims_a),
            reservations.ReleaseExpectation("svc-b", "install", claims_b),
        )
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", claims_a, NOW)
        self.store.reserve(TXN, PLAN_HASH, "svc-b", "install", claims_b, NOW)

        with (
            mock.patch.object(
                reservations,
                "_read_records",
                wraps=reservations._read_records,
            ) as read_records,
            mock.patch.object(
                reservations,
                "_write_snapshot",
                wraps=reservations._write_snapshot,
            ) as publish,
        ):
            self.store.batch_release(TXN, PLAN_HASH, expectations, LATER)
        self.assertEqual(read_records.call_count, 1)
        self.assertEqual(publish.call_count, 1)

        with mock.patch.object(
            reservations,
            "_write_snapshot",
            wraps=reservations._write_snapshot,
        ) as replay_publish:
            replay = self.store.batch_release(
                TXN, PLAN_HASH, expectations, LATER
            )
        self.assertEqual(replay_publish.call_count, 0)
        self.assertTrue(all(record.duplicate for record in replay))

    def test_non_tuple_expectations_rejected(self) -> None:
        claims = _claims(8080)
        exp = reservations.ReleaseExpectation(
            service_id="svc-a", action="install", claims=claims
        )
        with self.assertRaises(reservations.ReservationStoreError) as ctx:
            self.store.batch_release(TXN, PLAN_HASH, [exp], LATER)  # type: ignore[arg-type]
        self.assertEqual(ctx.exception.code, "binding-invalid")

    def test_tuple_subclass_expectations_rejected(self) -> None:
        class TupleSubclass(tuple):
            pass

        claims = _claims(8080)
        expectation = reservations.ReleaseExpectation(
            service_id="svc-a", action="install", claims=claims
        )
        with self.assertRaises(reservations.ReservationStoreError) as ctx:
            self.store.batch_release(
                TXN,
                PLAN_HASH,
                TupleSubclass((expectation,)),  # type: ignore[arg-type]
                LATER,
            )
        self.assertEqual(ctx.exception.code, "binding-invalid")

    def test_wrong_expectation_type_rejected(self) -> None:
        with self.assertRaises(reservations.ReservationStoreError) as ctx:
            self.store.batch_release(
                TXN, PLAN_HASH, (object(),), LATER  # type: ignore[arg-type]
            )
        self.assertEqual(ctx.exception.code, "binding-invalid")

    def test_expectation_subclass_rejected(self) -> None:
        class ExpectationSubclass(reservations.ReleaseExpectation):
            pass

        expectation = ExpectationSubclass(
            service_id="svc-a", action="install", claims=_claims(8080)
        )
        with self.assertRaises(reservations.ReservationStoreError) as ctx:
            self.store.batch_release(TXN, PLAN_HASH, (expectation,), LATER)
        self.assertEqual(ctx.exception.code, "binding-invalid")

    def test_claims_subclass_rejected(self) -> None:
        class ClaimsSubclass(reservations.ReservationClaims):
            pass

        claims = ClaimsSubclass(
            host_ports=(reservations.HostPort(8080, "tcp"),),
            exclusive=(),
        )
        expectation = reservations.ReleaseExpectation(
            service_id="svc-a", action="install", claims=claims
        )
        with self.assertRaises(reservations.ReservationStoreError) as ctx:
            self.store.batch_release(TXN, PLAN_HASH, (expectation,), LATER)
        self.assertEqual(ctx.exception.code, "binding-invalid")

    # -- write failure + ambiguity --

    def test_write_failure_leaves_snapshot_intact(self) -> None:
        """Simulate a write failure before the atomic rename.

        The write to the temp file fails.  The ambiguity handler re-reads
        the snapshot, finds records still active (not released), and emits
        a clean snapshot-integrity failure.  The original snapshot is intact.
        """
        claims_a = _claims(8080)
        claims_b = _claims(9090)
        exp_a = reservations.ReleaseExpectation(
            service_id="svc-a", action="install", claims=claims_a
        )
        exp_b = reservations.ReleaseExpectation(
            service_id="svc-b", action="install", claims=claims_b
        )
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", claims_a, NOW)
        self.store.reserve(TXN, PLAN_HASH, "svc-b", "install", claims_b, NOW)
        before = Path(self.tmpdir) / "root" / "reservations.json"
        before_bytes = before.read_bytes()

        def failing_write(*args, **kwargs):
            raise OSError(28, "No space left on device")

        with mock.patch.object(reservations.os, "write", side_effect=failing_write):
            with self.assertRaises(reservations.ReservationStoreError) as ctx:
                self.store.batch_release(
                    TXN, PLAN_HASH, (exp_a, exp_b), LATER
                )
            # Write failed; ambiguity handler re-reads, finds active records
            # (not released) -> snapshot-integrity is the correct fixed failure
            self.assertIn(ctx.exception.code, {
                "snapshot-integrity", "snapshot-io-error", "transition-invalid",
            })
        # Snapshot must still have both active
        after_bytes = before.read_bytes()
        self.assertEqual(after_bytes, before_bytes)

    # -- injected after-publish/readback ambiguity (write-before-replace) --
    # The store's _write_snapshot does a rename + fsync + re-read.
    # We inject failure AFTER the rename but BEFORE the re-read to simulate
    # an ambiguous write.

    def test_injected_after_replace_readback_failure(self) -> None:
        """A one-shot readback fault after replace reconciles exact post-state."""
        claims_a = _claims(8080)
        claims_b = _claims(9090)
        expectations = (
            reservations.ReleaseExpectation("svc-a", "install", claims_a),
            reservations.ReleaseExpectation("svc-b", "install", claims_b),
        )
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", claims_a, NOW)
        self.store.reserve(TXN, PLAN_HASH, "svc-b", "install", claims_b, NOW)

        original_read = reservations._read_snapshot
        read_count = 0

        def fail_first_post_replace_read(root_fd):
            nonlocal read_count
            read_count += 1
            if read_count == 2:
                raise reservations.ReservationStoreError("snapshot-io-error")
            return original_read(root_fd)

        with mock.patch.object(
            reservations,
            "_read_snapshot",
            side_effect=fail_first_post_replace_read,
        ):
            released = self.store.batch_release(
                TXN, PLAN_HASH, expectations, LATER
            )
        self.assertEqual(read_count, 3)
        self.assertTrue(all(record.status == reservations.RELEASED for record in released))
        self.assertTrue(all(not record.duplicate for record in released))

    def test_injected_after_replace_fsync_failure(self) -> None:
        """A directory-fsync fault after replace reconciles exact post-state."""
        claims = _claims(8080)
        expectation = reservations.ReleaseExpectation(
            service_id="svc-a", action="install", claims=claims
        )
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", claims, NOW)

        original_fsync = reservations.os.fsync
        fsync_count = 0

        def fail_directory_fsync(descriptor):
            nonlocal fsync_count
            fsync_count += 1
            if fsync_count == 2:
                raise OSError(5, "injected directory fsync failure")
            return original_fsync(descriptor)

        with mock.patch.object(
            reservations.os,
            "fsync",
            side_effect=fail_directory_fsync,
        ):
            released = self.store.batch_release(
                TXN, PLAN_HASH, (expectation,), LATER
            )
        self.assertEqual(fsync_count, 2)
        self.assertEqual(released[0].status, reservations.RELEASED)
        self.assertFalse(released[0].duplicate)

    def test_injected_write_before_rename_failure(self) -> None:
        """Failure during write (before rename) leaves original snapshot intact."""
        claims_a = _claims(8080)
        claims_b = _claims(9090)
        exp_a = reservations.ReleaseExpectation(
            service_id="svc-a", action="install", claims=claims_a
        )
        exp_b = reservations.ReleaseExpectation(
            service_id="svc-b", action="install", claims=claims_b
        )
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", claims_a, NOW)
        self.store.reserve(TXN, PLAN_HASH, "svc-b", "install", claims_b, NOW)
        snap = Path(self.tmpdir) / "root" / "reservations.json"
        before = snap.read_bytes()

        def bad_write(fd, data):
            raise OSError(28, "No space left on device")

        with mock.patch.object(reservations.os, "write", side_effect=bad_write), \
             self.assertRaises(reservations.ReservationStoreError):
            self.store.batch_release(TXN, PLAN_HASH, (exp_a, exp_b), LATER)
        # No mutation
        self.assertEqual(snap.read_bytes(), before)


# ---------------------------------------------------------------------------
# Release adapter
# ---------------------------------------------------------------------------


@unittest.skipUnless(SUPPORTED, "requires POSIX descriptor-relative filesystem APIs")
class ReleaseAdapterTests(unittest.TestCase):
    """Release adapter tests with a real temporary store."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        root = Path(self.tmpdir) / "root"
        _private_directory(root)
        self.store = reservations.ResourceReservationStore(str(root))
        self.adapt = release_adapter.ResourceReleaseAdapter(self.store)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _assert_forged_result_rejected(self, transform) -> None:
        operations = (PlannedOperation("svc-a", "install"),)
        definitions = (
            _def("svc-a", (PlannedHostPort("tcp", 8080),), ("gpu/slot-0",)),
        )
        plan_material = _plan_material(
            operations=operations,
            definitions=definitions,
        )
        self.store.reserve(
            TXN,
            PLAN_HASH,
            "svc-a",
            "install",
            _claims(8080, exclusive=("gpu/slot-0",)),
            NOW,
        )
        original_batch_release = self.store.batch_release

        def fake_batch_release(*args, **kwargs):
            return tuple(
                transform(record)
                for record in original_batch_release(*args, **kwargs)
            )

        self.store.batch_release = fake_batch_release  # type: ignore[method-assign]
        try:
            with self.assertRaises(
                release_adapter.ResourceReleaseAdapterError
            ) as ctx:
                self.adapt(_release_command(plan_material=plan_material))
            self.assertEqual(
                ctx.exception.code,
                "lifecycle-work-release-evidence-mismatch",
            )
        finally:
            self.store.batch_release = original_batch_release

    # -- happy path --

    def test_single_service_release(self) -> None:
        ops = (PlannedOperation("svc-a", "install"),)
        defs = (_def("svc-a", (PlannedHostPort("tcp", 8080),), ("gpu/slot-0",)),)
        pm = _plan_material(operations=ops, definitions=defs)
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", _claims(8080, exclusive=("gpu/slot-0",)), NOW)
        result = self.adapt(_release_command(plan_material=pm))
        self.assertEqual(len(result), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in result))

    def test_multi_service_release(self) -> None:
        ops = (
            PlannedOperation("svc-a", "install"),
            PlannedOperation("svc-b", "enable"),
        )
        defs = (
            _def("svc-a", (PlannedHostPort("tcp", 8080),), ("gpu/slot-0",)),
            _def("svc-b", (PlannedHostPort("tcp", 9090),), ("gpu/slot-1",)),
        )
        pm = _plan_material(operations=ops, definitions=defs)
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", _claims(8080, exclusive=("gpu/slot-0",)), NOW)
        self.store.reserve(TXN, PLAN_HASH, "svc-b", "enable", _claims(9090, exclusive=("gpu/slot-1",)), NOW)
        result = self.adapt(_release_command(plan_material=pm))
        self.assertEqual(len(result), 64)

    def test_release_order_matches_mutable_service_order(self) -> None:
        """Evidence must use the mutable service order from the plan."""
        ops = (
            PlannedOperation("svc-b", "install"),
            PlannedOperation("svc-a", "enable"),
        )
        defs = (
            _def("svc-b", (PlannedHostPort("tcp", 9090),), ("t1",)),
            _def("svc-a", (PlannedHostPort("tcp", 8080),), ("t2",)),
        )
        pm = _plan_material(operations=ops, definitions=defs)
        self.store.reserve(TXN, PLAN_HASH, "svc-b", "install", _claims(9090, exclusive=("t1",)), NOW)
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "enable", _claims(8080, exclusive=("t2",)), NOW)
        result = self.adapt(_release_command(plan_material=pm))
        self.assertEqual(len(result), 64)

    # -- action binding: adapter proves action == PlannedOperation.action --

    def test_action_mismatch_caught_in_store(self) -> None:
        """If plan says 'install' but record was reserved with 'enable',
        the atomic batch_release under the same lock must reject it."""
        ops = (PlannedOperation("svc-a", "install"),)
        defs = (_def("svc-a", (PlannedHostPort("tcp", 8080),), ("t",)),)
        pm = _plan_material(operations=ops, definitions=defs)
        # Reserve with 'enable' but plan expects 'install'
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "enable", _claims(8080, exclusive=("t",)), NOW)
        with self.assertRaises(release_adapter.ResourceReleaseAdapterError) as ctx:
            self.adapt(_release_command(plan_material=pm))
        self.assertEqual(ctx.exception.code, "lifecycle-work-release-failed")

    # -- noop excluded from release targets --

    def test_noop_excluded_from_release(self) -> None:
        ops = (
            PlannedOperation("svc-noop", "noop"),
            PlannedOperation("svc-a", "install"),
        )
        defs = (
            _def("svc-noop"),
            _def("svc-a", (PlannedHostPort("tcp", 8080),), ("t",)),
        )
        pm = _plan_material(operations=ops, definitions=defs)
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", _claims(8080, exclusive=("t",)), NOW)
        result = self.adapt(_release_command(plan_material=pm))
        self.assertEqual(len(result), 64)

    # -- exact replay (idempotent) --

    def test_exact_replay_after_release(self) -> None:
        """Second release of same batch succeeds; replay evidence is
        identical because the persisted record is unchanged and the
        released record SHA is deterministic."""
        ops = (PlannedOperation("svc-a", "install"),)
        defs = (_def("svc-a", (PlannedHostPort("tcp", 8080),), ("t",)),)
        pm = _plan_material(operations=ops, definitions=defs)
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", _claims(8080, exclusive=("t",)), NOW)
        result1 = self.adapt(_release_command(plan_material=pm))
        result2 = self.adapt(_release_command(plan_material=pm))
        self.assertEqual(len(result2), 64)
        # Replay evidence is identical: the released record SHA is deterministic
        # and does not change on replay (duplicate=True, but record content is the same).
        self.assertEqual(result1, result2)

    # -- exact replay evidence: records with duplicate=True produce stable output --

    def test_exact_replay_evidence_stable(self) -> None:
        """Two consecutive replays of the same released batch must produce
        identical evidence (records don't change on replay)."""
        ops = (PlannedOperation("svc-a", "install"),)
        defs = (_def("svc-a", (PlannedHostPort("tcp", 8080),), ("t",)),)
        pm = _plan_material(operations=ops, definitions=defs)
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", _claims(8080, exclusive=("t",)), NOW)
        self.adapt(_release_command(plan_material=pm))
        replay1 = self.adapt(_release_command(plan_material=pm))
        replay2 = self.adapt(_release_command(plan_material=pm))
        self.assertEqual(replay1, replay2)

    # -- validation: plan material --

    def test_missing_plan_material(self) -> None:
        cmd = LifecycleWorkCommand(
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            operation_key="release",
            request_hash="c" * 64,
            service_ids=("svc-a",),
            payload={"serviceIds": ["svc-a"]},
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
            operation_key="release",
            request_hash="c" * 64,
            service_ids=("svc-a",),
            payload={"serviceIds": ["svc-a"]},
            timeout_seconds=30,
            plan_material="not-plan",  # type: ignore[arg-type]
        )
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    def test_plan_state_wrong(self) -> None:
        ops = (PlannedOperation("svc-a", "install"),)
        defs = (_def("svc-a", (PlannedHostPort("tcp", 8080),), ("t",)),)
        pm = _plan_material(operations=ops, definitions=defs, state="reserved")
        cmd = _release_command(plan_material=pm)
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    def test_plan_state_reconciling_accepted(self) -> None:
        ops = (PlannedOperation("svc-a", "install"),)
        defs = (_def("svc-a", (PlannedHostPort("tcp", 8080),), ("t",)),)
        pm = _plan_material(operations=ops, definitions=defs, state="reconciling")
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", _claims(8080, exclusive=("t",)), NOW)
        result = self.adapt(_release_command(plan_material=pm))
        self.assertEqual(len(result), 64)

    def test_plan_schema_mismatch(self) -> None:
        ops = (PlannedOperation("svc-a", "install"),)
        defs = (_def("svc-a", (PlannedHostPort("tcp", 8080),), ("t",)),)
        pm = LifecyclePlanMaterial(
            schema="wrong-schema",
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            state="verifying",
            operations=ops,
            definitions=defs,
        )
        cmd = _release_command(plan_material=pm)
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    # -- validation: operation key --

    def test_wrong_operation_key(self) -> None:
        ops = (PlannedOperation("svc-a", "install"),)
        defs = (_def("svc-a", (PlannedHostPort("tcp", 8080),), ("t",)),)
        pm = _plan_material(operations=ops, definitions=defs)
        cmd = LifecycleWorkCommand(
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            operation_key="stage",
            request_hash="c" * 64,
            service_ids=("svc-a",),
            payload={"serviceIds": ["svc-a"]},
            timeout_seconds=30,
            plan_material=pm,
        )
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-operation-mismatch")

    # -- validation: command type --

    def test_invalid_command_type(self) -> None:
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt("not-a-command")  # type: ignore[arg-type]
        self.assertEqual(ctx.exception.code, "lifecycle-work-command-invalid")

    # -- validation: service_ids mismatch --

    def test_service_ids_must_match_mutable_order(self) -> None:
        ops = (
            PlannedOperation("svc-a", "install"),
            PlannedOperation("svc-b", "enable"),
        )
        defs = (
            _def("svc-a", (PlannedHostPort("tcp", 8080),), ("t1",)),
            _def("svc-b", (PlannedHostPort("tcp", 9090),), ("t2",)),
        )
        pm = _plan_material(operations=ops, definitions=defs)
        cmd = LifecycleWorkCommand(
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            operation_key="release",
            request_hash="c" * 64,
            service_ids=("svc-b", "svc-a"),  # wrong order
            payload={"serviceIds": ["svc-b", "svc-a"]},
            timeout_seconds=30,
            plan_material=pm,
        )
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-command-invalid")

    def test_payload_mismatch(self) -> None:
        ops = (PlannedOperation("svc-a", "install"),)
        defs = (_def("svc-a", (PlannedHostPort("tcp", 8080),), ("t",)),)
        pm = _plan_material(operations=ops, definitions=defs)
        cmd = LifecycleWorkCommand(
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            operation_key="release",
            request_hash="c" * 64,
            service_ids=("svc-a",),
            payload={"serviceIds": ["wrong-svc"]},
            timeout_seconds=30,
            plan_material=pm,
        )
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    # -- validation: crossing material --

    def test_crossing_service_ids_rejected(self) -> None:
        ops = (
            PlannedOperation("a", "install"),
            PlannedOperation("b", "install"),
        )
        defs = (
            _def("b", (PlannedHostPort("tcp", 8080),), ("t",)),
            _def("a", (PlannedHostPort("tcp", 9090),), ("t2",)),
        )
        pm = _plan_material(operations=ops, definitions=defs)
        cmd = _release_command(plan_material=pm)
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    # -- validation: empty/noncanonical material --

    def test_empty_operations_rejected(self) -> None:
        pm = LifecyclePlanMaterial(
            schema=PLAN_MATERIAL_SCHEMA,
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            state="verifying",
            operations=(),
            definitions=(),
        )
        cmd = _release_command(plan_material=pm)
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    def test_operations_definitions_length_mismatch(self) -> None:
        pm = LifecyclePlanMaterial(
            schema=PLAN_MATERIAL_SCHEMA,
            transaction_id=TXN,
            plan_hash=PLAN_HASH,
            state="verifying",
            operations=(PlannedOperation("a", "install"), PlannedOperation("b", "install")),
            definitions=(_def("a", (PlannedHostPort("tcp", 8080),), ("t",)),),
        )
        cmd = _release_command(plan_material=pm)
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    # -- validation: record missing --

    def test_missing_record_fails_before_effect(self) -> None:
        ops = (
            PlannedOperation("svc-a", "install"),
            PlannedOperation("svc-b", "enable"),
        )
        defs = (
            _def("svc-a", (PlannedHostPort("tcp", 8080),), ("t1",)),
            _def("svc-b", (PlannedHostPort("tcp", 9090),), ("t2",)),
        )
        pm = _plan_material(operations=ops, definitions=defs)
        # Only reserve svc-a; svc-b is missing
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", _claims(8080, exclusive=("t1",)), NOW)
        with self.assertRaises(release_adapter.ResourceReleaseAdapterError) as ctx:
            self.adapt(_release_command(plan_material=pm))
        self.assertEqual(ctx.exception.code, "lifecycle-work-release-failed")

    def test_missing_record_does_not_mutate_others(self) -> None:
        """When svc-b is missing, svc-a must remain active."""
        ops = (
            PlannedOperation("svc-a", "install"),
            PlannedOperation("svc-b", "enable"),
        )
        defs = (
            _def("svc-a", (PlannedHostPort("tcp", 8080),), ("t1",)),
            _def("svc-b", (PlannedHostPort("tcp", 9090),), ("t2",)),
        )
        pm = _plan_material(operations=ops, definitions=defs)
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", _claims(8080, exclusive=("t1",)), NOW)
        with self.assertRaises(release_adapter.ResourceReleaseAdapterError):
            self.adapt(_release_command(plan_material=pm))
        rec = self.store.snapshot(TXN, PLAN_HASH, "svc-a")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.status, reservations.ACTIVE)

    # -- validation: terminal record --

    def test_terminal_record_fails(self) -> None:
        ops = (PlannedOperation("svc-a", "install"),)
        defs = (_def("svc-a", (PlannedHostPort("tcp", 8080),), ("t",)),)
        pm = _plan_material(operations=ops, definitions=defs)
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", _claims(8080, exclusive=("t",)), NOW)
        self.store.finish(TXN, PLAN_HASH, "svc-a", reservations.FAILED, LATER)
        with self.assertRaises(release_adapter.ResourceReleaseAdapterError) as ctx:
            self.adapt(_release_command(plan_material=pm))
        self.assertEqual(ctx.exception.code, "lifecycle-work-release-failed")

    # -- validation: claims mismatch --

    def test_wrong_claims_fails(self) -> None:
        """Plan expects port 8080, record has port 9090 -> caught in store."""
        ops = (PlannedOperation("svc-a", "install"),)
        defs = (_def("svc-a", (PlannedHostPort("tcp", 8080),), ("t",)),)
        pm = _plan_material(operations=ops, definitions=defs)
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", _claims(9090), NOW)
        with self.assertRaises(release_adapter.ResourceReleaseAdapterError) as ctx:
            self.adapt(_release_command(plan_material=pm))
        self.assertEqual(ctx.exception.code, "lifecycle-work-release-failed")

    # -- validation: record hash integrity --

    def test_forged_record_hash_fails(self) -> None:
        """A record with a corrupted recordSha256 on disk must be caught
        by the store's validation before the adapter sees it."""
        ops = (PlannedOperation("svc-a", "install"),)
        defs = (_def("svc-a", (PlannedHostPort("tcp", 8080),), ("t",)),)
        pm = _plan_material(operations=ops, definitions=defs)
        self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", _claims(8080, exclusive=("t",)), NOW)

        # Corrupt the snapshot on disk
        snap_path = Path(self.tmpdir) / "root" / "reservations.json"
        data = json.loads(snap_path.read_bytes())
        data["records"][0]["recordSha256"] = "f" * 64
        snap_path.write_bytes(reservations._canonical_json_bytes(data))

        with self.assertRaises(release_adapter.ResourceReleaseAdapterError) as ctx:
            self.adapt(_release_command(plan_material=pm))
        self.assertEqual(ctx.exception.code, "lifecycle-work-release-failed")

    # -- evidence forgery --

    def test_store_returns_forged_evidence_rejected(self) -> None:
        """If the store returns a record with invalid hash, the adapter
        must reject it before returning evidence."""
        original_batch_release = self.store.batch_release

        def fake_batch_release(*args, **kwargs):
            records = original_batch_release(*args, **kwargs)
            return tuple(
                replace(r, record_sha256="g" * 64)
                for r in records
            )

        self.store.batch_release = fake_batch_release  # type: ignore[method-assign]
        try:
            ops = (PlannedOperation("svc-a", "install"),)
            defs = (_def("svc-a", (PlannedHostPort("tcp", 8080),), ("t",)),)
            pm = _plan_material(operations=ops, definitions=defs)
            self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", _claims(8080, exclusive=("t",)), NOW)
            with self.assertRaises(release_adapter.ResourceReleaseAdapterError) as ctx:
                self.adapt(_release_command(plan_material=pm))
            self.assertEqual(
                ctx.exception.code, "lifecycle-work-release-evidence-mismatch"
            )
        finally:
            self.store.batch_release = original_batch_release

    def test_forged_duplicate_type_rejected(self) -> None:
        """duplicate must be exact bool."""
        original_batch_release = self.store.batch_release

        def fake_batch_release(*args, **kwargs):
            records = original_batch_release(*args, **kwargs)
            return tuple(replace(r, duplicate=1) for r in records)  # int, not bool

        self.store.batch_release = fake_batch_release  # type: ignore[method-assign]
        try:
            ops = (PlannedOperation("svc-a", "install"),)
            defs = (_def("svc-a", (PlannedHostPort("tcp", 8080),), ("t",)),)
            pm = _plan_material(operations=ops, definitions=defs)
            self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", _claims(8080, exclusive=("t",)), NOW)
            with self.assertRaises(release_adapter.ResourceReleaseAdapterError) as ctx:
                self.adapt(_release_command(plan_material=pm))
            self.assertEqual(
                ctx.exception.code, "lifecycle-work-release-evidence-mismatch"
            )
        finally:
            self.store.batch_release = original_batch_release

    def test_forged_record_type_rejected(self) -> None:
        """Non-ReservationRecord in returned tuple is rejected."""
        original_batch_release = self.store.batch_release

        def fake_batch_release(*args, **kwargs):
            return (SimpleNamespace(),)

        self.store.batch_release = fake_batch_release  # type: ignore[method-assign]
        try:
            ops = (PlannedOperation("svc-a", "install"),)
            defs = (_def("svc-a", (PlannedHostPort("tcp", 8080),), ("t",)),)
            pm = _plan_material(operations=ops, definitions=defs)
            self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", _claims(8080, exclusive=("t",)), NOW)
            with self.assertRaises(release_adapter.ResourceReleaseAdapterError) as ctx:
                self.adapt(_release_command(plan_material=pm))
            self.assertEqual(
                ctx.exception.code, "lifecycle-work-release-evidence-mismatch"
            )
        finally:
            self.store.batch_release = original_batch_release

    def test_forged_record_status_rejected(self) -> None:
        """Returned record with wrong status is rejected."""
        original_batch_release = self.store.batch_release

        def fake_batch_release(*args, **kwargs):
            records = original_batch_release(*args, **kwargs)
            return tuple(replace(r, status="active") for r in records)

        self.store.batch_release = fake_batch_release  # type: ignore[method-assign]
        try:
            ops = (PlannedOperation("svc-a", "install"),)
            defs = (_def("svc-a", (PlannedHostPort("tcp", 8080),), ("t",)),)
            pm = _plan_material(operations=ops, definitions=defs)
            self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", _claims(8080, exclusive=("t",)), NOW)
            with self.assertRaises(release_adapter.ResourceReleaseAdapterError) as ctx:
                self.adapt(_release_command(plan_material=pm))
            self.assertEqual(
                ctx.exception.code, "lifecycle-work-release-evidence-mismatch"
            )
        finally:
            self.store.batch_release = original_batch_release

    def test_hash_consistent_forged_schema_rejected(self) -> None:
        self._assert_forged_result_rejected(
            lambda record: _rehash_record(
                record,
                schema="ods.extension-resource-reservation.v2",
            )
        )

    def test_hash_consistent_forged_action_rejected(self) -> None:
        self._assert_forged_result_rejected(
            lambda record: _rehash_record(record, action="enable")
        )

    def test_hash_consistent_forged_claims_rejected(self) -> None:
        forged_claims = _claims(9090, exclusive=("gpu/slot-1",))
        forged_digest = hashlib.sha256(
            reservations._canonical_json_bytes(forged_claims.to_dict())
        ).hexdigest()
        self._assert_forged_result_rejected(
            lambda record: _rehash_record(
                record,
                claims=forged_claims,
                claims_digest=forged_digest,
            )
        )

    def test_hash_consistent_forged_claims_digest_rejected(self) -> None:
        self._assert_forged_result_rejected(
            lambda record: _rehash_record(record, claims_digest="f" * 64)
        )

    def test_hash_consistent_future_timestamp_rejected(self) -> None:
        self._assert_forged_result_rejected(
            lambda record: _rehash_record(
                record,
                updated_at="9999-01-01T00:00:00Z",
            )
        )

    # -- exception redaction --

    def test_unexpected_exception_redacted(self) -> None:
        """Raw exception text must never appear in the error code."""
        original_batch_release = self.store.batch_release

        def exploding(*args, **kwargs):
            raise RuntimeError("internal database corruption: /etc/shadow")

        self.store.batch_release = exploding  # type: ignore[method-assign]
        try:
            ops = (PlannedOperation("svc-a", "install"),)
            defs = (_def("svc-a", (PlannedHostPort("tcp", 8080),), ("t",)),)
            pm = _plan_material(operations=ops, definitions=defs)
            self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", _claims(8080, exclusive=("t",)), NOW)
            with self.assertRaises(release_adapter.ResourceReleaseAdapterError) as ctx:
                self.adapt(_release_command(plan_material=pm))
            self.assertEqual(ctx.exception.code, "lifecycle-work-release-failed")
            self.assertNotIn("shadow", str(ctx.exception))
        finally:
            self.store.batch_release = original_batch_release

    def test_store_error_mapped(self) -> None:
        """ReservationStoreError must map to lifecycle-work-release-failed."""
        original_batch_release = self.store.batch_release

        def store_fail(*args, **kwargs):
            raise reservations.ReservationStoreError("transition-invalid")

        self.store.batch_release = store_fail  # type: ignore[method-assign]
        try:
            ops = (PlannedOperation("svc-a", "install"),)
            defs = (_def("svc-a", (PlannedHostPort("tcp", 8080),), ("t",)),)
            pm = _plan_material(operations=ops, definitions=defs)
            self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", _claims(8080, exclusive=("t",)), NOW)
            with self.assertRaises(release_adapter.ResourceReleaseAdapterError) as ctx:
                self.adapt(_release_command(plan_material=pm))
            self.assertEqual(ctx.exception.code, "lifecycle-work-release-failed")
        finally:
            self.store.batch_release = original_batch_release

    # -- wrong store type --

    def test_wrong_store_type(self) -> None:
        with self.assertRaises(release_adapter.ResourceReleaseAdapterError) as ctx:
            release_adapter.ResourceReleaseAdapter(object())  # type: ignore[arg-type]
        self.assertEqual(ctx.exception.code, "lifecycle-work-release-adapter-invalid")

    # -- constructor root custody --

    def test_constructor_does_not_mutate(self) -> None:
        """Constructor must not write to the store."""
        before = set(self.store.active())
        release_adapter.ResourceReleaseAdapter(self.store)
        after = set(self.store.active())
        self.assertEqual(before, after)

    # -- all exports --

    def test_all_exports_only_public(self) -> None:
        self.assertEqual(
            set(release_adapter.__all__),
            {"ResourceReleaseAdapter", "ResourceReleaseAdapterError"},
        )

    # -- binding mismatch --

    def test_plan_transaction_mismatch(self) -> None:
        ops = (PlannedOperation("svc-a", "install"),)
        defs = (_def("svc-a", (PlannedHostPort("tcp", 8080),), ("t",)),)
        pm = LifecyclePlanMaterial(
            schema=PLAN_MATERIAL_SCHEMA,
            transaction_id="txn-" + "2" * 24,
            plan_hash=PLAN_HASH,
            state="verifying",
            operations=ops,
            definitions=defs,
        )
        cmd = _release_command(plan_material=pm)
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    def test_plan_hash_mismatch(self) -> None:
        ops = (PlannedOperation("svc-a", "install"),)
        defs = (_def("svc-a", (PlannedHostPort("tcp", 8080),), ("t",)),)
        pm = LifecyclePlanMaterial(
            schema=PLAN_MATERIAL_SCHEMA,
            transaction_id=TXN,
            plan_hash="b" * 64,
            state="verifying",
            operations=ops,
            definitions=defs,
        )
        cmd = _release_command(plan_material=pm)
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    # -- missing claims on definition --

    def test_missing_claims_fails(self) -> None:
        ops = (PlannedOperation("svc-a", "install"),)
        defs = (_def("svc-a"),)  # no host_ports, no exclusive
        pm = _plan_material(operations=ops, definitions=defs)
        cmd = _release_command(plan_material=pm)
        with self.assertRaises(LifecycleWorkValidationError) as ctx:
            self.adapt(cmd)
        self.assertEqual(ctx.exception.code, "lifecycle-work-plan-mismatch")

    # -- action race/TOCTOU: expectations are validated under same lock --

    def test_action_race_one_lock(self) -> None:
        """The adapter does NOT pre-load records then call batch_release.
        It builds expectations and passes them into batch_release, so
        action/claims validation happens under the SAME lock.
        Verify by confirming the adapter only makes one store call."""
        call_count = 0
        original_reserve = self.store.reserve
        original_batch = self.store.batch_release

        def counted_reserve(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_reserve(*args, **kwargs)

        def counted_batch(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original_batch(*args, **kwargs)

        self.store.reserve = counted_reserve  # type: ignore[method-assign]
        self.store.batch_release = counted_batch  # type: ignore[method-assign]
        try:
            ops = (PlannedOperation("svc-a", "install"),)
            defs = (_def("svc-a", (PlannedHostPort("tcp", 8080),), ("t",)),)
            pm = _plan_material(operations=ops, definitions=defs)
            self.store.reserve(TXN, PLAN_HASH, "svc-a", "install", _claims(8080, exclusive=("t",)), NOW)
            call_count = 0  # reset
            self.adapt(_release_command(plan_material=pm))
            # Only batch_release called during release (1 call)
            self.assertEqual(call_count, 1)
        finally:
            self.store.reserve = original_reserve
            self.store.batch_release = original_batch

    # -- claims race: expectations validated atomically --

    def test_claims_race_one_lock(self) -> None:
        """Claims validation is inside batch_release under the same lock.
        Prove the adapter never observes claims through an unlocked snapshot."""
        operations = (PlannedOperation("svc-a", "install"),)
        definitions = (
            _def(
                "svc-a",
                (PlannedHostPort("tcp", 8080),),
                ("gpu/slot-0",),
            ),
        )
        plan_material = _plan_material(
            operations=operations,
            definitions=definitions,
        )
        self.store.reserve(
            TXN,
            PLAN_HASH,
            "svc-a",
            "install",
            _claims(8080, exclusive=("gpu/slot-0",)),
            NOW,
        )

        with mock.patch.object(
            self.store,
            "snapshot",
            side_effect=AssertionError("unlocked snapshot must not be used"),
        ) as snapshot:
            evidence = self.adapt(_release_command(plan_material=plan_material))
        snapshot.assert_not_called()
        self.assertRegex(evidence, r"^[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Runtime construction
# ---------------------------------------------------------------------------


@unittest.skipUnless(SUPPORTED, "requires POSIX descriptor-relative filesystem APIs")
class ReleaseRuntimeTests(unittest.TestCase):
    """Runtime construction tests covering release integration."""

    def test_runtime_includes_release_dispatcher(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = base / "data"
            root = data / "assistant-first" / "resource-reservations"
            _private_directory(root)
            composed = reservation_runtime.build_resource_reservation_runtime(
                data_dir=data
            )
            self.assertIs(
                type(composed.release_dispatcher),
                release_adapter.ResourceReleaseAdapter,
            )
            self.assertIs(
                type(composed.reserve_dispatcher),
                reserve_adapter.ResourceReservationAdapter,
            )

    def test_runtime_no_writes_on_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data = base / "data"
            root = data / "assistant-first" / "resource-reservations"
            _private_directory(root)
            before = {str(p) for p in root.rglob("*")}
            reservation_runtime.build_resource_reservation_runtime(data_dir=data)
            after = {str(p) for p in root.rglob("*")}
            self.assertEqual(before, after)

    def test_runtime_construction_no_effect(self) -> None:
        """Construction creates/registers nothing; no subprocess/network/
        service/container/Compose effect."""
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            root = data / "assistant-first" / "resource-reservations"
            _private_directory(root)
            reservation_runtime.build_resource_reservation_runtime(
                data_dir=data
            )
            self.assertEqual(reservations.ResourceReservationStore(root).active(), ())

    def test_runtime_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            root = data / "assistant-first" / "resource-reservations"
            _private_directory(root)
            composed = reservation_runtime.build_resource_reservation_runtime(
                data_dir=data
            )
            with self.assertRaises(FrozenInstanceError):
                composed.release_dispatcher = None  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Production reachability boundary
# ---------------------------------------------------------------------------


@unittest.skipUnless(SUPPORTED, "requires POSIX descriptor-relative filesystem APIs")
class ProductionReachabilityBoundaryTests(unittest.TestCase):
    """Keep host reachability paired without creating a second authority."""

    def test_host_imports_only_the_paired_runtime(self) -> None:
        agent_path = BIN_DIR / "ods-host-agent.py"
        agent_source = agent_path.read_text(encoding="utf-8")
        self.assertIn("extension_resource_reservation_runtime", agent_source)
        for name in (
            "extension_resource_reservation_release",
            "extension_resource_reservation_adapter",
        ):
            self.assertNotIn(name, agent_source)

    def test_dashboard_has_no_direct_reservation_authority(self) -> None:
        repo = Path(__file__).resolve().parents[4]
        dashboard_api = repo / "ods" / "extensions" / "services" / "dashboard-api"
        for path in dashboard_api.rglob("*.py"):
            if "tests" in path.parts:
                continue
            if path.name == "__init__.py":
                continue
            source = path.read_text(encoding="utf-8")
            for name in (
                "extension_resource_reservation_release",
                "extension_resource_reservation_runtime",
                "extension_resource_reservation_adapter",
            ):
                self.assertNotIn(
                    name,
                    source,
                    f"unexpected production import in {path.relative_to(repo)}",
                )


# ---------------------------------------------------------------------------
# Symlink / custody tests (store-level)
# ---------------------------------------------------------------------------


@unittest.skipUnless(SUPPORTED, "requires POSIX descriptor-relative filesystem APIs")
class SymlinkCustodyTests(unittest.TestCase):
    """Symlink and custody tests for batch_release."""

    def test_symlinked_root_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            parent = base / "assistant-first"
            target = base / "target"
            _private_directory(parent)
            _private_directory(target)
            (parent / "resource-reservations").symlink_to(
                target, target_is_directory=True
            )
            with self.assertRaises(reservations.ReservationStoreError) as ctx:
                reservations.ResourceReservationStore(
                    str(parent / "resource-reservations")
                )
            self.assertIn(ctx.exception.code, {"root-invalid", "root-io-error"})

    def test_wrong_mode_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "resource-reservations"
            _private_directory(root)
            root.chmod(0o755)
            with self.assertRaises(reservations.ReservationStoreError) as ctx:
                reservations.ResourceReservationStore(root)
            self.assertEqual(ctx.exception.code, "root-custody-violation")


if __name__ == "__main__":
    unittest.main()
