"""Focused regression tests for restore-migration-journal.py hardening.

Covers the atomic journal write's best-effort O_EXCL temp cleanup: if a write/fsync/
close failure happens before rename, the temp must be unlinked without masking the
original failure. Also asserts the exact hadOld 0/1 parsing helper.
"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "pixel_test_restore_journal_hardening", ROOT / "scripts/restore-migration-journal.py",
)
helper = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(helper)


def temp_files(custody: str):
    return [name for name in os.listdir(custody) if name.startswith(".pixel-journal-")]


class WriteJournalTempCleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.custody = Path(self.tmp.name)
        os.chmod(self.custody, 0o700)
        self.custody_fd = os.open(self.custody, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.custody_fd)
        self.addCleanup(self.tmp.cleanup)
        self.value = {
            "schemaVersion": 1, "kind": "pixel-restore-migration-reserved",
            "reservationToken": "a" * 32, "contractSha256": "b" * 64,
            "backupSha256": "c" * 64, "sourcePixel": "3.2.2", "targetPixel": "4.3.9",
        }

    def assert_no_temp_left(self):
        self.assertEqual(temp_files(str(self.custody)), [])

    def test_happy_path_replaces_journal_and_leaves_no_temp(self):
        helper._write_journal(self.custody_fd, "journal.json", self.value)
        self.assert_no_temp_left()
        self.assertEqual(
            json.loads((self.custody / "journal.json").read_text()),
            self.value,
        )

    def test_write_failure_unlinks_temp_without_masking_error(self):
        def failing_write(fd, view):
            raise OSError("simulated journal write failure")

        with mock.patch("os.write", side_effect=failing_write):
            with self.assertRaises(OSError) as ctx:
                helper._write_journal(self.custody_fd, "journal.json", self.value)
        self.assertIn("simulated journal write failure", str(ctx.exception))
        self.assert_no_temp_left()

    def test_fsync_failure_unlinks_temp_without_masking_error(self):
        with mock.patch("os.fsync", side_effect=OSError("simulated fsync failure")):
            with self.assertRaises(OSError) as ctx:
                helper._write_journal(self.custody_fd, "journal.json", self.value)
        self.assertIn("simulated fsync failure", str(ctx.exception))
        self.assert_no_temp_left()

    def test_truncated_write_fails_closed_and_cleans_temp(self):
        real_write = os.write

        def short_then_fail(fd, view):
            n = real_write(fd, view)
            raise OSError("simulated short-write tail failure")

        with mock.patch("os.write", side_effect=short_then_fail):
            with self.assertRaises(OSError):
                helper._write_journal(self.custody_fd, "journal.json", self.value)
        self.assert_no_temp_left()


class HadOldParseTests(unittest.TestCase):
    def test_accepts_only_literal_0_and_1(self):
        self.assertEqual(helper._parse_had_old("0"), 0)
        self.assertEqual(helper._parse_had_old("1"), 1)

    def test_rejects_truthy_int_coercions(self):
        for bad in ("2", "-1", "00", "true", "True", "1.0", ""):
            with self.assertRaises(SystemExit):
                helper._parse_had_old(bad)


class ReleaseIdentityLoaderTests(unittest.TestCase):
    """The journal helper's in-tree release-identity read is the same shared-concept secure
    read + strict JSON as the migration tool: O_NOFOLLOW/O_CLOEXEC, regular single-link
    bounded file, and duplicate-key rejection at any depth. It establishes coherence only,
    never authenticity."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_release_file_rejects_symlink(self):
        target = self.directory / "target"
        target.write_text("4.3.9\n", encoding="utf-8")
        path = self.directory / "VERSION"
        os.symlink(str(target), path)
        with self.assertRaises(RuntimeError):
            helper._read_release_file(str(self.directory), "VERSION", maximum=64)

    def test_read_release_file_rejects_hardlink(self):
        target = self.directory / "target"
        target.write_text("4.3.9\n", encoding="utf-8")
        os.link(str(target), str(self.directory / "VERSION"))
        with self.assertRaises(RuntimeError):
            helper._read_release_file(str(self.directory), "VERSION", maximum=64)

    def test_parse_release_manifest_rejects_duplicate_keys(self):
        payload = b'{"pixel":"4.3.9","pixel":"4.2.0"}'
        with self.assertRaises(RuntimeError):
            helper._parse_release_manifest(payload)

    def test_parse_release_manifest_rejects_nested_duplicate_keys(self):
        payload = b'{"a":{"b":1,"b":2}}'
        with self.assertRaises(RuntimeError):
            helper._parse_release_manifest(payload)


if __name__ == "__main__":
    unittest.main()
