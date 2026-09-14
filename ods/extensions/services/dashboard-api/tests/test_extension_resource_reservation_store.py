from __future__ import annotations

import ast
import json
import multiprocessing
import os
import stat
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BIN_DIR = Path(__file__).resolve().parents[4] / "bin"
ODS_ROOT = Path(__file__).resolve().parents[4]
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

SUPPORTED = (
    os.name == "posix"
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
)
if SUPPORTED:
    import extension_resource_reservation_store as reservations
else:
    reservations = None  # type: ignore[assignment]

NOW = "2026-09-13T12:00:00Z"
LATER = "2026-09-13T13:00:00Z"
PLAN_A = "a" * 64
PLAN_B = "b" * 64


def txn(char: str) -> str:
    return "txn-" + char * 24


def empty_claims() -> reservations.ReservationClaims:
    return reservations.ReservationClaims(host_ports=(), exclusive=())


def claims(
    port: int,
    *,
    protocol: str = "tcp",
    exclusive: tuple[str, ...] = (),
) -> reservations.ReservationClaims:
    return reservations.ReservationClaims(
        host_ports=(reservations.HostPort(port=port, protocol=protocol),),
        exclusive=exclusive,
    )


def reserve_conflicting_process(root: str, marker: str, start, queue) -> None:
    try:
        store = reservations.ResourceReservationStore(root)
        start.wait()
        record = store.reserve(
            txn(marker),
            marker * 64,
            f"service-{marker}",
            "install",
            claims(48123, exclusive=("gpu/slot-0",)),
            NOW,
        )
        queue.put(("ok", record.transaction_id))
    except reservations.ReservationStoreError as exc:
        queue.put(("error", exc.code))


