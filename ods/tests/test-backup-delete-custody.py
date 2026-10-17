#!/usr/bin/env python3
"""Scratch-only deletion custody regressions; no installed backup paths."""

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import pty
import select
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "delete_backup", Path(__file__).resolve().parents[1] / "scripts/delete-backup.py"
)
DELETE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DELETE)
BACKUP_ID = "backup-fixture-20261009-000000"


class DeleteCustodyTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.backups = self.root / "backups with spaces"
        self.backups.mkdir()
        self.target = self.backups / BACKUP_ID
        self.target.mkdir()
        self.manifest = {"manifest_version": "1.0", "backup_id": BACKUP_ID, "backup_type": "config"}
        (self.target / "manifest.json").write_text(json.dumps(self.manifest))
        (self.target / "config").mkdir()
        (self.target / "config/settings").write_text("original backup\n")
        self.output = io.StringIO()
        self.errors = io.StringIO()
        self.stdout = contextlib.redirect_stdout(self.output)
        self.stdout.__enter__()
        self.addCleanup(self.stdout.__exit__, None, None, None)
        self.stderr = contextlib.redirect_stderr(self.errors)
        self.stderr.__enter__()
        self.addCleanup(self.stderr.__exit__, None, None, None)

    def delete(self, confirm=lambda prompt: "y", name=BACKUP_ID):
        DELETE.delete_backup(str(self.backups), name, confirm)

    def swap(self):
        self.target.replace(self.backups / "preserved-original")
        self.target.mkdir()
        (self.target / "foreign-sentinel").write_text("foreign data\n")

    def test_replacement_during_confirmation_is_not_removed(self):
        def confirm(prompt):
            self.assertIn("[y/N]", prompt)
            self.swap()
            return "y"

        with self.assertRaisesRegex(DELETE.Refusal, "confirmation"):
            self.delete(confirm)
        self.assertEqual((self.target / "foreign-sentinel").read_text(), "foreign data\n")
        self.assertTrue((self.backups / "preserved-original/config/settings").is_file())
        self.assertEqual(list(self.backups.glob(".ods-delete-*")), [])

    def test_real_bash_cli_refuses_replacement_while_waiting_at_prompt(self):
        source = Path(__file__).resolve().parents[1]
        install = self.root / "install"
        (install / "lib").mkdir(parents=True)
        for name in ("rsync.sh", "backup-paths.sh"):
            shutil.copyfile(source / "lib" / name, install / "lib" / name)
        master, slave = pty.openpty()
        env = dict(os.environ, ODS_DIR=str(install), ODS_HOME=str(install), INSTALL_DIR=str(install))
        process = subprocess.Popen(
            ["bash", str(source / "ods-backup.sh"), "--output", str(self.backups), "--delete", BACKUP_ID],
            stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True,
        )
        os.close(slave)
        try:
            transcript = bytearray()
            deadline = time.monotonic() + 15
            while b"[y/N]" not in transcript and time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    transcript.extend(os.read(master, 8192))
                self.assertIsNone(process.poll(), transcript.decode(errors="replace"))
            self.assertIn(b"[y/N]", transcript)
            self.swap()
            os.write(master, b"y\n")
            self.assertNotEqual(process.wait(timeout=5), 0)
            self.assertEqual((self.target / "foreign-sentinel").read_text(), "foreign data\n")
            self.assertTrue((self.backups / "preserved-original/config/settings").is_file())
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
            os.close(master)

    def test_replacement_racing_retirement_is_preserved_in_private_directory(self):
        original_rename = os.rename

        def racing_rename(src, dst, **kwargs):
            self.swap()
            return original_rename(src, dst, **kwargs)

        with mock.patch.object(DELETE.os, "rename", side_effect=racing_rename):
            with self.assertRaisesRegex(DELETE.Refusal, "retirement"):
                self.delete()
        retired = list(self.backups.glob(".ods-delete-*/artifact"))
        self.assertEqual(len(retired), 1)
        self.assertEqual((retired[0] / "foreign-sentinel").read_text(), "foreign data\n")
        self.assertTrue((self.backups / "preserved-original/config/settings").is_file())
        self.assertEqual(retired[0].parent.stat().st_mode & 0o777, 0o700)
        self.assertIn(str(retired[0]), self.errors.getvalue())
        self.assertNotIn("[SUCCESS]", self.output.getvalue())

    def test_new_public_replacement_after_retirement_survives(self):
        original_rename = os.rename

        def replace_after_rename(src, dst, **kwargs):
            result = original_rename(src, dst, **kwargs)
            self.target.mkdir()
            (self.target / "foreign-sentinel").write_text("foreign data\n")
            return result

        with mock.patch.object(DELETE.os, "rename", side_effect=replace_after_rename):
            self.delete()
        self.assertEqual((self.target / "foreign-sentinel").read_text(), "foreign data\n")
        self.assertEqual(list(self.backups.glob(".ods-delete-*")), [])

    def test_manifest_change_during_confirmation_refuses_deletion(self):
        def confirm(prompt):
            (self.target / "manifest.json").write_text("not a manifest")
            return "y"

        with self.assertRaises(DELETE.Refusal):
            self.delete(confirm)
        self.assertTrue((self.target / "config/settings").is_file())

    def test_symlink_replacement_racing_retirement_preserves_referent(self):
        original_rename = os.rename
        foreign = self.root / "foreign"
        foreign.mkdir()
        (foreign / "sentinel").write_text("keep")

        def racing_rename(src, dst, **kwargs):
            self.target.replace(self.backups / "preserved-original")
            self.target.symlink_to(foreign, target_is_directory=True)
            return original_rename(src, dst, **kwargs)

        with mock.patch.object(DELETE.os, "rename", side_effect=racing_rename):
            with self.assertRaises(DELETE.Refusal):
                self.delete()
        self.assertEqual((foreign / "sentinel").read_text(), "keep")
        self.assertTrue(next(self.backups.glob(".ods-delete-*/artifact")).is_symlink())

    def test_directory_links_are_unlinked_without_removing_referents(self):
        foreign = self.root / "foreign"
        foreign.mkdir()
        (foreign / "sentinel").write_text("keep")
        (self.target / "linked").symlink_to(foreign, target_is_directory=True)
        self.delete()
        self.assertFalse(self.target.exists())
        self.assertEqual((foreign / "sentinel").read_text(), "keep")

    def test_cancel_and_eof_leave_original_untouched(self):
        for answer in ("", "n", "yes"):
            self.delete(lambda prompt: answer)
            self.assertTrue((self.target / "config/settings").is_file())
        self.delete(mock.Mock(side_effect=EOFError))
        self.assertTrue(self.target.exists())
        self.assertEqual(list(self.backups.glob(".ods-delete-*")), [])

    def test_remove_failure_is_not_success_and_preserves_retired_artifact(self):
        with mock.patch.object(DELETE.os, "unlink", side_effect=PermissionError("fixture")):
            with self.assertRaises(PermissionError):
                self.delete()
        retired = next(self.backups.glob(".ods-delete-*/artifact"))
        self.assertTrue((retired / "config/settings").is_file())
        self.assertIn(str(retired), self.errors.getvalue())
        self.assertNotIn("[SUCCESS]", self.output.getvalue())

    def test_rename_failure_preserves_original_without_retirement_leak(self):
        with mock.patch.object(DELETE.os, "rename", side_effect=PermissionError("fixture")):
            with self.assertRaises(PermissionError):
                self.delete()
        self.assertTrue((self.target / "config/settings").is_file())
        self.assertEqual(list(self.backups.glob(".ods-delete-*")), [])

    def archive(self):
        path = self.backups / (BACKUP_ID + ".tar.gz")
        with tarfile.open(path, "w:gz") as archive:
            archive.add(self.target, arcname=BACKUP_ID)
        shutil.rmtree(self.target)
        return path

    def test_archive_explicit_and_bare_id(self):
        path = self.archive()
        data = path.read_bytes()
        for name in (BACKUP_ID, path.name):
            path.write_bytes(data)
            self.delete(name=name)
            self.assertFalse(path.exists())

    def test_archive_replacement_during_confirmation_survives(self):
        path = self.archive()

        def confirm(prompt):
            path.rename(self.backups / "original.tar.gz")
            path.write_bytes(b"foreign data")
            return "y"

        with self.assertRaises(DELETE.Refusal):
            self.delete(confirm)
        self.assertEqual(path.read_bytes(), b"foreign data")
        self.assertTrue((self.backups / "original.tar.gz").is_file())

    def test_linked_archive_manifest_refused(self):
        path = self.backups / (BACKUP_ID + ".tar.gz")
        with tarfile.open(path, "w:gz") as archive:
            member = tarfile.TarInfo(BACKUP_ID + "/manifest.json")
            member.type = tarfile.SYMTYPE
            member.linkname = "other"
            archive.addfile(member)
        shutil.rmtree(self.target)
        with self.assertRaises(DELETE.Refusal):
            self.delete()
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
