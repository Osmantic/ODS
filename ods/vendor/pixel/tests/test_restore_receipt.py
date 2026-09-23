import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/restore-receipt.py"

SPEC = importlib.util.spec_from_file_location(
    "pixel_test_restore_receipt", ROOT / "scripts/migrate-legacy-clean.py",
)
migration = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(migration)

HELPER_SPEC = importlib.util.spec_from_file_location(
    "pixel_test_restore_receipt_helper", ROOT / "scripts/restore-receipt.py",
)
receipt_helper = importlib.util.module_from_spec(HELPER_SPEC)
assert HELPER_SPEC.loader is not None
HELPER_SPEC.loader.exec_module(receipt_helper)

BACKUP = "a" * 64
RESERVATION_JSON = {
    "schemaVersion": 1,
    "kind": "pixel-restore-receipt-reservation",
    "status": "reserved",
    "operation": "restore",
    "generatedAt": "2026-08-22T12:00:00Z",
    "reservationId": "a" * 32,
    "dev": 0,
    "ino": 0,
    "nlink": 1,
    "fileMode": 0o600,
    "markerSha256": None,
}


def reservation_json(**overrides):
    marker = dict(RESERVATION_JSON)
    marker.update(overrides)
    body = {key: child for key, child in marker.items() if key != "markerSha256"}
    marker["markerSha256"] = migration.self_hash(marker, "markerSha256")
    return json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n"


def write_reservation(path, content):
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


class RestoreReceiptHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.dir.chmod(0o700)
        self.repo = self.dir / "repo"
        self.repo.mkdir()
        self.parent = self.dir / "out"
        self.parent.mkdir()
        self.parent.chmod(0o700)
        self.receipt = self.parent / "receipt.json"

    def tearDown(self):
        self.tmp.cleanup()

    def run_helper(self, *args):
        return subprocess.run(
            ["python3", str(HELPER), *map(str, args)], capture_output=True, text=True,
        )

    def reserve(self):
        return self.run_helper("reserve", self.receipt, self.repo)

    def finalize(self, knowledge="0"):
        return self.run_helper(
            "finalize", self.receipt, BACKUP, "3.2.2", "4.3.27", self.repo, knowledge,
        )

    def test_reserve_creates_non_passing_reservation(self):
        self.assertEqual(self.reserve().returncode, 0)
        self.assertTrue(self.receipt.exists())
        value = json.loads(self.receipt.read_text(encoding="utf-8"))
        self.assertEqual(value["kind"], "pixel-restore-receipt-reservation")
        self.assertEqual(value["status"], "reserved")
        self.assertEqual(value["operation"], "restore")
        self.assertEqual(value["fileMode"], 0o600)
        self.assertNotIn("mode", value)
        with self.assertRaises(migration.MigrationError):
            migration.validate_restore_receipt(value)

    def test_reserve_writes_strict_identity_and_timestamp(self):
        self.assertEqual(self.reserve().returncode, 0)
        value = json.loads(self.receipt.read_text(encoding="utf-8"))
        self.assertRegex(value["reservationId"], r"^[a-f0-9]{32}$")
        self.assertRegex(value["generatedAt"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")

    def test_reserve_write_failure_leaves_no_partial_reservation(self):
        with mock.patch.object(
            receipt_helper.os, "write", side_effect=OSError("injected write failure"),
        ):
            with self.assertRaises(OSError):
                receipt_helper.reserve(str(self.receipt), str(self.repo))
        self.assertFalse(self.receipt.exists())

    def test_reserve_fsync_failure_leaves_no_partial_reservation(self):
        with mock.patch.object(
            receipt_helper.os, "fsync", side_effect=OSError("injected fsync failure"),
        ):
            with self.assertRaises(OSError):
                receipt_helper.reserve(str(self.receipt), str(self.repo))
        self.assertFalse(self.receipt.exists())

    def test_reserve_attacker_replacement_never_removed(self):
        replacement = self.parent / "attacker.json"
        payload = b"attacker-payload"
        replacement.write_bytes(payload)
        replacement.chmod(0o600)
        replaced = {"done": False}

        def hook(path):
            if not replaced["done"]:
                os.replace(replacement, path)
                replaced["done"] = True

        old_hook = receipt_helper._RESERVE_PRE_WRITE_HOOK
        receipt_helper._RESERVE_PRE_WRITE_HOOK = hook
        try:
            with mock.patch.object(
                receipt_helper.os, "write", side_effect=OSError("injected write failure"),
            ):
                with self.assertRaises(receipt_helper.ReceiptError):
                    receipt_helper.reserve(str(self.receipt), str(self.repo))
        finally:
            receipt_helper._RESERVE_PRE_WRITE_HOOK = old_hook
        self.assertTrue(replaced["done"])
        self.assertTrue(self.receipt.exists())
        self.assertEqual(self.receipt.read_bytes(), payload)

    def test_finalize_rejects_bad_backup_sha(self):
        self.assertEqual(self.reserve().returncode, 0)
        for bad in ("", "x" * 63, "X" * 64, "g" * 64):
            result = self.run_helper(
                "finalize", self.receipt, bad, "3.2.2", "4.3.27", self.repo, "0",
            )
            self.assertNotEqual(result.returncode, 0)

    def test_finalize_rejects_bad_pixel_versions(self):
        self.assertEqual(self.reserve().returncode, 0)
        # Source substitution, target substitution (legacy 4.2.0), wrong target, malformed
        # target must all fail closed against the derived 4.3.27 release target.
        for bad in (("3.2.1", "4.3.27"), ("3.2.2", "4.2.0"), ("3.2.2", "4.3.2"), ("3.2.2", "4.3.27 ")):
            result = self.run_helper(
                "finalize", self.receipt, BACKUP, bad[0], bad[1], self.repo, "0",
            )
            self.assertNotEqual(result.returncode, 0)

    def test_finalize_rejects_missing_or_unnormalized_repo(self):
        self.assertEqual(self.reserve().returncode, 0)
        missing = self.dir / "missing-repo"
        result = self.run_helper(
            "finalize", self.receipt, BACKUP, "3.2.2", "4.3.27", missing, "0",
        )
        self.assertNotEqual(result.returncode, 0)
        link_repo = self.dir / "repo-link"
        os.symlink(self.repo, link_repo)
        result = self.run_helper(
            "finalize", self.receipt, BACKUP, "3.2.2", "4.3.27", link_repo, "0",
        )
        self.assertNotEqual(result.returncode, 0)

    def test_finalize_rejects_non_boolean_flag(self):
        self.assertEqual(self.reserve().returncode, 0)
        for bad in ("2", "true", "yes", ""):
            result = self.run_helper(
                "finalize", self.receipt, BACKUP, "3.2.2", "4.3.27", self.repo, bad,
            )
            self.assertNotEqual(result.returncode, 0)

    def test_finalize_refuses_oversized_reservation(self):
        self.assertEqual(self.reserve().returncode, 0)
        self.receipt.write_bytes(b"x" * (receipt_helper.MAX_MARKER_BYTES + 100))
        self.assertNotEqual(self.finalize().returncode, 0)

    def test_finalize_refuses_duplicate_marker_fields(self):
        write_reservation(
            self.receipt,
            '{"kind":"pixel-restore-receipt-reservation","kind":"pixel-restore-receipt-reservation"}',
        )
        self.assertNotEqual(self.finalize().returncode, 0)

    def test_finalize_refuses_extra_marker_field(self):
        write_reservation(self.receipt, reservation_json(extra=1))
        self.assertNotEqual(self.finalize().returncode, 0)

    def test_finalize_refuses_missing_marker_field(self):
        marker = dict(RESERVATION_JSON)
        marker["markerSha256"] = None
        del marker["operation"]
        body = {key: child for key, child in marker.items() if key != "markerSha256"}
        marker["markerSha256"] = migration.self_hash(marker, "markerSha256")
        write_reservation(
            self.receipt, json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n",
        )
        self.assertNotEqual(self.finalize().returncode, 0)

    def test_finalize_refuses_malformed_reservation_fields(self):
        cases = (
            {"reservationId": ""},
            {"reservationId": 5},
            {"reservationId": "A" * 32},
            {"reservationId": "a" * 31},
            {"generatedAt": "2026-08-22T12:00:00+00:00"},
            {"generatedAt": "2026-08-22T12:00:00"},
            {"dev": "x"},
            {"dev": -1},
            {"dev": True},
            {"ino": "x"},
            {"ino": -1},
            {"ino": True},
            {"nlink": True},
            {"fileMode": 0o644},
            {"fileMode": True},
            {"nlink": 2},
            {"status": "pass"},
            {"operation": "other"},
            {"schemaVersion": 2},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                self.receipt.write_text("", encoding="utf-8")
                write_reservation(self.receipt, reservation_json(**overrides))
                self.assertNotEqual(self.finalize().returncode, 0)

    def test_finalize_refuses_replaced_regular_file_without_truncating(self):
        self.assertEqual(self.reserve().returncode, 0)
        replacement = self.parent / "replacement.json"
        payload = b"A" * 128
        replacement.write_bytes(payload)
        replacement.chmod(0o600)
        os.replace(replacement, self.receipt)
        self.assertNotEqual(self.finalize().returncode, 0)
        self.assertEqual(self.receipt.read_bytes(), payload)

    def test_finalize_replacement_between_read_and_rewrite_not_truncated(self):
        self.assertEqual(self.reserve().returncode, 0)
        replacement = self.parent / "swap.json"
        payload = b"B" * 256
        replacement.write_bytes(payload)
        replacement.chmod(0o600)
        calls = {"done": False}

        def hook(path):
            if not calls["done"]:
                os.replace(replacement, path)
                calls["done"] = True

        old_hook = receipt_helper._PRE_TRUNCATE_HOOK
        receipt_helper._PRE_TRUNCATE_HOOK = hook
        try:
            with self.assertRaises(receipt_helper.ReceiptError):
                receipt_helper.finalize(
                    str(self.receipt), BACKUP, "3.2.2", "4.3.27", str(self.repo), False,
                )
        finally:
            receipt_helper._PRE_TRUNCATE_HOOK = old_hook
        self.assertTrue(calls["done"])
        self.assertEqual(self.receipt.read_bytes(), payload)

    def test_reserve_refuses_overwrite(self):
        self.receipt.write_text("occupied", encoding="utf-8")
        result = self.reserve()
        self.assertNotEqual(result.returncode, 0)

    def test_reserve_refuses_symlink(self):
        target = self.dir / "target.json"
        target.write_text("x", encoding="utf-8")
        os.symlink(target, self.receipt)
        self.assertNotEqual(self.reserve().returncode, 0)

    def test_reserve_refuses_hardlink(self):
        other = self.dir / "other.json"
        other.write_text("x", encoding="utf-8")
        os.link(other, self.receipt)
        self.assertNotEqual(self.reserve().returncode, 0)

    def test_reserve_refuses_unsafe_parent(self):
        self.parent.chmod(0o755)
        result = self.reserve()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.receipt.exists())

    def test_abort_removes_only_exact_reservation(self):
        self.assertEqual(self.reserve().returncode, 0)
        self.assertEqual(self.run_helper("abort", self.receipt, self.repo).returncode, 0)
        self.assertFalse(self.receipt.exists())

    def test_abort_refuses_to_remove_pass_receipt(self):
        self.assertEqual(self.reserve().returncode, 0)
        self.assertEqual(self.finalize().returncode, 0)
        result = self.run_helper("abort", self.receipt, self.repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.receipt.exists())

    def test_finalize_refuses_swapped_inode(self):
        self.assertEqual(self.reserve().returncode, 0)
        content = self.receipt.read_bytes()
        replacement = self.parent / "replacement.json"
        replacement.write_bytes(content)
        replacement.chmod(0o600)
        os.replace(replacement, self.receipt)
        self.assertNotEqual(self.finalize().returncode, 0)

    def test_finalize_refuses_swapped_mode(self):
        self.assertEqual(self.reserve().returncode, 0)
        self.receipt.chmod(0o644)
        self.assertNotEqual(self.finalize().returncode, 0)

    def test_finalize_refuses_swapped_link(self):
        self.assertEqual(self.reserve().returncode, 0)
        extra = self.parent / "extra.json"
        os.link(self.receipt, extra)
        try:
            self.assertNotEqual(self.finalize().returncode, 0)
        finally:
            extra.unlink()

    def test_finalize_writes_exact_self_hashed_0600_receipt(self):
        self.assertEqual(self.reserve().returncode, 0)
        self.assertEqual(self.finalize().returncode, 0)
        info = self.receipt.lstat()
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(info.st_nlink, 1)
        value = json.loads(self.receipt.read_text(encoding="utf-8"))
        self.assertEqual(value["kind"], "pixel-restore-receipt")
        self.assertEqual(value["status"], "pass")
        self.assertEqual(value["mode"], "restore")
        self.assertEqual(value["backupSha256"], BACKUP)
        self.assertEqual(value["sourcePixel"], "3.2.2")
        self.assertEqual(value["targetPixel"], "4.3.27")
        self.assertEqual(value["knowledgeDeletionReconciled"], value["historicalKeyWrappingRemoved"])
        self.assertEqual(value["receiptSha256"], migration.self_hash(value, "receiptSha256"))

    def test_finalize_writes_matching_knowledge_booleans(self):
        self.assertEqual(self.reserve().returncode, 0)
        self.assertEqual(self.finalize(knowledge="1").returncode, 0)
        value = json.loads(self.receipt.read_text(encoding="utf-8"))
        self.assertIs(value["knowledgeDeletionReconciled"], True)
        self.assertIs(value["historicalKeyWrappingRemoved"], True)

    def test_no_receipt_restore_output_unchanged(self):
        source = (ROOT / "scripts/restore-private-state.sh").read_text(encoding="utf-8")
        expected_false = (
            '{"status":"pass","mode":"restore","verified":true,"automaticRollbackArmed":true,'
            '"knowledgeDeletionReconciled":false,"historicalKeyWrappingRemoved":false}'
        )
        expected_true = (
            '{"status":"pass","mode":"restore","verified":true,"automaticRollbackArmed":true,'
            '"knowledgeDeletionReconciled":true,"historicalKeyWrappingRemoved":true}'
        )
        self.assertIn(expected_false, source)
        self.assertIn(expected_true, source)
        self.assertEqual(self.reserve().returncode, 0)
        self.assertEqual(self.finalize(knowledge="0").returncode, 0)
        value = json.loads(self.receipt.read_text(encoding="utf-8"))
        self.assertIs(value["knowledgeDeletionReconciled"], False)
        self.assertIs(value["historicalKeyWrappingRemoved"], False)


class ReleaseIdentityLoaderTests(unittest.TestCase):
    """The restore-receipt helper's in-tree release-identity read is the same shared-concept
    secure read + strict JSON as the migration tool: O_NOFOLLOW/O_CLOEXEC, regular
    single-link bounded file, and duplicate-key rejection at any depth. It establishes
    coherence only, never authenticity."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_release_file_rejects_symlink(self):
        target = self.directory / "target"
        target.write_text("4.3.27\n", encoding="utf-8")
        path = self.directory / "VERSION"
        os.symlink(str(target), path)
        with self.assertRaises(RuntimeError):
            receipt_helper._read_release_file(str(self.directory), "VERSION", maximum=64)

    def test_read_release_file_rejects_hardlink(self):
        target = self.directory / "target"
        target.write_text("4.3.27\n", encoding="utf-8")
        os.link(str(target), str(self.directory / "VERSION"))
        with self.assertRaises(RuntimeError):
            receipt_helper._read_release_file(str(self.directory), "VERSION", maximum=64)

    def test_read_release_file_rejects_oversized(self):
        path = self.directory / "VERSION"
        path.write_text("x" * 65, encoding="utf-8")
        with self.assertRaises(RuntimeError):
            receipt_helper._read_release_file(str(self.directory), "VERSION", maximum=64)

    def test_parse_release_manifest_rejects_duplicate_keys(self):
        payload = b'{"pixel":"4.3.27","pixel":"4.2.0"}'
        with self.assertRaises(RuntimeError):
            receipt_helper._parse_release_manifest(payload)

    def test_parse_release_manifest_rejects_nested_duplicate_keys(self):
        payload = b'{"a":{"b":1,"b":2}}'
        with self.assertRaises(RuntimeError):
            receipt_helper._parse_release_manifest(payload)


if __name__ == "__main__":
    unittest.main()