@unittest.skipUnless(SUPPORTED, "requires POSIX descriptor-relative filesystem APIs")
class ResourceReservationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "resource-reservations"
        self.root.mkdir(mode=0o700)
        self.root.chmod(0o700)
        self.store = reservations.ResourceReservationStore(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @property
    def snapshot_path(self) -> Path:
        return self.root / reservations.SNAPSHOT_NAME

    def assert_code(self, code: str, call) -> None:
        with self.assertRaises(reservations.ReservationStoreError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def test_reserve_replay_conflict_finish_and_release(self) -> None:
        first_claims = claims(8080, exclusive=("network/private",))
        first = self.store.reserve(
            txn("a"), PLAN_A, "alpha", "install", first_claims, NOW
        )
        self.assertFalse(first.duplicate)
        self.assertEqual(first.status, reservations.ACTIVE)
        self.assertEqual(stat.S_IMODE(self.snapshot_path.stat().st_mode), 0o600)

        replay = self.store.reserve(
            txn("a"), PLAN_A, "alpha", "install", first_claims, LATER
        )
        self.assertTrue(replay.duplicate)
        self.assertEqual(replay.record_sha256, first.record_sha256)
        self.assert_code(
            "resource-reservation-binding-conflict",
            lambda: self.store.reserve(
                txn("a"), PLAN_A, "alpha", "enable", first_claims, NOW
            ),
        )
        self.assert_code(
            "resource-reservation-binding-conflict",
            lambda: self.store.reserve(
                txn("a"), PLAN_A, "alpha", "install", claims(8081), NOW
            ),
        )
        self.assert_code(
            "resource-reservation-claim-conflict",
            lambda: self.store.reserve(
                txn("b"), PLAN_B, "beta", "install", claims(8080), NOW
            ),
        )
        self.assert_code(
            "resource-reservation-claim-conflict",
            lambda: self.store.reserve(
                txn("b"),
                PLAN_B,
                "beta",
                "install",
                claims(8081, exclusive=("network/private",)),
                NOW,
            ),
        )

        finished = self.store.finish(
            txn("a"), PLAN_A, "alpha", reservations.RELEASED, LATER
        )
        self.assertEqual(finished.status, reservations.RELEASED)
        terminal_replay = self.store.finish(
            txn("a"), PLAN_A, "alpha", reservations.RELEASED, LATER
        )
        self.assertTrue(terminal_replay.duplicate)
        self.assert_code(
            "resource-reservation-terminal-duplicate",
            lambda: self.store.reserve(
                txn("a"), PLAN_A, "alpha", "install", first_claims, NOW
            ),
        )
        replacement = self.store.reserve(
            txn("b"), PLAN_B, "beta", "install", claims(8080), LATER
        )
        self.assertEqual(replacement.status, reservations.ACTIVE)

    def test_persisted_records_have_canonical_binding_order(self) -> None:
        self.store.reserve(txn("f"), "f" * 64, "zeta", "install", empty_claims(), NOW)
        self.store.reserve(txn("1"), "1" * 64, "alpha", "install", empty_claims(), NOW)
        payload = json.loads(self.snapshot_path.read_bytes())
        keys = [
            (record["transactionId"], record["planHash"], record["serviceId"])
            for record in payload["records"]
        ]
        self.assertEqual(keys, sorted(keys))

        payload["records"].reverse()
        self.snapshot_path.write_bytes(reservations._canonical_json_bytes(payload))
        self.assert_code("corrupt-record-order", self.store.active)

    def test_finish_time_regression_preserves_valid_snapshot(self) -> None:
        self.store.reserve(txn("a"), PLAN_A, "alpha", "install", empty_claims(), LATER)
        before = self.snapshot_path.read_bytes()
        self.assert_code(
            "timestamp-invalid",
            lambda: self.store.finish(
                txn("a"), PLAN_A, "alpha", reservations.FAILED, NOW
            ),
        )
        self.assertEqual(self.snapshot_path.read_bytes(), before)
        self.assertEqual(self.store.active()[0].status, reservations.ACTIVE)

    def test_invalid_candidate_is_rejected_before_temp_allocation(self) -> None:
        high = reservations._record_to_dict(
            reservations._build_record(
                txn("f"),
                "f" * 64,
                "zeta",
                "install",
                empty_claims(),
                reservations.ACTIVE,
                NOW,
                NOW,
            )
        )
        low = reservations._record_to_dict(
            reservations._build_record(
                txn("1"),
                "1" * 64,
                "alpha",
                "install",
                empty_claims(),
                reservations.ACTIVE,
                NOW,
                NOW,
            )
        )
        root_fd = reservations._open_root(self.store._root_parts)
        try:
            self.assert_code(
                "corrupt-record-order",
                lambda: reservations._write_snapshot(root_fd, [high, low]),
            )
        finally:
            os.close(root_fd)
        self.assertFalse(self.snapshot_path.exists())
        self.assertEqual(list(self.root.glob("tmp-*.tmp")), [])

    def test_nonmatching_temp_identity_does_not_replace_prior_snapshot(self) -> None:
        self.store.reserve(txn("a"), PLAN_A, "alpha", "install", empty_claims(), NOW)
        before = self.snapshot_path.read_bytes()
        real_stat = os.stat

        def mismatching_stat(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            if isinstance(path, str) and path.startswith(reservations.TEMP_PREFIX):
                return SimpleNamespace(
                    st_dev=result.st_dev,
                    st_ino=result.st_ino + 1,
                    st_mode=result.st_mode,
                    st_nlink=result.st_nlink,
                    st_uid=result.st_uid,
                    st_size=result.st_size,
                    st_mtime_ns=result.st_mtime_ns,
                    st_ctime_ns=result.st_ctime_ns,
                )
            return result

        with mock.patch.object(reservations.os, "stat", side_effect=mismatching_stat):
            self.assert_code(
                "snapshot-integrity",
                lambda: self.store.reserve(
                    txn("b"), PLAN_B, "beta", "install", empty_claims(), NOW
                ),
            )
        self.assertEqual(self.snapshot_path.read_bytes(), before)
        self.assertEqual(self.store.active()[0].service_id, "alpha")

    def test_total_record_cap_includes_terminal_records(self) -> None:
        with mock.patch.object(reservations, "MAX_RECORDS", 2):
            self.store.reserve(
                txn("1"), "1" * 64, "one", "install", empty_claims(), NOW
            )
            self.store.reserve(
                txn("2"), "2" * 64, "two", "install", empty_claims(), NOW
            )
            self.assert_code(
                "oversize-records",
                lambda: self.store.reserve(
                    txn("3"), "3" * 64, "three", "install", empty_claims(), NOW
                ),
            )
            self.store.finish(txn("1"), "1" * 64, "one", reservations.FAILED, LATER)
            self.assert_code(
                "oversize-records",
                lambda: self.store.reserve(
                    txn("3"), "3" * 64, "three", "install", empty_claims(), NOW
                ),
            )

    def test_two_processes_cannot_claim_the_same_resources(self) -> None:
        context = multiprocessing.get_context("fork")
        start = context.Event()
        queue = context.Queue()
        workers = [
            context.Process(
                target=reserve_conflicting_process,
                args=(str(self.root), marker, start, queue),
            )
            for marker in ("a", "b")
        ]
        for worker in workers:
            worker.start()
        start.set()
        results = [queue.get(timeout=10) for _ in workers]
        for worker in workers:
            worker.join(timeout=10)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual([kind for kind, _ in results].count("ok"), 1)
        self.assertEqual(
            results.count(("error", "resource-reservation-claim-conflict")), 1
        )
        self.assertEqual(len(self.store.active()), 1)

    def test_strict_json_digests_and_custody_fail_closed(self) -> None:
        self.store.reserve(txn("a"), PLAN_A, "alpha", "install", empty_claims(), NOW)
        raw = self.snapshot_path.read_bytes()
        self.snapshot_path.write_bytes(raw.rstrip(b"\n"))
        self.assert_code("snapshot-noncanonical", self.store.active)

        self.snapshot_path.write_bytes(
            b'{"schema":"ods.extension-resource-reservations.v1",'
            b'"schema":"duplicate","records":[]}\n'
        )
        self.assert_code("corrupt-snapshot-json", self.store.active)
        self.snapshot_path.write_bytes(raw)

        data = json.loads(raw)
        data["records"][0]["action"] = "enable"
        self.snapshot_path.write_bytes(reservations._canonical_json_bytes(data))
        self.assert_code("corrupt-record-sha", self.store.active)

    def test_argument_claim_and_root_validation(self) -> None:
        self.root.rename(self.root.with_name("moved"))
        self.assert_code(
            "binding-invalid",
            lambda: self.store.snapshot("txn-invalid", PLAN_A, "alpha"),
        )

        with self.assertRaises(ValueError):
            reservations.ReservationClaims(
                host_ports=(reservations.HostPort(port=True, protocol="tcp"),),
                exclusive=(),
            )
        with self.assertRaises(ValueError):
            reservations.ReservationClaims(
                host_ports=(
                    reservations.HostPort(port=8081, protocol="tcp"),
                    reservations.HostPort(port=8080, protocol="tcp"),
                ),
                exclusive=(),
            )

        unsafe = Path(self.temp.name) / "unsafe"
        unsafe.mkdir(mode=0o755)
        unsafe.chmod(0o755)
        self.assert_code(
            "root-custody-violation",
            lambda: reservations.ResourceReservationStore(unsafe),
        )
        self.assert_code(
            "root-missing",
            lambda: reservations.ResourceReservationStore(
                Path(self.temp.name) / "missing"
            ),
        )

    def test_values_are_frozen_and_public_surface_is_complete(self) -> None:
        record = self.store.reserve(
            txn("a"), PLAN_A, "alpha", "install", empty_claims(), NOW
        )
        with self.assertRaises(FrozenInstanceError):
            record.status = reservations.FAILED  # type: ignore[misc]
        self.assertIn("HostPort", reservations.__all__)

    def test_store_has_no_production_importer_yet(self) -> None:
        offenders: list[str] = []
        roots = [
            ODS_ROOT / "bin",
            ODS_ROOT / "extensions" / "services" / "dashboard-api",
        ]
        for root in roots:
            for path in root.rglob("*.py"):
                if (
                    path.name == "extension_resource_reservation_store.py"
                    or "tests" in path.parts
                ):
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import) and any(
                        alias.name == "extension_resource_reservation_store"
                        for alias in node.names
                    ):
                        offenders.append(str(path.relative_to(ODS_ROOT)))
                    if (
                        isinstance(node, ast.ImportFrom)
                        and node.module == "extension_resource_reservation_store"
                    ):
                        offenders.append(str(path.relative_to(ODS_ROOT)))
        allowed = {
            "bin/extension_resource_reservation_adapter.py",
            "bin/extension_resource_reservation_runtime.py",
        }
        self.assertEqual(set(offenders), allowed)


if __name__ == "__main__":
    unittest.main()
